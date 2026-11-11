#!/usr/bin/env python3
"""Real cross-UID approval acceptance for the externally hosted endpoint.

Two launch modes:

* default: uses ``sudo -n -u <approver>`` for each approval call. Works only
  where non-interactive sudo is already authorized.
* ``--authenticate``: launches ONE interactive ``sudo -u <approver>`` agent
  (sudo may prompt once on the terminal), then sends every approval request
  over a private local socket. This does not depend on sudo ticket sharing
  between the parent and its child processes.

When neither path is available the script reports ``outcome=blocked``; it never
reports an injected peer-credential result as a real cross-UID process.

``--verify-evidence`` re-checks a recorded JSON result without re-running the
acceptance. It only validates internal consistency of the evidence; seeing the
real process on this host is still the operator's on-site step.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phonebridge.approvals import ApprovalEndpoint, approval_request
from phonebridge.core import PhoneBridge
from phonebridge.hosted import account_name, platform_supported, verify_acl, verify_layout
from phonebridge.mcp import read_message, write_message

CHILD = r'''#!/usr/bin/env python3
"""Runs as the approver UID. Token comes from a file, never from argv."""
import argparse
import json
import socket
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--socket", required=True)          # hosted approval socket
p.add_argument("--shared", required=True)
p.add_argument("--service-uid", type=int, required=True)
p.add_argument("--action", default="")
p.add_argument("--approval-id", default="")
p.add_argument("--with-token", action="store_true")
p.add_argument("--control-socket", default="")
a = p.parse_args()
sys.path.insert(0, a.shared)
from phonebridge.approvals import approval_request
from phonebridge.mcp import read_message, write_message


def handle(req):
    token = None
    if req.get("with_token"):
        token = (Path(a.shared) / "token").read_text(encoding="utf-8").strip()
    try:
        return approval_request(a.socket, req["action"],
                                req.get("approval_id") or None,
                                token=token, hosted=True,
                                socket_owner_uid=a.service_uid)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


if a.control_socket:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(a.control_socket)
        stream = conn.makefile("rwb")
        while True:
            try:
                req = read_message(stream)
            except (EOFError, OSError, ValueError):
                break
            try:
                write_message(stream, handle(req))
            except OSError:
                break
else:
    print(json.dumps(handle({"action": a.action, "approval_id": a.approval_id,
                             "with_token": a.with_token}), ensure_ascii=False))
'''


def _run(command, runner=None):
    call = runner or subprocess.run
    return call(command, capture_output=True, text=True,
                encoding="utf-8", timeout=30)


def probe_sudo(approver: str, *, runner=None) -> dict:
    completed = _run(["sudo", "-n", "-u", approver, "/usr/bin/id", "-u"], runner)
    if completed.returncode != 0:
        return {"ok": False, "error": "sudo_non_interactive_unavailable",
                "detail": (completed.stderr or "").strip()[-300:]}
    return {"ok": True, "reported_uid": (completed.stdout or "").strip()}


def build_child_command(python: str, child: Path, *, socket_path: Path, shared: Path,
                        service_uid: int, action: str, approval_id: str,
                        with_token: bool) -> list[str]:
    """The token is never placed in argv or the environment of the child."""
    command = [python, str(child), "--socket", str(socket_path),
               "--shared", str(shared), "--service-uid", str(service_uid),
               "--action", action, "--approval-id", approval_id]
    if with_token:
        command.append("--with-token")
    return command


def run_as_approver(approver: str, command: list[str], *, runner=None) -> dict:
    completed = _run(["sudo", "-n", "-u", approver, *command], runner)
    if completed.returncode != 0:
        return {"ok": False, "error": "approver_process_failed",
                "detail": (completed.stderr or "").strip()[-300:]}
    try:
        return json.loads(completed.stdout)
    except (ValueError, TypeError):
        return {"ok": False, "error": "invalid_child_output"}


class ApproverSession:
    """One interactive sudo launch, then many requests over a private socket.

    The listener socket is created by the service account and given a named
    ACL for exactly one approver UID. sudo inherits the terminal so it can
    prompt once; after that no further sudo invocation is needed.
    """

    def __init__(self, *, approver: str, python: str, child: Path,
                 control_socket: Path, shared: Path, service_uid: int,
                 approval_socket: Path, approver_uid: int):
        self.launch_command = [
            "sudo", "-u", approver, python, str(child),
            "--socket", str(approval_socket),
            "--shared", str(shared),
            "--service-uid", str(service_uid),
            "--control-socket", str(control_socket),
        ]
        self.control_socket = control_socket
        self.approver_uid = approver_uid
        self.listener = None
        self.process = None
        self.connection = None
        self.stream = None

    def start(self):
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.control_socket))
        self.listener.listen(1)
        self.control_socket.chmod(0o600)
        grant = subprocess.run(
            ["chmod", "+a",
             f"user:{account_name(self.approver_uid)} allow read,write",
             str(self.control_socket)],
            capture_output=True, text=True, timeout=10)
        if grant.returncode != 0:
            raise RuntimeError("control socket ACL grant failed")
        import time
        self.listener.settimeout(2)
        # stdin/stdout/stderr are inherited so sudo can prompt once on the tty.
        self.process = subprocess.Popen(self.launch_command)
        deadline = time.monotonic() + 60
        while True:
            if self.process.poll() is not None:
                raise RuntimeError("approver agent exited before connecting")
            if time.monotonic() > deadline:
                raise RuntimeError("approver agent did not connect in time")
            try:
                self.connection, _ = self.listener.accept()
                break
            except socket.timeout:
                continue
        self.stream = self.connection.makefile("rwb")

    def request(self, action: str, approval_id: str = "", with_token: bool = True) -> dict:
        write_message(self.stream, {"action": action,
                                    "approval_id": approval_id,
                                    "with_token": with_token})
        return read_message(self.stream)

    def close(self):
        for resource in (self.stream, self.connection, self.listener):
            try:
                if resource is not None:
                    resource.close()
            except Exception:
                pass
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        try:
            self.control_socket.unlink()
        except FileNotFoundError:
            pass


def public_temp_parent() -> Path:
    """Use /tmp, not the per-user TMPDIR whose parents are 0700.

    On macOS tempfile.gettempdir() points into /var/folders/<hash>/T/ and
    the hash directory is private to the service account, so a second UID
    cannot traverse the path even if the leaf directory is 0755.
    """
    return Path("/tmp")


def path_readable_by_other(path: Path) -> bool:
    """Every parent needs other-execute; the leaf needs other-read."""
    parts = path.parts[1:]
    current = Path("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            mode = stat.S_IMODE(current.lstat().st_mode)
        except OSError:
            return False
        needed = 0o004 if index == len(parts) - 1 else 0o001
        if not (mode & needed):
            return False
    return True


NOTE = ("Structural re-check only. The JSON must come from the operator's "
        "on-site --authenticate run; this function never launches a process.")

REQUIRED_CHECKS = (
    "token_file_acl_verified",
    "shared_parent_traversable_by_other",
    "shared_child_path_readable_by_other",
    "shared_package_path_readable_by_other",
    "approver_process_available",
    "directory_acl_entry_verified",
    "socket_acl_entry_verified",
    "layout_still_private",
    "hosted_isolation_disclosed",
    "token_not_in_child_command",
    "real_peer_uid_accepted",
    "one_attributed_record",
    "cross_uid_replay_rejected",
    "missing_token_rejected_by_real_peer",
    "service_uid_rejected_as_peer",
)


def verify_evidence(payload: object) -> dict:
    """Re-check a recorded on-site JSON result without re-running the spike.

    This is a structural consistency check only; it cannot prove where the
    JSON came from. Rehearsals are rejected because they carry
    ``approver_process_real: false`` and ``cross_uid_process_verified: false``.
    """
    reasons: list[str] = []
    if not isinstance(payload, dict):
        return {"evidence_verified": False, "reasons": ["evidence_not_object"],
                "failed_checks": [], "unexpected_checks": [],
                "required_check_count": len(REQUIRED_CHECKS),
                "note": NOTE}
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        reasons.append("checks_missing")
        checks = {}
    failed = sorted(name for name in REQUIRED_CHECKS if checks.get(name) is not True)
    unexpected = sorted(set(checks) - set(REQUIRED_CHECKS))
    if failed:
        reasons.append("checks_not_all_true")
    if unexpected:
        reasons.append("unexpected_checks")
    if payload.get("check_count") != len(REQUIRED_CHECKS) or len(checks) != len(REQUIRED_CHECKS):
        reasons.append("check_count_mismatch")
    if payload.get("outcome") != "passed":
        reasons.append("outcome_not_passed")
    if payload.get("cross_uid_process_verified") is not True:
        reasons.append("cross_uid_process_not_verified")
    if payload.get("approver_process_real") is not True:
        reasons.append("approver_process_not_real")
    if payload.get("real_model_calls") is not False:
        reasons.append("model_calls_not_excluded")
    approver_uid, service_uid = payload.get("approver_uid"), payload.get("service_uid")
    if not (isinstance(approver_uid, int) and isinstance(service_uid, int)
            and approver_uid != service_uid and approver_uid >= 0 and service_uid >= 0):
        reasons.append("uids_not_distinct")
    approved = payload.get("approver_approve_response")
    if not isinstance(approved, dict) or not (
            approved.get("approved") is True or approved.get("ok") is True):
        reasons.append("approve_response_not_approved")
    replay = payload.get("approver_replay_response")
    if not isinstance(replay, dict) or replay.get("ok") is not False or not replay.get("error"):
        reasons.append("replay_not_rejected")
    command = payload.get("on_site_command")
    if not isinstance(command, str) or "cross-uid-approval-spike.py" not in command:
        reasons.append("on_site_command_missing")
    return {"evidence_verified": not reasons,
            "reasons": reasons,
            "failed_checks": failed,
            "unexpected_checks": unexpected,
            "check_count": payload.get("check_count"),
            "required_check_count": len(REQUIRED_CHECKS),
            "authenticate_mode": payload.get("authenticate_mode"),
            "note": NOTE}


def on_site_command(approver: str) -> str:
    return (f"cd {ROOT} && python3 scripts/cross-uid-approval-spike.py "
            f"--approver {approver} --authenticate")


def blocker_evidence(approver: str, probe: dict) -> dict:
    return {"outcome": "blocked",
            "cross_uid_process_verified": False,
            "approver_process_real": False,
            "approver": approver,
            "blocker": probe.get("error"),
            "blocker_detail": probe.get("detail"),
            "on_site_command": on_site_command(approver),
            "checks": {}, "check_count": 0,
            "note": ("Real named ACLs were verified separately by "
                     "scripts/hosted-approval-spike.py; this blocker only "
                     "concerns running the approval client as another UID. "
                     "Use --authenticate for one interactive sudo prompt.")}


def main(argv=None, *, runner=None, rehearsal_hook=None) -> int:
    # runner/rehearsal_hook exist only so the whole flow can be rehearsed with
    # an injected approver process. A real run always uses runner=None and
    # never applies a hook; its evidence says cross_uid_process_verified.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approver", default="nobody", help="Second OS account used for approval")
    parser.add_argument("--probe-only", action="store_true", help="Only check non-interactive sudo")
    parser.add_argument("--authenticate", action="store_true",
                        help="Launch one interactive sudo agent; may prompt once for a password")
    parser.add_argument("--verify-evidence", metavar="PATH",
                        help="Re-check a saved on-site JSON result; use - for stdin")
    args = parser.parse_args(argv)

    if args.verify_evidence:
        try:
            raw = (sys.stdin.read() if args.verify_evidence == "-"
                   else Path(args.verify_evidence).read_text(encoding="utf-8"))
            payload = json.loads(raw)
        except (OSError, ValueError) as exc:
            print(json.dumps({"evidence_verified": False,
                              "reasons": [f"unreadable_evidence: {type(exc).__name__}"]},
                             ensure_ascii=False, indent=2))
            return 1
        verdict = verify_evidence(payload)
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["evidence_verified"] else 1
    if args.probe_only:
        probe = probe_sudo(args.approver, runner=runner)
        print(json.dumps({"sudo_non_interactive_available": probe["ok"],
                          "approver": args.approver, **probe}, ensure_ascii=False, indent=2))
        return 0
    if runner is None and os.geteuid() == 0:
        print(json.dumps({
            "outcome": "blocked",
            "cross_uid_process_verified": False,
            "blocker": "script_must_not_run_as_root",
            "detail": ("Launching the whole spike with sudo makes the MCP service "
                       "run as root, which is not the intended two-unprivileged-account "
                       "acceptance condition. Only the approver agent may use sudo."),
            "on_site_command": on_site_command(args.approver),
            "checks": {}, "check_count": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    use_session = bool(args.authenticate and runner is None)
    probe = ({"ok": True, "reported_uid": "interactive"} if use_session
             else probe_sudo(args.approver, runner=runner))
    if not use_session and not probe["ok"]:
        print(json.dumps(blocker_evidence(args.approver, probe), ensure_ascii=False, indent=2))
        return 0
    if not platform_supported():
        print(json.dumps({**blocker_evidence(args.approver, probe),
                          "blocker": "acl_platform_unsupported"}, ensure_ascii=False, indent=2))
        return 0

    import pwd
    approver_uid = pwd.getpwnam(args.approver).pw_uid
    service_uid = os.getuid()
    temp_parent = public_temp_parent()
    shared = Path(tempfile.mkdtemp(prefix="pb-crossuid-", dir=str(temp_parent)))
    endpoint = None
    session = None
    checks: dict[str, bool] = {}
    evidence: dict = {"approver": args.approver,
                      "approver_uid": int(approver_uid),
                      "service_uid": service_uid,
                      "authenticate_mode": bool(args.authenticate),
                      "approver_process_real": runner is None,
                      "real_model_calls": False}
    try:
        os.chmod(shared, 0o755)
        shutil.copytree(ROOT / "phonebridge", shared / "phonebridge",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        subprocess.run(["chmod", "-R", "a+rX", str(shared / "phonebridge")],
                       check=True, timeout=20)
        service_dir = shared / "service"
        service_dir.mkdir(mode=0o700)
        service_dir.chmod(0o700)
        socket_path = service_dir / "approval.sock"

        token = secrets.token_urlsafe(24)
        token_file = shared / "token"
        token_file.write_text(token, encoding="utf-8")
        token_file.chmod(0o600)
        name = account_name(approver_uid)
        grant = subprocess.run(["chmod", "+a", f"user:{name} allow read", str(token_file)],
                               capture_output=True, text=True, timeout=10)
        checks["token_file_acl_verified"] = grant.returncode == 0

        child = shared / "child.py"
        child.write_text(CHILD, encoding="utf-8")
        child.chmod(0o755)
        checks["shared_parent_traversable_by_other"] = bool(
            stat.S_IMODE(temp_parent.lstat().st_mode) & 0o001)
        checks["shared_child_path_readable_by_other"] = path_readable_by_other(child)
        checks["shared_package_path_readable_by_other"] = path_readable_by_other(
            shared / "phonebridge" / "core.py")

        bridge = PhoneBridge()
        endpoint = ApprovalEndpoint(bridge, str(socket_path), token=token,
                                    required_uid=int(approver_uid), hosted=True).start()
        bridge.open_app("messaging")
        bridge.open_chat("contact-01")
        bridge.tap(.5, .9)
        bridge.type_text("hello")
        op = bridge.prepare_message("contact-01", "hello")["approval_id"]

        if use_session:
            session = ApproverSession(
                approver=args.approver, python=sys.executable, child=child,
                control_socket=shared / "agent.sock", shared=shared,
                service_uid=service_uid, approval_socket=socket_path,
                approver_uid=int(approver_uid))
            try:
                session.start()
            except Exception as exc:
                print(json.dumps({
                    "outcome": "blocked",
                    "cross_uid_process_verified": False,
                    "approver_process_real": False,
                    "blocker": "approver_agent_unavailable",
                    "blocker_detail": f"{type(exc).__name__}: {exc}",
                    "on_site_command": on_site_command(args.approver),
                    "checks": checks, "check_count": len(checks),
                }, ensure_ascii=False, indent=2))
                return 0

        def build_command(action, op_id, with_token):
            return build_child_command(
                sys.executable, child, socket_path=socket_path, shared=shared,
                service_uid=service_uid, action=action, approval_id=op_id,
                with_token=with_token)

        if session is not None:
            def call_approver(action, op_id, with_token):
                return session.request(action, op_id, with_token)
            launch_command = session.launch_command
        else:
            def call_approver(action, op_id, with_token):
                return run_as_approver(args.approver,
                                       build_command(action, op_id, with_token),
                                       runner=runner)
            launch_command = build_command("approve", op, True)

        checks["approver_process_available"] = bool(
            session is not None or probe["ok"])
        checks["directory_acl_entry_verified"] = bool(
            verify_acl(service_dir, int(approver_uid))["ok"])
        checks["socket_acl_entry_verified"] = bool(
            verify_acl(socket_path, int(approver_uid))["ok"])
        layout = verify_layout(service_dir, socket_path, service_uid=service_uid)
        checks["layout_still_private"] = bool(
            layout["ok"] and stat.S_IMODE(service_dir.lstat().st_mode) == 0o700
            and stat.S_IMODE(socket_path.lstat().st_mode) == 0o600)
        checks["hosted_isolation_disclosed"] = (
            endpoint.isolation_mode == "token+external_uid")
        checks["token_not_in_child_command"] = token not in json.dumps(launch_command)

        approved = call_approver("approve", op, True)
        if rehearsal_hook is not None:
            rehearsal_hook(bridge, "approve", op)
        checks["real_peer_uid_accepted"] = bool(approved.get("approved") or approved.get("ok"))
        evidence["approver_approve_response"] = approved

        executed = bridge.execute(op)
        checks["one_attributed_record"] = bool(
            executed.get("virtual_message_added") and len(bridge.inspect()["messages"]) == 1)

        replay = call_approver("approve", op, True)
        checks["cross_uid_replay_rejected"] = not replay.get("ok", False)
        evidence["approver_replay_response"] = replay

        no_token = call_approver("list", "", False)
        checks["missing_token_rejected_by_real_peer"] = (
            no_token.get("error") == "approval_token_required")

        service_peer = approval_request(str(socket_path), "list", token=token, hosted=True)
        checks["service_uid_rejected_as_peer"] = (
            service_peer.get("error") == "approval_peer_not_trusted")

        evidence["checks"] = checks
        evidence["check_count"] = len(checks)
        evidence["outcome"] = "passed" if all(checks.values()) else "failed"
        evidence["cross_uid_process_verified"] = bool(
            runner is None and all(checks.values()))
        evidence["on_site_command"] = on_site_command(args.approver)
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1
    except Exception as exc:
        evidence.update({"checks": checks, "check_count": len(checks),
                         "outcome": "failed", "cross_uid_process_verified": False,
                         "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 1
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        if endpoint is not None:
            try:
                endpoint.close()
            except Exception:
                pass
        shutil.rmtree(shared, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
