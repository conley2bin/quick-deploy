#!/usr/bin/env python3
"""Offline integration fixtures for app declarations and process matchers."""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLASH = ROOT / "clash-verge"
sys.path.insert(0, str(CLASH / "lib"))
import discover_apps  # noqa: E402
import rules  # noqa: E402


def write_sources(directory: Path, direct: str, proxy: str) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    direct_path, proxy_path = directory / "direct.yaml", directory / "proxy.yaml"
    direct_path.write_text(direct, encoding="utf-8")
    proxy_path.write_text(proxy, encoding="utf-8")
    return direct_path, proxy_path


def discovered(app_id: str, *paths: tuple[str, str]) -> discover_apps.DiscoveryResult:
    spec = discover_apps.SPECS[app_id]
    return discover_apps.DiscoveryResult(
        app_id, spec.name, "resolved",
        tuple(discover_apps.DiscoveredExecutable(path, role, ("fixture",)) for role, path in paths),
        ("fixture",),
    )


def fake_discover(results: dict[str, discover_apps.DiscoveryResult]):
    def discover(ids: list[str]) -> tuple[discover_apps.DiscoveryResult, ...]:
        return tuple(results[app_id] for app_id in ids)
    return discover


class AppIntegrationTests(unittest.TestCase):
    def test_mixed_declarations_expand_in_authored_order_and_relocate_without_source_writeback(self) -> None:
        direct = """version: 1
pre:
  - \"DOMAIN,before.example,DIRECT\"
  - app: baidunetdisk
  - \"DOMAIN,after.example,DIRECT\"
post:
  - app: feishu
"""
        proxy = """version: 1
pre:
  - app: wemeet
    target: Proxy
post:
  - app: wechat
    target: Proxy
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            direct_path, proxy_path = write_sources(directory, direct, proxy)
            original = hashlib.sha256(direct_path.read_bytes() + proxy_path.read_bytes()).hexdigest()
            source = rules.parsed_sources(direct_path, proxy_path)
            first = rules.resolve_app_rules(source, fake_discover({
                "baidunetdisk": discovered("baidunetdisk", ("main", "/relocated one/baidu"), ("helper", "/relocated one/service")),
                "wemeet": discovered("wemeet", ("main", "/relocated one/wemeet")),
                "feishu": discovered("feishu", ("main", "/relocated one/feishu")),
                "wechat": discovered("wechat", ("main", "/relocated one/wechat")),
            }))
            self.assertEqual([item.text for item in first["pre"]], [
                "DOMAIN,before.example,DIRECT", "PROCESS-PATH,/relocated one/baidu,DIRECT",
                "PROCESS-PATH,/relocated one/service,DIRECT", "DOMAIN,after.example,DIRECT",
                "PROCESS-PATH,/relocated one/wemeet,Proxy",
            ])
            self.assertEqual([item.text for item in first["post"]], [
                "PROCESS-PATH,/relocated one/feishu,DIRECT", "PROCESS-PATH,/relocated one/wechat,Proxy",
            ])
            second = rules.resolve_app_rules(source, fake_discover({
                "baidunetdisk": discovered("baidunetdisk", ("main", "/relocated two/baidu"), ("helper", "/relocated two/service")),
                "wemeet": discovered("wemeet", ("main", "/relocated two/wemeet")),
                "feishu": discovered("feishu", ("main", "/relocated two/feishu")),
                "wechat": discovered("wechat", ("main", "/relocated two/wechat")),
            }))
            self.assertIn("/relocated two/baidu", rules.render(second))
            self.assertNotEqual(rules.render(first), rules.render(second))
            self.assertEqual(original, hashlib.sha256(direct_path.read_bytes() + proxy_path.read_bytes()).hexdigest())

    def test_schema_process_validation_and_expanded_collision_fail_explicitly(self) -> None:
        valid_direct = """version: 1
pre:
  - \"PROCESS-NAME,feishu,DIRECT\"
  - \"PROCESS-PATH,/tmp/path with spaces/feishu,DIRECT\"
