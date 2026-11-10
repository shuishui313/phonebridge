import json
from collections.abc import Callable

from .model import BridgeError, PhoneBridge


VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_LINE = 65536


def read_message(stream):
    line = stream.readline(MAX_LINE + 1)
    if not line:
        raise EOFError
    if len(line) > MAX_LINE:
        while not line.endswith(b"\n"):
            line = stream.readline(MAX_LINE + 1)
            if not line:
                break
        raise ValueError("Message exceeds the 64 KiB limit.")
    return json.loads(line.decode("utf-8"), parse_constant=reject_constant)


def reject_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def write_message(stream, value):
    stream.write(json.dumps(value, allow_nan=False).encode("ascii") + b"\n")
    stream.flush()


def error_payload(error):
    return {"error": {"code": error.code, "message": error.message, **error.details}}


class RpcError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class McpSession:
    def __init__(self, phone: PhoneBridge):
        self.version = None
        self.initialized = False
        self.tools = {}
        self.add_tool("phone_inspect", "Read the current virtual phone screen.", phone.inspect)
        self.add_tool(
            "phone_open_app", "Open WeChat or Settings on the virtual phone.",
            phone.open_app, app_id="wechat or settings",
        )
        self.add_tool("phone_home", "Return the virtual phone to its home screen.", phone.home)
        self.add_tool("phone_back", "Go back one screen on the virtual phone.", phone.back)
        self.add_tool(
            "wechat_list_contacts", "List fixture contacts while virtual WeChat is open.",
            phone.list_contacts,
        )
        self.add_tool(
            "wechat_open_chat", "Open one virtual chat by exact name or stable contact ID.",
            phone.open_chat, contact="Exact display name or stable fixture ID",
        )
        self.add_tool(
            "wechat_prepare_message", "Stage a virtual message for local operator review.",
            phone.prepare_message, text="Message to propose for the active chat",
        )
        self.add_tool(
            "phone_execute_operation", "Execute a reviewed operation once on the virtual phone.",
            phone.execute_operation, operation_id="ID returned by the proposal",
        )

    def add_tool(self, name: str, description: str, handler: Callable, **fields: str):
        schema = {
            "type": "object",
            "properties": {
                key: {"type": "string", "description": value} for key, value in fields.items()
            },
            "required": list(fields),
            "additionalProperties": False,
        }
        self.tools[name] = ({"name": name, "description": description, "inputSchema": schema}, handler)

    def handle(self, message):
        request_id = None
        try:
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise RpcError(-32600, "Invalid request.")
            if not isinstance(message.get("method"), str):
                raise RpcError(-32600, "Expected a method name.")
            method = message["method"]
            if "id" not in message:
                if method == "notifications/initialized" and self.version:
                    self.initialized = True
                return None
            if type(message["id"]) not in (int, str):
                raise RpcError(-32600, "Expected a string or integer request ID.")
            request_id = message["id"]
            params = message.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "Expected an object for params.")
            return {"jsonrpc": "2.0", "id": request_id, "result": self.dispatch(method, params)}
        except RpcError as error:
            return rpc_error(request_id, error.code, error.message)

    def dispatch(self, method, params):
        if method == "ping":
            return {}
        if method == "initialize":
            if self.version:
                raise RpcError(-32600, "This session is already initialized.")
            version = params.get("protocolVersion")
            client = params.get("clientInfo")
            if (
                not isinstance(version, str)
                or not isinstance(client, dict)
                or not isinstance(client.get("name"), str)
                or not isinstance(client.get("version"), str)
                or not isinstance(params.get("capabilities"), dict)
            ):
                raise RpcError(-32602, "Expected protocolVersion, clientInfo and capabilities.")
            self.version = version if version in VERSIONS else VERSIONS[0]
            return {
                "protocolVersion": self.version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "phonebridge", "version": "0.3.0"},
                "instructions": "All operations affect a virtual iPhone with fixture data only.",
            }
        if method not in ("tools/list", "tools/call"):
            raise RpcError(-32601, "Method not found.")
        if not self.initialized:
            raise RpcError(-32002, "Complete initialization before using tools.")
        if method == "tools/list":
            if params.get("cursor") is not None:
                raise RpcError(-32602, "This server has no additional tool pages.")
            return {"tools": [spec for spec, _ in self.tools.values()]}
        return self.call_tool(params)

    def call_tool(self, params):
        name = params.get("name")
        if not isinstance(name, str) or name not in self.tools:
            raise RpcError(-32602, "Unknown tool.")
        spec, handler = self.tools[name]
        arguments = params.get("arguments", {})
        expected = spec["inputSchema"]["required"]
        if (
            not isinstance(arguments, dict)
            or arguments.keys() != set(expected)
            or any(not isinstance(value, str) for value in arguments.values())
        ):
            raise RpcError(-32602, "Tool arguments must match the declared input schema.")
        is_error = False
        try:
            payload = handler(**arguments)
        except BridgeError as error:
            is_error = True
            payload = error_payload(error)
        result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
        if self.version in VERSIONS[:2]:
            result["structuredContent"] = payload
        if is_error:
            result["isError"] = True
        return result


def rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio(phone, source, destination):
    session = McpSession(phone)
    while True:
        try:
            message = read_message(source)
        except EOFError:
            return
        except (ValueError, RecursionError):
            write_message(destination, rpc_error(None, -32700, "Could not parse JSON message."))
            continue
        response = session.handle(message)
        if response is not None:
            write_message(destination, response)
