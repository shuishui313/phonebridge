import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phonebridge.core import PhoneBridge
from phonebridge.submissions import SubmissionLedger, digest_of
from phonebridge.xcode_mcp import (
    ALLOWED_TOOLS, FIXTURE_BUNDLE, MCPBridgeClient, XcodeMCPDriver,
)

UDID = "9117E5FD-038E-4A8D-B262-C427F6F5B489"
RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-27-2"


class FakeClient:
    def __init__(self, root: Path, *, foreground_bundle=FIXTURE_BUNDLE, session_ok=True,
                 device_is_simulator=True, device_uuid=UDID, synth_ok=True,
                 fresh_paths=True, bridge_error=None, extra_lines=""):
        self.root = root
        self.foreground_bundle = foreground_bundle
        self.session_ok = session_ok
        self.device_is_simulator = device_is_simulator
        self.device_uuid = device_uuid
        self.synth_ok = synth_ok
        self.fresh_paths = fresh_paths
        self.bridge_error = bridge_error
        self.extra_lines = extra_lines
        self.capture_index = 0
        self.calls = []
        self.started = False
        self.closed = False

    @property
    def timeout(self):
        return 15.0

    def _observation(self):
        self.capture_index += 1
        hierarchy = self.root / f"hierarchy-{self.capture_index}.txt"
        screenshot = self.root / f"screen-{self.capture_index}.png"
        hierarchy.write_text(
            f"Application bundle identifier: {self.foreground_bundle}\n"
            " Window, {{0.0, 0.0}, {402.0, 874.0}}, hitPoint: {201.0, 437.0}\n"
            "  Button, {{16.0, 62.0}, {44.0, 44.0}}, hitPoint: {38.0, 84.0}\n"
            + self.extra_lines,
            encoding="utf-8",
        )
        screenshot.write_bytes(f"synthetic screenshot {self.capture_index}".encode())
        return str(hierarchy), str(screenshot)

    def start(self):
        self.started = True

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.bridge_error:
            return {"ok": False, "error": self.bridge_error}
        if name == "DeviceInteractionStartSession":
            if not self.session_ok:
                return {"ok": False, "error": "simulator_not_found"}
            return {"ok": True, "interactionSessionKey": "sess-1",
                    "deviceIsSimulator": self.device_is_simulator,
                    "deviceUUID": self.device_uuid}
        if name == "DeviceInteractionSynthesize":
            if not self.synth_ok:
                return {"ok": False, "error": "xcode_tool_error"}
            if not self.fresh_paths:
                return {"ok": True, "applicationState": "Activated"}
            hierarchy, screenshot = self._observation()
            return {"ok": True, "applicationState": "Activated",
                    "screenshotPath": screenshot, "hierarchyPath": hierarchy}
        if name == "DeviceInteractionEndSession":
            return {"ok": True, "userMessage": "Session stopped"}
        return {"ok": False, "error": "unexpected_tool"}

    def close(self):
        self.closed = True


class XcodeMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pb-xcode-mcp-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeClient(self.root)
        self.checker_calls = []

    def checker(self, udid):
        self.checker_calls.append(udid)
        return {"ok": True, "runtime": RUNTIME, "device_name": "iPhone 18 Pro"}

    def driver(self, **kw):
        return XcodeMCPDriver(UDID, client=self.client, simulator_checker=self.checker, **kw)

    def tool_calls(self, name):
        return [args for tool, args in self.client.calls if tool == name]

    def test_status_discloses_boundaries(self):
        result = self.driver().status()
        self.assertTrue(result["ok"])
        self.assertEqual(result["driver"], "xcode-mcp")
        self.assertFalse(result["simulated"])
        self.assertFalse(result["real_device_connected"])
        self.assertFalse(result["interaction_approved"])
        self.assertFalse(result["preflight_passed"])
        self.assertEqual(result["fixture_bundle"], FIXTURE_BUNDLE)
        self.assertEqual(self.client.calls, [])

    def test_session_identifiers_are_unique_by_default(self):
        first = self.driver()
        second = self.driver()
        self.assertNotEqual(first.session_identifier, second.session_identifier)
        self.assertTrue(first.session_identifier.startswith("phonebridge-"))
        self.assertEqual(self.driver(session_identifier="pb-fixed").session_identifier, "pb-fixed")
        with self.assertRaises(ValueError):
            self.driver(session_identifier="bad id!")

    def test_invalid_configuration(self):
        with self.assertRaises(ValueError):
            XcodeMCPDriver("not-a-udid", client=self.client)
        with self.assertRaises(ValueError):
            XcodeMCPDriver(UDID, client=self.client, fixture_bundle="../escape")
        with self.assertRaises(ValueError):
            XcodeMCPDriver(UDID, client=self.client, timeout=0)

    def test_observe_uses_fresh_state_and_verifies_simulator(self):
        d = self.driver()
        result = d.observe()
        self.assertTrue(result["ok"])
        self.assertEqual(result["foreground_bundle"], FIXTURE_BUNDLE)
        self.assertTrue(result["fixture_in_foreground"])
        self.assertEqual(result["hierarchy_path"], str(self.root / "hierarchy-1.txt"))
        self.assertEqual(len(result["sha256"]), 64)
        self.assertEqual(self.checker_calls, [UDID])
        self.assertEqual(self.tool_calls("DeviceInteractionSynthesize")[0]["interactionCommand"], "")

    def test_non_simulator_target_is_rejected(self):
        def reject(udid):
            return {"ok": False, "error": "not_a_simulator", "udid": udid}
        d = XcodeMCPDriver(UDID, client=self.client, simulator_checker=reject,
                           interaction_approved=True)
        self.assertEqual(d.observe()["error"], "not_a_simulator")
        self.assertEqual(d.tap(.5, .5)["error"], "not_a_simulator")
        self.assertEqual(self.client.calls, [])

    def test_session_must_confirm_simulator_identity(self):
        client = FakeClient(self.root, device_is_simulator=False)
        self.assertEqual(XcodeMCPDriver(UDID, client=client,
                                         simulator_checker=self.checker).observe()["error"],
                         "device_type_unverified")
        client = FakeClient(self.root, device_uuid="00000000-0000-0000-0000-000000000000")
        self.assertEqual(XcodeMCPDriver(UDID, client=client,
                                         simulator_checker=self.checker).observe()["error"],
                         "device_identity_mismatch")

    def test_open_app_allowlist_and_foreground_check(self):
        d = self.driver()
        self.assertEqual(d.open_app("bank")["error"], "app_not_allowlisted")
        self.assertEqual(self.client.calls, [])
        result = d.open_app("fixture")
        self.assertTrue(result["ok"])
        self.assertEqual(result["foreground_bundle"], FIXTURE_BUNDLE)
        self.assertEqual(self.tool_calls("DeviceInteractionSynthesize")[0]["activationBundleId"], FIXTURE_BUNDLE)

    def test_foreign_foreground_app_blocks_activation_and_interaction(self):
        client = FakeClient(self.root, foreground_bundle="com.apple.Preferences")
        d = XcodeMCPDriver(UDID, client=client, simulator_checker=self.checker,
                            interaction_approved=True)
        self.assertEqual(d.open_app("fixture")["error"], "foreground_app_not_allowlisted")
        self.assertEqual(d.tap(.1, .1)["error"], "foreground_app_not_allowlisted")
        commands = [a["interactionCommand"] for a in self.tool_calls("DeviceInteractionSynthesize")]
        self.assertTrue(all(c == "" for c in commands))

    def test_mutating_interactions_require_human_enablement(self):
        d = self.driver()
        for result in (d.tap(.5, .5), d.swipe(.5, .8, .5, .2),
                       d.type_text("hello"), d.home()):
            self.assertEqual(result["error"], "independent_approval_required")
        self.assertEqual(self.client.calls, [])

    def test_tap_requires_fresh_observation_and_uses_official_syntax(self):
        d = self.driver(interaction_approved=True)
        result = d.tap(38 / 402, 84 / 874)
        self.assertTrue(result["ok"])
        commands = [c["interactionCommand"] for c in self.tool_calls("DeviceInteractionSynthesize")]
        self.assertEqual(commands, ["", "t 38 84"])
        self.assertEqual(result["foreground_bundle"], FIXTURE_BUNDLE)

    def test_swipe_type_and_home_use_official_syntax(self):
        d = self.driver(interaction_approved=True)
        self.assertTrue(d.swipe(.5, .8, .5, .2)["ok"])
        self.assertTrue(d.type_text("Hello PhoneBridge")["ok"])
        self.assertTrue(d.home()["ok"])
        commands = [c["interactionCommand"] for c in self.tool_calls("DeviceInteractionSynthesize")]
        self.assertIn("t 201 699 f 201 175 0.3", commands)
        self.assertIn("sender keyboard kbd Hello PhoneBridge", commands)
        self.assertIn("b h", commands)

    def test_type_text_rejects_control_characters(self):
        d = self.driver(interaction_approved=True)
        self.assertEqual(d.type_text("line\nbreak")["error"], "unsupported_text_characters")
        self.assertEqual(d.type_text("back\\slash")["error"], "unsupported_text_characters")

    def test_missing_current_observation_fails_without_reusing_cache(self):
        d = self.driver(interaction_approved=True)
        self.assertTrue(d.observe()["ok"])
        self.client.fresh_paths = False
        result = d.tap(.5, .5)
        self.assertEqual(result["error"], "fresh_observation_unavailable")
        self.assertEqual(d._last_hierarchy, str(self.root / "hierarchy-1.txt"))
        commands = [c["interactionCommand"] for c in self.tool_calls("DeviceInteractionSynthesize")]
        self.assertNotIn("t 201 437", commands)

    def test_bridge_failure_invalidates_session(self):
        d = self.driver(interaction_approved=True)
        self.assertTrue(d.observe()["ok"])
        self.client.bridge_error = "xcode_mcp_unavailable"
        self.assertEqual(d.observe()["error"], "xcode_mcp_unavailable")
        self.assertFalse(d._preflight_ok)
        self.client.bridge_error = None
        self.assertEqual(d.status()["preflight_passed"], False)
        self.assertTrue(d.observe()["ok"])
        self.assertEqual(len(self.tool_calls("DeviceInteractionStartSession")), 2)

    def test_unsupported_operations_never_reach_the_bridge(self):
        d = self.driver(interaction_approved=True)
        for result in (d.back(), d.list_contacts(), d.open_chat("Casey"),
                       d.virtual_send("contact-02", "hi")):
            self.assertFalse(result["ok"])
        self.assertEqual(self.client.calls, [])

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

    def test_official_client_times_out_and_releases_process(self):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        proc = mock.Mock()
        proc.stdin = io.BytesIO()
        proc.stdout.fileno.return_value = read_fd
        proc.wait.return_value = 0
        client = MCPBridgeClient(popen=lambda *a, **k: proc, timeout=0.05)
        with mock.patch("phonebridge.xcode_mcp.select.select", return_value=([], [], [])):
            result = client.call("DeviceInteractionStartSession", {"deviceIdentifier": UDID})
        self.assertEqual(result["error"], "xcode_mcp_unavailable")
        self.assertIn("timed out", result["details"])
        self.assertIsNone(client._proc)
        proc.terminate.assert_called_once()


