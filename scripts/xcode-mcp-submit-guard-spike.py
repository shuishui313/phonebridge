#!/usr/bin/env python3
"""Verify that generic taps cannot submit in a normally launched fixture.

Unlike the 0b draft spike, this deliberately launches the fixture WITHOUT
``pb-disable-submit``, so the Send/Post controls are enabled. The acceptance
criterion is that an unapproved generic tap is rejected BEFORE any device
interaction command is dispatched and that no message record appears.
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
WINDOW = re.compile(r"Window, \{\{[\d.]+, [\d.]+\}, \{([\d.]+), ([\d.]+)\}\}")


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
            "clientInfo": {"name": "phonebridge-submit-guard-spike", "version": "0.4"},
        })
        if "result" not in hello:
            raise RuntimeError(f"initialize failed: {hello}")
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                          "method": "notifications/initialized"}) + "\n")
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


def exact_identifier(text: str, identifier: str) -> bool:
    return re.search(r"identifier:\s*'" + re.escape(identifier) + r"'", text) is not None


def hierarchy_text(result: dict):
    path = result.get("hierarchy_path")
    if not path:
        raise RuntimeError(f"no fresh hierarchy: {result}")
    return Path(path).read_text(encoding="utf-8")


def relaunch(udid: str, *arguments: str):
    subprocess.run(["xcrun", "simctl", "terminate", udid, BUNDLE_ID],
                   capture_output=True, text=True, timeout=15)
    launch = subprocess.run(["xcrun", "simctl", "launch", udid, BUNDLE_ID, *arguments],
                            capture_output=True, text=True, timeout=20)
    if launch.returncode != 0:
        raise RuntimeError(f"fixture launch failed: {launch.stderr.strip()}")
    return ":".join(arguments) or "enabled-submit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim-udid", required=True, help="Booted Apple Simulator UDID")
    ap.add_argument("--text", default="guard test", help="Synthetic draft text")
    args = ap.parse_args()

    evidence = {"sim_udid": args.sim_udid, "bundle_id": BUNDLE_ID,
                "checks": [], "tool_calls": []}
    # Deliberately launch with submission ENABLED.
    evidence["launch_arguments"] = relaunch(args.sim_udid)
    evidence["checks"].append("fixture_relaunched_with_submit_enabled")

    command = [sys.executable, "-m", "phonebridge.cli", "--driver", "xcode-mcp",
               "--sim-udid", args.sim_udid, "--allow-xcode-interactions", "serve"]
    client = MCPClient(command)
    try:
        evidence["server"] = client.initialize()

        def tool(name, arguments=None):
            evidence["tool_calls"].append(name)
            return client.call(name, arguments)

        opened = tool("phone_open_app", {"app": "fixture"})
        if not opened.get("ok"):
            raise RuntimeError(f"fixture activation failed: {opened}")

        def tap_identifier(identifier):
            text = hierarchy_text(tool("phone_inspect"))
            size, point = dimensions(text), element(text, identifier)
            if not size or not point:
                raise RuntimeError(f"cannot locate {identifier} in fresh hierarchy")
            result = tool("phone_tap", {"x": point[0] / size[0],
                                        "y": point[1] / size[1]})
            if not result.get("ok"):
                raise RuntimeError(f"tap {identifier} failed: {result}")
            return text

        tap_identifier("app.whatsapp")
        tap_identifier("contact.contact-02")
        tap_identifier("chat.input")
        typed = tool("phone_type_text", {"text": args.text})
        if not typed.get("ok"):
            raise RuntimeError(f"type_text failed: {typed}")

        after = hierarchy_text(tool("phone_inspect"))
        evidence["send_button_enabled"] = exact_identifier(after, "chat.send")
        evidence["send_button_disabled_variant"] = exact_identifier(after, "chat.send.disabled")
        size, point = dimensions(after), element(after, "chat.send")
        if not evidence["send_button_enabled"] or not size or not point:
            raise RuntimeError("enabled Send control not found in fresh hierarchy")

        blocked = tool("phone_tap", {"x": point[0] / size[0],
                                     "y": point[1] / size[1]})
        evidence["submit_tap_error"] = blocked.get("error")
        evidence["submit_tap_retry"] = blocked.get("retry")
        evidence["submit_tap_ok"] = blocked.get("ok", False)

        final = hierarchy_text(tool("phone_inspect"))
        evidence["record_increment"] = exact_identifier(final, "chat.message.0")
        evidence["guard_passed"] = (
            evidence["send_button_enabled"]
            and not evidence["send_button_disabled_variant"]
            and evidence["submit_tap_error"] == "submission_requires_approval"
            and not evidence["submit_tap_ok"]
            and evidence["submit_tap_retry"]["allowed"] is False
            and not evidence["record_increment"]
        )
        evidence["checks"].append("unapproved_submit_rejected_before_interaction")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0 if evidence["guard_passed"] else 1
    finally:
        client.close()
        # Restore the G1 baseline: submission paths disabled again.
        try:
            relaunch(args.sim_udid, "pb-disable-submit")
            print("Fixture restored with pb-disable-submit", file=sys.stderr)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"WARNING: could not restore disabled fixture: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
