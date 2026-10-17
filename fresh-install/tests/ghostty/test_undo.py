"""Undo-key migration and real ZLE/readline buffers in disposable PTYs.

PTY bytes prove receiver behavior, not a Ghostty GUI/physical key press.
No user's shell startup files, running windows or tmux sessions are modified.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

INSTALLER = Path(__file__).resolve().parents[2] / "modules/ghostty/install.sh"
UNDO_LINES = b"keybind = ctrl+z=text:\\x1f\nkeybind = ctrl+shift+z=text:\\x1a\n"
# Independent pre-feature fixture. Historical ownership must not drift with the renderer.
PRE_UNDO = """# 由 fresh-install/modules/ghostty/install.sh 生成与维护。
# 重跑脚本会把本文件重置为基准内容；手工修改请在脚本里改。
font-family = JetBrains Mono
font-family = Noto Sans Mono CJK SC
font-size = 12
theme = Catppuccin Frappe
keybind = f11=toggle_fullscreen
keybind = ctrl+alt+arrow_left=unbind
keybind = ctrl+alt+arrow_right=unbind
keybind = ctrl+alt+equal=csi:61;7u
keybind = ctrl+alt+shift+equal=csi:43;7u
keybind = ctrl+shift+r=reset
mouse-shift-capture = never
keybind = ctrl+shift+c=copy_to_clipboard:mixed
keybind = ctrl+shift+v=paste_from_clipboard
clipboard-write = allow
clipboard-read = deny
working-directory = home
window-inherit-working-directory = false
# 独立外部启动使用独立进程；内部窗口、tab 和 split 仍可能共享进程。
gtk-single-instance = false
shell-integration-features = ssh-env,ssh-terminfo
""".encode()


class UndoMigrationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="ghostty-undo-guards-")
        self.addCleanup(self.work.cleanup)
        self.home = Path(self.work.name)
        self.functions = self.home / "functions.sh"
        self.functions.write_text(INSTALLER.read_text().split('main "$@"')[0])
        self.config = self.home / ".config/ghostty/config.ghostty"

    def shell(self, body):
        return subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; '
             'font_exists() { return 0; }; resolved_working_directory() { echo home; }; ' + body,
             "_", str(self.functions)],
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            capture_output=True, timeout=10,
        )

    def seed(self, raw=False):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        content = PRE_UNDO.replace(b"paste_from_clipboard", b"text:\\x16") if raw else PRE_UNDO
        self.config.write_bytes(content)
        self.config.chmod(0o600)
        return content

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("拒绝".encode(), result.stderr)
        self.assertEqual(list(self.home.rglob("*.tmp.*")), [])

    def test_historical_fixture_and_minimal_idempotent_migration(self):
        for raw in (False, True):
            with self.subTest(raw=raw):
                before = self.seed(raw)
                paste = r"text:\x16" if raw else "paste_from_clipboard"
                result = self.shell(f"render_config '{paste}' false")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, before)
                result = self.shell("main --undo-keybind-only")
                self.assertEqual(result.returncode, 0, result.stderr)
                after = self.config.read_bytes()
                self.assertEqual(after.replace(UNDO_LINES, b""), before)
                self.assertEqual(after.count(UNDO_LINES), 1)
                self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
                state = self.config.stat()
                result = self.shell("apply_undo_keybind_only; apply_undo_keybind_only")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.config.read_bytes(), after)
                self.assertEqual(self.config.stat().st_ino, state.st_ino)
                self.assertEqual(self.config.stat().st_mtime_ns, state.st_mtime_ns)
                self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_paste_migration_preserves_pre_or_post_undo_state(self):
        for undo in (False, True):
            with self.subTest(undo=undo):
                before = self.seed(raw=True)
                if undo:
                    result = self.shell("apply_undo_keybind_only")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    before = self.config.read_bytes()
                result = self.shell("apply_paste_keybind_only")
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = before.replace(b"text:\\x16", b"paste_from_clipboard")
                self.assertEqual(self.config.read_bytes(), expected)
                state = self.config.stat()
                result = self.shell("apply_paste_keybind_only")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.config.stat().st_ino, state.st_ino)
                self.assertEqual(self.config.stat().st_mtime_ns, state.st_mtime_ns)

    def test_refuses_missing_custom_duplicate_partial_and_link_targets(self):
        for kind in ("missing", "custom", "duplicate", "partial", "no-newline", "symlink", "directory-link"):
            with self.subTest(kind=kind):
                case = self.home / kind
                case.mkdir()
                result = self.shell(r'''
CONFIG_DIR="$HOME/$KIND/config"
CONFIG_FILE="$CONFIG_DIR/config.ghostty"
mkdir -p "$CONFIG_DIR"
render_config "$GHOSTTY_PASTE_ACTION" false > "$CONFIG_FILE"
case "$KIND" in
    missing) rm "$CONFIG_FILE" ;;
    custom) printf '# USER\n' >> "$CONFIG_FILE" ;;
    duplicate) printf 'keybind = ctrl+z=ignore\n' >> "$CONFIG_FILE" ;;
    partial) printf 'keybind = ctrl+z=text:\\x1f\n' >> "$CONFIG_FILE" ;;
    no-newline) truncate -s -1 "$CONFIG_FILE" ;;
    symlink) mv "$CONFIG_FILE" "$HOME/$KIND/keep"; ln -s "$HOME/$KIND/keep" "$CONFIG_FILE" ;;
    directory-link) mv "$CONFIG_DIR" "$HOME/$KIND/keep"; ln -s "$HOME/$KIND/keep" "$CONFIG_DIR" ;;
esac
cp -a "$HOME/$KIND" "$HOME/expected-$KIND"
apply_undo_keybind_only
'''.replace("$KIND", kind))
                self.assert_refused(result)
                diff = subprocess.run(["diff", "-r", "--no-dereference", str(case), str(self.home / ("expected-" + kind))], capture_output=True)
                self.assertEqual(diff.returncode, 0, diff.stdout)

    def test_rechecks_concurrent_changes_and_cleans_temp(self):
        for kind in ("custom", "symlink", "missing", "directory-link"):
            with self.subTest(kind=kind):
                self.seed()
                result = self.shell(r'''
chmod() {
    command chmod "$@"
    case "$KIND" in
        custom) printf '# CONCURRENT\n' >> "$CONFIG_FILE" ;;
        symlink) mv "$CONFIG_FILE" "$HOME/keep"; ln -s "$HOME/keep" "$CONFIG_FILE" ;;
        missing) rm "$CONFIG_FILE" ;;
        directory-link) mv "$CONFIG_DIR" "$HOME/keep-dir"; ln -s "$HOME/keep-dir" "$CONFIG_DIR" ;;
    esac
}
apply_undo_keybind_only
'''.replace("$KIND", kind))
                self.assert_refused(result)
                if kind == "custom":
                    self.assertEqual(self.config.read_bytes(), PRE_UNDO + b"# CONCURRENT\n")
                elif kind == "symlink":
                    self.assertTrue(self.config.is_symlink())
                    self.assertEqual(self.config.read_bytes(), PRE_UNDO)
                    self.config.unlink()
                elif kind == "missing":
                    self.assertFalse(self.config.exists())
                else:
                    self.assertTrue(self.config.parent.is_symlink())
                    self.assertEqual(self.config.read_bytes(), PRE_UNDO)
                    self.config.parent.unlink()

    def test_narrow_mode_does_not_call_other_install_steps(self):
        self.seed()
        result = self.shell(r'''
check_supported_system() { exit 87; }
preflight_commands() { exit 87; }
install_ghostty() { exit 87; }
install_pi_launcher() { exit 87; }
install_backpressure() { exit 87; }
install_local_paste_hook() { exit 87; }
write_config() { exit 87; }
smoke_test() { exit 87; }
main --undo-keybind-only
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        # Only our fixture/functions and the one managed config may exist.
        self.assertEqual(sorted(p.relative_to(self.home).as_posix() for p in self.home.rglob("*") if p.is_file()),
                         [".config/ghostty/config.ghostty", "functions.sh"])

    def test_narrow_mode_conflicts_fail_before_writes(self):
        before = self.seed()
        for other in ("--check", "--ppa-only", "--deb-only", "--no-default-terminal", "--default-terminal",
                      "--shell-paste-only", "--paste-keybind-only", "--pi-launcher-only"):
            for args in ("--undo-keybind-only " + other, other + " --undo-keybind-only"):
                with self.subTest(args=args):
                    result = self.shell("parse_args " + args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("不能".encode(), result.stderr)
                    self.assertEqual(self.config.read_bytes(), before)


class ShellUndoTests(unittest.TestCase):
    def read_until(self, predicate, description):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if predicate():
                return
            if select.select([self.fd], [], [], .02)[0]:
                try:
                    self.output += os.read(self.fd, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                self.fail(f"shell exited before {description}: {self.output!r}")
        self.fail(f"timed out waiting for {description}: {self.output!r}")

    def shell_buffers(self, shell):
        if not shutil.which(shell):
            self.skipTest(f"{shell} is not installed")
        with tempfile.TemporaryDirectory(prefix="ghostty-undo-pty-") as directory:
            home = Path(directory)
            snapshots, ready = home / "buffers", home / "ready"
            rc = home / (".zshrc" if shell == "zsh" else ".bashrc")
            setup = (
                "bindkey -e\n_undo_snapshot() { printf '%s\\0' \"$BUFFER\" >> \"$UNDO_SNAPSHOTS\"; }\n"
                "zle -N _undo_snapshot\nbindkey '^]' _undo_snapshot\n"
                if shell == "zsh" else
                "set -o emacs\nbind -x '\"\\C-]\":printf \"%s\\0\" \"$READLINE_LINE\" >> \"$UNDO_SNAPSHOTS\"'\n"
            )
            rc.write_text(setup + "PS1='UNDO> '\nprintf ready > \"$UNDO_READY\"\n")
            env = {"HOME": str(home), "ZDOTDIR": str(home), "PATH": "/usr/bin:/bin", "TERM": "xterm-256color",
                   "LANG": "C.UTF-8", "EDITOR": "emacs", "UNDO_SNAPSHOTS": str(snapshots), "UNDO_READY": str(ready)}
            self.output = b""
            self.pid, self.fd = pty.fork()
            if self.pid == 0:
                argv = [shell, "-d", "-i"] if shell == "zsh" else [shell, "--noprofile", "--rcfile", str(rc), "-i"]
                os.execvpe(shell, argv, env)
            try:
                self.read_until(ready.exists, "shell ready")
                def capture(payload, count):
                    os.write(self.fd, payload + b"\x1d")
                    self.read_until(lambda: snapshots.exists() and snapshots.read_bytes().count(b"\0") == count,
                                    f"buffer snapshot {count}")
                capture(b"\x1f", 1)  # Empty undo stack is a no-op.
                capture(b"one two", 2)
                capture(b"\x17", 3)  # Native backward-kill-word, before submission.
                capture(b"\x1f", 4)  # Undo byte that Ghostty Ctrl+Z will emit.
                capture(b"\x17\x17", 5)
                capture(b"\x1f", 6)
                capture(b"\x1f", 7)
                buffers = snapshots.read_bytes().split(b"\0")[:-1]
                self.assertEqual(buffers, [b"", b"one two", b"one ", b"one two", b"", b"one ", b"one two"])
                return [value.decode() for value in buffers]
            finally:
                os.close(self.fd)
                try:
                    os.kill(self.pid, signal.SIGHUP)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(self.pid, 0)
                except ChildProcessError:
                    pass  # read_until already reaped an unexpectedly exited shell.

    def test_zle_emacs_undo_changes_real_buffer(self):
        self.shell_buffers("zsh")

    def test_readline_emacs_undo_changes_real_buffer(self):
        self.shell_buffers("bash")


if __name__ == "__main__":
    unittest.main(verbosity=2)
