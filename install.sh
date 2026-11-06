#!/bin/sh
set -eu
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Python 3.10+ is required. Install Python, then rerun this installer.' >&2
    exit 1
fi
exec python3 "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/install.py" "$@"
