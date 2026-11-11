import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from phonebridge.approvals import ApprovalEndpoint, approval_request
from phonebridge.core import PhoneBridge
from phonebridge.hosted import (account_name, grant_approver_access, platform_supported,
                                verify_acl, verify_layout)


OTHER_UID = 4294967294  # macOS nobody, reported as an unsigned 32-bit uid
TOKEN = "unit-test-token"


def ok_runner(command, **kw):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def failing_runner(command, **kw):
    return SimpleNamespace(returncode=1, stdout="", stderr="grant denied")


class HostedApprovalTests(unittest.TestCase):
    def setUp(self):
        if not platform_supported():
            self.skipTest("named filesystem ACLs are only implemented on macOS")
        self.t = tempfile.TemporaryDirectory(prefix="pb-hosted-")
        self.directory = Path(self.t.name)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "approval.sock"

    def tearDown(self):
        self.t.cleanup()

    def endpoint(self, path=None, **kw):
        params = dict(token=TOKEN, required_uid=OTHER_UID,
                      peer_uid_fn=lambda conn: OTHER_UID,
                      hosted=True, acl_runner=ok_runner)
        params.update(kw)
        return ApprovalEndpoint(PhoneBridge(), str(path or self.path), **params)

    def proposal(self, bridge):
        bridge.open_app("messaging")
        bridge.open_chat("contact-01")
        bridge.tap(.5, .9)
        bridge.type_text("hello")
        return bridge.prepare_message("contact-01", "hello")["approval_id"]

    def test_hosted_requires_a_different_uid_and_a_token(self):
        with self.assertRaises(PermissionError):
            self.endpoint(required_uid=None).start()
        with self.assertRaises(PermissionError):
            self.endpoint(token=None).start()
        with self.assertRaises(PermissionError):
            self.endpoint(required_uid=os.getuid()).start()

    def test_hosted_fails_closed_when_the_acl_grant_fails(self):
        with self.assertRaises(PermissionError):
            self.endpoint(acl_runner=failing_runner).start()
        self.assertFalse(self.path.exists())

    def test_hosted_upgrades_isolation_without_weakening_modes(self):
        endpoint = self.endpoint().start()
        try:
            self.assertEqual(endpoint.isolation_mode, "token+external_uid")
            self.assertTrue(endpoint.isolated)
            self.assertTrue(endpoint.acl_status["ok"])
            self.assertTrue(endpoint.bridge.approval_isolated)
            self.assertEqual(endpoint.bridge.approval_isolation_mode, "token+external_uid")
            self.assertEqual(stat.S_IMODE(self.path.lstat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.directory.lstat().st_mode), 0o700)
        finally:
            endpoint.close()

    def test_peer_uid_and_token_are_checked_on_every_connection(self):
        endpoint = self.endpoint().start()
        try:
            listing = approval_request(str(self.path), "list", token=TOKEN, hosted=True)
            self.assertTrue(listing["ok"])
            denied = approval_request(str(self.path), "list", token="wrong", hosted=True)
            self.assertEqual(denied["error"], "approval_token_required")
        finally:
            endpoint.close()
        wrong_peer = self.endpoint(path=self.directory / "peer.sock",
                                   peer_uid_fn=lambda conn: 12345).start()
        try:
            rejected = approval_request(str(self.directory / "peer.sock"), "list",
                                        token=TOKEN, hosted=True)
            self.assertEqual(rejected["error"], "approval_peer_not_trusted")
        finally:
            wrong_peer.close()

    def test_non_hosted_peer_uid_stays_unverified(self):
        endpoint = ApprovalEndpoint(PhoneBridge(), str(self.path),
                                    required_uid=OTHER_UID,
                                    peer_uid_fn=lambda conn: OTHER_UID).start()
        try:
            self.assertEqual(endpoint.isolation_mode, "external_uid_unverified")
            self.assertFalse(endpoint.bridge.approval_isolated)
        finally:
            endpoint.close()

    def test_hosted_client_rejects_a_wrong_socket_owner(self):
        endpoint = self.endpoint().start()
        try:
            with self.assertRaises(ValueError):
                approval_request(str(self.path), "list", token=TOKEN, hosted=True,
                                 socket_owner_uid=OTHER_UID)
        finally:
            endpoint.close()

    def test_hosted_client_rejects_a_world_writable_parent(self):
        endpoint = self.endpoint().start()
        try:
            os.chmod(self.directory, 0o702)
            with self.assertRaises(ValueError):
                approval_request(str(self.path), "list", token=TOKEN, hosted=True)
        finally:
            os.chmod(self.directory, 0o700)
            endpoint.close()

    def test_verify_acl_matches_only_the_named_allow_entry(self):
        name = account_name(OTHER_UID)
        listing = f"drwx------@ 2 owner wheel 64 Nov 11 00:00 /tmp/x\n 0: user:{name} allow list,search"

        def listed(command, **kw):
            return SimpleNamespace(returncode=0, stdout=listing, stderr="")

        self.assertTrue(verify_acl(self.directory, OTHER_UID, runner=listed)["ok"])
        empty = lambda command, **kw: SimpleNamespace(returncode=0, stdout="", stderr="")
        self.assertFalse(verify_acl(self.directory, OTHER_UID, runner=empty)["ok"])

    def test_grant_commands_only_name_one_approver(self):
        commands = []

        def recording(command, **kw):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        result = grant_approver_access(self.directory, self.path, OTHER_UID, runner=recording)
        self.assertTrue(result["ok"])
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(f"user:{account_name(OTHER_UID)} allow" in " ".join(c) for c in commands))
        self.assertFalse(any("everyone" in " ".join(c) for c in commands))

    def test_verify_layout_requires_socket_owned_by_service_account(self):
        endpoint = self.endpoint().start()
        try:
            self.assertTrue(verify_layout(self.directory, self.path,
                                          service_uid=os.getuid())["ok"])
            layout = verify_layout(self.directory, self.path, service_uid=OTHER_UID)
            self.assertFalse(layout["ok"])
            self.assertIn("socket_owner_mismatch", layout["problems"])
        finally:
            endpoint.close()

    def test_client_returns_structured_error_when_peer_closes_early(self):
        from unittest import mock
        from phonebridge import approvals as approvals_module
        endpoint = self.endpoint().start()
        try:
            with mock.patch.object(approvals_module, "write_message",
                                   side_effect=BrokenPipeError):
                result = approval_request(str(self.path), "list", token=TOKEN, hosted=True)
            self.assertEqual(result["error"], "approval_channel_unavailable")
        finally:
            endpoint.close()


if __name__ == "__main__":
    unittest.main()
