#!/usr/bin/env python3
"""Run the Hark/Codex spike over a temporary localhost SSH server.

This wrapper does not change macOS Remote Login or the user's SSH config. It
creates an ephemeral host key, client key, authorized_keys file and sshd
config, verifies SSH connectivity, then invokes ``hark-codex-spike.py`` with
``--transport ssh``. It exists so the SSH reconnect path is reproducible while
the real Hark iOS client is unavailable.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", **kwargs)


def find_sshd() -> str | None:
    for candidate in ("/usr/sbin/sshd", "/usr/bin/sshd"):
        if os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("sshd")


def sshd_options(work: Path, authorized: Path) -> list[str]:
    # ChallengeResponseAuthentication is rejected by OpenSSH versions that
    # dropped the keyword. It is included for older sshd and removed below
    # when `sshd -t` reports it as a bad option.
    return [
        "ListenAddress 127.0.0.1",
        f"HostKey {work / 'hostkey'}",
        f"PidFile {work / 'sshd.pid'}",
        f"AuthorizedKeysFile {authorized}",
        "PubkeyAuthentication yes",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "ChallengeResponseAuthentication no",
        "UsePAM no",
        "StrictModes yes",
        f"AllowUsers {getpass.getuser()}",
        "LogLevel VERBOSE",
    ]


def write_sshd_config(path: Path, options: list[str], rejected: set[str]) -> None:
    kept = [line for line in options if line.split()[0] not in rejected]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def start_sshd(binary: str, config: Path, port: int, options: list[str]):
    """Start a key-only sshd, dropping config keywords this build rejects."""
    rejected: set[str] = set()
    stderr_text = ""
    process = None
    for _ in range(len(options) + 1):
        write_sshd_config(config, options, rejected)
        probe = run([binary, "-t", "-f", str(config), "-p", str(port)], timeout=10)
        probe_text = (probe.stderr or "") + (probe.stdout or "")
        bad = re.search(r"(?:Bad configuration option|Unsupported option): (\S+)", probe_text)
        if bad and bad.group(1) not in rejected:
            rejected.add(bad.group(1))
            continue
        log_path = config.with_name("sshd.err")
        log_file = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen([binary, "-D", "-e", "-f", str(config), "-p", str(port)],
                                   stdout=subprocess.DEVNULL, stderr=log_file)
        process._sshd_log = log_file  # keep the handle alive for the daemon
        time.sleep(0.2)
        log_file.flush()
        stderr_text = log_path.read_text(encoding="utf-8", errors="replace")
        if process.poll() is None:
            return process, stderr_text
        if not bad:
            return process, stderr_text or probe_text
    return process, stderr_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-timeout", type=int, default=300)
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--ssh-precheck-only", action="store_true",
                        help="Only verify the temporary key-only SSH transport")
    parser.add_argument("--hark-spike-arg", action="append", default=[],
                        help="Additional argument passed to hark-codex-spike.py")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="pb-hark-loopback-"))
    os.chmod(work, 0o700)
    sshd = None
    try:
        for name in ("hostkey", "clientkey"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(work / name)],
                           check=True, timeout=20)
        authorized = work / "authorized_keys"
        authorized.write_text((work / "clientkey.pub").read_text(encoding="utf-8"), encoding="utf-8")
        authorized.chmod(0o600)
        port = free_port()
        config = work / "sshd_config"
        sshd_binary = find_sshd()
        if sshd_binary is None:
            print(json.dumps({
                "host_chain_passed": False,
                "ssh_loopback_precheck": False,
                "blocker": "sshd_not_installed",
                "error": "No sshd binary on PATH or in /usr/sbin",
            }, ensure_ascii=False, indent=2))
            return 1
        sshd, sshd_stderr = start_sshd(sshd_binary, config, port, sshd_options(work, authorized))
        known_hosts = work / "known_hosts"
        keyscan_output = ""
        for _ in range(100):
            if sshd.poll() is not None:
                break
            keyscan = run(["ssh-keyscan", "-T", "3", "-p", str(port), "127.0.0.1"], timeout=5)
            keyscan_output = keyscan.stdout
            if " ssh-" in keyscan_output:
                break
            time.sleep(0.05)
        known_hosts.write_text(keyscan_output, encoding="utf-8")

        base_ssh = [
            "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
            "-o", f"Port={port}", "-i", str(work / "clientkey"), "-o", "IdentitiesOnly=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "127.0.0.1",
        ]
        precheck = None
        for _ in range(200):
            if sshd.poll() is not None:
                break
            precheck = run(base_ssh + ["/usr/bin/true"], timeout=8)
            if precheck.returncode == 0:
                break
            time.sleep(0.1)
        if precheck is None or precheck.returncode != 0:
            print(json.dumps({
                "host_chain_passed": False,
                "ssh_loopback_precheck": False,
                "error": "temporary localhost sshd did not accept key-based SSH",
                "sshd": sshd_binary,
                "sshd_exit": None if sshd is None else sshd.poll(),
                "stderr": (precheck.stderr if precheck else "")[-1000:],
                "sshd_stderr": sshd_stderr[-1000:],
            }, ensure_ascii=False, indent=2))
            return 1
        if args.ssh_precheck_only:
            print(json.dumps({
                "host_chain_passed": False,
                "partial_chain_passed": False,
                "chain_scope": "ssh_precheck",
                "ssh_loopback_precheck": True,
                "ssh_loopback_port": port,
                "ssh_loopback_command": "127.0.0.1 key-only ephemeral sshd",
                "note": "SSH transport verified; Codex/Hark chain not run",
            }, ensure_ascii=False, indent=2))
            return 0

        command = [
            sys.executable, str(ROOT / "scripts" / "hark-codex-spike.py"),
            "--transport", "ssh",
            "--ssh-destination", "127.0.0.1",
            "--ssh-port", str(port),
            "--ssh-key", str(work / "clientkey"),
            "--ssh-known-hosts", str(known_hosts),
            "--phase-timeout", str(args.phase_timeout),
            *args.hark_spike_arg,
        ]
        if args.keep_artifacts:
            command.append("--keep-artifacts")
        timeout = args.phase_timeout * 5 + 240
        try:
            child = subprocess.run(command, capture_output=True, text=True,
                                   encoding="utf-8", timeout=timeout, cwd=ROOT)
        except subprocess.TimeoutExpired as exc:
            print(json.dumps({
                "host_chain_passed": False,
                "ssh_loopback_precheck": True,
                "error": f"hark spike timed out after {timeout}s",
                "stdout_tail": ((exc.stdout or "") if isinstance(exc.stdout, str) else "")[-1000:],
                "stderr_tail": ((exc.stderr or "") if isinstance(exc.stderr, str) else "")[-1000:],
            }, ensure_ascii=False, indent=2))
            return 1
        try:
            payload = json.loads(child.stdout)
        except ValueError:
            payload = {"host_chain_passed": False, "raw_stdout": child.stdout[-2000:],
                       "stderr": child.stderr[-2000:]}
        payload["ssh_loopback_precheck"] = True
        payload["ssh_loopback_port"] = port
        payload["ssh_loopback_command"] = "127.0.0.1 key-only ephemeral sshd"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return int(child.returncode)
    finally:
        if sshd is not None and sshd.poll() is None:
            sshd.terminate()
            try:
                sshd.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sshd.kill()
        if args.keep_artifacts:
            print(f"Loopback artifacts kept at {work}", file=sys.stderr)
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
