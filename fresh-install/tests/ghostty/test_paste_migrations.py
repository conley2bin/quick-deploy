"""Private HOME negative controls for the final config/launcher write guards."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).resolve().parents[2] / "modules/ghostty/install.sh"


class MigrationGuardTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="ghostty-paste-guards-")
        self.addCleanup(self.work.cleanup)
        self.home = Path(self.work.name)
        self.functions = self.home / "functions.sh"
        self.functions.write_text(INSTALLER.read_text().split('main "$@"')[0])

    def shell(self, body):
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; '
             'font_exists() { return 0; }; resolved_working_directory() { echo home; }; ' + body,
             "_", str(self.functions)],
            # Only system shell tools; desktop/SSH state and agent PATH wrappers
            # must not change the ownership/cleanup behavior under test.
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            capture_output=True, text=True, timeout=10,
        )

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("拒绝", result.stderr)
        self.assertEqual(list(self.home.rglob("*.tmp.*")), [], "failed guard left a temporary target")

    def test_native_config_is_inode_stable_without_prior_migration(self):
        result = self.shell('mkdir -p "$CONFIG_DIR"; render_config > "$CONFIG_FILE"; chmod 600 "$CONFIG_FILE"')
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.home / ".config/ghostty/config.ghostty"
        state = lambda: (config.stat().st_ino, config.stat().st_mode, config.stat().st_mtime_ns, config.read_bytes())
        before = state()
        result = self.shell('apply_paste_keybind_only; apply_paste_keybind_only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, state())
        self.assertEqual(list(config.parent.glob("*.bak.*")), [])

    def test_config_rechecks_after_generation(self):
        for change in ("custom", "symlink"):
            with self.subTest(change=change):
                result = self.shell(r'''
mkdir -p "$CONFIG_DIR"
render_config 'text:\x16' > "$CONFIG_FILE"
chmod() {
    command chmod "$@"
    if [ "$CHANGE" = custom ]; then
        printf '# CONCURRENT-USER\n' >> "$CONFIG_FILE"
    else
        mv "$CONFIG_FILE" "$HOME/original-config"
        ln -s "$HOME/original-config" "$CONFIG_FILE"
    fi
}
apply_paste_keybind_only
'''.replace('"$CHANGE"', '"' + change + '"'))
                self.assert_refused(result)
                config = self.home / ".config/ghostty/config.ghostty"
                self.assertIn(b'text:\\x16', config.read_bytes())
                if change == "custom":
                    self.assertIn(b"CONCURRENT-USER", config.read_bytes())
                    config.unlink()
                else:
                    self.assertTrue(config.is_symlink())
                    config.unlink()

    def test_launcher_rechecks_changed_target_before_backup(self):
        for change in ("custom", "symlink", "nonexec", "appeared"):
            with self.subTest(change=change):
                result = self.shell(r'''
mkdir -p "$HOME/.local/bin"
if [ "$CHANGE" != appeared ]; then
    render_legacy_pi_launcher > "$USER_PI_GHOSTTY_LAUNCHER"
    chmod 755 "$USER_PI_GHOSTTY_LAUNCHER"
fi
chmod() {
    command chmod "$@"
    case "$CHANGE" in
        custom|appeared) printf 'CONCURRENT-USER\n' > "$USER_PI_GHOSTTY_LAUNCHER" ;;
        symlink) mv "$USER_PI_GHOSTTY_LAUNCHER" "$HOME/original-launcher"; ln -s "$HOME/original-launcher" "$USER_PI_GHOSTTY_LAUNCHER" ;;
        nonexec) command chmod 644 "$USER_PI_GHOSTTY_LAUNCHER" ;;
    esac
}
install_pi_launcher
'''.replace('"$CHANGE"', '"' + change + '"'))
                self.assert_refused(result)
                target = self.home / ".local/bin/ghostty-pi"
                if change in ("custom", "appeared"):
                    self.assertEqual(target.read_bytes(), b"CONCURRENT-USER\n")
                elif change == "symlink":
                    self.assertTrue(target.is_symlink())
                else:
                    self.assertEqual(target.stat().st_mode & 0o777, 0o644)
                self.assertEqual(list(target.parent.glob("ghostty-pi.bak.*")), [])
                target.unlink()

    def test_launcher_refuses_failed_backup(self):
        result = self.shell(r'''
mkdir -p "$HOME/.local/bin"
render_legacy_pi_launcher > "$USER_PI_GHOSTTY_LAUNCHER"
chmod 755 "$USER_PI_GHOSTTY_LAUNCHER"
cp() { return 23; }
install_pi_launcher
''')
        self.assert_refused(result)
        self.assertIn("无法备份", result.stderr)
        self.assertIn(b'text:\\x16', (self.home / ".local/bin/ghostty-pi").read_bytes())
        self.assertEqual(list(self.home.rglob("ghostty-pi.bak.*")), [])

    def test_launcher_rechecks_after_backup_and_preserves_evidence(self):
        result = self.shell(r'''
mkdir -p "$HOME/.local/bin"
render_legacy_pi_launcher > "$USER_PI_GHOSTTY_LAUNCHER"
chmod 755 "$USER_PI_GHOSTTY_LAUNCHER"
cp() { command cp "$@"; printf '# CONCURRENT-USER\n' >> "$USER_PI_GHOSTTY_LAUNCHER"; }
install_pi_launcher
''')
        self.assert_refused(result)
        self.assertIn("备份期间变化", result.stderr)
        target = self.home / ".local/bin/ghostty-pi"
        self.assertIn(b"CONCURRENT-USER", target.read_bytes())
        backups = list(target.parent.glob("ghostty-pi.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertNotIn(b"CONCURRENT-USER", backups[0].read_bytes())
        self.assertIn(b'text:\\x16', backups[0].read_bytes())

    def test_launcher_does_not_overwrite_an_existing_backup(self):
        result = self.shell(r'''
mkdir -p "$HOME/.local/bin"
render_legacy_pi_launcher > "$USER_PI_GHOSTTY_LAUNCHER"
chmod 755 "$USER_PI_GHOSTTY_LAUNCHER"
date() { echo FIXED; }
printf 'PRIOR-BACKUP\n' > "$USER_PI_GHOSTTY_LAUNCHER.bak.FIXED"
install_pi_launcher
''')
        self.assert_refused(result)
        target = self.home / ".local/bin/ghostty-pi"
        self.assertIn(b'text:\\x16', target.read_bytes())
        self.assertEqual(Path(str(target) + ".bak.FIXED").read_bytes(), b"PRIOR-BACKUP\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
