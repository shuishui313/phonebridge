"""Channel A: explicitly published intents and A-first / B-fallback routing.

This module does not invoke real App Intents or Shortcuts. The catalog only
contains synthetic fixture capabilities that can be read back; system and
third-party App actions must be verified separately before being added.
Channel B is a routing decision here, never an automatic execution: the Agent
still has to use the existing device tools, which keep their approval gates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .drivers import MESSAGING_APP, MockIPhone, fail


@dataclass(frozen=True)
class IntentCapability:
    name: str
    summary: str
    result_key: str
    side_effect_free: bool = True
    requires_approval: bool = False

    @property
    def read_only(self) -> bool:
        return self.side_effect_free and not self.requires_approval

    def disclosure(self) -> dict:
        return {"name": self.name, "summary": self.summary,
                "side_effect_free": self.side_effect_free,
                "requires_approval": self.requires_approval,
                "channel": "A" if self.read_only else "blocked"}


CATALOG: dict[str, IntentCapability] = {
    "device.status": IntentCapability(
        "device.status", "Read synthetic driver status and capabilities", "capabilities"),
    "contacts.lookup": IntentCapability(
        "contacts.lookup", "List synthetic fixture contacts", "contacts"),
    "chat.readback": IntentCapability(
        "chat.readback", "Read messages for the currently selected Mock chat", "messages"),
    "message.send": IntentCapability(
        "message.send", "Write action, not published for direct intent calls", "",
        side_effect_free=False, requires_approval=True),
}

_BRIDGE_READERS = {
    "device.status": "status",
    "chat.readback": "inspect",
}


class SyntheticIntentExecutor:
    """Deterministic read-only executor over an injected bridge.

    Read from the Mock fixture without performing device interactions. Chat
    readback uses the fixture's selected conversation as its recipient scope.
    """

    def __init__(self, *, force_unclear: bool = False):
        self.force_unclear = force_unclear

    def execute(self, entry: IntentCapability, bridge: Any) -> dict:
        method = _BRIDGE_READERS.get(entry.name)
        if entry.name == "contacts.lookup":
            contacts = getattr(getattr(bridge, "driver", None), "contacts", None)
            if not isinstance(contacts, (list, tuple)) or not contacts:
                return {"ok": False, "error": "intent_not_published",
                        "no_side_effect_confirmed": True}
            payload = {"ok": True, "simulated": True,
                       "contacts": [dict(item) for item in contacts]}
        elif method is None:
            return {"ok": False, "error": "intent_not_executable",
                    "no_side_effect_confirmed": True}
        else:
            payload = getattr(bridge, method)()
        if not isinstance(payload, dict) or not payload.get("ok"):
            return {"ok": False, "error": "intent_execution_failed",
                    "no_side_effect_confirmed": False}
        if entry.name == "chat.readback":
            if payload.get("app") != MESSAGING_APP or payload.get("screen") != "chat":
                return {"ok": False, "error": "intent_chat_not_selected",
                        "no_side_effect_confirmed": True}
            contact = payload.get("contact")
            recipient_id = contact.get("id") if isinstance(contact, dict) else None
            verified = (isinstance(recipient_id, str) and bool(recipient_id)
                        and isinstance(payload.get("messages"), list)
                        and type(payload.get("revision")) is int)
        else:
            value = payload.get(entry.result_key)
            verified = isinstance(value, (list, dict)) and bool(value)
        if self.force_unclear:
            verified = False
        return {"ok": True, "result": payload, "verified": verified,
                "no_side_effect_confirmed": verified}


class ChannelRouter:
    """A first, B only as a decision; unclear results pause writes."""

    def __init__(self, catalog: dict[str, IntentCapability] | None = None,
                 executor: Any = None):
        self.catalog = dict(CATALOG if catalog is None else catalog)
        self.executor = executor if executor is not None else SyntheticIntentExecutor()

    @staticmethod
    def policy() -> dict:
        return {"strategy": "A_first_B_fallback",
                "channel_a": "explicitly_published_intents",
                "channel_b": "device_takeover",
                "fallback_requires_no_side_effect_confirmation": True,
                "unclear_result_pauses_writes": True,
                "real_app_intents_verified": False,
                "real_shortcuts_invoked": False}

    def capabilities(self, bridge: Any) -> dict:
        is_mock = isinstance(bridge.driver, MockIPhone)
        return {"ok": True, "channel": "A", "policy": self.policy(),
                "capabilities": [entry.disclosure() for entry in self.catalog.values()
                                 if is_mock and entry.read_only],
                "blocked_capabilities": [entry.disclosure() for entry in self.catalog.values()
                                         if not entry.read_only],
                "note": ("Only the Mock driver publishes synthetic intents. No system or "
                         "third-party App action is claimed or invoked.")}

    def run(self, capability: str, bridge: Any) -> dict:
        entry = self.catalog.get(capability)
        if entry is not None and not entry.read_only:
            return fail("intent_requires_approval", capability=capability,
                        routing={"channel": "blocked", "fallback_allowed": False,
                                 "reason": "write_action_requires_trusted_approval",
                                 "automatic_retry": False})
        if entry is None or not isinstance(bridge.driver, MockIPhone):
            return fail("intent_not_published", capability=capability,
                        routing={"channel": "B", "fallback_allowed": True,
                                 "reason": "no_published_intent",
                                 "automatic_retry": False})
        try:
            outcome = self.executor.execute(entry, bridge)
        except Exception:
            outcome = None
        if not isinstance(outcome, dict):
            outcome = fail("intent_execution_failed")
        if outcome.get("ok") is not True:
            confirmed = outcome.get("no_side_effect_confirmed") is True
            return fail(outcome.get("error") or "intent_execution_failed",
                        capability=capability,
                        routing={"channel": "A", "outcome": "failed",
                                 "fallback_allowed": confirmed,
                                 "reason": ("no_side_effect_confirmed" if confirmed
                                            else "side_effect_unconfirmed"),
                                 "automatic_retry": False},
                        pause=not confirmed)
        if outcome.get("verified") is not True or not isinstance(outcome.get("result"), dict):
            return fail("intent_readback_unclear", capability=capability,
                        routing={"channel": "A", "outcome": "unclear",
                                 "fallback_allowed": False,
                                 "reason": "readback_not_confirmed",
                                 "automatic_retry": False},
                        pause=True)
        return {"ok": True, "capability": capability, "channel": "A",
                "result": outcome["result"],
                "verification": {"outcome": "clear", "source": "intent_readback",
                                 "automatic_retry": False},
                "routing": {"channel": "A", "fallback_allowed": False,
                            "reason": "published_intent_succeeded"}}
