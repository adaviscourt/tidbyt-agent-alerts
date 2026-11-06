"""Claude Code adapter: owned, idempotent hook groups and minimal event data."""

import copy
import json
import os
from pathlib import Path
import shlex
import time

from .config import AlertError, atomic_write


OWNER = "tidbyt-agent-alerts:claude:v1"


def settings_path():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "settings.json"


def read_settings(path):
    if path.is_symlink():
        raise AlertError("Refusing to modify symlinked Claude settings.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        raise AlertError("Claude settings contain invalid JSON; no changes made.") from None
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise AlertError("Unexpected Claude settings structure; no changes made.")
    for groups in value.get("hooks", {}).values():
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            raise AlertError("Unexpected Claude hook structure; no changes made.")
    return value


def owned(group):
    hooks = group.get("hooks")
    return (isinstance(hooks, list) and len(hooks) == 1
            and isinstance(hooks[0], dict) and hooks[0].get("statusMessage") == OWNER)


def without_owned(settings):
    result = copy.deepcopy(settings)
    for event, groups in list(result.get("hooks", {}).items()):
        remaining = [group for group in groups if not owned(group)]
        if remaining:
            result["hooks"][event] = remaining
        else:
            del result["hooks"][event]
    if "hooks" in result and not result["hooks"]:
        del result["hooks"]
    return result


def save_settings(path, old, new):
    if old == new:
        return False
    if read_settings(path) != old:
        raise AlertError("Claude settings changed during setup; no changes made. Please retry.")
    if path.exists():
        backup = path.with_name(path.name + f".tidbyt-agent-alerts.{time.time_ns()}.bak")
        atomic_write(backup, path.read_text(encoding="utf-8"))
    atomic_write(path, json.dumps(new, indent=2) + "\n")
    return True


def install(path, launcher, config, events):
    old = read_settings(path)
    new = without_owned(old)
    command = shlex.join([str(launcher), "--config", str(config), "hook", "claude"])
    hook = {"type": "command", "command": command, "async": True, "statusMessage": OWNER}
    hooks = new.setdefault("hooks", {})
    if events in ("attention", "both"):
        hooks.setdefault("Notification", []).append({
            "matcher": "permission_prompt|idle_prompt", "hooks": [dict(hook)],
        })
    if events in ("complete", "both"):
        hooks.setdefault("Stop", []).append({"hooks": [dict(hook)]})
    return save_settings(path, old, new)


def uninstall(path):
    old = read_settings(path)
    return save_settings(path, old, without_owned(old))


def event(payload):
    if not isinstance(payload, dict):
        return None
    name = payload.get("hook_event_name")
    if name == "Notification":
        if payload.get("notification_type") not in ("permission_prompt", "idle_prompt"):
            return None
        kind, suffix = "attention", "needs your attention"
    elif name == "Stop":
        if payload.get("stop_hook_active"):
            return None
        kind, suffix = "complete", "finished responding"
    else:
        return None
    cwd = payload.get("cwd")
    project = Path(cwd).name if isinstance(cwd, str) and cwd else "Claude"
    # Never forward notification messages, prompts, responses, or transcript contents.
    return {"source": "claude", "event": kind, "project": project,
            "message": f"{project}: {suffix}"}
