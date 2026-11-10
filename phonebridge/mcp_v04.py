"""Small stdio MCP JSON-RPC host implemented without third-party dependencies.

Covers initialize, ping, tools/list, tools/call and initialized notification.
No claim of support for resources/prompts/sampling/HTTP transports.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

from .core import PhoneBridge, valid_utf8
from .drivers import fail
from .mcp import VERSIONS, RpcError, read_message, rpc_error, write_message


PARAMS: dict[str, tuple[str, ...]] = {
    "phone_status": (),
    "phone_inspect": (),
    "phone_open_app": ("app",),
    "phone_home": (),
    "phone_back": (),
    "phone_tap": ("x", "y"),
    "phone_swipe": ("start_x", "start_y", "end_x", "end_y"),
    "phone_type_text": ("text",),
    "wechat_list_contacts": (),
    "wechat_open_chat": ("contact",),
    "wechat_prepare_message": ("recipient_id", "message"),
    "phone_execute_operation": ("approval_id",),
}

DESCRIPTIONS = {
    "phone_status": "Return device mode and capability disclosure. Do not assume a real phone is connected.",
    "phone_inspect": "Observe simulated screen / selected chat. On simctl, capture screenshot to a private local path.",
    "phone_open_app": "Open an allowlisted app. Mock: wechat/settings. Apple Simulator: fixture only.",
    "phone_home": "Go to mock home screen. Not supported by simctl backend.",
    "phone_back": "Back navigation on mock fixture only.",
    "phone_tap": "Tap normalized x/y in [0,1]. In mock these are fixed fixture hit targets, NOT real taps.",
    "phone_swipe": "Swipe using normalized coordinates. Not real iPhone control.",
    "phone_type_text": "Type into previously focused input. Never automatically presses Send.",
    "wechat_list_contacts": "List synthetic fixture contacts. Not real WeChat data.",
    "wechat_open_chat": "Open synthetic fixture contact by exact name or stable ID. Reject ambiguity.",
    "wechat_prepare_message": "Prepare virtual message; requires typed draft, current verified recipient. Must be approved through independent local CLI.",
    "phone_execute_operation": "Execute one approved virtual message exactly once. Cannot send a real iPhone message.",
}

NUMERIC = {"x", "y", "start_x", "start_y", "end_x", "end_y"}


def _tool_schema(name: str) -> dict:
    fields = PARAMS[name]
    properties = {f: {"type": "number" if f in NUMERIC else "string"} for f in fields}
    return {"name": name, "description": DESCRIPTIONS[name],
            "inputSchema": {"type": "object", "properties": properties, "required": list(fields), "additionalProperties": False}}


class MCPServer:
    def __init__(self, bridge: PhoneBridge):
        self.bridge = bridge
        self.initialized = False
        self.version = None

    def _call(self, name: str, args: dict) -> dict:
        if name not in PARAMS:
            return fail("unknown_tool")
        expected = set(PARAMS[name])
        if set(args) != expected:
            return fail("invalid_parameters", expected=sorted(expected))
        for key, value in args.items():
            if key in NUMERIC:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return fail("invalid_parameter_type", parameter=key)
            elif not isinstance(value, str):
                return fail("invalid_parameter_type", parameter=key)
            elif not valid_utf8(value):
                return fail("invalid_parameter_value", parameter=key)
        b = self.bridge
        table: dict[str, Callable] = {
            "phone_status": b.status, "phone_inspect": b.inspect,
            "phone_open_app": b.open_app, "phone_home": b.home, "phone_back": b.back,
            "phone_tap": b.tap, "phone_swipe": b.swipe, "phone_type_text": b.type_text,
            "wechat_list_contacts": b.list_contacts, "wechat_open_chat": b.open_chat,
            "wechat_prepare_message": b.prepare_message, "phone_execute_operation": b.execute,
        }
        return table[name](*[args[k] for k in PARAMS[name]])

    def process(self, data: Any) -> dict | None:
        request_id = None
        try:
            if (
                not isinstance(data, dict)
                or data.get("jsonrpc") != "2.0"
                or not isinstance(data.get("method"), str)
            ):
                raise RpcError(-32600, "Invalid request.")
            method = data["method"]
            params = data.get("params", {})
            if "id" not in data:
                if method == "notifications/initialized" and self.version and isinstance(params, dict):
                    self.initialized = True
                return None
            candidate_id = data["id"]
            if type(candidate_id) not in (int, str) or (
                isinstance(candidate_id, str) and not valid_utf8(candidate_id)
            ):
                raise RpcError(-32600, "Expected a string or integer request ID.")
            request_id = candidate_id
            if not isinstance(params, dict):
                raise RpcError(-32602, "Expected an object for params.")
            result = self._dispatch(method, params)
        except RpcError as error:
            return rpc_error(request_id, error.code, error.message)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "ping":
            return {}
        if method == "initialize":
            if self.version:
                raise RpcError(-32600, "This session is already initialized.")
            requested = params.get("protocolVersion")
            client = params.get("clientInfo")
            if (
                not isinstance(requested, str)
                or not isinstance(client, dict)
                or not isinstance(client.get("name"), str)
                or not isinstance(client.get("version"), str)
                or not isinstance(params.get("capabilities"), dict)
            ):
                raise RpcError(-32602, "Expected protocolVersion, clientInfo and capabilities.")
            self.version = requested if requested in VERSIONS else "2025-06-18"
            return {
                "protocolVersion": self.version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "phonebridge", "version": "0.4.0"},
            }
        if method not in ("tools/list", "tools/call"):
            raise RpcError(-32601, "Method not found.")
        if not self.initialized:
            raise RpcError(-32002, "Complete initialization before using tools.")
        if method == "tools/list":
            if params.get("cursor") is not None:
                raise RpcError(-32602, "This server has no additional tool pages.")
            return {"tools": [_tool_schema(name) for name in PARAMS]}
        if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
            raise RpcError(-32602, "Expected a tool name and object arguments.")
        try:
            payload = self._call(params["name"], params.get("arguments", {}))
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except Exception:  # Keep driver paths and secrets out of protocol errors.
            payload = fail("internal_tool_failure")
            content = json.dumps(payload)
        result = {
            "content": [{"type": "text", "text": content}],
            "isError": not payload.get("ok", False),
        }
        if self.version in VERSIONS[:2]:
            result["structuredContent"] = payload
        return result

    def serve(self, stdin=None, stdout=None):
        source = stdin if stdin is not None else sys.stdin.buffer
        sink = stdout if stdout is not None else sys.stdout.buffer
        while True:
            try:
                message = read_message(source)
            except EOFError:
                return
            except (ValueError, RecursionError):
                write_message(sink, rpc_error(None, -32700, "Could not parse JSON message."))
                continue
            response = self.process(message)
            if response is not None:
                write_message(sink, response)