class SubmitGuardTests(unittest.TestCase):
    """Review P1-1: generic interactions must not submit before approval."""

    SEND_LINE = ("  Button, {{270.0, 62.0}, {60.0, 44.0}}, "
                 "hitPoint: {300.0, 84.0}, identifier: 'chat.send'\n")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pb-submit-guard-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def checker(self, udid):
        return {"ok": True, "runtime": RUNTIME, "device_name": "iPhone 18 Pro"}

    def driver(self, extra_lines):
        client = FakeClient(self.root, extra_lines=extra_lines)
        instance = XcodeMCPDriver(UDID, client=client, simulator_checker=self.checker,
                                  interaction_approved=True)
        self.addCleanup(instance.close)
        return instance, client

    def syntheses(self, client):
        return [args for name, args in client.calls if name == "DeviceInteractionSynthesize"]

    def test_tap_on_enabled_send_is_rejected_before_interaction(self):
        instance, client = self.driver(self.SEND_LINE)
        result = instance.tap(300 / 402, 84 / 874)
        self.assertEqual(result["error"], "submission_requires_approval")
        self.assertEqual(result["interaction"], "tap")
        # Only the pre-interaction observation happened; no interaction command.
        calls = self.syntheses(client)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["interactionCommand"], "")

    def test_tap_away_from_send_is_allowed(self):
        instance, client = self.driver(self.SEND_LINE)
        self.assertTrue(instance.tap(38 / 402, 84 / 874)["ok"])
        self.assertEqual(len(self.syntheses(client)), 2)

    def test_disabled_submit_control_is_not_blocked(self):
        line = self.SEND_LINE.replace("'chat.send'", "'chat.send.disabled'")
        instance, _ = self.driver(line)
        self.assertTrue(instance.tap(300 / 402, 84 / 874)["ok"])

    def test_post_control_is_also_guarded(self):
        line = self.SEND_LINE.replace("'chat.send'", "'compose.post'")
        instance, _ = self.driver(line)
        self.assertEqual(instance.tap(300 / 402, 84 / 874)["error"],
                         "submission_requires_approval")

    def test_unlocatable_enabled_submit_fails_closed(self):
        line = "  Button, {{270.0, 62.0}, {60.0, 44.0}}, identifier: 'chat.send'\n"
        instance, client = self.driver(line)
        self.assertEqual(instance.tap(38 / 402, 84 / 874)["error"],
                         "submit_guard_unavailable")
        self.assertEqual(len(self.syntheses(client)), 1)

    def test_swipe_into_send_is_rejected(self):
        instance, client = self.driver(self.SEND_LINE)
        result = instance.swipe(38 / 402, 84 / 874, 300 / 402, 84 / 874)
        self.assertEqual(result["error"], "submission_requires_approval")
        self.assertEqual(result["interaction"], "swipe_end")
        self.assertEqual(len(self.syntheses(client)), 1)

    def test_record_count_uses_current_hierarchy(self):
        line = self.SEND_LINE + (
            "  StaticText, {{0.0, 0.0}, {10.0, 10.0}}, identifier: 'chat.message.0'\n")
        instance, _ = self.driver(line)
        self.assertTrue(instance.observe()["ok"])
        self.assertEqual(instance.record_count(), 1)


