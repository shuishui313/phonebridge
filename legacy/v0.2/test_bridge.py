import unittest
from bridge import PhoneBridge, MockDriver

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.b = PhoneBridge(MockDriver())
    def test_device_disclosure(self):
        self.assertFalse(self.b.status()['real_device_connected'])
    def test_open_and_observe(self):
        self.assertTrue(self.b.open_app('WeChat')['ok'])
        self.assertEqual(self.b.observe()['app'], 'WeChat')
    def test_unapproved_app(self):
        self.assertFalse(self.b.open_app('Bank')['ok'])
    def test_tap_rejects_out_of_range(self):
        self.assertFalse(self.b.tap(2, 0.5)['ok'])
    def test_message_never_sent(self):
        draft = self.b.draft_message('Test Contact', 'Hello')
        self.assertEqual(draft['state'], 'awaiting_external_confirmation')
        result = self.b.confirm_message(draft['approval_id'], True)
        self.assertEqual(result['state'], 'blocked')
    def test_replay_rejected(self):
        draft = self.b.draft_message('Test Contact', 'Hi')
        self.b.confirm_message(draft['approval_id'])
        self.assertFalse(self.b.confirm_message(draft['approval_id'])['ok'])

if __name__ == '__main__':
    unittest.main()
