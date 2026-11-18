"""Best-effort local session title lookup; never forward transcript content."""

import json
import os
from pathlib import Path


def title_for(harness, payload):
    if not isinstance(payload, dict):
        return None
    if harness == "codex":
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "session_index.jsonl"
    elif harness == "claude":
        path = payload.get("transcript_path")
        if not isinstance(path, str) or not path:
            return None
        path = Path(path)
    else:
        return None
    title = None
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(entry, dict):
                    continue
                if harness == "codex" and entry.get("id") == session_id:
                    title = entry.get("thread_name")
                elif harness == "claude" and entry.get("type") == "custom-title":
                    title = entry.get("customTitle")
    except (OSError, UnicodeError, ValueError):
        return None
    return title if isinstance(title, str) and title.strip() else None
