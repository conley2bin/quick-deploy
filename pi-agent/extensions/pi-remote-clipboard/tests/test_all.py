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


if __name__ == "__main__":
    unittest.main(verbosity=2)
