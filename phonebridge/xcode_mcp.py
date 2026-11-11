"""Adapter for Apple's official Xcode MCP bridge (``xcrun mcpbridge``).

Only device-interaction and diagnostic tools are allowlisted. Before any
interaction the driver verifies that the target is an Apple Simulator, that
the foreground app is the allowlisted fixture, and that the observation used
for decisions is fresh. Mutating interactions additionally require an
out-of-band human decision at process start.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .drivers import MESSAGING_APP, fail

UDID_PATTERN = r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
BUNDLE_PATTERN = r"[A-Za-z0-9.-]{6,120}"
WINDOW_PATTERN = re.compile(r"Window, \{\{[\d.]+, [\d.]+\}, \{([\d.]+), ([\d.]+)\}\}")
BUNDLE_LINE_PATTERN = re.compile(r"Application bundle identifier:\s*([A-Za-z0-9.-]+)")

DEFAULT_TIMEOUT = 15.0
CLOSE_TIMEOUT = 5.0
SIMCTL_TIMEOUT = 8.0

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

# Enabled submit controls are identified by stable accessibility IDs. Generic
# taps/swipes must never reach them, whether or not the spike disabled them.
RECORD_IDENTIFIER = re.compile(r"identifier:\s*'(?:chat\.message|timeline\.post)\.\d+'")
ELEMENT_IDENTIFIER = re.compile(r"identifier:\s*'([^']*)'")
ELEMENT_HITPOINT = re.compile(r"hitPoint:\s*\{([\d.]+),\s*([\d.]+)\}")
SUBMIT_IDENTIFIERS = frozenset({"chat.send", "compose.post"})
SUBMIT_TOLERANCE = 30.0

# Fixed synthetic contacts in the owned fixture. "Alex" appears twice, so it
# is deliberately not resolvable by display name and can never be bound to an
# operation. No real contacts or messages are ever read here.
FIXTURE_CONTACTS = (
    {"id": "contact-01", "name": "Test Contact"},
    {"id": "contact-02", "name": "Casey"},
    {"id": "alex-01", "name": "Alex"},
    {"id": "alex-02", "name": "Alex"},
)
CHAT_RECORD_IDENTIFIER = re.compile(r"identifier:\s*'chat\.message\.(\d+)'")
CHAT_INPUT_IDENTIFIER = re.compile(r"identifier:\s*'chat\.input'")
CONTACT_ROW_IDENTIFIER = re.compile(r"identifier:\s*'contact\.[A-Za-z0-9_-]+'")
NAVIGATION_IDENTIFIER = re.compile(r"NavigationBar,.*?identifier:\s*'([^']*)'")
ELEMENT_VALUE = re.compile(r"(?:value|label):\s*(?:'([^']*)'|([^,]+))")


class BridgeUnavailable(Exception):
    """Raised when the official MCP bridge cannot be reached in time."""


def default_simulator_checker(udid: str) -> dict:
    """Verify a UDID belongs to an available Apple Simulator, never a real device."""
    try:
        proc = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "available", "-j"],
            capture_output=True, text=True, timeout=SIMCTL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return fail("simulator_verification_failed", details=str(exc)[:200])
    if proc.returncode != 0:
        return fail("simulator_verification_failed", details=(proc.stderr or "")[:200])
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return fail("simulator_verification_failed")
    for runtime, devices in (payload.get("devices") or {}).items():
        for device in devices:
            if (str(device.get("udid", "")).upper() == udid.upper()
                    and device.get("isAvailable", True)):
                return {"ok": True, "runtime": runtime, "device_name": device.get("name", "")}
    return fail("not_a_simulator", udid=udid)


class MCPBridgeClient:
    """Minimal stdio JSON-RPC client for ``xcrun mcpbridge`` with hard timeouts."""

    def __init__(self, command=("xcrun", "mcpbridge"), *, popen=None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.command = tuple(command)
        self._popen = popen or subprocess.Popen
        self.timeout = float(timeout)
        self._proc = None
        self._buffer = bytearray()
        self._next_id = 1
        self._lock = threading.RLock()

    def _terminate(self, proc) -> None:
        try:
            proc.stdin.close()
        except (OSError, AttributeError, ValueError):
            pass
        try:
            proc.terminate()
            proc.wait(timeout=CLOSE_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
            try:
                proc.kill()
            except (OSError, AttributeError):
                pass

    def _reset(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            self._buffer.clear()
        if proc is not None:
            self._terminate(proc)

    def _send(self, message: dict) -> None:
        try:
            self._proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (OSError, AttributeError, ValueError) as exc:
            raise BridgeUnavailable("mcpbridge stdin unavailable") from exc

    def _pop_line(self) -> str | None:
        index = self._buffer.find(b"\n")
        if index < 0:
            return None
        raw = bytes(self._buffer[:index])
        del self._buffer[:index + 1]
        return raw.decode("utf-8", "replace")

    def _read_id(self, request_id: int) -> dict:
        deadline = time.monotonic() + self.timeout
        while True:
            line = self._pop_line()
            if line is not None:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("id") == request_id:
                    return message
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeUnavailable(f"mcpbridge timed out after {self.timeout}s")
            try:
                fd = self._proc.stdout.fileno()
                ready, _, _ = select.select([fd], [], [], remaining)
            except (OSError, ValueError, AttributeError) as exc:
                raise BridgeUnavailable("mcpbridge stdout unavailable") from exc
            if not ready:
                raise BridgeUnavailable(f"mcpbridge timed out after {self.timeout}s")
            try:
                chunk = os.read(fd, 65536)
            except OSError as exc:
                raise BridgeUnavailable("mcpbridge stdout unavailable") from exc
            if not chunk:
                raise BridgeUnavailable("mcpbridge closed the connection")
            self._buffer.extend(chunk)

    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            try:
                self._proc = self._popen(
                    list(self.command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, bufsize=0,
                )
            except OSError as exc:
                raise BridgeUnavailable("xcrun mcpbridge unavailable") from exc
            self._buffer.clear()
            try:
                self._send({"jsonrpc": "2.0", "id": self._next_id, "method": "initialize",
                            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                       "clientInfo": {"name": "phonebridge", "version": "0.4"}}})
                hello = self._read_id(self._next_id)
                self._next_id += 1
                if "result" not in hello or "error" in hello:
                    raise BridgeUnavailable("mcpbridge initialize failed")
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except BridgeUnavailable:
                self._reset()
                raise

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
                self._reset()
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
        self._reset()


class XcodeMCPDriver:
    """PhoneBridge driver backed by Apple's official device interaction tools."""

    kind = "xcode-mcp"
    simulated = False
    send_enabled = False
    requires_interaction_approval = True

    def __init__(self, udid: str, *, client=None, session_identifier: str | None = None,
                 interaction_approved: bool = False, fixture_bundle: str = FIXTURE_BUNDLE,
                 simulator_checker=None, timeout: float = DEFAULT_TIMEOUT,
                 virtual_submit_enabled: bool = False):
        if not isinstance(udid, str) or not re.fullmatch(UDID_PATTERN, udid):
            raise ValueError("invalid simulator UDID")
        if not isinstance(fixture_bundle, str) or not re.fullmatch(BUNDLE_PATTERN, fixture_bundle):
            raise ValueError("invalid fixture bundle ID")
        if not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 120:
            raise ValueError("invalid bridge timeout")
        if session_identifier is not None and (not isinstance(session_identifier, str)
                                               or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", session_identifier)):
            raise ValueError("invalid session identifier")
        self.udid = udid.upper()
        self.client = client or MCPBridgeClient(timeout=float(timeout))
        self.simulator_checker = simulator_checker or default_simulator_checker
        # Xcode rejects reusing a recently used session identifier; each process
        # gets its own unless the operator explicitly supplies one.
        self.session_identifier = session_identifier or f"phonebridge-{uuid.uuid4().hex[:8]}"
        self.interaction_approved = bool(interaction_approved)
        self.fixture_bundle = fixture_bundle
        self.revision = 0
        self._lock = threading.RLock()
        self._session_key: str | None = None
        self._last_hierarchy: str | None = None
        self._last_screenshot: str | None = None
        self._preflight_ok = False
        self._simulator_runtime: str | None = None
        self._closed = False
        # A human opt-in is required before the approved submission gate may
        # produce a synthetic record inside the owned fixture. This is never a
        # real message send and never reachable through generic taps.
        self.virtual_submit_enabled = bool(virtual_submit_enabled)
        # Synthetic context derived ONLY from the most recent fresh hierarchy.
        # It implements the same contract as MockIPhone so the approval core
        # needs no device-specific branches.
        self.app = "home"
        self.screen = "home"
        self.selected_id: str | None = None
        self.input_text = ""
        self.contacts = tuple(dict(c) for c in FIXTURE_CONTACTS)
        self.messages: dict[str, list[dict]] = {}
        self._fixture_records: list[str] = []

    def _invoke(self, name: str, arguments: dict) -> dict:
        if name not in ALLOWED_TOOLS:
            return fail("tool_not_allowlisted", tool=name)
        result = self.client.call(name, arguments)
        if result.get("error") == "xcode_mcp_unavailable":
            # A dead bridge invalidates its session; never reuse it.
            self._session_key = None
            self._preflight_ok = False
        return result

    def _ensure_session(self) -> dict | None:
        if self._closed:
            return fail("session_closed")
        if self._session_key:
            return None
        if not self._preflight_ok:
            check = self.simulator_checker(self.udid)
            if not isinstance(check, dict) or not check.get("ok"):
                return check if isinstance(check, dict) else fail("simulator_verification_failed")
            self._simulator_runtime = check.get("runtime")
        result = self._invoke("DeviceInteractionStartSession", {
            "deviceIdentifier": self.udid, "sessionIdentifier": self.session_identifier,
        })
        if not result.get("ok"):
            return result
        if result.get("deviceIsSimulator") is not True:
            return fail("device_type_unverified", device_is_simulator=result.get("deviceIsSimulator"))
        returned_udid = result.get("deviceUUID")
        if not isinstance(returned_udid, str) or returned_udid.upper() != self.udid:
            return fail("device_identity_mismatch", device_uuid=returned_udid)
        self._session_key = result.get("interactionSessionKey") or self.session_identifier
        self._preflight_ok = True
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
        hierarchy = result.get("hierarchyPath")
        screenshot = result.get("screenshotPath")
        if not hierarchy or not screenshot:
            return fail("fresh_observation_unavailable",
                        hierarchy=bool(hierarchy), screenshot=bool(screenshot))
        # Never fall back to a cached observation for a current decision.
        self._last_hierarchy = hierarchy
        self._last_screenshot = screenshot
        self._refresh_fixture_state()
        return result

    def _refresh_fixture_state(self) -> None:
        """Derive synthetic approval context from the latest fresh hierarchy.

        Only the currently visible chat is exposed in ``messages``, so a record
        from another contact can never be attributed to an operation.
        """
        text = ""
        if self._last_hierarchy:
            try:
                text = Path(self._last_hierarchy).read_text(encoding="utf-8")
            except (OSError, TypeError, ValueError):
                text = ""
        if CHAT_INPUT_IDENTIFIER.search(text):
            screen = "chat"
        elif CONTACT_ROW_IDENTIFIER.search(text):
            screen = "contacts"
        elif re.search(r"identifier:\s*'app\.(?:whatsapp|x)'", text):
            screen = "home"
        else:
            screen = "other"
        self.screen = screen
        self.app = MESSAGING_APP if screen in ("chat", "contacts") else screen

        self.input_text = ""
        for line in text.splitlines():
            if CHAT_INPUT_IDENTIFIER.search(line):
                value = ELEMENT_VALUE.search(line)
                self.input_text = ((value.group(1) or value.group(2) or "").strip()
                                   if value else "")
                break

        self.selected_id = None
        nav = NAVIGATION_IDENTIFIER.search(text)
        if screen == "chat" and nav:
            matches = [c["id"] for c in FIXTURE_CONTACTS if c["name"] == nav.group(1)]
            if len(matches) == 1:  # ambiguous display names cannot be bound
                self.selected_id = matches[0]

        found = []
        for line in text.splitlines():
            identifier = CHAT_RECORD_IDENTIFIER.search(line)
            if not identifier:
                continue
            value = ELEMENT_VALUE.search(line)
            found.append((int(identifier.group(1)),
                          (value.group(1) or value.group(2) or "").strip() if value else ""))
        found.sort(key=lambda item: item[0])
        self._fixture_records = [body for _, body in found]
        self.messages = ({self.selected_id: [{"direction": "outbound", "text": body}
                                             for body in self._fixture_records]}
                         if screen == "chat" and self.selected_id else {})

    def resolve_contact_id(self, identity: str) -> str | None:
        if not isinstance(identity, str):
            return None
        for contact in self.contacts:
            if contact["id"] == identity:
                return contact["id"]
        matches = [c["id"] for c in self.contacts if c["name"] == identity]
        return matches[0] if len(matches) == 1 else None

    def _observation(self, hierarchy_path: str) -> dict:
        try:
            text = Path(hierarchy_path).read_text(encoding="utf-8")
        except (OSError, TypeError, ValueError):
            return fail("hierarchy_unavailable")
        bundle = BUNDLE_LINE_PATTERN.search(text)
        window = WINDOW_PATTERN.search(text)
        submit_points, unlocated = [], False
        for line in text.splitlines():
            identifier = ELEMENT_IDENTIFIER.search(line)
            if not identifier or identifier.group(1) not in SUBMIT_IDENTIFIERS:
                continue
            point = ELEMENT_HITPOINT.search(line)
            if point:
                submit_points.append((float(point.group(1)), float(point.group(2))))
            else:
                unlocated = True  # an enabled submit control we cannot localize
        return {"ok": True,
                "foreground_bundle": bundle.group(1) if bundle else None,
                "dimensions": (float(window.group(1)), float(window.group(2))) if window else None,
                "submit_points": submit_points, "submit_unlocated": unlocated}

    def record_count(self) -> int | None:
        """Records visible in the current fresh hierarchy; never a cached one."""
        if not self._last_hierarchy:
            return None
        try:
            text = Path(self._last_hierarchy).read_text(encoding="utf-8")
        except (OSError, TypeError, ValueError):
            return None
        return len(RECORD_IDENTIFIER.findall(text))

    @staticmethod
    def _submit_guard(points, x: float, y: float):
        for sx, sy in points:
            if abs(x - sx) <= SUBMIT_TOLERANCE and abs(y - sy) <= SUBMIT_TOLERANCE:
                return {"x": sx, "y": sy}
        return None

    def _pre_interaction(self) -> dict:
        captured = self._synthesize("")
        if not captured.get("ok"):
            return captured
        info = self._observation(captured["hierarchyPath"])
        if not info.get("ok"):
            return info
        if info["foreground_bundle"] != self.fixture_bundle:
            return fail("foreground_app_not_allowlisted",
                        foreground_bundle=info["foreground_bundle"])
        if not info["dimensions"]:
            return fail("screen_dimensions_unavailable")
        if info["submit_unlocated"]:
            return fail("submit_guard_unavailable")
        return {"ok": True, "dimensions": info["dimensions"],
                "foreground_bundle": info["foreground_bundle"],
                "submit_points": info["submit_points"]}

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
                "preflight_passed": self._preflight_ok,
                "simulator_runtime": self._simulator_runtime,
                "fixture_bundle": self.fixture_bundle,
                "bridge_timeout": getattr(self.client, "timeout", None),
                "virtual_submit_enabled": self.virtual_submit_enabled,
                "capabilities": ["status", "observe", "open_app"],
                "limited_capabilities": ["tap", "swipe", "type_text", "home"],
                "limitations": (
                    "Only allowlisted Xcode MCP tools. Interactions require a verified "
                    "Simulator, the fixture in foreground, a fresh observation and "
                    "out-of-band enablement. Generic taps never reach enabled submit "
                    "controls. With an explicit human opt-in the trusted-side approval "
                    "gate may create one synthetic record inside the owned fixture; "
                    "no real message is sent and no third-party app is touched.")}

    def observe(self):
        with self._lock:
            result = self._synthesize("")
            if not result.get("ok"):
                return result
            info = self._observation(result["hierarchyPath"])
            if not info.get("ok"):
                return info
            return self._state(
                result, foreground_bundle=info["foreground_bundle"],
                fixture_in_foreground=info["foreground_bundle"] == self.fixture_bundle,
                app=self.app, screen=self.screen, selected_id=self.selected_id,
                draft_length=len(self.input_text),
                record_count=len(self._fixture_records),
                virtual_submit_enabled=self.virtual_submit_enabled)

    def open_app(self, app: str):
        bundle = APP_BUNDLES.get(app)
        if bundle is None:
            return fail("app_not_allowlisted", app=app)
        with self._lock:
            result = self._synthesize("", bundle=self.fixture_bundle)
            if not result.get("ok"):
                return result
            info = self._observation(result["hierarchyPath"])
            if not info.get("ok"):
                return info
            if info["foreground_bundle"] != self.fixture_bundle:
                return fail("foreground_app_not_allowlisted",
                            foreground_bundle=info["foreground_bundle"])
            self.revision += 1
            return self._state(result, app=app, bundle_id=bundle,
                              foreground_bundle=info["foreground_bundle"],
                              application_state=result.get("applicationState"))

    def _interaction(self, command: str, *, guard: dict | None = None) -> dict:
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            if guard is None:
                guard = self._pre_interaction()
                if not guard.get("ok"):
                    return guard
            result = self._synthesize(command)
            if not result.get("ok"):
                return result
            self.revision += 1
            return self._state(result, interaction_command=command,
                               foreground_bundle=guard.get("foreground_bundle"),
                               application_state=result.get("applicationState"))

    def tap(self, x: float, y: float):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            guard = self._pre_interaction()
            if not guard.get("ok"):
                return guard
            width, height = guard["dimensions"]
            px, py = round(x * width), round(y * height)
            blocked = self._submit_guard(guard["submit_points"], px, py)
            if blocked:
                return fail("submission_requires_approval", interaction="tap",
                            hit_point=blocked)
            return self._interaction(f"t {px} {py}", guard=guard)

    def swipe(self, sx: float, sy: float, ex: float, ey: float):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            guard = self._pre_interaction()
            if not guard.get("ok"):
                return guard
            width, height = guard["dimensions"]
            start_x, start_y = round(sx * width), round(sy * height)
            end_x, end_y = round(ex * width), round(ey * height)
            points = guard["submit_points"]
            for label, (x, y) in (("start", (start_x, start_y)), ("end", (end_x, end_y))):
                blocked = self._submit_guard(points, x, y)
                if blocked:
                    return fail("submission_requires_approval", interaction=f"swipe_{label}",
                                hit_point=blocked)
            return self._interaction(
                f"t {start_x} {start_y} f {end_x} {end_y} 0.3", guard=guard)

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
        """Approved submission gate ONLY: create one synthetic fixture record.

        Generic taps and swipes cannot reach this path (``tap`` refuses to hit
        an enabled submit control). Requires a human opt-in, a verified
        Simulator, the fixture in foreground, and a fresh observation binding
        the exact recipient and draft. Nothing leaves the synthetic fixture.
        """
        if not self.virtual_submit_enabled or not self.interaction_approved:
            return fail("real_message_sending_disabled")
        if not isinstance(text, str) or not text.strip() or not contact_id:
            return fail("submission_context_mismatch")
        with self._lock:
            guard = self._pre_interaction()
            if not guard.get("ok"):
                return guard
            if (self.app != MESSAGING_APP or self.screen != "chat"
                    or self.selected_id != contact_id or self.input_text != text):
                return fail("submission_context_mismatch")
            points = guard.get("submit_points") or []
            if len(points) != 1:
                # Includes the G1 baseline where Send/Post are disabled.
                return fail("submit_control_unavailable")
            before = len(self._fixture_records)
            px, py = points[0]
            result = self._interaction(f"t {round(px)} {round(py)}", guard=guard)
            if not result.get("ok"):
                return result
            if (len(self._fixture_records) != before + 1
                    or self._fixture_records[-1] != text):
                return fail("virtual_submission_not_confirmed",
                            note="Do not resend; read the device back and pause.")
            return {"ok": True, "simulated": True, "virtual_fixture_send": True,
                    "real_message_sent": False, "recipient_id": contact_id,
                    "message_length": len(text), "record_count": len(self._fixture_records),
                    "foreground_bundle": guard.get("foreground_bundle"),
                    "hierarchy_path": self._last_hierarchy}

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            key = self._session_key
        if key:
            self._invoke("DeviceInteractionEndSession", {"interactionSessionKey": key})
        self.client.close()
