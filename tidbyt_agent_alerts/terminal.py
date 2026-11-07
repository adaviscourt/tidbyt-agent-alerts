"""Small, dependency-free terminal components with accessible plain fallbacks."""

from contextlib import contextmanager
import getpass
import os
import select
import shutil
import signal
import sys
import termios
import textwrap
import tty
import unicodedata
import warnings

from . import __version__
from .config import AlertError


PLAIN_ENV = "TIDBYT_AGENT_ALERTS_PLAIN"
COLORS = {"accent": "33", "success": "32", "error": "31", "bold": "1"}


def visible(text):
    """Untrusted device labels must never inject terminal control sequences."""
    return " ".join("".join(c for c in str(text) if c.isprintable()).split())


def fit(text, width):
    """Truncate single-line widgets using terminal cells rather than code points."""
    result, cells = [], 0
    text = visible(text)
    for char in text:
        size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
        if cells + size > max(0, width - 3):
            return "".join(result) + "..."
        result.append(char)
        cells += size
    return text


def wrap_cells(text, width):
    """Wrap menu copy without overflowing on wide Unicode characters."""
    lines, line, cells = [], "", 0
    for word in visible(text).split():
        word_cells = sum(0 if unicodedata.combining(char) else
                         (2 if unicodedata.east_asian_width(char) in "WF" else 1) for char in word)
        if line and cells + 1 + word_cells > width:
            lines.append(line)
            line, cells = "", 0
        if line:
            line += " "
            cells += 1
        for char in word:
            size = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
            if cells + size > width:
                lines.append(line)
                line, cells = "", 0
            line += char
            cells += size
    if line:
        lines.append(line)
    return lines or [""]


