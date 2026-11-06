"""Full installed CLI + real Pixlet against a local fake device API."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from install import install
from test_api import Handler
from test_config import settings
from tidbyt_agent_alerts.config import write_env


@unittest.skipUnless(shutil.which("pixlet"), "Pixlet is not installed")
class EndToEndTests(unittest.TestCase):
    def test_installed_generic_command_and_claude_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.requests = []
            server.status = 200
            server.response = b'{"id":"desk","brightness":25}'
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            launcher = install(root / "prefix", Path(__file__).resolve().parents[1])
            config = root / "config.env"
            write_env(config, settings(
                TIDBYT_BASE_URL=f"http://127.0.0.1:{server.server_port}/v0/devices",
                TIDBYT_ALLOW_HTTP="true", TIDBYT_PIXLET=shutil.which("pixlet"),
            ))
            env = os.environ | {"HOME": str(root), "XDG_STATE_HOME": str(root / "state")}
            command = [str(launcher), "--config", str(config)]
            result = subprocess.run(command + ["notify", "--source", "cmux", "--message", 'Project "demo" needs input'],
                                    env=env, cwd=root, check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "sent")
            payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                       "cwd": "/private/project", "message": "DO NOT FORWARD THIS"}
            result = subprocess.run(command + ["hook", "claude"], input=json.dumps(payload),
                                    env=env, cwd=root, check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")
            pushes = [request for request in server.requests if request[0] == "POST"]
            self.assertEqual(len(pushes), 2)
            for _, path, headers, body in pushes:
                self.assertEqual(path, "/v0/devices/desk/push")
                self.assertEqual(headers["Authorization"], "Bearer test-key")
                self.assertTrue(body["image"])
                self.assertFalse(body["background"])
                self.assertNotIn("installationID", body)
