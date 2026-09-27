#!/usr/bin/env python3
"""pi-remote-clipboard 的单元测试。"""
from __future__ import annotations

import base64
import io
import json
import os
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import provider
import requester
import ssh_detect
import push


class ProviderTests(unittest.TestCase):
    def test_text_response(self) -> None:
        with mock.patch.object(provider, "list_types", return_value=["text/plain;charset=utf-8"]), \
             mock.patch.object(provider, "read_text", return_value="hello"):
            result = provider.read_clipboard()
        self.assertEqual(result["kind"], "text")
        self.assertEqual(result["text"], "hello")

    def test_image_response(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
        with mock.patch.object(provider, "list_types", return_value=["image/png"]), \
             mock.patch.object(provider, "read_image", return_value=png):
            result = provider.read_clipboard()
        self.assertEqual(result["kind"], "image")
        self.assertEqual(result["mime"], "image/png")
        self.assertEqual(base64.b64decode(result["b64"]), png)

    def test_empty(self) -> None:
        with mock.patch.object(provider, "list_types", return_value=[]):
            result = provider.read_clipboard()
        self.assertEqual(result["kind"], "empty")

    def test_text_oversize(self) -> None:
        big = "x" * (provider.MAX_TEXT_BYTES + 1)
        with mock.patch.object(provider, "list_types", return_value=["text/plain"]), \
             mock.patch.object(provider, "read_text", return_value=big):
            result = provider.read_clipboard()
        self.assertEqual(result["kind"], "error")

    def test_wayland_detection(self) -> None:
        with mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertTrue(provider._is_wayland())
        with mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            self.assertTrue(provider._is_wayland())
        with mock.patch.dict(os.environ, {"DISPLAY": ":0"}, clear=True):
            self.assertFalse(provider._is_wayland())


class RequesterTests(unittest.TestCase):
    def test_text_insert(self) -> None:
        text, err = requester.handle_response({"kind": "text", "text": "hello"})
        self.assertEqual(text, "hello")
        self.assertIsNone(err)

    def test_image_writes_tmp(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"1" * 50
        payload = {"kind": "image", "mime": "image/png", "b64": base64.b64encode(data).decode()}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tempfile, "gettempdir", return_value=tmp):
                path, err = requester.handle_response(payload)
            self.assertIsNone(err)
            self.assertTrue(path.startswith(tmp))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), data)

    def test_empty(self) -> None:
        _, err = requester.handle_response({"kind": "empty"})
        self.assertEqual(err, "剪贴板为空")

    def test_unknown_kind(self) -> None:
        _, err = requester.handle_response({"kind": "weird"})
        self.assertIn("未知响应", err)


