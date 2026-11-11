import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import re
import socket
import sys

if os.environ.get("PB_HARK_TOKEN"):
    print("approval token leaked into Codex environment", file=sys.stderr)
    raise SystemExit(9)

joined = "\0".join(sys.argv)
match = re.search(r'"--socket",\s*"([^"]+)"', joined)
if not match:
    print("relay socket not found", file=sys.stderr)
    raise SystemExit(2)
socket_path = match.group(1)
prompt = sys.argv[-1]
text_match = re.search(r'with exactly "([^"]+)"', prompt)
TYPED_TEXT = text_match.group(1) if text_match else "Harness probe"

stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
stream.connect(socket_path)
reader = stream.makefile("rwb")
request_id = 0


def send(message):
    reader.write(json.dumps(message).encode("ascii") + b"\n")
    reader.flush()


def request(method, params=None, notification=False):
    global request_id
    request_id += 1
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        message["id"] = request_id
    send(message)
    if notification:
        return None
    while True:
        response = json.loads(reader.readline().decode("utf-8"))
        if response.get("id") == request_id:
            return response


request("initialize", {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "fake-codex", "version": "0"},
})
request("notifications/initialized", notification=True)

event_id = 0


def emit(tool, arguments, payload):
    global event_id
    event_id += 1
    result = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structured_content": payload,
    }
    print(json.dumps({
        "type": "item.completed",
        "item": {"id": f"item_{event_id}", "type": "mcp_tool_call", "server": "phonebridge",
                 "tool": tool, "arguments": arguments, "result": result,
                 "error": None, "status": "completed"},
    }), flush=True)


def call(tool, arguments):
    response = request("tools/call", {"name": tool, "arguments": arguments})
    result = response["result"]
    payload = result.get("structuredContent") or result.get("structured_content")
    if payload is None:
        payload = json.loads(result["content"][0]["text"])
    emit(tool, arguments, payload)
    return payload


def open_casey_and_type():
    call("phone_open_app", {"app": "messaging"})
    call("contacts_open", {"contact": "Casey"})
    call("phone_tap", {"x": 0.5, "y": 0.9})
    call("phone_type_text", {"text": TYPED_TEXT})


if "Stop immediately after message_prepare" in prompt:
    open_casey_and_type()
    prepared = call("message_prepare", {"recipient_id": "contact-02", "message": TYPED_TEXT})
    APPROVAL_ID = prepared.get("approval_id")
elif "No approval exists" in prompt:
    open_casey_and_type()
    prepared = call("message_prepare", {"recipient_id": "contact-02", "message": TYPED_TEXT})
    call("phone_execute_operation", {"approval_id": prepared.get("approval_id")})
elif "phone_inspect" in prompt:
    call("phone_inspect", {})
elif "This is a replay" in prompt:
    approval_id = re.search(r'approval_id="([^"]+)"', prompt).group(1)
    call("phone_execute_operation", {"approval_id": approval_id})
elif "phone_execute_operation" in prompt:
    approval_id = re.search(r'approval_id="([^"]+)"', prompt).group(1)
    call("phone_execute_operation", {"approval_id": approval_id})
else:
    print("unrecognized fake phase", file=sys.stderr)
    raise SystemExit(3)

print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
'''

FAKE_CODEX_INTERRUPT = r'''#!/usr/bin/env python3
import json
import re
import socket
import sys
import time

MARKER = "PB_INTERRUPTION_AFTER_DISPATCH"

joined = "\0".join(sys.argv)
match = re.search(r'"--socket",\s*"([^"]+)"', joined)
if not match:
    print("relay socket not found", file=sys.stderr)
    raise SystemExit(2)
prompt = sys.argv[-1]
approval = re.search(r'approval_id="([^"]+)"', prompt)
if not approval:
    print("approval id missing", file=sys.stderr)
    raise SystemExit(3)

sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(match.group(1))
reader = sock.makefile("rwb")

