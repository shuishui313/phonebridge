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
EVIDENCE_KEYS = frozenset({"screen", "revision", "sha256", "hierarchy_path",
                           "foreground_bundle", "messages", "input_text", "app", "contact"})


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

    def note_failure(self, action: str, kind: str) -> dict:
        with self._lock:
            self._failure_run = self._failure_run + 1 if self._last_failure == action else 1
            self._last_failure = action
            if kind == "critical":
                return {"allowed": False, "reason": "submission_requires_manual_readback"}
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
        return {**self.bridge.status(), "task": self.governor.snapshot()}

    def reset_task(self) -> None:
        """Trusted-side only; the Agent cannot call this through MCP."""
        self.governor.reset()

    def _verify(self) -> dict:
        """Read back after a state change; never automatically replay a submission."""
        try:
            observed = self.bridge.inspect()
        except Exception:
            observed = fail("verification_failed")
        fresh = (isinstance(observed, dict) and observed.get("ok") is True
                 and any(key in observed for key in EVIDENCE_KEYS))
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
        problem = self.governor.check(action, kind)
        if problem:
            return problem
        if action == "phone_status":
            return self.status()
        try:
            result = getattr(self.bridge, method)(*[args[field] for field in fields])
        except Exception:
            result = fail("internal_tool_failure")
        if not isinstance(result, dict):
            result = fail("invalid_driver_result")
        if not result.get("ok"):
            return {**result, "retry": self.governor.note_failure(action, kind)}
        self.governor.note_success()
        if kind == "read":
            return result
        return {**result, "verification": self._verify()}
