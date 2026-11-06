"""Render every bundled theme without credentials or network calls."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidbyt_agent_alerts.alerts import render  # noqa: E402
from tidbyt_agent_alerts.config import DEFAULTS  # noqa: E402

for event in ("attention", "complete", "error"):
    image = render(DEFAULTS, 'Quotes " and spaces: project needs attention', "agent", event)
    assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"
    print(f"OK: neutral / {event}")

for animation in ("thinking", "loading", "sparkle", "wave"):
    values = DEFAULTS | {"TIDBYT_THEME": "claude", "TIDBYT_ANIMATION": animation}
    image = render(values, "Claude needs your attention", "claude", "attention")
    assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"
    print(f"OK: claude / {animation}")
