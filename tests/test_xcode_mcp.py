import tempfile
import unittest
from pathlib import Path

from phonebridge.xcode_mcp import (
    ALLOWED_TOOLS, FIXTURE_BUNDLE, MCPBridgeClient, XcodeMCPDriver,
)

UDID = "9117E5FD-038E-4A8D-B262-C427F6F5B489"


class FakeClient:
    def __init__(self, hierarchy: str, screenshot: str, *, session_ok=True, synth_ok=True):
        self.hierarchy = hierarchy
        self.screenshot = screenshot
        self.session_ok = session_ok
        self.synth_ok = synth_ok
        self.calls = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "DeviceInteractionStartSession":
            if not self.session_ok:
                return {"ok": False, "error": "simulator_not_found"}
            return {"ok": True, "interactionSessionKey": "sess-1", "deviceIsSimulator": True}
        if name == "DeviceInteractionSynthesize":
            if not self.synth_ok:
                return {"ok": False, "error": "xcode_tool_error"}
            return {"ok": True, "applicationState": "Activated",
                    "screenshotPath": self.screenshot, "hierarchyPath": self.hierarchy}
        if name == "DeviceInteractionEndSession":
            return {"ok": True, "userMessage": "Session stopped"}
        return {"ok": False, "error": "unexpected_tool"}

    def close(self):
        self.closed = True


class XcodeMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pb-xcode-mcp-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.hierarchy = str(root / "hierarchy.txt")
        self.screenshot = str(root / "screen.png")
        Path(self.hierarchy).write_text(
            "Application bundle identifier: com.apple.Preferences\n"
            " Window, {{0.0, 0.0}, {402.0, 874.0}}, hitPoint: {201.0, 437.0}\n"
            "  Button, {{16.0, 62.0}, {44.0, 44.0}}, hitPoint: {38.0, 84.0}\n",
            encoding="utf-8",
        )
        Path(self.screenshot).write_bytes(b"synthetic screenshot bytes")
        self.client = FakeClient(self.hierarchy, self.screenshot)

    def driver(self, **kw):
        return XcodeMCPDriver(UDID, client=self.client, **kw)

    def tool_calls(self, name):
        return [args for tool, args in self.client.calls if tool == name]

    def test_status_discloses_boundaries(self):
        result = self.driver().status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["driver"], "xcode-mcp")
        self.assertFalse(result["simulated"])
        self.assertFalse(result["real_device_connected"])
        self.assertFalse(result["interaction_approved"])
        self.assertEqual(self.client.calls, [])

    def test_udid_and_bundle_validation(self):
        with self.assertRaises(ValueError):
            XcodeMCPDriver("not-a-udid", client=self.client)
        with self.assertRaises(ValueError):
            XcodeMCPDriver(UDID, client=self.client, fixture_bundle="../escape")

    def test_observe_captures_local_state(self):
        result = self.driver().observe()
        self.assertTrue(result["ok"])
        self.assertEqual(result["hierarchy_path"], self.hierarchy)
        self.assertEqual(result["screenshot_path"], self.screenshot)
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(self.tool_calls("DeviceInteractionSynthesize")[0]["interactionCommand"], "")
        self.assertEqual(self.tool_calls("DeviceInteractionStartSession")[0]["deviceIdentifier"], UDID)

    def test_open_app_allowlist_and_activation(self):
        d = self.driver()
        self.assertEqual(d.open_app("bank")["error"], "app_not_allowlisted")
        self.assertEqual(self.client.calls, [])
        result = d.open_app("fixture")
        self.assertTrue(result["ok"])
        self.assertEqual(self.tool_calls("DeviceInteractionSynthesize")[0]["activationBundleId"], FIXTURE_BUNDLE)
        self.assertEqual(result["revision"], 1)

    def test_mutating_interactions_require_human_enablement(self):
        d = self.driver()
        for result in (d.tap(.5, .5), d.swipe(.5, .8, .5, .2),
                       d.type_text("hello"), d.home()):
            self.assertEqual(result["error"], "independent_approval_required")
        self.assertEqual(self.client.calls, [])

    def test_tap_uses_official_syntax_and_hierarchy_dimensions(self):
        d = self.driver(interaction_approved=True)
        result = d.tap(38 / 402, 84 / 874)
        self.assertTrue(result["ok"])
        commands = [c["interactionCommand"] for c in self.tool_calls("DeviceInteractionSynthesize")]
        self.assertIn("t 38 84", commands)
        self.assertEqual(result["interaction_command"], "t 38 84")

    def test_swipe_and_type_use_official_syntax(self):
        d = self.driver(interaction_approved=True)
        self.assertTrue(d.swipe(.5, .8, .5, .2)["ok"])
        self.assertTrue(d.type_text("Hello PhoneBridge")["ok"])
        commands = [c["interactionCommand"] for c in self.tool_calls("DeviceInteractionSynthesize")]
        self.assertIn("t 201 699 f 201 175 0.3", commands)
        self.assertIn("sender keyboard kbd Hello PhoneBridge", commands)

    def test_type_text_rejects_control_characters(self):
        d = self.driver(interaction_approved=True)
        self.assertEqual(d.type_text("line\nbreak")["error"], "unsupported_text_characters")
        self.assertEqual(d.type_text("back\\slash")["error"], "unsupported_text_characters")

    def test_hardware_home_uses_button_command(self):
        d = self.driver(interaction_approved=True)
        self.assertTrue(d.home()["ok"])
        self.assertIn("b h", [c["interactionCommand"] for c in self.tool_calls("DeviceInteractionSynthesize")])

    def test_unsupported_operations_never_reach_the_bridge(self):
        d = self.driver(interaction_approved=True)
        for result in (d.back(), d.list_contacts(), d.open_chat("Casey"),
                       d.virtual_send("contact-02", "hi")):
            self.assertFalse(result["ok"])
        self.assertEqual(self.client.calls, [])

    def test_screen_dimensions_missing_fails_closed(self):
        Path(self.hierarchy).write_text("Application bundle identifier: com.apple.Preferences\n", encoding="utf-8")
        d = self.driver(interaction_approved=True)
        self.assertEqual(d.tap(.5, .5)["error"], "screen_dimensions_unavailable")

    def test_session_start_failure_propagates(self):
        client = FakeClient(self.hierarchy, self.screenshot, session_ok=False)
        result = XcodeMCPDriver(UDID, client=client).observe()
        self.assertEqual(result["error"], "simulator_not_found")
        self.assertEqual(client.calls[0][0], "DeviceInteractionStartSession")

    def test_close_ends_session_once(self):
        d = self.driver()
        d.observe()
        d.close()
        d.close()
        self.assertEqual(len(self.tool_calls("DeviceInteractionEndSession")), 1)
        self.assertTrue(self.client.closed)
        self.assertEqual(d.observe()["error"], "session_closed")

    def test_official_client_rejects_tools_outside_allowlist(self):
        client = MCPBridgeClient(popen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not spawn")))
        for name in ("XcodeWrite", "BuildProject", "DeviceInteractionSynthesizeX"):
            self.assertEqual(client.call(name, {})["error"], "tool_not_allowlisted")
        self.assertEqual(len(ALLOWED_TOOLS), 6)


if __name__ == "__main__":
    unittest.main()
