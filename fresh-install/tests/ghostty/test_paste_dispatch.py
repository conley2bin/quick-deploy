"""Receiver-level native text/control distinction; no Ghostty GUI or real clipboard.

The PTY uses a canonical, echo-disabled reader and known dummy text. The bridge
uses a one-shot loopback fixture plus the unchanged requester implementation.
Neither test claims a physical Ghostty key press or a real SSH connection.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import pty
import select
import socket
import sys
import tempfile
import termios
import threading
import unittest
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[3] / "pi-agent/extensions/pi-remote-clipboard/src"
sys.path.insert(0, str(BRIDGE))
import requester


class PasteDispatchTests(unittest.TestCase):
    def echo_disabled_line(self, payload):
        master, slave = pty.openpty()
        original = termios.tcgetattr(slave)
        attrs = termios.tcgetattr(slave)
        attrs[3] = (attrs[3] | termios.ICANON | termios.IEXTEN) & ~(termios.ECHO | termios.ECHONL)
        attrs[6][termios.VLNEXT] = b"\x16"
        try:
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            os.write(master, payload)
            self.assertEqual(select.select([slave], [], [], .05)[0], [], "canonical reader submitted without Enter")
            # An explicit submit in our private fixture, not an injected GUI key.
            os.write(master, b"\n")
            self.assertTrue(select.select([slave], [], [], 1)[0], "reader did not receive explicit submission")
            result = os.read(slave, 4096)
            self.assertEqual(select.select([master], [], [], .05)[0], [], "echo-disabled reader echoed input")
            return result
        finally:
            termios.tcsetattr(slave, termios.TCSANOW, original)
            os.close(slave)
            os.close(master)

    def test_native_text_reaches_echo_disabled_reader(self):
        token = "DUMMY-不是密码-042".encode()
        self.assertEqual(self.echo_disabled_line(token), token + b"\n")

    def test_raw_ctrl_v_quotes_input_instead_of_fetching_clipboard(self):
        # VLNEXT consumes ^V and quotes X; no application clipboard handler exists.
        result = self.echo_disabled_line(b"\x16X")
        self.assertEqual(result, b"X\n")
        self.assertNotIn(b"DUMMY", result)
        self.assertNotIn(b"\x16", result)

    def bridge_response(self, payload):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        observed = []
        errors = []

        def serve():
            try:
                listener.settimeout(2)
                with listener.accept()[0] as connection:
                    connection.settimeout(2)
                    with connection.makefile("rb") as reader:
                        observed.append(json.loads(reader.readline()))
                    connection.sendall(json.dumps(payload).encode() + b"\n")
            except Exception as error:
                errors.append(error)
            finally:
                listener.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            response = requester.request_clipboard(port=port)  # never the production 37844 listener
        finally:
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(observed, [{"action": "read"}])
        return response

    def test_existing_bridge_text_and_png_transport(self):
        # Distinct from native terminal text; bridge bytes stay application-owned.
        payload = self.bridge_response({"kind": "text", "text": "BRIDGE-中文-text"})
        self.assertEqual(requester.handle_response(payload), ("BRIDGE-中文-text", None))
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        payload = self.bridge_response({"kind": "image", "mime": "image/png", "b64": base64.b64encode(png).decode()})
        with tempfile.TemporaryDirectory(prefix="ghostty-paste-image-") as directory:
            with mock.patch.object(requester.tempfile, "gettempdir", return_value=directory):
                path, error = requester.handle_response(payload)
            self.assertIsNone(error)
            image = Path(path)
            self.assertEqual(image.parent, Path(directory))
            self.assertEqual(image.suffix, ".png")
            self.assertEqual(image.read_bytes(), png)
            self.assertEqual(image.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
