import ast
import contextlib
import getpass
import io
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path

from phonebridge import remote_readonly
from phonebridge.remote_readonly import RemoteReadOnlyApp, RemoteReadOnlyServer


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "phonebridge" / "remote_readonly.py"
TOKEN = "synthetic-remote-token"
FORBIDDEN_IMPORTS = {
    "phonebridge.approvals",
    "phonebridge.submissions",
    "phonebridge.hosted",
    "phonebridge.xcode_mcp",
    "phonebridge.mcp_v04",
    "phonebridge.api",
    "phonebridge.cli",
    "phonebridge.relay",
    "phonebridge.core",
}


def normalized_imports(source: str, package: str = "phonebridge") -> set[str]:
    """Resolve relative imports so ``from .approvals`` is ``phonebridge.approvals``."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                keep = len(parts) - (node.level - 1)
                base = ".".join(parts[:keep]) if keep > 0 else ""
                if node.module:
                    full = f"{base}.{node.module}" if base else node.module
                    found.add(full)
                else:
                    for alias in node.names:
                        found.add(f"{base}.{alias.name}" if base else alias.name)
            elif node.module:
                found.add(node.module)
    return found


def signature(response) -> tuple:
    body = response.read()
    connection = getattr(response, "_connection", None)
    if connection is not None:
        connection.close()
    return (response.status, response.getheader("Content-Type"),
            response.getheader("Content-Length"), body)


class RemoteReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pb-remote-")
        self.audit = str(Path(self.tmp.name) / "audit.jsonl")
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.stop()
        self.tmp.cleanup()

    def start(self, token=TOKEN, expires_at=None, **kwargs) -> RemoteReadOnlyServer:
        if expires_at is None:
            expires_at = 10**12
        app = RemoteReadOnlyApp(token, expires_at, self.audit, clock=lambda: 1_700_000_000,
                                **kwargs)
        server = RemoteReadOnlyServer(app)
        server.start()
        self.servers.append(server)
        return server

    def request(self, server, payload, *, token=None, origin=None, session=None,
                query="", authorization=None):
        connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        elif token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        if session is not None:
            headers["Mcp-Session-Id"] = session
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        connection.request("POST", "/mcp" + query, body=body, headers=headers)
        response = connection.getresponse()
        response._connection = connection
        return response

    def handshake(self, server, token=TOKEN) -> str:
        opened = self.request(server, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "remote-test", "version": "0"}},
        }, token=token)
        body = opened.read()
        opened._connection.close()
        self.assertEqual(opened.status, 200, body)
        session = opened.getheader("Mcp-Session-Id")
        noted = self.request(server, {
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        }, token=token, session=session)
        noted.read()
        noted._connection.close()
        self.assertEqual(noted.status, 202)
        return session

    def call(self, server, session, name, arguments, request_id=3):
        response = self.request(server, {
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, token=TOKEN, session=session)
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read().decode("utf-8"))
        response._connection.close()
        return payload

    def test_module_does_not_import_write_or_approval_code(self):
        imported = normalized_imports(MODULE.read_text(encoding="utf-8"))
        self.assertTrue(imported.isdisjoint(FORBIDDEN_IMPORTS | {"phonebridge.drivers"}), imported)
        samples = {
            "from .approvals import ApprovalEndpoint": "phonebridge.approvals",
            "from .core import PhoneBridge": "phonebridge.core",
            "from .drivers import MockIPhone": "phonebridge.drivers",
            "from . import drivers": "phonebridge.drivers",
        }
        for source, forbidden in samples.items():
            self.assertIn(forbidden, normalized_imports(source))
        self.assertEqual(remote_readonly.TOOLS, ("phone_status", "intent_run", "phone_inspect"))
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stderr(stderr):
                remote_readonly.main(["--driver", "mock"])
        self.assertEqual(raised.exception.code, 2)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        probe = subprocess.run(
            [sys.executable, "-c",
             "import phonebridge.remote_readonly, sys\n"
             "print('\\n'.join(sorted(name for name in sys.modules "
             "if name == 'phonebridge' or name.startswith('phonebridge.'))))"],
            check=True, capture_output=True, text=True, env=env, cwd=ROOT)
        loaded = set(probe.stdout.split())
        self.assertIn("phonebridge.remote_readonly", loaded)
        self.assertTrue(loaded.isdisjoint({
            "phonebridge.drivers", "phonebridge.core", "phonebridge.approvals",
            "phonebridge.submissions", "phonebridge.mcp_v04",
        }), loaded)

    def test_tools_list_is_exactly_three_read_tools(self):
        server = self.start()
        session = self.handshake(server)
        response = self.request(server, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, token=TOKEN, session=session)
        self.assertEqual(response.status, 200)
        payload = json.loads(response.read().decode("utf-8"))
        response._connection.close()
        names = [item["name"] for item in payload["result"]["tools"]]
        self.assertEqual(names, ["phone_status", "intent_run", "phone_inspect"])

    def test_auth_failures_are_identical(self):
        server = self.start()
        missing = signature(self.request(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                   "params": {}}, token=None))
        wrong = signature(self.request(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                 "params": {}}, token="other-token"))
        expired = self.start(expires_at=1)
        stale = signature(self.request(expired, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                  "params": {}}, token=TOKEN))
        leaked = signature(self.request(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                  "params": {}}, token=TOKEN, query=f"?token={TOKEN}"))
        self.assertEqual(missing, wrong)
        self.assertEqual(missing, stale)
        self.assertEqual(missing, leaked)
        self.assertEqual(missing[0], 401)
        self.assertEqual(missing[3], b"unauthorized\n")

    def test_invalid_origin_is_rejected(self):
        server = self.start()
        response = self.request(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {}}, token=TOKEN, origin="https://evil.example")
        self.assertEqual(response.status, 403)
        self.assertEqual(response.getheader("Connection"), "close")
        self.assertEqual(response.read(), b"origin_rejected\n")
        response._connection.close()

    def test_intent_whitelist_rejects_write_and_unknown(self):
        server = self.start()
        session = self.handshake(server)
        write = self.call(server, session, "intent_run", {"capability": "message.send"}, 4)
        unknown = self.call(server, session, "intent_run", {"capability": "device.status"}, 5)
        self.assertEqual(write["result"]["structuredContent"]["error"], "intent_not_allowed")
        self.assertEqual(unknown["result"]["structuredContent"]["error"], "intent_not_allowed")
        self.assertTrue(write["result"]["isError"])
        self.assertTrue(unknown["result"]["isError"])

    def test_oversized_body_and_rate_limit_are_rejected(self):
        tiny = self.start(max_body=32)
        response = self.request(tiny, b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                                token=TOKEN)
        self.assertEqual(response.status, 413)
        self.assertEqual(response.read(), b"payload_too_large\n")
        response._connection.close()
        limited = self.start(rate_limit=1)
        first = self.request(limited, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "remote-test", "version": "0"}},
        }, token=TOKEN)
        self.assertEqual(first.status, 200)
        first.read()
        first._connection.close()
        second = self.request(limited, {
            "jsonrpc": "2.0", "id": 2, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "remote-test", "version": "0"}},
        }, token=TOKEN)
        self.assertEqual(second.status, 429)
        self.assertEqual(second.read(), b"rate_limited\n")
        second._connection.close()

    def test_successful_reads_are_synthetic_and_carry_no_local_identifiers(self):
        server = self.start()
        server.app.contacts = [
            {"id": "contact-01", "name": "Test Contact", "secret": "do-not-serialize"},
        ]
        session = self.handshake(server)
        status = self.call(server, session, "phone_status", {}, 6)
        contacts = self.call(server, session, "intent_run", {"capability": "contacts.lookup"}, 7)
        inspect = self.call(server, session, "phone_inspect", {}, 8)
        status_body = status["result"]["structuredContent"]
        contact_body = contacts["result"]["structuredContent"]
        inspect_body = inspect["result"]["structuredContent"]
        self.assertEqual(status_body["driver"], "mock")
        self.assertEqual(contact_body["result"]["contacts"],
                         [{"id": "contact-01", "name": "Test Contact"}])
        self.assertEqual(inspect_body["messages"], [])
        rendered = json.dumps([status, contacts, inspect])
        banned = [getpass.getuser(), socket.gethostname(), os.getcwd(),
                  os.path.expanduser("~"), self.audit, "mcp.sock", "do-not-serialize", TOKEN]
        for item in banned:
            if item:
                self.assertNotIn(item, rendered)
        text = Path(self.audit).read_text(encoding="utf-8")
        self.assertIn("phone_status", text)
        self.assertIn("intent_run", text)
        self.assertNotIn(TOKEN, text)
        self.assertEqual(stat.S_IMODE(os.stat(self.audit).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(self.audit).parent.stat().st_mode) & 0o077, 0)

    def test_unauthenticated_requests_are_rate_limited(self):
        server = self.start(rate_limit=2)
        statuses = []
        for _ in range(8):
            response = self.request(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                              "params": {}})
            statuses.append(response.status)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
            response._connection.close()
        self.assertEqual(statuses[:2], [401, 401])
        self.assertEqual(statuses[2:], [429] * 6)

    def test_oversized_response_does_not_poison_the_connection(self):
        server = self.start(max_body=32)
        connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
        connection.request("POST", "/mcp", body=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
                           headers={"Content-Type": "application/json",
                                    "Authorization": f"Bearer {TOKEN}"})
        first = connection.getresponse()
        self.assertEqual(first.status, 413)
        self.assertEqual(first.read(), b"payload_too_large\n")
        self.assertEqual(first.getheader("Connection"), "close")
        try:
            connection.request("POST", "/mcp", body=b"{}",
                               headers={"Content-Type": "application/json",
                                        "Authorization": f"Bearer {TOKEN}"})
            second = connection.getresponse()
            body = second.read()
            self.assertNotIn(b"<html", body.lower())
        except (ConnectionError, OSError):
            return
        finally:
            connection.close()

    def test_nonfinite_ttl_is_rejected_and_port_line_is_flushed(self):
        with self.assertRaises(ValueError):
            RemoteReadOnlyApp(TOKEN, float("nan"), self.audit)
        with self.assertRaises(ValueError):
            RemoteReadOnlyApp(TOKEN, float("inf"), self.audit)
        env_name = "PB_REMOTE_REVIEW_TOKEN"
        os.environ[env_name] = TOKEN
        try:
            code = remote_readonly.main([
                "--port", "0", "--token-env", env_name,
                "--token-ttl-seconds", "nan", "--audit-path", self.audit,
            ])
        finally:
            os.environ.pop(env_name, None)
        self.assertEqual(code, 2)
        self.assertNotIn(env_name, os.environ)
        self.assertEqual(remote_readonly.take_token(env_name), "")
        os.environ[env_name] = TOKEN
        self.assertEqual(remote_readonly.take_token(env_name), TOKEN)
        self.assertNotIn(env_name, os.environ)
        os.environ[env_name] = TOKEN
        process = subprocess.Popen(
            [sys.executable, "-m", "phonebridge.remote_readonly",
             "--port", "0", "--token-env", env_name,
             "--token-ttl-seconds", "30", "--audit-path", self.audit],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            line = process.stdout.readline()
            self.assertIsNone(process.poll())
            self.assertIn("port", line)
        finally:
            process.send_signal(signal.SIGINT)
            process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            os.environ.pop(env_name, None)

    def test_server_header_hides_runtime_and_unused_methods_are_closed(self):
        server = self.start()
        session = self.handshake(server)
        response = self.request(server, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        }, token=TOKEN, session=session)
        server_header = response.getheader("Server")
        response.read()
        response._connection.close()
        self.assertNotIn("Python", server_header)
        for method in ("DELETE", "OPTIONS"):
            connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
            connection.request(method, "/mcp")
            reply = connection.getresponse()
            body = reply.read()
            connection.close()
            self.assertEqual(reply.status, 405)
            self.assertEqual(body, b"method_not_allowed\n")
            self.assertEqual(reply.getheader("Connection"), "close")

    def test_default_host_rejects_a_tunnel_name(self):
        server = self.start()
        connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
        connection.request("POST", "/mcp", body=b"{}",
                           headers={"Content-Type": "application/json",
                                    "Host": "tunnel.example.com",
                                    "Authorization": f"Bearer {TOKEN}"})
        response = connection.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        connection.close()
        allowed = self.start(allowed_hosts=frozenset({"tunnel.example.com"}))
        connection = HTTPConnection("127.0.0.1", allowed.port, timeout=5)
        connection.request("POST", "/mcp", body=b"{}",
                           headers={"Content-Type": "application/json",
                                    "Host": "tunnel.example.com"})
        response = connection.getresponse()
        self.assertEqual(response.status, 401)
        self.assertEqual(response.read(), b"unauthorized\n")
        connection.close()


if __name__ == "__main__":
    unittest.main()
