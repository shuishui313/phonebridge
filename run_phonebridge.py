import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

from phonebridge.mcp import serve_stdio
from phonebridge.model import PhoneBridge
from phonebridge.operator import approval_server, review_from_terminal


def main():
    parser = argparse.ArgumentParser(description="Run the PhoneBridge virtual iPhone MCP server.")
    parser.add_argument("--approval-socket", type=Path, help="Local socket in a private directory")
    commands = parser.add_subparsers(dest="command")
    approvals = commands.add_parser("approvals", help="Review proposals in the operator's terminal")
    actions = approvals.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    actions.add_parser("approve").add_argument("operation_id")
    actions.add_parser("deny").add_argument("operation_id")
    args = parser.parse_args()

    try:
        if args.command == "approvals":
            if args.approval_socket is None:
                parser.error("approvals requires --approval-socket")
            return review_from_terminal(
                args.approval_socket, args.action, getattr(args, "operation_id", None),
            )
        phone = PhoneBridge()
        channel = approval_server(args.approval_socket, phone) if args.approval_socket else nullcontext()
        with channel:
            serve_stdio(phone, sys.stdin.buffer, sys.stdout.buffer)
    except BrokenPipeError:
        return 1
    except (EOFError, OSError, ValueError) as error:
        print(f"PhoneBridge: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
