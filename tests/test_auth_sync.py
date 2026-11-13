import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "codex_auth_sync", Path(__file__).resolve().parents[1] / "scripts/codex_auth_sync.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class AuthSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.auth = Path(self.temp.name) / "auth.json"
        self.state = Path(self.temp.name) / "identity"
        self.command = ["codex", "app-server", "daemon", "restart"]
        self.login("old", "owner-old")
        self.assertEqual(self.tick(), "initialized")

    def login(self, account, owner, token="secret-token"):
        self.auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
            "account_id": account, "login_owner_id": owner, "access_token": token,
            "refresh_token": "secret-refresh"}}), encoding="utf-8")

    def tick(self):
        return sync.sync_once(self.auth, self.state, self.command, settle_seconds=0)

    def test_failed_restart_remains_pending_and_is_retried(self):
        self.login("new", "owner-new")
        with mock.patch.object(sync.subprocess, "run", side_effect=[
                subprocess.CompletedProcess(self.command, 1),
                subprocess.CompletedProcess(self.command, 0)]) as run:
            self.assertEqual(self.tick(), "retry")
            self.assertEqual(sync.load_state(self.state), ("old", "owner-old"))
            self.assertEqual(self.tick(), "restarted")
            self.assertEqual(self.tick(), "unchanged")
            self.assertEqual(run.call_count, 2)
        self.assertEqual(sync.load_state(self.state), ("new", "owner-new"))

    def test_timeout_and_missing_binary_do_not_acknowledge_login(self):
        self.login("new", "owner-new")
        for error in [subprocess.TimeoutExpired(self.command, 120), OSError("missing")]:
            with self.subTest(error=type(error).__name__), mock.patch.object(
                    sync.subprocess, "run", side_effect=error):
                self.assertEqual(self.tick(), "retry")
                self.assertEqual(sync.load_state(self.state), ("old", "owner-old"))

    def test_partial_auth_write_does_not_forget_previous_login(self):
        self.auth.write_text('{"tokens":', encoding="utf-8")
        with mock.patch.object(sync.subprocess, "run") as run:
            self.assertEqual(self.tick(), "unavailable")
            self.assertEqual(sync.load_state(self.state), ("old", "owner-old"))
            run.assert_not_called()

    def test_refresh_does_not_restart_but_same_account_relogin_does(self):
        self.login("old", "owner-old", token="refreshed-token")
        with mock.patch.object(sync.subprocess, "run", return_value=
                               subprocess.CompletedProcess(self.command, 0)) as run:
            self.assertEqual(self.tick(), "unchanged")
            run.assert_not_called()
            self.login("old", "new-login-owner")
            self.assertEqual(self.tick(), "restarted")
            run.assert_called_once()

    def test_login_changes_during_debounce_are_rechecked(self):
        self.login("new", "owner-new")
        with mock.patch.object(sync.time, "sleep", side_effect=lambda _: self.login(
                "another", "owner-another")), mock.patch.object(sync.subprocess, "run") as run:
            self.assertEqual(self.tick(), "changed_again")
            run.assert_not_called()
        self.assertEqual(sync.load_state(self.state), ("old", "owner-old"))

    def test_logs_and_state_never_include_tokens_or_daemon_output(self):
        self.login("new", "owner-new")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sync.subprocess, "run",
                return_value=subprocess.CompletedProcess(self.command, 0,
                    stdout="secret-token", stderr="secret-refresh")):
            self.assertEqual(self.tick(), "restarted")
        combined = out.getvalue() + self.state.read_text(encoding="utf-8")
        self.assertNotIn("secret-token", combined)
        self.assertNotIn("secret-refresh", combined)


if __name__ == "__main__":
    unittest.main()
