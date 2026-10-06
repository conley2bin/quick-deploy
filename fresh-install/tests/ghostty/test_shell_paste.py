"""Exercise the exact Python reader embedded in the rendered ZLE hook.

Synthetic tmux/proc/protocol evidence only; never read a desktop clipboard.
The run.sh private PTYs separately exercise insertion and fallback behavior.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2] / "modules" / "ghostty"
ID = "00000000-0000-4000-8000-000000000001"


def load_reader() -> dict:
    with tempfile.TemporaryDirectory() as directory:
        functions = Path(directory) / "functions.sh"
        functions.write_text((ROOT / "install.sh").read_text().split('main "$@"')[0])
        rendered = subprocess.run(
            ["bash", "-c", 'source "$1"; render_local_paste_hook', "_", str(functions)],
            capture_output=True, text=True, check=True,
        ).stdout
    source = rendered.split("<<'GHOSTTY_REMOTE_PASTE_PY'\n", 1)[1].split("\nGHOSTTY_REMOTE_PASTE_PY", 1)[0]
    namespace = {"__name__": "reader_test"}
    exec(compile(source, "rendered-clipboard-reader", "exec"), namespace)
    return namespace


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = load_reader()

    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {
            "TMUX": "/tmp/private-socket,10,0", "TMUX_PANE": "%7",
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @contextlib.contextmanager
    def clients(self, listing: str, parents: dict[int, tuple[str, int]]):
        def stat(path):
            pid = int(path.parts[-2])
            if pid not in parents:
                raise FileNotFoundError(path)
            comm, parent = parents[pid]
            return f"{pid} ({comm}) S {parent} 0 0"

        with mock.patch.object(subprocess, "run", return_value=types.SimpleNamespace(stdout=listing)) as run:
            with mock.patch.object(Path, "read_text", stat):
                yield run

    def test_direct_local_ignores_stale_ssh_environment(self):
        os.environ.pop("TMUX")
        os.environ.pop("TMUX_PANE")
        os.environ["SSH_CONNECTION"] = "stale inherited env"
        with mock.patch.object(subprocess, "run") as run:
            self.assertFalse(self.reader["remote_route"]())
        run.assert_not_called()

    def test_only_clients_displaying_this_pane_affect_destination(self):
        with self.clients("10\t100\t%7\n20\t999\t%8\n", {10: ("tmux: client", 1)}) as run:
            self.assertFalse(self.reader["remote_route"]())
        self.assertEqual(run.call_args.args[0][:3], ["tmux", "-S", "/tmp/private-socket"])

    def test_newest_remote_reads_client_even_with_local_display(self):
        os.environ["WAYLAND_DISPLAY"] = "server-wayland"
        with self.clients("10\t100\t%7\n20\t101\t%7\n", {
            10: ("tmux: client", 1), 20: ("tmux: client", 21), 21: ("sshd", 1),
        }):
            self.assertTrue(self.reader["remote_route"]())

    def test_newest_local_does_not_use_remote_environment(self):
        os.environ["SSH_CONNECTION"] = "stale"
        with self.clients("10\t102\t%7\n20\t101\t%7\n", {10: ("tmux: client", 1)}):
            self.assertFalse(self.reader["remote_route"]())

    def test_mixed_or_multiple_remote_same_second_ties_are_errors(self):
        for local_comm in ("tmux: client", "sshd"):
            with self.subTest(comm=local_comm), self.clients("10\t100\t%7\n20\t100\t%7\n", {
                10: (local_comm, 1), 20: ("sshd", 1),
            }):
                with self.assertRaisesRegex(RuntimeError, "same second"):
                    self.reader["remote_route"]()

    def test_two_local_clients_share_same_clipboard(self):
        with self.clients("10\t100\t%7\n20\t100\t%7\n", {
            10: ("tmux: client", 1), 20: ("tmux: client", 1),
        }):
            self.assertFalse(self.reader["remote_route"]())

    def test_unknown_client_cannot_authorize_server_clipboard_fallback(self):
        with self.clients("10\t100\t%7\n", {}):
            with self.assertRaises(FileNotFoundError):
                self.reader["remote_route"]()

    def test_absent_pane_or_failed_tmux_is_not_local(self):
        with self.clients("20\t100\t%8\n", {}):
            with self.assertRaisesRegex(RuntimeError, "no client"):
                self.reader["remote_route"]()
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("tmux", 2)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.reader["remote_route"]()
        os.environ.pop("TMUX_PANE")
        with self.assertRaisesRegex(RuntimeError, "TMUX_PANE"):
            self.reader["remote_route"]()

    def read_response(self, payload=None, wire=None):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        os.environ["PI_REMOTE_CLIPBOARD_REMOTE_PORT"] = str(listener.getsockname()[1])
        requests = []
        errors = []

        def serve():
            try:
                listener.settimeout(2)
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    with connection.makefile("rb") as reader:
                        requests.append(json.loads(reader.readline()))
                    connection.sendall(wire if wire is not None else json.dumps(payload).encode() + b"\n")
            except Exception as error:
                errors.append(error)
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            return self.reader["read_remote_text"]()
        finally:
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(requests, [{"action": "read"}])

    def test_remote_id_and_utf8_trailing_newlines_preserved(self):
        for text in (ID, "\t中文  \n\n\n", "", "literal\x01"):
            with self.subTest(text=text):
                self.assertEqual(self.read_response({"kind": "text", "text": text}), text)
        self.assertEqual(self.read_response({"kind": "empty"}), "")

    def test_bad_protocol_and_image_cannot_be_inserted(self):
        for payload in ([], {"kind": "image", "mime": "image/png", "b64": "AAAA"},
                        {"kind": "text", "text": 12}, {"kind": "text", "text": "a\0b"},
                        {"kind": "error", "message": "provider unavailable"},
                        {"kind": "text", "text": "x" * (1024 * 1024 + 1)}):
            with self.subTest(kind=type(payload)):
                with self.assertRaises(RuntimeError):
                    self.read_response(payload)
        for wire in (b"", b"not json\n", b'{"kind":"text","text":"partial"}'):
            with self.subTest(wire=wire), self.assertRaises((RuntimeError, json.JSONDecodeError)):
                self.read_response(wire=wire)

    def test_exit_codes_distinguish_local_and_remote_failures(self):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(self.reader, remote_route=lambda: False, read_remote_text=mock.Mock()):
            self.assertEqual(self.reader["main"](), 2)
        with mock.patch.dict(self.reader, remote_route=lambda: True, read_remote_text=lambda: ID):
            with contextlib.redirect_stdout(output):
                self.assertEqual(self.reader["main"](), 0)
        self.assertEqual(output.getvalue(), ID)
        for failing_function, message in (("read_remote_text", "bridge unavailable"),
                                           ("remote_route", "origin unknown")):
            output = io.StringIO()
            replacements = {"remote_route": lambda: True, "read_remote_text": lambda: ID,
                            failing_function: mock.Mock(side_effect=RuntimeError(message))}
            with mock.patch.dict(self.reader, replacements):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    self.assertEqual(self.reader["main"](), 1)
            self.assertEqual(output.getvalue(), "", "failed read leaked partial text")
        self.assertIn("origin unknown", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
