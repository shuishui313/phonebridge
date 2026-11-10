#!/usr/bin/env python3
"""Step 0b draft spike: verify the MCP -> official driver -> fixture chain.

Runs against a booted Apple Simulator with the synthetic fixture installed.
The fixture is relaunched with ``pb-disable-submit`` so sending and posting
paths are disabled for this trial. All contacts and text are synthetic.

Usage:
    python3 scripts/xcode-mcp-draft-spike.py --sim-udid <SIMULATOR_UDID>

Requires a full Xcode with MCP access enabled for external agents. The
--allow-xcode-interactions flag is set here on behalf of the human operator;
do not enable it by default in production.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BUNDLE_ID = "org.example.phonebridge.fixture"
HITPOINT = re.compile(r"hitPoint:\s*\{([\d.]+),\s*([\d.]+)\}")
IDENTIFIER = re.compile(r"identifier:\s*'([^']*)'")
LABEL = re.compile(r"label:\s*'([^']*)'")
WINDOW = re.compile(r"Window, \{\{[\d.]+, [\d.]+\}, \{([\d.]+), ([\d.]+)\}\}")
FIELD_VALUE = re.compile(r"(?:value|label):\s*(?:'([^']*)'|([^,]+))")


class MCPClient:
    def __init__(self, command):
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
        )
        self.next_id = 1

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                          "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed: " + self.proc.stderr.read())
            message = json.loads(line)
            if message.get("id") == request_id:
                return message

    def initialize(self):
        hello = self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "phonebridge-draft-spike", "version": "0.4"},
        })
        if "result" not in hello:
            raise RuntimeError(f"initialize failed: {hello}")
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()
        return hello["result"]["serverInfo"]

    def call(self, name, arguments=None):
        response = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return json.loads(response["result"]["content"][0]["text"])

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def element(text: str, identifier: str):
    for line in text.splitlines():
        if "hitPoint:" not in line:
            continue
        match = IDENTIFIER.search(line)
        if match and match.group(1) == identifier:
            point = HITPOINT.search(line)
            if point:
                return float(point.group(1)), float(point.group(2))
    return None


def dimensions(text: str):
    match = WINDOW.search(text)
    return (float(match.group(1)), float(match.group(2))) if match else None


def field_value(text: str, identifier: str) -> str | None:
    for line in text.splitlines():
        if "TextField," not in line:
            continue
        match = IDENTIFIER.search(line)
        if not match or match.group(1) != identifier:
            continue
        value = FIELD_VALUE.search(line)
        if value:
            return (value.group(1) or value.group(2) or "").strip()
    return None


def exact_identifier(text: str, identifier: str) -> bool:
    return re.search(r"identifier:\s*'" + re.escape(identifier) + r"'", text) is not None


def hierarchy_text(result: dict):
    path = result.get("hierarchy_path")
    if not path:
        raise RuntimeError(f"no fresh hierarchy: {result}")
    return Path(path).read_text(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim-udid", required=True, help="Booted Apple Simulator UDID")
    ap.add_argument("--text", default="测试消息", help="Synthetic draft text (ASCII fallback used if needed)")
    ap.add_argument("--bundle-id", default=BUNDLE_ID)
    args = ap.parse_args()

    evidence = {"sim_udid": args.sim_udid, "bundle_id": args.bundle_id,
               "text": args.text, "checks": [], "tool_calls": []}

    # Relaunch the fixture with submission paths disabled.
    subprocess.run(["xcrun", "simctl", "terminate", args.sim_udid, args.bundle_id],
                   capture_output=True, text=True, timeout=15)
    launch = subprocess.run(
        ["xcrun", "simctl", "launch", args.sim_udid, args.bundle_id, "pb-disable-submit"],
        capture_output=True, text=True, timeout=20,
    )
    if launch.returncode != 0:
        print("Fixture launch with disabled submit failed:", launch.stderr.strip(), file=sys.stderr)
        return 1
    evidence["checks"].append("fixture_relaunched_with_submit_disabled")

    command = [sys.executable, "-m", "phonebridge.cli", "--driver", "xcode-mcp",
              "--sim-udid", args.sim_udid, "--allow-xcode-interactions", "serve"]
    client = MCPClient(command)
    try:
        evidence["server"] = client.initialize()
        tools = client.request("tools/list", {})["result"]["tools"]
        evidence["tool_count"] = len(tools)

        def tool(name, arguments=None, *, label=None):
            evidence["tool_calls"].append(label or name)
            return client.call(name, arguments)

        status = tool("phone_status")
        evidence["status"] = {"driver": status.get("driver"),
                               "preflight_passed": status.get("preflight_passed")}

        opened = tool("phone_open_app", {"app": "fixture"}, label="phone_open_app(fixture)")
        if not opened.get("ok"):
            raise RuntimeError(f"fixture activation failed: {opened}")
        evidence["checks"].append("fixture_in_foreground")

        def tap_identifier(identifier):
            text = hierarchy_text(tool("phone_inspect", label=f"phone_inspect before {identifier}"))
            size, point = dimensions(text), element(text, identifier)
            if not size or not point:
                raise RuntimeError(f"cannot locate {identifier} in fresh hierarchy")
            result = tool("phone_tap", {"x": point[0] / size[0], "y": point[1] / size[1]},
                         label=f"phone_tap({identifier})")
            if not result.get("ok"):
                raise RuntimeError(f"tap {identifier} failed: {result}")
            evidence["checks"].append(f"located_and_tapped:{identifier}")
            return text

        tap_identifier("app.whatsapp")
        contacts_text = hierarchy_text(tool("phone_inspect", label="phone_inspect before contact"))
        if "contact.contact-02" not in contacts_text or "Casey" not in contacts_text:
            raise RuntimeError("stable contact ID/name not found in fresh hierarchy")
        evidence["checks"].append("stable_contact_present:contact-02/Casey")
        tap_identifier("contact.contact-02")
        tap_identifier("chat.input")

        draft = tool("phone_type_text", {"text": args.text}, label="phone_type_text")
        if not draft.get("ok"):
            raise RuntimeError(f"type_text failed: {draft}")

        after = hierarchy_text(tool("phone_inspect", label="phone_inspect after type_text"))
        evidence["expected_text"] = args.text
        evidence["input_value"] = field_value(after, "chat.input")
        evidence["draft_visible"] = evidence["input_value"] == args.text
        evidence["submit_disabled_notice"] = exact_identifier(after, "chat.submit.disabled.notice")
        evidence["send_button_disabled"] = exact_identifier(after, "chat.send.disabled")
        evidence["send_button_enabled"] = exact_identifier(after, "chat.send")
        evidence["record_increment"] = exact_identifier(after, "chat.message.0")
        if not evidence["draft_visible"]:
            print("Draft did not match exactly; rerun with --text using ASCII if needed.",
                  file=sys.stderr)

        # A tap on the disabled Send control must not create a message record.
        size, point = dimensions(after), element(after, "chat.send.disabled")
        if size and point:
            tool("phone_tap", {"x": point[0] / size[0], "y": point[1] / size[1]},
                 label="phone_tap(disabled send)")
            final = hierarchy_text(tool("phone_inspect", label="phone_inspect after disabled send"))
            evidence["record_increment"] = exact_identifier(final, "chat.message.0")
            evidence["input_value"] = field_value(final, "chat.input")
            evidence["draft_visible"] = evidence["input_value"] == evidence["expected_text"]

        prepare = tool("message_prepare", {"recipient_id": "contact-02", "message": "x"},
                       label="message_prepare(disabled)")
        evidence["prepare_error"] = prepare.get("error")
        if evidence["prepare_error"] == "real_message_sending_disabled":
            evidence["checks"].append("message_prepare_disabled")
    finally:
        client.close()

    # Negative case: a separate process without the human interaction switch.
    restricted = MCPClient([sys.executable, "-m", "phonebridge.cli", "--driver", "xcode-mcp",
                            "--sim-udid", args.sim_udid, "serve"])
    try:
        restricted.initialize()
        denied = restricted.call("phone_tap", {"x": 0.5, "y": 0.5})
        evidence["interaction_without_switch"] = denied.get("error")
    finally:
        restricted.close()

    ok = (evidence["draft_visible"] and evidence["submit_disabled_notice"]
          and evidence["send_button_disabled"] and not evidence["send_button_enabled"]
          and not evidence["record_increment"]
          and evidence["prepare_error"] == "real_message_sending_disabled"
          and evidence["interaction_without_switch"] == "independent_approval_required")
    evidence["g1_passed"] = ok
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