class Terminal:
    def __init__(self, *, plain=False, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.size = shutil.get_terminal_size((80, 24))
        self.plain = (plain or os.environ.get(PLAIN_ENV) == "1"
                      or "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"
                      or not self.stream.isatty())
        self.color = not self.plain
        encoding = (getattr(self.stream, "encoding", None) or "ascii").lower().replace("-", "")
        self.unicode = not self.plain and encoding in ("utf8", "utf_8")
        self.width = max(20, min(88, self.size.columns - 2))

    def style(self, text, role="accent"):
        return f"\033[{COLORS[role]}m{text}\033[0m" if self.color else text

    def write(self, text=""):
        print(text, file=self.stream, flush=True)

    def mark(self, unicode, ascii):
        return unicode if self.unicode else ascii

    def intro(self, subtitle):
        self.write()
        if self.width < 34:
            self.write(self.style("Tidbyt Agent Alerts", "bold"))
            self.note(f"v{__version__}")
        else:
            self.write(self.style(self.mark("┌", "+")) + "  "
                       + self.style("Tidbyt Agent Alerts", "bold") + f"  v{__version__}")
        self.note(subtitle)

    def note(self, text):
        for line in textwrap.wrap(visible(text), max(16, self.width - 4)) or [""]:
            self.write(self.style(self.mark("│", "|")) + "  " + line)

    def step(self, number, total, title):
        self.note("")
        self.write(self.style(self.mark("◇", ">")) + "  "
                   + self.style(f"{number}/{total}  {title}", "bold"))

    def status(self, text, *, error=False):
        role = "error" if error else "success"
        marker = self.mark("×" if error else "✓", "!" if error else "ok")
        self.write(self.style(self.mark("│", "|")) + "  " + self.style(marker, role) + " " + visible(text))

    def warning(self, text):
        self.note("! " + text)

    def question(self, label, hint=None):
        self.note("")
        self.write(self.style(self.mark("◆", "?")) + "  " + self.style(visible(label), "bold"))
        if hint:
            self.note(hint)

    def prompt(self, label, default="", *, hint=None, validator=None):
        self.question(label, hint)
        suffix = f" [{visible(default)}]" if default else ""
        if len(suffix) > self.width - 8:
            self.note("Default: " + visible(default))
            suffix = ""
        while True:
            value = input(self.style(self.mark("│", "|")) + "  " + suffix.lstrip() + " > ").strip() or default
            try:
                if validator:
                    validator(value)
                return value
            except AlertError as error:
                self.warning(str(error))

    def secret(self, label, *, existing="", validator=None):
        self.question(label, "Input is hidden." + (" Press Enter to keep the saved key." if existing else ""))
        while True:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    value = getpass.getpass(self.mark("│", "|") + "  > ").strip() or existing
            except getpass.GetPassWarning:
                raise AlertError("Hidden input is unavailable. Use a terminal that can hide your API key.") from None
            try:
                if validator:
                    validator(value)
                return value
            except AlertError as error:
                self.warning(str(error))

    def confirm(self, label, default=False, *, hint=None):
        self.question(label, hint)
        suffix = "Y/n" if default else "y/N"
        while True:
            answer = input(self.style(self.mark("│", "|")) + f"  [{suffix}] > ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes", "n", "no"):
                return answer in ("y", "yes")
            self.warning("Enter yes or no, or press Enter for the default.")

    def integer(self, label, default, minimum, maximum, *, hint=None):
        def validate(value):
            try:
                valid = minimum <= int(value) <= maximum
            except ValueError:
                valid = False
            if not valid:
                raise AlertError(f"Enter a whole number from {minimum} to {maximum}.")

        range_hint = f"Use a whole number from {minimum} to {maximum}."
        return self.prompt(label, str(default), hint=range_hint + (" " + hint if hint else ""), validator=validate)

    def select(self, label, options, default, *, custom=False):
        """Options are (value, human label, explanation) tuples."""
        self.question(label)
        index = next((i for i, option in enumerate(options) if option[0] == default), 0)
        initial = ""
        if self._can_select(len(options), lines=len(self._menu_lines(options, index))):
            result, initial = self._arrow_select(options, index)
            if result is not None:
                return result
        for number, (value, title, description) in enumerate(options, 1):
            suffix = " (default)" if value == default else ""
            for line in self._option_lines(number, title + suffix, description):
                self.write(line)
        self.note("Enter a number, name, or device ID." if custom else "Enter a number or name. Press Enter for the default.")
        while True:
            answer = initial + input(self.style(self.mark("│", "|")) + f"  [{visible(default)}] > " + initial)
            initial = ""
            answer = answer.strip()
            if not answer:
                return options[index][0]
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][0]
            for value, title, _ in options:
                if answer.casefold() in (str(value).casefold(), title.casefold()):
                    return value
            if custom:
                return answer
            self.warning("Choose one of the listed options.")

    def _can_select(self, count, *, lines=None):
        return (not self.plain and sys.stdin.isatty() and self.size.columns >= 52
                and self.size.lines >= (lines if lines is not None else count) + 8 and count <= 8)

    def _option_lines(self, number, title, description, *, selected=False):
        prefix = f"{'>' if selected else ' '} {number}. "
        indent = " " * len(prefix)
        width = self.width - 3 - len(prefix)
        rail = self.mark("│", "|") + "  "
        lines = []
        for index, line in enumerate(wrap_cells(title, width)):
            label = (prefix if index == 0 else indent) + line
            lines.append(rail + (self.style(label) if selected else label))
        if description:
            lines.extend(rail + indent + line for line in wrap_cells(description, width))
        return lines

    def _menu_lines(self, options, index):
        hint = self.mark("↑/↓ to move · Enter to select · Esc to type",
                         "Up/Down: move; Enter: select; Esc: type")
        lines = [self.mark("│", "|") + "  " + hint]
        for number, (_, title, description) in enumerate(options, 1):
            lines.extend(self._option_lines(number, title, description, selected=number - 1 == index))
        return lines

    @contextmanager
    def _keyboard(self):
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
        previous = signal.getsignal(signal.SIGTERM)

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGTERM, interrupted)
            tty.setcbreak(fd)
            self.stream.write("\033[?25l")
            self.stream.flush()
            yield fd
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, original)
            finally:
                signal.signal(signal.SIGTERM, previous)
                self.stream.write("\033[?25h")
                self.stream.flush()

    def _arrow_select(self, options, index):
        count = 0

        def draw():
            nonlocal count
            lines = self._menu_lines(options, index)
            count = len(lines)
            for line in lines:
                self.write(line)

        def erase():
            self.stream.write(f"\033[{count}A\r")
            for _ in range(count):
                self.stream.write("\033[2K\n")
            self.stream.write(f"\033[{count}A\r")
            self.stream.flush()

        draw()
        try:
            with self._keyboard() as fd:
                while True:
                    key = os.read(fd, 1)
                    if not key or key == b"\x04":
                        raise EOFError
                    if key == b"\x03":
                        raise KeyboardInterrupt
                    if key in (b"\r", b"\n"):
                        erase()
                        self.status(options[index][1])
                        return options[index][0], ""
                    if key == b"\x1b":
                        sequence = b""
                        while len(sequence) < 2 and select.select([fd], [], [], 0.08)[0]:
                            sequence += os.read(fd, 1)
                        if sequence in (b"[A", b"OA", b"[B", b"OB"):
                            index = (index + (-1 if sequence[-1:] == b"A" else 1)) % len(options)
                            erase()
                            draw()
                            continue
                        erase()
                        return None, ""
                    if key.isdigit() and 1 <= int(key) <= len(options):
                        index = int(key) - 1
                        erase()
                        draw()
                    elif key.isascii() and key.isalpha():
                        erase()
                        return None, key.decode("ascii")
        except (termios.error, OSError):
            erase()
            return None, ""

    def run(self, label, action):
        line = fit(label, self.width - 6)
        self.note(line + "...")
        try:
            result = action()
        except (AlertError, OSError, RuntimeError):
            if not self.plain:
                self.stream.write("\033[1A\r\033[2K")
            self.status(line + ": failed", error=True)
            raise
        if not self.plain:
            self.stream.write("\033[1A\r\033[2K")
        self.status(line)
        return result

    def summary(self, rows, *, title="Review your setup"):
        if title:
            self.note("")
            self.write(self.style(self.mark("◇", ">")) + "  " + self.style(title, "bold"))
        label_width = min(14, max((len(visible(label)) + 1 for label, _ in rows), default=1))
        value_width = max(10, self.width - label_width - 5)
        for label, value in rows:
            lines = textwrap.wrap(visible(value), value_width) or [""]
            for index, line in enumerate(lines):
                prefix = f"{visible(label) + ':':<{label_width}} " if index == 0 else " " * (label_width + 1)
                self.write(self.style(self.mark("│", "|")) + "  " + prefix + line)

    def finish(self, title, detail=""):
        self.note("")
        self.write(self.style(self.mark("└", "+"), "success") + "  " + self.style(title, "bold"))
        if detail:
            for line in textwrap.wrap(visible(detail), max(16, self.width - 3)):
                self.write("   " + line)
        self.write()

    def error(self, message):
        self.write()
        self.write(self.style(self.mark("×", "!"), "error") + "  " + self.style("Error", "bold"))
        for line in textwrap.wrap(visible(message), max(16, self.width - 3)):
            self.write("   " + line)
        self.write()
