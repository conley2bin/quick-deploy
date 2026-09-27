"""远端剪贴板请求端。

当前 Pi 被判定为 SSH 终端时，通过 SSH remote forwarding 的本地端口，
向发起端机器的 provider 请求剪贴板内容。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import tempfile
import uuid
from typing import Any

DEFAULT_PORT = int(os.environ.get("PI_REMOTE_CLIPBOARD_REMOTE_PORT", "37844"))
REQUEST_TIMEOUT_S = float(os.environ.get("PI_REMOTE_CLIPBOARD_REQUEST_TIMEOUT", "5"))
MAX_IMAGE_BYTES = int(os.environ.get("PI_REMOTE_CLIPBOARD_MAX_IMAGE_BYTES", str(32 * 1024 * 1024)))
MAX_TEXT_BYTES = int(os.environ.get("PI_REMOTE_CLIPBOARD_MAX_TEXT_BYTES", str(1024 * 1024)))

EXT_FOR_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}


def request_clipboard(port: int = DEFAULT_PORT) -> dict[str, Any]:
    with socket.create_connection(("127.0.0.1", port), timeout=REQUEST_TIMEOUT_S) as sock:
        sock.sendall(b'{"action":"read"}\n')
        f = sock.makefile("rb")
        line = f.readline(64 * 1024)
    if not line:
        return {"kind": "error", "message": "提供者无响应"}
    return json.loads(line.decode("utf-8"))


def handle_response(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """返回 (插入文本, 错误消息)。"""
    kind = payload.get("kind")
    if kind == "text":
        text = payload.get("text", "")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return None, f"文本超过 {MAX_TEXT_BYTES} 字节上限"
        return text, None
    if kind == "image":
        mime = payload.get("mime", "")
        b64 = payload.get("b64", "")
        if mime not in EXT_FOR_MIME:
            return None, f"不支持的图片类型: {mime}"
        try:
            data = base64.b64decode(b64, validate=True)
        except Exception:  # noqa: BLE001
            return None, "图片 base64 解码失败"
        if len(data) > MAX_IMAGE_BYTES:
            return None, f"图片超过 {MAX_IMAGE_BYTES} 字节上限"
        ext = EXT_FOR_MIME[mime]
        path = os.path.join(tempfile.gettempdir(), f"pi-remote-clipboard-{uuid.uuid4()}.{ext}")
        with open(path, "wb") as f:
            f.write(data)
        os.chmod(path, 0o600)
        return path, None
    if kind == "empty":
        return None, "剪贴板为空"
    if kind == "error":
        return None, str(payload.get("message", "未知错误"))
    return None, f"未知响应: {kind}"
