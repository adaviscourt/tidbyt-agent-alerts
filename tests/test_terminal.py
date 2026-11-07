import errno
import getpass
import io
import json
import os
import pty
import select
import signal
import sys
import time
import unittest
import warnings
from unittest.mock import patch

from tidbyt_agent_alerts.config import AlertError
from tidbyt_agent_alerts.terminal import Terminal, fit, visible


OPTIONS = [("neutral", "Neutral", "Any agent"), ("claude", "Claude", "Original animations")]


class TTYBuffer(io.StringIO):
    @property
    def encoding(self):
        return "utf-8"

    def isatty(self):
        return True


class TerminalTests(unittest.TestCase):
    def test_color_and_step_hierarchy_in_supported_terminal(self):
        stream = TTYBuffer()
        with patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            ui = Terminal(stream=stream)
            ui.intro("Connect your display.")
            ui.step(1, 4, "Connect")
            ui.status("Device authenticated")
            ui.finish("Setup complete")
        output = stream.getvalue()
        self.assertIn("\033[33m", output)
        self.assertIn("Tidbyt Agent Alerts", output)
        self.assertIn("1/4", output)
        self.assertIn("✓", output)

    def test_no_color_dumb_and_plain_disable_cursor_widgets(self):
        for environment, plain in (({"NO_COLOR": ""}, False), ({"TERM": "dumb"}, False),
                                   ({"TIDBYT_AGENT_ALERTS_PLAIN": "1"}, False), ({}, True)):
            stream = TTYBuffer()
            with patch.dict(os.environ, environment, clear=True):
                ui = Terminal(stream=stream, plain=plain)
                ui.intro("Setup")
                ui.status("Verified")
                with patch("sys.stdin.isatty", return_value=True):
                    self.assertFalse(ui._can_select(2))
            self.assertNotIn("\033", stream.getvalue())
            self.assertFalse(ui.unicode)

    def test_redirected_output_stays_plain(self):
        stream = io.StringIO()
        ui = Terminal(stream=stream)
        ui.intro("Setup")
        ui.error("Check your network.")
        self.assertNotIn("\033", stream.getvalue())
        self.assertIn("Error", stream.getvalue())

    def test_numeric_name_default_and_custom_selection(self):
        ui = Terminal(stream=io.StringIO())
        for typed, expected in (("2", "claude"), ("Neutral", "neutral"), ("", "neutral")):
            with patch("builtins.input", return_value=typed):
                self.assertEqual(ui.select("Theme", OPTIONS, "neutral"), expected)
        with patch("builtins.input", side_effect=["invalid", "claude"]):
            self.assertEqual(ui.select("Theme", OPTIONS, "neutral"), "claude")
        with patch("builtins.input", return_value="custom-device-id"):
            self.assertEqual(ui.select("Device", OPTIONS, "neutral", custom=True), "custom-device-id")

    def test_invalid_integer_reprompts_in_place(self):
        stream = io.StringIO()
        ui = Terminal(stream=stream)
        with patch("builtins.input", side_effect=["wrong", "101", "-1", "25"]):
            self.assertEqual(ui.integer("Brightness", "100", 0, 100), "25")
        self.assertEqual(stream.getvalue().count("Enter a whole number"), 3)

    def test_confirm_retries_invalid_answers(self):
        ui = Terminal(stream=io.StringIO())
        with patch("builtins.input", side_effect=["maybe", "yes"]):
            self.assertTrue(ui.confirm("Install hooks?"))
        with patch("builtins.input", return_value=""):
            self.assertFalse(ui.confirm("Boost brightness?"))

    def test_secret_existing_value_is_never_echoed(self):
        stream = io.StringIO()
        ui = Terminal(stream=stream)
        with patch("tidbyt_agent_alerts.terminal.getpass.getpass", return_value=""):
            self.assertEqual(ui.secret("API key", existing="TOP-SECRET"), "TOP-SECRET")
        self.assertNotIn("TOP-SECRET", stream.getvalue())
        self.assertIn("hidden", stream.getvalue())

    def test_secret_refuses_echoing_fallback(self):
        ui = Terminal(stream=io.StringIO())

        def insecure_fallback(prompt):
            warnings.warn("Cannot hide input", getpass.GetPassWarning)
            self.fail("Secret input must not continue with echo enabled")

        with patch("tidbyt_agent_alerts.terminal.getpass.getpass", side_effect=insecure_fallback), \
                self.assertRaisesRegex(AlertError, "Hidden input is unavailable"):
            ui.secret("API key")

    def test_task_failure_has_named_state(self):
        stream = io.StringIO()
        ui = Terminal(stream=stream)

        def offline():
            raise AlertError("Network unavailable")

        with self.assertRaisesRegex(AlertError, "Network unavailable"):
            ui.run("Device authentication", offline)
        self.assertIn("Device authentication: failed", stream.getvalue())

    def test_narrow_terminal_wraps_notes_and_uses_typed_choices(self):
        stream = TTYBuffer()
        with patch.dict(os.environ, {"TERM": "xterm"}, clear=True), \
                patch("shutil.get_terminal_size", return_value=os.terminal_size((40, 24))):
            ui = Terminal(stream=stream, plain=True)
            ui.intro("Connect your display without replacing your existing hooks.")
            ui.summary([("Config", "/very/long/path/" * 5)])
            with patch("sys.stdin.isatty", return_value=True):
                self.assertFalse(ui._can_select(2))
        self.assertTrue(all(len(line) <= 40 for line in stream.getvalue().splitlines()))

    def test_device_labels_cannot_inject_terminal_controls(self):
        self.assertNotIn("\033", visible("Name\033[2J\nnext"))
        self.assertLessEqual(len(fit("x" * 100, 24)), 24)


