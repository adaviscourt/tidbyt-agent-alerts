# Tidbyt Agent Alerts

Your agent needs you. Your Tidbyt lets you know.

A local command-line tool that turns agent events into notifications on a
Tidbyt or Tronbyt display. Guided setup saves your credentials privately and
installs Claude Code hooks without replacing existing settings.

**Claude Code is the first automatic integration, not the product boundary.**
The same `notify` command can be called by OpenCode, Codex, cmux, scripts, or
other tools. Dedicated adapters for those tools are future work.

## Requirements

- macOS or Linux; Python **3.10+**. No third-party Python packages required.
- [Pixlet](https://github.com/tidbyt/pixlet#Getting-started) installed locally.
- A Tidbyt API key and device ID, or a compatible Tronbyt server and device key.
- Claude Code with async command-hook support, if installing its integration.

On macOS, install the prerequisites with Homebrew:

```sh
brew install python tidbyt/tidbyt/pixlet
```

On Linux, install Python through your package manager and obtain Pixlet from
its [releases](https://github.com/tidbyt/pixlet/releases). Windows is not
supported in this version; WSL is not yet verified.

## Install

### One command

After the first tagged release is published:

```sh
curl -fsSL https://github.com/adaviscourt/tidbyt-agent-alerts/releases/latest/download/bootstrap.sh | bash
```

The bootstrap checks prerequisites, downloads a release over HTTPS, verifies
its SHA-256 checksum, validates archive contents, then starts the installer.
Interactive setup reads from your terminal, not the download pipe. Missing
dependencies produce installation instructions; nothing is installed silently.
No terminal is required when `--no-setup` is used.

**Security:** Piping a download to Bash immediately executes remote code.
Checksums detect mismatched downloads but do not establish publisher trust.
To inspect before running, download the script separately:

```sh
curl -fsSL -o bootstrap.sh https://github.com/adaviscourt/tidbyt-agent-alerts/releases/latest/download/bootstrap.sh
less bootstrap.sh
bash bootstrap.sh
```

Pin a release, skip setup for automation, or select an install prefix:

```sh
# Pin both the bootstrap script and application release:
curl -fsSL https://github.com/adaviscourt/tidbyt-agent-alerts/releases/download/v0.1.0/bootstrap.sh | bash -s -- --version 0.1.0

# Download the bootstrap first for headless installation:
bash bootstrap.sh --no-setup --prefix "$HOME/tools"

# Screen-reader-friendly output and typed choices:
bash bootstrap.sh --plain
```

`--version` also accepts a leading `v`. Without it, the bootstrap resolves
the latest release from its checksum manifest, then pins the archive download
to that version. Re-running the command upgrades files without deleting your
configuration. Temporary downloads are removed on exit. No clone is needed.

### From source

Until the first tagged release, or for development, install from source:

```sh
git clone https://github.com/adaviscourt/tidbyt-agent-alerts.git
cd tidbyt-agent-alerts
./install.sh
```

### Manual release download

Once a release is published, download `tidbyt-agent-alerts-VERSION.tar.gz`,
`bootstrap.sh`, and
`SHA256SUMS` from [Releases](https://github.com/adaviscourt/tidbyt-agent-alerts/releases).
Verify before extracting or running it:

```sh
# macOS
shasum -a 256 -c SHA256SUMS
# Linux: sha256sum -c SHA256SUMS

tar -xzf tidbyt-agent-alerts-VERSION.tar.gz
cd tidbyt-agent-alerts-VERSION
./install.sh
```

The installer copies the application into `~/.local/share/tidbyt-agent-alerts`
and creates `~/.local/bin/tidbyt-agent-alerts`. It then opens guided setup.
No `sudo`, shell-profile edits, background daemon, or hosted service.
The downloaded source directory can be moved or deleted afterward.

If `~/.local/bin` is not on your PATH, the installer shows the command to add
it. You can always run `~/.local/bin/tidbyt-agent-alerts` directly.

Install without setup, or into a different prefix:

```sh
./install.sh --no-setup
./install.sh --prefix "$HOME/tools"
./install.sh --plain
```

## Guided setup

```sh
tidbyt-agent-alerts setup
```

Setup asks for:

1. **API URL**: Tidbyt cloud by default; use `https://YOUR-SERVER/v0/devices`
   for Tronbyt. HTTP requires explicit consent because it exposes credentials.
2. **API key**: hidden input. For Tidbyt, find the device ID and key under
   the device's settings / Get API Key in the mobile app. For Tronbyt, use
   the per-device key from its web UI.
3. **Device**: select from the API list, or enter an ID manually if listing
   is unavailable. Setup authenticates against that device before saving.
4. **Theme**: neutral status labels, or the original Claude animations
   (`thinking`, `loading`, `sparkle`, `wave`).
5. **Brightness**: unchanged by default. Optionally boost temporarily, then
   restore the device's reported prior brightness.
6. **Claude hooks**: attention, response finished, or both. Default: attention.
7. **Test alert**: optional confirmation that the full path works.

The wizard groups setup into four steps: connect your display, choose its
appearance, choose agent signals, and review before saving. Supported
terminals offer arrow-key menus: use Up/Down, then Enter. Press Esc or start
typing a name to switch to typed choices. Numeric fields validate in place.
No credentials or hooks are written until you confirm the review.

For screen readers or simpler terminals, use `tidbyt-agent-alerts --plain setup`.
The installer and bootstrap accept `--plain` too. `NO_COLOR`, `TERM=dumb`, and
redirected output disable color and cursor-driven menus automatically; narrow
terminals use typed menus. The interface uses your terminal's native colors,
never relies on color alone, and has no animation. Terminal settings and the
cursor are restored when a menu is interrupted.

Configuration is written to `~/.config/tidbyt-agent-alerts/config.env` with
owner-only permissions (`0600`). `XDG_CONFIG_HOME` is respected. Values are
parsed as data, not executed as shell code. **Do not source the file.**

Re-running setup keeps your key unless you enter a replacement. Reinstalling
hooks updates this tool's entries rather than duplicating them. Settings are
backed up alongside `~/.claude/settings.json` before changes. Restart Claude
Code after changing hooks. Managed organizational policies may prevent them
from running.

### Claude behavior

- **Attention**: `Notification` events for `permission_prompt` and `idle_prompt`.
- **Complete**: `Stop`, meaning Claude finished a response—not proof a task
  succeeded. Recursive stop-hook events are ignored.
- Hooks run asynchronously; network/render failures do not block Claude.
- Only the project basename and a fixed event label are sent. Prompt text,
  response text, notification bodies, and transcript files are not forwarded.

To change the events without repeating setup:

```sh
tidbyt-agent-alerts install-hook --events both
```

## Any harness or terminal

```sh
tidbyt-agent-alerts notify --source codex --event complete
tidbyt-agent-alerts notify --source opencode --event attention --project my-app
tidbyt-agent-alerts notify --source cmux --event attention --message "Workspace needs input"
```

These are ordinary CLI calls, **not automatic integrations**. Connect them
to the relevant tool's supported lifecycle event or notification mechanism.
Do not assume OpenCode, Codex, and cmux use Claude's hook format.

`--event` accepts `attention`, `complete`, or `error`. Without `--message`,
the message uses the current directory's basename. Custom messages are sent
to your configured device API; do not include secrets. Text is limited to
256 characters and stripped of terminal control characters.

Simultaneous alerts for the same configured device are skipped while an
alert is active. Identical successful alerts have a 10-second cooldown.
`notify --force` bypasses cooldown, not the device lock. With brightness
boost enabled, the command remains active until brightness is restored;
integrations should invoke it asynchronously, as the Claude adapter does.

## Test and troubleshoot

```sh
tidbyt-agent-alerts test
tidbyt-agent-alerts doctor
tidbyt-agent-alerts --help
```

`doctor` checks private config permissions, API authentication, Pixlet
rendering, and managed Claude hooks **without pushing an image or changing
brightness**. HTTP requests have timeouts; API response bodies and keys are
not printed. Redirects are rejected rather than forwarding credentials.

| Problem | Check |
| --- | --- |
| HTTP 401/403 | Correct per-device key and ID; whether device listing is allowed |
| HTTP 404 / invalid JSON | Base URL must end in `/v0/devices`, not the server homepage |
| Pixlet not found | Install Pixlet, then rerun setup to save its absolute path |
| No Claude alerts | Restart Claude, inspect `/hooks`, check event selection and managed policies |
| Duplicate alerts | Remove any old `claude-alert.sh` hook manually; this tool never deletes unrelated hooks |
| Brightness left boosted after a crash | Retry an alert after restoring connectivity; saved recovery state restores it first |

Alerts use local rendering and the standard `/push` image endpoint. No
published community app, server-side `/push_app`, or server extension is
required. Images are pushed without an installation ID, so they do not stay
in the normal rotation. **Alert duration controls brightness restoration,
not how long firmware displays the image.** Display timing remains under
device/server control. SIGKILL, power loss, and API outages can delay
brightness restoration until a later invocation; leaving brightness boost
off avoids that risk. An independent concurrent brightness change may be
overwritten when the prior value is restored.

## Configuration

See [config.env.example](config.env.example). A different configuration can
be selected with `--config` **before** the command:

```sh
tidbyt-agent-alerts --config "$HOME/private/alerts.env" setup
tidbyt-agent-alerts --config "$HOME/private/alerts.env" test
```

The selected config path is recorded in installed Claude hooks; credentials
are not. `--claude-settings PATH` similarly selects an alternative settings
file. State and device locks live under
`${XDG_STATE_HOME:-~/.local/state}/tidbyt-agent-alerts` and contain hashes,
timestamps, and optional brightness recovery values—not prompts or API keys.
`CLAUDE_CONFIG_DIR` is respected when selecting the default Claude settings file.

The former `~/.config/claude-alert.env` is **not** imported or executed. Enter
its URL, key, and device ID in the new setup, then remove the old hook after
testing. The original community app and script are left untouched.

## Update and uninstall

Install a newer release with the same prefix. Credentials, hooks, and their
stable launcher path are preserved. Rerun setup if a prerequisite moves.

```sh
tidbyt-agent-alerts uninstall
# Also delete the selected credential file:
tidbyt-agent-alerts uninstall --purge
```

Uninstall removes only managed Claude hook groups and this installer's
program directory and launcher. It preserves unrelated settings, backups,
and private state. Credentials are kept unless `--purge` is specified.

## Development

From a Git checkout:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check_render.py  # requires Pixlet, no device/API credentials
python3 scripts/package.py      # writes archive + SHA256SUMS to dist/
python3 scripts/preview_setup.py # interactive UI demo, fake devices, disposable settings
```

Architecture: `alerts.py` owns delivery, rendering, deduplication, and device
locking; `claude.py` adapts Claude events into the generic notification
fields. Future adapters should produce `source`, `event`, `project`, and
`message`, and reuse the same engine. No agent SDK or model calls needed.

GitHub Actions tests macOS/Linux with Python 3.10 and 3.13, checks rendering
on macOS, and publishes versioned archives, the bootstrap, and checksums for `v*` tags.
Before tagging, update `__version__`, add release notes to
[CHANGELOG.md](CHANGELOG.md), and merge the tested change.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Independent project;
not affiliated with Tidbyt, Anthropic, OpenAI, OpenCode, or cmux.
