"""Small standard-library CLI with interactive setup and explicit integrations."""

import argparse
import getpass
import json
from pathlib import Path
import shlex
import shutil
import sys

from . import __version__, claude
from .alerts import notify, render
from .api import Client
from .config import (
    AlertError, DEFAULTS, config_path, load, read_env, state_dir, validate, write_env,
)


def prompt(label, default=""):
    suffix = f" [{default}]" if default else ""
    return input(label + suffix + ": ").strip() or default


def yes(label, default=False):
    while True:
        answer = prompt(label + (" [Y/n]" if default else " [y/N]")).lower()
        if not answer:
            return default
        if answer in ("y", "yes", "n", "no"):
            return answer in ("y", "yes")
        print("Enter yes or no.")


def choice(label, options, default):
    while True:
        answer = prompt(f"{label} ({'/'.join(options)})", default)
        if answer in options:
            return answer
        print("Choose one of: " + ", ".join(options))


def launcher_path():
    root = Path(__file__).resolve().parent.parent
    if (root / "installation.json").is_file() and root.parent.name == "share":
        return root.parent.parent / "bin" / "tidbyt-agent-alerts"
    return Path(sys.argv[0]).expanduser().absolute()


def clean(text, limit):
    # Avoid terminal control sequences and keep messages short on a 64x32 display.
    return " ".join("".join(c for c in str(text) if c.isprintable()).split())[:limit]


def setup(args):
    if not sys.stdin.isatty():
        raise AlertError("Setup requires an interactive terminal. Run tidbyt-agent-alerts setup directly.")
    values = DEFAULTS.copy()
    if args.config.exists():
        values.update(read_env(args.config))
    print("Tidbyt Agent Alerts setup\nCredentials stay on this computer. No hosted service is used.\n")
    values["TIDBYT_BASE_URL"] = prompt(
        "API base URL (Tronbyt: https://your-server/v0/devices)", values["TIDBYT_BASE_URL"],
    ).rstrip("/")
    values["TIDBYT_ALLOW_HTTP"] = "false"
    if values["TIDBYT_BASE_URL"].startswith("http:"):
        print("WARNING: HTTP sends your API key unencrypted. Use HTTPS unless you trust this network.")
        if not yes("Allow unencrypted HTTP for this server?"):
            raise AlertError("Setup canceled; no configuration was saved.")
        values["TIDBYT_ALLOW_HTTP"] = "true"
    keep_key = " (Enter keeps existing key)" if values.get("TIDBYT_API_KEY") else ""
    values["TIDBYT_API_KEY"] = getpass.getpass("Device API key" + keep_key + ": ").strip() or values.get("TIDBYT_API_KEY", "")
    values.setdefault("TIDBYT_DEVICE_ID", "pending")
    values = validate(values)
    client = Client(values)
    try:
        devices = client.devices()
    except AlertError:
        devices = []
        print("Device listing unavailable; enter the device ID manually.")
    if devices:
        for index, device in enumerate(devices, 1):
            name = clean(device.get("displayName", device["id"]), 80)
            print(f"  {index}. {name} ({clean(device['id'], 80)})")
        default = values["TIDBYT_DEVICE_ID"]
        if default == "pending":
            default = "1"
        selected = prompt("Device number or device ID", default)
        if selected.isdigit() and 1 <= int(selected) <= len(devices):
            selected = str(devices[int(selected) - 1]["id"])
        values["TIDBYT_DEVICE_ID"] = selected
    else:
        default = values["TIDBYT_DEVICE_ID"]
        values["TIDBYT_DEVICE_ID"] = prompt("Device ID", "" if default == "pending" else default)
    values = validate(values)
    Client(values).device()
    pixlet = shutil.which(values["TIDBYT_PIXLET"])
    if not pixlet:
        raise AlertError("Install Pixlet first: https://github.com/tidbyt/pixlet#installation. Then rerun setup.")
    values["TIDBYT_PIXLET"] = str(Path(pixlet).resolve())
    values["TIDBYT_THEME"] = choice("Display theme", ("neutral", "claude"), values["TIDBYT_THEME"])
    if values["TIDBYT_THEME"] == "claude":
        values["TIDBYT_ANIMATION"] = choice(
            "Claude animation", ("thinking", "loading", "sparkle", "wave"), values["TIDBYT_ANIMATION"],
        )
    if yes("Temporarily boost brightness?", bool(values["TIDBYT_ALERT_BRIGHTNESS"])):
        values["TIDBYT_ALERT_BRIGHTNESS"] = prompt("Alert brightness (0-100)", values["TIDBYT_ALERT_BRIGHTNESS"] or "100")
        values["TIDBYT_ALERT_SECONDS"] = prompt("Seconds before restoring prior brightness (1-120)", values["TIDBYT_ALERT_SECONDS"])
    else:
        values["TIDBYT_ALERT_BRIGHTNESS"] = ""
    values = validate(values)
    # Check rendering before changing any persistent configuration or hook settings.
    render(values, "Setup successful", "agent", "attention")
    install_claude = yes("Install Claude Code hooks?", True)
    events = "attention"
    if install_claude:
        events = choice("Alert when Claude needs attention, finishes a response, or both", ("attention", "complete", "both"), "attention")
        claude.read_settings(args.claude_settings)  # Preflight malformed settings before saving credentials.
    write_env(args.config, values)
    print(f"Saved private config: {args.config}")
    if install_claude:
        claude.install(args.claude_settings, launcher_path(), args.config, events)
        print("Installed Claude Code hooks. Restart Claude Code to load them.")
    if yes("Send a test alert now?", True):
        result = notify(values, state_dir(), message="Tidbyt Agent Alerts is ready", source="agent", event="attention", force=True)
        print("Test: " + result)
    print("Setup complete. Other integrations can use: tidbyt-agent-alerts notify --source NAME")


