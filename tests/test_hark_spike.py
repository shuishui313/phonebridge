import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HarkSpikeHarnessTests(unittest.TestCase):
    def assert_host_chain_payload(self, payload: dict):
        self.assertTrue(payload["host_chain_passed"], payload)
        self.assertTrue(payload["partial_chain_passed"], payload)
        self.assertEqual(payload["chain_scope"], "full")
        self.assertEqual(payload["check_count"], 51)
        self.assertFalse(payload["hark_client_verified"])
        checks = payload["checks"]
        self.assertTrue(checks["trusted_side_approval"])
        self.assertTrue(checks["approved_execute_completed"])
        self.assertTrue(checks["unapproved_execute_rejected"])
        self.assertTrue(checks["replay_rejected"])
        self.assertTrue(checks["interrupted_execute_dropped_before_receipt"])
        self.assertTrue(checks["interruption_no_duplicate_record"])
        self.assertTrue(checks["interrupted_operation_replay_rejected"])
        self.assertTrue(checks["reconnect_sessions_share_backend"])
        events = payload["ledger_events"]
        self.assertEqual(events[:4], ["prepared", "approved", "dispatching", "completed"])
        self.assertEqual({event: events.count(event) for event in set(events)},
                         {"prepared": 3, "approved": 2, "dispatching": 2, "completed": 2})
        self.assertEqual(checks["multiple_transport_connections_detail"]["connections"], 9)
        self.assertTrue(payload["interruption"]["verified"])
        self.assertTrue(payload["interruption"]["receipt_dropped"])
        self.assertTrue(payload["interruption"]["replay_rejected"])
        classes = payload["evidence_classes"]
        self.assertEqual(set(classes), {"success", "failure", "interruption", "reconnect"})
        self.assertTrue(all(item["verified"] for item in classes.values()), classes)
        self.assertEqual(classes["reconnect"]["session_count"], 9)

    def _failed_checks(self, payload: dict) -> list[str]:
        checks = payload.get("checks") or {}
        return sorted(
            name for name, value in checks.items()
            if isinstance(value, bool) and not name.endswith("_detail") and value is not True
        )

    def _run(self, command: list[str], timeout: int) -> tuple[subprocess.CompletedProcess, dict]:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", timeout=timeout, cwd=ROOT)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail(f"rc={result.returncode} stdout={result.stdout[-1500:]} stderr={result.stderr[-800:]}")
        if result.returncode != 0 and not payload.get("partial_chain_passed"):
            self.fail(
                f"rc={result.returncode} failed={self._failed_checks(payload)} "
                f"exception={payload.get('exception')}"
            )
        return result, payload

    def test_full_host_chain_with_deterministic_codex_client(self):
        command = [
            sys.executable, str(ROOT / "scripts" / "hark-codex-spike.py"),
            "--deterministic", "--text", "Harness probe", "--phase-timeout", "60",
        ]
        result, payload = self._run(command, 120)
        self.assertEqual(result.returncode, 0, self._failed_checks(payload))
        self.assert_host_chain_payload(payload)

    def test_partial_chain_without_interruption_is_not_a_full_pass(self):
        with tempfile.TemporaryDirectory(prefix="pb-hark-partial-") as td:
            from phonebridge.deterministic_codex import write_deterministic_clients
            normal, _interrupt = write_deterministic_clients(Path(td))
            command = [
                sys.executable, str(ROOT / "scripts" / "hark-codex-spike.py"),
                "--codex-path", str(normal), "--text", "Harness probe", "--phase-timeout", "60",
            ]
            result, payload = self._run(command, 120)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["host_chain_passed"])
        self.assertTrue(payload["partial_chain_passed"])
        self.assertEqual(payload["chain_scope"], "partial")
        self.assertEqual(payload["check_count"], 33)
        self.assertFalse(payload["interruption"]["verified"])
        self.assertEqual(payload["evidence_classes"]["reconnect"]["session_count"], 5)
        self.assertFalse(payload["hark_client_verified"])
        self.assertFalse(payload["hark_ui_verified"])

    def test_full_chain_over_key_only_ssh_loopback(self):
        command = [
            sys.executable, str(ROOT / "scripts" / "hark-codex-ssh-loopback-spike.py"),
            "--phase-timeout", "60",
            "--hark-spike-arg=--deterministic",
            "--hark-spike-arg=--text", "--hark-spike-arg=Harness probe",
        ]
        result, payload = self._run(command, 180)
        self.assertEqual(result.returncode, 0, self._failed_checks(payload))
        self.assertTrue(payload["ssh_loopback_precheck"], payload)
        self.assert_host_chain_payload(payload)


if __name__ == "__main__":
    unittest.main()
