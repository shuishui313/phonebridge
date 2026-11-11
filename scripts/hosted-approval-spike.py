#!/usr/bin/env python3
"""Reproducible evidence for an externally hosted approval endpoint.

Grants real named filesystem ACLs to a second account on macOS, verifies the
ACEs with ls -lde, and exercises peer-UID and token checks over a real Unix
socket. Running the approval CLI as that second UID needs separate operator
authorization, so cross_uid_process_verified stays false.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phonebridge.approvals import ApprovalEndpoint, approval_request
from phonebridge.core import PhoneBridge
from phonebridge.hosted import (account_name, platform_supported,
                                verify_acl, verify_layout)

TOKEN = "spike-only-token"


def second_uid() -> int:
    import pwd
    return pwd.getpwnam("nobody").pw_uid


def main() -> int:
    checks: dict[str, bool] = {}
    evidence: dict = {"platform": sys.platform,
                      "cross_uid_process_verified": False,
                      "real_model_calls": False}
    work = Path(tempfile.mkdtemp(prefix="pb-hosted-spike-"))
    os.chmod(work, 0o700)
    endpoints = []
    try:
        checks["darwin_named_acl_supported"] = platform_supported()
        if not platform_supported():
            evidence.update({"checks": checks, "hosted_passed": False,
                             "error": "acl_platform_unsupported"})
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
            return 1

        approver_uid = second_uid()
        evidence["approver_uid"] = int(approver_uid)
        evidence["approver_name"] = account_name(approver_uid)
        evidence["service_uid"] = os.getuid()

        socket_path = work / "approval.sock"
        endpoint = ApprovalEndpoint(
            PhoneBridge(), str(socket_path), token=TOKEN,
            required_uid=int(approver_uid),
            peer_uid_fn=lambda conn: int(approver_uid), hosted=True).start()
        endpoints.append(endpoint)

        checks["acl_grant_succeeded"] = bool(endpoint.acl_status.get("ok"))
        dir_acl = verify_acl(work, int(approver_uid))
        sock_acl = verify_acl(socket_path, int(approver_uid))
        checks["directory_acl_entry_verified"] = bool(dir_acl["ok"])
        checks["socket_acl_entry_verified"] = bool(sock_acl["ok"])
        evidence["directory_acl"] = dir_acl["listing"]
        evidence["socket_acl"] = sock_acl["listing"]

        layout = verify_layout(work, socket_path, service_uid=os.getuid())
        checks["layout_still_private"] = bool(
            layout["ok"] and stat.S_IMODE(work.lstat().st_mode) == 0o700
            and stat.S_IMODE(socket_path.lstat().st_mode) == 0o600)
        evidence["layout"] = {k: layout[k] for k in ("parent_mode", "socket_mode", "problems")}

        checks["isolation_mode_upgraded"] = (
            endpoint.isolation_mode == "token+external_uid"
            and endpoint.bridge.approval_isolated is True)
        evidence["isolation_mode"] = endpoint.isolation_mode

        bridge = endpoint.bridge
        bridge.open_app("messaging")
        bridge.open_chat("contact-01")
        bridge.tap(.5, .9)
        bridge.type_text("hello")
        op = bridge.prepare_message("contact-01", "hello")["approval_id"]
        approved = approval_request(str(socket_path), "approve", op,
                                    token=TOKEN, hosted=True)
        checks["named_approver_can_approve"] = bool(approved.get("approved"))
        executed = bridge.execute(op)
        checks["one_attributed_record"] = bool(
            executed.get("virtual_message_added") and len(bridge.inspect()["messages"]) == 1)
        replay = approval_request(str(socket_path), "approve", op,
                                  token=TOKEN, hosted=True)
        checks["replay_rejected"] = not replay.get("ok", False)

        missing_token = approval_request(str(socket_path), "list", hosted=True)
        checks["missing_token_rejected"] = (
            missing_token["error"] == "approval_token_required")

        wrong_path = work / "wrong-peer.sock"
        wrong = ApprovalEndpoint(
            PhoneBridge(), str(wrong_path), token=TOKEN,
            required_uid=int(approver_uid),
            peer_uid_fn=lambda conn: 12345, hosted=True).start()
        endpoints.append(wrong)
        rejected = approval_request(str(wrong_path), "list", token=TOKEN, hosted=True)
        checks["wrong_peer_uid_rejected"] = (
            rejected["error"] == "approval_peer_not_trusted")

        plain_path = work / "plain.sock"
        plain = ApprovalEndpoint(PhoneBridge(), str(plain_path),
                                 required_uid=int(approver_uid),
                                 peer_uid_fn=lambda conn: int(approver_uid)).start()
        endpoints.append(plain)
        checks["non_hosted_stays_unverified"] = (
            plain.isolation_mode == "external_uid_unverified")

        steps = endpoint.acl_status.get("steps", [])
        checks["acl_grant_targets_directory_and_socket"] = bool(
            len(steps) == 2 and all(step["returncode"] == 0 for step in steps)
            and {step["target"] for step in steps} == {"directory", "socket"})
        evidence["acl_grant_steps"] = steps

        evidence["checks"] = checks
        evidence["check_count"] = len(checks)
        evidence["hosted_acl_verified"] = bool(
            checks["directory_acl_entry_verified"] and checks["socket_acl_entry_verified"])
        evidence["hosted_passed"] = all(checks.values())
        evidence["note"] = (
            "Named ACLs and peer-UID checks are real; the approver UID is "
            "simulated with an injected peer credential function because "
            "running a process under a different UID needs operator authorization.")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if evidence["hosted_passed"] else 1
    except Exception as exc:
        evidence.update({"checks": checks, "hosted_passed": False,
                         "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 1
    finally:
        for endpoint in reversed(endpoints):
            try:
                endpoint.close()
            except Exception:
                pass
        import shutil
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
