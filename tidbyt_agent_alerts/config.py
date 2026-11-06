"""Private configuration; values are data, never shell code."""

import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


class AlertError(Exception):
    """An actionable error that is safe to display without exposing secrets."""


def config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "tidbyt-agent-alerts" / "config.env"


def state_dir():
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return root / "tidbyt-agent-alerts"


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_env(path):
    path = Path(path)
    if path.is_symlink():
        raise AlertError("Refusing a symlinked config file.")
    if path.stat().st_mode & 0o077:
        raise AlertError(f"Config permissions are too broad. Run: chmod 600 {path}")
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().isidentifier():
            raise AlertError(f"Invalid config at line {number}.")
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise AlertError(f"Invalid quoted config at line {number}.") from None
        if not isinstance(value, str):
            raise AlertError(f"Expected text at config line {number}.")
        values[key.strip()] = value
    return values


def write_env(path, values):
    text = "# Managed by tidbyt-agent-alerts. Parsed as data; do not source this file.\n"
    text += "".join(f"{key}={json.dumps(str(value))}\n" for key, value in values.items())
    atomic_write(path, text)


DEFAULTS = {
    "TIDBYT_BASE_URL": "https://api.tidbyt.com/v0/devices",
    "TIDBYT_THEME": "neutral",
    "TIDBYT_ANIMATION": "thinking",
    "TIDBYT_PIXLET": "pixlet",
    "TIDBYT_ALERT_BRIGHTNESS": "",  # Empty means leave brightness unchanged.
    "TIDBYT_ALERT_SECONDS": "15",
    "TIDBYT_COOLDOWN_SECONDS": "10",
    "TIDBYT_ALLOW_HTTP": "false",
}


def integer(values, key, minimum, maximum):
    try:
        value = int(values[key])
    except (ValueError, KeyError):
        raise AlertError(f"{key} must be an integer.") from None
    if not minimum <= value <= maximum:
        raise AlertError(f"{key} must be between {minimum} and {maximum}.")
    return value


def validate(values):
    values = DEFAULTS | values
    for key in ("TIDBYT_API_KEY", "TIDBYT_DEVICE_ID"):
        if not values.get(key, "").strip():
            raise AlertError(f"Missing {key}; run tidbyt-agent-alerts setup.")
    if any(ord(c) < 32 or ord(c) == 127 for c in values["TIDBYT_API_KEY"]):
        raise AlertError("API key contains invalid control characters.")
    try:
        url = urlsplit(values["TIDBYT_BASE_URL"])
        _ = url.port  # Validate the port even though requests use the original URL.
    except ValueError:
        raise AlertError("Invalid API base URL.") from None
    if (url.scheme not in ("http", "https") or not url.hostname
            or url.username is not None or url.password is not None
            or url.query or url.fragment):
        raise AlertError("Use an http(s) API base URL without credentials, query, or fragment.")
    if url.scheme == "http" and values["TIDBYT_ALLOW_HTTP"] != "true":
        raise AlertError("HTTP exposes your API key. Use HTTPS or explicitly enable TIDBYT_ALLOW_HTTP.")
    if values["TIDBYT_THEME"] not in ("neutral", "claude"):
        raise AlertError("TIDBYT_THEME must be neutral or claude.")
    if values["TIDBYT_ANIMATION"] not in ("thinking", "loading", "sparkle", "wave"):
        raise AlertError("Unknown Claude animation.")
    integer(values, "TIDBYT_ALERT_SECONDS", 1, 120)
    integer(values, "TIDBYT_COOLDOWN_SECONDS", 0, 300)
    if values["TIDBYT_ALERT_BRIGHTNESS"]:
        integer(values, "TIDBYT_ALERT_BRIGHTNESS", 0, 100)
    values["TIDBYT_BASE_URL"] = values["TIDBYT_BASE_URL"].rstrip("/")
    return values


def load(path):
    try:
        return validate(read_env(path))
    except FileNotFoundError:
        raise AlertError("Config not found; run tidbyt-agent-alerts setup.") from None
