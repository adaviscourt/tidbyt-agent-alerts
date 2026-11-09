"""Small standard-library CLI with interactive setup and explicit integrations."""

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys

from . import __version__, claude, integrations
from .alerts import notify, render
from .api import Client
from .config import (
    AlertError, DEFAULTS, config_path, load, read_env, state_dir, validate, write_env,
)
from .terminal import Terminal


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
    ui = Terminal(plain=args.plain)
    ui.intro("A small signal from your agent to your display.")
    ui.note("Your key is saved privately on this computer. No hosted service is used.")
    pixlet = shutil.which(values["TIDBYT_PIXLET"])
    if not pixlet:
        raise AlertError("Install Pixlet first: https://github.com/tidbyt/pixlet#installation. Then rerun setup.")
    values["TIDBYT_PIXLET"] = str(Path(pixlet).resolve())
    ui.step(1, 4, "Connect your display")

    def endpoint(value):
        validate(DEFAULTS | {"TIDBYT_API_KEY": "pending", "TIDBYT_DEVICE_ID": "pending",
                             "TIDBYT_BASE_URL": value, "TIDBYT_ALLOW_HTTP": "true"})

    def api_key(value):
        if not value:
            raise AlertError("Enter a device API key.")
        validate(DEFAULTS | {"TIDBYT_API_KEY": value, "TIDBYT_DEVICE_ID": "pending"})

    values["TIDBYT_BASE_URL"] = ui.prompt(
        "API base URL", values["TIDBYT_BASE_URL"], validator=endpoint,
        hint="Keep the default for Tidbyt. Tronbyt: https://your-server/v0/devices",
    ).rstrip("/")
    values["TIDBYT_ALLOW_HTTP"] = "false"
    if values["TIDBYT_BASE_URL"].startswith("http:"):
        ui.warning("HTTP sends your API key unencrypted. Use HTTPS unless you trust this network.")
        if not ui.confirm("Allow unencrypted HTTP for this server?"):
            raise AlertError("Setup canceled; no configuration was saved.")
        values["TIDBYT_ALLOW_HTTP"] = "true"
    values["TIDBYT_API_KEY"] = ui.secret("Device API key", existing=values.get("TIDBYT_API_KEY", ""), validator=api_key)
    values.setdefault("TIDBYT_DEVICE_ID", "pending")
    values = validate(values)
    client = Client(values)
    try:
        devices = ui.run("Device discovery", client.devices)
    except AlertError:
        devices = []
        ui.warning("Device listing is unavailable. Enter your device ID instead.")
    if devices:
        default = values["TIDBYT_DEVICE_ID"]
        if default == "pending":
            default = str(devices[0]["id"])
        options = [(str(device["id"]), clean(device.get("displayName", device["id"]), 80),
                    "Device ID: " + clean(device["id"], 80)) for device in devices]
        values["TIDBYT_DEVICE_ID"] = ui.select("Choose your display", options, default, custom=True)
    else:
        default = values["TIDBYT_DEVICE_ID"]
        values["TIDBYT_DEVICE_ID"] = ui.prompt("Device ID", "" if default == "pending" else default)
    values = validate(values)
    device = ui.run("Device authentication", Client(values).device)
    ui.step(2, 4, "Display appearance")
    values["TIDBYT_THEME"] = ui.select("Display theme", [
        ("neutral", "Neutral", "Text alerts: INPUT, DONE, or ERROR."),
        ("agent", "Agent artwork", "Artwork matches Claude, Codex, or OpenCode; neutral for other sources."),
        ("claude", "Classic Claude", "Use Claude artwork for every source (legacy theme)."),
    ], values["TIDBYT_THEME"])
    if values["TIDBYT_THEME"] == "claude":
        values["TIDBYT_ANIMATION"] = ui.select(
            "Claude animation", [(key, key.title(), "") for key in ("thinking", "loading", "sparkle", "wave")],
            values["TIDBYT_ANIMATION"],
        )
    if ui.confirm("Temporarily boost brightness?", bool(values["TIDBYT_ALERT_BRIGHTNESS"]),
                  hint="Off by default. If enabled, the prior brightness is restored afterward."):
        values["TIDBYT_ALERT_BRIGHTNESS"] = ui.integer("Alert brightness", values["TIDBYT_ALERT_BRIGHTNESS"] or "100", 0, 100)
        values["TIDBYT_ALERT_SECONDS"] = ui.integer("Restore brightness after how many seconds?", values["TIDBYT_ALERT_SECONDS"], 1, 120,
                                                   hint="This does not control how long the image stays on screen.")
    else:
        values["TIDBYT_ALERT_BRIGHTNESS"] = ""
    values = validate(values)
    # Check rendering before changing any persistent configuration or hook settings.
    ui.run("Display rendering", lambda: render(values, "Setup successful", "agent", "attention"))
    ui.step(3, 4, "Agent notifications")
    selected = {}
    for name in ("Claude Code", "Codex", "OpenCode"):
        if ui.confirm(f"Connect {name}?", False, hint="Existing settings are preserved."):
            selected[name] = ui.select(f"When should {name} alert you?", [
                ("attention", "Needs your input", "Permission requests and supported questions."),
                ("complete", "Response finished", "When the agent finishes responding."),
                ("both", "Both", "Input requests and finished responses."),
            ], "attention")
    if any(event in ("complete", "both") for event in selected.values()):
        ui.note("A finished response does not mean the task is complete or successful.")
    # Preflight every selected target before saving credentials.
    if "Claude Code" in selected:
        claude.read_settings(args.claude_settings)
    if "Codex" in selected:
        integrations.codex_read(args.codex_hooks)
    if "OpenCode" in selected:
        integrations.opencode_read(args.opencode_plugin)
    ui.step(4, 4, "Review your setup")
    event_labels = {"attention": "Needs your input", "complete": "Response finished", "both": "Input + response finished"}
    try:
        config_label = "~/" + args.config.relative_to(Path.home()).as_posix()
    except ValueError:
        config_label = str(args.config)
    ui.summary([
        ("Display", clean(device.get("displayName", values["TIDBYT_DEVICE_ID"]), 80)),
        ("API", values["TIDBYT_BASE_URL"]),
        ("Theme", values["TIDBYT_THEME"].title() + (f" / {values['TIDBYT_ANIMATION']}" if values["TIDBYT_THEME"] == "claude" else "")),
        ("Brightness", "Unchanged" if not values["TIDBYT_ALERT_BRIGHTNESS"] else f"{values['TIDBYT_ALERT_BRIGHTNESS']}% for {values['TIDBYT_ALERT_SECONDS']}s, then restore"),
        *((name, event_labels[selected[name]] if name in selected else "Unchanged")
          for name in ("Claude Code", "Codex", "OpenCode")),
        ("API key", "Hidden; saved with owner-only access"),
        ("Config", config_label),
    ], title=None)
    if not ui.confirm("Save this configuration" + (" and connect selected agents?" if selected else "?"), True):
        ui.finish("Setup canceled", "No configuration changes were saved.")
        return
    ui.run("Private configuration saved", lambda: write_env(args.config, values))
    if "Claude Code" in selected:
        ui.run("Claude Code connected", lambda: claude.install(args.claude_settings, launcher_path(), args.config, selected["Claude Code"]))
    if "Codex" in selected:
        ui.run("Codex connected", lambda: integrations.codex_install(args.codex_hooks, launcher_path(), args.config, selected["Codex"]))
        ui.note("In Codex, open /hooks and trust the new hooks.")
    if "OpenCode" in selected:
        ui.run("OpenCode connected", lambda: integrations.opencode_install(args.opencode_plugin, launcher_path(), args.config, selected["OpenCode"]))
    if ui.confirm("Send a test alert now?", True):
        result = ui.run("Test notification", lambda: notify(values, state_dir(), message="Tidbyt Agent Alerts is ready", source="agent", event="attention", force=True))
        ui.note("Test: " + result)
    ui.finish("Setup complete", "Restart Claude Code to load its hooks." if "Claude Code" in selected else "Your display is ready.")
    ui.write("   Check setup:  tidbyt-agent-alerts doctor")
    ui.write("   Other tools:  tidbyt-agent-alerts notify --source NAME")
    ui.write()


