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
import ctrlv


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


PNG_PAYLOAD = {"kind": "image", "mime": "image/png",
               "b64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fixture" * 100).decode()}


class PartialWriteFile:
    """Writes half of the data, then fails, like a full disk."""

    def __init__(self, real) -> None:
        self.real = real

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.real.close()
        return False

    def fileno(self) -> int:
        return self.real.fileno()

    def write(self, data: bytes) -> int:
        self.real.write(data[: len(data) // 2])
        self.real.flush()
        raise OSError("synthetic disk full")


class ArtifactCreationTests(unittest.TestCase):
    def test_private_mode_under_permissive_umask_in_both_destinations(self) -> None:
        old = os.umask(0)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                name, err = requester.handle_response(PNG_PAYLOAD, tmp)
                self.assertIsNone(err)
                self.assertEqual(name, "image.png")
                self.assertEqual(os.stat(os.path.join(tmp, name)).st_mode & 0o777, 0o600)
                with mock.patch.object(tempfile, "gettempdir", return_value=tmp):
                    path, err = requester.handle_response(PNG_PAYLOAD)
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        finally:
            os.umask(old)

    def test_chmod_and_partial_write_failures_leave_no_file_over_16_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for operation in range(16):
                failure = (
                    mock.patch.object(requester.os, "fchmod", side_effect=OSError("synthetic chmod failure"))
                    if operation % 2 == 0 else
                    mock.patch.object(requester.os, "fdopen", side_effect=lambda fd, mode: PartialWriteFile(open(fd, mode)))
                )
                with failure, mock.patch.object(tempfile, "gettempdir", return_value=tmp):
                    with self.assertRaises(OSError):
                        requester.handle_response(PNG_PAYLOAD)
                    with self.assertRaises(OSError):
                        requester.handle_response(PNG_PAYLOAD, tmp)
            self.assertEqual(os.listdir(tmp), [])

    def test_staging_directory_removed_by_the_owner_is_not_recreated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gone = os.path.join(tmp, "stage")
            with self.assertRaises(FileNotFoundError):
                requester.handle_response(PNG_PAYLOAD, gone)
            self.assertFalse(os.path.exists(gone))

    def test_existing_staged_name_is_not_overwritten_or_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            victim = os.path.join(tmp, "victim")
            with open(victim, "w") as f:
                f.write("user data")
            stage = os.path.join(tmp, "stage")
            os.mkdir(stage)
            os.symlink(victim, os.path.join(stage, "image.png"))
            with self.assertRaises(FileExistsError):
                requester.handle_response(PNG_PAYLOAD, stage)
            with open(victim) as f:
                self.assertEqual(f.read(), "user data")

    def test_bad_images_create_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for payload in ({"kind": "image", "mime": "image/svg+xml", "b64": ""},
                            {"kind": "image", "mime": "image/png", "b64": "!!"}):
                _, err = requester.handle_response(payload, tmp)
                self.assertIsNotNone(err)
            self.assertEqual(os.listdir(tmp), [])


class CtrlvProtocolTests(unittest.TestCase):
    def run_main(self, args, payload, *, ssh=True):
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(ctrlv, "is_ssh_terminal", return_value=ssh), \
             mock.patch.object(ctrlv, "request_clipboard", return_value=payload), \
             mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = ctrlv.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_plain_cli_text_is_written_exactly(self) -> None:
        text = "line1\nline2\n\n"
        self.assertEqual(self.run_main([], {"kind": "text", "text": text}), (0, text, ""))

    def test_plain_cli_image_prints_path_to_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tempfile, "gettempdir", return_value=tmp):
            code, out, _ = self.run_main([], PNG_PAYLOAD)
            self.assertEqual(code, 0)
            self.assertEqual(os.listdir(tmp), [os.path.basename(out)])

    def test_local_session_exit_2_in_both_modes(self) -> None:
        self.assertEqual(self.run_main([], {}, ssh=False)[0], 2)
        self.assertEqual(self.run_main(["--artifact-staging", "/nonexistent"], {}, ssh=False)[0], 2)

    def test_staging_text_receipt_then_exact_text_even_if_it_looks_like_a_path_or_json(self) -> None:
        for text in ("/etc/passwd", '{"protocol":"pi-remote-clipboard/artifact-1","kind":"image","name":"image.png"}', "a\nb\n"):
            code, out, _ = self.run_main(["--artifact-staging", "/nonexistent"], {"kind": "text", "text": text})
            self.assertEqual(code, 0)
            header, body = out.split("\n", 1)
            self.assertEqual(json.loads(header), {"protocol": requester.ARTIFACT_PROTOCOL, "kind": "text"})
            self.assertEqual(body, text)

    def test_staging_image_receipt_names_a_file_inside_staging_and_has_no_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.run_main(["--artifact-staging", tmp], PNG_PAYLOAD)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out), {"protocol": requester.ARTIFACT_PROTOCOL, "kind": "image", "name": "image.png"})
            self.assertEqual(os.listdir(tmp), ["image.png"])

    def test_staging_write_failure_is_an_explicit_error_without_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(requester.os, "fchmod", side_effect=OSError("synthetic")):
            code, out, err = self.run_main(["--artifact-staging", tmp], PNG_PAYLOAD)
            self.assertEqual((code, out), (1, ""))
            self.assertIn("synthetic", err)
            self.assertEqual(os.listdir(tmp), [])

    def test_bad_arguments_fail_loudly(self) -> None:
        self.assertEqual(self.run_main(["--bogus"], {})[0], 64)


