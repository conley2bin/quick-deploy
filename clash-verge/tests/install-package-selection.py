#!/usr/bin/env python3
"""Offline installer package-selection regressions.

The copied installer runs in a temporary directory with package-manager command
stubs. These tests never touch the real apt/dpkg database.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CLASH_VERGE_DIR = Path(__file__).resolve().parents[1]
INSTALLER = CLASH_VERGE_DIR / "install.sh"
REAL_DPKG = shutil.which("dpkg")


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def package_metadata(package: str, version: str, architecture: str) -> str:
    return f"Package: {package}\nVersion: {version}\nArchitecture: {architecture}\n"


class InstallerPackageSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        if REAL_DPKG is None:
            self.skipTest("dpkg is required for Debian version comparison")
        self.tmp = tempfile.TemporaryDirectory(prefix="clash-installer-test-")
        self.root = Path(self.tmp.name)
        self.stubs = self.root / "stubs"
        self.stubs.mkdir()
        self.script = self.root / "install.sh"
        shutil.copy2(INSTALLER, self.script)
        self.apt_log = self.root / "apt-args.json"
        self._write_stubs()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_stubs(self) -> None:
        write_executable(
            self.stubs / "dpkg",
            f"""#!/usr/bin/env python3
import os
import sys

if len(sys.argv) > 1 and sys.argv[1] == "--print-architecture":
    print(os.environ["TEST_ARCH"])
elif len(sys.argv) > 1 and sys.argv[1] == "--compare-versions":
    os.execv({REAL_DPKG!r}, [{REAL_DPKG!r}, *sys.argv[1:]])
else:
    sys.exit(2)
""",
        )
        write_executable(
            self.stubs / "dpkg-deb",
            """#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) < 3 or sys.argv[1] not in {"--field", "-f", "--info"}:
    sys.exit(2)

try:
    lines = Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
    fields = dict(line.split(": ", 1) for line in lines if ": " in line)
except (OSError, UnicodeDecodeError, ValueError):
    sys.exit(2)

if sys.argv[1] == "--info":
    if not fields:
        sys.exit(2)
    print(f"Package: {fields['Package']}")
    print(f"Version: {fields['Version']}")
    print(f"Architecture: {fields['Architecture']}")
else:
    value = fields.get(sys.argv[3])
    if value is None:
        sys.exit(2)
    print(value)
""",
        )
        write_executable(
            self.stubs / "sudo",
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

Path(os.environ["APT_LOG"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
""",
        )
        write_executable(
            self.stubs / "dpkg-query",
            """#!/usr/bin/env python3
import sys

if any("${Version}" in argument for argument in sys.argv):
    print("2.5.10")
else:
    print("installed")
""",
        )

    def run_installer(self, packages: dict[str, str], arch: str = "amd64") -> subprocess.CompletedProcess[str]:
        for name, content in packages.items():
            (self.root / name).write_text(content, encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = f"{self.stubs}{os.pathsep}{env['PATH']}"
        env["TEST_ARCH"] = arch
        env["APT_LOG"] = str(self.apt_log)
        return subprocess.run(
            ["bash", str(self.script)],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def apt_args(self) -> list[str]:
        return json.loads(self.apt_log.read_text(encoding="utf-8"))

    def test_selects_highest_version_for_current_architecture(self) -> None:
        result = self.run_installer(
            {
                "Clash.Verge_2.5.8_amd64.deb": package_metadata("clash-verge", "2.5.8", "amd64"),
                "Clash.Verge_2.5.10_amd64.deb": package_metadata("clash-verge", "2.5.10", "amd64"),
                "Clash.Verge_9.9.0_arm64.deb": package_metadata("clash-verge", "9.9.0", "arm64"),
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.apt_args()[:3], ["apt", "install", "-y"])
        self.assertEqual(Path(self.apt_args()[3]).name, "Clash.Verge_2.5.10_amd64.deb")

    def test_no_matching_architecture_fails_before_install(self) -> None:
        result = self.run_installer(
            {"Clash.Verge_2.5.8_amd64.deb": package_metadata("clash-verge", "2.5.8", "amd64")},
            arch="arm64",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未找到适配 arm64 架构", result.stderr)
        self.assertIn("Clash.Verge_*_arm64.deb", result.stderr)
        self.assertFalse(self.apt_log.exists())

    def test_metadata_mismatch_fails_before_install(self) -> None:
        result = self.run_installer(
            {"Clash.Verge_2.5.8_amd64.deb": package_metadata("not-clash-verge", "2.5.8", "amd64")}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Package=not-clash-verge", result.stderr)
        self.assertFalse(self.apt_log.exists())

    def test_architecture_metadata_mismatch_fails_before_install(self) -> None:
        result = self.run_installer(
            {"Clash.Verge_2.5.8_amd64.deb": package_metadata("clash-verge", "2.5.8", "arm64")}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Architecture=arm64", result.stderr)
        self.assertFalse(self.apt_log.exists())

    def test_corrupt_package_fails_before_install(self) -> None:
        result = self.run_installer({"Clash.Verge_2.5.8_amd64.deb": "not a deb\n"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("安装包已损坏", result.stderr)
        self.assertFalse(self.apt_log.exists())


if __name__ == "__main__":
    unittest.main()
