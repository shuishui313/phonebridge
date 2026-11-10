import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phonebridge.core import PhoneBridge
from phonebridge.drivers import AppleSimulatorDriver


UDID = "A4B3C2D1-E0F1-4A7A-B9C1-0123456789AB"

class FakeResult:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class SimulatorTests(unittest.TestCase):
    def test_rejects_bad_udid(self):
        with self.assertRaises(ValueError): AppleSimulatorDriver(";rm -rf /tmp")

    def test_status_parses_devices(self):
        output = json.dumps({"devices": {"iOS 27.0": [{"udid": UDID, "name": "iPhone 17", "state": "Booted"}]}})
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return FakeResult(stdout=output)
        d = AppleSimulatorDriver(UDID, runner=runner)
        self.assertEqual(d.status()["device"], "iPhone 17")
        self.assertEqual(calls[0], ["xcrun", "simctl", "list", "devices", "available", "--json"])

    def test_missing_simulator(self):
        d = AppleSimulatorDriver(UDID, runner=lambda *a, **k: FakeResult(stdout='{"devices":{}}'))
        self.assertEqual(d.status()["error"], "simulator_not_found")

    def test_simctl_not_available(self):
        def runner(*a, **kw): raise FileNotFoundError("xcrun")
        d = AppleSimulatorDriver(UDID, runner=runner)
        self.assertEqual(d.status()["error"], "simctl_unavailable")

    def test_only_fixture_launch(self):
        d = AppleSimulatorDriver(UDID, runner=lambda *a, **k: FakeResult())
        self.assertEqual(d.open_app("wechat")["error"], "unsupported_app")
        self.assertTrue(d.open_app("fixture")["ok"])

    def test_tap_is_explicitly_unsupported(self):
        d = AppleSimulatorDriver(UDID, runner=lambda *a, **k: FakeResult())
        self.assertEqual(PhoneBridge(d).tap(.5, .5)["error"], "unsupported_by_simctl")

    def test_screenshot_driver(self):
        with tempfile.TemporaryDirectory() as td:
            def runner(cmd, **kw):
                if "screenshot" in cmd:
                    Path(cmd[-1]).write_bytes(b"fake-png-test-only")
                return FakeResult()
            d = AppleSimulatorDriver(UDID, runner=runner, screenshot_dir=td)
            result = d.observe()
            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["screenshot_path"]).stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(result["screenshot_path"]).parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(len(result["sha256"]), 64)

    def test_failed_screenshot_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as td:
            def runner(cmd, **kwargs):
                Path(cmd[-1]).write_bytes(b"partial")
                return FakeResult(returncode=1)
            driver = AppleSimulatorDriver(UDID, runner=runner, screenshot_dir=td)
            self.assertEqual(driver.observe()["error"], "simctl_failed")
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_prepare_message_on_simulator_is_blocked(self):
        d = AppleSimulatorDriver(UDID, runner=lambda *a, **k: FakeResult())
        b = PhoneBridge(d)
        self.assertEqual(b.prepare_message("foo", "hi")["error"], "real_message_sending_disabled")


if __name__ == "__main__": unittest.main()
