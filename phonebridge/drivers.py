"""Device adapters. The mock device is synthetic, not Apple's iOS Simulator."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def fail(code: str, **details: Any) -> dict:
    return {"ok": False, "error": code, **details}


class MockIPhone:
    """A deliberately synthetic virtual phone with a small WeChat-like fixture."""

    kind = "mock"
    simulated = True
    send_enabled = True  # Only virtual messages are sent, never external messages.

    def __init__(self):
        self.app = "home"
        self.screen = "home"
        self.revision = 0
        self.selected_id: str | None = None
        self.input_text = ""
        self.focused = False
        self.contacts = (
            {"id": "test-01", "name": "测试联系人"},
            {"id": "xiaoming-01", "name": "小明"},
            {"id": "alex-01", "name": "Alex"},
            {"id": "alex-02", "name": "Alex"},
        )
        self.messages: dict[str, list[dict]] = {x["id"]: [] for x in self.contacts}
        self.actions = []

    def _changed(self, action: str):
        self.revision += 1
        self.actions.append(action)

    def status(self):
        return {"ok": True, "driver": self.kind, "simulated": True,
                "real_device_connected": False, "capabilities": ["observe", "open_app", "home", "back", "tap", "swipe", "type_text", "list_contacts", "open_chat", "virtual_message"]}

    def observe(self):
        contact = next((c for c in self.contacts if c["id"] == self.selected_id), None)
        return {"ok": True, "driver": self.kind, "simulated": True,
                "app": self.app, "screen": self.screen, "revision": self.revision,
                "contact": dict(contact) if contact else None,
                "draft_length": len(self.input_text),
                "messages": [dict(m) for m in self.messages[self.selected_id]] if self.screen == "chat" and self.selected_id else [],
                "screenshot": None}

    def open_app(self, app: str):
        if app not in ("wechat", "settings"):
            return fail("app_not_allowlisted", app=app)
        self.app = app
        self.screen = "contacts" if app == "wechat" else "settings"
        self.selected_id = None
        self.input_text = ""
        self.focused = False
        self._changed("open_app")
        return {"ok": True, "simulated": True, "app": app, "screen": self.screen}

    def home(self):
        self.app, self.screen = "home", "home"
        self.selected_id, self.input_text, self.focused = None, "", False
        self._changed("home")
        return {"ok": True, "simulated": True}

    def back(self):
        if self.screen == "chat":
            self.screen = "contacts"
            self.selected_id, self.input_text, self.focused = None, "", False
        elif self.screen in ("contacts", "settings"):
            self.app, self.screen = "home", "home"
        else:
            return fail("nothing_to_go_back_to")
        self._changed("back")
        return {"ok": True, "simulated": True, "screen": self.screen}

    def list_contacts(self):
        if self.app != "wechat" or self.screen != "contacts":
            return fail("not_on_wechat_contacts")
        return {"ok": True, "simulated": True, "contacts": [dict(x) for x in self.contacts]}

    def open_chat(self, identity: str):
        if self.app != "wechat" or self.screen != "contacts":
            return fail("not_on_wechat_contacts")
        matches = [x for x in self.contacts if x["id"] == identity or x["name"] == identity]
        if not matches:
            return fail("contact_not_found")
        if len(matches) > 1:
            return fail("ambiguous_contact", candidates=[dict(x) for x in matches])
        self.screen = "chat"
        self.selected_id = matches[0]["id"]
        self.input_text = ""
        self.focused = False
        self._changed("open_chat")
        return {"ok": True, "simulated": True, "contact": dict(matches[0])}

    def tap(self, x: float, y: float):
        # Strictly fixture-specific coordinates, not genuine iPhone hit testing.
        if self.screen == "home" and 0.1 <= x <= 0.4 and 0.1 <= y <= 0.5:
            return self.open_app("wechat")
        if self.screen == "contacts":
            idx = round((y - 0.18) / 0.15)
            if 0 <= idx < len(self.contacts) and 0.1 <= x <= 0.9 and abs(y-(0.18+idx*.15)) <= 0.045:
                return self.open_chat(self.contacts[idx]["id"])
        if self.screen == "chat" and y >= 0.82:
            self.focused = True
            self._changed("tap_input")
            return {"ok": True, "simulated": True, "focused": True}
        return fail("no_interactive_element_at_point")

    def swipe(self, sx: float, sy: float, ex: float, ey: float):
        self._changed("swipe")
        return {"ok": True, "simulated": True, "note": "fixture scroll only"}

    def type_text(self, text: str):
        if self.screen != "chat" or not self.focused:
            return fail("input_not_focused")
        if len(self.input_text) + len(text) > 1000:
            return fail("draft_too_long")
        self.input_text += text
        self._changed("type_text")
        return {"ok": True, "simulated": True, "characters": len(text)}

    def virtual_send(self, contact_id: str, text: str):
        if self.screen != "chat" or self.selected_id != contact_id:
            return fail("recipient_context_changed")
        if text != self.input_text:
            return fail("draft_does_not_match_proposal")
        self.messages[contact_id].append({"direction": "outbound", "text": text, "simulated": True})
        self.input_text, self.focused = "", False
        self._changed("virtual_send")
        return {"ok": True, "simulated": True, "real_message_sent": False,
                "virtual_message_added": True, "recipient_id": contact_id,
                "message_sha256": hashlib.sha256(text.encode()).hexdigest()}


class AppleSimulatorDriver:
    """Intentionally limited simctl backend. No general-purpose UI control exists in simctl.

    Supports fixture launch and screenshot, not GUI taps, WeChat or real devices.
    """

    kind = "apple-simulator"
    simulated = True
    send_enabled = False

    def __init__(self, udid: str, fixture_bundle: str = "org.example.phonebridge.fixture", runner=None, screenshot_dir: str | None = None):
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", udid):
            raise ValueError("invalid simulator UDID")
        if not re.fullmatch(r"[A-Za-z0-9.-]{6,120}", fixture_bundle):
            raise ValueError("invalid fixture bundle ID")
        self.udid = udid.upper()
        self.fixture_bundle = fixture_bundle
        self._runner = runner or subprocess.run
        self.screenshot_dir = Path(screenshot_dir or tempfile.gettempdir())
        self.revision = 0

    def _call(self, args: list[str]):
        try:
            result = self._runner(["xcrun", "simctl", *args], capture_output=True, text=True, timeout=20, check=False)
            if result.returncode != 0:
                return fail("simctl_failed", details=result.stderr[:250])
            return {"ok": True, "stdout": result.stdout}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return fail("simctl_unavailable", details=type(exc).__name__)

    def status(self):
        result = self._call(["list", "devices", "available", "--json"])
        if not result["ok"]:
            return result
        try:
            devices = [d for v in json.loads(result["stdout"])["devices"].values() for d in v]
        except (ValueError, KeyError, TypeError):
            return fail("unexpected_simctl_output")
        chosen = next((d for d in devices if d.get("udid") == self.udid), None)
        if not chosen:
            return fail("simulator_not_found")
        return {"ok": True, "driver": self.kind, "simulated": True,
                "real_device_connected": False, "device": chosen.get("name"),
                "state": chosen.get("state"), "udid": self.udid,
                "capabilities": ["status", "screenshot", "launch_fixture"],
                "limitations": "No tap/swipe/type/send support. Real iPhone is not connected."}

    def observe(self):
        try:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            directory = Path(tempfile.mkdtemp(prefix="phonebridge-", dir=self.screenshot_dir))
        except OSError:
            return fail("screenshot_directory_unavailable")
        target = directory / "screen.png"
        digest = None
        try:
            result = self._call(["io", self.udid, "screenshot", str(target)])
            if not result["ok"]:
                return result
            if not target.is_file():
                return fail("screenshot_not_created")
            target.chmod(0o600)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            return fail("screenshot_unreadable")
        finally:
            if digest is None:
                shutil.rmtree(directory, ignore_errors=True)
        return {"ok": True, "driver": self.kind, "simulated": True,
                "screenshot_path": str(target), "sha256": digest,
                "revision": self.revision,
                "notice": "Screenshot is local to host; not automatically sent to AI model"}

    def open_app(self, app: str):
        if app != "fixture":
            return fail("unsupported_app", supported=["fixture"])
        result = self._call(["launch", self.udid, self.fixture_bundle])
        if result["ok"]:
            self.revision += 1
            return {"ok": True, "simulated": True, "app": "fixture", "note": "fixture launched; no interaction tested"}
        return result

    def home(self): return fail("unsupported_by_simctl", action="home")
    def back(self): return fail("unsupported_by_simctl", action="back")
    def list_contacts(self): return fail("unsupported_by_simctl", action="list_contacts")
    def open_chat(self, identity: str): return fail("unsupported_by_simctl", action="open_chat")
    def tap(self, x: float, y: float): return fail("unsupported_by_simctl", action="tap")
    def swipe(self, sx: float, sy: float, ex: float, ey: float): return fail("unsupported_by_simctl", action="swipe")
    def type_text(self, text: str): return fail("unsupported_by_simctl", action="type_text")