def doctor(args):
    values = load(args.config)
    print("OK: private configuration")
    device = Client(values).device()
    print("OK: authenticated device " + clean(device.get("displayName", values["TIDBYT_DEVICE_ID"]), 80))
    render(values, "Diagnostics", "agent", "attention")
    print("OK: Pixlet rendering (no image pushed)")
    settings = claude.read_settings(args.claude_settings)
    count = sum(claude.owned(group) for groups in settings.get("hooks", {}).values() for group in groups)
    print(f"Claude Code: {count} managed hook group(s)")
    if settings.get("disableAllHooks"):
        print("WARNING: Claude settings disable hooks.")
    if "claude-alert.sh" in json.dumps(settings):
        print("WARNING: legacy claude-alert.sh hook found; remove it manually to prevent duplicate alerts.")
    print("Restart Claude Code after changing hooks. Managed policy may restrict hooks.")


def hook(args):
    # Notification failures must never block the agent or expose incoming payloads.
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return
        event = claude.event(json.loads(raw))
        if event:
            event = {key: clean(value, 256 if key == "message" else 80) for key, value in event.items()}
            notify(load(args.config), state_dir(), **event)
    except (AlertError, OSError, ValueError, TypeError, KeyError):
        print("tidbyt-agent-alerts: notification failed; run doctor for diagnostics.", file=sys.stderr)


