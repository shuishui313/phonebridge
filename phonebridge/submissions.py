"""Durable, minimal submission state and the single submission entry point.

Only operation IDs, recipient IDs, message digests/lengths and state
transitions are persisted. Plaintext bodies never reach disk. The
``dispatching`` record is fsynced before the device call, so a crash or
restart is recovered by reading the device back instead of replaying a
message that may already have been sent.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .drivers import MockIPhone, fail

EVENTS = frozenset({"prepared", "approved", "denied", "dispatching",
                    "completed", "unresolved", "reconciled"})
NON_REPLAYABLE = frozenset({"dispatching", "completed", "unresolved", "denied", "reconciled"})
RECOVERABLE = frozenset({"approved", "dispatching"})
NO_DEVICE_CALL = frozenset({"stale_or_expired_proposal", "real_message_sending_disabled"})


class SimulatedCrash(Exception):
    """Test-only hook used to model a process dying mid-submission."""


@dataclass
class SubmissionState:
    op_id: str
    recipient_id: str | None = None
    message_sha256: str | None = None
    message_chars: int | None = None
    baseline_count: int | None = None
    state: str = "prepared"
    events: list[str] = field(default_factory=list)
    updated_at: str | None = None
    detail: str | None = None

    @property
    def pending_readback(self) -> bool:
        return (self.state == "dispatching"
                or (self.state == "unresolved" and self.detail not in NO_DEVICE_CALL))


def _replay(records: list[dict]) -> dict[str, SubmissionState]:
    states: dict[str, SubmissionState] = {}
    for record in records:
        op_id = record.get("op_id")
        event = record.get("event")
        if not isinstance(op_id, str) or event not in EVENTS:
            continue
        current = states.get(op_id) or SubmissionState(op_id=op_id)
        current.events.append(event)
        current.state = event
        current.updated_at = record.get("ts")
        current.detail = record.get("detail")
        for key in ("recipient_id", "message_sha256", "message_chars", "baseline_count"):
            if record.get(key) is not None:
                setattr(current, key, record[key])
        states[op_id] = current
    return states


def _record(event: str, fields: dict) -> dict:
    if event not in EVENTS:
        raise ValueError("unknown submission event")
    for forbidden in ("message", "text", "body", "content"):
        if forbidden in fields:
            raise ValueError("submission ledger must never store plaintext bodies")
    return {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}


class _FileTransaction:
    def __init__(self, fd: int):
        self.fd = fd

    def append(self, event: str, **fields: Any) -> None:
        line = (json.dumps(_record(event, fields), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        os.lseek(self.fd, 0, os.SEEK_END)
        if os.write(self.fd, line) != len(line):
            raise OSError("short submission ledger write")
        os.fsync(self.fd)  # the dispatch record must survive a crash

    def records(self) -> list[dict]:
        size = os.fstat(self.fd).st_size
        raw = os.pread(self.fd, size, 0)
        out = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def replay(self) -> dict[str, SubmissionState]:
        return _replay(self.records())

    def state(self, op_id: str) -> SubmissionState | None:
        return self.replay().get(op_id)


class SubmissionLedger:
    """Append-only private JSONL with an exclusive lock across processes."""

    def __init__(self, path: str):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("submission ledger path must be absolute")
        parent = self.path.parent
        if not parent.is_dir():
            raise ValueError("submission ledger directory must already exist")
        info = parent.lstat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("submission ledger needs a private 0700 directory")

    @contextmanager
    def transaction(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise PermissionError("submission ledger must be a private 0600 regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield _FileTransaction(fd)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def append(self, event: str, **fields: Any) -> None:
        with self.transaction() as txn:
            txn.append(event, **fields)

    def replay(self) -> dict[str, SubmissionState]:
        with self.transaction() as txn:
            return txn.replay()

    def state(self, op_id: str) -> SubmissionState | None:
        return self.replay().get(op_id)

    def summary(self) -> dict:
        states = self.replay()
        return {"total": len(states),
                "by_state": {name: sum(1 for s in states.values() if s.state == name)
                             for name in sorted(EVENTS)},
                "persistent": True,
                "stores_plaintext": False}


class MemoryLedger:
    """Same contract without durability, for Mock demo and unit tests."""

    def __init__(self):
        self.records: list[dict] = []
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self._lock:
            yield self

    def append(self, event: str, **fields: Any) -> None:
        self.records.append(_record(event, fields))

    def replay(self) -> dict[str, SubmissionState]:
        with self._lock:
            return _replay(list(self.records))

    def state(self, op_id: str) -> SubmissionState | None:
        return self.replay().get(op_id)

    def summary(self) -> dict:
        states = self.replay()
        return {"total": len(states),
                "by_state": {name: sum(1 for s in states.values() if s.state == name)
                             for name in sorted(EVENTS)},
                "persistent": False,
                "stores_plaintext": False}


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_count_for(driver, recipient_id: str | None) -> int | None:
    """Records belonging to exactly one target, or None when unattributable.

    Device drivers expose a UI hierarchy rather than per-recipient records,
    so they must never claim an attributed record count.
    """
    if not recipient_id:
        return None
    messages = getattr(driver, "messages", None)
    if not isinstance(messages, dict):
        return None
    records = messages.get(recipient_id)
    return len(records) if isinstance(records, list) else None


def submission_evidence(driver, recipient_id, message_sha256, baseline_count) -> dict:
    """Confirm only a NEW record belonging to this operation's recipient.

    A digest match against an arbitrary or historical record is not evidence.
    Without a baseline and per-recipient records the outcome stays unclear,
    so recovery pauses instead of guessing.
    """
    if not recipient_id or baseline_count is None or not message_sha256:
        return {"matched": False, "unclear": True, "source": "baseline_unavailable"}
    current = record_count_for(driver, recipient_id)
    if current is None:
        return {"matched": False, "unclear": True, "source": "record_not_attributable"}
    messages = getattr(driver, "messages", None)
    records = messages[recipient_id]
    if current <= baseline_count:
        return {"matched": False, "unclear": False, "source": "no_new_record"}
    for item in records[baseline_count:]:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and digest_of(text) == message_sha256:
            return {"matched": True, "unclear": False, "source": "attributed_new_record"}
    return {"matched": False, "unclear": False, "source": "new_record_digest_mismatch"}


class SubmissionGate:
    """The only code path allowed to call ``driver.virtual_send``."""

    def __init__(self, bridge, ledger=None):
        self.bridge = bridge
        self.ledger = ledger or MemoryLedger()

    def _fields(self, p) -> dict:
        fields = {"op_id": p.id, "recipient_id": p.recipient_id,
                  "message_sha256": digest_of(p.message), "message_chars": len(p.message)}
        baseline = record_count_for(self.bridge.driver, p.recipient_id)
        if baseline is not None:
            fields["baseline_count"] = baseline
        return fields

    def execute(self, op_id: str, *, hooks: dict | None = None) -> dict:
        """Atomically consume the approval, mark dispatching, submit, then verify.

        Called under ``bridge._lock``. The dispatch record is durable before
        the device call; a lost receipt is never automatically replayed.
        """
        bridge = self.bridge
        p = bridge.proposals.get(op_id)
        if not p or p.consumed or p.denied:
            return fail("unknown_or_processed_operation")
        if not p.approved:
            return fail("independent_approval_required")
        if not bridge._is_current(p):
            p.consumed = True
            self.ledger.append("unresolved", op_id=op_id, detail="stale_or_expired_proposal")
            return fail("stale_or_expired_proposal")
        fields = self._fields(p)
        with self.ledger.transaction() as txn:
            prior = txn.state(op_id)
            # The ledger is the single source of truth. An in-memory approval
            # without a durable approved event must never reach the device.
            if not prior or "approved" not in prior.events:
                return fail("approval_not_durable")
            if prior.state in NON_REPLAYABLE:
                return fail("submission_already_processed", state=prior.state)
            if prior.message_sha256 not in (None, fields["message_sha256"]):
                return fail("submission_digest_mismatch")
            txn.append("dispatching", **fields)
        p.consumed = True  # approval consumed before the device call
        if (not getattr(bridge.driver, "simulated", False)
                and not getattr(bridge.driver, "virtual_submit_enabled", False)):
            self.ledger.append("unresolved", **fields, detail="real_message_sending_disabled")
            return fail("real_message_sending_disabled")
        if hooks and callable(hooks.get("crash_before_send")):
            raise SimulatedCrash("process stopped after dispatch record, before device call")
        try:
            result = bridge.driver.virtual_send(p.recipient_id, p.message)
        except Exception:
            self.ledger.append("unresolved", **fields, detail="driver_call_failed")
            return fail("submission_receipt_lost", pause=True,
                        note="Read the device back; do not resend automatically.")
        if hooks and callable(hooks.get("crash_after_send")):
            raise SimulatedCrash("device call returned but the receipt was lost")
        if not isinstance(result, dict) or not result.get("ok"):
            self.ledger.append("unresolved", **fields, detail="driver_rejected")
            return {**(result if isinstance(result, dict) else {}),
                    "ok": False, "submission_state": "unresolved", "pause": True,
                    "error": (result or {}).get("error", "submission_unverified")
                    if isinstance(result, dict) else "submission_unverified"}
        evidence = submission_evidence(bridge.driver, p.recipient_id,
                                       fields["message_sha256"], fields.get("baseline_count"))
        if evidence["matched"] and not evidence["unclear"]:
            self.ledger.append("completed", **fields, detail=evidence["source"])
            return {**result, "submission_state": "completed", "verification": evidence,
                    "pause": False}
        self.ledger.append("unresolved", **fields, detail="record_not_confirmed")
        return {**result, "ok": False, "error": "submission_unverified",
                "submission_state": "unresolved", "pause": True,
                "verification": evidence,
                "note": "Outcome unclear; read back by operation ID and do not resend."}

    def recover(self) -> dict:
        """Inspect approved or in-flight operations after a restart.

        A confirmed record is marked completed without resending. Anything
        unconfirmed is paused: no automatic replay is ever attempted.
        """
        report = []
        with self.ledger.transaction() as txn:
            for op_id, state in txn.replay().items():
                if state.state not in RECOVERABLE:
                    continue
                evidence = submission_evidence(self.bridge.driver, state.recipient_id,
                                               state.message_sha256, state.baseline_count)
                confirmed = (state.state == "dispatching"
                             and evidence["matched"] and not evidence["unclear"]
                             and record_count_for(self.bridge.driver, state.recipient_id)
                             == state.baseline_count + 1)
                event = "completed" if confirmed else "unresolved"
                detail = "recovered_confirmed_no_resend" if confirmed else "recovered_paused_no_resend"
                txn.append(event, op_id=op_id, recipient_id=state.recipient_id,
                           message_sha256=state.message_sha256,
                           message_chars=state.message_chars,
                           detail=f"{detail}:{state.state}")
                report.append({"op_id": op_id, "prior_state": state.state,
                               "state": event, "confirmed": confirmed,
                               "resent": False, "evidence": evidence["source"]})
            pending = [op_id for op_id, state in txn.replay().items()
                       if state.pending_readback]
        return {"ok": True, "recovered": report, "pending_pause": len(pending),
                "pending_operations": pending}

    def pending_operations(self) -> list[str]:
        return [op_id for op_id, state in self.ledger.replay().items()
                if state.pending_readback]

    def reconcile(self, op_id: str, observed: dict) -> dict:
        """Close one pending operation using current evidence, without replay."""
        with self.ledger.transaction() as txn:
            state = txn.state(op_id)
            if not state or not state.pending_readback:
                return fail("unknown_or_resolved_operation")
            event = "reconciled"
            detail = "verified_not_dispatched"
            if "dispatching" in state.events:
                contact = observed.get("contact")
                selected = (contact.get("id") if isinstance(contact, dict)
                            else observed.get("selected_id"))
                driver = self.bridge.driver
                # Mock has a direct per-recipient store even on its home screen.
                # UI drivers only populate the recipient cache from the current chat.
                if (not isinstance(driver, MockIPhone)
                        and (observed.get("screen") != "chat" or selected != state.recipient_id)):
                    return fail("readback_target_mismatch")
                evidence = submission_evidence(driver, state.recipient_id,
                                               state.message_sha256, state.baseline_count)
                count = record_count_for(driver, state.recipient_id)
                if evidence["unclear"]:
                    return fail("readback_not_confirmed")
                if evidence["matched"] and count == state.baseline_count + 1:
                    event, detail = "completed", "verified_attributed_record"
                elif evidence["source"] == "no_new_record" and count == state.baseline_count:
                    detail = "verified_no_new_record"
                else:
                    return fail("readback_not_confirmed")
            txn.append(event, op_id=op_id, detail=detail)
        return {"ok": True, "operation_id": op_id, "submission_state": event,
                "evidence": detail, "resent": False}
