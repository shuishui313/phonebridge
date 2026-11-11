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

from .drivers import fail


@dataclass(frozen=True)
class IntentCapability:
    name: str
    summary: str
    result_key: str
    side_effect_free: bool = True
    requires_approval: bool = False

    def disclosure(self) -> dict:
        return {"name": self.name, "summary": self.summary,
                "side_effect_free": self.side_effect_free,
                "requires_approval": self.requires_approval,
                "channel": "A" if self.side_effect_free else "blocked"}


CATALOG: dict[str, IntentCapability] = {
    "device.status": IntentCapability(
        "device.status", "Read synthetic driver status and capabilities", "capabilities"),
    "contacts.lookup": IntentCapability(
        "contacts.lookup", "List synthetic fixture contacts", "contacts"),
    "chat.readback": IntentCapability(
        "chat.readback", "Read back the current synthetic chat record", "messages"),
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

    Published intents must not depend on the current UI screen: that would
    turn channel A into a disguised channel B interaction. Drivers without the
    synthetic published data simply report the capability as unavailable.
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
            try:
                payload = getattr(bridge, method)()
            except Exception:
                return {"ok": False, "error": "intent_execution_failed",
                        "no_side_effect_confirmed": False}
        if not isinstance(payload, dict) or not payload.get("ok"):
            return {"ok": False, "error": "intent_execution_failed",
                    "no_side_effect_confirmed": False}
        if entry.name == "chat.readback":
            verified = (isinstance(payload.get("messages"), list)
                        and payload.get("revision") is not None)
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

    def capabilities(self) -> dict:
        return {"ok": True, "channel": "A", "policy": self.policy(),
                "capabilities": [entry.disclosure() for entry in self.catalog.values()],
                "note": ("Synthetic fixture capabilities only. No system or "
                         "third-party App action is claimed or invoked.")}

    def run(self, capability: str, bridge: Any) -> dict:
        entry = self.catalog.get(capability)
        if entry is None:
            return fail("intent_not_published", capability=capability,
                        routing={"channel": "B", "fallback_allowed": True,
                                 "reason": "no_published_intent",
                                 "automatic_retry": False})
        if not entry.side_effect_free or entry.requires_approval:
            return fail("intent_requires_approval", capability=capability,
                        routing={"channel": "blocked", "fallback_allowed": False,
                                 "reason": "write_action_requires_trusted_approval",
                                 "automatic_retry": False})
        outcome = self.executor.execute(entry, bridge)
        if not outcome.get("ok"):
            confirmed = bool(outcome.get("no_side_effect_confirmed"))
            return fail(outcome.get("error") or "intent_execution_failed",
                        capability=capability,
                        routing={"channel": "A", "outcome": "failed",
                                 "fallback_allowed": confirmed,
                                 "reason": ("no_side_effect_confirmed" if confirmed
                                            else "side_effect_unconfirmed"),
                                 "automatic_retry": False},
                        pause=not confirmed)
        if not outcome.get("verified"):
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
