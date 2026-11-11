"""Step 2: durable per-operation approval, single submission entry, recovery."""
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from phonebridge.api import CoreAPI
from phonebridge.approvals import ApprovalEndpoint, approval_request
from phonebridge.cli import main as cli_main
from phonebridge.core import PhoneBridge
from phonebridge.drivers import MockIPhone
from phonebridge.submissions import (SimulatedCrash, SubmissionLedger,
                                     submission_evidence)

TEXT = "测试消息"


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class BypassDriver(MockIPhone):
    """Simulates an Agent clicking Send through generic interaction commands."""

    def tap(self, x, y):
        result = super().tap(x, y)
        if self.screen == "chat" and self.input_text:
            self.virtual_send(self.selected_id, self.input_text)
        return result


def prepared(*, ledger=None, hooks=None, clock=None, driver=None, ttl=300):
    bridge = PhoneBridge(driver or MockIPhone(), ledger=ledger,
                         submission_hooks=hooks, clock=clock, approval_ttl=ttl)
    bridge.open_app("messaging")
    bridge.open_chat("contact-02")
    bridge.tap(.5, .9)
    bridge.type_text(TEXT)
    op_id = bridge.prepare_message("contact-02", TEXT)["approval_id"]
    return bridge, op_id


def approved(*, ledger=None, hooks=None, clock=None, driver=None, ttl=300):
    bridge, op_id = prepared(ledger=ledger, hooks=hooks, clock=clock, driver=driver, ttl=ttl)
    assert bridge.approve(op_id)["ok"]
    return bridge, op_id


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="pb-submissions-")
        self.addCleanup(self.t.cleanup)
        self.dir = Path(self.t.name)
        self.ledger_path = str(self.dir / "submissions.jsonl")

    def ledger(self):
        return SubmissionLedger(self.ledger_path)

    def records(self):
        return [json.loads(line) for line in
                Path(self.ledger_path).read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- state, privacy and the happy path ---------------------------------

    def test_ledger_is_private_and_stores_no_plaintext(self):
        ledger = self.ledger()
        bridge, op_id = approved(ledger=ledger)
        self.assertTrue(bridge.execute(op_id)["ok"])
        info = os.stat(self.ledger_path)
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        raw = Path(self.ledger_path).read_text(encoding="utf-8")
        self.assertNotIn(TEXT, raw)
        for record in self.records():
            for forbidden in ("message", "text", "body", "content"):
                self.assertNotIn(forbidden, record)
        events = [r["event"] for r in self.records() if r["op_id"] == op_id]
        self.assertEqual(events, ["prepared", "approved", "dispatching", "completed"])

    def test_approved_submission_adds_exactly_one_matching_record(self):
        ledger = self.ledger()
        bridge, op_id = approved(ledger=ledger)
        result = bridge.execute(op_id)
        self.assertEqual(result["submission_state"], "completed")
        self.assertTrue(result["verification"]["matched"])
        self.assertEqual(len(bridge.inspect()["messages"]), 1)
        self.assertEqual(bridge.inspect()["messages"][0]["text"], TEXT)
        self.assertEqual(ledger.state(op_id).state, "completed")

    def test_summary_is_disclosed_in_status(self):
        ledger = self.ledger()
        bridge, op_id = approved(ledger=ledger)
        api = CoreAPI(bridge)
        summary = api.status()["submissions"]
        self.assertTrue(summary["persistent"])
        self.assertFalse(summary["stores_plaintext"])
        self.assertFalse(summary["approval_isolated"])
        self.assertEqual(summary["by_state"]["approved"], 1)
        bridge.execute(op_id)
        self.assertEqual(api.status()["submissions"]["by_state"]["completed"], 1)

    # --- rejections ---------------------------------------------------------

    def test_unapproved_is_rejected(self):
        bridge, op_id = prepared(ledger=self.ledger())
        self.assertEqual(bridge.execute(op_id)["error"], "independent_approval_required")
        self.assertEqual(len(bridge.inspect()["messages"]), 0)

    def test_target_change_is_rejected(self):
        bridge, op_id = approved(ledger=self.ledger())
        self.assertTrue(bridge.back()["ok"])
        self.assertTrue(bridge.open_chat("contact-01")["ok"])
        self.assertEqual(bridge.execute(op_id)["error"], "stale_or_expired_proposal")
        self.assertEqual(len(bridge.inspect()["messages"]), 0)

    def test_body_change_is_rejected(self):
        bridge, op_id = approved(ledger=self.ledger())
        bridge.type_text("changed")
        self.assertEqual(bridge.execute(op_id)["error"], "stale_or_expired_proposal")

    def test_expiry_is_rejected(self):
        clock = FakeClock()
        bridge, op_id = approved(ledger=self.ledger(), clock=clock, ttl=10)
        clock.advance(11)
        self.assertEqual(bridge.execute(op_id)["error"], "stale_or_expired_proposal")

    def test_replay_is_rejected(self):
        bridge, op_id = approved(ledger=self.ledger())
        self.assertTrue(bridge.execute(op_id)["ok"])
        self.assertEqual(bridge.execute(op_id)["error"], "unknown_or_processed_operation")
        self.assertEqual(len(bridge.inspect()["messages"]), 1)

    def test_concurrent_execution_submits_once(self):
        bridge, op_id = approved(ledger=self.ledger())
        results = []
        lock = threading.Lock()

        def run():
            outcome = bridge.execute(op_id)
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r.get("ok")), 1)
        self.assertEqual(len(bridge.inspect()["messages"]), 1)

    # --- crash recovery -----------------------------------------------------

    def test_crash_before_send_pauses_without_resend(self):
        ledger = self.ledger()
        driver = MockIPhone()
        bridge, op_id = approved(ledger=ledger, driver=driver,
                                 hooks={"crash_before_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op_id)
        self.assertEqual(len(driver.messages["contact-02"]), 0)
        restarted = PhoneBridge(driver, ledger=ledger)
        report = restarted.recover_submissions()
        self.assertEqual(report["pending_pause"], 1)
        self.assertEqual(report["recovered"][0]["prior_state"], "dispatching")
        self.assertFalse(report["recovered"][0]["confirmed"])
        self.assertFalse(report["recovered"][0]["resent"])
        self.assertEqual(len(driver.messages["contact-02"]), 0)
        self.assertEqual(ledger.state(op_id).state, "unresolved")
        self.assertEqual(restarted.execute(op_id)["error"], "unknown_or_processed_operation")

    def test_lost_receipt_is_confirmed_by_readback_and_never_resent(self):
        ledger = self.ledger()
        driver = MockIPhone()
        bridge, op_id = approved(ledger=ledger, driver=driver,
                                 hooks={"crash_after_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op_id)
        self.assertEqual(len(driver.messages["contact-02"]), 1)
        restarted = PhoneBridge(driver, ledger=ledger)
        report = restarted.recover_submissions()
        self.assertEqual(report["pending_pause"], 0)
        self.assertTrue(report["recovered"][0]["confirmed"])
        self.assertFalse(report["recovered"][0]["resent"])
        self.assertEqual(ledger.state(op_id).state, "completed")
        self.assertEqual(len(driver.messages["contact-02"]), 1)  # no duplicate
        self.assertEqual(restarted.execute(op_id)["error"], "unknown_or_processed_operation")
        self.assertEqual(len(driver.messages["contact-02"]), 1)

    def test_lost_receipt_without_confirmation_pauses(self):
        ledger = self.ledger()
        driver = MockIPhone()
        bridge, op_id = approved(ledger=ledger, driver=driver,
                                 hooks={"crash_after_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op_id)
        # Device state could not be observed after restart; do not guess.
        restarted = PhoneBridge(MockIPhone(), ledger=ledger)
        report = restarted.recover_submissions()
        self.assertEqual(report["pending_pause"], 1)
        self.assertFalse(report["recovered"][0]["resent"])
        self.assertEqual(ledger.state(op_id).state, "unresolved")

    def test_approved_but_not_dispatched_is_paused_on_restart(self):
        ledger = self.ledger()
        bridge, op_id = approved(ledger=ledger)
        restarted = PhoneBridge(bridge.driver, ledger=ledger)
        report = restarted.recover_submissions()
        self.assertEqual(report["recovered"][0]["prior_state"], "approved")
        self.assertEqual(report["pending_pause"], 1)
        self.assertEqual(len(bridge.inspect()["messages"]), 0)

    def test_recovery_is_idempotent(self):
        ledger = self.ledger()
        driver = MockIPhone()
        bridge, op_id = approved(ledger=ledger, driver=driver,
                                 hooks={"crash_after_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op_id)
        PhoneBridge(driver, ledger=ledger).recover_submissions()
        again = PhoneBridge(driver, ledger=ledger).recover_submissions()
        self.assertEqual(again["recovered"], [])

    # --- single submission entry and bypass detection -----------------------

    def test_only_one_module_calls_virtual_send(self):
        root = Path(__file__).resolve().parents[1] / "phonebridge"
        callers = []
        for path in root.glob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "virtual_send(" in line and "def virtual_send" not in line:
                    callers.append(f"{path.name}:{number}")
        self.assertTrue(callers, "virtual_send must have exactly one caller")
        self.assertEqual({c.split(":")[0] for c in callers}, {"submissions.py"})

    def test_generic_interaction_bypass_is_detected_and_paused(self):
        driver = BypassDriver()
        bridge = PhoneBridge(driver, ledger=self.ledger())
        api = CoreAPI(bridge)
        api.dispatch("phone_open_app", {"app": "messaging"})
        api.dispatch("contacts_open", {"contact": "Casey"})
        api.dispatch("phone_tap", {"x": .5, "y": .9})
        api.dispatch("phone_type_text", {"text": "unapproved send"})
        result = api.dispatch("phone_tap", {"x": .5, "y": .9})
        self.assertTrue(result["verification"]["submission_bypass_detected"])
        self.assertTrue(result["verification"]["pause"])
        self.assertEqual(result["verification"]["outcome"], "unclear")
        self.assertFalse(result["verification"]["automatic_retry"])

    def test_approved_submission_is_not_flagged_as_bypass(self):
        bridge, op_id = approved(ledger=self.ledger())
        api = CoreAPI(bridge)
        result = api.dispatch("phone_execute_operation", {"approval_id": op_id})
        self.assertTrue(result["ok"])
        self.assertNotIn("submission_bypass_detected", result["verification"])

    def test_agent_cannot_reach_approval_actions(self):
        api = CoreAPI(PhoneBridge(ledger=self.ledger()))
        for name in ("approve", "deny", "approval_list", "recover_submissions",
                     "reset_task"):
            self.assertEqual(api.dispatch(name, {})["error"], "unknown_action")


class ApprovalIsolationTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="pb-approval-")
        self.addCleanup(self.t.cleanup)
        self.path = str(Path(self.t.name) / "approval.sock")

    def endpoint(self, **kw):
        bridge, op_id = prepared()
        endpoint = ApprovalEndpoint(bridge, self.path, **kw).start()
        self.addCleanup(endpoint.close)
        return bridge, op_id, endpoint

    def test_token_is_required_and_verified(self):
        bridge, op_id, endpoint = self.endpoint(token="trusted-secret")
        self.assertTrue(endpoint.isolated)
        self.assertTrue(bridge.approval_isolated)
        self.assertEqual(approval_request(self.path, "list")["error"], "approval_token_required")
        self.assertEqual(approval_request(self.path, "approve", op_id, token="wrong")["error"],
                         "approval_token_required")
        self.assertTrue(approval_request(self.path, "approve", op_id,
                                         token="trusted-secret")["approved"])
        self.assertTrue(bridge.execute(op_id)["ok"])

    def test_peer_uid_must_match_when_configured(self):
        other = os.getuid() + 1
        bridge, op_id, endpoint = self.endpoint(required_uid=other,
                                                peer_uid_fn=lambda conn: os.getuid())
        self.assertTrue(endpoint.isolated)
        self.assertEqual(approval_request(self.path, "approve", op_id)["error"],
                         "approval_peer_not_trusted")
        self.assertFalse(bridge.execute(op_id)["ok"])

    def test_peer_uid_match_allows_approval(self):
        other = os.getuid() + 1
        bridge, op_id, _ = self.endpoint(required_uid=other,
                                         peer_uid_fn=lambda conn: other)
        self.assertTrue(approval_request(self.path, "approve", op_id)["approved"])
        self.assertTrue(bridge.execute(op_id)["ok"])

    def test_same_uid_configuration_is_refused(self):
        bridge, _ = prepared()
        with self.assertRaises(PermissionError):
            ApprovalEndpoint(bridge, self.path, required_uid=os.getuid()).start()

    def test_demo_mode_discloses_that_approval_is_not_isolated(self):
        bridge, _, endpoint = self.endpoint()
        self.assertFalse(endpoint.isolated)
        self.assertFalse(bridge.approval_isolated)
        self.assertFalse(CoreAPI(bridge).status()["submissions"]["approval_isolated"])

    def test_cli_requires_isolation_inputs(self):
        with self.assertRaises(SystemExit):
            cli_main(["--require-approval-isolation", "serve"])
        with self.assertRaises(SystemExit):
            cli_main(["--require-approval-isolation", "--approval-socket", self.path, "serve"])

    def test_uid_only_mode_is_not_reported_as_isolated(self):
        other = os.getuid() + 1
        bridge, _, endpoint = self.endpoint(required_uid=other,
                                            peer_uid_fn=lambda conn: other)
        self.assertEqual(endpoint.isolation_mode, "external_uid_unverified")
        self.assertTrue(endpoint.isolated)  # a control is configured
        self.assertFalse(bridge.approval_isolated)  # but it is not usable isolation
        summary = CoreAPI(bridge).status()["submissions"]
        self.assertFalse(summary["approval_isolated"])
        self.assertEqual(summary["approval_isolation_mode"], "external_uid_unverified")

    def test_cli_rejects_uid_without_token(self):
        with self.assertRaises(SystemExit):
            cli_main(["--require-approval-isolation", "--approval-socket", self.path,
                      "--approval-uid", str(os.getuid() + 1),
                      "--submission-state", str(Path(self.t.name) / "s.jsonl"), "serve"])


class FailingLedger:
    def __init__(self, inner):
        self.inner = inner
        self.fail_approved = True

    def append(self, event, **fields):
        if event == "approved" and self.fail_approved:
            raise OSError("simulated ledger write failure")
        return self.inner.append(event, **fields)

    def transaction(self):
        return self.inner.transaction()

    def replay(self):
        return self.inner.replay()

    def state(self, op_id):
        return self.inner.state(op_id)

    def summary(self):
        return self.inner.summary()


class ReviewRegressionTests(unittest.TestCase):
    """Counter-examples reproduced during the review of 711ab2a."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory(prefix="pb-review-")
        self.addCleanup(self.t.cleanup)
        self.path = str(Path(self.t.name) / "submissions.jsonl")

    def ledger(self):
        return SubmissionLedger(self.path)

    def test_recovery_ignores_same_text_on_another_contact(self):
        ledger = self.ledger()
        driver = MockIPhone()
        driver.messages["contact-01"].append({"direction": "outbound", "text": TEXT})
        bridge, op = approved(ledger=ledger, driver=driver,
                              hooks={"crash_before_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op)
        report = PhoneBridge(driver, ledger=ledger).recover_submissions()
        self.assertEqual(report["pending_pause"], 1)
        self.assertFalse(report["recovered"][0]["confirmed"])
        self.assertEqual(ledger.state(op).state, "unresolved")
        self.assertEqual(len(driver.messages["contact-02"]), 0)

    def test_recovery_ignores_historical_same_text_on_target(self):
        ledger = self.ledger()
        driver = MockIPhone()
        driver.messages["contact-02"].append({"direction": "outbound", "text": TEXT})
        bridge, op = approved(ledger=ledger, driver=driver,
                              hooks={"crash_before_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op)
        report = PhoneBridge(driver, ledger=ledger).recover_submissions()
        self.assertEqual(report["pending_pause"], 1)
        self.assertEqual(ledger.state(op).state, "unresolved")
        self.assertEqual(len(driver.messages["contact-02"]), 1)

    def test_lost_receipt_is_confirmed_against_baseline(self):
        ledger = self.ledger()
        driver = MockIPhone()
        driver.messages["contact-02"].append({"direction": "outbound", "text": "earlier"})
        bridge, op = approved(ledger=ledger, driver=driver,
                              hooks={"crash_after_send": lambda: None})
        with self.assertRaises(SimulatedCrash):
            bridge.execute(op)
        report = PhoneBridge(driver, ledger=ledger).recover_submissions()
        self.assertEqual(report["pending_pause"], 0)
        self.assertTrue(report["recovered"][0]["confirmed"])
        self.assertEqual(ledger.state(op).state, "completed")
        self.assertEqual(len(driver.messages["contact-02"]), 2)

    def test_unattributable_records_stay_unclear(self):
        class HierarchyDriver:
            def observe(self):
                return {"ok": True, "hierarchy_path": "/nonexistent"}

        evidence = submission_evidence(HierarchyDriver(), "contact-02", "0" * 64, 0)
        self.assertTrue(evidence["unclear"])
        self.assertFalse(evidence["matched"])

    def test_ledger_write_failure_does_not_allow_dispatch(self):
        ledger = self.ledger()
        driver = MockIPhone()
        bridge, op = prepared(ledger=ledger, driver=driver)
        failing = FailingLedger(ledger)
        bridge.submissions.ledger = failing
        self.assertEqual(bridge.approve(op)["error"], "approval_not_durable")
        failing.fail_approved = False
        bridge.proposals[op].approved = True  # simulate a forged in-memory approval
        self.assertEqual(bridge.execute(op)["error"], "approval_not_durable")
        self.assertEqual(len(driver.messages["contact-02"]), 0)

    def test_dispatch_requires_durable_approval_event(self):
        ledger = self.ledger()
        bridge, op = prepared(ledger=ledger)
        bridge.proposals[op].approved = True
        self.assertEqual(bridge.execute(op)["error"], "approval_not_durable")

    def test_bypass_enters_persistent_pause(self):
        driver = BypassDriver()
        api = CoreAPI(PhoneBridge(driver, ledger=self.ledger()))
        api.dispatch("phone_open_app", {"app": "messaging"})
        api.dispatch("contacts_open", {"contact": "Casey"})
        api.dispatch("phone_tap", {"x": .5, "y": .9})
        api.dispatch("phone_type_text", {"text": "unapproved send"})
        result = api.dispatch("phone_tap", {"x": .5, "y": .9})
        self.assertTrue(result["verification"]["submission_bypass_detected"])
        self.assertEqual(api.dispatch("phone_home")["error"], "paused_pending_readback")


if __name__ == "__main__":
    unittest.main()
