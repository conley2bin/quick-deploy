#!/usr/bin/env python3
"""Isolated installer tests: systemctl is a recorder, never the live user manager."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

INSTALL = Path(__file__).resolve().parents[1] / "install.sh"
SERVICE = "pi-remote-clipboard.service"


class InstallerLifecycleTests(unittest.TestCase):
    def run_installer(self, *, target_active: bool, service_active: bool,
                      display_ready: bool, legacy_link: bool = False,
                      foreign_legacy_link: bool = False,
                      enable_fails: bool = False) -> tuple[subprocess.CompletedProcess[str], list[str], str, bool, bool]:
        with tempfile.TemporaryDirectory(prefix="pi-remote-installer-") as directory:
            root = Path(directory)
            home, bin_dir = root / "home", root / "bin"
            home.mkdir()
            bin_dir.mkdir()
            service_dir = home / ".config/systemd/user"
            service_dir.mkdir(parents=True)
            old_link = service_dir / "default.target.wants" / SERVICE
            new_link = service_dir / "graphical-session.target.wants" / SERVICE
            if legacy_link or foreign_legacy_link:
                old_link.parent.mkdir()
                old_link.symlink_to(Path("..") / SERVICE if legacy_link else root / "foreign.service")
            commands = root / "systemctl.log"
            systemctl = bin_dir / "systemctl"
            systemctl.write_text('''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$SYSTEMCTL_LOG"
case "$*" in
  '--user daemon-reload') ;;
  '--user enable pi-remote-clipboard.service')
    [[ "$ENABLE_FAILS" == 0 ]] || exit 27
    mkdir -p "$HOME/.config/systemd/user/graphical-session.target.wants"
    ln -sfn ../pi-remote-clipboard.service "$HOME/.config/systemd/user/graphical-session.target.wants/pi-remote-clipboard.service" ;;
  '--user is-active --quiet graphical-session.target') [[ "$TARGET_ACTIVE" == 1 ]] ;;
  '--user is-active --quiet pi-remote-clipboard.service') [[ "$SERVICE_ACTIVE" == 1 ]] ;;
  '--user show-environment') [[ "$DISPLAY_READY" == 1 ]] && echo WAYLAND_DISPLAY=wayland-0 || : ;;
  '--user start pi-remote-clipboard.service'|'--user restart pi-remote-clipboard.service'|'--user stop pi-remote-clipboard.service') ;;
  *) echo "unexpected systemctl call: $*" >&2; exit 90 ;;
esac
''')
            systemctl.chmod(0o755)
            env = {**os.environ, "HOME": str(home), "PI_CODING_AGENT_DIR": str(home / ".pi/agent"),
                   "PATH": f"{bin_dir}:{os.environ['PATH']}", "SYSTEMCTL_LOG": str(commands),
                   "TARGET_ACTIVE": str(int(target_active)), "SERVICE_ACTIVE": str(int(service_active)),
                   "DISPLAY_READY": str(int(display_ready)), "ENABLE_FAILS": str(int(enable_fails))}
            result = subprocess.run(["bash", str(INSTALL)], cwd=root, env=env,
                                    text=True, capture_output=True, timeout=20)
            calls = commands.read_text().splitlines() if commands.exists() else []
            unit = (service_dir / SERVICE).read_text()
            library = home / ".local/lib/pi-remote-clipboard"
            self.installed = {path.name: path.stat().st_mode & 0o777 for path in library.iterdir()} if library.is_dir() else {}
            return result, calls, unit, old_link.is_symlink(), new_link.is_symlink()

    def test_fresh_install_waits_for_graphical_session(self) -> None:
        result, calls, unit, old, new = self.run_installer(
            target_active=False, service_active=False, display_ready=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("After=graphical-session.target", unit)
        self.assertIn("PartOf=graphical-session.target", unit)
        self.assertIn("WantedBy=graphical-session.target", unit)
        self.assertNotIn("WantedBy=default.target", unit)
        self.assertFalse(old)
        self.assertTrue(new)
        self.assertEqual(calls, ["--user daemon-reload", "--user enable pi-remote-clipboard.service",
                                 "--user is-active --quiet graphical-session.target",
                                 "--user is-active --quiet pi-remote-clipboard.service"])
        self.assertIn("等待 graphical-session.target", result.stdout)
        # The versioned artifact endpoint ships with the helpers (manual CLI copies; the Pi extension itself
        # runs the checkout's src/ and does not depend on this copy).
        self.assertEqual(self.installed.get("ctrlv-artifact-v1.py"), 0o755)
        self.assertIn("ctrlv.py", self.installed)

    def test_migrates_old_default_link_and_restarts_in_graphical_manager(self) -> None:
        result, calls, _, old, new = self.run_installer(
            target_active=True, service_active=True, display_ready=True, legacy_link=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old, "old default.target enablement must be removed")
        self.assertTrue(new)
        self.assertEqual(calls[-4:], ["--user is-active --quiet graphical-session.target",
                                      "--user show-environment",
                                      "--user is-active --quiet pi-remote-clipboard.service",
                                      "--user restart pi-remote-clipboard.service"])
        self.assertFalse(any("enable --now" in call for call in calls))

    def test_stops_legacy_service_outside_graphical_session(self) -> None:
        result, calls, _, old, new = self.run_installer(
            target_active=False, service_active=True, display_ready=False, legacy_link=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old)
        self.assertTrue(new)
        self.assertEqual(calls[-1], "--user stop pi-remote-clipboard.service")
        self.assertFalse(any("start pi-remote-clipboard.service" in call for call in calls))

    def test_active_target_without_display_env_does_not_start_stale_provider(self) -> None:
        result, calls, _, _, new = self.run_installer(
            target_active=True, service_active=True, display_ready=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(new)
        self.assertEqual(calls[-1], "--user stop pi-remote-clipboard.service")
        self.assertIn("未启动", result.stderr)
        self.assertFalse(any("restart pi-remote-clipboard.service" in call for call in calls))

    def test_starts_when_graphical_target_and_manager_display_are_ready(self) -> None:
        result, calls, _, _, new = self.run_installer(
            target_active=True, service_active=False, display_ready=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(new)
        self.assertEqual(calls[-1], "--user start pi-remote-clipboard.service")

    def test_systemctl_enable_preserves_custom_link_in_offline_root(self) -> None:
        if not shutil.which("systemctl"):
            self.skipTest("systemctl unavailable for offline enablement check")
        with tempfile.TemporaryDirectory(prefix="pi-remote-unit-offline-") as directory:
            root = Path(directory)
            unit_dir = root / "etc/systemd/user"
            unit_dir.mkdir(parents=True)
            unit = unit_dir / SERVICE
            unit.write_text("[Unit]\nDescription=fixture\n[Service]\nExecStart=/usr/bin/true\n"
                            "[Install]\nWantedBy=default.target\n")
            old = unit_dir / "default.target.wants" / SERVICE
            new = unit_dir / "graphical-session.target.wants" / SERVICE
            custom = unit_dir / "custom.target.wants" / SERVICE
            first = subprocess.run(["systemctl", "--global", f"--root={root}", "enable", SERVICE],
                                   text=True, capture_output=True, timeout=10)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(old.is_symlink())
            custom.parent.mkdir()
            custom.symlink_to(Path("/etc/systemd/user") / SERVICE)
            # Use the actual installer-generated unit, but keep all enablement
            # confined to this disposable offline root. No user manager is called.
            result, _, generated, _, _ = self.run_installer(
                target_active=False, service_active=False, display_ready=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            unit.write_text(generated)
            old.unlink()  # The installer removes only this verified legacy link.
            second = subprocess.run(["systemctl", "--global", f"--root={root}", "enable", SERVICE],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertFalse(old.is_symlink())
            self.assertTrue(new.is_symlink())
            self.assertTrue(custom.is_symlink(), "manual additional enablement must survive")

    def test_foreign_legacy_link_is_preserved_and_refused(self) -> None:
        result, calls, _, old, new = self.run_installer(
            target_active=True, service_active=True, display_ready=True, foreign_legacy_link=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("旧版 provider 启用链接指向别处", result.stderr)
        self.assertTrue(old)
        self.assertFalse(new)
        self.assertEqual(calls, ["--user daemon-reload"])

    def test_enable_failure_is_explicit_without_start_or_restart(self) -> None:
        result, calls, _, old, new = self.run_installer(
            target_active=True, service_active=True, display_ready=True, legacy_link=True,
            enable_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("无法启用 provider", result.stderr)
        self.assertFalse(old, "known legacy link is removed before failed new enablement")
        self.assertFalse(new)
        self.assertEqual(calls, ["--user daemon-reload", "--user enable pi-remote-clipboard.service"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
