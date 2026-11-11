import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from phonebridge.submissions import SubmissionLedger


ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-m", "phonebridge.cli"]
TIMEOUT = 10


class SubprocessTests(unittest.TestCase):
    def start_server(self, *args):
        stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self.addCleanup(stderr.close)
        process = subprocess.Popen(
            [*CLI, *args, "serve"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        responses = queue.Queue()

        def read_stdout():
            for line in process.stdout:
                responses.put(line)
            responses.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()

        def close():
            try:
                process.stdin.close()
                try:
                    process.wait(timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=TIMEOUT)
                    self.fail("MCP server did not exit after stdin closed")
            finally:
                reader.join(timeout=TIMEOUT)
                process.stdout.close()

        self.addCleanup(close)
        request_id = 0

        def send(method, *, params=None, notification=False):
            nonlocal request_id
            request = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                request["params"] = params
            if not notification:
                request_id += 1
                request["id"] = request_id
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                if notification:
                    return None
                line = responses.get(timeout=TIMEOUT)
            except queue.Empty:
                self.fail(f"No MCP response to {method}")
            except BrokenPipeError:
                line = None
            if line is None:
                stderr.seek(0)
                self.fail(f"MCP server closed stdout during {method}: {stderr.read()}")
            response = json.loads(line)
            self.assertEqual(response["jsonrpc"], "2.0")
            self.assertEqual(response["id"], request_id)
            return response["result"]

        result = send("initialize", params={
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "phonebridge-v04-test", "version": "1"},
        })
        self.assertEqual(result["serverInfo"]["name"], "phonebridge")
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        send("notifications/initialized", notification=True)
        names = {tool["name"] for tool in send("tools/list")["tools"]}
        self.assertEqual(len(names), 14)
        self.assertIn("message_prepare", names)
        self.assertNotIn("wechat_prepare_message", names)
        self.assertNotIn("approval_approve", names)

        def tool(name, **arguments):
            return send("tools/call", params={"name": name, "arguments": arguments})["structuredContent"]

        return tool

    def prepare_message(self, tool):
        self.assertTrue(tool("phone_open_app", app="messaging")["ok"])
        # Historical tool names remain callable over a real stdio process.
        self.assertEqual(len(tool("wechat_list_contacts")["contacts"]), 4)
        self.assertEqual(tool("wechat_open_chat", contact="小明")["contact"]["id"], "contact-02")
        self.assertTrue(tool("phone_back")["ok"])
        self.assertEqual(tool("contacts_open", contact="Casey")["contact"]["id"], "contact-02")
        for result in (
            tool("phone_tap", x=.5, y=.9),
            tool("phone_type_text", text="今天测试成功"),
        ):
            self.assertTrue(result["ok"], result)
        proposal = tool("message_prepare", recipient_id="contact-02", message="今天测试成功")
        self.assertTrue(proposal["ok"], proposal)
        return proposal["approval_id"]

    def test_jsonrpc_stdio_process_and_out_of_band_approval(self):
        directory = tempfile.TemporaryDirectory(prefix="pb-v04-")
        self.addCleanup(directory.cleanup)
        socket_path = Path(directory.name) / "approval.sock"
        socket_args = ["--approval-socket", str(socket_path)]
        tool = self.start_server(*socket_args)
        operation = self.prepare_message(tool)
        self.assertEqual(
            tool("phone_execute_operation", approval_id=operation)["error"],
            "independent_approval_required",
        )
        approval = subprocess.run(
            [*CLI, *socket_args, "approvals", "approve", operation],
            cwd=ROOT,
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=TIMEOUT,
        )
        self.assertTrue(json.loads(approval.stdout)["approved"])
        sent = tool("phone_execute_operation", approval_id=operation)
        self.assertTrue(sent["virtual_message_added"])
        self.assertFalse(sent["real_message_sent"])
        self.assertFalse(tool("phone_execute_operation", approval_id=operation)["ok"])
        self.assertEqual([msg["text"] for msg in tool("phone_inspect")["messages"]], ["今天测试成功"])

    def test_jsonrpc_stdio_without_approval_channel_fails_closed(self):
        tool = self.start_server()
        operation = self.prepare_message(tool)
        self.assertEqual(
            tool("phone_execute_operation", approval_id=operation)["error"],
            "independent_approval_required",
        )
        self.assertEqual(tool("phone_inspect")["messages"], [])

    def test_repeated_process_restart_preserves_pause_until_trusted_reconciliation(self):
        with tempfile.TemporaryDirectory(prefix="pb-v04-recovery-") as directory:
            ledger_path = str(Path(directory) / "submissions.jsonl")
            socket_path = str(Path(directory) / "approval.sock")
            ledger = SubmissionLedger(ledger_path)
            for operation in ("pending-1", "pending-2"):
                ledger.append("approved", op_id=operation, recipient_id="contact-02")
            ledger.append("dispatching", op_id="pending-1", recipient_id="contact-02",
                          message_sha256="0" * 64, baseline_count=0)
            args = ["--submission-state", ledger_path, "--approval-socket", socket_path]
            self.start_server(*args)
            self.doCleanups()
            try:
                tool = self.start_server(*args)
                self.assertEqual(tool("phone_status")["task"]["pending_submissions"], 2)
                self.assertTrue(tool("phone_inspect")["ok"])
                self.assertEqual(tool("phone_home")["error"], "paused_pending_readback")
                for operation, resumed in (("pending-1", False), ("pending-2", True)):
                    reply = subprocess.run(
                        [*CLI, "--approval-socket", socket_path, "approvals", "reconcile", operation],
                        cwd=ROOT, capture_output=True, text=True, timeout=TIMEOUT,
                    )
                    self.assertEqual(reply.returncode, 0, reply.stderr)
                    result = json.loads(reply.stdout)
                    self.assertEqual(result["resumed"], resumed)
                    self.assertFalse(result["resent"])
                self.assertTrue(tool("phone_home")["ok"])
            finally:
                self.doCleanups()


if __name__ == "__main__":
    unittest.main()
