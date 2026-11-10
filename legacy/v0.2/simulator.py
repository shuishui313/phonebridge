"""Deterministic, entirely synthetic iPhone workflow test (no Apple simulator)."""
from __future__ import annotations
from bridge import MockDriver, PhoneBridge

class WorkflowSimulator:
    """Tests task orchestration and safety boundaries, not iOS device control."""
    def __init__(self):
        self.driver = MockDriver()
        self.bridge = PhoneBridge(self.driver)

    def prepare_wechat_message(self, recipient: str, message: str) -> dict:
        steps = []
        for label, result in [
            ('device_status', self.bridge.status()),
            ('open_wechat', self.bridge.open_app('WeChat')),
            ('observe_wechat', self.bridge.observe()),
        ]:
            steps.append({'step': label, 'result': result})
            if not result.get('ok'):
                return {'ok': False, 'steps': steps, 'simulated': True}
        draft = self.bridge.draft_message(recipient, message)
        steps.append({'step': 'prepare_message', 'result': draft})
        return {
            'ok': draft.get('ok', False),
            'simulated': True,
            'real_device_connected': False,
            'message_sent': False,
            'human_confirmation_required': True,
            'steps': steps,
        }

    def ensure_no_send_without_real_driver(self, recipient: str, message: str) -> dict:
        run = self.prepare_wechat_message(recipient, message)
        if not run['ok']:
            return run
        approval = run['steps'][-1]['result']['approval_id']
        result = self.bridge.confirm_message(approval, approved=True)
        return {'ok': result['state'] == 'blocked', 'simulated': True,
                'message_sent': False, 'send_result': result}
