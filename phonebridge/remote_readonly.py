"""Loopback Streamable HTTP MCP for a synthetic read surface.

This process is separate from the stdio server. It does not import the
approval, submission, or device-driver modules. Contacts below are the
synthetic fixture values, copied as id and name only. The listener accepts
127.0.0.1 only. Extra Host names are off unless the process is started
with them after a separate review.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import secrets
import stat
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


TOOLS = ("phone_status", "intent_run", "phone_inspect")
ALLOWED_INTENT = "contacts.lookup"
CONTACT_FIELDS = ("id", "name")
SYNTHETIC_CONTACTS = (
    {"id": "contact-01", "name": "Test Contact"},
    {"id": "contact-02", "name": "Casey"},
    {"id": "alex-01", "name": "Alex"},
    {"id": "alex-02", "name": "Alex"},
)
QUERY_TOKEN_KEYS = frozenset({"token", "access_token", "auth", "authorization"})
AUTH_BODY = b"unauthorized\n"
ORIGIN_BODY = b"origin_rejected\n"
SIZE_BODY = b"payload_too_large\n"
RATE_BODY = b"rate_limited\n"
PROTOCOL = "2025-06-18"


def _project_contacts(contacts) -> list[dict[str, str]]:
    projected = []
    for item in contacts:
        projected.append({field: str(item[field]) for field in CONTACT_FIELDS})
    return projected


def take_token(env_name: str) -> str:
    """Read the bearer secret once and drop it from the process environment."""
    return os.environ.pop(env_name, "")


def prepare_audit(path: str) -> None:
    """Create a 0600 audit file inside an already private directory."""
    target = Path(path)
    parent = target.parent
    if not parent.is_dir():
        raise ValueError("audit directory is missing")
    parent_mode = stat.S_IMODE(parent.stat().st_mode)
    if parent_mode & 0o077:
        raise ValueError("audit directory must be private")
    fd = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.chmod(fd, 0o600)
    finally:
        os.close(fd)


class ReadOnlySession:
    def __init__(self) -> None:
        self.initialized = False
        self.version = PROTOCOL


class RemoteReadOnlyApp:
    """In-memory Mock reader, token MAC, and audit log."""

    def __init__(self, token: str, expires_at: float, audit_path: str, *,
                 max_body: int = 8192, rate_limit: int = 30,
                 rate_window_seconds: float = 60.0, clock=None,
                 allowed_hosts: frozenset[str] | None = None):
        if not isinstance(token, str) or not token or any(ch in token for ch in "\r\n\x00"):
            raise ValueError("remote token must be a single non-empty line")
        if not math.isfinite(float(expires_at)):
            raise ValueError("expiry must be finite")
        if max_body < 1 or rate_limit < 1 or not math.isfinite(rate_window_seconds) or rate_window_seconds <= 0:
            raise ValueError("limits must be positive")
        prepare_audit(audit_path)
        self._salt = secrets.token_bytes(32)
        material = token.encode("utf-8")
        self._mac = hmac.new(self._salt, material, hashlib.sha256).digest()
        self._dummy = hmac.new(self._salt, b"", hashlib.sha256).digest()
        del material
        self.expires_at = float(expires_at)
        self.audit_path = audit_path
        self.max_body = int(max_body)
        self.rate_limit = int(rate_limit)
        self.rate_window_seconds = float(rate_window_seconds)
        self.clock = clock or time.time
        self.contacts = [dict(item) for item in SYNTHETIC_CONTACTS]
        self.allowed_hosts = frozenset(allowed_hosts or ())
        self._sessions: dict[str, ReadOnlySession] = {}
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()
        self._port = 0

    def set_port(self, port: int) -> None:
        self._port = int(port)

    def audit(self, tool: str, result: str) -> None:
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock())),
                  "tool": tool, "result": result}
        line = json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        with self._lock:
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def auth_failure(self, presented: str | None) -> str:
        """Return an audit reason. The HTTP response stays identical."""
        if presented is None:
            hmac.compare_digest(self._mac, self._dummy)
            return "missing_token"
        candidate = hmac.new(self._salt, presented.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(self._mac, candidate):
            return "bad_token"
        if self.clock() >= self.expires_at:
            return "expired_token"
        return ""

    def allow_request(self) -> bool:
        now = self.clock()
        with self._lock:
            while self._hits and now - self._hits[0] >= self.rate_window_seconds:
                self._hits.popleft()
            if len(self._hits) >= self.rate_limit:
                return False
            self._hits.append(now)
            return True

    def host_allowed(self, host: str) -> bool:
        loopback = {f"127.0.0.1:{self._port}", f"localhost:{self._port}"}
        return host in loopback or host in self.allowed_hosts

    def origin_allowed(self, origin: str | None) -> bool:
        if origin is None:
            return True
        allowed = {f"http://127.0.0.1:{self._port}", f"http://localhost:{self._port}"}
        return origin in allowed

    def open_session(self) -> str:
        session_id = secrets.token_hex(16)
        with self._lock:
            self._sessions[session_id] = ReadOnlySession()
        return session_id

    def session(self, session_id: str | None) -> ReadOnlySession | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def mark_initialized(self, session_id: str) -> bool:
        with self._lock:
            found = self._sessions.get(session_id)
            if found is None:
                return False
            found.initialized = True
            return True

    def phone_status(self) -> dict:
        return {"ok": True, "driver": "mock", "simulated": True,
                "real_device_connected": False}

    def phone_inspect(self) -> dict:
        return {"ok": True, "driver": "mock", "simulated": True, "messages": []}

    def intent_run(self, arguments: dict) -> dict:
        capability = arguments.get("capability")
        if set(arguments) != {"capability"} or not isinstance(capability, str):
            return {"ok": False, "error": "invalid_parameters"}
        if capability != ALLOWED_INTENT:
            return {"ok": False, "error": "intent_not_allowed", "capability": capability}
        return {"ok": True, "capability": ALLOWED_INTENT, "channel": "A",
                "result": {"contacts": _project_contacts(self.contacts)}}

    def call_tool(self, name: str, arguments: dict) -> dict:
        if name == "phone_status" and arguments == {}:
            return self.phone_status()
        if name == "phone_inspect" and arguments == {}:
            return self.phone_inspect()
        if name == "intent_run":
            return self.intent_run(arguments)
        return {"ok": False, "error": "unknown_tool"}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "phonebridge-remote-readonly"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def version_string(self) -> str:
        return self.server_version

    def date_time_string(self, timestamp: float | None = None) -> str:
        return "Thu, 01 Jan 2026 00:00:00 GMT"

    def _send(self, status: int, body: bytes, content_type: str,
              session_id: str | None = None) -> None:
        if status >= 400:
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status >= 400:
            self.send_header("Connection", "close")
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        self.wfile.write(body)

    def _content_length(self, app: RemoteReadOnlyApp) -> int | None:
        length = self.headers.get("Content-Length")
        if length is None:
            return 0
        try:
            size = int(length)
        except ValueError:
            return None
        if size < 0:
            return None
        return size

    def _reject_method(self) -> None:
        self.close_connection = True
        self._send(405, b"method_not_allowed\n", "text/plain; charset=utf-8")

    def _unauthorized(self, reason: str) -> None:
        self.server.app.audit("auth", reason)  # type: ignore[attr-defined]
        self._send(401, AUTH_BODY, "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        app: RemoteReadOnlyApp = self.server.app  # type: ignore[attr-defined]
        parts = urlsplit(self.path)
        if parts.path != "/mcp":
            self._send(404, b"not_found\n", "text/plain; charset=utf-8")
            return
        size = self._content_length(app)
        if size is None or size > app.max_body:
            self._send(413, SIZE_BODY, "text/plain; charset=utf-8")
            return
        raw = self.rfile.read(size) if size else b""
        query = parse_qs(parts.query, keep_blank_values=True)
        if any(key.lower() in QUERY_TOKEN_KEYS for key in query):
            self._unauthorized("token_in_query")
            return
        origin = self.headers.get("Origin")
        if not app.origin_allowed(origin):
            self._send(403, ORIGIN_BODY, "text/plain; charset=utf-8")
            return
        host = self.headers.get("Host", "")
        if not app.host_allowed(host):
            self._send(403, ORIGIN_BODY, "text/plain; charset=utf-8")
            return
        if not app.allow_request():
            app.audit("rate_limit", "rejected")
            self._send(429, RATE_BODY, "text/plain; charset=utf-8")
            return
        header = self.headers.get("Authorization")
        presented = None
        if isinstance(header, str) and header.startswith("Bearer ") and len(header.split(" ")) == 2:
            presented = header.split(" ", 1)[1]
        reason = app.auth_failure(presented)
        if reason:
            self._unauthorized(reason)
            return
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send(400, b"bad_request\n", "text/plain; charset=utf-8")
            return
        self._dispatch(app, message)

    def _dispatch(self, app: RemoteReadOnlyApp, message: Any) -> None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._send(400, b"bad_request\n", "text/plain; charset=utf-8")
            return
        method = message.get("method")
        params = message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            self._send(400, b"bad_request\n", "text/plain; charset=utf-8")
            return
        session_header = self.headers.get("Mcp-Session-Id")
        if method == "initialize":
            session_id = app.open_session()
            body = {"jsonrpc": "2.0", "id": message.get("id"), "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "phonebridge-remote-readonly", "version": "0.4.0"},
            }}
            app.audit("initialize", "ok")
            self._send(200, _json_bytes(body), "application/json", session_id)
            return
        session = app.session(session_header)
        if session is None:
            self._send(400, b"bad_request\n", "text/plain; charset=utf-8")
            return
        if method == "notifications/initialized":
            app.mark_initialized(session_header or "")
            app.audit("initialized", "ok")
            self._send(202, b"", "text/plain; charset=utf-8", session_header)
            return
        if not session.initialized:
            self._send(400, b"bad_request\n", "text/plain; charset=utf-8")
            return
        request_id = message.get("id")
        if method == "tools/list":
            result = {"tools": [_schema(name) for name in TOOLS]}
            app.audit("tools/list", "ok")
            self._send(200, _json_bytes(_result(request_id, result)), "application/json", session_header)
            return
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                self._send(400, b"bad_request\n", "text/plain; charset=utf-8")
                return
            payload = app.call_tool(name, arguments)
            app.audit(name, "ok" if payload.get("ok") else str(payload.get("error")))
            encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            result = {"content": [{"type": "text", "text": encoded}],
                      "structuredContent": payload,
                      "isError": payload.get("ok") is not True}
            self._send(200, _json_bytes(_result(request_id, result)), "application/json", session_header)
            return
        self._send(400, b"bad_request\n", "text/plain; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        self._reject_method()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_method()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._reject_method()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_method()


def _schema(name: str) -> dict:
    if name == "intent_run":
        properties = {"capability": {"type": "string"}}
        required = ["capability"]
    else:
        properties = {}
        required = []
    return {"name": name,
            "description": "Read-only synthetic Mock surface.",
            "inputSchema": {"type": "object", "properties": properties,
                             "required": required, "additionalProperties": False}}


def _result(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True).encode("ascii")


class RemoteReadOnlyServer:
    """127.0.0.1 HTTP server. The public constructor refuses other hosts."""

    def __init__(self, app: RemoteReadOnlyApp, port: int = 0):
        self.app = app
        self._httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), _Handler)
        self._httpd.app = app  # type: ignore[attr-defined]
        host, bound = self._httpd.server_address[:2]
        if host != "127.0.0.1":
            self._httpd.server_close()
            raise RuntimeError("remote readonly MCP refuses a non-loopback bind")
        app.set_port(int(bound))
        self.port = int(bound)
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phonebridge-remote-readonly")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token-env", required=True)
    parser.add_argument("--token-ttl-seconds", type=float, required=True)
    parser.add_argument("--audit-path", required=True)
    parser.add_argument("--max-body", type=int, default=8192)
    parser.add_argument("--rate-limit", type=int, default=30)
    parser.add_argument("--rate-window-seconds", type=float, default=60.0)
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="Extra Host header allowed only after a separate tunnel review")
    args = parser.parse_args(argv)
    token = take_token(args.token_env)
    if (not math.isfinite(args.token_ttl_seconds) or args.token_ttl_seconds <= 0
            or not math.isfinite(args.rate_window_seconds) or args.rate_window_seconds <= 0):
        print("remote readonly MCP refused to start", file=sys.stderr)
        return 2
    try:
        app = RemoteReadOnlyApp(
            token, time.time() + args.token_ttl_seconds, args.audit_path,
            max_body=args.max_body, rate_limit=args.rate_limit,
            rate_window_seconds=args.rate_window_seconds,
            allowed_hosts=frozenset(args.allowed_host))
    except ValueError:
        print("remote readonly MCP refused to start", file=sys.stderr)
        return 2
    server = RemoteReadOnlyServer(app, args.port)
    print(json.dumps({"port": server.port}), flush=True)
    try:
        server._httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
