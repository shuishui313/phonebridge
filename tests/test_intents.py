import json
import unittest

from phonebridge.api import ACTION_SPECS, CoreAPI
from phonebridge.core import PhoneBridge
from phonebridge.drivers import MockIPhone
from phonebridge.intents import ChannelRouter, SyntheticIntentExecutor
from phonebridge.mcp_v04 import MCPServer, PARAMS


class StubExecutor:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def execute(self, entry, bridge):
        self.calls.append(entry.name)
        return self.outcome


class IntentChannelTests(unittest.TestCase):
    def make_api(self, *, executor=None, **kw):
        router = ChannelRouter(executor=executor) if executor is not None else None
        return CoreAPI(PhoneBridge(MockIPhone()), router=router,
                       max_read_retries=100, **kw)

    def test_catalog_discloses_only_synthetic_read_only_capabilities(self):
        result = self.make_api().dispatch("intents_list")
        self.assertTrue(result["ok"])
        self.assertEqual(result["channel"], "A")
        policy = result["policy"]
        self.assertEqual(policy["strategy"], "A_first_B_fallback")
        self.assertFalse(policy["real_app_intents_verified"])
        self.assertFalse(policy["real_shortcuts_invoked"])
        entries = {item["name"]: item for item in result["capabilities"]}
        self.assertEqual(set(entries), {"device.status", "contacts.lookup",
                                        "chat.readback", "message.send"})
        for name in ("device.status", "contacts.lookup", "chat.readback"):
            self.assertTrue(entries[name]["side_effect_free"])
            self.assertEqual(entries[name]["channel"], "A")
        self.assertFalse(entries["message.send"]["side_effect_free"])
        self.assertTrue(entries["message.send"]["requires_approval"])
        self.assertEqual(entries["message.send"]["channel"], "blocked")

    def test_intent_actions_match_advertised_mcp_contract(self):
        self.assertEqual(set(PARAMS), set(ACTION_SPECS))
        self.assertIn("intents_list", PARAMS)
        self.assertEqual(PARAMS["intent_run"], ("capability",))
        self.assertNotIn("approval_approve", PARAMS)

    def test_published_read_only_intent_uses_channel_a(self):
        result = self.make_api().dispatch("intent_run", {"capability": "contacts.lookup"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["channel"], "A")
        self.assertEqual(result["verification"]["outcome"], "clear")
        self.assertFalse(result["routing"]["fallback_allowed"])
        self.assertEqual(len(result["result"]["contacts"]), 4)

    def test_chat_readback_verifies_fresh_observation(self):
        result = self.make_api().dispatch("intent_run", {"capability": "chat.readback"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["verification"]["source"], "intent_readback")
        self.assertIsInstance(result["result"]["messages"], list)

    def test_unpublished_capability_only_routes_to_b(self):
        result = self.make_api().dispatch("intent_run", {"capability": "unknown.capability"})
        self.assertEqual(result["error"], "intent_not_published")
        self.assertEqual(result["routing"]["channel"], "B")
        self.assertTrue(result["routing"]["fallback_allowed"])
        self.assertFalse(result["routing"]["automatic_retry"])

    def test_write_intent_is_blocked_without_execution(self):
        executor = StubExecutor({"ok": True, "verified": True, "result": {}})
        result = self.make_api(executor=executor).dispatch(
            "intent_run", {"capability": "message.send"})
        self.assertEqual(result["error"], "intent_requires_approval")
        self.assertEqual(result["routing"]["channel"], "blocked")
        self.assertFalse(result["retry"]["allowed"])
        self.assertEqual(executor.calls, [])

    def test_confirmed_no_side_effect_failure_allows_b_fallback(self):
        executor = StubExecutor({"ok": False, "error": "intent_execution_failed",
                                 "no_side_effect_confirmed": True})
        api = self.make_api(executor=executor)
        result = api.dispatch("intent_run", {"capability": "contacts.lookup"})
        self.assertEqual(result["error"], "intent_execution_failed")
        self.assertTrue(result["routing"]["fallback_allowed"])
        self.assertFalse(result.get("pause", False))
        self.assertTrue(api.dispatch("phone_open_app", {"app": "messaging"})["ok"])

    def test_unconfirmed_failure_pauses_all_writes(self):
        executor = StubExecutor({"ok": False, "error": "intent_execution_failed",
                                 "no_side_effect_confirmed": False})
        api = self.make_api(executor=executor)
        result = api.dispatch("intent_run", {"capability": "contacts.lookup"})
        self.assertFalse(result["routing"]["fallback_allowed"])
        self.assertTrue(result.get("pause"))
        self.assertEqual(api.dispatch("phone_open_app", {"app": "messaging"})["error"],
                         "paused_pending_readback")

    def test_unclear_readback_pauses_writes_and_resumes_after_fresh_readback(self):
        api = self.make_api(executor=SyntheticIntentExecutor(force_unclear=True))
        result = api.dispatch("intent_run", {"capability": "contacts.lookup"})
        self.assertEqual(result["error"], "intent_readback_unclear")
        self.assertEqual(result["routing"]["outcome"], "unclear")
        self.assertFalse(result["routing"]["fallback_allowed"])
        self.assertFalse(result["retry"]["allowed"])
        self.assertEqual(api.dispatch("phone_open_app", {"app": "messaging"})["error"],
                         "paused_pending_readback")
        self.assertTrue(api.resume_after_readback()["ok"])
        self.assertTrue(api.dispatch("phone_open_app", {"app": "messaging"})["ok"])

    def test_routing_policy_is_disclosed_in_status(self):
        status = self.make_api().dispatch("phone_status")
        self.assertEqual(status["routing"]["strategy"], "A_first_B_fallback")
        self.assertTrue(status["routing"]["unclear_result_pauses_writes"])

    def test_mcp_tools_call_runs_channel_a_over_json_rpc(self):
        server = MCPServer(self.make_api())
        server.process({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "intent-test", "version": "0"}}})
        server.process({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        def call(name, arguments=None):
            response = server.process({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                       "params": {"name": name, "arguments": arguments or {}}})
            return json.loads(response["result"]["content"][0]["text"])

        self.assertTrue(call("intents_list")["ok"])
        run = call("intent_run", {"capability": "device.status"})
        self.assertTrue(run["ok"])
        self.assertEqual(run["channel"], "A")


if __name__ == "__main__":
    unittest.main()
