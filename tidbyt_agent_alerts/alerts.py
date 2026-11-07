"""Shared notification engine; no harness-specific dependencies."""

from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from .api import Client
from .config import AlertError, atomic_write


ASSETS = Path(__file__).parent / "assets"


def read_state(path):
    try:
        state = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except ValueError:
        raise AlertError("Invalid notification state. Inspect the private state file before retrying.") from None
    valid = isinstance(state, dict)
    if valid and "restore_brightness" in state:
        valid = type(state["restore_brightness"]) is int and 0 <= state["restore_brightness"] <= 100
    if valid:
        valid = (isinstance(state.get("sent_at", 0), (int, float))
                 and math.isfinite(state.get("sent_at", 0))
                 and isinstance(state.get("fingerprint", ""), str))
    if not valid:
        raise AlertError("Invalid notification state. Inspect the private state file before retrying.")
    return state


def render(values, message, source, event):
    executable = shutil.which(values["TIDBYT_PIXLET"])
    if not executable:
        raise AlertError("Pixlet not found. Install Pixlet, then rerun setup.")
    theme = values["TIDBYT_THEME"]
    artwork = {"codex": "codex-loop.gif", "opencode": "opencode.png"}.get(source) if theme == "agent" else None
    script = ASSETS / ("agent.star" if artwork else "claude.star" if theme == "claude" else "alert.star")
    with tempfile.TemporaryDirectory(prefix="tidbyt-agent-alerts-") as directory:
        # Newer Pixlet scans sibling .star files even in single-file mode.
        # Isolate the selected, self-contained theme from other app entry points.
        isolated = Path(directory) / "alert.star"
        if artwork:
            encoded = base64.b64encode((ASSETS / artwork).read_bytes()).decode("ascii")
            isolated.write_text(script.read_text().replace("__ARTWORK__", encoded))
        else:
            shutil.copyfile(script, isolated)
        output = Path(directory) / "alert.webp"
        command = [executable, "render", str(isolated), f"message={message}",
                   f"source={source}", f"event={event}",
                   f"gif={values['TIDBYT_ANIMATION']}", "--output", str(output)]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=30)
        except (subprocess.SubprocessError, OSError):
            raise AlertError("Pixlet rendering failed. Run doctor to check your installation.") from None
        return output.read_bytes()


@contextmanager
def device_lock(directory, device):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity = hashlib.sha256(device.encode()).hexdigest()[:24]
    path = directory / (identity + ".lock")
    with path.open("a+") as handle:
        path.chmod(0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
            return
        try:
            yield directory / (identity + ".json")
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def notify(values, directory, *, message, source, event, project="", force=False):
    # Serialize per physical target across all integrations, not per session.
    identity = values["TIDBYT_BASE_URL"] + "/" + values["TIDBYT_DEVICE_ID"]
    fingerprint = hashlib.sha256(json.dumps([source, event, project, message]).encode()).hexdigest()
    with device_lock(directory, identity) as state_path:
        if state_path is None:
            return "busy (another alert is active)"
        state = read_state(state_path)
        client = Client(values)
        # Recover a brightness change interrupted by a crash on the next invocation.
        if "restore_brightness" in state:
            client.brightness(state["restore_brightness"])
            state.pop("restore_brightness")
            atomic_write(state_path, json.dumps(state))
        if (not force and state.get("fingerprint") == fingerprint
                and 0 <= time.time() - state.get("sent_at", 0) < int(values["TIDBYT_COOLDOWN_SECONDS"])):
            return "skipped (cooldown)"
        image = render(values, message, source, event)
        brightness = values["TIDBYT_ALERT_BRIGHTNESS"]
        original = None
        if brightness:
            original = client.device().get("brightness")
            if type(original) is not int or not 0 <= original <= 100:
                raise AlertError("Device did not report brightness; refusing to change it.")
        previous_handlers = {}

        def interrupted(signum, frame):
            raise AlertError("Alert interrupted; restoring brightness.")

        try:
            if original is not None:
                state["restore_brightness"] = original
                atomic_write(state_path, json.dumps(state))
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous_handlers[sig] = signal.signal(sig, interrupted)
                client.brightness(int(brightness))
            client.push(image)
            state.update(fingerprint=fingerprint, sent_at=time.time())
            atomic_write(state_path, json.dumps(state))
            if original is not None:
                time.sleep(int(values["TIDBYT_ALERT_SECONDS"]))
        finally:
            try:
                if original is not None:
                    client.brightness(original)
                    state.pop("restore_brightness", None)
                    atomic_write(state_path, json.dumps(state))
            finally:
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)
        return "sent"
