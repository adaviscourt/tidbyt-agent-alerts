import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

from tidbyt_agent_alerts import cli, integrations
from tidbyt_agent_alerts.config import AlertError


class IntegrationTests(unittest.TestCase):
    def test_codex_events_do_not_forward_private_fields(self):
        payload = {"hook_event_name": "PermissionRequest", "cwd": "/work/demo",
                   "tool_input": {"command": "SECRET"}, "last_assistant_message": "SECRET"}
        event = cli.codex_event(payload)
        self.assertEqual(event["event"], "attention")
        self.assertEqual(event["project"], "demo")
        self.assertNotIn("SECRET", str(event))
        payload["hook_event_name"] = "Stop"
        self.assertEqual(cli.codex_event(payload)["event"], "complete")
        payload["stop_hook_active"] = True
        self.assertIsNone(cli.codex_event(payload))
        self.assertIsNone(cli.codex_event([]))

    @patch("tidbyt_agent_alerts.cli.notify")
    @patch("tidbyt_agent_alerts.cli.load", return_value={})
    def test_codex_hook_returns_json_and_never_leaks_payload(self, load, notify):
        payload = {"hook_event_name": "PermissionRequest", "cwd": "/work/demo", "tool_input": "SECRET"}
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(payload))), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["hook", "codex"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {})
        self.assertNotIn("SECRET", str(notify.call_args))
        self.assertEqual(err.getvalue(), "")

    def test_assets_exist(self):
        assets = Path(cli.__file__).parent / "assets"
        for name in ("codex-loop.gif", "opencode.png", "opencode-plugin/index.js"):
            self.assertTrue((assets / name).is_file())

    def test_codex_preserves_user_hooks_and_updates_owned_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hooks.json"
            original = {"description": "keep", "hooks": {"Stop": [{"hooks": [{"command": "other"}]}]}}
            path.write_text(json.dumps(original))
            integrations.codex_install(path, Path("/prefix with space/launcher"), Path("/private config"), "both")
            data = json.loads(path.read_text())
            self.assertEqual(data["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
            self.assertIn("'/private config'", data["hooks"]["Stop"][1]["hooks"][0]["command"])
            self.assertFalse(integrations.codex_install(path, Path("/prefix with space/launcher"), Path("/private config"), "both"))
            integrations.codex_install(path, Path("/launcher"), Path("/config"), "attention")
            self.assertEqual(len(json.loads(path.read_text())["hooks"]["Stop"]), 1)
            integrations.codex_uninstall(path)
            self.assertEqual(json.loads(path.read_text()), original)

    def test_opencode_refuses_unowned_or_symlink_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.js"
            path.write_text("user plugin")
            with self.assertRaises(AlertError):
                integrations.opencode_install(path, Path("/launcher"), Path("/config"), "both")
            self.assertEqual(path.read_text(), "user plugin")
            path.unlink()
            self.assertTrue(integrations.opencode_install(path, Path("/launcher"), Path("/config"), "both"))
            self.assertIn('"events": "both"', path.read_text())
            self.assertFalse(integrations.opencode_install(path, Path("/launcher"), Path("/config"), "both"))
            integrations.opencode_uninstall(path)
            path.symlink_to(Path(directory) / "elsewhere")
            with self.assertRaises(AlertError):
                integrations.opencode_install(path, Path("/launcher"), Path("/config"), "both")