class FakeChatClient(FakeClient):
    """Synthetic chat hierarchy mirroring the owned fixture's real layout."""

    SEND_POINT = (370.3, 811.2)

    def __init__(self, root: Path, *, draft="G2 synthetic record", contact="Casey",
                 existing=(), submit_enabled=True, wrong_record=False):
        super().__init__(root)
        self.draft = draft
        self.contact = contact
        self.records = list(existing)
        self.submit_enabled = submit_enabled
        self.wrong_record = wrong_record
        self.submits = 0

    def _observation(self):
        self.capture_index += 1
        hierarchy = self.root / f"hierarchy-{self.capture_index}.txt"
        screenshot = self.root / f"screen-{self.capture_index}.png"
        value = f", value: {self.draft}" if self.draft else ""
        identifier = "chat.send" if self.submit_enabled else "chat.send.disabled"
        lines = [
            f"Application bundle identifier: {FIXTURE_BUNDLE}\n",
            " Window, {{0.0, 0.0}, {402.0, 874.0}}, hitPoint: {201.0, 437.0}\n",
            f" NavigationBar, {{{{0.0, 62.0}}, {{402.0, 106.0}}}}, "
            f"identifier: '{self.contact}', hitPoint: {{201.0, 115.0}}\n",
            "  TextField, {{12.0, 794.0}, {330.7, 34.0}}, identifier: 'chat.input', "
            f"placeholderValue: 'Message'{value}, Keyboard Focused, hitPoint: {{177.3, 811.0}}\n",
        ]
        for index, body in enumerate(self.records):
            lines.append("  StaticText, {{16.0, 168.0}, {370.0, 52.3}}, "
                         f"identifier: 'chat.message.{index}', label: '{body}', "
                         "hitPoint: {201.0, 194.2}\n")
        lines.append("  Button, {{350.7, 801.0}, {39.3, 20.3}}, "
                     f"identifier: '{identifier}', label: 'Send', hitPoint: {{370.3, 811.2}}\n")
        hierarchy.write_text("".join(lines), encoding="utf-8")
        screenshot.write_bytes(f"synthetic {self.capture_index}".encode())
        return str(hierarchy), str(screenshot)

    def call(self, name, arguments):
        if name == "DeviceInteractionSynthesize":
            command = arguments.get("interactionCommand", "")
            if self.submit_enabled and command == (
                    f"t {round(self.SEND_POINT[0])} {round(self.SEND_POINT[1])}"):
                self.records.append("OTHER TEXT" if self.wrong_record else self.draft)
                self.draft = ""
                self.submits += 1
        return super().call(name, arguments)


