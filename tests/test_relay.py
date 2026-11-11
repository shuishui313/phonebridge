import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from phonebridge.api import CoreAPI
from phonebridge.hark import (
    build_codex_command,
    codex_env,
    parse_codex_output,
    ssh_wrap,
)
from phonebridge.relay import _RelayHandler, _RelayServer


ROOT = Path(__file__).resolve().parents[1]


class UnixMcpClient:
    def __init__(self, path: Path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.stream = self.sock.makefile("rwb")
        self.next_id = 1

    def request(self, method, params=None, notification=False):
        request_id = None if notification else self.next_id
        if not notification:
            self.next_id += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if request_id is not None:
            message["id"] = request_id
        self.stream.write(json.dumps(message).encode("ascii") + b"\n")
        self.stream.flush()
        if notification:
            return None
        response = json.loads(self.stream.readline().decode("utf-8"))
        if response.get("id") != request_id:
            raise AssertionError(f"unexpected response id: {response}")
        return response

    def initialize(self):
        response = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "relay-test", "version": "0"},
        })
        assert "result" in response, response
        self.request("notifications/initialized", notification=True)

    def call(self, name, arguments=None):
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        result = response["result"]
        return json.loads(result["content"][0]["text"])

    def close(self):
        self.stream.close()
        self.sock.close()


class RelayBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pb-relay-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.socket_path = self.root / "relay.sock"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "phonebridge.cli", "--driver", "mock",
             "backend", "--socket", str(self.socket_path)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self._stop_backend)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("relay backend exited: " + (self.process.stderr.read() or ""))
            if self.socket_path.exists():
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    try:
                        probe.connect(str(self.socket_path))
                    except OSError:
                        pass
                    else:
                        return
            time.sleep(0.02)
        raise AssertionError("relay backend did not start")

    def _stop_backend(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.process.stderr:
            self.process.stderr.read()
            self.process.stderr.close()

    def test_reconnect_preserves_bridge_state(self):
        first = UnixMcpClient(self.socket_path)
        self.addCleanup(first.close)
        first.initialize()
        self.assertTrue(first.call("phone_open_app", {"app": "messaging"})["ok"])

        # A new session gets its own handshake while the previous client waits.
        second = UnixMcpClient(self.socket_path)
        self.addCleanup(second.close)
        second.sock.settimeout(2)
        self.assertEqual(second.request("tools/list")["error"]["code"], -32002)
        second.initialize()
        observed = second.call("phone_inspect")
        self.assertEqual(observed["app"], "messaging")
        first.sock.settimeout(2)
        self.assertEqual(first.call("phone_inspect")["app"], "messaging")

    def test_stdio_proxy_connects_to_backend(self):
        command = build_codex_command("/bin/codex", sys.executable, str(ROOT),
                                      str(self.socket_path), "prompt", str(self.root))
        config = dict(setting.split("=", 1) for option, setting in zip(command, command[1:])
                      if option == "-c")
        executable = json.loads(config["mcp_servers.phonebridge.command"])
        args = json.loads(config["mcp_servers.phonebridge.args"])
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "stdio-relay-test", "version": "0"}}}
        relay = subprocess.run(
            [executable, *args], cwd=ROOT, input=json.dumps(request) + "\n",
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(relay.returncode, 0, relay.stderr)
        response = json.loads(relay.stdout)
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["serverInfo"]["name"], "phonebridge")

    def test_socket_permissions_are_private(self):
        mode = self.socket_path.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class RelayTimeoutTests(unittest.TestCase):
    @patch.object(_RelayHandler, "timeout", 0.05)
    def test_idle_socket_closes_without_an_mcp_parse_error(self):
        with tempfile.TemporaryDirectory(prefix="pb-relay-idle-") as directory:
            path = str(Path(directory) / "relay.sock")
            with _RelayServer(path, CoreAPI()) as server:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                    conn.settimeout(2)
                    conn.connect(path)
                    server.handle_request()
                    self.assertEqual(conn.recv(1), b"")


class HarkHelperTests(unittest.TestCase):
    def test_parse_completed_mcp_tool_call(self):
        stdout = "\n".join([
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
            json.dumps({"type": "item.completed", "item": {
                "type": "mcp_tool_call", "server": "phonebridge", "tool": "phone_status",
                "arguments": {}, "error": None,
                "result": {"content": [{"type": "text", "text": "{\"ok\": true, \"driver\": \"mock\"}"}]},
            }}),
        ])
        parsed = parse_codex_output(stdout)
        self.assertEqual(len(parsed.tool_calls), 1)
        self.assertEqual(parsed.tool_calls[0].tool, "phone_status")
        self.assertEqual(parsed.tool_calls[0].payload, {"ok": True, "driver": "mock"})

    def test_build_codex_command_uses_relay_and_auto_review(self):
        command = build_codex_command("/bin/codex", sys.executable, "/repo", "/tmp/mcp.sock",
                                      "prompt", "/tmp/work", trusted_project="/tmp/work")
        joined = " ".join(command)
        self.assertIn("--approve-for-me", command)
        self.assertIn("--json", command)
        self.assertIn("phonebridge.relay", joined)
        self.assertIn("mcp_servers.phonebridge.enabled=true", command)
        self.assertIn('projects."/tmp/work".trust_level="trusted"', command)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_codex_env_removes_approval_token(self):
        env = codex_env({"KEEP": "1", "PB_HARK_TOKEN": "secret"}, approval_token_env="PB_HARK_TOKEN")
        self.assertEqual(env, {"KEEP": "1"})

    def test_ssh_wrap_quotes_remote_command(self):
        command = ssh_wrap(["/bin/echo", "hello world"], "user@example", port=2222, key_path="/tmp/key")
        self.assertEqual(command[:2], ["ssh", "-T"])
        self.assertIn("user@example", command)
        self.assertIn("'hello world'", command[-1])


if __name__ == "__main__":
    unittest.main()
