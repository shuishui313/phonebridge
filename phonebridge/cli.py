"""CLI and stdio entrypoint. No network services are opened."""
from __future__ import annotations

import argparse
import json
import sys

from .api import CoreAPI
from .approvals import ApprovalEndpoint, approval_request
from .core import PhoneBridge
from .drivers import AppleSimulatorDriver, MockIPhone
from .mcp_v04 import MCPServer
from .xcode_mcp import XcodeMCPDriver


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
    sub = p.add_subparsers(dest="command")
    sub.add_parser("serve")
    ap = sub.add_parser("approvals")
    ap.add_argument("action", choices=["list", "approve", "deny"])
    ap.add_argument("id", nargs="?")
    args = p.parse_args(argv)
    if args.command == "approvals":
        if not args.approval_socket:
            p.error("--approval-socket is required for approvals")
        if args.action in ("approve", "deny") and not args.id:
            p.error("approval ID required")
        try:
            response = approval_request(args.approval_socket, args.action, args.id)
        except (OSError, ValueError) as exc:
            print(f"Approval channel unavailable: {type(exc).__name__}", file=sys.stderr)
            return 2
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") else 1
    if args.driver in ("simctl", "xcode-mcp") and not args.sim_udid:
        p.error(f"--sim-udid required when --driver {args.driver}")
    if args.allow_xcode_interactions and args.driver != "xcode-mcp":
        p.error("--allow-xcode-interactions requires --driver xcode-mcp")
    driver = None
    try:
        if args.driver == "mock":
            driver = MockIPhone()
        elif args.driver == "simctl":
            driver = AppleSimulatorDriver(args.sim_udid)
        else:
            driver = XcodeMCPDriver(args.sim_udid,
                                    interaction_approved=args.allow_xcode_interactions,
                                    fixture_bundle=args.fixture_bundle,
                                    timeout=args.bridge_timeout,
                                    session_identifier=args.session_id)
        bridge = PhoneBridge(driver, audit_path=args.audit_path)
        api = CoreAPI(bridge, time_limit=args.task_timeout, action_budget=args.action_budget,
                      max_read_retries=args.max_read_retries, max_retries=args.max_retries)
        if args.approval_socket:
            with ApprovalEndpoint(bridge, args.approval_socket):
                MCPServer(api).serve()
        else:
            MCPServer(api).serve()
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