class KeyboardTests(unittest.TestCase):
    def run_menu(self, keys, *, plain=False):
        program = '''import json, termios
from tidbyt_agent_alerts.terminal import Terminal
before = termios.tcgetattr(0)
try:
    result = Terminal(plain=PLAIN).select("Theme", [("neutral", "Neutral", "Any agent"), ("claude", "Claude", "Animations")], "neutral")
except KeyboardInterrupt:
    result = "canceled"
after = termios.tcgetattr(0)
# macOS may set the transient PENDIN bit while restoring canonical input.
before[3] &= ~getattr(termios, "PENDIN", 0)
after[3] &= ~getattr(termios, "PENDIN", 0)
print("RESULT=" + json.dumps({"selected": result, "restored": before == after}))
'''.replace("PLAIN", repr(plain))
        environment = os.environ | {"TERM": "xterm-256color", "COLUMNS": "80", "LINES": "24"}
        environment.pop("NO_COLOR", None)
        environment.pop("TIDBYT_AGENT_ALERTS_PLAIN", None)
        pid, master = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, "-c", program], environment)
        captured, sent, reaped = b"", False, False
        deadline = time.monotonic() + 15
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                    self.fail("Menu timed out: " + repr(captured))
                try:
                    chunk = os.read(master, 4096)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                captured += chunk
                ready = b"> " if plain else b"\033[?25l"
                if not sent and ready in captured:
                    if isinstance(keys, int):
                        os.kill(pid, keys)
                    else:
                        os.write(master, keys)
                    sent = True
            _, status = os.waitpid(pid, 0)
            reaped = True
        finally:
            os.close(master)
            if not reaped:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, captured.decode())
        line = captured.decode().split("RESULT=")[-1].strip()
        return json.loads(line), captured

    def test_arrow_keys_select_and_restore_terminal(self):
        result, output = self.run_menu(b"\033[B\r")
        self.assertEqual(result, {"selected": "claude", "restored": True})
        self.assertIn(b"\033[?25h", output)

    def test_ctrl_c_restores_cursor_and_terminal_echo(self):
        result, output = self.run_menu(b"\x03")
        self.assertEqual(result, {"selected": "canceled", "restored": True})
        self.assertIn(b"\033[?25h", output)

    def test_sigterm_restores_cursor_and_terminal_echo(self):
        result, output = self.run_menu(signal.SIGTERM)
        self.assertEqual(result, {"selected": "canceled", "restored": True})
        self.assertIn(b"\033[?25h", output)

    def test_typing_a_name_switches_to_line_input(self):
        result, _ = self.run_menu(b"claude\n")
        self.assertEqual(result, {"selected": "claude", "restored": True})

    def test_plain_mode_uses_typed_choices_without_escape_sequences(self):
        result, output = self.run_menu(b"2\n", plain=True)
        self.assertEqual(result, {"selected": "claude", "restored": True})
        self.assertNotIn(b"\033", output)
