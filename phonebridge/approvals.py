"""Operator-only approval Unix socket. It is deliberately not an MCP tool."""
from __future__ import annotations

import hmac
import json
import os
import socket
import socketserver
import stat
import struct
import sys
import threading
from pathlib import Path
from typing import Any

from .core import PhoneBridge
from .drivers import fail
from .mcp import read_message, write_message
from .operator import private_path


MAX_REPLY = 1_000_000


def peer_credential_uid(conn) -> int | None:
    """OS-level identity of the peer, or None when the platform cannot tell."""
    try:
        if hasattr(socket, "SO_PEERCRED"):
            raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            return struct.unpack("3i", raw)[1]
        if sys.platform == "darwin":
            # SOL_LOCAL = 0, LOCAL_PEERCRED = 0x001, struct xucred: version, uid, ...
            raw = conn.getsockopt(0, 0x001, 128)
            if len(raw) >= 8:
                return struct.unpack_from("I", raw, 4)[0]
    except (OSError, ValueError, struct.error, AttributeError):
        return None
    return None


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


class ApprovalEndpoint:
    """Trusted-side approval channel.

    ``required_uid`` and ``token`` are the isolation controls. A deployment
    that intends to submit anything must set at least one; leaving both unset
    is only acceptable for the Mock demo, and is disclosed in status.
    """

    def __init__(self, bridge: PhoneBridge, path: str, *, token: str | None = None,
                 required_uid: int | None = None, peer_uid_fn=None):
        self.bridge = bridge
        self.path = Path(path)
        self.token = token
        self.required_uid = int(required_uid) if required_uid is not None else None
        self.peer_uid_fn = peer_uid_fn or peer_credential_uid
        self._server = None
        self._thread = None
        self._socket_identity = None

    @property
    def isolated(self) -> bool:
        return bool(self.token) or self.required_uid is not None

    def start(self):
        if self.required_uid is not None and self.required_uid == os.getuid():
            raise PermissionError(
                "Approval isolation requires a UID different from the MCP service process.")
        # The service owns its private socket directory; the approver identity
        # is enforced per connection instead of by directory ownership.
        target = private_path(self.path)
        if os.path.lexists(target):
            raise FileExistsError(f"approval socket already exists: {target}")
        bridge = self.bridge
        token, required_uid, peer_uid_fn = self.token, self.required_uid, self.peer_uid_fn
        bridge.approval_isolated = self.isolated

        class Handler(socketserver.StreamRequestHandler):
            timeout = 3

            def handle(self):
                try:
                    if required_uid is not None and peer_uid_fn(self.request) != required_uid:
                        reply = fail("approval_peer_not_trusted")
                    else:
                        request = read_message(self.rfile)
                        if not isinstance(request, dict):
                            raise ValueError("Expected an approval request object.")
                        if token and not hmac.compare_digest(
                                str(request.get("token", "")), token):
                            reply = fail("approval_token_required")
                        else:
                            op, item = request.get("action"), request.get("approval_id")
                            if op == "list":
                                reply = bridge.approval_list()
                            elif op == "approve" and isinstance(item, str):
                                reply = bridge.approve(item)
                            elif op == "deny" and isinstance(item, str):
                                reply = bridge.deny(item)
                            else:
                                reply = fail("unknown_approval_action")
                except (EOFError, OSError, ValueError, RecursionError):
                    reply = fail("invalid_approval_request")
                try:
                    write_message(self.wfile, reply)
                except OSError:
                    pass

        server = _UnixServer(str(target), Handler, bind_and_activate=False)
        try:
            server.server_bind()
            info = target.lstat()
            self._socket_identity = (info.st_dev, info.st_ino)
            target.chmod(0o600)
            server.server_activate()
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            worker.start()
        except BaseException:
            server.server_close()
            self._remove_socket()
            raise
        self._server = server
        self._thread = worker
        return self

    def close(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._remove_socket()

    def _remove_socket(self):
        identity, self._socket_identity = self._socket_identity, None
        if identity is None:
            return
        try:
            info = self.path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == identity:
                self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()


def approval_request(path: str, action: str, approval_id: str | None = None,
                     timeout: float = 5, token: str | None = None,
                     socket_owner_uid: int | None = None) -> dict[str, Any]:
    owner = os.getuid() if socket_owner_uid is None else int(socket_owner_uid)
    path = private_path(Path(path), expected_uid=owner)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Expected a private approval socket owned by the service account.")
    message = {"action": action}
    if approval_id:
        message["approval_id"] = approval_id
    if token:
        message["token"] = token
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(str(path))
        with conn.makefile("rwb") as stream:
            write_message(stream, message)
            data = stream.readline(MAX_REPLY + 1)
    if len(data) > MAX_REPLY:
        raise ValueError("Approval response is too large.")
    response = json.loads(data)
    if not isinstance(response, dict):
        raise ValueError("Invalid approval response.")
    return response
