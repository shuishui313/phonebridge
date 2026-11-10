"""In-memory phone and chat fixtures for PhoneBridge's simulated backend."""

from dataclasses import dataclass
from threading import Lock
from typing import Any
from unicodedata import normalize
from uuid import uuid4


class BridgeError(Exception):
    """An actionable rejection of a simulated operation."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class Contact:
    id: str
    name: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


@dataclass
class MessageOperation:
    id: str
    contact: Contact
    text: str
    revision: int
    status: str = "pending"

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.id,
            "type": "send_message",
            "contact": self.contact.as_dict(),
            "text": self.text,
            "status": self.status,
        }


CONTACTS = (
    Contact("xiaoming", "小明"),
    Contact("sarah", "Sarah Chen"),
    Contact("alex-li", "Alex"),
    Contact("alex-wong", "Alex"),
)

APP_SCREENS = {"wechat": "contacts", "settings": "settings"}
SCREEN_APPS = {"home": None, "contacts": "wechat", "chat": "wechat", "settings": "settings"}
MAX_MESSAGE_LENGTH = 2_000


class PhoneBridge:
    """Run a single mock phone with isolated contacts, messages, and approvals."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._screen = "home"
        self._contact: Contact | None = None
        self._revision = 0
        self._operations: dict[str, MessageOperation] = {}
        self._messages: dict[str, list[str]] = {contact.id: [] for contact in CONTACTS}

    def inspect(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot()

    def open_app(self, app_id: str) -> dict[str, Any]:
        if not isinstance(app_id, str) or app_id not in APP_SCREENS:
            raise BridgeError("APP_NOT_ALLOWED", "Choose a simulated app.", apps=list(APP_SCREENS))
        with self._lock:
            return self._navigate(APP_SCREENS[app_id])

    def home(self) -> dict[str, Any]:
        with self._lock:
            return self._navigate("home")

    def back(self) -> dict[str, Any]:
        with self._lock:
            return self._navigate("contacts" if self._screen == "chat" else "home")

    def list_contacts(self) -> dict[str, Any]:
        with self._lock:
            self._require_wechat()
            return {"contacts": [contact.as_dict() for contact in CONTACTS]}

    def open_chat(self, contact: str) -> dict[str, Any]:
        with self._lock:
            self._require_wechat()
            return self._navigate("chat", self._resolve_contact(contact))

    def prepare_message(self, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE_LENGTH:
            raise BridgeError(
                "INVALID_ARGUMENT",
                f"Message must contain text and be at most {MAX_MESSAGE_LENGTH} characters.",
            )
        # JSON input may contain unpaired UTF-16 surrogates even when the wire is valid UTF-8.
        try:
            text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise BridgeError("INVALID_ARGUMENT", "Message must be valid Unicode.") from error

        with self._lock:
            if self._contact is None:
                raise BridgeError("WRONG_SCREEN", "Open a simulated WeChat conversation first.")
            operation = MessageOperation(uuid4().hex, self._contact, text, self._revision)
            self._operations[operation.id] = operation
            return operation.as_dict()

    def list_pending_operations(self) -> dict[str, Any]:
        with self._lock:
            return {
                "operations": [
                    operation.as_dict()
                    for operation in self._operations.values()
                    if operation.status == "pending" and operation.revision == self._revision
                ]
            }

    def decide_operation(self, operation_id: str, approved: bool) -> dict[str, Any]:
        """Record an operator's decision from the trusted local interface."""
        if type(approved) is not bool:
            raise BridgeError("INVALID_ARGUMENT", "Approval must be true or false.")
        with self._lock:
            operation = self._active_operation(operation_id)
            if operation.status == "approved":
                raise BridgeError("OPERATION_ALREADY_APPROVED", "This proposal is already approved.")
            operation.status = "approved" if approved else "denied"
            return operation.as_dict()

    def execute_operation(self, operation_id: str) -> dict[str, Any]:
        with self._lock:
            operation = self._active_operation(operation_id)
            if operation.status != "approved":
                raise BridgeError("APPROVAL_REQUIRED", "Approve the proposal in the local operator interface.")
            self._messages[operation.contact.id].append(operation.text)
            operation.status = "executed"
            self._revision += 1
            return operation.as_dict()

    def _snapshot(self) -> dict[str, Any]:
        contact = self._contact
        messages = self._messages[contact.id] if contact else []
        return {
            "backend": "simulated",
            "app": SCREEN_APPS[self._screen],
            "screen": self._screen,
            "contact": contact.as_dict() if contact else None,
            "messages": [{"direction": "outgoing", "text": text} for text in messages],
        }

    def _navigate(self, screen: str, contact: Contact | None = None) -> dict[str, Any]:
        if (self._screen, self._contact) != (screen, contact):
            self._screen = screen
            self._contact = contact
            self._revision += 1
        return self._snapshot()

    def _require_wechat(self) -> None:
        if SCREEN_APPS[self._screen] != "wechat":
            raise BridgeError("WRONG_SCREEN", "Open simulated WeChat first.")

    def _resolve_contact(self, query: str) -> Contact:
        if not isinstance(query, str) or not query.strip():
            raise BridgeError("INVALID_ARGUMENT", "Provide a contact name or ID.")
        for contact in CONTACTS:
            if contact.id == query.strip():
                return contact

        name = normalize("NFKC", query).strip().casefold()
        matches = [
            contact for contact in CONTACTS if normalize("NFKC", contact.name).strip().casefold() == name
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise BridgeError(
                "AMBIGUOUS_CONTACT",
                "Several contacts match. Specify one contact ID.",
                candidates=[contact.as_dict() for contact in matches],
            )
        raise BridgeError("CONTACT_NOT_FOUND", "No contact has that exact name or ID.")

    def _active_operation(self, operation_id: str) -> MessageOperation:
        if not isinstance(operation_id, str) or not operation_id:
            raise BridgeError("INVALID_ARGUMENT", "Provide an operation ID.")
        operation = self._operations.get(operation_id)
        if operation is None:
            raise BridgeError("OPERATION_NOT_FOUND", "No proposal has that operation ID.")
        if operation.status == "denied":
            raise BridgeError("OPERATION_DENIED", "This proposal was denied.")
        if operation.status == "executed":
            raise BridgeError("OPERATION_ALREADY_EXECUTED", "This proposal was already executed.")
        if operation.revision != self._revision:
            raise BridgeError("OPERATION_STALE", "The simulated phone changed. Prepare a new proposal.")
        return operation
