"""Installer regressions: temporary checkout/HOME only, never execute a receiver."""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1]
NAME = "pi-client-link-helper"


class InstallerCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pi-client-install-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.checkout = self.root / "reviewed checkout"
        self.client = self.checkout / "pi-agent/extensions/pi-copy-links/client"
        self.client.mkdir(parents=True)
        for name in ("install.sh", NAME):
            shutil.copy2(MODULE / name, self.client / name)
        self.installer = self.client / "install.sh"
        self.source = self.client / NAME
        self.home = self.root / "client home"
        self.home.mkdir()
        self.target = self.home / ".local/bin" / NAME
        self.legacy = self.checkout / "pi-agent/client-link-helper" / NAME
        self.env = os.environ.copy()
        for name in ("PYTHONPATH", "PYTHONHOME"):
            self.env.pop(name, None)
        self.env.update(HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")

    def run_install(self, *args, installer=None):
        return subprocess.run(["bash", str(installer or self.installer), *args],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=10)

    def run_fault(self, *args):
        # Execute the exact embedded installer body with mocked OS publication calls.
        # Normal behavior is always tested through the real shell entry above.
        payload = self.installer.read_text().split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(sys, "argv", ["-", str(self.installer), *args]):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    exec(compile(payload, str(self.installer), "exec"), {"__name__": "__main__"})
                except SystemExit as result:
                    return SimpleNamespace(returncode=result.code, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        self.fail("installer did not exit")

    def link(self, destination):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.symlink_to(destination)
        return self.target.lstat(), os.readlink(self.target)

    def assert_preserved(self, before):
        self.assertEqual(self.target.lstat(), before[0])
        self.assertEqual(os.readlink(self.target), before[1])
        self.assertFalse(list(self.target.parent.glob(f".{NAME}-install-*")))

    def assert_installed(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.resolve(strict=True), self.source.resolve(strict=True))
        self.assertTrue(os.access(self.target, os.X_OK))
        self.assertFalse(os.path.isabs(os.readlink(self.target)))
        self.assertFalse(list(self.target.parent.glob(f".{NAME}-install-*")))

    def foreign_source(self):
        foreign = self.root / "independent version/client" / NAME
        foreign.parent.mkdir(parents=True, exist_ok=True)
        # Identical reviewed bytes do not establish ownership of another deployment.
        shutil.copy2(self.source, foreign)
        return foreign

    def test_fresh_relative_link_and_current_noop_preserve_inodes(self):
        source_before = self.source.lstat()
        self.assert_installed(self.run_install())
        before = self.target.lstat(), os.readlink(self.target)
        for args in ((), ("--replace",)):
            result = self.run_install(*args)
            self.assertIn("already installed", result.stdout)
            self.assert_preserved(before)
        self.assertEqual(self.source.lstat(), source_before)

    def test_existing_current_absolute_link_is_a_genuine_noop(self):
        before = self.link(self.source)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already installed", result.stdout)
        self.assert_preserved(before)

    def test_exact_legacy_live_and_dangling_absolute_relative_migrate(self):
        for live in (True, False):
            for relative in (True, False):
                with self.subTest(live=live, relative=relative):
                    if live:
                        self.legacy.parent.mkdir(parents=True, exist_ok=True)
                        self.legacy.write_text("legacy receiver must never run\n")
                        self.legacy.chmod(0o755)
                    destination = os.path.relpath(self.legacy, self.target.parent) if relative else str(self.legacy)
                    self.link(destination)
                    result = self.run_install()
                    self.assert_installed(result)
                    self.assertIn("migrated same-checkout legacy symlink", result.stdout)
                    self.assertIn(destination, result.stdout)
                    if live:
                        self.assertEqual(self.legacy.read_text(), "legacy receiver must never run\n")
                        self.legacy.unlink()
                    else:
                        self.assertFalse(self.legacy.exists())
                    self.target.unlink()

    def test_checkout_alias_and_lexical_dots_recognize_exact_dangling_legacy(self):
        alias = self.root / "checkout alias"
        alias.symlink_to(self.checkout, target_is_directory=True)
        destination = str(alias / "pi-agent/client-link-helper/../client-link-helper" / NAME)
        self.link(destination)
        result = self.run_install(installer=alias / "pi-agent/extensions/pi-copy-links/client/install.sh")
        self.assert_installed(result)
        self.assertIn("migrated same-checkout legacy symlink", result.stdout)

    def test_relative_legacy_through_checkout_alias(self):
        alias = self.root / "checkout alias"
        alias.symlink_to(self.checkout, target_is_directory=True)
        self.link(os.path.relpath(alias / "pi-agent/client-link-helper" / NAME, self.target.parent))
        self.assert_installed(self.run_install())

    def test_symlinked_home_and_bin_use_physical_relative_destination(self):
        alias = self.root / "home alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.env["HOME"] = str(alias)
        self.target.parent.parent.mkdir(parents=True)
        commands = self.root / "client commands"
        commands.mkdir()
        self.target.parent.symlink_to(commands, target_is_directory=True)
        self.assert_installed(self.run_install())
        self.assertEqual(os.readlink(self.target), os.path.relpath(self.source, commands))

    def test_source_symlink_is_canonicalized_without_running_it(self):
        canonical = self.client / "reviewed receiver"
        self.source.rename(canonical)
        self.source.symlink_to(canonical.name)
        self.assert_installed(self.run_install())
        self.assertEqual(os.readlink(self.target), os.path.relpath(canonical, self.target.parent))

    def test_independent_versioned_symlink_requires_explicit_replace(self):
        foreign = self.foreign_source()
        bytes_before, stat_before = foreign.read_bytes(), foreign.stat()
        before = self.link(foreign)
        refused = self.run_install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("use --replace", refused.stderr)
        self.assert_preserved(before)
        result = self.run_install("--replace")
        self.assert_installed(result)
        self.assertIn("replaced symlink (--replace)", result.stdout)
        self.assertIn(str(foreign), result.stdout)
        self.assertEqual(foreign.read_bytes(), bytes_before)
        self.assertEqual(foreign.stat(), stat_before)

    def test_other_checkouts_exact_old_suffix_is_foreign(self):
        other = self.root / "other checkout/pi-agent/client-link-helper" / NAME
        other.parent.mkdir(parents=True)
        shutil.copy2(self.source, other)
        before = self.link(other)
        refused = self.run_install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("refusing foreign symlink", refused.stderr)
        self.assert_preserved(before)

    def test_dangling_foreign_relative_link_requires_replace(self):
        foreign = self.root / "missing version" / NAME
        before = self.link(os.path.relpath(foreign, self.target.parent))
        self.assertNotEqual(self.run_install().returncode, 0)
        self.assert_preserved(before)
        self.assert_installed(self.run_install("--replace"))
        self.assertFalse(foreign.exists())

    def test_regular_file_or_directory_refused_even_with_replace(self):
        self.target.parent.mkdir(parents=True)
        for directory in (False, True):
            with self.subTest(directory=directory):
                if directory:
                    self.target.mkdir()
                    (self.target / "owned data").write_text("keep directory")
                else:
                    self.target.write_text("keep ordinary file")
                before = self.target.lstat()
                for args in ((), ("--replace",)):
                    result = self.run_install(*args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("refusing to replace ordinary file/directory", result.stderr)
                    self.assertEqual(self.target.lstat(), before)
                if directory:
                    self.assertEqual((self.target / "owned data").read_text(), "keep directory")
                    (self.target / "owned data").unlink()
                    self.target.rmdir()
                else:
                    self.assertEqual(self.target.read_text(), "keep ordinary file")
                    self.target.unlink()

    def test_invalid_arguments_do_not_create_bin(self):
        for args in (("--bad",), ("--help",), ("--replace", "--bad"), ("--replace", "--replace")):
            with self.subTest(args=args):
                result = self.run_install(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("[--replace]", result.stderr)
                self.assertFalse(self.target.parent.exists())

    def test_missing_source_fails_before_creating_bin(self):
        self.source.unlink()
        self.assertNotEqual(self.run_install().returncode, 0)
        self.assertFalse(self.target.parent.exists())

    def test_nonexecutable_source_preserves_existing_link_even_replace(self):
        before = self.link(self.foreign_source())
        self.source.chmod(0o644)
        for args in ((), ("--replace",)):
            result = self.run_install(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source must be an executable regular file", result.stderr)
            self.assert_preserved(before)

    def test_directory_source_fails_before_creating_bin(self):
        self.source.unlink()
        self.source.mkdir()
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source must be an executable regular file", result.stderr)
        self.assertFalse(self.target.parent.exists())

    def test_stage_creation_failure_preserves_legacy_command(self):
        before = self.link(self.legacy)
        with patch("os.symlink", side_effect=OSError("controlled staging failure")):
            result = self.run_fault()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("controlled staging failure", result.stderr)
        self.assert_preserved(before)

    def test_atomic_relink_failure_preserves_legacy_command(self):
        before = self.link(self.legacy)
        with patch("os.replace", side_effect=OSError("controlled relink failure")):
            result = self.run_fault()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("controlled relink failure", result.stderr)
        self.assert_preserved(before)

    def test_source_changed_during_staging_does_not_repoint_command(self):
        before = self.link(self.legacy)
        symlink = os.symlink

        def changed_source(*args, **kwargs):
            symlink(*args, **kwargs)
            self.source.chmod(0o644)

        with patch("os.symlink", side_effect=changed_source):
            result = self.run_fault()
        self.assertNotEqual(result.returncode, 0)
        self.assert_preserved(before)

    def test_successor_before_publish_is_preserved_even_replace(self):
        foreign = self.foreign_source()
        symlink = os.symlink
        for ordinary in (False, True):
            with self.subTest(ordinary=ordinary):
                self.link(self.legacy)
                successor = self.root / "successor"
                if ordinary:
                    successor.write_text("successor data")
                else:
                    successor.symlink_to(foreign)
                expected_inode = successor.lstat().st_ino

                def changed_target(*args, **kwargs):
                    symlink(*args, **kwargs)
                    os.replace(successor, self.target)

                with patch("os.symlink", side_effect=changed_target):
                    result = self.run_fault("--replace")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.target.lstat().st_ino, expected_inode)
                if ordinary:
                    self.assertEqual(self.target.read_text(), "successor data")
                else:
                    self.assertEqual(os.readlink(self.target), str(foreign))
                self.assertFalse(list(self.target.parent.glob(f".{NAME}-install-*")))
                self.target.unlink()

    def test_fresh_publish_cannot_overwrite_competing_file(self):
        link = os.link

        def raced_link(*args, **kwargs):
            self.target.write_text("new owner data")
            return link(*args, **kwargs)

        with patch("os.link", side_effect=raced_link):
            result = self.run_fault()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.target.read_text(), "new owner data")
        self.assertFalse(list(self.target.parent.glob(f".{NAME}-install-*")))

    def test_noop_relink_call_cannot_report_success(self):
        before = self.link(self.legacy)
        with patch("os.replace", return_value=None):
            result = self.run_fault()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(": installed ", result.stdout)
        self.assert_preserved(before)

    def test_successor_after_publish_is_not_removed_or_restored(self):
        self.link(self.legacy)
        foreign = self.foreign_source()
        successor = self.root / "successor"
        successor.symlink_to(foreign)
        expected_inode = successor.lstat().st_ino
        replace = os.replace

        def replaced_after_publish(*args, **kwargs):
            replace(*args, **kwargs)
            replace(successor, self.target)

        with patch("os.replace", side_effect=replaced_after_publish):
            result = self.run_fault()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed after publication", result.stderr)
        self.assertEqual(self.target.lstat().st_ino, expected_inode)
        self.assertEqual(os.readlink(self.target), str(foreign))
        self.assertFalse(list(self.target.parent.glob(f".{NAME}-install-*")))


if __name__ == "__main__":
    unittest.main()
