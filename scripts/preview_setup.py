"""Try the real setup UI with fake devices and disposable settings. No network calls."""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidbyt_agent_alerts import cli  # noqa: E402
from tidbyt_agent_alerts.config import DEFAULTS, write_env  # noqa: E402


def main():
    print("Preview only. Devices are simulated; settings are temporary.")
    print("The saved API key is a dummy value. Press Enter to keep it.\n")
    with tempfile.TemporaryDirectory(prefix="tidbyt-agent-alerts-preview-") as directory:
        root = Path(directory)
        config = root / "config.env"
        write_env(config, DEFAULTS | {"TIDBYT_API_KEY": "demo-key", "TIDBYT_DEVICE_ID": "desk"})
        with patch.object(cli, "Client") as client, \
                patch.object(cli, "render", return_value=b"preview"), \
                patch.object(cli, "notify", return_value="sent (simulated)"), \
                patch.object(cli.shutil, "which", return_value="/demo/bin/pixlet"):
            devices = [
                {"id": "desk", "displayName": "Desk display"},
                {"id": "studio", "displayName": "Studio display"},
            ]
            client.side_effect = lambda values: SimpleNamespace(
                devices=lambda: devices,
                device=lambda: next((device for device in devices if device["id"] == values["TIDBYT_DEVICE_ID"]),
                                    {"id": values["TIDBYT_DEVICE_ID"], "displayName": "Custom display"}),
            )
            return cli.main(["--config", str(config), "--claude-settings", str(root / "settings.json"),
                             "--codex-hooks", str(root / "hooks.json"), "--opencode-plugin", str(root / "plugin.js"),
                             *sys.argv[1:], "setup"])


if __name__ == "__main__":
    raise SystemExit(main())
