import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
