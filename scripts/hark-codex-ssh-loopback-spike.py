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
        config.write_text(
            f"ListenAddress 127.0.0.1\n"
            f"HostKey {work / 'hostkey'}\n"
            f"PidFile {work / 'sshd.pid'}\n"
            f"AuthorizedKeysFile {authorized}\n"
            "PasswordAuthentication no\n"
            "KbdInteractiveAuthentication no\n"
            "ChallengeResponseAuthentication no\n"
            "UsePAM no\n"
            "StrictModes yes\n"
            f"AllowUsers {getpass.getuser()}\n"
            "LogLevel VERBOSE\n",
            encoding="utf-8",
        )
        sshd = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config), "-p", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                "stderr": (precheck.stderr if precheck else "")[-1000:],
            }, ensure_ascii=False, indent=2))
            return 1
        if args.ssh_precheck_only:
            print(json.dumps({
                "host_chain_passed": True,
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
