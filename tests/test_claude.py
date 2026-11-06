import copy
import json
from pathlib import Path
import shlex
import tempfile
import unittest

from tidbyt_agent_alerts import claude
from tidbyt_agent_alerts.config import AlertError


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "settings.json"
        self.original = {"theme": "dark", "hooks": {
            "Notification": [{"matcher": "*", "hooks": [{"type": "command", "command": "my-hook"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "other-hook"}]}],
        }}
        self.path.write_text(json.dumps(self.original))

    def install(self, events="both"):
        return claude.install(self.path, Path("/a path/it's/bin/tidbyt-agent-alerts"), Path("/private/my config.env"), events)

    def test_preserves_existing_settings_and_makes_backup(self):
        self.assertTrue(self.install())
        result = json.loads(self.path.read_text())
        self.assertEqual(result["theme"], "dark")
        self.assertEqual(result["hooks"]["Notification"][0], self.original["hooks"]["Notification"][0])
        self.assertEqual(result["hooks"]["Stop"][0], self.original["hooks"]["Stop"][0])
        backups = list(self.path.parent.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), self.original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_idempotent_install_and_quoted_paths(self):
        self.install()
        text = self.path.read_text()
        self.assertFalse(self.install())
        self.assertEqual(self.path.read_text(), text)
        settings = json.loads(text)
        hook = settings["hooks"]["Notification"][-1]["hooks"][0]
        self.assertTrue(hook["async"])
        self.assertEqual(shlex.split(hook["command"]), ["/a path/it's/bin/tidbyt-agent-alerts", "--config", "/private/my config.env", "hook", "claude"])

    def test_switch_event_selection_removes_old_managed_entries(self):
        self.install()
        self.install("attention")
        result = json.loads(self.path.read_text())
        self.assertEqual(len(result["hooks"]["Stop"]), 1)
        self.assertEqual(len(result["hooks"]["Notification"]), 2)
        self.assertEqual(result["hooks"]["Notification"][-1]["matcher"], "permission_prompt|idle_prompt")

    def test_uninstall_preserves_unrelated_changes_after_install(self):
        self.install()
        changed = json.loads(self.path.read_text())
        changed["newSetting"] = True
        self.path.write_text(json.dumps(changed))
        claude.uninstall(self.path)
        self.assertEqual(json.loads(self.path.read_text()), self.original | {"newSetting": True})
        self.assertFalse(claude.uninstall(self.path))

    def test_install_into_missing_settings_and_uninstall(self):
        self.path.unlink()
        self.install("complete")
        self.assertEqual(list(json.loads(self.path.read_text())["hooks"]), ["Stop"])
        claude.uninstall(self.path)
        self.assertEqual(json.loads(self.path.read_text()), {})

    def test_malformed_settings_are_not_overwritten(self):
        for value in ("{", "[]", '{"hooks":[]}', '{"hooks":{"Stop":{}}}', '{"hooks":{"Stop":[4]}}'):
            self.path.write_text(value)
            with self.subTest(value=value), self.assertRaises(AlertError):
                self.install()
            self.assertEqual(self.path.read_text(), value)

    def test_symlinked_settings_are_not_modified(self):
        target = self.path.parent / "target.json"
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(AlertError, "symlink"):
            self.install()

    def test_events_ignore_private_payload_fields(self):
        payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                   "cwd": "/Users/owner/private/my-project", "message": "SECRET body",
                   "transcript_path": "/SECRET/path", "prompt": "SECRET prompt"}
        original = copy.deepcopy(payload)
        result = claude.event(payload)
        self.assertEqual(result, {"source": "claude", "event": "attention", "project": "my-project", "message": "my-project: needs your attention"})
        self.assertEqual(payload, original)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_stop_semantics_and_recursion_guard(self):
        result = claude.event({"hook_event_name": "Stop", "cwd": "/project"})
        self.assertEqual(result["message"], "project: finished responding")
        self.assertIsNone(claude.event({"hook_event_name": "Stop", "stop_hook_active": True}))

    def test_unrelated_or_invalid_events_ignored(self):
        for payload in (None, [], {}, {"hook_event_name": "PreToolUse"},
                        {"hook_event_name": "Notification", "notification_type": "auth_success"}):
            self.assertIsNone(claude.event(payload))
