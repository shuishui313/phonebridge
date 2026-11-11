import unittest

from phonebridge.api import ACTION_SPECS, CoreAPI
from phonebridge.core import PhoneBridge
from phonebridge.drivers import MockIPhone
from phonebridge.mcp_v04 import MCPServer, PARAMS


class FakeClock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class UnclearDriver(MockIPhone):
    def observe(self):
        return {"ok": False, "error": "fresh_observation_unavailable"}


class ExplodingDriver(MockIPhone):
    def observe(self):
        raise RuntimeError("bridge exploded")


class CoreAPITests(unittest.TestCase):
    def api(self, *, driver=None, clock=None, **kw):
        return CoreAPI(PhoneBridge(driver or MockIPhone()), clock=clock, **kw)

    def test_contract_matches_mcp_tools(self):
        self.assertEqual(set(PARAMS), set(ACTION_SPECS))
        for name in PARAMS:
            self.assertEqual(set(PARAMS[name]), set(ACTION_SPECS[name][1]))
        server = MCPServer(self.api())
        server.process({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "x", "version": "1"}}})
        server.process({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = server.process({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertEqual(len(tools), 12)

    def test_dispatch_validation_and_no_approval_actions(self):
        api = self.api(action_budget=100, max_read_retries=100)
        self.assertTrue(api.dispatch("phone_status")["ok"])
        self.assertEqual(api.dispatch("does_not_exist")["error"], "unknown_action")
        self.assertEqual(api.dispatch("approve", {"op_id": "x"})["error"], "unknown_action")
        self.assertEqual(api.dispatch("deny", {"op_id": "x"})["error"], "unknown_action")
        self.assertEqual(api.dispatch("reset_task")["error"], "unknown_action")
        self.assertEqual(api.dispatch("phone_tap", {"x": .5})["error"], "invalid_parameters")
        self.assertEqual(api.dispatch("phone_tap", {"x": "0.5", "y": .5})["error"],
                         "invalid_parameter_type")

    def test_invalid_limits_are_rejected(self):
        for kw in ({"time_limit": 0}, {"action_budget": 0}, {"max_read_retries": 0},
                   {"max_retries": -1}):
            with self.assertRaises(ValueError):
                self.api(**kw)

    def test_time_limit_blocks_state_changes_but_not_reads(self):
        clock = FakeClock()
        api = self.api(clock=clock, time_limit=10, action_budget=100, max_read_retries=100)
        self.assertTrue(api.dispatch("phone_open_app", {"app": "messaging"})["ok"])
        clock.advance(11)
        self.assertEqual(api.dispatch("phone_tap", {"x": .5, "y": .9})["error"],
                         "task_time_limit_exceeded")
        self.assertTrue(api.dispatch("phone_status")["ok"])
        api.reset_task()
        self.assertTrue(api.dispatch("phone_home")["ok"])
        self.assertTrue(api.dispatch("phone_tap", {"x": .2, "y": .3})["ok"])

    def test_action_budget_exhausted(self):
        api = self.api(action_budget=2, time_limit=1000, max_read_retries=100)
        self.assertTrue(api.dispatch("phone_open_app", {"app": "messaging"})["ok"])
        self.assertTrue(api.dispatch("phone_home")["ok"])
        self.assertEqual(api.dispatch("phone_back")["error"], "action_budget_exhausted")
        self.assertTrue(api.dispatch("phone_inspect")["ok"])
        api.reset_task()
        self.assertTrue(api.dispatch("phone_home")["ok"])

    def test_read_only_actions_do_not_consume_budget(self):
        api = self.api(action_budget=1, time_limit=1000, max_read_retries=100)
        for _ in range(5):
            self.assertTrue(api.dispatch("phone_status")["ok"])
            self.assertTrue(api.dispatch("phone_inspect")["ok"])
        self.assertTrue(api.dispatch("phone_home")["ok"])
        self.assertEqual(api.dispatch("phone_back")["error"], "action_budget_exhausted")

    def test_consecutive_read_retry_limit(self):
        api = self.api(max_read_retries=2, action_budget=100)
        self.assertTrue(api.dispatch("phone_status")["ok"])
        self.assertTrue(api.dispatch("phone_status")["ok"])
        self.assertEqual(api.dispatch("phone_status")["error"], "read_retry_limit_exceeded")
        self.assertTrue(api.dispatch("phone_inspect")["ok"])
        self.assertTrue(api.dispatch("phone_status")["ok"])

    def test_failure_retry_advice_is_bounded_and_resets_on_success(self):
        api = self.api(max_retries=2, action_budget=100, time_limit=1000)
        bad = {"x": 2, "y": .5}
        for attempt in (1, 2):
            result = api.dispatch("phone_tap", bad)
            self.assertEqual(result["error"], "normalized_coordinates_required")
            self.assertEqual(result["retry"], {"allowed": True, "attempt": attempt, "limit": 2})
        result = api.dispatch("phone_tap", bad)
        self.assertEqual(result["retry"],
                         {"allowed": False, "reason": "retry_limit_reached", "attempt": 3, "limit": 2})
        self.assertTrue(api.dispatch("phone_home")["ok"])
        self.assertEqual(api.dispatch("phone_tap", bad)["retry"]["attempt"], 1)

    def test_critical_submission_never_suggests_retry(self):
        api = self.api()
        result = api.dispatch("phone_execute_operation", {"approval_id": "missing"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["retry"],
                         {"allowed": False, "reason": "submission_requires_manual_readback"})

    def test_successful_state_change_is_read_back(self):
        api = self.api()
        result = api.dispatch("phone_open_app", {"app": "messaging"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["verification"]["outcome"], "clear")
        self.assertFalse(result["verification"]["pause"])
        self.assertFalse(result["verification"]["automatic_retry"])
        self.assertEqual(result["verification"]["observation_error"], None)

    def test_unclear_readback_pauses_without_replay(self):
        api = self.api(driver=UnclearDriver())
        result = api.dispatch("phone_open_app", {"app": "messaging"})
        self.assertEqual(result["verification"]["outcome"], "unclear")
        self.assertTrue(result["verification"]["pause"])
        self.assertFalse(result["verification"]["automatic_retry"])
        self.assertEqual(result["verification"]["observation_error"],
                         "fresh_observation_unavailable")

    def test_readback_exception_fails_closed(self):
        api = self.api(driver=ExplodingDriver())
        result = api.dispatch("phone_open_app", {"app": "messaging"})
        self.assertEqual(result["verification"]["outcome"], "unclear")
        self.assertTrue(result["verification"]["pause"])

    def test_automatic_readback_does_not_consume_read_retry_budget(self):
        api = self.api(max_read_retries=1, action_budget=100)
        self.assertTrue(api.dispatch("phone_open_app", {"app": "messaging"})["ok"])
        self.assertTrue(api.dispatch("phone_back")["ok"])
        self.assertTrue(api.dispatch("phone_status")["ok"])
        self.assertEqual(api.dispatch("phone_status")["error"], "read_retry_limit_exceeded")

    def test_status_discloses_task_limits(self):
        api = self.api(clock=FakeClock(), action_budget=7, time_limit=42,
                       max_read_retries=3, max_retries=1)
        task = api.dispatch("phone_status")["task"]
        self.assertEqual(task["action_budget"], 7)
        self.assertEqual(task["time_limit"], 42)
        self.assertIsNone(task["remaining_seconds"])  # task starts on first state change
        self.assertEqual(task["max_retries"], 1)
        api.dispatch("phone_home")
        self.assertEqual(api.dispatch("phone_status")["task"]["remaining_seconds"], 42)

    def test_approval_channel_still_works_with_api(self):
        bridge = PhoneBridge()
        api = CoreAPI(bridge)
        api.dispatch("phone_open_app", {"app": "messaging"})
        api.dispatch("contacts_open", {"contact": "Casey"})
        api.dispatch("phone_tap", {"x": .5, "y": .9})
        api.dispatch("phone_type_text", {"text": "hi"})
        op_id = api.dispatch("message_prepare", {"recipient_id": "contact-02", "message": "hi"})["approval_id"]
        self.assertFalse(bridge.execute(op_id)["ok"])
        bridge.approve(op_id)
        self.assertTrue(bridge.execute(op_id)["ok"])
        self.assertFalse(bridge.execute(op_id)["ok"])


class PauseStateTests(unittest.TestCase):
    """Review P1-3: an unclear outcome must actually stop further writes."""

    def test_unclear_readback_blocks_follow_up_writes(self):
        api = CoreAPI(PhoneBridge(UnclearDriver()))
        first = api.dispatch("phone_open_app", {"app": "messaging"})
        self.assertTrue(first["verification"]["pause"])
        self.assertEqual(api.dispatch("phone_home")["error"], "paused_pending_readback")
        self.assertEqual(api.dispatch("message_prepare",
                                      {"recipient_id": "c", "message": "x"})["error"],
                         "paused_pending_readback")
        self.assertTrue(api.dispatch("phone_status")["task"]["paused"])
        self.assertNotEqual(api.dispatch("phone_inspect").get("error"),
                            "paused_pending_readback")

    def test_reset_task_does_not_clear_pause(self):
        api = CoreAPI(PhoneBridge(UnclearDriver()))
        api.dispatch("phone_open_app", {"app": "messaging"})
        api.reset_task()
        self.assertEqual(api.dispatch("phone_home")["error"], "paused_pending_readback")

    def test_resume_requires_a_fresh_readback(self):
        api = CoreAPI(PhoneBridge(UnclearDriver()))
        api.dispatch("phone_open_app", {"app": "messaging"})
        self.assertEqual(api.resume_after_readback()["error"], "readback_not_confirmed")
        self.assertEqual(api.dispatch("phone_home")["error"], "paused_pending_readback")

    def test_resume_after_successful_readback_allows_writes(self):
        bridge = PhoneBridge(UnclearDriver())
        api = CoreAPI(bridge)
        api.dispatch("phone_open_app", {"app": "messaging"})
        bridge.driver = MockIPhone()  # observation recovers on the trusted side
        self.assertTrue(api.resume_after_readback()["resumed"])
        self.assertTrue(api.dispatch("phone_home")["ok"])
        self.assertFalse(api.dispatch("phone_status")["task"]["paused"])

    def test_weak_evidence_is_not_clear(self):
        class WeakDriver(MockIPhone):
            def observe(self):
                return {"ok": True, "screen": "home"}  # no revision, no content evidence

        api = CoreAPI(PhoneBridge(WeakDriver()))
        self.assertEqual(api.dispatch("phone_home")["verification"]["outcome"], "unclear")
        self.assertEqual(api.dispatch("phone_back")["error"], "paused_pending_readback")

    def test_security_rejection_does_not_suggest_retry(self):
        class SubmitRejectingDriver(MockIPhone):
            def tap(self, x, y):
                return {"ok": False, "error": "submission_requires_approval"}

        api = CoreAPI(PhoneBridge(SubmitRejectingDriver()))
        result = api.dispatch("phone_tap", {"x": .5, "y": .5})
        self.assertEqual(result["retry"],
                         {"allowed": False, "reason": "non_retryable_security_error"})


if __name__ == "__main__":
    unittest.main()
