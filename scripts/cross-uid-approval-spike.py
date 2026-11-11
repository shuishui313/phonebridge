#!/usr/bin/env python3
"""Real cross-UID approval acceptance for the externally hosted endpoint.

Needs a host where ``sudo -n -u <approver>`` works without a password. When
it does not, this script records the blocker and reports ``outcome=blocked``;
it never reports an injected peer-credential result as a real cross-UID
process. Run it on site after sudo credentials are available.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
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

CHILD = r'''#!/usr/bin/env python3
"""Runs as the approver UID; reads the token from a file, never from argv."""
import argparse
import json
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--socket", required=True)
p.add_argument("--shared", required=True)
p.add_argument("--service-uid", type=int, required=True)
p.add_argument("--action", required=True)
p.add_argument("--approval-id", default="")
p.add_argument("--with-token", action="store_true")
a = p.parse_args()
sys.path.insert(0, a.shared)
from phonebridge.approvals import approval_request

token = None
if a.with_token:
    token = (Path(a.shared) / "token").read_text(encoding="utf-8").strip()
try:
    result = approval_request(a.socket, a.action, a.approval_id or None,
                              token=token, hosted=True,
                              socket_owner_uid=a.service_uid)
except Exception as exc:
    result = {"ok": False, "error": type(exc).__name__}
print(json.dumps(result, ensure_ascii=False))
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


def blocker_evidence(approver: str, probe: dict) -> dict:
    return {"outcome": "blocked",
            "cross_uid_process_verified": False,
            "approver_process_real": False,
            "approver": approver,
            "blocker": probe.get("error"),
            "blocker_detail": probe.get("detail"),
            "on_site_command": (
                "After sudo credentials are available, run: "
                f"python3 scripts/cross-uid-approval-spike.py --approver {approver}"),
            "checks": {}, "check_count": 0,
            "note": ("Real named ACLs were verified separately by "
                     "scripts/hosted-approval-spike.py; this blocker only "
                     "concerns running the approval client as another UID.")}


def main(argv=None, *, runner=None, rehearsal_hook=None) -> int:
    # runner/rehearsal_hook exist only so the whole flow can be rehearsed with
    # an injected approver process. A real run always uses runner=None and
    # never applies a hook; its evidence says cross_uid_process_verified.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approver", default="nobody", help="Second OS account used for approval")
    parser.add_argument("--probe-only", action="store_true", help="Only check non-interactive sudo")
    args = parser.parse_args(argv)

    probe = probe_sudo(args.approver, runner=runner)
    if args.probe_only:
        print(json.dumps({"sudo_non_interactive_available": probe["ok"],
                          "approver": args.approver, **probe}, ensure_ascii=False, indent=2))
        return 0
    if not probe["ok"]:
        print(json.dumps(blocker_evidence(args.approver, probe), ensure_ascii=False, indent=2))
        return 0
    if not platform_supported():
        print(json.dumps({**blocker_evidence(args.approver, probe),
                          "blocker": "acl_platform_unsupported"}, ensure_ascii=False, indent=2))
        return 0

    import pwd
    approver_uid = pwd.getpwnam(args.approver).pw_uid
    service_uid = os.getuid()
    shared = Path(tempfile.mkdtemp(prefix="pb-crossuid-"))
    endpoint = None
    checks: dict[str, bool] = {}
    evidence: dict = {"approver": args.approver,
                      "approver_uid": int(approver_uid),
                      "service_uid": service_uid,
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

        bridge = PhoneBridge()
        endpoint = ApprovalEndpoint(bridge, str(socket_path), token=token,
                                    required_uid=int(approver_uid), hosted=True).start()
        bridge.open_app("messaging")
        bridge.open_chat("contact-01")
        bridge.tap(.5, .9)
        bridge.type_text("hello")
        op = bridge.prepare_message("contact-01", "hello")["approval_id"]

        checks["sudo_non_interactive_available"] = probe["ok"]
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

        command = build_child_command(sys.executable, child, socket_path=socket_path,
                                      shared=shared, service_uid=service_uid,
                                      action="approve", approval_id=op, with_token=True)
        checks["token_not_in_child_command"] = token not in json.dumps(command)
        approved = run_as_approver(args.approver, command, runner=runner)
        if rehearsal_hook is not None:
            rehearsal_hook(bridge, "approve", op)
        checks["real_peer_uid_accepted"] = bool(approved.get("approved") or approved.get("ok"))
        evidence["approver_approve_response"] = approved

        executed = bridge.execute(op)
        checks["one_attributed_record"] = bool(
            executed.get("virtual_message_added") and len(bridge.inspect()["messages"]) == 1)

        replay = run_as_approver(args.approver, command, runner=runner)
        checks["cross_uid_replay_rejected"] = not replay.get("ok", False)
        evidence["approver_replay_response"] = replay

        no_token_command = build_child_command(
            sys.executable, child, socket_path=socket_path, shared=shared,
            service_uid=service_uid, action="list", approval_id="", with_token=False)
        no_token = run_as_approver(args.approver, no_token_command, runner=runner)
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
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1
    except Exception as exc:
        evidence.update({"checks": checks, "check_count": len(checks),
                         "outcome": "failed", "cross_uid_process_verified": False,
                         "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 1
    finally:
        if endpoint is not None:
            try:
                endpoint.close()
            except Exception:
                pass
        shutil.rmtree(shared, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
