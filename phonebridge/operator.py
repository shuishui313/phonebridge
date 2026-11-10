import json
import os
import socket
import socketserver
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

from .mcp import error_payload, read_message, write_message
from .model import BridgeError


def private_path(path, expected_uid=None):
    if not path.is_absolute():
        raise ValueError("The approval socket path must be absolute.")
    owner = os.getuid() if expected_uid is None else int(expected_uid)
    parent = path.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != owner
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise ValueError("The approval socket needs a private 0700 directory owned by the approver.")
    return path


class ApprovalHandler(socketserver.StreamRequestHandler):
    timeout = 3

    def handle(self):
        try:
            request = read_message(self.rfile)
            result = self.review(request)
        except BridgeError as error:
            result = error_payload(error)
        except (EOFError, OSError, RecursionError, ValueError):
            result = {"error": {"code": "INVALID_REQUEST", "message": "Invalid approval request."}}
        try:
            write_message(self.wfile, result)
        except OSError:
            pass

    def review(self, request):
        if not isinstance(request, dict):
            raise ValueError("Expected an approval request object.")
        action = request.get("action")
        phone = self.server.phone
        if action == "list":
            return phone.list_pending_operations()
        operation_id = request.get("operation_id")
        if action not in ("approve", "deny") or not isinstance(operation_id, str):
            raise ValueError("Expected a review action and operation ID.")
        return phone.decide_operation(operation_id, approved=action == "approve")


@contextmanager
def approval_server(path, phone):
    path = private_path(path)
    if os.path.lexists(path):
        raise ValueError("The approval socket path already exists. Choose a fresh path.")
    server = socketserver.UnixStreamServer(str(path), ApprovalHandler, bind_and_activate=False)
    with server:
        server.phone = phone
        server.server_bind()
        try:
            path.chmod(0o600)
            server.server_activate()
            worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            worker.start()
            try:
                yield
            finally:
                server.shutdown()
                worker.join()
        finally:
            path.unlink(missing_ok=True)


def review_from_terminal(path: Path, action, operation_id=None):
    path = private_path(path)
    info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Expected an approval socket owned by the current user.")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Expected approval socket mode 0600.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(path))
        with client.makefile("rwb") as stream:
            write_message(stream, {"action": action, "operation_id": operation_id})
            response = read_message(stream)
    failed = "error" in response
    print(json.dumps(response, indent=2), file=sys.stderr if failed else sys.stdout)
    return int(failed)