def doctor(args):
    ui = Terminal(plain=args.plain)
    ui.intro("Check the connection without sending an alert.")
    values = load(args.config)
    ui.status("Private configuration")
    device = ui.run("Device authentication", Client(values).device)
    ui.note("Display: " + clean(device.get("displayName", values["TIDBYT_DEVICE_ID"]), 80))
    ui.run("Pixlet rendering (no image pushed)", lambda: render(values, "Diagnostics", "agent", "attention"))
    settings = claude.read_settings(args.claude_settings)
    count = sum(claude.owned(group) for groups in settings.get("hooks", {}).values() for group in groups)
    ui.status(f"Claude Code: {count} managed hook group(s)")
    if settings.get("disableAllHooks"):
        ui.warning("Claude settings disable hooks.")
    if "claude-alert.sh" in json.dumps(settings):
        ui.warning("Legacy claude-alert.sh hook found; remove it manually to prevent duplicate alerts.")
    codex = integrations.codex_read(args.codex_hooks)
    count = sum(integrations.codex_owned(group) for groups in codex.get("hooks", {}).values() for group in groups)
    ui.status(f"Codex: {count} managed hook group(s)")
    ui.status("OpenCode: " + ("managed plugin installed" if integrations.opencode_read(args.opencode_plugin) is not None else "not connected"))
    ui.finish("Checks complete", "Agent policies and hook trust settings may restrict notifications.")


