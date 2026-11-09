"""Managed Codex hooks and OpenCode plugin, without replacing user settings."""

import copy
import json
import os
from pathlib import Path
import shlex
import time

from .config import AlertError, atomic_write

OWNER = "tidbyt-agent-alerts:codex:v1"
PLUGIN_MARKER = "// Managed by tidbyt-agent-alerts:opencode:v1\n"
PLUGIN = Path(__file__).parent / "assets/opencode-plugin/index.js"


def codex_path():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "hooks.json"


def opencode_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "opencode/plugins/tidbyt-agent-alerts.js"


def codex_read(path):
    if path.is_symlink():
        raise AlertError("Refusing to modify symlinked Codex hooks.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        raise AlertError("Codex hooks contain invalid JSON; no changes made.") from None
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise AlertError("Unexpected Codex hooks structure; no changes made.")
    for groups in data.get("hooks", {}).values():
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            raise AlertError("Unexpected Codex hook structure; no changes made.")
    return data


def codex_owned(group):
    hooks = group.get("hooks")
    return (isinstance(hooks, list) and len(hooks) == 1 and isinstance(hooks[0], dict)
            and hooks[0].get("statusMessage") == OWNER)


def codex_without_owned(data):
    result = copy.deepcopy(data)
    for event, groups in list(result.get("hooks", {}).items()):
        remaining = [group for group in groups if not codex_owned(group)]
        if remaining:
            result["hooks"][event] = remaining
        else:
            del result["hooks"][event]
    if "hooks" in result and not result["hooks"]:
        del result["hooks"]
    return result


def codex_save(path, old, new):
    if old == new:
        return False
    if codex_read(path) != old:
        raise AlertError("Codex hooks changed during setup; no changes made. Please retry.")
    if path.exists():
        backup = path.with_name(path.name + f".tidbyt-agent-alerts.{time.time_ns()}.bak")
        atomic_write(backup, path.read_text(encoding="utf-8"))
    atomic_write(path, json.dumps(new, indent=2) + "\n")
    return True


def codex_install(path, launcher, config, events):
    old = codex_read(path)
    new = codex_without_owned(old)
    command = shlex.join([str(launcher), "--config", str(config), "hook", "codex"])
    hook = {"type": "command", "command": command, "async": True, "statusMessage": OWNER}
    hooks = new.setdefault("hooks", {})
    if events in ("attention", "both"):
        hooks.setdefault("PermissionRequest", []).append({"hooks": [dict(hook)]})
    if events in ("complete", "both"):
        hooks.setdefault("Stop", []).append({"hooks": [dict(hook)]})
    return codex_save(path, old, new)


def codex_uninstall(path):
    old = codex_read(path)
    return codex_save(path, old, codex_without_owned(old))


def opencode_read(path):
    if path.is_symlink():
        raise AlertError("Refusing to modify symlinked OpenCode plugin.")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if not text.startswith(PLUGIN_MARKER):
        raise AlertError("OpenCode plugin path is not owned by this tool; no changes made.")
    return text


def opencode_install(path, launcher, config, events):
    old = opencode_read(path)
    options = json.dumps({"launcher": str(launcher), "config": str(config), "events": events})
    new = PLUGIN_MARKER + PLUGIN.read_text(encoding="utf-8").replace("const managed = null", "const managed = " + options)
    if old == new:
        return False
    if opencode_read(path) != old:
        raise AlertError("OpenCode plugin changed during setup; no changes made. Please retry.")
    atomic_write(path, new)
    return True


def opencode_uninstall(path):
    if opencode_read(path) is not None:
        path.unlink()
