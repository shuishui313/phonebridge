#!/usr/bin/env python3
"""Reproducible channel-A evidence over the synthetic Mock fixture.

No real App Intents, Shortcuts, devices or third-party Apps are touched. The
script prints one JSON object and exits non-zero if any check fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phonebridge.api import CoreAPI
from phonebridge.core import PhoneBridge
from phonebridge.drivers import MockIPhone
from phonebridge.intents import ChannelRouter, SyntheticIntentExecutor
from phonebridge.mcp_v04 import MCPServer


def main() -> int:
    checks: dict[str, bool] = {}
    driver = MockIPhone()
    api = CoreAPI(PhoneBridge(driver), max_read_retries=100)
    server = MCPServer(api)
    server.process({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "intent-spike", "version": "0"}}})
    server.process({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    def tool(name, arguments=None):
        response = server.process({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments or {}}})
        return json.loads(response["result"]["content"][0]["text"])

    names = {entry["name"] for entry in server.process(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
    )["result"]["tools"]}
    checks["mcp_advertises_intent_tools"] = {"intents_list", "intent_run"} <= names
    checks["legacy_tool_contract_preserved"] = (
        {"phone_status", "phone_inspect", "phone_open_app", "phone_home", "phone_back",
         "phone_tap", "phone_swipe", "phone_type_text", "contacts_list", "contacts_open",
         "message_prepare", "phone_execute_operation"} <= names
        and not any(name.startswith("wechat_") for name in names))

    listing = tool("intents_list")
    checks["catalog_is_synthetic_only"] = bool(
        listing.get("ok") and not listing["policy"]["real_app_intents_verified"]
        and not listing["policy"]["real_shortcuts_invoked"])
    checks["a_first_policy_disclosed"] = (
        listing["policy"]["strategy"] == "A_first_B_fallback"
        and listing["policy"]["fallback_requires_no_side_effect_confirmation"])

    entries = {item["name"]: item for item in listing["capabilities"]}
    blocked = {item["name"]: item for item in listing["blocked_capabilities"]}
    checks["write_intent_not_published_for_direct_use"] = bool(
        "message.send" not in entries and blocked["message.send"]["requires_approval"]
        and not blocked["message.send"]["side_effect_free"])

    before = driver.record_count()
    write = tool("intent_run", {"capability": "message.send"})
    checks["write_intent_blocked"] = (write["error"] == "intent_requires_approval"
                                      and write["routing"]["channel"] == "blocked")

    unknown = tool("intent_run", {"capability": "not.published"})
    checks["unpublished_routes_to_b_only"] = (
        unknown["error"] == "intent_not_published"
        and unknown["routing"]["channel"] == "B"
        and unknown["routing"]["fallback_allowed"])

    contacts = tool("intent_run", {"capability": "contacts.lookup"})
    checks["read_only_intent_clear_readback"] = bool(
        contacts.get("ok") and contacts["channel"] == "A"
        and contacts["verification"]["outcome"] == "clear"
        and len(contacts["result"]["contacts"]) == 4)
    chat_args = {"capability": "chat.readback"}
    unselected = tool("intent_run", chat_args)
    opened_app = tool("phone_open_app", {"app": "messaging"})
    opened_chat = tool("contacts_open", {"contact": "contact-01"})
    chat = tool("intent_run", chat_args)
    checks["chat_readback_is_fresh"] = bool(
        unselected.get("error") == "intent_chat_not_selected"
        and not unselected["retry"]["allowed"] and opened_app["ok"] and opened_chat["ok"]
        and chat.get("ok") and chat["result"]["contact"]["id"] == "contact-01"
        and chat["result"]["messages"] == [] and chat["result"]["revision"] == driver.revision)
    checks["no_records_created_by_channel_a"] = before == driver.record_count() == 0

    status = tool("phone_status")
    checks["status_discloses_routing"] = (
        status["routing"]["strategy"] == "A_first_B_fallback"
        and status["routing"]["unclear_result_pauses_writes"])

    unclear_api = CoreAPI(PhoneBridge(MockIPhone()), max_read_retries=100,
                          router=ChannelRouter(executor=SyntheticIntentExecutor(force_unclear=True)))
    unclear = unclear_api.dispatch("intent_run", {"capability": "contacts.lookup"})
    checks["unclear_readback_pauses_writes"] = bool(
        unclear["error"] == "intent_readback_unclear"
        and not unclear["routing"]["fallback_allowed"]
        and not unclear["retry"]["allowed"]
        and unclear_api.dispatch("phone_open_app", {"app": "messaging"})["error"]
        == "paused_pending_readback")
    checks["fresh_readback_resumes"] = bool(
        unclear_api.resume_after_readback()["ok"]
        and unclear_api.dispatch("phone_open_app", {"app": "messaging"})["ok"])

    evidence = {
        "driver": "mock",
        "channel": "A",
        "real_app_intents_verified": False,
        "real_shortcuts_invoked": False,
        "real_message_sent": False,
        "checks": checks,
        "check_count": len(checks),
        "channel_a_passed": all(checks.values()),
        "note": ("Synthetic fixture capabilities only; channel B is a routing "
                 "decision, never an automatic execution."),
    }
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["channel_a_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
