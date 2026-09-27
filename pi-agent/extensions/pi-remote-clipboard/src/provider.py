#!/usr/bin/env python3
"""本地剪贴板提供器。

只在 localhost 上监听，只在被请求时读取一次本机剪贴板。
支持文本与图片；不主动同步、不保存历史、不接受写入命令。

协议（JSON Lines over TCP，仅本机）：
  请求：  {"action":"read"}
  响应：  {"kind":"text","text":"..."} | {"kind":"image","mime":"image/png","b64":"..."}
          | {"kind":"empty"} | {"kind":"error","message":"..."}
"""
from __future__ import annotations

import base64
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
from typing import Any

READ_TIMEOUT_S = float(os.environ.get("PI_REMOTE_CLIPBOARD_READ_TIMEOUT", "4"))
MAX_IMAGE_BYTES = int(os.environ.get("PI_REMOTE_CLIPBOARD_MAX_IMAGE_BYTES", str(32 * 1024 * 1024)))
MAX_TEXT_BYTES = int(os.environ.get("PI_REMOTE_CLIPBOARD_MAX_TEXT_BYTES", str(1024 * 1024)))
LISTEN_HOST = os.environ.get("PI_REMOTE_CLIPBOARD_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("PI_REMOTE_CLIPBOARD_PORT", "37843"))

IMAGE_MIME_ORDER = ["image/png", "image/jpeg", "image/webp", "image/gif"]
TEXT_MIME_CANDIDATES = ["text/plain;charset=utf-8", "text/plain", "text", "UTF8_STRING", "STRING"]


def _run(argv: list[str], timeout: float = READ_TIMEOUT_S, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY")) or bool(os.environ.get("WAYLAND_DISPLAY"))


def list_types() -> list[str]:
    if not _has_display():
        return []
    if _is_wayland():
        result = _run(["wl-paste", "--list-types"])
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    result = _run(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def read_image(mime: str) -> bytes | None:
    if _is_wayland():
        result = _run(["wl-paste", "--type", mime, "--no-newline"])
    else:
        result = _run(["xclip", "-selection", "clipboard", "-t", mime, "-o"])
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def read_text() -> str | None:
    if not _has_display():
        return None
    if _is_wayland():
        for mime in TEXT_MIME_CANDIDATES:
            result = _run(["wl-paste", "--type", mime, "--no-newline"])
            if result.returncode == 0 and result.stdout:
                return result.stdout.decode("utf-8", "replace")
        return None
    result = _run(["xclip", "-selection", "clipboard", "-o"])
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", "replace")
    return text if text else None


def _write_text(text: str) -> None:
    if _is_wayland():
        result = _run(["wl-copy"], input_bytes=text.encode("utf-8"))
    elif os.environ.get("DISPLAY"):
        result = _run(["xclip", "-selection", "clipboard", "-i"], input_bytes=text.encode("utf-8"))
    else:
        raise RuntimeError("本机没有可用的显示会话（WAYLAND_DISPLAY/DISPLAY 均缺失）")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "剪贴板写入失败")


def _write_image(mime: str, data: bytes) -> None:
    if _is_wayland():
        result = _run(["wl-copy", "--type", mime], input_bytes=data)
    elif os.environ.get("DISPLAY"):
        result = _run(["xclip", "-selection", "clipboard", "-t", mime], input_bytes=data)
    else:
        raise RuntimeError("本机没有可用的显示会话（WAYLAND_DISPLAY/DISPLAY 均缺失）")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "剪贴板写入失败")


def write_clipboard(request: dict[str, Any]) -> dict[str, Any]:
    kind = request.get("kind")
    if kind == "text":
        text = request.get("text")
        if not isinstance(text, str) or not text:
            return {"kind": "error", "message": "write: 缺少 text"}
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return {"kind": "error", "message": f"文本超过 {MAX_TEXT_BYTES} 字节上限"}
        try:
            _write_text(text)
        except Exception as exc:  # noqa: BLE001
            return {"kind": "error", "message": str(exc)}
        return {"kind": "ok"}
    if kind == "image":
        mime = request.get("mime", "")
        if not isinstance(mime, str) or not mime.startswith("image/"):
            return {"kind": "error", "message": f"write: 不支持的 MIME: {mime}"}
        try:
            data = base64.b64decode(request.get("b64", ""), validate=True)
        except Exception:  # noqa: BLE001
            return {"kind": "error", "message": "write: base64 解码失败"}
        if not data:
            return {"kind": "error", "message": "write: 图片内容为空"}
        if len(data) > MAX_IMAGE_BYTES:
            return {"kind": "error", "message": f"图片超过 {MAX_IMAGE_BYTES} 字节上限"}
        try:
            _write_image(mime, data)
        except Exception as exc:  # noqa: BLE001
            return {"kind": "error", "message": str(exc)}
        return {"kind": "ok"}
    return {"kind": "error", "message": f"write: 未知 kind: {kind}"}


def read_clipboard() -> dict[str, Any]:
    types = list_types()
    if not types:
        return {"kind": "empty"}
    image_mime = next((m for m in IMAGE_MIME_ORDER if m in types), None)
    if image_mime is None:
        image_mime = next((m for m in types if m.startswith("image/")), None)
    if image_mime is not None:
        data = read_image(image_mime)
        if data is None:
            return {"kind": "empty"}
        if len(data) > MAX_IMAGE_BYTES:
            return {"kind": "error", "message": f"图片超过 {MAX_IMAGE_BYTES} 字节上限"}
        return {"kind": "image", "mime": image_mime, "b64": base64.b64encode(data).decode("ascii")}
    text = read_text()
    if text is None:
        return {"kind": "empty"}
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        return {"kind": "error", "message": f"文本超过 {MAX_TEXT_BYTES} 字节上限"}
    return {"kind": "text", "text": text}


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(64 * 1024 * 1024)
            if not raw:
                return
            request = json.loads(raw.decode("utf-8"))
            action = request.get("action")
            if action == "read":
                self._send(read_clipboard())
            elif action == "write":
                self._send(write_clipboard(request))
            else:
                self._send({"kind": "error", "message": "未知 action"})
        except Exception as exc:  # noqa: BLE001
            self._send({"kind": "error", "message": str(exc)})

    def _send(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> int:
    server = Server((LISTEN_HOST, LISTEN_PORT), Handler)
    print(f"pi-remote-clipboard provider listening on {LISTEN_HOST}:{LISTEN_PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
