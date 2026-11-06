#!/usr/bin/env python3
"""Install only into a user-owned prefix; never edit shell profiles or use sudo."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def install(prefix, source):
    target = prefix / "share" / "tidbyt-agent-alerts"
    launcher = prefix / "bin" / "tidbyt-agent-alerts"
    if target.is_symlink() or launcher.is_symlink():
        raise RuntimeError("Refusing to overwrite a symlinked installation or launcher.")
    if target.exists() and not (target / "installation.json").is_file():
        raise RuntimeError("Installation directory already exists and is not owned by this installer.")
    if launcher.exists() and "# Managed by tidbyt-agent-alerts" not in launcher.read_text():
        raise RuntimeError("An unrelated executable already exists at the install location.")
    target.parent.mkdir(parents=True, exist_ok=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tidbyt-agent-alerts-", dir=target.parent) as directory:
        staged = Path(directory) / "new"
        staged.mkdir()
        shutil.copytree(source / "tidbyt_agent_alerts", staged / "tidbyt_agent_alerts",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ("run.py", "LICENSE", "NOTICE", "README.md"):
            shutil.copy2(source / name, staged / name)
        (staged / "installation.json").write_text(json.dumps({"project": "tidbyt-agent-alerts", "schema": 1}) + "\n")
        backup = Path(directory) / "previous"
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
            text = "#!/bin/sh\n# Managed by tidbyt-agent-alerts\nexec "
            text += shlex.join([str(Path(sys.executable).resolve()), "-I", str(target / "run.py")]) + ' "$@"\n'
            fd, temporary = tempfile.mkstemp(prefix=".tidbyt-agent-alerts-", dir=launcher.parent)
            try:
                with os.fdopen(fd, "w") as handle:
                    handle.write(text)
                    os.fchmod(handle.fileno(), 0o755)
                os.replace(temporary, launcher)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            if backup.exists():
                backup.rename(target)
            raise
    return launcher


def main():
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")
    parser = argparse.ArgumentParser(description="Install Tidbyt Agent Alerts, then start guided setup.")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local", help="install prefix (default: ~/.local)")
    parser.add_argument("--no-setup", action="store_true", help="install files without configuring credentials or hooks")
    args = parser.parse_args()
    try:
        launcher = install(args.prefix.expanduser().resolve(), Path(__file__).resolve().parent)
    except (OSError, RuntimeError) as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 1
    print(f"Installed: {launcher}", flush=True)
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print("To add the command to this shell: export PATH=" + shlex.quote(str(launcher.parent)) + ':"$PATH"', flush=True)
    if not args.no_setup:
        return subprocess.call([str(launcher), "setup"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
