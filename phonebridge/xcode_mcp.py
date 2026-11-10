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
from pathlib import Path
from typing import Any

from .drivers import fail

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

    def __init__(self, udid: str, *, client=None, session_identifier: str = "phonebridge",
                 interaction_approved: bool = False, fixture_bundle: str = FIXTURE_BUNDLE,
                 simulator_checker=None, timeout: float = DEFAULT_TIMEOUT):
        if not isinstance(udid, str) or not re.fullmatch(UDID_PATTERN, udid):
            raise ValueError("invalid simulator UDID")
        if not isinstance(fixture_bundle, str) or not re.fullmatch(BUNDLE_PATTERN, fixture_bundle):
            raise ValueError("invalid fixture bundle ID")
        if not isinstance(timeout, (int, float)) or not 0 < float(timeout) <= 120:
            raise ValueError("invalid bridge timeout")
        self.udid = udid.upper()
        self.client = client or MCPBridgeClient(timeout=float(timeout))
        self.simulator_checker = simulator_checker or default_simulator_checker
        self.session_identifier = session_identifier
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
        return result

    def _observation(self, hierarchy_path: str) -> dict:
        try:
            text = Path(hierarchy_path).read_text(encoding="utf-8")
        except (OSError, TypeError, ValueError):
            return fail("hierarchy_unavailable")
        bundle = BUNDLE_LINE_PATTERN.search(text)
        window = WINDOW_PATTERN.search(text)
        return {"ok": True,
                "foreground_bundle": bundle.group(1) if bundle else None,
                "dimensions": (float(window.group(1)), float(window.group(2))) if window else None}

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
        return {"ok": True, "dimensions": info["dimensions"],
                "foreground_bundle": info["foreground_bundle"]}

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
                "capabilities": ["status", "observe", "open_app"],
                "limited_capabilities": ["tap", "swipe", "type_text", "home"],
                "limitations": "Only allowlisted Xcode MCP tools. Interactions require a verified Simulator, the fixture in foreground, a fresh observation and out-of-band enablement. No message sending."}

    def observe(self):
        with self._lock:
            result = self._synthesize("")
            if not result.get("ok"):
                return result
            info = self._observation(result["hierarchyPath"])
            if not info.get("ok"):
                return info
            return self._state(result, foreground_bundle=info["foreground_bundle"],
                              fixture_in_foreground=info["foreground_bundle"] == self.fixture_bundle)

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
            return self._interaction(f"t {round(x * width)} {round(y * height)}", guard=guard)

    def swipe(self, sx: float, sy: float, ex: float, ey: float):
        if not self.interaction_approved:
            return fail("independent_approval_required")
        with self._lock:
            guard = self._pre_interaction()
            if not guard.get("ok"):
                return guard
            width, height = guard["dimensions"]
            return self._interaction(
                f"t {round(sx * width)} {round(sy * height)} f {round(ex * width)} {round(ey * height)} 0.3",
                guard=guard)

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
