import json
import tempfile
import unittest
from pathlib import Path

from phonebridge.core import PhoneBridge
from phonebridge.drivers import MockIPhone


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.clock_value = 1000
        self.driver = MockIPhone()
        self.b = PhoneBridge(self.driver, clock=lambda: self.clock_value)

    def chat(self, identity="contact-01", text="测试消息"):
        self.assertTrue(self.b.open_app("messaging")["ok"])
        self.assertTrue(self.b.open_chat(identity)["ok"])
        self.assertTrue(self.b.tap(.5, .9)["ok"])
        self.assertTrue(self.b.type_text(text)["ok"])

    def test_status_reveals_mock(self):
        self.assertFalse(self.b.status()["real_device_connected"])
        self.assertTrue(self.b.status()["simulated"])

    def test_only_allowlisted_apps(self):
        self.assertFalse(self.b.open_app("bank")["ok"])
        self.assertTrue(self.b.open_app("settings")["ok"])

    def test_empty_contacts_when_not_in_messaging(self):
        self.assertEqual(self.b.list_contacts()["error"], "not_on_messaging_contacts")

    def test_legacy_app_and_contact_aliases_still_work(self):
        self.assertTrue(self.b.open_app("wechat")["ok"])
        self.assertEqual(self.b.inspect()["app"], "messaging")
        self.assertEqual(self.b.open_chat("小明")["contact"]["id"], "contact-02")
        self.b.tap(.5, .9)
        self.b.type_text("legacy alias")
        self.assertEqual(self.b.prepare_message("xiaoming-01", "legacy alias")["recipient_id"], "contact-02")

    def test_contacts_and_exact_match(self):
        self.b.open_app("messaging")
        self.assertEqual(len(self.b.list_contacts()["contacts"]), 4)
        self.assertEqual(self.b.open_chat("Casey")["contact"]["id"], "contact-02")

    def test_ambiguous_recipient(self):
        self.b.open_app("messaging")
        result = self.b.open_chat("Alex")
        self.assertEqual(result["error"], "ambiguous_contact")
        self.assertEqual(len(result["candidates"]), 2)

    def test_recipient_not_found(self):
        self.b.open_app("messaging")
        self.assertEqual(self.b.open_chat("没有这个人")["error"], "contact_not_found")

    def test_tap_fixture_navigation(self):
        self.b.tap(.2, .3)
        self.b.tap(.5, .18)
        self.assertEqual(self.b.inspect()["contact"]["id"], "contact-01")

    def test_tap_rejects_invalid_coordinate(self):
        for x in (-.1, 2, float('nan'), float('inf'), True, 10**400):
            self.assertEqual(self.b.tap(x, .5)["error"], "normalized_coordinates_required")

    def test_swipe_rejects_invalid_coordinate(self):
        self.assertEqual(self.b.swipe(0, 0, 10, 1)["error"], "normalized_coordinates_required")

    def test_type_needs_focus(self):
        self.b.open_app("messaging")
        self.b.open_chat("Casey")
        self.assertEqual(self.b.type_text("a")["error"], "input_not_focused")

    def test_draft_requires_exact_text(self):
        self.chat()
        result = self.b.prepare_message("contact-01", "不是输入的消息")
        self.assertEqual(result["error"], "draft_mismatch")

    def test_draft_requires_correct_recipient(self):
        self.chat()
        result = self.b.prepare_message("alex-01", "测试消息")
        self.assertEqual(result["error"], "recipient_not_verified_in_current_chat")

    def test_no_self_approval(self):
        self.chat()
        draft = self.b.prepare_message("contact-01", "测试消息")
        self.assertEqual(self.b.execute(draft["approval_id"])["error"], "independent_approval_required")
        self.assertEqual(self.driver.messages["contact-01"], [])

    def test_approved_virtual_message_and_readback(self):
        self.chat()
        draft = self.b.prepare_message("contact-01", "测试消息")
        self.assertTrue(self.b.approve(draft["approval_id"])["ok"])
        result = self.b.execute(draft["approval_id"])
        self.assertTrue(result["virtual_message_added"])
        self.assertFalse(result["real_message_sent"])
        self.assertEqual(self.b.inspect()["messages"][-1]["text"], "测试消息")

    def test_replay_rejected(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.b.approve(op)
        self.b.execute(op)
        self.assertEqual(self.b.execute(op)["error"], "unknown_or_processed_operation")

    def test_denial_blocks_execution(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.assertTrue(self.b.deny(op)["ok"])
        self.assertEqual(self.b.execute(op)["error"], "unknown_or_processed_operation")

    def test_screen_change_revokes_draft(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.b.back()
        self.assertEqual(self.b.approve(op)["error"], "stale_or_expired_proposal")

    def test_any_input_change_revokes_draft(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.b.type_text("!")
        self.assertEqual(self.b.approve(op)["error"], "stale_or_expired_proposal")

    def test_expiration(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.clock_value += 301
        self.assertEqual(self.b.approve(op)["error"], "stale_or_expired_proposal")

    def test_approved_but_expired(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.b.approve(op)
        self.clock_value += 301
        self.assertEqual(self.b.execute(op)["error"], "stale_or_expired_proposal")

    def test_invalid_long_text(self):
        self.assertEqual(self.b.type_text("A"*1001)["error"], "invalid_text")

    def test_draft_rejects_invalid_unicode_and_bounds_appends(self):
        self.chat(text="a" * 1000)
        self.assertEqual(self.b.type_text("\ud800")["error"], "invalid_text")
        self.assertEqual(self.b.type_text("b")["error"], "draft_too_long")
        self.assertEqual(self.b.inspect()["draft_length"], 1000)
        self.assertTrue(self.b.prepare_message("contact-01", "a" * 1000)["ok"])

    def test_messages_are_per_contact(self):
        self.chat()
        op = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
        self.b.approve(op)
        self.b.execute(op)
        self.b.back()
        self.b.open_chat("Casey")
        self.assertEqual(self.b.inspect()["messages"], [])

    def test_audit_never_logs_body(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            b = PhoneBridge(MockIPhone(), audit_path=str(p))
            b.open_app("messaging")
            b.open_chat("Casey")
            b.tap(.5, .9)
            b.type_text("secret canary 012345")
            b.prepare_message("contact-02", "secret canary 012345")
            b.open_app("secret app canary")
            self.assertTrue(p.exists())
            self.assertNotIn("secret canary", p.read_text())
            self.assertNotIn("secret app canary", p.read_text())
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_audit_symlink_does_not_hide_successful_send(self):
        self.chat()
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "untouched"
            target.write_text("keep")
            self.b.audit_path = Path(td) / "audit.jsonl"
            self.b.audit_path.symlink_to(target)
            operation = self.b.prepare_message("contact-01", "测试消息")["approval_id"]
            self.b.approve(operation)
            result = self.b.execute(operation)
            self.assertTrue(result["virtual_message_added"])
            self.assertFalse(result["audit_recorded"])
            self.assertEqual(target.read_text(), "keep")
            self.assertEqual(len(self.b.inspect()["messages"]), 1)


if __name__ == "__main__": unittest.main()
