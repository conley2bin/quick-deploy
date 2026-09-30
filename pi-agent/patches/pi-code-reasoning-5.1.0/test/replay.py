#!/usr/bin/env python3
"""Offline guarded-replay tests; never writes a real Pi installation."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = (ROOT / "test/fixtures/package.json").read_bytes()


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="pi-reasoning-replay-")
        self.addCleanup(self.scratch.cleanup)
        self.target = Path(self.scratch.name)
        self.manifest = self.target / "package.json"
        self.manifest.write_bytes(ORIGINAL)
        self.manifest.chmod(0o640)
        # These represent files the repair must not touch.
        (self.target / "package-lock.json").write_text("keep lock unchanged\n")
        (self.target / "node_modules").mkdir()
        (self.target / "node_modules/keep").write_text("keep dependencies unchanged\n")

    def apply(self):
        return subprocess.run(["bash", str(ROOT / "apply.sh"), str(self.target)],
                              text=True, capture_output=True)

    def assert_unchanged_dependents(self):
        self.assertEqual((self.target / "package-lock.json").read_text(), "keep lock unchanged\n")
        self.assertEqual((self.target / "node_modules/keep").read_text(), "keep dependencies unchanged\n")

    def test_pristine_repair_and_idempotence(self):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = json.loads(ORIGINAL)
        del expected["dependencies"]["typebox"]
        expected["peerDependencies"]["typebox"] = "*"
        self.assertEqual(json.loads(self.manifest.read_bytes()), expected)
        self.assertEqual(self.manifest.stat().st_mode & 0o777, 0o640)
        repaired = self.manifest.read_bytes()
        before_stat = self.manifest.stat()
        again = self.apply()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already applied", again.stdout)
        self.assertEqual(self.manifest.read_bytes(), repaired)
        self.assertEqual(self.manifest.stat().st_ino, before_stat.st_ino)
        self.assertEqual(self.manifest.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assert_unchanged_dependents()
        self.assertFalse(list(self.target.glob(".pi-code-reasoning-peer-*")))

    def assert_rejected_without_write(self, value):
        self.manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        original = self.manifest.read_bytes()
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("guard failed", result.stderr)
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assert_unchanged_dependents()

    def test_unknown_version(self):
        value = json.loads(ORIGINAL)
        value["version"] = "5.2.0"
        self.assert_rejected_without_write(value)

    def test_local_manifest_edit(self):
        value = json.loads(ORIGINAL)
        value["description"] += " local edit"
        self.assert_rejected_without_write(value)

    def test_wrong_package_identity(self):
        value = json.loads(ORIGINAL)
        value["name"] = "another-extension"
        self.assert_rejected_without_write(value)

    def test_manifest_symlink(self):
        other = self.target / "original.json"
        shutil.move(self.manifest, other)
        self.manifest.symlink_to(other.name)
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.manifest.is_symlink())
        self.assertEqual(other.read_bytes(), ORIGINAL)
        self.assert_unchanged_dependents()


if __name__ == "__main__":
    unittest.main()
