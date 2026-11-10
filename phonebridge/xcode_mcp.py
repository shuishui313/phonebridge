"""Adapter for Apple's official Xcode MCP bridge (``xcrun mcpbridge``).

Only device-interaction and diagnostic tools are allowlisted. Mutating device
interactions are denied by default and require an out-of-band human decision at
process start; the Agent cannot enable them through an MCP tool.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from .drivers import fail

UDID_PATTERN = r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
BUNDLE_PATTERN = r"[A-Za-z0-9.-]{6,120}"
WINDOW_PATTERN = re.compile(r"Window, \{\{[\d.]+, [\d.]+\}, \{([\d.]+), ([\d.]+)\}\}")

ALLOWED_TOOLS = frozenset({
    "DeviceInteractionStartSession",
    "DeviceInteractionStartWorkspaceSession",
    "DeviceInteractionInstallAndRun",
    "DeviceInteractionSynthesize",
    "DeviceInteractionEndSession",
    "GetConsoleOutput",
})

FIXTURE_BUNDLE = "org.example.phonebridge.fixture"
APP_BUNDLES = {"fixture": FIXTURE_BUNDLE}


class BridgeUnavailable(Exception):
    """Raised when the official MCP bridge cannot be reached."""


class MCPBridgeClient:
    """Minimal stdio JSON-RPC client for ``xcrun mcpbridge``."""

    def __init__(self, command=("xcrun", "mcpbridge"), *, popen=None):
        self.command = tuple(command)
        self._popen = popen or subprocess.Popen
        self._proc = None
        self._next_id = 1
        self._lock = threading.RLock()

    def _send(self, message: dict) -> None:
        try:
            self._proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (OSError, AttributeError, ValueError) as exc:
            raise BridgeUnavailable("mcpbridge stdin unavailable") from exc

    def _read_id(self, request_id: int) -> dict:
        while True:
            try:
                line = self._proc.stdout.readline()
            except (OSError, AttributeError, ValueError) as exc:
                raise BridgeUnavailable("mcpbridge stdout unavailable") from exc
            if not line:
                raise BridgeUnavailable("mcpbridge closed the connection")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == request_id:
                return message

    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            try:
                self._proc = self._popen(
                    list(self.command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                )
            except OSError as exc:
                raise BridgeUnavailable("xcrun mcpbridge unavailable") from exc
            self._send({"jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
                        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "phonebridge", "version": "0.4"}}})
            hello = self._read_id(self._next_id)
            self._next_id += 1
            if "result" not in hello or "error" in hello:
                self.close()
                raise BridgeUnavailable("mcpbridge initialize failed")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name: str, arguments: dict) -> dict:
        if name not in ALLOWED_TOOLS:
            return fail("tool_not_allowlisted", tool=name)
        try:
            self.start()
        except BridgeUnavailable as exc:
            return fail("xcode_mcp_unavailable", details=str(exc))
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            try:
                self._send({"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                            "params": {"name": name, "arguments": arguments}})
                response = self._read_id(request_id)
            except BridgeUnavailable as exc:
                return fail("xcode_mcp_unavailable", details=str(exc))
        if "error" in response:
            return fail("xcode_mcp_rpc_error", details=str(response["error"])[:250])
        result = response.get("result") or {}
        texts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)]
        text = texts[0] if texts else ""
        if result.get("isError"):
            return fail("xcode_tool_error", details=text[:250])
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"raw": text}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        payload.setdefault("ok", True)
        return payload

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            proc.kill()


class XcodeMCPDriver:
    """PhoneBridge driver backed by Apple's official device interaction tools."""

    kind = "xcode-mcp"
    simulated = False
    send_enabled = False
    requires_interaction_approval = True

    def __init__(self, udid: str, *, client=None, session_identifier: str = "phonebridge",
                 interaction_approved: bool = False, fixture_bundle: str = FIXTURE_BUNDLE):
        if not isinstance(udid, str) or not re.fullmatch(UDID_PATTERN, udid):
            raise ValueError("invalid simulator UDID")
        if not isinstance(fixture_bundle, str) or not re.fullmatch(BUNDLE_PATTERN, fixture_bundle):
            raise ValueError("invalid fixture bundle ID")
        self.udid = udid.upper()
        self.client = client or MCPBridgeClient()
        self.session_identifier = session_identifier
        self.interaction_approved = bool(interaction_approved)
        self.fixture_bundle = fixture_bundle
        self.revision = 0
        self._lock = threading.RLock()
        self._session_key: str | None = None
        self._last_hierarchy: str | None = None
        self._last_screenshot: str | None = None
        self._closed = False

    def _invoke(self, name: str, arguments: dict) -> dict:
        if name not in ALLOWED_TOOLS:
            return fail("tool_not_allowlisted", tool=name)
        return self.client.call(name, arguments)

    def _ensure_session(self) -> dict | None:
        if self._closed:
            return fail("session_closed")
        if self._session_key:
            return None
        result = self._invoke("DeviceInteractionStartSession", {
            "deviceIdentifier": self.udid, "sessionIdentifier": self.session_identifier,
        })
        if not result.get("ok"):
            return result
        self._session_key = result.get("interactionSessionKey") or self.session_identifier
        return None

    def _synthesize(self, command: str = "", *, bundle: str | None = None) -> dict:
        problem = self._ensure_session()
        if problem:
            return problem
        arguments: dict[str, Any] = {"interactSessionKey": self._session_key,
                                     "interactionCommand": command}
        if bundle:
            arguments["activationBundleId"] = bundle
        result = self._invoke("DeviceInteractionSynthesize", arguments)
        if not result.get("ok"):
            return result
        self._last_hierarchy = result.get("hierarchyPath") or self._last_hierarchy
        self._last_screenshot = result.get("screenshotPath") or self._last_screenshot
        return result

    def _state(self, result: dict, **extra: Any) -> dict:
        digest = None
        if self._last_screenshot:
            try:
                digest = hashlib.sha256(Path(self._last_screenshot).read_bytes()).hexdigest()
            except OSError:
                digest = None
        return {"ok": True, "driver": self.kind, "simulated": False,
                "screenshot_path": self._last_screenshot, "hierarchy_path": self._last_hierarchy,
                "sha256": digest, "revision": self.revision,
                "notice": "Screenshot and hierarchy are local to this host.", **extra}

    def status(self):
        return {"ok": True, "driver": self.kind, "simulated": False,
                "real_device_connected": False, "device_udid": self.udid,
                "interaction_approved": self.interaction_approved,
                "capabilities": ["status", "observe", "open_app"],
                "limited_capabilities": ["tap", "swipe", "type_text", "home"],
                "limitations": "Only allowlisted Xcode MCP tools. No message sending, no workspace or build tools. Mutating interactions require out-of-band enablement."}

    def observe(self):
        with self._lock:
            result = self._synthesize("")
            return self._state(result) if result.get("ok") else result

    def open_app(self, app: str):
        bundle = APP_BUNDLES.get(app)
        if bundle is None:
            return fail("app_not_allowlisted", app=app)
        with self._lock:
            result = self._synthesize("", bundle=self.fixture_bundle)
            if not result.get("ok"):
                return result
            self.revision += 1
            return self._state(result, app=app, bundle_id=bundle,
                               application_state=result.get("applicationState"))

    def _screen_size(self) -> tuple[float, float] | dict:
        if not self._last_hierarchy:
            captured = self._synthesize("")
            if not captured.get("ok"):
                return captured
        try:
            text = Path(self._last_hierarchy).read_text(encoding="utf-8")
        except (OSError, TypeError, ValueError):
            return fail("hierarchy_unavailable")
        match = WINDOW_PATTERN.search(text)
        if not match:
            return fail("screen_dimensions_unavailable")
        return float(match.group(1)), float(match.group(2))

    def _interaction(self, command: str) -> dict:
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            result = self._synthesize(command)
            if not result.get("ok"):
                return result
            self.revision += 1
            return self._state(result, interaction_command=command,
                               application_state=result.get("applicationState"))

    def tap(self, x: float, y: float):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            dimensions = self._screen_size()
            if isinstance(dimensions, dict):
                return dimensions
            width, height = dimensions
            return self._interaction(f"t {round(x * width)} {round(y * height)}")

    def swipe(self, sx: float, sy: float, ex: float, ey: float):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            dimensions = self._screen_size()
            if isinstance(dimensions, dict):
                return dimensions
            width, height = dimensions
            return self._interaction(
                f"t {round(sx * width)} {round(sy * height)} f {round(ex * width)} {round(ey * height)} 0.3")

    def type_text(self, text: str):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        if not isinstance(text, str) or not text or any(ord(ch) < 32 or ch == "\\" for ch in text):
            return fail("unsupported_text_characters")
        return self._interaction(f"sender keyboard kbd {text}")

    def home(self):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        return self._interaction("b h")

    def back(self):
        return fail("unsupported_by_xcode_mcp", action="back")

    def list_contacts(self):
        return fail("unsupported_by_xcode_mcp", action="list_contacts")

    def open_chat(self, identity: str):
        return fail("unsupported_by_xcode_mcp", action="open_chat")

    def virtual_send(self, contact_id: str, text: str):
        return fail("real_message_sending_disabled")

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            key = self._session_key
        if key:
            self._invoke("DeviceInteractionEndSession", {"interactionSessionKey": key})
        self.client.close()
