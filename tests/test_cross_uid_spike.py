import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "cross_uid_approval_spike", ROOT / "scripts" / "cross-uid-approval-spike.py")
if _spec is None:
    raise ImportError("cannot load cross-uid-approval-spike.py")
_loader = _spec.loader
if _loader is None:
    raise ImportError("cross-uid-approval-spike.py has no loader")
spike = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = spike
_loader.exec_module(spike)

from phonebridge.hosted import platform_supported  # noqa: E402


def result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def capture(argv, runner, rehearsal_hook=None):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = spike.main(argv, runner=runner, rehearsal_hook=rehearsal_hook)
    return code, json.loads(out.getvalue())


class CrossUIDSpikeTests(unittest.TestCase):
    def test_probe_only_reports_sudo_availability(self):
        code, payload = capture(["--probe-only"], lambda command, **kw: result(stdout="4294967294\n"))
        self.assertEqual(code, 0)
        self.assertTrue(payload["sudo_non_interactive_available"])

    def test_blocker_is_recorded_without_claiming_success(self):
        failing = lambda command, **kw: result(returncode=1, stderr="sudo: a password is required")
        code, payload = capture([], failing)
        self.assertEqual(code, 0)
        self.assertEqual(payload["outcome"], "blocked")
        self.assertFalse(payload["cross_uid_process_verified"])
        self.assertEqual(payload["blocker"], "sudo_non_interactive_unavailable")
        self.assertIn("cross-uid-approval-spike.py", payload["on_site_command"])
        self.assertIn("--authenticate", payload["on_site_command"])
        self.assertIn(str(ROOT), payload["on_site_command"])
        self.assertEqual(payload["check_count"], 0)

    def test_child_command_never_carries_the_token(self):
        token = "super-secret-token"
        command = spike.build_child_command(
            sys.executable, Path("/tmp/child.py"),
            socket_path=Path("/tmp/service/approval.sock"), shared=Path("/tmp/shared"),
            service_uid=os.getuid(), action="approve", approval_id="op-1", with_token=True)
        self.assertIn("--with-token", command)
        self.assertIn("--service-uid", command)
        self.assertIn("op-1", command)
        self.assertNotIn(token, json.dumps(command))

    def test_run_as_approver_handles_failure_and_bad_output(self):
        failed = spike.run_as_approver("nobody", ["/usr/bin/true"],
                                       runner=lambda command, **kw: result(returncode=1, stderr="denied"))
        self.assertEqual(failed["error"], "approver_process_failed")
        bad = spike.run_as_approver("nobody", ["/usr/bin/true"],
                                    runner=lambda command, **kw: result(stdout="not json"))
        self.assertEqual(bad["error"], "invalid_child_output")
        good = spike.run_as_approver("nobody", ["/usr/bin/true"],
                                     runner=lambda command, **kw: result(stdout='{"ok": true}'))
        self.assertTrue(good["ok"])

    def test_full_logic_with_injected_approver_process(self):
        if not platform_supported():
            self.skipTest("named filesystem ACLs are only implemented on macOS")
        state = {"approve": 0}

        def fake_sudo(command, **kw):
            if command[-2:] == ["/usr/bin/id", "-u"]:
                return result(stdout="4294967294\n")
            if "--action" in command and "approve" in command and "--with-token" in command:
                state["approve"] += 1
                if state["approve"] == 1:
                    return result(stdout='{"ok": true, "approved": true}')
                return result(stdout='{"ok": false, "error": "submission_already_processed"}')
            return result(stdout='{"ok": false, "error": "approval_token_required"}')

        def rehearsed_approval(bridge, action, op):
            bridge.approve(op)

        code, payload = capture([], fake_sudo, rehearsal_hook=rehearsed_approval)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["outcome"], "passed", payload)
        self.assertFalse(payload["cross_uid_process_verified"])
        self.assertFalse(payload["approver_process_real"])
        self.assertGreaterEqual(payload["check_count"], 10)
        self.assertEqual([k for k, v in payload["checks"].items() if not v], [], payload)
        self.assertEqual(set(payload["checks"]), set(spike.REQUIRED_CHECKS))

    def test_root_execution_is_refused_with_the_exact_on_site_command(self):
        with mock.patch.object(spike.os, "geteuid", return_value=0), \
             mock.patch.object(spike, "probe_sudo",
                               return_value={"ok": True, "reported_uid": "0"}):
            code, payload = capture([], runner=None)
        self.assertEqual(code, 0)
        self.assertEqual(payload["outcome"], "blocked")
        self.assertEqual(payload["blocker"], "script_must_not_run_as_root")
        self.assertFalse(payload["cross_uid_process_verified"])
        self.assertIn("--authenticate", payload["on_site_command"])
        self.assertIn(str(ROOT), payload["on_site_command"])

    def test_authenticated_session_launches_one_agent_without_token(self):
        session = spike.ApproverSession(
            approver="nobody", python=sys.executable,
            child=ROOT / "scripts" / "child.py",
            control_socket=ROOT / "agent.sock", shared=ROOT,
            service_uid=os.getuid(),
            approval_socket=ROOT / "service" / "approval.sock",
            approver_uid=4294967294)
        command = session.launch_command
        self.assertEqual(command[:3], ["sudo", "-u", "nobody"])
        self.assertNotIn("-n", command)
        self.assertIn("--control-socket", command)
        self.assertIn("--socket", command)
        self.assertNotIn("super-secret-token", json.dumps(command))
        self.assertIn("--authenticate", spike.on_site_command("nobody"))

    def test_shared_paths_are_readable_by_another_uid(self):
        self.assertEqual(str(spike.public_temp_parent()), "/tmp")
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            work = Path(tmp)
            os.chmod(work, 0o755)
            child = work / "child.py"
            child.write_text("pass", encoding="utf-8")
            child.chmod(0o755)
            self.assertTrue(spike.path_readable_by_other(child))
            private = work / "private"
            private.mkdir(mode=0o700)
            hidden = private / "child.py"
            hidden.write_text("pass", encoding="utf-8")
            hidden.chmod(0o755)
            self.assertFalse(spike.path_readable_by_other(hidden))