reader.write((json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "interrupting-codex", "version": "0"}},
}) + "\n").encode("ascii"))
reader.flush()
while True:
    line = reader.readline()
    if not line:
        raise SystemExit(4)
    if json.loads(line.decode("utf-8")).get("id") == 1:
        break
reader.write((json.dumps({
    "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
}) + "\n").encode("ascii"))
reader.flush()
reader.write((json.dumps({
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "phone_execute_operation",
               "arguments": {"approval_id": approval.group(1)}},
}) + "\n").encode("ascii"))
reader.flush()
time.sleep(0.3)
print(MARKER, file=sys.stderr)
sock.close()
raise SystemExit(74)
'''


class HarkSpikeHarnessTests(unittest.TestCase):
    def write_fake_codex(self, root: Path, *, interrupt: bool = False) -> Path:
        fake = root / ("fake-codex-interrupt.py" if interrupt else "fake-codex.py")
        fake.write_text(FAKE_CODEX_INTERRUPT if interrupt else FAKE_CODEX, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return fake

    def assert_host_chain_payload(self, payload: dict):
        self.assertTrue(payload["host_chain_passed"], payload)
        self.assertFalse(payload["hark_client_verified"])
        checks = payload["checks"]
        self.assertTrue(checks["trusted_side_approval"])
        self.assertTrue(checks["approved_execute_completed"])
        self.assertTrue(checks["unapproved_execute_rejected"])
        self.assertTrue(checks["replay_rejected"])
        self.assertTrue(checks["interrupted_execute_dropped_before_receipt"])
        self.assertTrue(checks["interruption_no_duplicate_record"])
        self.assertTrue(checks["interrupted_operation_replay_rejected"])
        self.assertTrue(checks["reconnect_sessions_share_backend"])
        events = payload["ledger_events"]
        self.assertEqual(events[:4], ["prepared", "approved", "dispatching", "completed"])
        self.assertEqual({event: events.count(event) for event in set(events)},
                         {"prepared": 3, "approved": 2, "dispatching": 2, "completed": 2})
        self.assertEqual(checks["multiple_transport_connections_detail"]["connections"], 9)
        self.assertTrue(payload["interruption"]["verified"])
        self.assertTrue(payload["interruption"]["receipt_dropped"])
        classes = payload["evidence_classes"]
        self.assertEqual(set(classes), {"success", "failure", "interruption", "reconnect"})
        self.assertTrue(all(item["verified"] for item in classes.values()), classes)
        self.assertEqual(classes["reconnect"]["session_count"], 9)

    def test_full_host_chain_with_deterministic_codex_client(self):
        with tempfile.TemporaryDirectory(prefix="pb-hark-harness-") as td:
            root = Path(td)
            fake = self.write_fake_codex(root)
            interruption_client = self.write_fake_codex(root, interrupt=True)
            command = [
                sys.executable, str(ROOT / "scripts" / "hark-codex-spike.py"),
                "--codex-path", str(fake),
                "--interruption-codex-path", str(interruption_client),
                "--text", "Harness probe",
                "--phase-timeout", "60",
            ]
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", timeout=120, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-3000:])
            self.assert_host_chain_payload(json.loads(result.stdout))

    def test_full_chain_over_key_only_ssh_loopback(self):
        with tempfile.TemporaryDirectory(prefix="pb-hark-ssh-harness-") as td:
            root = Path(td)
            fake = self.write_fake_codex(root)
            interruption_client = self.write_fake_codex(root, interrupt=True)
            command = [
                sys.executable, str(ROOT / "scripts" / "hark-codex-ssh-loopback-spike.py"),
                "--phase-timeout", "60",
                "--hark-spike-arg=--codex-path", f"--hark-spike-arg={fake}",
                "--hark-spike-arg=--interruption-codex-path",
                f"--hark-spike-arg={interruption_client}",
            ]
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding="utf-8", timeout=180, cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-3000:])
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ssh_loopback_precheck"], payload)
            self.assert_host_chain_payload(payload)


if __name__ == "__main__":
    unittest.main()