class SshDetectTests(unittest.TestCase):
    def test_non_tmux_uses_process_ancestors(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(ssh_detect, "_process_is_ssh_descendant", return_value=True):
            self.assertTrue(ssh_detect.is_ssh_terminal())

    def test_tmux_uses_client_ancestors(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,1", "TMUX_PANE": "%5"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("subprocess.run") as run_mock, \
             mock.patch.object(ssh_detect, "_process_is_ssh_descendant", return_value=True) as anc:
            run_mock.return_value.stdout = "12345\n"
            self.assertTrue(ssh_detect.is_ssh_terminal())
            anc.assert_called_with(12345)

    def test_tmux_no_client_pid(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,1", "TMUX_PANE": "%5"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("subprocess.run") as run_mock:
            run_mock.return_value.stdout = "\n"
            self.assertFalse(ssh_detect.is_ssh_terminal())

    def test_tmux_client_not_ssh(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,1", "TMUX_PANE": "%5"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("subprocess.run") as run_mock, \
             mock.patch.object(ssh_detect, "_process_is_ssh_descendant", return_value=False):
            run_mock.return_value.stdout = "999\n"
            self.assertFalse(ssh_detect.is_ssh_terminal())


class ProviderWriteTests(unittest.TestCase):
    def test_write_text_wayland(self) -> None:
        with mock.patch.object(provider, "_is_wayland", return_value=True), \
             mock.patch.object(provider, "_run", return_value=mock.Mock(returncode=0)) as run_mock:
            result = provider.write_clipboard({"action": "write", "kind": "text", "text": "hi"})
        self.assertEqual(result, {"kind": "ok"})
        self.assertEqual(run_mock.call_args[0][0], ["wl-copy"])

    def test_write_text_x11(self) -> None:
        with mock.patch.object(provider, "_is_wayland", return_value=False), \
             mock.patch.dict(os.environ, {"DISPLAY": ":1"}, clear=True), \
             mock.patch.object(provider, "_run", return_value=mock.Mock(returncode=0)) as run_mock:
            result = provider.write_clipboard({"action": "write", "kind": "text", "text": "hi"})
        self.assertEqual(result, {"kind": "ok"})
        self.assertEqual(run_mock.call_args[0][0], ["xclip", "-selection", "clipboard", "-i"])

    def test_write_image_wayland(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
        with mock.patch.object(provider, "_is_wayland", return_value=True), \
             mock.patch.object(provider, "_run", return_value=mock.Mock(returncode=0)) as run_mock:
            result = provider.write_clipboard({
                "action": "write", "kind": "image", "mime": "image/png",
                "b64": base64.b64encode(png).decode(),
            })
        self.assertEqual(result, {"kind": "ok"})
        self.assertEqual(run_mock.call_args[0][0][:2], ["wl-copy", "--type"])
        self.assertEqual(run_mock.call_args[1]["input_bytes"], png)

    def test_write_failure_returns_error(self) -> None:
        with mock.patch.object(provider, "_is_wayland", return_value=True), \
             mock.patch.object(provider, "_run", return_value=mock.Mock(returncode=1, stderr=b"boom")):
            result = provider.write_clipboard({"action": "write", "kind": "text", "text": "hi"})
        self.assertEqual(result["kind"], "error")
        self.assertIn("boom", result["message"])

    def test_write_rejects_oversize_and_bad_kind(self) -> None:
        big = "x" * (provider.MAX_TEXT_BYTES + 1)
        result = provider.write_clipboard({"action": "write", "kind": "text", "text": big})
        self.assertEqual(result["kind"], "error")
        result = provider.write_clipboard({"action": "write", "kind": "video"})
        self.assertEqual(result["kind"], "error")

    def test_handler_supports_write(self) -> None:
        received = []

        class FakeHandler(provider.Handler):
            def _send(self, payload):
                received.append(payload)

        handler = FakeHandler.__new__(FakeHandler)
        handler.rfile = io.BytesIO(json.dumps({"action": "write", "kind": "text", "text": "hi"}).encode() + b"\n")
        with mock.patch.object(provider, "write_clipboard", return_value={"kind": "ok"}) as wc:
            handler.handle()
        wc.assert_called_once()
        self.assertEqual(received, [{"kind": "ok"}])


class PushTests(unittest.TestCase):
    def test_detect_image_mime(self) -> None:
        self.assertEqual(push.detect_image_mime(b"\x89PNG\r\n\x1a\n" + b"x" * 8), "image/png")
        self.assertEqual(push.detect_image_mime(b"\xff\xd8\xff\xe0" + b"x" * 8), "image/jpeg")
        self.assertEqual(push.detect_image_mime(b"GIF89a" + b"x" * 8), "image/gif")
        self.assertEqual(push.detect_image_mime(b"RIFFxxxxWEBP" + b"x" * 8), "image/webp")
        self.assertIsNone(push.detect_image_mime(b"plain text"))

    def test_build_push_payload_from_clipboard_text(self) -> None:
        with mock.patch.object(provider, "read_clipboard", return_value={"kind": "text", "text": "hi"}):
            payload, err = push.build_push_payload(None)
        self.assertIsNone(err)
        self.assertEqual(payload, {"kind": "text", "text": "hi"})

    def test_build_push_payload_empty_clipboard(self) -> None:
        with mock.patch.object(provider, "read_clipboard", return_value={"kind": "empty"}):
            payload, err = push.build_push_payload(None)
        self.assertIsNone(payload)
        self.assertEqual(err, "本机剪贴板为空")

    def test_build_push_payload_from_file(self) -> None:
        data = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            f.flush()
            path = f.name
        try:
            payload, err = push.build_push_payload(path)
            self.assertIsNone(err)
            self.assertEqual(payload["kind"], "image")
            self.assertEqual(payload["mime"], "image/png")
            self.assertEqual(base64.b64decode(payload["b64"]), data)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