def uninstall(args):
    root = Path(__file__).resolve().parent.parent
    marker = root / "installation.json"
    installed = marker.is_file() and root.name == "tidbyt-agent-alerts" and root.parent.name == "share"
    launcher = None
    if installed:
        try:
            metadata = json.loads(marker.read_text())
        except ValueError:
            metadata = None
        if not isinstance(metadata, dict) or metadata.get("project") != "tidbyt-agent-alerts":
            raise AlertError("Unrecognized installation marker; no changes made.")
        launcher = root.parent.parent / "bin" / "tidbyt-agent-alerts"
        if launcher.is_symlink():
            raise AlertError("Refusing to remove a symlinked launcher.")
        if launcher.exists():
            text = launcher.read_text(encoding="utf-8")
            try:
                command = shlex.split(text.splitlines()[-1])
                owned = ("# Managed by tidbyt-agent-alerts" in text
                         and len(command) == 5 and command[0] == "exec"
                         and command[2] == "-I" and command[4] == "$@"
                         and Path(command[3]).resolve() == root / "run.py")
            except (ValueError, IndexError):
                owned = False
            if not owned:
                raise AlertError("Launcher is not owned by this installation; no changes made.")
    print("This removes managed Claude hooks" + (" and the installed CLI." if installed else "."))
    print("Credentials and settings backups are kept unless --purge is specified.")
    if not args.yes and not yes("Continue?"):
        return
    claude.uninstall(args.claude_settings)
    if args.purge:
        args.config.unlink(missing_ok=True)
    if installed:
        launcher.unlink(missing_ok=True)
        shutil.rmtree(root)
    print("Uninstalled. Private state and any Claude settings backups were retained.")


def parser():
    result = argparse.ArgumentParser(prog="tidbyt-agent-alerts", description="Local agent notifications for Tidbyt and Tronbyt.")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--config", type=Path, default=config_path(), help="private config.env path")
    result.add_argument("--claude-settings", type=Path, default=claude.settings_path(), help="Claude settings.json path")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="guide configuration and Claude hook installation")
    commands.add_parser("doctor", help="verify credentials, rendering, and hooks without alerting")
    commands.add_parser("test", help="send a test notification")
    send = commands.add_parser("notify", help="send a harness-independent notification")
    send.add_argument("--source", default="agent", help="integration name, e.g. codex or cmux")
    send.add_argument("--event", choices=("attention", "complete", "error"), default="attention")
    send.add_argument("--project", default="", help="project label; defaults to current directory name")
    send.add_argument("--message", help="custom display text; sent to your configured device API")
    send.add_argument("--force", action="store_true", help="bypass cooldown, not the device lock")
    hooks = commands.add_parser("hook", help="read a harness event from stdin (internal adapter)")
    hooks.add_argument("harness", choices=("claude",))
    install = commands.add_parser("install-hook", help="install or update managed Claude hooks")
    install.add_argument("--events", choices=("attention", "complete", "both"), default="attention")
    remove = commands.add_parser("uninstall", help="remove owned hooks and installed CLI")
    remove.add_argument("--yes", action="store_true", help="skip confirmation")
    remove.add_argument("--purge", action="store_true", help="also delete the selected credential file")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    args.config = args.config.expanduser().absolute()
    args.claude_settings = args.claude_settings.expanduser().absolute()
    try:
        if args.command == "setup":
            setup(args)
        elif args.command == "doctor":
            doctor(args)
        elif args.command == "hook":
            hook(args)
        elif args.command == "install-hook":
            load(args.config)
            claude.install(args.claude_settings, launcher_path(), args.config, args.events)
            print("Claude hooks installed. Restart Claude Code to load them.")
        elif args.command == "uninstall":
            uninstall(args)
        else:
            values = load(args.config)
            if args.command == "test":
                data = dict(message="Tidbyt Agent Alerts test", source="agent", event="attention", force=True)
            else:
                project = clean(args.project or Path.cwd().name, 80)
                suffix = {"attention": "needs your attention", "complete": "finished", "error": "encountered an error"}[args.event]
                data = dict(message=clean(args.message or f"{project}: {suffix}", 256),
                            source=clean(args.source, 32), event=args.event, project=project, force=args.force)
            print(notify(values, state_dir(), **data))
    except (AlertError, OSError) as error:
        message = str(error) if isinstance(error, AlertError) else "Filesystem operation failed. Check file paths and permissions."
        print("Error: " + message, file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCanceled.", file=sys.stderr)
        return 130
    return 0
