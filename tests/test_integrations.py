import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

from tidbyt_agent_alerts import cli


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