def hook(args):
    # Notification failures must never block the agent or expose incoming payloads.
    try:
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            if args.harness == "codex":
                print("{}")
            return
        payload = json.loads(raw)
        if args.harness == "codex":
            event = codex_event(payload)
        else:
            event = claude.event(payload)
        if event:
            event = {key: clean(value, 256 if key == "message" else 80) for key, value in event.items()}
            notify(load(args.config), state_dir(), **event)
    except (AlertError, OSError, ValueError, TypeError, KeyError):
        print("tidbyt-agent-alerts: notification failed; run doctor for diagnostics.", file=sys.stderr)
    if args.harness == "codex":
        print("{}")


def codex_event(payload):
    if not isinstance(payload, dict):
        return None
    name = payload.get("hook_event_name")
    if name == "PermissionRequest":
        kind, suffix = "attention", "needs your attention"
    elif name == "Stop" and not payload.get("stop_hook_active"):
        kind, suffix = "complete", "finished responding"
    else:
        return None
    cwd = payload.get("cwd")
    project = Path(cwd).name if isinstance(cwd, str) and cwd else "Codex"
    return {"source": "codex", "event": kind, "project": project, "message": f"{project}: {suffix}"}


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
    print("This removes managed agent integrations" + (" and the installed CLI." if installed else "."))
    print("Credentials and settings backups are kept unless --purge is specified.")
    if not args.yes and not Terminal(plain=args.plain).confirm("Continue?"):
        return
    # Refuse malformed or unowned targets before changing anything.
    claude.read_settings(args.claude_settings)
    integrations.codex_read(args.codex_hooks)
    integrations.opencode_read(args.opencode_plugin)
    claude.uninstall(args.claude_settings)
    integrations.codex_uninstall(args.codex_hooks)
    integrations.opencode_uninstall(args.opencode_plugin)
    if args.purge:
        args.config.unlink(missing_ok=True)
    if installed:
        launcher.unlink(missing_ok=True)
        shutil.rmtree(root)
    print("Uninstalled. Private state and settings backups were retained.")


def parser():
    result = argparse.ArgumentParser(prog="tidbyt-agent-alerts", description="Local agent notifications for Tidbyt and Tronbyt.")
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--plain", action="store_true", help="disable color and interactive menus (screen-reader-friendly)")
    result.add_argument("--config", type=Path, default=config_path(), help="private config.env path")
    result.add_argument("--claude-settings", type=Path, default=claude.settings_path(), help="Claude settings.json path")
    result.add_argument("--codex-hooks", type=Path, default=integrations.codex_path(), help="Codex hooks.json path")
    result.add_argument("--opencode-plugin", type=Path, default=integrations.opencode_path(), help="managed OpenCode plugin path")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="configure display and agent integrations")
    commands.add_parser("doctor", help="verify credentials, rendering, and hooks without alerting")
    commands.add_parser("test", help="send a test notification")
    send = commands.add_parser("notify", help="send a harness-independent notification")
    send.add_argument("--source", default="agent", help="integration name, e.g. codex or cmux")
    send.add_argument("--event", choices=("attention", "complete", "error"), default="attention")
    send.add_argument("--project", default="", help="project label; defaults to current directory name")
    send.add_argument("--message", help="custom display text; sent to your configured device API")
    send.add_argument("--force", action="store_true", help="bypass cooldown, not the device lock")
    hooks = commands.add_parser("hook", help="read a harness event from stdin (internal adapter)")
    hooks.add_argument("harness", choices=("claude", "codex"))
    install = commands.add_parser("install-hook", help="install or update a managed agent integration")
    install.add_argument("harness", nargs="?", choices=("claude", "codex", "opencode"), default="claude", help="agent to connect (default: claude, for compatibility)")
    install.add_argument("--events", choices=("attention", "complete", "both"), default="attention")
    remove = commands.add_parser("uninstall", help="remove owned hooks and installed CLI")
    remove.add_argument("--yes", action="store_true", help="skip confirmation")
    remove.add_argument("--purge", action="store_true", help="also delete the selected credential file")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    args.config = args.config.expanduser().absolute()
    args.claude_settings = args.claude_settings.expanduser().absolute()
    args.codex_hooks = args.codex_hooks.expanduser().absolute()
    args.opencode_plugin = args.opencode_plugin.expanduser().absolute()
    try:
        if args.command == "setup":
            setup(args)
        elif args.command == "doctor":
            doctor(args)
        elif args.command == "hook":
            hook(args)
        elif args.command == "install-hook":
            load(args.config)
            if args.harness == "claude":
                claude.install(args.claude_settings, launcher_path(), args.config, args.events)
                print("Claude Code connected. Restart Claude Code to load its hooks.")
            elif args.harness == "codex":
                integrations.codex_install(args.codex_hooks, launcher_path(), args.config, args.events)
                print("Codex connected. Open /hooks in Codex and trust the hooks.")
            else:
                integrations.opencode_install(args.opencode_plugin, launcher_path(), args.config, args.events)
                print("OpenCode connected.")
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
        Terminal(plain=args.plain, stream=sys.stderr).error(message)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nCanceled. You can rerun setup when you are ready.", file=sys.stderr)
        return 130
    return 0