def sample_evidence(**changes):
    """Synthetic on-site result; no real UIDs, tokens, paths or messages."""
    payload = {
        "approver": "approver-account",
        "approver_uid": 2002,
        "service_uid": 2001,
        "authenticate_mode": True,
        "approver_process_real": True,
        "real_model_calls": False,
        "approver_approve_response": {"ok": True, "approved": True},
        "approver_replay_response": {"ok": False, "error": "unknown_or_processed_approval"},
        "checks": {name: True for name in spike.REQUIRED_CHECKS},
        "check_count": len(spike.REQUIRED_CHECKS),
        "outcome": "passed",
        "cross_uid_process_verified": True,
        "on_site_command": ("cd <repo> && python3 scripts/cross-uid-approval-spike.py "
                            "--approver approver-account --authenticate"),
    }
    payload.update(changes)
    return payload


class EvidenceVerificationTests(unittest.TestCase):
    def test_real_process_evidence_is_accepted(self):
        verdict = spike.verify_evidence(sample_evidence())
        self.assertTrue(verdict["evidence_verified"], verdict)
        self.assertEqual(verdict["reasons"], [])
        self.assertEqual(verdict["failed_checks"], [])
        self.assertEqual(verdict["required_check_count"], 15)
        self.assertEqual(verdict["check_count"], 15)

    def test_rehearsal_evidence_is_rejected(self):
        verdict = spike.verify_evidence(sample_evidence(
            approver_process_real=False, cross_uid_process_verified=False))
        self.assertFalse(verdict["evidence_verified"])
        self.assertIn("approver_process_not_real", verdict["reasons"])
        self.assertIn("cross_uid_process_not_verified", verdict["reasons"])

    def test_missing_false_or_unknown_checks_are_rejected(self):
        checks = {name: True for name in spike.REQUIRED_CHECKS}
        checks["one_attributed_record"] = False
        verdict = spike.verify_evidence(sample_evidence(checks=checks))
        self.assertFalse(verdict["evidence_verified"])
        self.assertEqual(verdict["failed_checks"], ["one_attributed_record"])

        incomplete = {name: True for name in spike.REQUIRED_CHECKS}
        del incomplete["service_uid_rejected_as_peer"]
        verdict = spike.verify_evidence(sample_evidence(checks=incomplete, check_count=14))
        self.assertFalse(verdict["evidence_verified"])
        self.assertIn("checks_not_all_true", verdict["reasons"])
        self.assertIn("check_count_mismatch", verdict["reasons"])

        extra = {**{name: True for name in spike.REQUIRED_CHECKS}, "invented_check": True}
        verdict = spike.verify_evidence(sample_evidence(checks=extra, check_count=16))
        self.assertFalse(verdict["evidence_verified"])
        self.assertEqual(verdict["unexpected_checks"], ["invented_check"])

    def test_inconsistent_identity_and_responses_are_rejected(self):
        changes = [
            ({"approver_uid": 2001}, "uids_not_distinct"),
            ({"real_model_calls": True}, "model_calls_not_excluded"),
            ({"outcome": "blocked"}, "outcome_not_passed"),
            ({"approver_approve_response": {"ok": False}}, "approve_response_not_approved"),
            ({"approver_replay_response": {"ok": True}}, "replay_not_rejected"),
            ({"on_site_command": "python3 -c pass"}, "on_site_command_missing"),
            ({"check_count": 14}, "check_count_mismatch"),
        ]
        for change, reason in changes:
            with self.subTest(reason=reason):
                verdict = spike.verify_evidence(sample_evidence(**change))
                self.assertFalse(verdict["evidence_verified"])
                self.assertIn(reason, verdict["reasons"])

    def test_non_object_evidence_is_rejected(self):
        for payload in ([], None, "passed", 15):
            with self.subTest(payload=payload):
                self.assertFalse(spike.verify_evidence(payload)["evidence_verified"])

    def test_cli_reads_saved_result_from_stdin_and_file(self):
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(sample_evidence()))):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = spike.main(["--verify-evidence", "-"], runner=None)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())["evidence_verified"])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(sample_evidence()), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = spike.main(["--verify-evidence", str(path)], runner=None)
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out.getvalue())["evidence_verified"])

    def test_cli_rejects_bad_json_and_tampered_evidence(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = spike.main(["--verify-evidence", "/definitely/missing/result.json"], runner=None)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["evidence_verified"])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(json.dumps(sample_evidence(cross_uid_process_verified=False)),
                            encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = spike.main(["--verify-evidence", str(path)], runner=None)
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out.getvalue())["evidence_verified"])

        with mock.patch.object(sys, "stdin", io.StringIO("not json")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = spike.main(["--verify-evidence", "-"], runner=None)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["evidence_verified"])


if __name__ == "__main__":
    unittest.main()
