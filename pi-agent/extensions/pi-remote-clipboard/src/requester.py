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


def _send(payload: dict[str, Any], port: int) -> dict[str, Any]:
    line_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    with socket.create_connection(("127.0.0.1", port), timeout=REQUEST_TIMEOUT_S) as sock:
        sock.sendall(line_bytes)
        f = sock.makefile("rb")
        line = f.readline(64 * 1024 * 1024)
    if not line:
        return {"kind": "error", "message": "提供者无响应"}
    return json.loads(line.decode("utf-8"))


def request_clipboard(port: int = DEFAULT_PORT) -> dict[str, Any]:
    return _send({"action": "read"}, port)


def push_text(text: str, port: int = DEFAULT_PORT) -> dict[str, Any]:
    return _send({"action": "write", "kind": "text", "text": text}, port)


def push_image(data: bytes, mime: str, port: int = DEFAULT_PORT) -> dict[str, Any]:
    return _send(
        {"action": "write", "kind": "image", "mime": mime, "b64": base64.b64encode(data).decode("ascii")},
        port,
    )


ARTIFACT_PROTOCOL = "pi-remote-clipboard/artifact-1"
ARTIFACT_NAME_FOR_EXT = {ext: f"image.{ext}" for ext in EXT_FOR_MIME.values()}


def _create_private(directory: str, name: str, data: bytes) -> str:
    """在已存在的 directory 中以 0600 独占创建 name 并写入 data；任何失败都回滚（删除半成品）。

    不创建 directory：它若已被调用方取消并删除，这里直接失败而不是重建。
    """
    path = os.path.join(directory, name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(data)
    except BaseException:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        raise
    return path


def _decode_image(payload: dict[str, Any]) -> tuple[bytes | None, str | None, str | None]:
    """返回 (图片字节, 扩展名, 错误消息)。"""
    mime = payload.get("mime", "")
    if mime not in EXT_FOR_MIME:
        return None, None, f"不支持的图片类型: {mime}"
    try:
        data = base64.b64decode(payload.get("b64", ""), validate=True)
    except Exception:  # noqa: BLE001
        return None, None, "图片 base64 解码失败"
    if len(data) > MAX_IMAGE_BYTES:
        return None, None, f"图片超过 {MAX_IMAGE_BYTES} 字节上限"
    return data, EXT_FOR_MIME[mime], None


def handle_response(payload: dict[str, Any], staging: str | None = None) -> tuple[str | None, str | None]:
    """返回 (插入文本, 错误消息)。

    图片：不带 staging 时（手动 CLI）写到系统临时目录并返回路径，路径即交付物；
    带 staging（Pi 扩展的单次操作私有目录）时写入其中固定名 image.<ext>，返回该文件名，
    所有权由调用方的 staging 目录承担。两种情况下写入失败都不留文件。
    """
    kind = payload.get("kind")
    if kind == "text":
        text = payload.get("text", "")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return None, f"文本超过 {MAX_TEXT_BYTES} 字节上限"
        return text, None
    if kind == "image":
        data, ext, error = _decode_image(payload)
        if error is not None:
            return None, error
        if staging is not None:
            _create_private(staging, ARTIFACT_NAME_FOR_EXT[ext], data)
            return ARTIFACT_NAME_FOR_EXT[ext], None
        return _create_private(tempfile.gettempdir(), f"pi-remote-clipboard-{uuid.uuid4()}.{ext}", data), None
    if kind == "empty":
        return None, "剪贴板为空"
    if kind == "error":
        return None, str(payload.get("message", "未知错误"))
    return None, f"未知响应: {kind}"
