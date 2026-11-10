"""Exercise PhoneBridge over MCP stdio and the separate operator command."""

import json
import queue
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "run_phonebridge.py"
TIMEOUT = 10
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
TOOLS = {
    "phone_inspect",
    "phone_open_app",
    "phone_home",
    "phone_back",
    "wechat_list_contacts",
    "wechat_open_chat",
    "wechat_prepare_message",
    "phone_execute_operation",
}


def command(socket_path, *args):
    return [
        sys.executable,
        str(ENTRYPOINT),
        "--approval-socket",
        str(socket_path),
        *args,
    ]


class MCPProcess:
    def __init__(self, socket_path):
        self.next_id = 0
        self.lines = queue.Queue()
        self.stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                command(socket_path),
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError:
            self.stderr.close()
            raise
        self.reader = threading.Thread(target=self.read_stdout, daemon=True)
        self.reader.start()

    def read_stdout(self):
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def close(self):
        try:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=TIMEOUT)
                raise AssertionError("MCP server did not exit after closing stdin")
        finally:
            self.reader.join(timeout=TIMEOUT)
            self.process.stdout.close()
            self.stderr.close()

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def notify(self, method):
        self.send({"jsonrpc": "2.0", "method": method})

    def request(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        try:
            line = self.lines.get(timeout=TIMEOUT)
        except queue.Empty:
            raise AssertionError(f"No MCP response to {method}") from None
        if line is None:
            self.stderr.seek(0)
            raise AssertionError(
                f"MCP server closed stdout during {method}: {self.stderr.read()}"
            )
        response = json.loads(line)
        if response.get("jsonrpc") != "2.0" or response.get("id") != self.next_id:
            raise AssertionError(f"Unexpected MCP response to {method}: {response!r}")
        return response


class PhoneBridgeMCPTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="pb-")
        self.addCleanup(directory.cleanup)
        self.socket_path = Path(directory.name) / "approval.sock"

    def start_server(self, version=VERSIONS[-1], socket_path=None):
        server = MCPProcess(socket_path or self.socket_path)
        self.addCleanup(server.close)
        response = server.request(
            "initialize",
            {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "phonebridge-integration-test", "version": "1"},
            },
        )
        self.assertNotIn("error", response)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], version)
        self.assertIn("tools", result["capabilities"])
        self.assertTrue(result["serverInfo"]["name"])
        server.notify("notifications/initialized")
        return server

    def call(self, server, name, **arguments):
        return server.request("tools/call", {"name": name, "arguments": arguments})

    def call_result(self, server, name, **arguments):
        response = self.call(server, name, **arguments)
        self.assertNotIn("error", response)
        return response["result"]

    def call_tool(self, server, name, **arguments):
        result = self.call_result(server, name, **arguments)
        self.assertFalse(result.get("isError", False), result)
        data = json.loads(result["content"][0]["text"])
        if "structuredContent" in result:
            self.assertEqual(result["structuredContent"], data)
        return data

    def assert_tool_error(self, code, server, name, **arguments):
        result = self.call_result(server, name, **arguments)
        self.assertIs(result["isError"], True)
        data = result["structuredContent"]
        self.assertEqual(data, json.loads(result["content"][0]["text"]))
        self.assertEqual(data["error"]["code"], code)
        return data

    def operator(self, *args):
        response = subprocess.run(
            command(self.socket_path, "approvals", *args),
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
        self.assertEqual(response.returncode, 0, response.stderr)
        return json.loads(response.stdout)

    def open_chat(self, server, contact="xiaoming"):
        self.call_tool(server, "phone_open_app", app_id="wechat")
        return self.call_tool(server, "wechat_open_chat", contact=contact)

    def test_stdio_negotiates_and_lists_tools(self):
        for version in VERSIONS:
            with self.subTest(version=version):
                server = self.start_server(version, self.socket_path.with_name(f"{version}.sock"))
                response = server.request("tools/list")
                tools = response["result"]["tools"]
                self.assertEqual({tool["name"] for tool in tools}, TOOLS)
                for tool in tools:
                    self.assertTrue(tool["description"])
                    self.assertEqual(tool["inputSchema"]["type"], "object")
                result = self.call_result(server, "phone_inspect")
                data = json.loads(result["content"][0]["text"])
                self.assertEqual(data["backend"], "simulated")
                self.assertEqual(data["screen"], "home")
                if version in ("2025-06-18", "2025-11-25"):
                    self.assertEqual(result["structuredContent"], data)
                else:
                    self.assertNotIn("structuredContent", result)

    def test_invalid_calls_and_no_mcp_approval_tool(self):
        server = self.start_server()
        requests = [
            self.call(server, "phone_approve_operation", operation_id="proposal"),
            self.call(server, "wechat_open_chat"),
            self.call(server, "phone_open_app", app_id=True),
            self.call(server, "phone_inspect", unexpected="value"),
        ]
        for response in requests:
            self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(server.request("unsupported/method")["error"]["code"], -32601)
        self.assertEqual(self.call_tool(server, "phone_inspect")["screen"], "home")

    def test_simulated_navigation_and_contact_resolution(self):
        server = self.start_server()
        self.assert_tool_error("WRONG_SCREEN", server, "wechat_list_contacts")
        self.assert_tool_error("APP_NOT_ALLOWED", server, "phone_open_app", app_id="mail")
        contacts_screen = self.call_tool(server, "phone_open_app", app_id="wechat")
        self.assertEqual(contacts_screen["screen"], "contacts")
        contacts = self.call_tool(server, "wechat_list_contacts")["contacts"]
        self.assertIn({"id": "xiaoming", "name": "小明"}, contacts)
        self.assert_tool_error("CONTACT_NOT_FOUND", server, "wechat_open_chat", contact="Nobody")
        self.assert_tool_error("AMBIGUOUS_CONTACT", server, "wechat_open_chat", contact="Alex")
        self.assertEqual(self.call_tool(server, "phone_inspect")["screen"], "contacts")
        chat = self.call_tool(server, "wechat_open_chat", contact="  SARAH CHEN  ")
        self.assertEqual(chat["contact"], {"id": "sarah", "name": "Sarah Chen"})
        self.assertEqual(self.call_tool(server, "phone_back")["screen"], "contacts")
        self.assertEqual(self.call_tool(server, "phone_home")["screen"], "home")

    def test_operator_approves_and_execution_is_once_only(self):
        server = self.start_server()
        self.open_chat(server)
        proposal = self.call_tool(server, "wechat_prepare_message", text="你好，PhoneBridge")
        operation_id = proposal["operation_id"]
        self.assertEqual(proposal["contact"], {"id": "xiaoming", "name": "小明"})
        self.assertEqual(proposal["text"], "你好，PhoneBridge")
        self.assert_tool_error(
            "APPROVAL_REQUIRED", server, "phone_execute_operation", operation_id=operation_id
        )
        self.assertEqual(self.call_tool(server, "phone_inspect")["messages"], [])
        self.assertIn(proposal, self.operator("list")["operations"])
        self.operator("approve", operation_id)
        executed = self.call_tool(server, "phone_execute_operation", operation_id=operation_id)
        self.assertEqual(executed["status"], "executed")
        self.assert_tool_error(
            "OPERATION_ALREADY_EXECUTED",
            server,
            "phone_execute_operation",
            operation_id=operation_id,
        )
        self.assertEqual(
            self.call_tool(server, "phone_inspect")["messages"],
            [{"text": "你好，PhoneBridge", "direction": "outgoing"}],
        )

    def test_approval_does_not_survive_leaving_and_reopening_chat(self):
        server = self.start_server()
        self.open_chat(server)
        proposal = self.call_tool(server, "wechat_prepare_message", text="旧草稿")
        operation_id = proposal["operation_id"]
        self.operator("approve", operation_id)
        self.call_tool(server, "phone_home")
        self.open_chat(server)
        self.assert_tool_error(
            "OPERATION_STALE", server, "phone_execute_operation", operation_id=operation_id
        )
        self.assertEqual(self.call_tool(server, "phone_inspect")["messages"], [])

    def test_operator_denial_blocks_execution(self):
        server = self.start_server()
        self.open_chat(server)
        proposal = self.call_tool(server, "wechat_prepare_message", text="请勿发送")
        operation_id = proposal["operation_id"]
        self.operator("deny", operation_id)
        self.assert_tool_error(
            "OPERATION_DENIED", server, "phone_execute_operation", operation_id=operation_id
        )
        self.assertEqual(self.call_tool(server, "phone_inspect")["messages"], [])

    def test_approval_socket_uses_private_mode(self):
        self.start_server()
        mode = self.socket_path.lstat().st_mode
        self.assertTrue(stat.S_ISSOCK(mode))
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    def test_server_refuses_to_replace_existing_socket(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as existing:
            existing.bind(str(self.socket_path))
            inode = self.socket_path.lstat().st_ino
            response = subprocess.run(
                command(self.socket_path),
                cwd=ROOT,
                input="",
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
            )
            self.assertNotEqual(response.returncode, 0)
            self.assertEqual(self.socket_path.lstat().st_ino, inode)


if __name__ == "__main__":
    unittest.main()
