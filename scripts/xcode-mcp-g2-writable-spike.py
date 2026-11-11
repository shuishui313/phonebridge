#!/usr/bin/env python3
"""G2 full writable acceptance on an Apple Simulator and the owned fixture.

Scope is deliberately narrow: the fixture is relaunched with its submit
control ENABLED, an operator opt-in allows the trusted-side approval gate to
create ONE synthetic record, and every other path must fail closed. Nothing
is a real message send, no third-party app is opened, and no real device is
used. The fixture is relaunched with ``pb-disable-submit`` at the end so the
G1 baseline is restored.

Checks:
  * unapproved generic tap on Send is rejected before any interaction command
  * unapproved execute, missing token, wrong target, changed body, stale
    context and replay cannot produce a record
  * an approved operation produces exactly one attributable matching record
  * restarting the MCP service with an in-flight ledger entry pauses and
    never resends (reconnect / no-duplicate)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE_ID = "org.example.phonebridge.fixture"
HITPOINT = re.compile(r"hitPoint:\s*\{([\d.]+),\s*([\d.]+)\}")
IDENTIFIER = re.compile(r"identifier:\s*'([^']*)'")
WINDOW = re.compile(r"Window, \{\{[\d.]+, [\d.]+\}, \{([\d.]+), ([\d.]+)\}\}")
FIELD_VALUE = re.compile(r"(?:value|label):\s*(?:'([^']*)'|([^,]+))")
MESSAGE_IDENTIFIER = re.compile(r"identifier:\s*'chat\.message\.(\d+)'")


class MCPClient:
    def __init__(self, command, env):
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=env,
        )
        self.next_id = 1

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                          "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed: " + self.proc.stderr.read())
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def initialize(self):
        hello = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "phonebridge-g2-writable-spike", "version": "0.4"},
        })
        if "result" not in hello:
            raise RuntimeError(f"initialize failed: {hello}")
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                          "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()
        return hello["result"]["serverInfo"]

    def call(self, name, arguments=None):
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return json.loads(response["result"]["content"][0]["text"])

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        return self.proc.stderr.read()


def element(text: str, identifier: str):
    for line in text.splitlines():
        match = IDENTIFIER.search(line)
        if match and match.group(1) == identifier:
            point = HITPOINT.search(line)
            if point:
                return float(point.group(1)), float(point.group(2))
    return None


def dimensions(text: str):
    match = WINDOW.search(text)
    return (float(match.group(1)), float(match.group(2))) if match else None


def exact_identifier(text: str, identifier: str) -> bool:
    return re.search(r"identifier:\s*'" + re.escape(identifier) + r"'", text) is not None


def field_value(text: str, identifier: str):
    for line in text.splitlines():
        match = IDENTIFIER.search(line)
        if not match or match.group(1) != identifier:
            continue
        value = FIELD_VALUE.search(line)
        return (value.group(1) or value.group(2) or "").strip() if value else ""
    return None


def records(text: str):
    found = []
    for line in text.splitlines():
        identifier = MESSAGE_IDENTIFIER.search(line)
        if not identifier:
            continue
        label = re.search(r"label:\s*'([^']*)'", line)
        found.append((int(identifier.group(1)), label.group(1) if label else ""))
    return sorted(found)


def hierarchy_text(result: dict):
    path = result.get("hierarchy_path")
    if not path:
        raise RuntimeError(f"no fresh hierarchy: {result}")
    return Path(path).read_text(encoding="utf-8")


def relaunch(udid: str, *arguments: str):
    subprocess.run(["xcrun", "simctl", "terminate", udid, BUNDLE_ID],
                   capture_output=True, text=True, timeout=15)
    launch = subprocess.run(["xcrun", "simctl", "launch", udid, BUNDLE_ID, *arguments],
                            capture_output=True, text=True, timeout=20)
    if launch.returncode != 0:
        raise RuntimeError(f"fixture launch failed: {launch.stderr.strip()}")
    return ":".join(arguments) or "submit-enabled"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim-udid", required=True, help="Booted Apple Simulator UDID")
    ap.add_argument("--text", default="G2 acceptance record",
                    help="Synthetic ASCII draft text")
    args = ap.parse_args()

    evidence = {"sim_udid": args.sim_udid, "bundle_id": BUNDLE_ID,
                "text": args.text, "checks": {}, "tool_calls": []}
    work = tempfile.TemporaryDirectory(prefix="pb-g2-writable-")
    root = Path(work.name)
    os.chmod(root, 0o700)
    ledger_path = root / "submissions.jsonl"
    socket_path = root / "approval.sock"
    token = secrets.token_urlsafe(24)
    env = {**os.environ, "PB_G2_TOKEN": token}

    def server_command():
        return [sys.executable, "-m", "phonebridge.cli",
                "--driver", "xcode-mcp", "--sim-udid", args.sim_udid,
                "--fixture-bundle", BUNDLE_ID,
                "--allow-xcode-interactions", "--allow-fixture-virtual-submit",
                "--require-approval-isolation",
                "--approval-token-env", "PB_G2_TOKEN",
                "--approval-socket", str(socket_path),
                "--submission-state", str(ledger_path),
                "--max-read-retries", "30", "--action-budget", "80", "serve"]

    def approve(op_id, *, with_token=True):
        command = [sys.executable, "-m", "phonebridge.cli",
                   "--approval-socket", str(socket_path), "approvals", "approve", op_id]
        if with_token:
            command += ["--token-env", "PB_G2_TOKEN"]
        out = subprocess.run(command, capture_output=True, text=True, env=env, timeout=20)
        try:
            return json.loads(out.stdout)
        except ValueError:
            return {"error": "approval_cli_unavailable", "stderr": out.stderr[:200]}

    def check(name, condition, detail=None):
        evidence["checks"][name] = bool(condition)
        if detail is not None:
            evidence["checks"][name + "_detail"] = detail
        return bool(condition)

    client = None
    try:
        evidence["launch_arguments"] = relaunch(args.sim_udid)
        client = MCPClient(server_command(), env)
        evidence["server"] = client.initialize()

        def tool(name, arguments=None):
            evidence["tool_calls"].append(name)
            return client.call(name, arguments)

        def inspect():
            return hierarchy_text(tool("phone_inspect"))

        def tap_identifier(text, identifier):
            size, point = dimensions(text), element(text, identifier)
            if not size or not point:
                raise RuntimeError(f"cannot locate {identifier} in fresh hierarchy")
            return tool("phone_tap", {"x": point[0] / size[0],
                                      "y": point[1] / size[1]})

        status = tool("phone_status")
        submission = status.get("submissions") or {}
        check("token_isolation", submission.get("approval_isolated") is True
              and submission.get("approval_isolation_mode") == "token", submission)
        check("virtual_submit_optin_disclosed", status.get("virtual_submit_enabled") is True)
        check("fixture_activated", tool("phone_open_app", {"app": "fixture"}).get("ok") is True)

        for identifier in ("app.whatsapp", "contact.contact-02", "chat.input"):
            check("tap_" + identifier, tap_identifier(inspect(), identifier).get("ok") is True)
        check("draft_typed", tool("phone_type_text", {"text": args.text}).get("ok") is True)

        before = inspect()
        check("send_enabled", exact_identifier(before, "chat.send")
              and not exact_identifier(before, "chat.send.disabled"))
        check("draft_visible", field_value(before, "chat.input") == args.text,
              field_value(before, "chat.input"))
        check("no_record_before", records(before) == [], records(before))

        # 1. Unapproved generic tap on the enabled Send control.
        send_point = element(before, "chat.send")
        size = dimensions(before)
        blocked = tool("phone_tap", {"x": send_point[0] / size[0],
                                     "y": send_point[1] / size[1]})
        check("unapproved_tap_rejected", blocked.get("error") == "submission_requires_approval",
              blocked.get("error"))
        check("no_retry_advice", (blocked.get("retry") or {}).get("allowed") is False,
              blocked.get("retry"))
        check("no_record_after_unapproved_tap", records(inspect()) == [])

        # 2. Unapproved execute, and approval without the operator token.
        op1 = tool("message_prepare", {"recipient_id": "contact-02",
                                       "message": args.text}).get("approval_id")
        check("prepare_ok", bool(op1))
        r = tool("phone_execute_operation", {"approval_id": op1})
        check("unapproved_execute_rejected", r.get("error") == "independent_approval_required",
              r.get("error"))
        op2 = tool("message_prepare", {"recipient_id": "contact-02",
                                       "message": args.text}).get("approval_id")
        no_token = approve(op2, with_token=False)
        check("missing_token_rejected", no_token.get("error") == "approval_token_required",
              no_token.get("error"))
        r = tool("phone_execute_operation", {"approval_id": op2})
        check("self_approval_rejected", r.get("error") == "independent_approval_required",
              r.get("error"))
        check("no_record_after_rejections", records(inspect()) == [])

        # 3. Wrong target, changed body and a stale context after revision change.
        r = tool("message_prepare", {"recipient_id": "contact-01", "message": args.text})
        check("wrong_target_rejected",
              r.get("error") == "recipient_not_verified_in_current_chat", r.get("error"))
        r = tool("message_prepare", {"recipient_id": "contact-02",
                                    "message": args.text + " changed"})
        check("changed_body_rejected", r.get("error") == "draft_mismatch", r.get("error"))
        op3 = tool("message_prepare", {"recipient_id": "contact-02",
                                       "message": args.text}).get("approval_id")
        context = inspect()
        input_point, size = element(context, "chat.input"), dimensions(context)
        check("context_change_applied",
              tool("phone_tap", {"x": input_point[0] / size[0],
                                 "y": input_point[1] / size[1]}).get("ok") is True)
        stale = approve(op3)
        check("stale_context_rejected",
              stale.get("error") == "stale_or_expired_proposal", stale.get("error"))
        r = tool("phone_execute_operation", {"approval_id": op3})
        check("stale_execute_rejected", r.get("ok") is not True, r.get("error"))
        check("no_record_after_stale", records(inspect()) == [])

        # 4. The approved operation produces exactly one attributable record.
        op4 = tool("message_prepare", {"recipient_id": "contact-02",
                                      "message": args.text}).get("approval_id")
        approved = approve(op4)
        check("operator_approval", approved.get("approved") is True, approved.get("error"))
        result = tool("phone_execute_operation", {"approval_id": op4})
        check("approved_submission_completed", result.get("ok") is True
              and result.get("submission_state") == "completed",
              {k: result.get(k) for k in ("ok", "error", "submission_state")})
        check("record_attributed", (result.get("verification") or {}).get("source")
              == "attributed_new_record", result.get("verification"))
        check("no_real_message_sent", result.get("real_message_sent") is False
              and result.get("virtual_fixture_send") is True)
        after = inspect()
        check("exactly_one_matching_record",
              records(after) == [(0, args.text)], records(after))

        # 5. Replay cannot duplicate.
        replay = tool("phone_execute_operation", {"approval_id": op4})
        check("replay_rejected", replay.get("error") == "unknown_or_processed_operation",
              replay.get("error"))
        check("no_record_after_replay", records(inspect()) == [(0, args.text)])

        # 6. Reconnect: restart the MCP service with an in-flight ledger entry.
        stderr1 = client.close()
        client = None
        check("clean_first_shutdown", "submission_recovery" not in stderr1)
        with ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "ts": "2026-11-11T00:00:00+00:00", "event": "dispatching",
                "op_id": "g2-in-flight", "recipient_id": "contact-02",
                "message_sha256": hashlib.sha256(args.text.encode("utf-8")).hexdigest(),
                "message_chars": len(args.text), "baseline_count": 0,
            }, ensure_ascii=False) + "\n")
        client = MCPClient(server_command(), env)
        client.initialize()
        check("reconnect_keeps_one_record", records(hierarchy_text(client.call("phone_inspect")))
              == [(0, args.text)])
        stderr2 = client.close()
        client = None
        recovery_lines = [line for line in stderr2.splitlines()
                          if "submission_recovery" in line]
        report = (json.loads(recovery_lines[0])["submission_recovery"]
                  if recovery_lines else {})
        check("restart_inflight_paused", report.get("pending_pause") == 1, report)
        check("restart_never_resends", bool(report.get("recovered"))
              and report["recovered"][0]["resent"] is False
              and report["recovered"][0]["confirmed"] is False, report.get("recovered"))
        evidence["restart_recovery"] = report

        evidence["ledger_events"] = [json.loads(line)["event"] for line in
                                     ledger_path.read_text(encoding="utf-8").splitlines()]
        check("ledger_has_no_plaintext", args.text not in
              ledger_path.read_text(encoding="utf-8"))

        boolean_checks = [v for k, v in evidence["checks"].items()
                          if not k.endswith("_detail") and isinstance(v, bool)]
        evidence["g2_passed"] = all(boolean_checks) and len(boolean_checks) >= 25
        evidence["check_count"] = len(boolean_checks)
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if evidence["g2_passed"] else 1
    except Exception as exc:  # keep the evidence, restore the baseline
        evidence["g2_passed"] = False
        evidence["exception"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 1
    finally:
        if client is not None:
            client.close()
        try:
            relaunch(args.sim_udid, "pb-disable-submit")
            print("Fixture restored with pb-disable-submit", file=sys.stderr)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"WARNING: could not restore disabled fixture: {exc}", file=sys.stderr)
        work.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