class VirtualSubmitTests(unittest.TestCase):
    """The approved gate may create one synthetic record; nothing else may."""

    DRAFT = "G2 synthetic record"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pb-virtual-submit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)

    def checker(self, udid):
        return {"ok": True, "runtime": RUNTIME, "device_name": "iPhone 18 Pro"}

    def build(self, *, client=None, virtual_submit_enabled=True):
        client = client or FakeChatClient(self.root)
        driver = XcodeMCPDriver(
            UDID, client=client, simulator_checker=self.checker,
            interaction_approved=True,
            virtual_submit_enabled=virtual_submit_enabled)
        self.addCleanup(driver.close)
        ledger = SubmissionLedger(str(self.root / "submissions.jsonl"))
        return client, driver, PhoneBridge(driver, ledger=ledger), ledger

    def test_switch_is_off_by_default(self):
        client, driver, bridge, _ = self.build(virtual_submit_enabled=False)
        self.assertFalse(driver.status()["virtual_submit_enabled"])
        self.assertTrue(bridge.inspect()["ok"])
        self.assertEqual(bridge.prepare_message("contact-02", self.DRAFT)["error"],
                         "real_message_sending_disabled")
        self.assertEqual(driver.virtual_send("contact-02", self.DRAFT)["error"],
                         "real_message_sending_disabled")
        self.assertEqual(client.submits, 0)

    def test_context_is_parsed_from_fresh_hierarchy(self):
        client, driver, bridge, _ = self.build()
        observed = bridge.inspect()
        self.assertEqual(observed["screen"], "chat")
        self.assertEqual(observed["selected_id"], "contact-02")
        self.assertEqual(observed["draft_length"], len(self.DRAFT))
        self.assertTrue(observed["virtual_submit_enabled"])
        self.assertEqual(driver.contacts[1]["id"], "contact-02")
        self.assertEqual(driver.resolve_contact_id("Casey"), "contact-02")
        self.assertIsNone(driver.resolve_contact_id("Alex"))  # two matching contacts

    def test_generic_tap_still_cannot_submit(self):
        client, driver, bridge, _ = self.build()
        result = bridge.tap(370.3 / 402, 811.2 / 874)
        self.assertEqual(result["error"], "submission_requires_approval")
        self.assertEqual(client.submits, 0)
        self.assertEqual(driver.record_count(), 0)

    def test_approved_submission_creates_one_attributed_record(self):
        client, driver, bridge, ledger = self.build()
        self.assertTrue(bridge.inspect()["ok"])
        op = bridge.prepare_message("contact-02", self.DRAFT)["approval_id"]
        self.assertTrue(bridge.approve(op)["ok"])
        result = bridge.execute(op)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["submission_state"], "completed")
        self.assertEqual(result["verification"]["source"], "attributed_new_record")
        self.assertFalse(result["real_message_sent"])
        self.assertTrue(result["virtual_fixture_send"])
        self.assertEqual(client.submits, 1)
        self.assertEqual(driver.record_count(), 1)
        events = [json.loads(line)["event"] for line in
                  (self.root / "submissions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, ["prepared", "approved", "dispatching", "completed"])
        self.assertEqual(bridge.execute(op)["error"], "unknown_or_processed_operation")
        self.assertEqual(client.submits, 1)

    def test_target_and_body_mismatch_are_rejected(self):
        client, driver, bridge, _ = self.build()
        self.assertTrue(bridge.inspect()["ok"])
        self.assertEqual(driver.virtual_send("contact-01", self.DRAFT)["error"],
                         "submission_context_mismatch")
        self.assertEqual(driver.virtual_send("contact-02", "different body")["error"],
                         "submission_context_mismatch")
        self.assertEqual(client.submits, 0)

    def test_disabled_fixture_send_control_blocks_virtual_submit(self):
        client = FakeChatClient(self.root, submit_enabled=False)
        _, driver, bridge, _ = self.build(client=client)
        self.assertTrue(bridge.inspect()["ok"])
        self.assertEqual(driver.virtual_send("contact-02", self.DRAFT)["error"],
                         "submit_control_unavailable")
        self.assertEqual(client.submits, 0)

    def test_unconfirmed_record_pauses_without_resend(self):
        client = FakeChatClient(self.root, wrong_record=True)
        _, _, bridge, ledger = self.build(client=client)
        self.assertTrue(bridge.inspect()["ok"])
        op = bridge.prepare_message("contact-02", self.DRAFT)["approval_id"]
        self.assertTrue(bridge.approve(op)["ok"])
        result = bridge.execute(op)
        self.assertEqual(result["submission_state"], "unresolved")
        self.assertTrue(result["pause"])
        self.assertEqual(client.submits, 1)
        self.assertEqual(ledger.state(op).state, "unresolved")

    def test_ambiguous_contact_cannot_be_bound(self):
        client = FakeChatClient(self.root, contact="Alex")
        _, _, bridge, _ = self.build(client=client)
        self.assertTrue(bridge.inspect()["ok"])
        self.assertEqual(bridge.prepare_message("contact-02", self.DRAFT)["error"],
                         "recipient_not_verified_in_current_chat")

    def test_recovery_without_fresh_observation_pauses(self):
        _, _, _, ledger = self.build()
        ledger.append("dispatching", op_id="op-restart", recipient_id="contact-02",
                      message_sha256=digest_of(self.DRAFT),
                      message_chars=len(self.DRAFT), baseline_count=0)
        fresh = FakeChatClient(self.root, existing=[self.DRAFT])
        _, _, restarted, _ = self.build(client=fresh)
        report = restarted.recover_submissions()
        self.assertEqual(report["pending_pause"], 1)
        self.assertFalse(report["recovered"][0]["confirmed"])
        self.assertFalse(report["recovered"][0]["resent"])
        self.assertEqual(fresh.submits, 0)


if __name__ == "__main__":
    unittest.main()
