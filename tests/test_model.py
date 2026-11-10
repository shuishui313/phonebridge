import unittest
from concurrent.futures import ThreadPoolExecutor

from phonebridge.model import MAX_MESSAGE_LENGTH, BridgeError, PhoneBridge


class PhoneBridgeTests(unittest.TestCase):
    def setUp(self):
        self.phone = PhoneBridge()

    def open_chat(self, contact="xiaoming"):
        self.phone.open_app("wechat")
        self.phone.open_chat(contact)

    def assert_error(self, code, method, *args):
        with self.assertRaises(BridgeError) as context:
            method(*args)
        self.assertEqual(context.exception.code, code)
        return context.exception

    def test_home_app_and_back(self):
        self.assertEqual(self.phone.inspect()["screen"], "home")
        self.assertEqual(self.phone.open_app("settings")["app"], "settings")
        self.assertEqual(self.phone.back()["screen"], "home")
        self.assertEqual(self.phone.open_app("wechat")["screen"], "contacts")
        self.assertEqual(self.phone.open_chat("xiaoming")["screen"], "chat")
        self.assertEqual(self.phone.back()["screen"], "contacts")
        self.assertEqual(self.phone.home()["screen"], "home")
        self.assertEqual(self.phone.home()["screen"], "home")

    def test_allowlist_and_screen_requirements(self):
        self.assert_error("APP_NOT_ALLOWED", self.phone.open_app, "camera")
        self.assert_error("WRONG_SCREEN", self.phone.open_chat, "xiaoming")
        self.assert_error("WRONG_SCREEN", self.phone.list_contacts)
        self.assert_error("WRONG_SCREEN", self.phone.prepare_message, "hello")
        self.phone.open_app("wechat")
        self.assert_error("WRONG_SCREEN", self.phone.prepare_message, "hello")

    def test_exact_name_and_unicode_normalization(self):
        self.phone.open_app("wechat")
        self.assertEqual(self.phone.open_chat(" 小明 ")["contact"]["id"], "xiaoming")
        self.assertEqual(self.phone.open_chat(" ＳＡＲＡＨ Chen ")["contact"]["id"], "sarah")
        self.assert_error("CONTACT_NOT_FOUND", self.phone.open_chat, "Sarah")
        self.assert_error("INVALID_ARGUMENT", self.phone.open_chat, " ")

    def test_ambiguous_contact_requires_id(self):
        self.phone.open_app("wechat")
        self.assertEqual(len(self.phone.list_contacts()["contacts"]), 4)
        error = self.assert_error("AMBIGUOUS_CONTACT", self.phone.open_chat, "alex")
        self.assertEqual(
            [contact["id"] for contact in error.details["candidates"]], ["alex-li", "alex-wong"]
        )
        self.assertEqual(self.phone.inspect()["screen"], "contacts")
        self.assertEqual(self.phone.open_chat("alex-wong")["contact"]["id"], "alex-wong")

    def test_proposal_and_approval_do_not_send(self):
        self.open_chat()
        proposal = self.phone.prepare_message("  你好！\n")
        operation_id = proposal["operation_id"]
        self.assertEqual(proposal["contact"]["id"], "xiaoming")
        self.assertEqual(self.phone.list_pending_operations(), {"operations": [proposal]})
        self.assert_error("APPROVAL_REQUIRED", self.phone.execute_operation, operation_id)
        self.assertEqual(self.phone.inspect()["messages"], [])
        self.assertEqual(self.phone.decide_operation(operation_id, True)["status"], "approved")
        self.assertEqual(self.phone.list_pending_operations(), {"operations": []})
        self.assertEqual(self.phone.inspect()["messages"], [])

        self.assertEqual(self.phone.execute_operation(operation_id)["status"], "executed")
        self.assert_error("OPERATION_ALREADY_EXECUTED", self.phone.execute_operation, operation_id)
        self.assertEqual(self.phone.inspect()["messages"], [{"text": "  你好！\n", "direction": "outgoing"}])

    def test_deny_cannot_be_reversed_or_executed(self):
        self.open_chat()
        operation_id = self.phone.prepare_message("hello")["operation_id"]
        self.assertEqual(self.phone.decide_operation(operation_id, False)["status"], "denied")
        self.assert_error("OPERATION_DENIED", self.phone.decide_operation, operation_id, True)
        self.assert_error("OPERATION_DENIED", self.phone.execute_operation, operation_id)
        self.assertEqual(self.phone.inspect()["messages"], [])

    def test_navigation_round_trip_invalidates_pending_and_approved_proposals(self):
        self.open_chat()
        pending = self.phone.prepare_message("first")["operation_id"]
        approved = self.phone.prepare_message("second")["operation_id"]
        self.phone.decide_operation(approved, True)
        self.phone.back()
        self.phone.open_chat("xiaoming")
        self.assertEqual(self.phone.list_pending_operations(), {"operations": []})
        self.assert_error("OPERATION_STALE", self.phone.decide_operation, pending, True)
        self.assert_error("OPERATION_STALE", self.phone.execute_operation, approved)
        self.assertEqual(self.phone.inspect()["messages"], [])

    def test_message_history_is_per_contact(self):
        self.open_chat("alex-li")
        operation_id = self.phone.prepare_message("hi")["operation_id"]
        self.phone.decide_operation(operation_id, True)
        self.phone.execute_operation(operation_id)
        self.assertEqual(self.phone.open_chat("alex-wong")["messages"], [])
        self.assertEqual(self.phone.open_chat("alex-li")["messages"][0]["text"], "hi")

    def test_validation(self):
        self.open_chat()
        for text in ("", " \n", "x" * (MAX_MESSAGE_LENGTH + 1), "\ud800"):
            with self.subTest(text=repr(text)[:30]):
                self.assert_error("INVALID_ARGUMENT", self.phone.prepare_message, text)
        self.assert_error("OPERATION_NOT_FOUND", self.phone.execute_operation, "missing")
        operation_id = self.phone.prepare_message("x" * MAX_MESSAGE_LENGTH)["operation_id"]
        self.assert_error("INVALID_ARGUMENT", self.phone.decide_operation, operation_id, 1)

    def test_concurrent_execute_only_sends_once(self):
        self.open_chat()
        operation_id = self.phone.prepare_message("hi")["operation_id"]
        self.phone.decide_operation(operation_id, True)

        def attempt():
            try:
                return self.phone.execute_operation(operation_id)["status"]
            except BridgeError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(outcomes, ["executed", "OPERATION_ALREADY_EXECUTED"])
        self.assertEqual(len(self.phone.inspect()["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
