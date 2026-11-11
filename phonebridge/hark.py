"""Host-side helpers for Hark -> SSH -> Codex -> PhoneBridge MCP runs.

The helpers deliberately keep the approval token out of Codex's environment
and command line. Codex may call the synthetic MCP tools, while the trusted
side keeps ledger approval in the local approval CLI.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CODEX_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
    "/Applications/Codex.app/Contents/Resources/codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
)


@dataclass
class CodexToolCall:
    tool: str
    arguments: dict
    payload: dict | None
    error: str | None
    server: str | None = None


@dataclass
class CodexRun:
    returncode: int
    stderr: str
    events: list[dict] = field(default_factory=list)
    tool_calls: list[CodexToolCall] = field(default_factory=list)
    agent_messages: list[str] = field(default_factory=list)


def find_codex(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit if Path(explicit).is_file() else None
    for candidate in CODEX_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return shutil.which("codex")


def payload_from_result(result: Any) -> dict | None:
    if not isinstance(result, dict):
        return None
    structured = result.get("structuredContent", result.get("structured_content"))
    if isinstance(structured, dict):
        return structured
    for content in result.get("content", []):
        if not isinstance(content, dict) or content.get("type") != "text":
            continue
        try:
            payload = json.loads(content.get("text", ""))
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def parse_codex_output(stdout: str) -> CodexRun:
    parsed = CodexRun(returncode=0, stderr="")
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        parsed.events.append(event)
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed":
            continue
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            parsed.agent_messages.append(item["text"])
        if item.get("type") != "mcp_tool_call":
            continue
        error = item.get("error")
        parsed.tool_calls.append(CodexToolCall(
            tool=str(item.get("tool") or ""),
            arguments=item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
            payload=payload_from_result(item.get("result")),
            error=str(error) if error else None,
            server=item.get("server") if isinstance(item.get("server"), str) else None,
        ))
    return parsed


def run_codex(command: list[str], env: dict[str, str], timeout: int = 300) -> CodexRun:
    completed = subprocess.run(command, capture_output=True, text=True,
                               encoding="utf-8", env=env, timeout=timeout)
    parsed = parse_codex_output(completed.stdout)
    parsed.returncode = completed.returncode
    parsed.stderr = completed.stderr
    return parsed


def relay_stdio_command(python: str, socket_path: str) -> list[str]:
    return [python, "-m", "phonebridge.relay", "stdio", "--socket", socket_path]


def ssh_wrap(command: list[str], destination: str, *, port: int | None = None,
             key_path: str | None = None, known_hosts: str | None = None,
             extra_options: list[str] | None = None) -> list[str]:
    wrapped = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if port is not None:
        wrapped += ["-p", str(port)]
    if key_path:
        wrapped += ["-i", key_path, "-o", "IdentitiesOnly=yes"]
    if known_hosts:
        wrapped += ["-o", f"UserKnownHostsFile={known_hosts}"]
    for option in extra_options or []:
        wrapped += ["-o", option]
    wrapped += [destination, shlex.join(command)]
    return wrapped


def build_codex_command(codex_path: str, python: str, repo_root: str,
                        socket_path: str, prompt: str, workspace: str,
                        trusted_project: str | None = None) -> list[str]:
    relay_args = relay_stdio_command(python, socket_path)
    command = [
        codex_path, "exec", "--json", "--ephemeral", "--approve-for-me",
        "--ignore-rules", "--skip-git-repo-check", "-C", workspace,
        "-c", "mcp_servers.phonebridge.enabled=true",
        "-c", "mcp_servers.phonebridge.startup_timeout_sec=30",
        "-c", "mcp_servers.phonebridge.tool_timeout_sec=120",
        "-c", f"mcp_servers.phonebridge.command={json.dumps(python)}",
        "-c", "mcp_servers.phonebridge.args=" + json.dumps(relay_args),
        "-c", f"mcp_servers.phonebridge.cwd={json.dumps(repo_root)}",
    ]
    if trusted_project is not None:
        command += ["-c", f"projects.{json.dumps(trusted_project)}.trust_level=\"trusted\""]
    command.append(prompt)
    return command


def codex_env(base: dict[str, str] | None = None, *, approval_token_env: str) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env.pop(approval_token_env, None)
    return env
