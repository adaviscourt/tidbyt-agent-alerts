from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tidbyt_agent_alerts import cli
from tidbyt_agent_alerts.config import AlertError, load, write_env
from test_config import settings


class CLITests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "config.env"
        self.claude_settings = self.root / "settings.json"
        self.flags = ["--config", str(self.config), "--claude-settings", str(self.claude_settings)]

    def invoke(self, *args):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = cli.main(self.flags + list(args))
        return code, output.getvalue(), error.getvalue()

    @patch("tidbyt_agent_alerts.cli.notify")
    def test_generic_notify_has_no_harness_dependency(self, notify):
        write_env(self.config, settings())
        notify.return_value = "sent"
        code, output, _ = self.invoke("notify", "--source", "cmux", "--event", "complete", "--project", "demo", "--message", 'message "quoted"')
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "sent")
        self.assertEqual(notify.call_args.kwargs["source"], "cmux")
        self.assertEqual(notify.call_args.kwargs["message"], 'message "quoted"')

    @patch("tidbyt_agent_alerts.cli.notify", side_effect=AlertError("SECRET failure"))
    def test_hook_failure_is_nonblocking_and_redacted(self, notify):
        write_env(self.config, settings())
        payload = {"hook_event_name": "Notification", "notification_type": "idle_prompt", "cwd": "/project", "message": "SECRET input"}
        with patch("sys.stdin", io.StringIO(json.dumps(payload))):
            code, output, error = self.invoke("hook", "claude")
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        self.assertNotIn("SECRET", error)
        self.assertIn("doctor", error)

    @patch("tidbyt_agent_alerts.cli.notify")
    def test_malformed_and_oversized_hook_input_is_nonblocking(self, notify):
        for raw in ("not JSON", "[]", '{"hook_event_name":"ignored"}', "x" * (1024 * 1024 + 1)):
            with patch("sys.stdin", io.StringIO(raw)):
                code, output, _ = self.invoke("hook", "claude")
                self.assertEqual(code, 0)
                self.assertEqual(output, "")
        notify.assert_not_called()

    def test_missing_config_errors_without_traceback(self):
        code, _, error = self.invoke("test")
        self.assertEqual(code, 1)
        self.assertIn("setup", error)
        self.assertNotIn("Traceback", error)

    @patch("tidbyt_agent_alerts.cli.render")
    @patch("tidbyt_agent_alerts.cli.Client")
    @patch("tidbyt_agent_alerts.cli.shutil.which", return_value="/usr/bin/pixlet")
    @patch("tidbyt_agent_alerts.cli.getpass.getpass", return_value="setup-key")
    @patch("sys.stdin.isatty", return_value=True)
    def test_guided_setup_creates_private_config_and_hooks(self, isatty, secret, which, client, render):
        client.return_value.devices.return_value = [{"id": "desk", "displayName": "Desk"}]
        client.return_value.device.return_value = {"id": "desk"}
        answers = ["", "1", "neutral", "n", "y", "both", "n"]
        with patch("builtins.input", side_effect=answers):
            code, output, error = self.invoke("setup")
        self.assertEqual(code, 0, error)
        self.assertEqual(load(self.config)["TIDBYT_API_KEY"], "setup-key")
        self.assertEqual(load(self.config)["TIDBYT_DEVICE_ID"], "desk")
        self.assertEqual(set(json.loads(self.claude_settings.read_text())["hooks"]), {"Notification", "Stop"})
        self.assertNotIn("setup-key", output + error)

    @patch("sys.stdin.isatty", return_value=False)
    def test_noninteractive_setup_does_not_save_credentials(self, isatty):
        code, _, _ = self.invoke("setup")
        self.assertEqual(code, 1)
        self.assertFalse(self.config.exists())

    @patch("tidbyt_agent_alerts.cli.render")
    @patch("tidbyt_agent_alerts.cli.Client")
    @patch("tidbyt_agent_alerts.cli.notify")
    def test_doctor_does_not_push_image(self, notify, client, render):
        write_env(self.config, settings())
        client.return_value.device.return_value = {"id": "desk", "displayName": "Desk"}
        code, output, error = self.invoke("doctor")
        self.assertEqual(code, 0, error)
        self.assertIn("no image pushed", output)
        notify.assert_not_called()
        client.return_value.push.assert_not_called()

    def test_control_characters_and_length_are_cleaned(self):
        self.assertEqual(cli.clean("hello\nworld\x00", 80), "helloworld")
        self.assertEqual(len(cli.clean("x" * 300, 256)), 256)
