"""Independent phone-control core with deny-by-default, out-of-band approval."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .drivers import MESSAGING_APP, MockIPhone, fail
from .submissions import SubmissionGate, digest_of


def normalized_coordinates(*coordinates):
    return all(type(value) in (int, float) and 0 <= value <= 1 for value in coordinates)


def valid_utf8(text):
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass
class Proposal:
    id: str
    recipient_id: str
    recipient_name: str
    message: str
    revision: int
    created_at: float
    approved: bool = False
    consumed: bool = False
    denied: bool = False


class PhoneBridge:
    def __init__(self, driver=None, *, approval_ttl: int = 300, audit_path: str | None = None,
                 clock=None, ledger=None, submission_hooks: dict | None = None,
                 approval_isolated: bool = False):
        self.driver = driver if driver is not None else MockIPhone()
        self.ttl = approval_ttl
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self.proposals: dict[str, Proposal] = {}
        self.audit_path = Path(audit_path) if audit_path else None
        self.approval_isolated = bool(approval_isolated)
        self.submission_hooks = submission_hooks
        # Single submission entry point. All durable state lives in the ledger,
        # which never stores the plaintext message body.
        self.submissions = SubmissionGate(self, ledger)

    def _audit(self, action, result, **safe_fields):
        if self.audit_path is None:
            return result
        record = {"time": datetime.now(timezone.utc).isoformat(), "action": action,
                  "ok": result.get("ok", False), "error": result.get("error"), **safe_fields}
        try:
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(self.audit_path, flags, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as file:
                info = os.fstat(file.fileno())
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise PermissionError("Audit file must be private and owned by the current user.")
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Preserve the actual outcome when a write to the audit log fails.
            return {**result, "audit_recorded": False}
        return result

    def status(self):
        with self._lock:
            return self.driver.status()

    def inspect(self):
        with self._lock:
            return self.driver.observe()

    def open_app(self, app: str):
        with self._lock:
            return self._audit("open_app", self.driver.open_app(app))

    def home(self):
        with self._lock:
            return self._audit("home", self.driver.home())

    def back(self):
        with self._lock:
            return self._audit("back", self.driver.back())

    def list_contacts(self):
        with self._lock:
            return self.driver.list_contacts()

    def open_chat(self, contact: str):
        with self._lock:
            return self._audit("open_chat", self.driver.open_chat(contact))

    def tap(self, x: float, y: float):
        if not normalized_coordinates(x, y):
            return fail("normalized_coordinates_required")
        with self._lock:
            return self._audit("tap", self.driver.tap(x, y))

    def swipe(self, sx: float, sy: float, ex: float, ey: float):
        if not normalized_coordinates(sx, sy, ex, ey):
            return fail("normalized_coordinates_required")
        with self._lock:
            return self._audit("swipe", self.driver.swipe(sx, sy, ex, ey))

    def type_text(self, text: str):
        if not isinstance(text, str) or not text or len(text) > 1000 or not valid_utf8(text):
            return fail("invalid_text")
        with self._lock:
            return self._audit("type_text", self.driver.type_text(text), characters=len(text))

    def prepare_message(self, recipient_id: str, message: str):
        """Only virtual fixture messages; an agent cannot grant its own approval."""
        if (
            not isinstance(recipient_id, str) or not recipient_id or not isinstance(message, str)
            or not message.strip() or len(message) > 1000 or not valid_utf8(message)
        ):
            return fail("invalid_message")
        with self._lock:
            if not getattr(self.driver, "simulated", False) or not getattr(self.driver, "send_enabled", False):
                return fail("real_message_sending_disabled")
            resolver = getattr(self.driver, "resolve_contact_id", None)
            canonical_id = resolver(recipient_id) if callable(resolver) else recipient_id
            if (self.driver.app != MESSAGING_APP or self.driver.screen != "chat"
                    or self.driver.selected_id != canonical_id or canonical_id is None):
                return fail("recipient_not_verified_in_current_chat")
            if self.driver.input_text != message:
                return fail("draft_mismatch", expected_chars=len(self.driver.input_text))
            recipient = next((c for c in self.driver.contacts if c["id"] == canonical_id), None)
            if not recipient:
                return fail("recipient_not_found")
            op_id = secrets.token_urlsafe(24)
            self.proposals[op_id] = Proposal(op_id, canonical_id, recipient["name"], message,
                                             self.driver.revision, self.clock())
            digest = hashlib.sha256(message.encode()).hexdigest()
            self.submissions.ledger.append(
                "prepared", op_id=op_id, recipient_id=canonical_id,
                message_sha256=digest, message_chars=len(message))
            return self._audit("prepare_message", {"ok": True, "approval_id": op_id,
                "recipient_name": recipient["name"], "recipient_id": canonical_id,
                "message_length": len(message), "message_sha256": digest,
                "approved": False, "simulated": True, "real_message_sent": False}, recipient_id=canonical_id)

    def _is_current(self, p: Proposal):
        return (0 <= self.clock() - p.created_at <= self.ttl and
                self.driver.revision == p.revision and
                self.driver.screen == "chat" and self.driver.selected_id == p.recipient_id and
                hmac.compare_digest(self.driver.input_text.encode("utf-8"), p.message.encode("utf-8")))

    def approval_list(self):
        """Internal trusted-side command. NEVER exposed as MCP tool."""
        with self._lock:
            return {"ok": True, "proposals": [
                {"approval_id": p.id, "recipient_id": p.recipient_id, "recipient_name": p.recipient_name,
                 "message": p.message, "status": "approved" if p.approved else "pending",
                 "valid": self._is_current(p)}
                for p in self.proposals.values() if not p.consumed and not p.denied]}

    def approve(self, op_id: str):
        with self._lock:
            p = self.proposals.get(op_id)
            if not p or p.consumed or p.denied or p.approved:
                return fail("unknown_or_processed_approval")
            if not self._is_current(p):
                return fail("stale_or_expired_proposal")
            p.approved = True
            self.submissions.ledger.append("approved", op_id=op_id,
                                           message_sha256=digest_of(p.message))
            return self._audit("approve", {"ok": True, "approved": True}, recipient_id=p.recipient_id)

    def deny(self, op_id: str):
        with self._lock:
            p = self.proposals.get(op_id)
            if not p or p.consumed or p.denied:
                return fail("unknown_or_processed_approval")
            p.denied = True
            self.submissions.ledger.append("denied", op_id=op_id)
            return self._audit("deny", {"ok": True, "denied": True}, recipient_id=p.recipient_id)

    def execute(self, op_id: str):
        """Single submission entry point. Never call driver.virtual_send elsewhere."""
        with self._lock:
            result = self.submissions.execute(op_id, hooks=self.submission_hooks)
            recipient = (self.proposals.get(op_id).recipient_id
                         if op_id in self.proposals else None)
            return self._audit("execute_virtual_message", result, recipient_id=recipient,
                               submission_state=result.get("submission_state"))

    def submission_summary(self) -> dict:
        return {**self.submissions.ledger.summary(),
                "approval_isolated": self.approval_isolated}

    def recover_submissions(self) -> dict:
        """Trusted-side startup step; never exposed as an MCP tool."""
        with self._lock:
            return self.submissions.recover()
