#!/usr/bin/env python3
"""Run from an installed archive without pip or third-party Python packages."""

from pathlib import Path
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tidbyt_agent_alerts.cli import main  # noqa: E402

raise SystemExit(main())
