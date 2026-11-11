#!/usr/bin/env python3
"""Hark -> SSH -> Codex -> PhoneBridge MCP integration spike.

The script keeps one reconnectable PhoneBridge backend alive, then drives it
through separate Codex exec processes. With ``--transport ssh`` every Codex
phase is a separate SSH connection, which exercises the Hark reconnect path
without depending on the Hark UI. The approval token stays in the backend and
trusted-side approval CLI environments, never in Codex's command line.

This is a synthetic Mock-device integration. No real phone, real messaging
app or real external message is used.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phonebridge.hark import (
    build_codex_command,
    codex_env,
    find_codex,
    run_codex,
    ssh_wrap,
)

TOKEN_ENV = "PB_HARK_TOKEN"
INTERRUPTION_MARKER = "PB_INTERRUPTION_AFTER_DISPATCH"


class Backend:
    def __init__(self, command: list[str], env: dict[str, str]):
        self._stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self.process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL,
                                        stderr=self._stderr, env=env)

    def wait_ready(self, socket_path: Path, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("backend exited early: " + self.stderr())
            if socket_path.exists():
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.connect(str(socket_path))
                    return
                except OSError:
                    pass
                finally:
                    probe.close()
            time.sleep(0.05)
        raise RuntimeError("relay backend did not become ready")

    def stderr(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._stderr.close()


def result_for(run, tool: str) -> dict | None:
    for call in reversed(run.tool_calls):
        if call.tool == tool:
            return call.payload
    return None


def disallowed_items(run) -> list[str]:
    forbidden = {"function_call", "local_shell_call", "command_execution"}
    found = []
    for event in run.events:
        item = event.get("item") if isinstance(event, dict) else None
        if event.get("type") == "item.completed" and isinstance(item, dict):
            item_type = item.get("type")
            if item_type in forbidden:
                found.append(str(item_type))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=["direct", "ssh"], default="direct")
    parser.add_argument("--ssh-destination", help="SSH destination for Codex phases, e.g. user@host")
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--ssh-key", help="Private key for BatchMode SSH")
    parser.add_argument("--ssh-known-hosts", help="Known-hosts file for the SSH destination")
    parser.add_argument("--codex-path", help="Codex CLI path; auto-detected when omitted")
    parser.add_argument("--interruption-codex-path",
                        help="Deterministic client that drops the execute receipt to produce interruption evidence")
    parser.add_argument("--python", default=sys.executable, help="Python used for the relay stdio command")
    parser.add_argument("--text", default="Hark SSH probe", help="Synthetic ASCII draft text")
    parser.add_argument("--phase-timeout", type=int, default=300)
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    if args.transport == "ssh" and not args.ssh_destination:
        parser.error("--ssh-destination is required with --transport ssh")

    evidence = {
        "checks": {},
        "phases": {},
        "transport": args.transport,
        "driver": "mock",
        "text": args.text,
        "rejection_text": args.text + " rejection",
        "hark_client_detected": Path("/Applications/Hark.app").exists(),
        "hark_client_verified": False,
        "approval_token_env": TOKEN_ENV,
    }

    def check(name: str, condition: bool, detail=None) -> bool:
        evidence["checks"][name] = bool(condition)
        if detail is not None:
            evidence["checks"][name + "_detail"] = detail
        return bool(condition)

    work_name = tempfile.mkdtemp(prefix="pb-hark-codex-")
    work = Path(work_name)
    os.chmod(work, 0o700)
    backend = None
    try:
        codex = find_codex(args.codex_path)
        if not codex:
            raise RuntimeError("Codex CLI not found; pass --codex-path")
        token = secrets.token_urlsafe(24)
        relay_socket = work / "mcp.sock"
        approval_socket = work / "approval.sock"
        ledger_path = work / "submissions.jsonl"
        workspace = work / "codex-workspace"
        workspace.mkdir(mode=0o700)
        repo_copy = work / "codex-repo"
        shutil.copytree(
            ROOT,
            repo_copy,
            ignore=shutil.ignore_patterns(".git", ".codebuddy", "__pycache__",
                                          "*.pyc", ".pytest_cache"),
        )

        backend_command = [
            sys.executable, "-m", "phonebridge.cli", "--driver", "mock",
            "--approval-socket", str(approval_socket),
            "--submission-state", str(ledger_path),
            "--approval-token-env", TOKEN_ENV,
            "--require-approval-isolation",
            "--max-read-retries", "30", "--action-budget", "80",
            "backend", "--socket", str(relay_socket),
        ]
        backend_env = dict(os.environ)
        backend_env[TOKEN_ENV] = token
        backend = Backend(backend_command, backend_env)
        backend.wait_ready(relay_socket)
        check("reconnectable_backend_started", True, {"socket": str(relay_socket)})

        codex_path = str(codex)
        interruption_path = str(args.interruption_codex_path) if args.interruption_codex_path else None
        phase_count = 0
        reconnect_sessions: list[dict] = []

        def run_phase(name: str, prompt: str, *, client_path: str | None = None,
                      expected_interruption: bool = False) -> object:
            nonlocal phase_count
            phase_count += 1
            reconnect_sessions.append({
                "phase": name,
                "connection": phase_count,
                "transport": args.transport,
                "backend_socket": str(relay_socket),
            })
            command = build_codex_command(client_path or codex_path, args.python, str(repo_copy),
                                          str(relay_socket), prompt, str(workspace),
                                          trusted_project=str(workspace))
            if args.transport == "ssh":
                command = ssh_wrap(
                    command,
                    args.ssh_destination,
                    port=args.ssh_port,
                    key_path=args.ssh_key,
                    known_hosts=args.ssh_known_hosts,
                )
            env = codex_env(os.environ, approval_token_env=TOKEN_ENV)
            check(f"{name}_token_removed_from_codex_env", TOKEN_ENV not in env)
            check(f"{name}_token_not_in_command", token not in json.dumps(command))
            run = run_codex(command, env, timeout=args.phase_timeout)
            entry = {
                "returncode": run.returncode,
                "tools": [call.tool for call in run.tool_calls],
                "mcp_servers": sorted({call.server for call in run.tool_calls if call.server}),
                "errors": [call.error for call in run.tool_calls if call.error],
                "disallowed_items": disallowed_items(run),
                "agent_messages": run.agent_messages[-3:],
                "stderr_tail": run.stderr[-300:],
                "transport_command_count": phase_count,
            }
            evidence["phases"][name] = entry
            if expected_interruption:
                entry["interrupted"] = True
                dropped = (run.returncode != 0
                           and INTERRUPTION_MARKER in run.stderr
                           and not run.tool_calls)
                check(f"{name}_dropped_before_receipt", dropped,
                      {"returncode": run.returncode, "marker": INTERRUPTION_MARKER,
                       "stderr_tail": run.stderr[-200:],
                       "completed_tool_events": len(run.tool_calls)})
                return run
            if run.returncode != 0:
                raise RuntimeError(f"{name} failed: {run.stderr[-500:]}")
            unexpected = [server for server in entry["mcp_servers"] if server != "phonebridge"]
            check(f"{name}_only_phonebridge_mcp", not unexpected, unexpected)
            check(f"{name}_no_shell_execution", not entry["disallowed_items"])
            return run

        prepare_prompt = (
            "Use ONLY the phonebridge MCP tools; do not use shell, file, web or other MCP tools. "
            "Call phone_open_app with app=\"messaging\". Call contacts_open with contact=\"Casey\". "
            "Call phone_tap with x=0.5 and y=0.9. Call phone_type_text with exactly \"{text}\". "
            "Then call message_prepare with recipient_id=\"contact-02\" and message=\"{text}\". "
            "Do not approve or execute the operation. Stop immediately after message_prepare."
        ).format(text=args.text)
        prepare = run_phase("prepare", prepare_prompt)
        prepared = result_for(prepare, "message_prepare")
        if not prepared or prepared.get("ok") is not True or not isinstance(prepared.get("approval_id"), str):
            raise RuntimeError(f"message_prepare did not return an approval_id: {prepared!r}")
        approval_id = prepared["approval_id"]
        check("codex_prepare_ok", True, {"approval_id": approval_id})
        check("codex_prepare_not_executed", result_for(prepare, "phone_execute_operation") is None)

        approval_command = [
            sys.executable, "-m", "phonebridge.cli", "--approval-socket", str(approval_socket),
            "approvals", "approve", approval_id, "--token-env", TOKEN_ENV,
        ]
        approval_env = dict(os.environ)
        approval_env[TOKEN_ENV] = token
        approval = subprocess.run(approval_command, capture_output=True, text=True,
                                  encoding="utf-8", env=approval_env, timeout=20)
        approval_payload = json.loads(approval.stdout) if approval.stdout.strip() else {}
        check("trusted_side_approval", approval.returncode == 0
              and approval_payload.get("approved") is True, approval_payload)

        execute_prompt = (
            "Use ONLY the phonebridge MCP tool phone_execute_operation. "
            f"Call it with approval_id=\"{approval_id}\". Do not use any other tool. "
            "Do not modify files."
        )
        execute = run_phase("execute", execute_prompt)
        executed = result_for(execute, "phone_execute_operation") or {}
        check("approved_execute_completed", executed.get("ok") is True
              and executed.get("submission_state") == "completed", executed)
        check("synthetic_record_not_real", executed.get("simulated") is True
              and executed.get("real_message_sent") is False)

        inspect_prompt = (
            "Use ONLY the phonebridge MCP tool phone_inspect. Call it once with no arguments. "
            "Do not use shell or file tools."
        )
        inspect = run_phase("inspect_after_reconnect", inspect_prompt)
        inspected = result_for(inspect, "phone_inspect") or {}
        messages = inspected.get("messages") if isinstance(inspected.get("messages"), list) else []
        check("reconnect_readback_one_record", inspected.get("ok") is True
              and len(messages) == 1
              and messages[0].get("text") == args.text
              and messages[0].get("simulated") is True,
              {"count": len(messages), "texts": [m.get("text") for m in messages]})

        rejection_prompt = (
            "Use ONLY the phonebridge MCP tools; do not use shell, file or other tools. "
            "Call phone_open_app with app=\"messaging\". Call contacts_open with contact=\"Casey\". "
            "Call phone_tap with x=0.5 and y=0.9. Call phone_type_text with exactly \"{text}\". "
            "Then call message_prepare with recipient_id=\"contact-02\" and message=\"{text}\". "
            "Immediately call phone_execute_operation using the approval_id returned by message_prepare. "
            "No approval exists; this call must fail. Do not retry it."
        ).format(text=evidence["rejection_text"])
        rejection = run_phase("unapproved_execute", rejection_prompt)
        rejected = result_for(rejection, "phone_execute_operation") or {}
        check("unapproved_execute_rejected", rejected.get("ok") is not True
              and rejected.get("error") == "independent_approval_required", rejected)

        replay_prompt = (
            "Use ONLY the phonebridge MCP tool phone_execute_operation. "
            f"Call it with approval_id=\"{approval_id}\". This is a replay and must not create a record. "
            "Do not use any other tool."
        )
        replay = run_phase("replay_after_reconnect", replay_prompt)
        replayed = result_for(replay, "phone_execute_operation") or {}
        check("replay_rejected", replayed.get("ok") is not True
              and replayed.get("error") == "unknown_or_processed_operation", replayed)

        # A second approved operation is dispatched through a client that
        # disconnects after sending the request, before reading the receipt.
        # The backend must still finish exactly once; a fresh connection then
        # reads the device back and a replay must not create another record.
        interrupted_text = args.text + " interrupted"
        interruption_evidence = {
            "injected": interruption_path is not None,
            "verified": False,
            "marker": INTERRUPTION_MARKER,
        }
        if interruption_path:
            prepare2_prompt = (
                "Use ONLY the phonebridge MCP tools; do not use shell, file, web or other MCP tools. "
                "Call phone_open_app with app=\"messaging\". Call contacts_open with contact=\"Casey\". "
                "Call phone_tap with x=0.5 and y=0.9. Call phone_type_text with exactly \"{text}\". "
                "Then call message_prepare with recipient_id=\"contact-02\" and message=\"{text}\". "
                "Do not approve or execute the operation. Stop immediately after message_prepare."
            ).format(text=interrupted_text)
            prepare2 = run_phase("prepare_for_interruption", prepare2_prompt)
            prepared2 = result_for(prepare2, "message_prepare")
            if (not prepared2 or prepared2.get("ok") is not True
                    or not isinstance(prepared2.get("approval_id"), str)):
                raise RuntimeError(f"interruption prepare failed: {prepared2!r}")
            approval2 = prepared2["approval_id"]
            approval2_command = [
                sys.executable, "-m", "phonebridge.cli", "--approval-socket", str(approval_socket),
                "approvals", "approve", approval2, "--token-env", TOKEN_ENV,
            ]
            approval2_run = subprocess.run(approval2_command, capture_output=True, text=True,
                                           encoding="utf-8", env=approval_env, timeout=20)
            approval2_payload = json.loads(approval2_run.stdout) if approval2_run.stdout.strip() else {}
            check("trusted_side_approval_for_interruption",
                  approval2_run.returncode == 0 and approval2_payload.get("approved") is True,
                  approval2_payload)

            interrupt_prompt = (
                "Use ONLY the phonebridge MCP tool phone_execute_operation. "
                f"Call it with approval_id=\"{approval2}\". Do not use any other tool."
            )
            run_phase("interrupted_execute", interrupt_prompt,
                      client_path=interruption_path, expected_interruption=True)

            inspect_after_interruption = run_phase("inspect_after_interruption", inspect_prompt)
            observed2 = result_for(inspect_after_interruption, "phone_inspect") or {}
            records_after = observed2.get("messages") if isinstance(observed2.get("messages"), list) else []
            texts_after = [item.get("text") for item in records_after]
            check("interruption_no_duplicate_record",
                  observed2.get("ok") is True
                  and len(records_after) == 2
                  and texts_after.count(args.text) == 1
                  and texts_after.count(interrupted_text) == 1,
                  {"count": len(records_after), "texts": texts_after})

            replay2_prompt = (
                "Use ONLY the phonebridge MCP tool phone_execute_operation. "
                f"Call it with approval_id=\"{approval2}\". This is a replay and must not create a record. "
                "Do not use any other tool."
            )
            replay2 = run_phase("replay_interrupted_operation", replay2_prompt)
            replayed2 = result_for(replay2, "phone_execute_operation") or {}
            check("interrupted_operation_replay_rejected",
                  replayed2.get("ok") is not True
                  and replayed2.get("error") == "unknown_or_processed_operation", replayed2)
            interruption_evidence.update({
                "verified": True,
                "phase": "interrupted_execute",
                "operation_id": approval2,
                "record_count_after_reconnect": len(records_after),
                "receipt_dropped": True,
                "replay_rejected": True,
            })
        else:
            interruption_evidence["note"] = (
                "Pass --interruption-codex-path to inject a dropped execute receipt."
            )
        evidence["interruption"] = interruption_evidence

        ledger_text = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else ""
        ledger_events = [json.loads(line).get("event") for line in ledger_text.splitlines() if line.strip()]
        evidence["ledger_events"] = ledger_events
        expected_counts = (
            {"prepared": 3, "approved": 2, "dispatching": 2, "completed": 2}
            if interruption_path else
            {"prepared": 2, "approved": 1, "dispatching": 1, "completed": 1}
        )
        check("ledger_has_expected_events",
              ledger_events[:4] == ["prepared", "approved", "dispatching", "completed"]
              and all(ledger_events.count(event) == count
                      for event, count in expected_counts.items()),
              {"events": ledger_events, "expected_counts": expected_counts})
        check("ledger_has_no_plaintext", args.text not in ledger_text
              and evidence["rejection_text"] not in ledger_text
              and interrupted_text not in ledger_text)
        check("multiple_transport_connections",
              phase_count >= (8 if interruption_path else 4),
              {"connections": phase_count, "sessions": reconnect_sessions})
        check("reconnect_sessions_share_backend",
              len({session["backend_socket"] for session in reconnect_sessions}) == 1
              and len(reconnect_sessions) == phase_count,
              {"sessions": len(reconnect_sessions)})
        evidence["evidence_classes"] = {
            "success": {
                "verified": bool(evidence["checks"].get("approved_execute_completed")),
                "phases": ["prepare", "execute", "inspect_after_reconnect"],
            },
            "failure": {
                "verified": bool(evidence["checks"].get("unapproved_execute_rejected")
                                 and evidence["checks"].get("replay_rejected")),
                "phases": ["unapproved_execute", "replay_after_reconnect"],
            },
            "interruption": {
                "verified": bool(interruption_evidence["verified"]),
                "phases": (["prepare_for_interruption", "interrupted_execute",
                            "inspect_after_interruption", "replay_interrupted_operation"]
                           if interruption_path else []),
            },
            "reconnect": {
                "verified": bool(evidence["checks"].get("reconnect_readback_one_record")
                                 and evidence["checks"].get("reconnect_sessions_share_backend")),
                "session_count": phase_count,
                "sessions": reconnect_sessions,
            },
        }
        evidence["hark_ui_verified"] = False
        evidence["hark_ui_note"] = (
            "Hark.app is installed, but this scripted run does not operate its UI; "
            "it verifies the SSH/Codex/MCP host chain only."
            if evidence["hark_client_detected"]
            else "Hark.app is not installed on this host; this run verifies the SSH/Codex/MCP host chain only."
        )

        bool_checks = [value for key, value in evidence["checks"].items()
                       if isinstance(value, bool) and not key.endswith("_detail")]
        evidence["host_chain_passed"] = all(bool_checks)
        evidence["check_count"] = len(bool_checks)
        evidence["hark_client_verified"] = False
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if evidence["host_chain_passed"] else 1
    except Exception as exc:
        evidence["host_chain_passed"] = False
        evidence["exception"] = f"{type(exc).__name__}: {exc}"
        if backend is not None:
            evidence["backend_stderr"] = backend.stderr()[-1000:]
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 1
    finally:
        if backend is not None:
            backend.close()
        if args.keep_artifacts:
            print(f"Artifacts kept at {work}", file=sys.stderr)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
