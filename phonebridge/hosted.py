"""OS-level grants for an externally hosted approval endpoint.

The service account keeps a dedicated 0700 directory and 0600 Unix socket.
The directory access needed by macOS includes rights to change its entries,
so it must contain only the approval socket. The endpoint checks the peer UID
on every connection, in addition to the per-operation approval token.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

DIRECTORY_GRANTS = "list,search,add_file,delete_child,readattr,readextattr,readsecurity"
SOCKET_GRANTS = "read,write"


def platform_supported() -> bool:
    """Named filesystem ACLs are only implemented for macOS here."""
    return sys.platform == "darwin"


def _invoke(command: list[str], runner: Any = None):
    call = runner or subprocess.run
    return call(command, capture_output=True, text=True,
                encoding="utf-8", timeout=10)


def _signed_uid(uid: int) -> int:
    value = int(uid)
    return value - (1 << 32) if value >= (1 << 31) else value


def account_name(uid: int) -> str:
    """Resolve the name shown by ``ls -lde``.

    macOS stores nobody with an unsigned 32-bit UID while peer credentials
    may use the signed form, so try both and fall back to scanning the account
    database. A numeric string is not accepted by ``chmod +a``.
    """
    candidates = {int(uid), _signed_uid(uid)}
    try:
        import pwd
        for candidate in candidates:
            try:
                return pwd.getpwuid(candidate).pw_name
            except KeyError:
                continue
        for entry in pwd.getpwall():
            if entry.pw_uid in candidates:
                return entry.pw_name
    except (ImportError, OSError):
        pass
    return str(uid)


def grant_approver_access(directory: Path, socket_path: Path, approver_uid: int,
                          *, runner: Any = None) -> dict:
    """Grant one approver account named access to the directory and socket."""
    if not platform_supported():
        return {"ok": False, "supported": False, "error": "acl_platform_unsupported"}
    name = account_name(approver_uid)
    commands = [
        ["chmod", "+a", f"user:{name} allow {SOCKET_GRANTS}", str(socket_path)],
        ["chmod", "+a", f"user:{name} allow {DIRECTORY_GRANTS}", str(directory)],
    ]
    steps = []
    for command in commands:
        completed = _invoke(command, runner)
        steps.append({"target": "directory" if command[-1] == str(directory) else "socket",
                      "returncode": completed.returncode,
                      "stderr": (completed.stderr or "").strip()[-300:]})
        if completed.returncode != 0:
            return {"ok": False, "supported": True, "error": "acl_grant_failed",
                    "steps": steps}
    return {"ok": True, "supported": True, "approver_uid": int(approver_uid),
            "approver_name": name, "steps": steps}


def describe_acl(path: Path, *, runner: Any = None) -> str:
    """Return the platform's ACL listing for verification and evidence."""
    if not platform_supported():
        return ""
    result = _invoke(["ls", "-lde", str(path)], runner)
    if result.returncode != 0:
        raise PermissionError(f"Cannot inspect the ACL for {path}")
    return result.stdout or ""


def _acl_entries(path: Path, runner: Any) -> list[str]:
    return re.findall(r"^\s*\d+:\s+(.+)$", describe_acl(path, runner=runner), re.MULTILINE)


def require_dedicated_directory(directory: Path, *, runner: Any = None) -> None:
    if not platform_supported():
        raise PermissionError("Hosted approval ACLs require macOS.")
    if next(directory.iterdir(), None) is not None:
        raise PermissionError("Hosted approval requires a dedicated empty socket directory.")
    if _acl_entries(directory, runner):
        raise PermissionError("Hosted approval requires a socket directory without existing ACLs.")


def revoke_approver_directory_access(directory: Path, approver_uid: int,
                                     *, runner: Any = None) -> None:
    """Revoke the directory grant, including after a partially failed startup."""
    name = account_name(approver_uid)
    result = _invoke(["chmod", "-a", f"user:{name} allow {DIRECTORY_GRANTS}",
                      str(directory)], runner)
    if result.returncode != 0 and _acl_entries(directory, runner):
        raise PermissionError(f"Cannot remove the hosted approval ACL from {directory}")


def verify_acl(path: Path, approver_uid: int, *, runner: Any = None) -> dict:
    """Check whether the ACL listing includes an allow entry for the approver."""
    if not platform_supported():
        return {"ok": False, "supported": False, "error": "acl_platform_unsupported"}
    text = describe_acl(path, runner=runner)
    name = account_name(approver_uid)
    principals = set(re.findall(r"^\s*\d+:\s+(\S+)\s+allow\s", text, re.MULTILINE))
    found = bool(principals & {f"user:{name}", f"user:{int(approver_uid)}"})
    if not found and any(re.fullmatch(r"[\da-fA-F-]{36}", p) for p in principals):
        # macOS can print a principal's UUID when the display name is unavailable.
        uuid = _invoke(["/usr/bin/dsmemberutil", "getuuid", "-U", name], runner)
        found = uuid.returncode == 0 and uuid.stdout.strip().lower() in {
            principal.lower() for principal in principals}
    return {"ok": found, "supported": True, "approver_uid": int(approver_uid),
            "entry_found": found, "listing": text.strip()[:500],
            "error": None if found else "acl_entry_missing"}


def verify_layout(directory: Path, socket_path: Path, *, service_uid: int) -> dict:
    """Check the hosted layout without trusting ACL claims from the client.

    The socket and its parent must be owned by the service account and keep
    the private POSIX modes used by the hosted server.
    """
    try:
        parent = directory.lstat()
        info = socket_path.lstat()
    except OSError as exc:
        return {"ok": False, "problems": ["layout_unavailable"], "detail": type(exc).__name__}
    problems = []
    if not stat.S_ISDIR(parent.st_mode):
        problems.append("parent_not_directory")
    if parent.st_uid != int(service_uid):
        problems.append("parent_owner_mismatch")
    if stat.S_IMODE(parent.st_mode) != 0o700:
        problems.append("parent_mode_not_private")
    if not stat.S_ISSOCK(info.st_mode):
        problems.append("not_a_socket")
    if info.st_uid != int(service_uid):
        problems.append("socket_owner_mismatch")
    if stat.S_IMODE(info.st_mode) != 0o600:
        problems.append("socket_mode_not_private")
    return {"ok": not problems, "problems": problems,
            "parent_mode": oct(stat.S_IMODE(parent.st_mode)),
            "socket_mode": oct(stat.S_IMODE(info.st_mode)),
            "service_uid": int(service_uid)}
