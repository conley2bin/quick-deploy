#!/usr/bin/env python3
"""Focused offline fixtures for native application discovery."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import discover_apps as apps  # noqa: E402


def elf(path: Path, executable: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELFfixture")
    path.chmod(0o755 if executable else 0o644)
    return path


def install(root: Path, app_id: str, *, executable: bool = True) -> list[str]:
    spec = apps.SPECS[app_id]
    paths = []
    for _role, relative, _required in spec.programs:
        paths.append(str(elf(root / relative, executable)))
    return paths


def package_evidence(installs: dict[str, list[str]]) -> dict[str, list[str]]:
    return {apps.SPECS[app_id].packages[0]: paths for app_id, paths in installs.items()}


class DiscoveryTests(unittest.TestCase):
    def resolve(self, app_id: str, packages: dict[str, list[str]], **kwargs: object) -> apps.DiscoveryResult:
        evidence = apps.Evidence("linux", packages, **kwargs)
        return apps.resolve_app(app_id, evidence)

    def test_every_canonical_id_resolves_relocated_closed_native_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            installs = {
                app_id: install(temp / f"relocated {app_id}", app_id)
                for app_id in apps.supported_ids()
            }
            packages = package_evidence(installs)
            for app_id in apps.supported_ids():
                result = self.resolve(app_id, packages)
                self.assertEqual(result.status, "resolved", result)
                self.assertTrue(all("relocated " in item.path for item in result.executables))
            baidu = self.resolve("baidunetdisk", packages)
            self.assertEqual([item.role for item in baidu.executables], ["main", "helper"])
            self.assertEqual([item.path for item in baidu.executables], sorted(item.path for item in baidu.executables))

    def test_same_installation_desktop_and_process_evidence_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "feishu"
            paths = install(root, "feishu")
            result = self.resolve(
                "feishu", package_evidence({"feishu": paths}),
                desktops=(apps.DesktopEntry("/launchers/bytedance-feishu.desktop", paths[0]),),
                processes=(apps.ProcessEntry(42, "feishu", paths[0]),),
            )
            self.assertEqual(result.status, "resolved")
            self.assertEqual(len(result.executables), 1)
            self.assertIn("desktop:/launchers/bytedance-feishu.desktop: Exec matches manifest path", result.evidence)
            self.assertIn("proc:42: comm=feishu", result.executables[0].evidence)

    def test_uninstalled_and_multiple_complete_roots_do_not_guess(self) -> None:
        self.assertEqual(self.resolve("wechat", {}).status, "missing")
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            first = install(temp / "a", "wemeet")
            second = install(temp / "z", "wemeet")
            result = self.resolve("wemeet", {"wemeet": first + second})
            self.assertEqual(result.status, "ambiguous")
            self.assertIn(str(temp / "a"), result.reason or "")
            self.assertLess((result.reason or "").index(str(temp / "a")), (result.reason or "").index(str(temp / "z")))

    def test_symlink_emits_real_app_owned_binary_but_wrappers_and_shared_runtime_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            root = temp / "wechat"
            real = elf(root / "current" / "wechat-real")
            launcher = root / "wechat"
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.symlink_to(real)
            result = self.resolve("wechat", {"wechat": [str(launcher), str(real)]})
            self.assertEqual(result.status, "resolved")
            self.assertEqual(result.executables[0].path, str(real))

            wrapper_root = temp / "wrapper"
            wrapper = wrapper_root / "bin" / "wemeetapp"
            wrapper.parent.mkdir(parents=True)
            wrapper.write_text("#!/bin/sh\nexec /usr/bin/node\n", encoding="utf-8")
            wrapper.chmod(0o755)
            result = self.resolve("wemeet", {"wemeet": [str(wrapper)]})
            self.assertEqual(result.status, "unsupported")
            self.assertIn("wrapper or non-native", result.reason or "")

            shared = elf(temp / "shared" / "node")
            shared_root = temp / "shared-runtime"
            shared_launcher = shared_root / "bin" / "wemeetapp"
            shared_launcher.parent.mkdir(parents=True)
            shared_launcher.symlink_to(shared)
            result = self.resolve("wemeet", {"wemeet": [str(shared_launcher)]})
            self.assertEqual(result.status, "unsupported")
            self.assertIn("target lacks package-manifest", result.reason or "")

            in_root = temp / "usr"
            runtime = elf(in_root / "bin" / "node")
            app_link = in_root / "bin" / "wemeetapp"
            app_link.symlink_to(runtime)
            result = self.resolve("wemeet", {"wemeet": [str(app_link), str(runtime)]})
            self.assertEqual(result.status, "unsupported")
            self.assertIn("shared runtime", result.reason or "")

    def test_missing_or_nonexecutable_required_helper_blocks_baidu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "baidu"
            paths = install(root, "baidunetdisk")
            helper = root / "netdisk_service"
            helper.unlink()
            result = self.resolve("baidunetdisk", {"baidunetdisk": paths})
            self.assertEqual(result.status, "missing")
            self.assertIn("missing or not executable", result.reason or "")

            paths = install(root, "baidunetdisk")
            os.chmod(root / "netdisk_service", 0o644)
            result = self.resolve("baidunetdisk", {"baidunetdisk": paths})
            self.assertEqual(result.status, "missing")
            self.assertIn("missing or not executable", result.reason or "")

    def test_desktop_parser_is_data_only_and_payload_rejects_unrepresentable_paths(self) -> None:
        malformed = apps.parse_desktop_entry("/launcher.desktop", "[Desktop Entry]\nExec='unterminated\n")
        self.assertIsNotNone(malformed.error)
        self.assertFalse(apps.safe_process_path("/opt/path,comma/app"))
        self.assertFalse(apps.safe_process_path("/opt/path\nnewline/app"))
        self.assertTrue(apps.safe_process_path("/opt/path with spaces/app"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "comma,root"
            paths = install(root, "wechat")
            result = self.resolve("wechat", {"wechat": paths})
            self.assertEqual(result.status, "unsupported")
            self.assertIn("cannot be represented", result.reason or "")

    def test_unsupported_platform_and_unknown_id_are_explicit(self) -> None:
        result = apps.resolve_app("wechat", apps.Evidence("darwin", {}))
        self.assertEqual(result.status, "unsupported")
        with self.assertRaisesRegex(ValueError, "unsupported app id"):
            apps.resolve_app("arbitrary-command", apps.Evidence("linux", {}))


if __name__ == "__main__":
    unittest.main()
