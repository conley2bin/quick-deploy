#!/usr/bin/python3
"""Subprocess tests for the inventory connector; no network, GUI, or service calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

MODULE = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[\u4e00-\u9fff]")

FAKE_PROGRAM = """#!/usr/bin/python3
import json
import os
import sys
from pathlib import Path
Path(os.environ['FAKE_LOG']).write_text(json.dumps({'argv': sys.argv, 'stdin': sys.stdin.read()}))
raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))
"""

FAKE_TAILSCALE = """#!/usr/bin/python3
import os
import sys
from pathlib import Path
with Path(os.environ['FAKE_TAILSCALE_LOG']).open('a') as log:
    log.write(' '.join(sys.argv[1:]) + '\\n')
raise SystemExit(0)
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

LEGACY_ONLY = """\
machines:
  legacy:
    ssh: legacy-host
    tailnet_ip: 100.64.0.8
"""

DECOY = """\
machines:
  decoy:
    ssh: decoy-host
    tailnet_ip: 100.64.0.99
"""

# The connector must never parse this file: the alias stays unresolved in argv.
SSH_CONFIG = """\
Host laptop-alias
  HostName 203.0.113.9
  User sshconfig-sentinel
"""


class RunServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="qd run server ")
        self.root = Path(self.tmp.name)
        self.module = self.root / "repo with spaces" / "sunshine-moonlight"
        (self.module / "service").mkdir(parents=True)
        (self.module / "lib").mkdir()
        shutil.copy2(MODULE / "run_server.sh", self.module / "run_server.sh")
        shutil.copy2(MODULE / "service/run-server.py", self.module / "service/run-server.py")
        shutil.copy2(MODULE / "lib/machines_inventory.py", self.module / "lib/machines_inventory.py")
        self.default = self.module / "machines.yaml"
        self.legacy = self.module / "machines.local.yaml"
        self.stale = self.module / "machines.example.yaml"
        self.home = self.root / "fake home"
        self.bin = self.root / "fake bin"
        self.log = self.root / "program.json"
        self.tailscale_log = self.root / "tailscale.log"
        self.home.mkdir()
        self.bin.mkdir()
        self.env = dict(
            os.environ,
            HOME=str(self.home),
            PATH=f"{self.bin}:{os.environ['PATH']}",
            FAKE_LOG=str(self.log),
            FAKE_TAILSCALE_LOG=str(self.tailscale_log),
        )
        self.write_program(self.home / ".local/bin/moonlight")
        self.write_program(self.bin / "ssh")
        self.write_program(self.bin / "tailscale", FAKE_TAILSCALE)
        ssh_dir = self.home / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "config").write_text(SSH_CONFIG)
        (ssh_dir / "known_hosts").write_text("100.64.0.9 ssh-ed25519 AAAAsentinelknownhost\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_program(self, path: Path, content: str = FAKE_PROGRAM) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    def generate_inventory(self) -> subprocess.CompletedProcess[str]:
        """Run the module's real generator from an unrelated cwd, exactly as one
        successful module-root install leaves the inventory behind."""
        result = subprocess.run(
            [sys.executable, str(self.module / "lib/machines_inventory.py")],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def run_generator(self) -> subprocess.CompletedProcess[str]:
        """Run the real generator without asserting success (refusal probes)."""
        return subprocess.run(
            [sys.executable, str(self.module / "lib/machines_inventory.py")],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )

    def write_default(self, content: str = VALID) -> Path:
        self.default.write_text(textwrap.dedent(content))
        return self.default

    def write_legacy(self, content: str = LEGACY_ONLY) -> Path:
        self.legacy.write_text(textwrap.dedent(content))
        return self.legacy

    def inventory_bytes(self) -> dict[str, bytes | None]:
        return {
            path.name: path.read_bytes() if path.exists() else None
            for path in (self.default, self.legacy, self.stale)
        }

    def ssh_bytes(self) -> dict[str, bytes]:
        return {path.name: path.read_bytes() for path in sorted((self.home / ".ssh").iterdir())}

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

    def assert_no_tailscale(self) -> None:
        self.assertFalse(
            self.tailscale_log.exists(),
            self.tailscale_log.read_text() if self.tailscale_log.exists() else "",
        )

    def test_default_moonlight_exact_argv_stdin_and_exit(self) -> None:
        self.write_default()
        result = self.invoke("desktop", stdin="stdin remains attached\n", exit_code=23)
        self.assertEqual(result.returncode, 23, result.stderr)
        record = self.recorded()
        self.assertEqual(record["argv"][1:], ["stream", "--", "100.64.0.2:47989", "Desktop"])
        self.assertEqual(record["stdin"], "stdin remains attached\n")
        self.assert_no_tailscale()

    def test_explicit_moonlight_and_custom_config_are_cwd_relative(self) -> None:
        config_dir = self.root / "config cwd"
        config_dir.mkdir()
        (config_dir / "custom inventory.yaml").write_text(textwrap.dedent(VALID))
        result = self.invoke("--config", "custom inventory.yaml", "--moonlight", "laptop", cwd=config_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["stream", "--", "100.64.0.3:48000", "Desktop"])
        self.assertFalse(self.default.exists())

        missing = config_dir / "absent inventory.yaml"
        result = self.invoke("--config", "absent inventory.yaml", "--list", cwd=config_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单不存在", result.stderr)
        self.assertIn("absent inventory.yaml", result.stderr)
        self.assertFalse(missing.exists())
        self.assert_no_program()

    def test_ssh_exact_argv_with_and_without_optional_port(self) -> None:
        self.write_default()
        result = self.invoke("--ssh", "laptop", stdin="ssh stdin\n", exit_code=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["-p", "2222", "laptop-alias"])
        self.assertEqual(self.recorded()["stdin"], "ssh stdin\n")

        result = self.invoke("--ssh", "desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["conley@100.64.0.2"])

    def test_default_inventory_is_module_relative_ignoring_cwd_file(self) -> None:
        inventory = self.write_default()
        before = inventory.read_bytes()
        unrelated_cwd = self.root / "elsewhere"
        unrelated_cwd.mkdir()
        decoy = unrelated_cwd / "machines.yaml"
        decoy.write_text(textwrap.dedent(DECOY))
        decoy_before = decoy.read_bytes()
        result = self.invoke("--list", cwd=unrelated_cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop", "laptop"])
        self.assertEqual(inventory.read_bytes(), before)
        self.assertEqual(decoy.read_bytes(), decoy_before)
        self.assert_no_program()
        self.assert_no_tailscale()

    def test_help_documents_new_default_without_helper_or_inventory(self) -> None:
        (self.module / "service/run-server.py").unlink()
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--list", result.stdout)
        self.assertIn("machines.yaml", result.stdout)
        self.assertIn("不探测 Tailscale", result.stdout)
        self.assertNotIn("machines.local.yaml", result.stdout)
        self.assertNotIn("machines.example.yaml", result.stdout)
        self.assertNotIn("cp ", result.stdout)
        self.assert_no_program()

    def test_fresh_clone_without_generated_inventory_guides_one_successful_install(self) -> None:
        # A fresh clone has no inventory until one root install succeeds: the connector
        # must name the install step and never offer to copy anything for the user.
        result = self.invoke("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单不存在", result.stderr)
        self.assertIn(str(self.default), result.stderr)
        self.assertIn("./install.sh", result.stderr)
        self.assertIn("直接编辑", result.stderr)
        self.assertFalse(
            any(line.strip().startswith("cp ") for line in result.stderr.splitlines()), result.stderr
        )
        self.assertFalse(self.default.exists())
        self.assertFalse(self.legacy.exists())
        self.assertFalse(self.stale.exists())
        self.assert_no_program()
        self.assert_no_tailscale()

    def test_absent_default_never_falls_back_to_leftover_legacy_file(self) -> None:
        # A hand-edited leftover machines.example.yaml must stay invisible: no path,
        # alias or byte from it may reach the user or any child process.
        self.stale.write_text(textwrap.dedent(DECOY))
        stale_before = self.stale.read_bytes()
        legacy = self.write_legacy()
        legacy_before = legacy.read_bytes()
        result = self.invoke("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单不存在", result.stderr)
        self.assertIn(str(self.default), result.stderr)
        self.assertIn("./install.sh", result.stderr)
        self.assertNotIn(str(self.stale), result.stderr)
        self.assertNotIn("decoy-host", result.stdout + result.stderr)
        self.assertFalse(self.default.exists())
        self.assertEqual(self.stale.read_bytes(), stale_before)
        self.assertEqual(legacy.read_bytes(), legacy_before)
        self.assert_no_program()
        self.assert_no_tailscale()

    def test_hyphen_leading_explicit_config_advice_and_explicit_read(self) -> None:
        # A literal `--config=PATH` keeps a leading-hyphen path usable as a path, not
        # as an option; the connector never creates or guesses the file it was given.
        config_dir = self.root / "custom cwd"
        config_dir.mkdir()
        target = config_dir / "-inventory.yaml"

        result = self.invoke("--config=-inventory.yaml", "--list", cwd=config_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单不存在", result.stderr)
        self.assertIn("-inventory.yaml", result.stderr)
        self.assertIn(str(self.default), result.stderr)
        self.assertFalse(
            any(line.strip().startswith("cp ") for line in result.stderr.splitlines()), result.stderr
        )
        self.assertFalse(target.exists())
        self.assertFalse(self.default.exists())
        self.assert_no_program()
        self.assert_no_tailscale()

        # The same path loads once the user supplies it explicitly.
        target.write_text(textwrap.dedent(VALID))
        result = self.invoke("--config=-inventory.yaml", "--list", cwd=config_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop", "laptop"])
        result = self.invoke("--config=-inventory.yaml", "--moonlight", "desktop", cwd=config_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["stream", "--", "100.64.0.2:47989", "Desktop"])

        self.assertFalse(self.default.exists())
        self.assertFalse(self.legacy.exists())
        self.assert_no_tailscale()

    def test_legacy_only_inventory_is_never_read_created_or_fallen_back_to(self) -> None:
        legacy = self.write_legacy()
        legacy_before = legacy.read_bytes()
        result = self.invoke("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单不存在", result.stderr)
        self.assertIn(str(self.default), result.stderr)
        self.assertNotIn("legacy-host", result.stderr)
        self.assertNotIn("legacy", result.stdout)
        self.assertFalse(self.default.exists())
        self.assertEqual(legacy.read_bytes(), legacy_before)
        self.assert_no_program()
        self.assert_no_tailscale()

    def test_explicit_config_can_select_legacy_file_when_user_asks(self) -> None:
        legacy = self.write_legacy()
        legacy_before = legacy.read_bytes()
        result = self.invoke("--config", "machines.local.yaml", "--list", cwd=self.module)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["legacy"])
        self.assertFalse(self.default.exists())
        self.assertEqual(legacy.read_bytes(), legacy_before)
        self.assert_no_tailscale()

    def test_default_module_inventory_coexists_with_legacy_and_leftover(self) -> None:
        legacy = self.write_legacy()
        self.stale.write_text(textwrap.dedent(DECOY))
        inventory = self.write_default()
        legacy_before = legacy.read_bytes()
        inventory_before = inventory.read_bytes()
        stale_before = self.stale.read_bytes()
        result = self.invoke("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop", "laptop"])
        self.assertNotIn("decoy-host", result.stdout + result.stderr)
        self.assertNotIn("legacy-host", result.stdout + result.stderr)
        self.assertEqual(legacy.read_bytes(), legacy_before)
        self.assertEqual(inventory.read_bytes(), inventory_before)
        self.assertEqual(self.stale.read_bytes(), stale_before)
        result = self.invoke("legacy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("清单中没有机器", result.stderr)
        self.assert_no_program()
        self.assert_no_tailscale()

    def test_generated_inventory_passes_strict_schema_and_reads_edits(self) -> None:
        self.generate_inventory()
        result = self.invoke("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop"])
        result = self.invoke("--moonlight", "desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["stream", "--", "100.64.0.10:47989", "Desktop"])
        # Editing this file in place is the documented next step after one install.
        self.write_default()
        result = self.invoke("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["desktop", "laptop"])
        result = self.invoke("--ssh", "laptop", exit_code=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["-p", "2222", "laptop-alias"])

    def test_generator_writes_module_inventory_regardless_of_cwd(self) -> None:
        self.assertFalse(self.default.exists())
        unrelated = self.root / "generator cwd"
        unrelated.mkdir()
        self.generate_inventory()
        self.assertTrue(self.default.is_file())
        self.assertEqual(self.default.stat().st_mode & 0o777, 0o644)
        self.assertFalse((unrelated / "machines.yaml").exists())
        # Regeneration replaces the module inventory and leaves every other file alone.
        self.write_legacy()
        self.stale.write_text("leftover stays\n")
        legacy_before = self.legacy.read_bytes()
        stale_before = self.stale.read_bytes()
        self.write_default()
        self.generate_inventory()
        self.assertNotIn("desktop:\n    ssh: conley@100.64.0.2", self.default.read_text(encoding="utf-8"))
        self.assertEqual(self.legacy.read_bytes(), legacy_before)
        self.assertEqual(self.stale.read_bytes(), stale_before)

    def test_generator_refuses_link_and_directory_inventory_targets(self) -> None:
        self.write_legacy()
        legacy_before = self.legacy.read_bytes()
        self.stale.write_text("leftover stays\n")
        stale_before = self.stale.read_bytes()
        self.default.symlink_to(self.legacy)
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("符号链接", result.stderr)
        self.assertTrue(self.default.is_symlink())
        self.assertEqual(self.legacy.read_bytes(), legacy_before)
        self.default.unlink()
        self.default.mkdir()
        (self.default / "sentinel").write_text("preserve directory sentinel\n")
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不是普通文件", result.stderr)
        self.assertEqual((self.default / "sentinel").read_text(), "preserve directory sentinel\n")
        (self.default / "sentinel").unlink()
        self.default.rmdir()
        # A FIFO is non-regular too: publishing must refuse it without blocking on
        # an open-for-write of the target.
        os.mkfifo(self.default)
        result = self.run_generator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不是普通文件", result.stderr)
        self.assertTrue(stat.S_ISFIFO(self.default.stat().st_mode))
        self.default.unlink()
        # A refused publish leaves no temporary residue behind.
        self.assertEqual(list(self.module.glob(".machines.yaml.*.qdtmp")), [])
        self.assertEqual(self.legacy.read_bytes(), legacy_before)
        self.assertEqual(self.stale.read_bytes(), stale_before)

    def test_generated_inventory_documents_every_field_in_chinese(self) -> None:
        self.generate_inventory()
        text = self.default.read_text(encoding="utf-8")
        keys = ("machines", "desktop", "ssh", "tailnet_ip", "moonlight_port", "ssh_port", "note")
        for key in keys:
            match = re.search(rf"^[ \t]*#?[ \t]*{re.escape(key)}:", text, re.M)
            self.assertIsNotNone(match, f"示例缺少字段 {key}")
            preceding = text[: match.start()].splitlines()
            comment = preceding[-1].strip() if preceding else ""
            self.assertTrue(comment.startswith("#"), f"{key} 上方应有注释，实际: {comment!r}")
            self.assertRegex(comment, CJK, f"{key} 上方注释应为中文: {comment!r}")
        for marker in ("必填", "可选", "默认"):
            self.assertIn(marker, text)
        self.assertIn("Web UI", text)
        self.assertNotIn("示例", text)
        self.assertFalse(self.legacy.exists())
        self.assertFalse(self.stale.exists())

    def test_connector_probes_no_tailscale_and_preserves_ssh_state(self) -> None:
        self.write_default()
        ssh_before = self.ssh_bytes()
        self.assertEqual(sorted(ssh_before), ["config", "known_hosts"])

        result = self.invoke("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.invoke("--moonlight", "desktop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded()["argv"][1:], ["stream", "--", "100.64.0.2:47989", "Desktop"])
        result = self.invoke("--ssh", "laptop", exit_code=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        # The alias is passed through verbatim; the connector never resolves it from ~/.ssh/config.
        # argv[0] is the fake program path because the shebang rewrites it, so compare from index 1.
        self.assertEqual(self.recorded()["argv"][1:], ["-p", "2222", "laptop-alias"])
        self.assertNotIn("sshconfig-sentinel", result.stdout + result.stderr)
        self.assertEqual(self.ssh_bytes(), ssh_before)
        self.assert_no_tailscale()

    def test_invalid_utf8_inventory_reports_path_without_traceback_or_child(self) -> None:
        self.default.write_bytes(b"machines: \xff\n")
        result = self.invoke("--list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.default), result.stderr)
        self.assertIn("UTF-8", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_no_program()

    def test_unknown_target_and_parser_errors_do_not_exec(self) -> None:
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
