"""Reconnectable MCP transport used by Hark/SSH/Codex integration.

The backend owns the CoreAPI state and listens on a private Unix socket. Each
stdio relay connection gets a fresh MCP protocol session while the PhoneBridge
state survives client disconnects. This is the host-side path used when an
SSH client such as Hark launches Codex repeatedly against the same device
session.
"""
from __future__ import annotations

import argparse
import os
import socket
import socketserver
import sys
import threading
from pathlib import Path

from .mcp import MAX_LINE, read_message, rpc_error, write_message
from .mcp_v04 import MCPServer
from .operator import private_path


class _RelayServer(socketserver.UnixStreamServer):
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, socket_path: str, api):
        self.api = api
        super().__init__(socket_path, _RelayHandler)


class _RelayHandler(socketserver.StreamRequestHandler):
    timeout = 30

    def handle(self):
        # Protocol state resets per connection; device/session state stays in CoreAPI.
        session = MCPServer(self.server.api)
        while True:
            try:
                message = read_message(self.rfile)
            except EOFError:
                return
            except (ValueError, RecursionError, OSError):
                try:
                    write_message(self.wfile, rpc_error(None, -32700, "Could not parse JSON message."))
                except OSError:
                    return
                continue
            response = session.process(message)
            if response is None:
                continue
            try:
                write_message(self.wfile, response)
            except OSError:
                return


def serve_backend(api, socket_path: str) -> None:
    """Serve one CoreAPI on a private Unix socket until interrupted."""
    target = private_path(Path(socket_path))
    if os.path.lexists(target):
        raise FileExistsError(f"relay socket already exists: {target}")
    server = _RelayServer(str(target), api)
    os.chmod(target, 0o600)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            target.unlink()
        except OSError:
            pass


def proxy_stdio(socket_path: str) -> int:
    """Bridge MCP JSON lines between stdio and the reconnectable backend."""
    try:
        bridge = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bridge.connect(str(socket_path))
    except OSError as exc:
        print(f"relay backend unavailable: {exc}", file=sys.stderr)
        return 2

    def forward_stdin():
        try:
            while True:
                line = sys.stdin.buffer.readline(MAX_LINE + 1)
                if not line:
                    break
                bridge.sendall(line)
        except (BrokenPipeError, OSError):
            pass
        try:
            bridge.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    sender = threading.Thread(target=forward_stdin, daemon=True)
    sender.start()
    try:
        reader = bridge.makefile("rb")
        while True:
            line = reader.readline(MAX_LINE + 1)
            if not line:
                break
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        bridge.close()
        sender.join(timeout=2)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=["stdio"], default="stdio")
    parser.add_argument("--socket", required=True, help="Private backend Unix socket path")
    args = parser.parse_args(argv)
    return proxy_stdio(args.socket)


if __name__ == "__main__":
    raise SystemExit(main())
