import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

from install import install
from scripts.package import package
from tidbyt_agent_alerts import __version__


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.prefix = self.home / "prefix with space and 'quote"
        self.env = os.environ | {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / "config"), "XDG_STATE_HOME": str(self.home / "state")}

    def test_installed_launcher_runs_from_arbitrary_directory(self):
        launcher = install(self.prefix, ROOT)
        result = subprocess.run([str(launcher), "--version"], cwd=self.home, env=self.env,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), __version__)
        self.assertNotIn(str(ROOT), launcher.read_text())
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)

    def test_plain_install_remains_readable_without_control_sequences(self):
        result = subprocess.run([sys.executable, str(ROOT / "install.py"), "--prefix", str(self.prefix),
                                 "--no-setup", "--plain"], env=self.env, cwd=self.home,
                                capture_output=True, text=True, check=True)
        self.assertIn("Command installed", result.stdout)
        self.assertNotIn("\033", result.stdout + result.stderr)

    def test_installed_hooks_use_stable_launcher_not_run_script(self):
        launcher = install(self.prefix, ROOT)
        config = self.home / "config.env"
        config.write_text('TIDBYT_API_KEY="key"\nTIDBYT_DEVICE_ID="desk"\n')
        config.chmod(0o600)
        settings = self.home / "settings.json"
        subprocess.run([str(launcher), "--config", str(config), "--claude-settings", str(settings),
                        "install-hook"], cwd=self.home, env=self.env, check=True, capture_output=True)
        hook = json.loads(settings.read_text())["hooks"]["Notification"][0]["hooks"][0]
        self.assertIn("/bin/tidbyt-agent-alerts", hook["command"])
        self.assertNotIn("run.py", hook["command"])

    def test_upgrade_preserves_external_configuration(self):
        launcher = install(self.prefix, ROOT)
        config = self.home / "private.env"
        config.write_text("KEEP CONFIG")
        self.assertEqual(install(self.prefix, ROOT), launcher)
        self.assertEqual(config.read_text(), "KEEP CONFIG")
        self.assertTrue((self.prefix / "share/tidbyt-agent-alerts/installation.json").exists())

    def test_refuses_unowned_launcher_and_directory(self):
        launcher = self.prefix / "bin/tidbyt-agent-alerts"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("unrelated executable")
        with self.assertRaisesRegex(RuntimeError, "unrelated executable"):
            install(self.prefix, ROOT)
        self.assertEqual(launcher.read_text(), "unrelated executable")
        launcher.unlink()
        target = self.prefix / "share/tidbyt-agent-alerts"
        target.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            install(self.prefix, ROOT)

    def test_refuses_symlinked_launcher(self):
        launcher = self.prefix / "bin/tidbyt-agent-alerts"
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to(self.home / "elsewhere")
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            install(self.prefix, ROOT)

    def test_uninstall_is_scoped_and_purge_removes_selected_config(self):
        launcher = install(self.prefix, ROOT)
        settings = self.home / "claude-settings.json"
        settings.write_text('{"theme":"dark","hooks":{"Stop":[{"hooks":[{"command":"user-hook"}]}]}}')
        original = json.loads(settings.read_text())
        config = self.home / "config.env"
        config.write_text("credentials")
        unrelated = self.prefix / "bin/unrelated"
        unrelated.write_text("keep")
        result = subprocess.run([str(launcher), "--config", str(config), "--claude-settings", str(settings),
                                 "uninstall", "--yes", "--purge"], cwd=self.home, env=self.env,
                                capture_output=True, text=True, check=True)
        self.assertIn("Uninstalled", result.stdout)
        self.assertFalse(launcher.exists())
        self.assertFalse(config.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(json.loads(settings.read_text()), original)
        self.assertFalse((self.prefix / "share/tidbyt-agent-alerts").exists())

    def test_archive_checksum_and_install_from_extracted_release(self):
        archive = package(self.home / "dist")
        checksum = (archive.parent / "SHA256SUMS").read_text().split()[0]
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), checksum)
        bootstrap = archive.parent / "bootstrap.sh"
        self.assertEqual(bootstrap.read_bytes(), (ROOT / "bootstrap.sh").read_bytes())
        checksums = (archive.parent / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(checksums[1], hashlib.sha256(bootstrap.read_bytes()).hexdigest() + "  bootstrap.sh")
        with tarfile.open(archive) as handle:
            names = handle.getnames()
            self.assertFalse(any("__pycache__" in name or "/.git/" in name for name in names))
            self.assertFalse(any(name.endswith("/config.env") for name in names))
            self.assertTrue(any(name.endswith("/bootstrap.sh") for name in names))
            # This is the archive just built from the explicit, local allowlist.
            handle.extractall(self.home / "extracted")
        source = self.home / f"extracted/tidbyt-agent-alerts-{__version__}"
        subprocess.run([sys.executable, str(source / "install.py"), "--prefix", str(self.prefix), "--no-setup"],
                       env=self.env, check=True, capture_output=True)
        launcher = self.prefix / "bin/tidbyt-agent-alerts"
        self.assertTrue(launcher.exists())
