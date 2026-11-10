"""Original, minimal phone-control core. Mock mode only; no iOS privileges implied."""
from __future__ import annotations
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

class PhoneBridge:
    def __init__(self, driver=None, log_path=None):
        self.driver = driver or MockDriver()
        self.pending = {}
        self.log_path = Path(log_path) if log_path else None

    def _log(self, action, result):
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'action': action, 'result': result}, ensure_ascii=False) + '\n')
        return result

    def status(self):
        return self._log('status', self.driver.status())

    def observe(self):
        return self._log('observe', self.driver.observe())

    def open_app(self, app):
        if app not in {'WeChat', 'Settings', 'Safari'}:
            return self._log('open_app', {'ok': False, 'error': 'app_not_allowlisted'})
        return self._log('open_app', self.driver.open_app(app))

    def tap(self, x, y):
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return {'ok': False, 'error': 'normalized_coordinates_required'}
        return self._log('tap', self.driver.tap(x, y))

    def swipe(self, start_x, start_y, end_x, end_y):
        if not all(0 <= c <= 1 for c in (start_x, start_y, end_x, end_y)):
            return {'ok': False, 'error': 'normalized_coordinates_required'}
        return self._log('swipe', self.driver.swipe(start_x, start_y, end_x, end_y))

    def type_text(self, value):
        if len(value) > 1000:
            return {'ok': False, 'error': 'text_too_long'}
        return self._log('type_text', self.driver.type_text(value))

    def draft_message(self, recipient, message):
        if not recipient.strip() or not message.strip() or len(message) > 1000:
            return {'ok': False, 'error': 'invalid_message'}
        token = secrets.token_urlsafe(16)
        self.pending[token] = {'recipient': recipient, 'message': message}
        return {'ok': True, 'approval_id': token, 'recipient': recipient, 'message': message, 'state': 'awaiting_external_confirmation'}

    def confirm_message(self, approval_id, approved=False):
        task = self.pending.pop(approval_id, None)
        if task is None:
            return {'ok': False, 'error': 'unknown_or_expired_approval'}
        if not approved:
            return self._log('send_message', {'ok': False, 'state': 'cancelled'})
        # Deliberately never send a real message from the MVP. A real iOS driver
        # and a trusted, out-of-band user-approval mechanism are required.
        return self._log('send_message', {'ok': False, 'state': 'blocked', 'reason': 'real_send_driver_not_integrated'})

class MockDriver:
    def __init__(self):
        self.app = 'Home'
        self.actions = []

    def status(self):
        return {'ok': True, 'driver': 'mock', 'device': 'simulated iPhone', 'real_device_connected': False}

    def observe(self):
        return {'ok': True, 'app': self.app, 'screen_description': f'Mock screen: {self.app}', 'screenshot': None}

    def open_app(self, app):
        self.app = app
        self.actions.append(['open_app', app])
        return {'ok': True, 'simulated': True, 'app': app}

    def tap(self, x, y):
        self.actions.append(['tap', x, y])
        return {'ok': True, 'simulated': True}

    def swipe(self, sx, sy, ex, ey):
        self.actions.append(['swipe', sx, sy, ex, ey])
        return {'ok': True, 'simulated': True}

    def type_text(self, value):
        self.actions.append(['type_text', value])
        return {'ok': True, 'simulated': True, 'characters': len(value)}
