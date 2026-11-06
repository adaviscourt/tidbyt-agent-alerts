"""Exercise the real piped bootstrap with local release fixtures, never the network."""

import errno
import hashlib
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest

from scripts.package import package
from tidbyt_agent_alerts import __version__


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
INSTALLER = '''import argparse
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--prefix", type=Path, required=True)
parser.add_argument("--no-setup", action="store_true")
args = parser.parse_args()
result = {"isatty": sys.stdin.isatty(), "no_setup": args.no_setup}
if not args.no_setup:
    result["answer"] = input("Setup prompt: ")
args.prefix.mkdir(parents=True, exist_ok=True)
(args.prefix / "result.json").write_text(json.dumps(result))
'''


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.prefix = self.root / "install with space and 'quote"
        (self.bin / "python3").symlink_to(Path(sys.executable).resolve())
        (self.bin / "pixlet").write_text("#!/bin/sh\nexit 0\n")
        (self.bin / "pixlet").chmod(0o755)
        curl = self.bin / "curl"
        curl.write_text(f'''#!{sys.executable}
import json
from pathlib import Path
import shutil
import sys

root = Path({str(self.root)!r})
args = sys.argv[1:]
url = args[-1]
with (root / "requests.jsonl").open("a") as log:
    log.write(json.dumps({{"url": url, "args": args}}) + "\\n")
if ((root / "fail-download").exists()
        or ((root / "fail-archive").exists() and not url.endswith("/SHA256SUMS"))):
    sys.exit(22)
assert url.startswith("https://github.com/adaviscourt/tidbyt-agent-alerts/releases/")
assert args[args.index("--proto") + 1] == "=https"
assert args[args.index("--proto-redir") + 1] == "=https"
output = Path(args[args.index("--output") + 1])
filename = "SHA256SUMS" if url.endswith("/SHA256SUMS") else "release.tar.gz"
shutil.copyfile(root / filename, output)
''')
        curl.chmod(0o755)
        self.env = os.environ | {
            "HOME": str(self.root), "TMPDIR": str(self.root),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
        }
        self.release()

    def release(self, extra=None, version=VERSION):
        archive = self.root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            content = INSTALLER.encode()
            member = tarfile.TarInfo(f"tidbyt-agent-alerts-{version}/install.py")
            member.size = len(content)
            handle.addfile(member, io.BytesIO(content))
            if extra is not None:
                handle.addfile(extra, io.BytesIO(b"bad") if extra.isfile() else None)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (self.root / "SHA256SUMS").write_text(
            f"{digest}  tidbyt-agent-alerts-{version}.tar.gz\n" + "0" * 64 + "  bootstrap.sh\n"
        )

    def command(self, *args):
        return ["/bin/bash", "-c", 'cat "$1" | /bin/bash -s -- "${@:2}"',
                "bootstrap-test", str(ROOT / "bootstrap.sh"), "--prefix", str(self.prefix), *args]

    def run_bootstrap(self, *args):
        return subprocess.run(self.command(*args), env=self.env, cwd=self.root,
                              capture_output=True, text=True, start_new_session=True, timeout=20)

    def requests(self):
        path = self.root / "requests.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_cleaned(self):
        self.assertEqual(list(self.root.glob("tidbyt-agent-alerts-*")), [])

    def test_piped_headless_install_resolves_latest_then_pins_archive(self):
        result = self.run_bootstrap("--no-setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.prefix / "result.json").read_text()), {"isatty": False, "no_setup": True})
        requests = self.requests()
        self.assertTrue(requests[0]["url"].endswith("/latest/download/SHA256SUMS"))
        self.assertTrue(requests[1]["url"].endswith(f"/download/v{VERSION}/tidbyt-agent-alerts-{VERSION}.tar.gz"))
        self.assert_cleaned()

    def test_pinned_version_and_leading_v(self):
        result = self.run_bootstrap("--no-setup", "--version", "v" + VERSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.requests()[0]["url"].endswith(f"/download/v{VERSION}/SHA256SUMS"))

    def test_piped_interactive_setup_uses_controlling_terminal(self):
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(self.root)
            os.execve("/bin/bash", self.command(), self.env)
        captured = b""
        answered = False
        reaped = False
        deadline = time.monotonic() + 20
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([master], [], [], remaining)[0]:
                    self.fail("Terminal setup timed out: " + repr(captured))
                try:
                    chunk = os.read(master, 4096)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break  # Linux reports PTY closure as EIO.
                    raise
                if not chunk:
                    break
                captured += chunk
                if not answered and b"Setup prompt:" in captured:
                    os.write(master, b"terminal-answer\n")
                    answered = True
            _, status = os.waitpid(pid, 0)
            reaped = True
        finally:
            os.close(master)
            if not reaped:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, captured.decode())
        self.assertIn(b"Setup prompt:", captured)
        self.assertEqual(json.loads((self.prefix / "result.json").read_text()),
                         {"isatty": True, "no_setup": False, "answer": "terminal-answer"})
        self.assert_cleaned()

    def test_interactive_mode_without_terminal_stops_before_download(self):
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--no-setup", result.stderr)
        self.assertEqual(self.requests(), [])
        self.assertFalse(self.prefix.exists())

    def test_checksum_mismatch_never_runs_installer(self):
        with (self.root / "release.tar.gz").open("ab") as handle:
            handle.write(b"tampered")
        result = self.run_bootstrap("--no-setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertFalse(self.prefix.exists())
        self.assert_cleaned()

    def test_wrong_release_version_stops_before_archive_download(self):
        result = self.run_bootstrap("--no-setup", "--version", "9.9.9")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stderr)
        self.assertEqual(len(self.requests()), 1)
        self.assertFalse(self.prefix.exists())
        self.assert_cleaned()

    def test_malformed_duplicate_or_unsafe_manifests_are_rejected(self):
        manifest = self.root / "SHA256SUMS"
        original = manifest.read_text()
        for content in ("not a manifest", original + original,
                        "0" * 64 + "  ../../escape.tar.gz\n"):
            manifest.write_text(content)
            result = self.run_bootstrap("--no-setup")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one", result.stderr)
            self.assertFalse(self.prefix.exists())
            self.assert_cleaned()

    def test_path_traversal_and_links_are_rejected_after_checksum_verification(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           (f"tidbyt-agent-alerts-{VERSION}/link", tarfile.SYMTYPE),
                           (f"tidbyt-agent-alerts-{VERSION}/hardlink", tarfile.LNKTYPE)):
            entry = tarfile.TarInfo(name)
            entry.type = kind
            entry.linkname = "../../escape"
            entry.size = 3 if kind == tarfile.REGTYPE else 0
            self.release(extra=entry)
            result = self.run_bootstrap("--no-setup")
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("Unsafe or invalid", result.stderr)
            self.assertFalse(self.prefix.exists())
            self.assertFalse((self.root / "escape").exists())
            self.assert_cleaned()

    def test_failed_download_stops_without_installing(self):
        (self.root / "fail-download").touch()
        result = self.run_bootstrap("--no-setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not download", result.stderr)
        self.assertFalse(self.prefix.exists())
        self.assert_cleaned()

    def test_failed_archive_download_stops_without_installing(self):
        (self.root / "fail-archive").touch()
        result = self.run_bootstrap("--no-setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not download the release archive", result.stderr)
        self.assertEqual(len(self.requests()), 2)
        self.assertFalse(self.prefix.exists())
        self.assert_cleaned()

    def test_truncated_bootstrap_never_starts_installation(self):
        truncated = self.root / "truncated.sh"
        truncated.write_text((ROOT / "bootstrap.sh").read_text().rsplit('main "$@"', 1)[0])
        command = self.command("--no-setup")
        command[4] = str(truncated)
        result = subprocess.run(command, env=self.env, cwd=self.root, capture_output=True,
                                text=True, start_new_session=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests(), [])
        self.assertFalse(self.prefix.exists())

    def test_invalid_options_fail_before_network_access(self):
        for args in (("--version", "../../escape"), ("--version",), ("--unknown",)):
            result = self.run_bootstrap(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.requests(), [])

    def test_help_never_downloads_or_installs(self):
        result = self.run_bootstrap("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertEqual(self.requests(), [])
        self.assertFalse(self.prefix.exists())

    def test_real_packaged_release_installs_through_pipe(self):
        output = package(self.root / "packaged")
        (self.root / "release.tar.gz").write_bytes(output.read_bytes())
        (self.root / "SHA256SUMS").write_bytes((output.parent / "SHA256SUMS").read_bytes())
        result = self.run_bootstrap("--no-setup", "--version", __version__)
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.prefix / "bin/tidbyt-agent-alerts"
        installed = subprocess.run([str(launcher), "--version"], env=self.env, cwd=self.root,
                                   check=True, capture_output=True, text=True)
        self.assertEqual(installed.stdout.strip(), __version__)
        self.assert_cleaned()
