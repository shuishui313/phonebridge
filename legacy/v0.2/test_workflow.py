import unittest
from simulator import WorkflowSimulator

class WorkflowTests(unittest.TestCase):
    def test_prepare_wechat_message_flow(self):
        sim = WorkflowSimulator()
        response = sim.prepare_wechat_message('测试联系人', '今晚八点到')
        self.assertTrue(response['ok'])
        self.assertFalse(response['message_sent'])
        self.assertFalse(response['real_device_connected'])
        self.assertEqual([x['step'] for x in response['steps']], [
            'device_status', 'open_wechat', 'observe_wechat', 'prepare_message'])
        self.assertEqual(sim.driver.actions, [['open_app', 'WeChat']])

    def test_rejects_blank_recipient(self):
        response = WorkflowSimulator().prepare_wechat_message(' ', '消息')
        self.assertFalse(response['ok'])
        self.assertFalse(response['message_sent'])

    def test_rejects_blank_message(self):
        response = WorkflowSimulator().prepare_wechat_message('张三', ' ')
        self.assertFalse(response['ok'])

    def test_even_approved_mock_cannot_send(self):
        response = WorkflowSimulator().ensure_no_send_without_real_driver('测试联系人', 'hello')
        self.assertTrue(response['ok'])
        self.assertEqual(response['send_result']['state'], 'blocked')
        self.assertFalse(response['message_sent'])

if __name__ == '__main__':
    unittest.main()
