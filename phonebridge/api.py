"""Stable internal API between the PhoneBridge core and its thin shells.

The MCP stdio host and any future standalone App shell call ``dispatch`` with
the same action names and arguments. Approval actions are deliberately absent:
they stay on the independent trusted-side channel.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from .core import PhoneBridge, valid_utf8
from .drivers import fail

# action -> (core method, argument names, kind)
# kind: read (no device change), mutating (changes device state), critical (submission)
ACTION_SPECS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "phone_status": ("status", (), "read"),
    "phone_inspect": ("inspect", (), "read"),
    "phone_open_app": ("open_app", ("app",), "mutating"),
    "phone_home": ("home", (), "mutating"),
    "phone_back": ("back", (), "mutating"),
    "phone_tap": ("tap", ("x", "y"), "mutating"),
    "phone_swipe": ("swipe", ("start_x", "start_y", "end_x", "end_y"), "mutating"),
    "phone_type_text": ("type_text", ("text",), "mutating"),
    "contacts_list": ("list_contacts", (), "read"),
    "contacts_open": ("open_chat", ("contact",), "mutating"),
    "message_prepare": ("prepare_message", ("recipient_id", "message"), "mutating"),
    "phone_execute_operation": ("execute", ("approval_id",), "critical"),
}

NUMERIC = {"x", "y", "start_x", "start_y", "end_x", "end_y"}
NO_RETRY_ERRORS = frozenset({
    "submission_requires_approval", "submit_guard_unavailable",
    "paused_pending_readback", "approval_not_durable",
    "submission_already_processed", "submission_digest_mismatch",
    "real_message_sending_disabled", "submit_control_unavailable",
    "submission_context_mismatch", "virtual_submission_not_confirmed",
})


class TaskGovernor:
    """Bound the effort of one task without exposing reset to the Agent."""

    def __init__(self, *, time_limit: float = 300.0, action_budget: int = 40,
                 max_read_retries: int = 5, max_retries: int = 2, clock=None):
        self.time_limit = float(time_limit)
        self.action_budget = int(action_budget)
        self.max_read_retries = int(max_read_retries)
        self.max_retries = int(max_retries)
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._deadline: float | None = None
        self._actions = 0
        self._last_read: str | None = None
        self._read_run = 0
        self._last_failure: str | None = None
        self._failure_run = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {"actions_used": self._actions, "action_budget": self.action_budget,
                    "time_limit": self.time_limit,
                    "remaining_seconds": None if self._deadline is None
                    else max(0.0, self._deadline - self.clock()),
                    "max_read_retries": self.max_read_retries,
                    "max_retries": self.max_retries}

    def reset(self) -> None:
        """Trusted-side only; never exposed as an MCP tool."""
        with self._lock:
            self._deadline = None
            self._actions = 0
            self._last_read = None
            self._read_run = 0
            self._last_failure = None
            self._failure_run = 0

    def check(self, action: str, kind: str) -> dict | None:
        with self._lock:
            now = self.clock()
            if kind == "read":
                self._read_run = self._read_run + 1 if self._last_read == action else 1
                self._last_read = action
                if self._read_run > self.max_read_retries:
                    return fail("read_retry_limit_exceeded", action=action,
                                limit=self.max_read_retries)
                return None
            if self._deadline is None:
                self._deadline = now + self.time_limit
            if now > self._deadline:
                return fail("task_time_limit_exceeded", limit=self.time_limit)
            if self._actions >= self.action_budget:
                return fail("action_budget_exhausted", limit=self.action_budget)
            self._actions += 1  # count attempts, including failures
            self._last_read = None
            self._read_run = 0
            return None

    def note_failure(self, action: str, kind: str, error: str | None = None) -> dict:
        with self._lock:
            self._failure_run = self._failure_run + 1 if self._last_failure == action else 1
            self._last_failure = action
            if kind == "critical":
                return {"allowed": False, "reason": "submission_requires_manual_readback"}
            if error in NO_RETRY_ERRORS:
                return {"allowed": False, "reason": "non_retryable_security_error"}
            if self._failure_run > self.max_retries:
                return {"allowed": False, "reason": "retry_limit_reached",
                        "attempt": self._failure_run, "limit": self.max_retries}
            return {"allowed": True, "attempt": self._failure_run, "limit": self.max_retries}

    def note_success(self) -> None:
        with self._lock:
            self._last_failure = None
            self._failure_run = 0


class CoreAPI:
    """One stable entry point shared by the MCP host and future App shell."""

    def __init__(self, bridge: PhoneBridge | None = None, *, time_limit: float = 300.0,
                 action_budget: int = 40, max_read_retries: int = 5,
                 max_retries: int = 2, clock=None):
        if time_limit <= 0 or action_budget < 1 or max_read_retries < 1 or max_retries < 0:
            raise ValueError("invalid task limits")
        self.bridge = bridge if bridge is not None else PhoneBridge()
        self.governor = TaskGovernor(time_limit=time_limit, action_budget=action_budget,
                                     max_read_retries=max_read_retries,
                                     max_retries=max_retries, clock=clock)
        self._paused = False
        self._pause_reason: str | None = None

    @staticmethod
    def _validate(action: str, args: Any) -> dict | None:
        fields = ACTION_SPECS[action][1]
        if not isinstance(args, dict) or set(args) != set(fields):
            return fail("invalid_parameters", expected=sorted(fields))
        for key, value in args.items():
            if key in NUMERIC:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return fail("invalid_parameter_type", parameter=key)
            elif not isinstance(value, str) or not valid_utf8(value):
                return fail("invalid_parameter_type", parameter=key)
        return None

    def status(self) -> dict:
        task = {**self.governor.snapshot(), "paused": self._paused,
                "pause_reason": self._pause_reason}
        return {**self.bridge.status(), "task": task,
                "submissions": self.bridge.submission_summary()}

    @staticmethod
    def _record_count(bridge) -> int | None:
        """Best-effort count of outbound records, used to detect a bypass.

        Drivers report their own count from the current fresh observation;
        this also works for hierarchy-based device drivers.
        """
        try:
            counter = getattr(bridge.driver, "record_count", None)
            if callable(counter):
                value = counter()
                if isinstance(value, int) and not isinstance(value, bool):
                    return value
                return None
            messages = getattr(bridge.driver, "messages", None)
            if isinstance(messages, dict):
                return sum(len(v) for v in messages.values() if isinstance(v, list))
            observed = bridge.inspect()
            if isinstance(observed, dict) and isinstance(observed.get("messages"), list):
                return len(observed["messages"])
        except Exception:
            return None
        return None

    def reset_task(self) -> None:
        """Trusted-side only; the Agent cannot call this through MCP.

        Resetting task limits never clears a pause: that requires one
        confirmed fresh readback through ``resume_after_readback``.
        """
        self.governor.reset()

    @staticmethod
    def _fresh_evidence(observed) -> bool:
        """A clear outcome needs a concrete observation, not merely a key."""
        if not isinstance(observed, dict) or observed.get("ok") is not True:
            return False
        if observed.get("observation_error"):
            return False
        if observed.get("revision") is None:
            return False
        if observed.get("hierarchy_path") or observed.get("sha256"):
            return True
        return (isinstance(observed.get("messages"), list)
                and bool(observed.get("screen") or observed.get("app")))

    def resume_after_readback(self) -> dict:
        """Trusted-side only. Resume writes after one confirmed fresh readback."""
        try:
            observed = self.bridge.inspect()
        except Exception:
            observed = fail("verification_failed")
        if not self._fresh_evidence(observed):
            return fail("readback_not_confirmed")
        self._paused = False
        self._pause_reason = None
        self.governor.note_success()
        return {"ok": True, "resumed": True}

    def _verify(self) -> dict:
        """Read back after a state change; never automatically replay a submission."""
        try:
            observed = self.bridge.inspect()
        except Exception:
            observed = fail("verification_failed")
        fresh = self._fresh_evidence(observed)
        return {"outcome": "clear" if fresh else "unclear",
                "pause": not fresh,
                "automatic_retry": False,
                "observation_error": observed.get("error") if isinstance(observed, dict) else None}

    def dispatch(self, action: str, args: dict | None = None) -> dict:
        args = {} if args is None else args
        spec = ACTION_SPECS.get(action)
        if spec is None:
            return fail("unknown_action")
        problem = self._validate(action, args)
        if problem:
            return problem
        method, fields, kind = spec
        if self._paused and kind != "read":
            return fail("paused_pending_readback", reason=self._pause_reason)
        problem = self.governor.check(action, kind)
        if problem:
            return problem
        if action == "phone_status":
            return self.status()
        before = self._record_count(self.bridge) if kind == "mutating" else None
        try:
            result = getattr(self.bridge, method)(*[args[field] for field in fields])
        except Exception:
            result = fail("internal_tool_failure")
        if not isinstance(result, dict):
            result = fail("invalid_driver_result")
        if not result.get("ok"):
            # A failed submission can still carry pause=True (lost receipt,
            # unconfirmed record). That must be a real pause, not just a field
            # in the response, otherwise the next write would go through.
            if result.get("pause"):
                self._paused = True
                self._pause_reason = result.get("error") or "submission_receipt_lost"
            return {**result, "retry": self.governor.note_failure(
                action, kind, result.get("error"))}
        self.governor.note_success()
        if kind == "read":
            return result
        verification = self._verify()
        evidence = result.get("verification")
        if isinstance(evidence, dict):
            verification = {**verification, **evidence}
            if evidence.get("unclear") is True:
                verification = {**verification, "outcome": "unclear", "pause": True,
                                "automatic_retry": False}
        if before is not None:
            after = self._record_count(self.bridge)
            if after is not None and after > before:
                # Only phone_execute_operation may add a record. A generic
                # interaction that does so bypassed the approval gate.
                verification = {**verification, "submission_bypass_detected": True,
                                "outcome": "unclear", "pause": True,
                                "automatic_retry": False}
        if verification.get("pause"):
            self._paused = True
            self._pause_reason = (verification.get("observation_error")
                                  or ("submission_bypass_detected"
                                      if verification.get("submission_bypass_detected")
                                      else "readback_unclear"))
        return {**result, "verification": verification}