post: []
"""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            direct_path, proxy_path = write_sources(directory, valid_direct, "version: 1\npre: []\npost: []\n")
            parsed = rules.parsed_sources(direct_path, proxy_path)
            runtime = {"rules": [
                {"type": "ProcessName", "payload": "feishu", "proxy": "DIRECT"},
                {"type": "ProcessPath", "payload": "/tmp/path with spaces/feishu", "proxy": "DIRECT"},
                {"type": "Match", "payload": "", "proxy": "DIRECT"},
            ]}
            self.assertTrue(rules.verify_runtime_rules(parsed, io.StringIO(json.dumps(runtime))))

            for bad in ("PROCESS-NAME,feishu*,DIRECT", "PROCESS-PATH,/tmp/a*,DIRECT", "PROCESS-PATH,/tmp/^name,DIRECT", "PROCESS-PATH,/tmp/a,DIRECT,no-resolve"):
                direct_path.write_text(f'version: 1\npre:\n  - "{bad}"\npost: []\n', encoding="utf-8")
                with self.assertRaises(rules.SourceError):
                    rules.parsed_sources(direct_path, proxy_path)

            direct_path, proxy_path = write_sources(
                directory / "same-app-opposite-targets",
                "version: 1\npre:\n  - app: wechat\npost: []\n",
                "version: 1\npre:\n  - app: wechat\n    target: Proxy\npost: []\n",
            )
            with self.assertRaisesRegex(rules.SourceError, "selector conflicts"):
                rules.parsed_sources(direct_path, proxy_path)

            direct_path, proxy_path = write_sources(
                directory / "collision",
                "version: 1\npre:\n  - \"PROCESS-PATH,/fixture/app,DIRECT\"\npost: []\n",
                "version: 1\npre:\n  - app: wemeet\n    target: Proxy\npost: []\n",
            )
            source = rules.parsed_sources(direct_path, proxy_path)
            with self.assertRaisesRegex(rules.SourceError, "selector conflicts"):
                rules.resolve_app_rules(source, fake_discover({
                    "wemeet": discovered("wemeet", ("main", "/fixture/app")),
                }))

    def test_invalid_app_mappings_and_unresolved_render_apply_leave_files_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            for name, body in {
                "unknown": "version: 1\npre:\n  - app: unknown\npost: []\n",
                "extra": "version: 1\npre:\n  - app: wechat\n    target: DIRECT\npost: []\n",
                "proxy-missing": "version: 1\npre: []\npost: []\n",
            }.items():
                direct = body if name != "proxy-missing" else "version: 1\npre: []\npost: []\n"
                proxy = "version: 1\npre:\n  - app: wechat\npost: []\n" if name == "proxy-missing" else "version: 1\npre: []\npost: []\n"
                direct_path, proxy_path = write_sources(temporary_path / name, direct, proxy)
                with self.assertRaises(rules.SourceError):
                    rules.parsed_sources(direct_path, proxy_path)

            source_dir = temporary_path / "rules"
            direct_path, proxy_path = write_sources(
                source_dir, "version: 1\npre:\n  - app: wemeet\npost: []\n", "version: 1\npre: []\npost: []\n"
            )
            source = rules.parsed_sources(direct_path, proxy_path)
            unresolved = discover_apps.DiscoveryResult("wemeet", "Tencent Meeting", "missing", (), (), "fixture absent")
            with self.assertRaisesRegex(rules.SourceError, "cannot resolve PROCESS-PATH"):
                rules.resolve_app_rules(source, fake_discover({"wemeet": unresolved}))

            fake_bin = temporary_path / "bin"
            fake_bin.mkdir()
            dpkg = fake_bin / "dpkg-query"
            dpkg.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            dpkg.chmod(0o755)
            home = temporary_path / "home"
            state = home / ".local/share/io.github.clash-verge-rev.clash-verge-rev"
            profiles = state / "profiles"
            profiles.mkdir(parents=True)
            (state / "profiles.yaml").write_text("items:\n- uid: Script\n  type: script\n  file: Script.js\n", encoding="utf-8")
            target = profiles / "Script.js"
            target.write_text("// sentinel\n", encoding="utf-8")
            environment = dict(os.environ, HOME=str(home), RULES_DIR=str(source_dir), PATH=f"{fake_bin}:{os.environ['PATH']}")
            check = subprocess.run(["bash", str(CLASH / "tun-fix.sh"), "rules", "check"], cwd=ROOT, env=environment,
                                   text=True, capture_output=True, check=False)
            self.assertEqual(check.returncode, 0)
            self.assertIn("paths are resolved only", check.stdout)
            rendered = subprocess.run(["bash", str(CLASH / "tun-fix.sh"), "rules", "render"], cwd=ROOT, env=environment,
                                      text=True, capture_output=True, check=False)
            self.assertNotEqual(rendered.returncode, 0)
            self.assertIn("cannot resolve PROCESS-PATH", rendered.stderr)
            applied = subprocess.run(["bash", str(CLASH / "tun-fix.sh"), "rules", "apply"], cwd=ROOT, env=environment,
                                     text=True, capture_output=True, check=False)
            self.assertNotEqual(applied.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "// sentinel\n")
            self.assertFalse(list(profiles.glob("Script.js.backup.*")))
            self.assertFalse(list(profiles.glob(".Script.js.candidate.*")))

    def test_real_mihomo_accepts_literal_process_rules(self) -> None:
        core = shutil.which("verge-mihomo")
        self.assertIsNotNone(core, "verge-mihomo is required by the repository test contract")
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "process.yaml"
            config.write_text(
                "mixed-port: 7890\nmode: rule\nrules:\n"
                "  - PROCESS-NAME,feishu,DIRECT\n"
                "  - PROCESS-PATH,/tmp/path with spaces/feishu,DIRECT\n"
                "  - MATCH,DIRECT\n", encoding="utf-8"
            )
            result = subprocess.run([core, "-t", "-f", str(config)], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
