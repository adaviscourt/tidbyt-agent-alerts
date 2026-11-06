import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tidbyt_agent_alerts.config import (
    AlertError, DEFAULTS, config_path, load, read_env, state_dir, validate, write_env,
)


def settings(**extra):
    return DEFAULTS | {"TIDBYT_API_KEY": "test-key", "TIDBYT_DEVICE_ID": "desk"} | extra


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "config.env"

    def test_round_trip_special_characters_and_permissions(self):
        value = 'quotes " backslash \\ dollar $(touch nope) and spaces'
        write_env(self.path, settings(TIDBYT_API_KEY=value))
        self.assertEqual(load(self.path)["TIDBYT_API_KEY"], value)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_configuration_is_data_not_shell(self):
        sentinel = Path(self.directory.name) / "executed"
        write_env(self.path, settings(TIDBYT_API_KEY=f"$(touch {sentinel})"))
        load(self.path)
        self.assertFalse(sentinel.exists())

    def test_rejects_broad_permissions(self):
        write_env(self.path, settings())
        self.path.chmod(0o644)
        with self.assertRaisesRegex(AlertError, "permissions"):
            read_env(self.path)

    def test_rejects_symlinked_config(self):
        write_env(self.path, settings())
        link = self.path.parent / "link.env"
        link.symlink_to(self.path)
        with self.assertRaisesRegex(AlertError, "symlink"):
            read_env(link)

    def test_rejects_invalid_lines_without_echoing_values(self):
        for line in ('SECRET-VALUE', 'TIDBYT_API_KEY="SECRET', 'KEY=123\nINVALID SECRET'):
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(line)
            self.path.chmod(0o600)
            with self.assertRaises(AlertError) as caught:
                read_env(self.path)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_plain_values_are_allowed(self):
        self.path.parent.mkdir()
        self.path.write_text("# Comment\nTIDBYT_API_KEY=test-key\nTIDBYT_DEVICE_ID=desk\n")
        self.path.chmod(0o600)
        self.assertEqual(load(self.path)["TIDBYT_DEVICE_ID"], "desk")

    def test_invalid_urls_and_http_consent(self):
        for url in ("https://user:password@example.com/v0/devices", "file:///secret",
                    "https://example.com/?key=secret", "https://example.com/#secret",
                    "http://localhost/v0/devices", "https://example.com:wrong"):
            with self.subTest(url=url), self.assertRaises(AlertError):
                validate(settings(TIDBYT_BASE_URL=url))
        validate(settings(TIDBYT_BASE_URL="http://localhost/v0/devices", TIDBYT_ALLOW_HTTP="true"))

    def test_invalid_ranges_and_missing_credentials(self):
        for overrides in ({"TIDBYT_ALERT_SECONDS": "0"}, {"TIDBYT_COOLDOWN_SECONDS": "301"},
                          {"TIDBYT_ALERT_BRIGHTNESS": "101"}, {"TIDBYT_API_KEY": ""},
                          {"TIDBYT_DEVICE_ID": ""}, {"TIDBYT_THEME": "unknown"},
                          {"TIDBYT_API_KEY": "key\r\nInjected"}, {"TIDBYT_ANIMATION": "unknown"}):
            with self.subTest(overrides=json.dumps(list(overrides))), self.assertRaises(AlertError):
                validate(settings(**overrides))

    def test_xdg_locations(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/private/config", "XDG_STATE_HOME": "/private/state"}):
            self.assertEqual(config_path(), Path("/private/config/tidbyt-agent-alerts/config.env"))
            self.assertEqual(state_dir(), Path("/private/state/tidbyt-agent-alerts"))

    def test_missing_file_has_actionable_error(self):
        with self.assertRaisesRegex(AlertError, "setup"):
            load(self.path)
