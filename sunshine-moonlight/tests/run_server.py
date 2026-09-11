#!/usr/bin/python3
"""Subprocess tests for the inventory connector; no network, GUI, or service calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

MODULE = Path(__file__).resolve().parents[1]

FAKE_PROGRAM = """#!/usr/bin/python3
import json
import os
import sys
from pathlib import Path
Path(os.environ['FAKE_LOG']).write_text(json.dumps({'argv': sys.argv, 'stdin': sys.stdin.read()}))
raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))
"""

VALID = """\
machines:
  desktop:
    ssh: conley@100.64.0.2
    tailnet_ip: 100.64.0.2
  laptop:
    ssh: laptop-alias
    tailnet_ip: 100.64.0.3
    moonlight_port: 48000
    ssh_port: 2222
    note: never forwarded
"""


class RunServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="qd run server ")
        self.root = Path(self.tmp.name)
        self.module = self.root / "repo with spaces" / "sunshine-moonlight"
        (self.module / "service").mkdir(parents=True)
        shutil.copy2(MODULE / "run_server.sh", self.module / "run_server.sh")
        shutil.copy2(MODULE / "service/run-server.py", self.module / "service/run-server.py")
        self.home = self.root / "fake home"
        self.bin = self.root / "fake bin"
        self.log = self.root / "program.json"
        self.home.mkdir()
        self.bin.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{self.bin}:{os.environ['PATH']}", FAKE_LOG=str(self.log))
        self.write_program(self.home / ".local/bin/moonlight")
        self.write_program(self.bin / "ssh")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_program(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FAKE_PROGRAM)
        path.chmod(0o755)

    def write_default(self, content: str = VALID) -> Path:
        path = self.module / "machines.local.yaml"
        path.write_text(textwrap.dedent(content))
        return path

    def invoke(self, *args: str, cwd: Path | None = None, stdin: str = "", exit_code: int = 0) -> subprocess.CompletedProcess[str]:
        self.log.unlink(missing_ok=True)
        env = dict(self.env, FAKE_EXIT=str(exit_code))
        return subprocess.run(
            [str(self.module / "run_server.sh"), *args],
            cwd=cwd or self.root,
            env=env,
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
        )

    def recorded(self) -> dict[str, object]:
        return json.loads(self.log.read_text())

    def assert_no_program(self) -> None:
        self.assertFalse(self.log.exists(), self.log.read_text() if self.log.exists() else "")

    def test_default_moonlight_exact_argv_stdin_and_exit(self) -> None:
        self.write_default()
        result = self.invoke("desktop", stdin="stdin remains attached\n", exit_code=23)
        self.assertEqual(result.returncode, 23, result.stderr)
        record = self.recorded()
        self.assertEqual(record["argv"][1:], ["stream", "--", "100.64.0.2:47989", "Desktop"])
        self.assertEqual(record["stdin"], "stdin remains attached\n")

    def test_explicit_moonlight_and_custom_config_are_cwd_relative(self) -> None:
        config_dir = self.root / "config cwd"
        config_dir.mkdir()
        (config_dir / "custom inventory.yaml").write_text(textwrap.dedent(VALID))
        result = self.invoke("--config", "custom inventory.yaml", "--moonlight", "laptop", cwd=config_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["stream", "--", "100.64.0.3:48000", "Desktop"])

    def test_ssh_exact_argv_with_and_without_optional_port(self) -> None:
        self.write_default()
        result = self.invoke("--ssh", "laptop", stdin="ssh stdin\n", exit_code=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["-p", "2222", "laptop-alias"])
        self.assertEqual(self.recorded()["stdin"], "ssh stdin\n")

        result = self.invoke("--ssh", "desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["conley@100.64.0.2"])

    def test_list_uses_script_relative_default_without_child_or_write(self) -> None:
        inventory = self.write_default()
        before = inventory.read_bytes()
        unrelated_cwd = self.root / "elsewhere"
        unrelated_cwd.mkdir()
        result = self.invoke("--list", cwd=unrelated_cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop", "laptop"])
        self.assertEqual(inventory.read_bytes(), before)
        self.assert_no_program()

    def test_help_does_not_require_helper_or_inventory(self) -> None:
        (self.module / "service/run-server.py").unlink()
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--list", result.stdout)
        self.assert_no_program()

    def test_invalid_utf8_inventory_reports_path_without_traceback_or_child(self) -> None:
        inventory = self.module / "machines.local.yaml"
        inventory.write_bytes(b"machines: \xff\n")
        result = self.invoke("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(inventory), result.stderr)
        self.assertIn("UTF-8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_no_program()

    def test_missing_inventory_unknown_target_and_parser_errors_do_not_exec(self) -> None:
        result = self.invoke("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单不存在", result.stderr)
        self.assert_no_program()

        self.write_default()
        for args in (("unknown",), ("--list", "desktop"), ("--unknown",), ("--ssh", "--moonlight", "desktop")):
            with self.subTest(args=args):
                result = self.invoke(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assert_no_program()

    def test_invalid_yaml_schema_duplicates_types_and_injection_never_exec(self) -> None:
        invalid_cases = {
            "syntax": "machines: [",
            "duplicate": """\
machines:
  desktop:
    ssh: host
    tailnet_ip: 100.64.0.2
  desktop:
    ssh: other
    tailnet_ip: 100.64.0.3
""",
            "unsafe-tag": "machines: !!python/object {}",
            "extra-field": """\
machines:
  desktop:
    ssh: host
    tailnet_ip: 100.64.0.2
    command: rm -rf /
""",
            "bool-port": """\
machines:
  desktop:
    ssh: host
    tailnet_ip: 100.64.0.2
    moonlight_port: true
""",
            "string-port": """\
machines:
  desktop:
    ssh: host
    tailnet_ip: 100.64.0.2
    ssh_port: '22'
""",
            "bad-ip": """\
machines:
  desktop:
    ssh: host
    tailnet_ip: 100.64.0.2; --option
""",
            "ssh-injection": """\
machines:
  desktop:
    ssh: host -oProxyCommand=evil
    tailnet_ip: 100.64.0.2
""",
        }
        for label, content in invalid_cases.items():
            with self.subTest(case=label):
                self.write_default(content)
                result = self.invoke("desktop")
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assert_no_program()


if __name__ == "__main__":
    unittest.main()
