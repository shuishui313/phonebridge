import io
import json
import unittest

from phonebridge.core import PhoneBridge
from phonebridge.mcp import MAX_LINE
from phonebridge.mcp_v04 import MCPServer, PARAMS


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.s = MCPServer(PhoneBridge())

    def request(self, method, params=None, request_id=1):
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        return self.s.process(request)

    def initialize(self, protocol="2025-06-18", *, notify=True):
        result = self.request("initialize", {
            "protocolVersion": protocol,
            "capabilities": {},
            "clientInfo": {"name": "tester", "version": "1"},
        })
        if notify:
            self.s.process({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call(self, name, **kw):
        r = self.request("tools/call", {"name": name, "arguments": kw})
        return r["result"]["structuredContent"]

    def test_initialize_protocol(self):
        r = self.initialize()
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")

    def test_unknown_version_falls_back(self):
        r = self.initialize("future")
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")

    def test_tools_list_schema(self):
        self.initialize()
        r = self.request("tools/list", {})
        self.assertEqual({t["name"] for t in r["result"]["tools"]}, set(PARAMS))
        schemas = {t["name"]: t["inputSchema"] for t in r["result"]["tools"]}
        self.assertEqual(schemas["phone_execute_operation"]["required"], ["approval_id"])
        self.assertEqual(schemas["intents_list"]["required"], [])
        self.assertEqual(schemas["intent_run"]["required"], ["capability"])

    def test_notification_no_reply(self):
        self.assertIsNone(self.s.process({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method(self):
        self.assertEqual(self.request("foo")["error"]["code"], -32601)

    def test_ping(self):
        self.assertEqual(self.request("ping")["result"], {})

    def test_invalid_args(self):
        self.initialize()
        self.assertEqual(self.call("phone_tap", x="0.5", y=.5)["error"], "invalid_parameter_type")
        self.assertEqual(self.call("phone_tap", x=.5, y=.5, extra=1)["error"], "invalid_parameters")

    def test_unknown_tool(self):
        self.initialize()
        self.assertEqual(self.call("approval_approve", approval_id="notpossible")["error"], "unknown_tool")

    def test_tool_flow(self):
        self.initialize()
        self.assertTrue(self.call("phone_open_app", app="messaging")["ok"])
        self.assertEqual(self.call("contacts_open", contact="Casey")["contact"]["id"], "contact-02")
        self.assertTrue(self.call("phone_tap", x=.5, y=.9)["ok"])
        self.assertTrue(self.call("phone_type_text", text="你好")["ok"])
        draft = self.call("message_prepare", recipient_id="contact-02", message="你好")
        self.assertTrue(draft["ok"])
        self.assertEqual(self.call("phone_execute_operation", approval_id=draft["approval_id"])["error"], "independent_approval_required")

    def test_legacy_tool_aliases_are_callable_but_not_advertised(self):
        self.initialize()
        names = {t["name"] for t in self.request("tools/list", {})["result"]["tools"]}
        self.assertNotIn("wechat_prepare_message", names)
        self.assertTrue(self.call("phone_open_app", app="wechat")["ok"])
        self.assertEqual(len(self.call("wechat_list_contacts")["contacts"]), 4)
        self.assertEqual(self.call("wechat_open_chat", contact="小明")["contact"]["id"], "contact-02")

    def test_stream_responds_to_requests(self):
        self.initialize(notify=False)
        src = io.BytesIO(
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        )
        out = io.BytesIO()
        self.s.serve(src, out)
        responses = out.getvalue().splitlines()
        self.assertEqual(len(responses), 1)
        self.assertEqual(len(json.loads(responses[0])["result"]["tools"]), 14)

    def test_bad_json(self):
        out = io.BytesIO()
        self.s.serve(io.BytesIO(b"{bad}\n"), out)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], -32700)

    def test_lifecycle_requires_successful_initialization_and_notification(self):
        self.assertIsNone(self.s.process({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self.request("tools/list")["error"]["code"], -32002)
        self.assertEqual(self.request("initialize", {"protocolVersion": "2025-06-18"})["error"]["code"], -32602)
        self.assertIn("result", self.initialize(notify=False))
        self.assertEqual(self.request("tools/list")["error"]["code"], -32002)
        self.assertEqual(self.initialize()["error"]["code"], -32600)
        self.assertIn("result", self.request("tools/list"))

    def test_legacy_version_text_result(self):
        self.initialize("2025-03-26")
        result = self.request("tools/call", {"name": "phone_status"})["result"]
        self.assertNotIn("structuredContent", result)
        self.assertTrue(json.loads(result["content"][0]["text"])["simulated"])

    def test_invalid_request_ids_are_never_echoed(self):
        for bad_id in (None, True, 0.5, [], {}, "\ud800"):
            with self.subTest(request_id=bad_id):
                r = self.request("ping", request_id=bad_id)
                self.assertEqual(r["error"]["code"], -32600)
                self.assertIsNone(r["id"])
        self.assertEqual(self.request("ping", request_id="测试")["id"], "测试")

    def test_bad_frames_do_not_break_later_requests(self):
        source = io.BytesIO(
            b"[" * (MAX_LINE + 2) + b"\n"
            b"\xff\n"
            b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n'
            b"[]\n"
            b'{"jsonrpc":"2.0","id":"ok","method":"ping"}\n'
        )
        out = io.BytesIO()
        self.s.serve(source, out)
        responses = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([r["error"]["code"] for r in responses[:-1]], [-32700, -32700, -32700, -32600])
        self.assertEqual(responses[-1], {"jsonrpc": "2.0", "id": "ok", "result": {}})

    def test_tool_rejects_unpaired_unicode_surrogate(self):
        self.initialize()
        self.assertEqual(self.call("phone_type_text", text="\ud800")["error"], "invalid_parameter_value")


if __name__ == "__main__": unittest.main()
