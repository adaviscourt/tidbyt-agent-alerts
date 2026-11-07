#!/usr/bin/env bash
# Keep execution inside main so a truncated piped download cannot run a partial install.
main() (
    set -euo pipefail

    local version="" no_setup=false prefix="" plain=false
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version|--prefix)
                if [ "$#" -lt 2 ] || [ -z "$2" ]; then
                    echo "$1 requires a value." >&2
                    exit 1
                fi
                if [ "$1" = --version ]; then
                    version="${2#v}"
                else
                    prefix="$2"
                fi
                shift 2
                ;;
            --no-setup) no_setup=true; shift ;;
            --plain) plain=true; shift ;;
            --help|-h)
                echo 'Usage: bootstrap.sh [--version VERSION] [--prefix DIRECTORY] [--no-setup] [--plain]'
                echo 'Downloads a verified GitHub release, installs it, and opens terminal setup.'
                exit 0
                ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    local accent="" reset=""
    if [ -t 1 ] && ! "$plain" && [ -z "${NO_COLOR+x}" ] \
        && [ "${TERM:-}" != dumb ] && [ "${TIDBYT_AGENT_ALERTS_PLAIN:-}" != 1 ]; then
        accent=$'\033[33m'
        reset=$'\033[0m'
    fi
    printf '\n%sTidbyt Agent Alerts%s\n  Download > verify > install\n\n' "$accent" "$reset"
    stage() { printf '  %s%s%s  %s\n' "$accent" "$1" "$reset" "$2"; }

    case "$(uname -s)" in
        Darwin|Linux) ;;
        *) echo 'Only macOS and Linux are supported.' >&2; exit 1 ;;
    esac
    local python
    python="$(command -v python3 || true)"
    if [ -z "$python" ] || ! "$python" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        echo 'Python 3.10+ is required. Install Python, then rerun this command.' >&2
        echo 'macOS with Homebrew: brew install python' >&2
        exit 1
    fi
    if [ -z "$prefix" ]; then
        prefix="$("$python" -c 'from pathlib import Path; print(Path.home() / ".local")')"
    fi
    "$python" - "$version" <<'PY'
import re
import sys

if sys.argv[1] and not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", sys.argv[1]):
    raise SystemExit("Invalid version. Use a release version such as 0.1.0.")
PY
    if ! command -v curl >/dev/null 2>&1; then
        echo 'curl is required. Install curl, then rerun this command.' >&2
        exit 1
    fi
    if ! "$no_setup"; then
        if ! { exec 3<>/dev/tty; } 2>/dev/null; then
            echo 'Interactive setup requires a terminal. Use --no-setup, then run tidbyt-agent-alerts setup later.' >&2
            exit 1
        fi
        if ! command -v pixlet >/dev/null 2>&1; then
            echo 'Pixlet is required for setup. Install it, then rerun this command.' >&2
            echo 'macOS: brew install tidbyt/tidbyt/pixlet' >&2
            echo 'Linux: https://github.com/tidbyt/pixlet/releases' >&2
            exit 1
        fi
    fi

    local work_dir release_root checksum_url
    work_dir="$("$python" -c 'import tempfile; print(tempfile.mkdtemp(prefix="tidbyt-agent-alerts-"))')"
    # This private directory was created above; never clean a caller-supplied path.
    trap 'rm -rf -- "$work_dir"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    release_root='https://github.com/adaviscourt/tidbyt-agent-alerts/releases'
    if [ -n "$version" ]; then
        checksum_url="$release_root/download/v$version/SHA256SUMS"
    else
        checksum_url="$release_root/latest/download/SHA256SUMS"
    fi
    stage '1/3' 'Downloading release checksums...'
    if ! curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 120 --retry 2 \
        --max-filesize 65536 --output "$work_dir/SHA256SUMS" "$checksum_url"; then
        echo 'Could not download release checksums. Verify that the requested GitHub release exists.' >&2
        exit 1
    fi
    # Resolve latest from the archive name, then pin the archive request to that release.
    version="$("$python" - "$work_dir" "$version" <<'PY'
import json
from pathlib import Path
import re
import sys

root, requested = Path(sys.argv[1]), sys.argv[2]
pattern = r"([0-9a-fA-F]{64})\s+\*?(tidbyt-agent-alerts-([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\.tar\.gz)"
matches = [match for line in (root / "SHA256SUMS").read_text().splitlines()
           if (match := re.fullmatch(pattern, line.strip()))]
if len(matches) != 1:
    raise SystemExit("Release checksums must identify exactly one application archive.")
digest, filename, version = matches[0].groups()
if requested and version != requested:
    raise SystemExit("Release version does not match the requested version.")
(root / "plan.json").write_text(json.dumps({"sha256": digest.lower(), "filename": filename, "version": version}))
print(version)
PY
)"
    stage '2/3' "Downloading Tidbyt Agent Alerts $version..."
    if ! curl --fail --silent --show-error --location \
        --proto '=https' --proto-redir '=https' --connect-timeout 10 --max-time 120 --retry 2 \
        --max-filesize 20971520 --output "$work_dir/release.tar.gz" \
        "$release_root/download/v$version/tidbyt-agent-alerts-$version.tar.gz"; then
        echo 'Could not download the release archive. No program files were installed.' >&2
        exit 1
    fi
    stage '3/3' 'Verifying release and archive...'
    "$python" - "$work_dir" <<'PY'
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

root = Path(sys.argv[1])
plan = json.loads((root / "plan.json").read_text())
archive = root / "release.tar.gz"
if archive.stat().st_size > 20 * 1024 * 1024:
    raise SystemExit("Release archive is too large.")
if hashlib.sha256(archive.read_bytes()).hexdigest() != plan["sha256"]:
    raise SystemExit("Release checksum mismatch. No program files were installed.")
expected = "tidbyt-agent-alerts-" + plan["version"]
destination = root / "extracted"
destination.mkdir(mode=0o700)
try:
    with tarfile.open(archive, "r:gz") as handle:
        members, seen, size = [], set(), 0
        for index, member in enumerate(handle):
            path = PurePosixPath(member.name)
            if (index >= 256 or path.is_absolute() or ".." in path.parts
                    or not path.parts or path.parts[0] != expected
                    or not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError("Unsafe archive entry")
            size += member.size
            if member.size > 2 * 1024 * 1024 or size > 20 * 1024 * 1024:
                raise ValueError("Archive contents too large")
            seen.add(member.name)
            members.append(member)
        for member in members:
            target = destination.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
except (tarfile.TarError, ValueError, OSError):
    raise SystemExit("Unsafe or invalid release archive. No program files were installed.") from None
if not (destination / expected / "install.py").is_file():
    raise SystemExit("Release is missing its installer. No program files were installed.")
print("Release checksum and archive verified.")
PY
    local installer="$work_dir/extracted/tidbyt-agent-alerts-$version/install.py"
    local -a flags=(--prefix "$prefix")
    if "$plain"; then flags+=(--plain); fi
    if "$no_setup"; then
        "$python" "$installer" "${flags[@]}" --no-setup </dev/null
    else
        # stdin is the terminal, not the pipe containing this bootstrap script.
        "$python" "$installer" "${flags[@]}" <&3
    fi
)

main "$@"
