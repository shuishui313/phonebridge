"""CLI and stdio entrypoint. No network services are opened."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .api import CoreAPI
from .approvals import ApprovalEndpoint, approval_request
from .core import PhoneBridge
from .drivers import AppleSimulatorDriver, MockIPhone
from .mcp_v04 import MCPServer
from .relay import serve_backend
from .submissions import SubmissionLedger
from .xcode_mcp import XcodeMCPDriver


_APPROVAL_OPTIONS_WITH_VALUE = {"--token-env", "--socket-owner-uid"}


def peel_dashed_approval_id(argv: list[str]) -> tuple[list[str], str | None]:
    """Pull an operation id that starts with '-' out before argparse sees it.

    ``secrets.token_urlsafe`` emits '-' often enough that ``approvals approve
    <id>`` otherwise exits 2 before the approval socket is contacted. A bare
    ``--`` terminator does not survive this parser once another option follows
    the subcommand, so the id is removed and restored after parsing.
    """
    if "approvals" not in argv:
        return argv, None
    index = argv.index("approvals")
    rest = argv[index + 1:]
    if not rest or rest[0] not in {"approve", "deny", "reconcile"}:
        return argv, None
    rebuilt = [rest[0]]
    peeled = None
    cursor = 1
    while cursor < len(rest):
        token = rest[cursor]
        if token in _APPROVAL_OPTIONS_WITH_VALUE:
            rebuilt.extend(rest[cursor:cursor + 2])
            cursor += 2
            continue
        if peeled is None and token.startswith("-") and not token.startswith("--"):
            peeled = token
            cursor += 1
            continue
        rebuilt.append(token)
        cursor += 1
    if peeled is None:
        return argv, None
    return [*argv[:index + 1], *rebuilt], peeled


def main(argv=None):
    p = argparse.ArgumentParser(prog="phonebridge")
    p.add_argument("--approval-socket", help="Private Unix approval socket; parent directory MUST have chmod 700")
    p.add_argument("--driver", choices=["mock", "simctl", "xcode-mcp"], default="mock")
    p.add_argument("--sim-udid", help="Apple Simulator UDID for device drivers")
    p.add_argument("--allow-xcode-interactions", action="store_true",
                   help="Human opt-in at process start: allow tap/swipe/type/home through Xcode MCP")
    p.add_argument("--fixture-bundle", default="org.example.phonebridge.fixture",
                   help="Allowlisted fixture bundle ID for the Xcode MCP driver")
    p.add_argument("--bridge-timeout", type=float, default=15.0,
                   help="Hard timeout in seconds for Xcode MCP handshake and calls")
    p.add_argument("--session-id", help="Optional Xcode device interaction session identifier (default: unique)")
    p.add_argument("--task-timeout", type=float, default=300.0,
                   help="Task time limit in seconds from the first state-changing action")
    p.add_argument("--action-budget", type=int, default=40,
                   help="Maximum number of state-changing action attempts per task")
    p.add_argument("--max-read-retries", type=int, default=5,
                   help="Maximum consecutive identical read-only actions")
    p.add_argument("--max-retries", type=int, default=2,
                   help="Maximum consecutive failed attempts suggested for retry")
    p.add_argument("--audit-path", help="Private metadata-only JSONL audit file")
    p.add_argument("--submission-state", help="Private 0600 JSONL ledger in a 0700 directory")
    p.add_argument("--approval-token-env", help="Env var holding the trusted-side approval token")
    p.add_argument("--approval-uid", type=int,
                   help="Peer UID check; requires an externally hosted endpoint with OS ACLs")
    p.add_argument("--approval-hosted", action="store_true",
                   help="Externally hosted approval endpoint: grant one named approver UID "
                        "filesystem ACLs and verify the peer UID on every connection")
    p.add_argument("--require-approval-isolation", action="store_true",
                   help="Refuse to start unless an approval token and durable state are configured")
    p.add_argument("--allow-fixture-virtual-submit", action="store_true",
                   help="Human opt-in: let the approved submission gate create one synthetic "
                        "record inside the owned Simulator fixture. Never a real message send.")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("serve")
    backend = sub.add_parser("backend", help="Serve MCP on a private Unix socket for reconnecting clients")
    backend.add_argument("--socket", required=True, help="Private Unix socket path in a 0700 directory")
    ap = sub.add_parser("approvals")
    ap.add_argument("action", choices=["list", "approve", "deny", "reconcile"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--token-env", help="Env var holding the approval token")
    ap.add_argument("--socket-owner-uid", type=int, help="Socket owner UID for a separate approver account")
    raw = list(sys.argv[1:] if argv is None else argv)
    raw, dashed_operation_id = peel_dashed_approval_id(raw)
    args = p.parse_args(raw)
    if dashed_operation_id is not None:
        args.id = dashed_operation_id
    if args.command == "approvals":
        if not args.approval_socket:
            p.error("--approval-socket is required for approvals")
        if args.action != "list" and not args.id:
            p.error("operation ID required")
        token = os.environ.get(args.token_env) if args.token_env else None
        try:
            response = approval_request(args.approval_socket, args.action, args.id,
                                        token=token, socket_owner_uid=args.socket_owner_uid,
                                        hosted=args.approval_hosted)
        except (OSError, ValueError) as exc:
            print(f"Approval channel unavailable: {type(exc).__name__}", file=sys.stderr)
            return 2
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") else 1
    if args.driver in ("simctl", "xcode-mcp") and not args.sim_udid:
        p.error(f"--sim-udid required when --driver {args.driver}")
    if args.allow_xcode_interactions and args.driver != "xcode-mcp":
        p.error("--allow-xcode-interactions requires --driver xcode-mcp")
    token = os.environ.get(args.approval_token_env) if args.approval_token_env else None
    if args.approval_hosted:
        if not args.approval_socket:
            p.error("--approval-hosted needs --approval-socket")
        if args.approval_uid is None or args.approval_uid == os.getuid():
            p.error("--approval-hosted needs --approval-uid for an account different "
                    "from the service account")
        if not token:
            p.error("--approval-hosted needs an approval token in --approval-token-env")
        approval_directory = Path(args.approval_socket).resolve().parent
        for flag, path in (("--submission-state", args.submission_state),
                           ("--audit-path", args.audit_path),
                           ("backend --socket", getattr(args, "socket", None))):
            if path and Path(path).resolve().is_relative_to(approval_directory):
                p.error(f"{flag} must be outside the dedicated hosted approval directory")
    if args.require_approval_isolation:
        if not args.approval_socket:
            p.error("--require-approval-isolation needs --approval-socket")
        if not token:
            p.error("--require-approval-isolation needs a token in --approval-token-env; "
                    "--approval-uid alone needs --approval-hosted plus an OS ACL")
        if not args.submission_state:
            p.error("--require-approval-isolation needs durable --submission-state")
    if args.allow_fixture_virtual_submit:
        if args.driver != "xcode-mcp" or not args.allow_xcode_interactions:
            p.error("--allow-fixture-virtual-submit requires --driver xcode-mcp "
                    "and --allow-xcode-interactions")
        if not args.require_approval_isolation:
            p.error("--allow-fixture-virtual-submit requires --require-approval-isolation "
                    "(an approval token and a durable submission ledger)")
    driver = None
    try:
        ledger = SubmissionLedger(args.submission_state) if args.submission_state else None
        if args.driver == "mock":
            driver = MockIPhone()
        elif args.driver == "simctl":
            driver = AppleSimulatorDriver(args.sim_udid)
        else:
            driver = XcodeMCPDriver(args.sim_udid,
                                    interaction_approved=args.allow_xcode_interactions,
                                    fixture_bundle=args.fixture_bundle,
                                    timeout=args.bridge_timeout,
                                    session_identifier=args.session_id,
                                    virtual_submit_enabled=args.allow_fixture_virtual_submit)
        bridge = PhoneBridge(driver, audit_path=args.audit_path, ledger=ledger)
        recovery = bridge.recover_submissions()
        if recovery["recovered"] or recovery["pending_pause"]:
            print(json.dumps({"submission_recovery": recovery}, ensure_ascii=False), file=sys.stderr)
        api = CoreAPI(bridge, time_limit=args.task_timeout, action_budget=args.action_budget,
                      max_read_retries=args.max_read_retries, max_retries=args.max_retries)
        def serve_api():
            if args.command == "backend":
                serve_backend(api, args.socket)
            else:
                MCPServer(api).serve()

        if args.approval_socket:
            with ApprovalEndpoint(bridge, args.approval_socket, token=token,
                                  required_uid=args.approval_uid,
                                  hosted=args.approval_hosted,
                                  reconcile_submission=api.resume_after_readback):
                serve_api()
        else:
            serve_api()
    except BrokenPipeError:
        return 1
    except (OSError, ValueError) as exc:
        print(f"PhoneBridge: {exc}", file=sys.stderr)
        return 1
    finally:
        close = getattr(driver, "close", None)
        if callable(close):
            close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
