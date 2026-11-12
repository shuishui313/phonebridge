import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from phonebridge.approvals import ApprovalEndpoint, approval_request
from phonebridge.core import PhoneBridge
from phonebridge.mcp import write_message


class ApprovalSocketTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.path = str(Path(self.t.name) / "approval.sock")
        self.b = PhoneBridge()
        self.endpoint = ApprovalEndpoint(self.b, self.path).start()
        self.b.open_app("messaging")
        self.b.open_chat("contact-01")
        self.b.tap(.5, .9)
        self.b.type_text("hello")
        self.op = self.b.prepare_message("contact-01", "hello")["approval_id"]

    def tearDown(self):
        self.endpoint.close()
        self.t.cleanup()

    def test_permissions(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_list_preview(self):
        p = approval_request(self.path, "list")
        self.assertEqual(p["proposals"][0]["message"], "hello")
        self.assertEqual(p["proposals"][0]["recipient_id"], "contact-01")

    def test_approve_and_execute(self):
        self.assertTrue(approval_request(self.path, "approve", self.op)["approved"])
        self.assertTrue(self.b.execute(self.op)["virtual_message_added"])

    def test_deny(self):
        self.assertTrue(approval_request(self.path, "deny", self.op)["denied"])
        self.assertFalse(self.b.execute(self.op)["ok"])

    def test_repeated_approval_rejected(self):
        approval_request(self.path, "approve", self.op)
        self.assertFalse(approval_request(self.path, "approve", self.op)["ok"])

    def test_concurrent_execution_at_most_once(self):
        approval_request(self.path, "approve", self.op)
        results = []
        def run(): results.append(self.b.execute(self.op))
        ts = [threading.Thread(target=run) for _ in range(8)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(sum(bool(x["ok"]) for x in results), 1)
        self.assertEqual(len(self.b.inspect()["messages"]), 1)

    def test_endpoint_cleanup(self):
        self.endpoint.close()
        self.assertFalse(Path(self.path).exists())

    def test_existing_socket_path_refused(self):
        with self.assertRaises(FileExistsError):
            ApprovalEndpoint(self.b, self.path).start()

    def test_unknown_action(self):
        self.assertFalse(approval_request(self.path, "shutdown")["ok"])

    def test_operation_id_starting_with_dash_reaches_the_approval_socket(self):
        result = subprocess.run(
            [sys.executable, "-m", "phonebridge.cli", "--approval-socket", self.path,
             "approvals", "approve", "-dashed-operation", "--token-env", "PB_UNUSED"],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)
        self.assertEqual(json.loads(result.stdout)["error"], "unknown_or_processed_approval")

    def test_delayed_request_is_still_approved(self):
        """The server must outwait a client that is slow to write the request."""
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(15)
        try:
            conn.connect(self.path)
            time.sleep(4)
            with conn.makefile("rwb") as stream:
                write_message(stream, {"action": "approve", "approval_id": self.op})
                data = stream.readline()
        finally:
            conn.close()
        response = json.loads(data)
        self.assertTrue(response.get("approved"), response)


if __name__ == "__main__": unittest.main()