class SshDetectTests(unittest.TestCase):
    def test_non_tmux_uses_process_ancestors(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(ssh_detect, "_process_is_ssh_descendant", return_value=True):
            self.assertTrue(ssh_detect.is_ssh_terminal())

    def test_tmux_uses_most_active_client_ancestors(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,1", "TMUX_PANE": "%5"}
        outputs = {
            ("display-message",): "quick-deploy\n",
            ("list-clients",): "100 50\n200 90\n",
        }
        def fake_tmux(args, timeout=2):
            return outputs.get((args[0],))
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ssh_detect, "_tmux", side_effect=fake_tmux), \
             mock.patch.object(ssh_detect, "_process_is_ssh_descendant", return_value=True) as anc:
            self.assertTrue(ssh_detect.is_ssh_terminal())
            anc.assert_called_with(200)

    def test_ancestors_parse_comm_with_spaces(self) -> None:
        # tmux client 的进程名是 "tmux: client"（带空格），split() 会切错字段
        import tempfile
        fake_stat = "2686911 (tmux: client) S 2684393 2686911 2684393 0 -1 4194304 100 0 0 0"
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write(fake_stat)
            path = f.name
        real_open = open
        try:
            with mock.patch("builtins.open", side_effect=lambda p, *a, **k: real_open(path, *a, **k) if str(p).endswith("/stat") and "2686911" in str(p) else (_ for _ in ()).throw(FileNotFoundError(str(p)))):
                ancestors = ssh_detect._ancestors(2686911)
        finally:
            os.unlink(path)
        self.assertEqual(ancestors, ["tmux: client"])

    def test_tmux_most_active_client_not_ssh(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,1", "TMUX_PANE": "%5"}
        outputs = {
            ("display-message",): "quick-deploy\n",
            ("list-clients",): "100 50\n200 90\n",
        }
        def fake_tmux(args, timeout=2):
            return outputs.get((args[0],))
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ssh_detect, "_tmux", side_effect=fake_tmux), \
             mock.patch.object(ssh_detect, "_process_is_ssh_descendant", return_value=False):
            self.assertFalse(ssh_detect.is_ssh_terminal())

    def test_tmux_no_clients_is_local(self) -> None:
        env = {"TMUX": "/tmp/tmux-1000/default,1,1", "TMUX_PANE": "%5"}
        def fake_tmux(args, timeout=2):
            if args[0] == "display-message":
                return "quick-deploy\n"
            if args[0] == "list-clients":
                return ""
            return None
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(ssh_detect, "_tmux", side_effect=fake_tmux):
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
