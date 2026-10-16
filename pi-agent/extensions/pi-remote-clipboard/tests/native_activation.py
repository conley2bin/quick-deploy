#!/usr/bin/env python3
"""Run the clipboard extension through Pi's actual CLI loader without a live clipboard."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
import unittest

EXTENSION = Path(__file__).resolve().parents[1] / "pi-extension/index.ts"


def installed_cli() -> tuple[Path, Path]:
    pi = shutil.which("pi")
    node = shutil.which("node")
    if not pi or not node:
        raise RuntimeError("This test requires an installed Pi CLI and Node.js on PATH")
    entry = Path(pi).resolve()
    root = entry.parents[2]
    package = root / "package.json"
    if not package.is_file() or json.loads(package.read_text())["name"] != "@earendil-works/pi-coding-agent":
        raise RuntimeError(f"Pi CLI does not resolve to its installed package: {entry}")
    return entry, Path(node).resolve()


class NativeActivationTests(unittest.TestCase):
    def run_cli(self, *, target: bool, missing_paste: bool = False,
                terminal_text: bool = False, remote: bool = True) -> list[dict]:
        entry, node = installed_cli()
        with tempfile.TemporaryDirectory(prefix="pi-remote-clipboard-native-") as directory:
            root = Path(directory)
            home = root / "home"
            agent_dir = home / ".pi/agent"
            bin_dir = root / "bin"
            agent_dir.mkdir(parents=True)
            bin_dir.mkdir()
            event_log = root / "events.jsonl"
            helper_log = root / "helper.log"
            tripwire = root / "forbidden-clipboard-command.log"
            fixture = root / "fixture.ts"
            replacement = (
                "delete Agent.InteractiveMode.prototype.handleClipboardPaste;"
                if missing_paste else
                'Agent.InteractiveMode.prototype.handleClipboardPaste = async function() { log("native-paste", {rendererMatchesTuiAltScreen: this.renderer?.constructor === Tui.TuiAltScreen}); };'
            )
            fixture.write_text('''import * as Agent from "@earendil-works/pi-coding-agent";
import * as Tui from "@earendil-works/pi-tui";
import { appendFileSync } from "node:fs";
const log = (kind, details = {}) => appendFileSync(process.env.PROBE_EVENT_LOG, JSON.stringify({kind, ...details}) + "\\n");
const native = Agent.InteractiveMode?.prototype.handleClipboardPaste;
log("loaded", {method: typeof native});
''' + replacement + '''
export default function(pi) {
  pi.on("session_start", (_event, ctx) => {
    log("started", {mode: ctx.mode});
    const interval = setInterval(() => {
      const text = ctx.ui.getEditorText?.();
      if (text?.includes("BRIDGE_FIXTURE") || text?.includes("TERMINAL_TEXT_FIXTURE")) {
        log("editor", {text});
        clearInterval(interval);
      }
    }, 50);
    pi.on("session_shutdown", () => clearInterval(interval));
  });
}
''')
            helper = bin_dir / "python3"
            helper.write_text('''#!/bin/sh
[ "$1" = "$PI_REMOTE_CLIPBOARD_DIR/ctrlv.py" ] || exit 111
echo requested >> "$PROBE_HELPER_LOG"
printf 'BRIDGE_FIXTURE\\n'
''')
            helper.chmod(0o755)
            for name in ("xclip", "xsel", "wl-paste", "termux-clipboard-get"):
                fake = bin_dir / name
                fake.write_text('#!/bin/sh\necho "$0" >> "$PROBE_TRIPWIRE"\nexit 111\n')
                fake.chmod(0o755)

            # Whitelist environment so the child cannot inherit a desktop display,
            # user Pi settings, provider credentials, or an SSH/tmux client socket.
            env = {
                "HOME": str(home), "PI_CODING_AGENT_DIR": str(agent_dir),
                "XDG_CONFIG_HOME": str(root / "config"), "XDG_DATA_HOME": str(root / "data"),
                "PI_REMOTE_CLIPBOARD_DIR": str(root / "remote-lib"),
                "PROBE_EVENT_LOG": str(event_log), "PROBE_HELPER_LOG": str(helper_log),
                "PROBE_TRIPWIRE": str(tripwire), "SSH_CONNECTION": "fixture 123 127.0.0.1 22",
                "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1", "PATH": f"{bin_dir}:/usr/bin:/bin",
                "TERM": "xterm-256color", "NO_COLOR": "1", "CI": "1", "LANG": "C.UTF-8",
            }
            if not remote:
                env.pop("SSH_CONNECTION")
            args = [
                str(node), str(entry), "--no-extensions", "--extension", str(fixture),
                *(["--extension", str(EXTENSION)] if target else []),
                "--no-session", "--no-skills", "--no-prompt-templates", "--no-themes",
                "--no-context-files", "--offline", "--tui-mode", "fullscreen",
            ]
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 34, 120, 0, 0))
            process = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave,
                                       cwd=root, env=env, start_new_session=True)
            os.close(slave)
            output = bytearray()

            def drain(seconds: float) -> None:
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    readable, _, _ = select.select([master], [], [], min(.05, max(0, until - time.monotonic())))
                    if readable:
                        try:
                            output.extend(os.read(master, 65536))
                            if len(output) > 200_000:
                                del output[:-200_000]
                        except OSError:
                            return
                    if process.poll() is not None:
                        return

            def events() -> list[dict]:
                return [json.loads(line) for line in event_log.read_text().splitlines()] if event_log.exists() else []

            def helpers() -> int:
                return len(helper_log.read_text().splitlines()) if helper_log.exists() else 0

            def wait_for(predicate, what: str, timeout: float = 8) -> None:
                deadline = time.monotonic() + timeout
                while not predicate() and time.monotonic() < deadline and process.poll() is None:
                    drain(.05)
                self.assertTrue(predicate(), f"{what}: events={events()}, helpers={helpers()}, "
                                f"exit={process.poll()}, pty={bytes(output[-2000:])!r}")

            try:
                wait_for(lambda: any(item["kind"] == "started" for item in events()), "Pi startup")
                self.assertEqual(events()[0], {"kind": "loaded", "method": "function"})
                drain(.5)
                if terminal_text:
                    # Model text already read by the terminal. This is not a GUI
                    # key event and must not invoke Pi's clipboard key handler.
                    os.write(master, b"\x1b[200~TERMINAL_TEXT_FIXTURE\x1b[201~")
                    wait_for(lambda: any(item["kind"] == "editor" for item in events()), "terminal text insertion")
                    self.assertEqual(helpers(), 0, "terminal text invoked the SSH clipboard bridge")
                    self.assertFalse(any(item["kind"] == "native-paste" for item in events()))
                    self.assertEqual(next(item for item in events() if item["kind"] == "editor")["text"],
                                     "TERMINAL_TEXT_FIXTURE")
                elif missing_paste:
                    self.assertTrue(target, "only the explicit shortcut uses a missing private paste seam")
                    os.write(master, b"\x1b\x16")  # Alt+Ctrl+V via classic terminal encoding
                    wait_for(lambda: helpers() == 1, "explicit Ctrl+Alt+V shortcut")
                    wait_for(lambda: any(item["kind"] == "editor" for item in events()), "explicit paste insertion")
                    self.assertFalse(any(item["kind"] == "native-paste" for item in events()))
                else:
                    os.write(master, b"\x16")  # Pi's native Ctrl+V key
                    if target and remote:
                        wait_for(lambda: helpers() == 1, "Ctrl+V bridge wrapper")
                        wait_for(lambda: any(item["kind"] == "editor" for item in events()), "Ctrl+V insertion")
                        self.assertFalse(any(item["kind"] == "native-paste" for item in events()))
                    else:
                        wait_for(lambda: any(item["kind"] == "native-paste" for item in events()),
                                 "control key reaches the actual CLI InteractiveMode")
                        self.assertTrue(next(item for item in events() if item["kind"] == "native-paste")
                                        ["rendererMatchesTuiAltScreen"], "CLI must use the extension's TuiAltScreen class")
                        self.assertEqual(helpers(), 0)
                self.assertFalse(tripwire.exists(), "a physical clipboard command was attempted")
                return events()
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=3)
                drain(.1)
                os.close(master)

    def test_ctrl_v_intercepts_actual_cli_instance(self) -> None:
        self.run_cli(target=False)  # control proves the key reaches our bundled-class stub
        events = self.run_cli(target=True)
        self.assertEqual([event["kind"] for event in events if event["kind"] == "editor"], ["editor"])

    def test_local_ctrl_v_with_extension_keeps_native_handler(self) -> None:
        self.run_cli(target=True, remote=False)

    def test_terminal_text_bypasses_native_and_bridge_clipboard_handlers(self) -> None:
        self.run_cli(target=False, terminal_text=True)
        self.run_cli(target=True, terminal_text=True)

    def test_explicit_shortcut_survives_missing_private_paste_method(self) -> None:
        events = self.run_cli(target=True, missing_paste=True)
        self.assertEqual([event["kind"] for event in events if event["kind"] == "editor"], ["editor"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
