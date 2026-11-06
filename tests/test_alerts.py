import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tidbyt_agent_alerts import alerts
from tidbyt_agent_alerts.config import AlertError
from test_config import settings


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name)
        client_patch = patch.object(alerts, "Client")
        self.client = client_patch.start().return_value
        self.addCleanup(client_patch.stop)
        self.client.device.return_value = {"id": "desk", "brightness": 25}
        render_patch = patch.object(alerts, "render", return_value=b"image")
        self.render = render_patch.start()
        self.addCleanup(render_patch.stop)
        sleep_patch = patch.object(alerts.time, "sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.event = dict(message='a "quoted" project', source="codex", event="attention", project="project")

    def send(self, **overrides):
        return alerts.notify(settings(**overrides), self.state, **self.event)

    def test_default_does_not_change_brightness(self):
        self.assertEqual(self.send(), "sent")
        self.client.push.assert_called_once_with(b"image")
        self.client.brightness.assert_not_called()
        self.sleep.assert_not_called()

    def test_successful_alert_deduplicates(self):
        self.send()
        self.assertIn("cooldown", self.send())
        self.client.push.assert_called_once()
        state = next(self.state.glob("*.json")).read_text()
        self.assertNotIn(self.event["message"], state)
        self.assertNotIn("test-key", state)

    def test_different_event_and_force_bypass_cooldown(self):
        self.send()
        self.event["event"] = "complete"
        self.assertEqual(self.send(), "sent")
        self.assertEqual(alerts.notify(settings(), self.state, **self.event, force=True), "sent")
        self.assertEqual(self.client.push.call_count, 3)

    def test_device_lock_skips_overlapping_alert(self):
        identity = settings()["TIDBYT_BASE_URL"] + "/desk"
        with alerts.device_lock(self.state, identity):
            self.assertIn("busy", self.send())
        self.client.push.assert_not_called()

    def test_restores_reported_brightness(self):
        self.send(TIDBYT_ALERT_BRIGHTNESS="100")
        self.assertEqual([call.args for call in self.client.brightness.call_args_list], [(100,), (25,)])
        self.sleep.assert_called_once_with(15)
        state = json.loads(next(self.state.glob("*.json")).read_text())
        self.assertNotIn("restore_brightness", state)

    def test_restores_brightness_when_push_fails(self):
        self.client.push.side_effect = AlertError("push failed")
        with self.assertRaisesRegex(AlertError, "push failed"):
            self.send(TIDBYT_ALERT_BRIGHTNESS="100")
        self.assertEqual(self.client.brightness.call_args.args, (25,))
        state = json.loads(next(self.state.glob("*.json")).read_text())
        self.assertNotIn("fingerprint", state)

    def test_recovery_survives_failed_restore_and_runs_before_next_alert(self):
        self.client.brightness.side_effect = [None, AlertError("offline")]
        with self.assertRaisesRegex(AlertError, "offline"):
            self.send(TIDBYT_ALERT_BRIGHTNESS="100")
        path = next(self.state.glob("*.json"))
        self.assertEqual(json.loads(path.read_text())["restore_brightness"], 25)
        self.client.brightness.side_effect = None
        self.send()
        self.assertEqual(self.client.brightness.call_args.args, (25,))
        self.assertNotIn("restore_brightness", json.loads(path.read_text()))

    def test_refuses_unknown_brightness(self):
        self.client.device.return_value = {"id": "desk"}
        with self.assertRaisesRegex(AlertError, "report brightness"):
            self.send(TIDBYT_ALERT_BRIGHTNESS="100")
        self.client.brightness.assert_not_called()
        self.client.push.assert_not_called()

    def test_sigterm_restores_brightness_and_signal_handler(self):
        original = signal.getsignal(signal.SIGTERM)
        self.sleep.side_effect = lambda seconds: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        with self.assertRaisesRegex(AlertError, "interrupted"):
            self.send(TIDBYT_ALERT_BRIGHTNESS="100")
        self.assertEqual(self.client.brightness.call_args.args, (25,))
        self.assertEqual(signal.getsignal(signal.SIGTERM), original)

    def test_corrupt_state_does_not_overwrite_recovery_data_or_call_api(self):
        identity = settings()["TIDBYT_BASE_URL"] + "/desk"
        with alerts.device_lock(self.state, identity) as path:
            pass
        for content in ("{", "[]", '{"restore_brightness":"wrong"}', '{"sent_at":"bad"}'):
            path.write_text(content)
            with self.subTest(content=content), self.assertRaisesRegex(AlertError, "Invalid notification state"):
                self.send()
            self.assertEqual(path.read_text(), content)
        self.client.push.assert_not_called()


class RenderTests(unittest.TestCase):
    @patch("tidbyt_agent_alerts.alerts.shutil.which", return_value="/usr/bin/pixlet")
    @patch("tidbyt_agent_alerts.alerts.subprocess.run")
    def test_messages_are_passed_as_argv_not_shell(self, run, which):
        message = 'quote " $(touch /tmp/should-not-exist)\nnewline'

        def write_image(command, **kwargs):
            Path(command[-1]).write_bytes(b"image")

        run.side_effect = write_image
        self.assertEqual(alerts.render(settings(), message, "cmux", "attention"), b"image")
        command = run.call_args.args[0]
        self.assertIn("message=" + message, command)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    @patch("tidbyt_agent_alerts.alerts.shutil.which", return_value=None)
    def test_missing_pixlet(self, which):
        with self.assertRaisesRegex(AlertError, "Pixlet not found"):
            alerts.render(settings(), "message", "agent", "attention")

    @patch("tidbyt_agent_alerts.alerts.shutil.which", return_value="/usr/bin/pixlet")
    @patch("tidbyt_agent_alerts.alerts.subprocess.run", side_effect=subprocess.TimeoutExpired("pixlet", 30))
    def test_render_timeout_is_actionable(self, run, which):
        with self.assertRaisesRegex(AlertError, "rendering failed"):
            alerts.render(settings(), "message", "agent", "attention")
