import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tidbyt_agent_alerts.session_title import title_for


class SessionTitleTests(unittest.TestCase):
    def test_codex_latest_matching_title_and_missing_index(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"CODEX_HOME": directory}):
            self.assertIsNone(title_for("codex", {"session_id": "one"}))
            path = Path(directory) / "session_index.jsonl"
            path.write_text('\n'.join(json.dumps(row) for row in [
                {"id": "one", "thread_name": "Old"},
                {"id": "two", "thread_name": "Other"},
                {"id": "one", "thread_name": "New"},
            ]))
            self.assertEqual(title_for("codex", {"session_id": "one"}), "New")
            self.assertIsNone(title_for("codex", {"session_id": "missing"}))

    def test_claude_custom_title_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript.jsonl"
            path.write_text('\n'.join([json.dumps({"type": "user", "message": "secret"}),
                                       'invalid', json.dumps({"type": "custom-title", "customTitle": "My task"})]))
            self.assertEqual(title_for("claude", {"transcript_path": str(path)}), "My task")
            self.assertIsNone(title_for("claude", {}))
            self.assertIsNone(title_for("opencode", {}))
