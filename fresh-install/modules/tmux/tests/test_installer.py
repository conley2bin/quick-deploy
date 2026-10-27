#!/usr/bin/python3
"""Offline ownership/idempotence/rollback tests; no user-manager mutation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

INSTALL = Path(__file__).resolve().parents[1] / "install-runtime.py"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="resilience-install-test-")
        self.home = Path(self.temp.name)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"))

    def tearDown(self):
        self.temp.cleanup()

    def install(self, *args):
        return subprocess.run([sys.executable, str(INSTALL), "--no-reload", *args], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_jobs_then_full_idempotent_and_rollback(self):
        self.assertEqual(self.install("--jobs-only").returncode, 0)
        self.assertTrue((self.home / ".local/bin/job").is_symlink())
        self.assertFalse((self.home / ".local/bin/tmux-managed").exists())
        self.assertEqual(self.install().returncode, 0)
        paths = sorted(self.home.rglob("*"))
        before = [(str(p), os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in paths if not p.is_dir()]
        self.assertEqual(self.install().returncode, 0)
        after = [(str(p), os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in paths if not p.is_dir()]
        self.assertEqual(before, after)
        config = self.home / ".config/workstation-resilience/tmux.json"
        config.write_text('{"executable":"/user/custom/tmux"}\n')
        self.assertEqual(self.install("--uninstall").returncode, 0)
        self.assertTrue(config.exists())
        self.assertFalse((self.home / ".local/bin/job").exists())
        self.assertFalse((self.home / ".local/bin/tmux-managed").exists())
        policy = self.home / ".config/systemd/user/jobs.slice"
        self.assertTrue(policy.is_symlink(), "rollback must retain aggregate policy through any daemon-reload")
        self.assertTrue((self.home / ".config/workstation-resilience/install.json").exists())
        self.assertEqual(config.read_text(), '{"executable":"/user/custom/tmux"}\n')

    def test_jobs_only_rollback_explicitly_retains_policy(self):
        self.assertEqual(self.install("--jobs-only").returncode, 0)
        policy = self.home / ".config/systemd/user/jobs.slice"
        before = policy.read_bytes()
        result = self.install("--jobs-only", "--uninstall")
        self.assertEqual(result.returncode, 0)
        self.assertIn("retained aggregate policy", result.stdout)
        self.assertTrue(policy.is_symlink())
        self.assertEqual(policy.read_bytes(), before)
        self.assertFalse((self.home / ".local/bin/job").exists())
        self.assertEqual(self.install("--jobs-only").returncode, 0)

    def test_foreign_path_refuses_before_partial_install(self):
        foreign = self.home / ".local/bin/tmux-managed"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("foreign user file")
        proc = self.install()
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(foreign.read_text(), "foreign user file")
        self.assertFalse((self.home / ".local/bin/job").exists())


if __name__ == "__main__":
    unittest.main()
