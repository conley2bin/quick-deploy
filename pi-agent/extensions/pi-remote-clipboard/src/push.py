"""把本机内容推送到 SSH 对端剪贴板（复制方向）。

用法：
  push.py [图片路径]

- 带路径：读取文件，识别图片类型，推送到对端剪贴板（image MIME）。
- 不带路径：读取本机剪贴板（图片优先，其次文本），推送到对端。

仅在 SSH 终端中生效；本地会话返回退出码 2。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import provider
import requester
from ssh_detect import is_ssh_terminal

MAX_FILE_BYTES = int(os.environ.get("PI_REMOTE_CLIPBOARD_MAX_IMAGE_BYTES", str(32 * 1024 * 1024)))


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def build_push_payload(source: str | None) -> tuple[dict | None, str | None]:
    """返回 (payload, error)。source 为 None 时读本机剪贴板。"""
    if source is None:
        payload = provider.read_clipboard()
        if payload.get("kind") == "empty":
            return None, "本机剪贴板为空"
        if payload.get("kind") == "error":
            return None, str(payload.get("message"))
        return payload, None
    path = Path(source)
    if not path.is_file():
        return None, f"文件不存在: {source}"
    data = path.read_bytes()
    if not data:
        return None, "文件为空"
    if len(data) > MAX_FILE_BYTES:
        return None, f"文件超过 {MAX_FILE_BYTES} 字节上限"
    mime = detect_image_mime(data)
    if mime is None:
        return None, "文件不是支持的图片（png/jpeg/webp/gif）"
    return {"kind": "image", "mime": mime, "b64": __import__("base64").b64encode(data).decode("ascii")}, None


def main() -> int:
    if not is_ssh_terminal():
        return 2
    source = sys.argv[1] if len(sys.argv) > 1 else None
    payload, error = build_push_payload(source)
    if error is not None or payload is None:
        print(error or "无内容可推送", file=sys.stderr)
        return 1
    try:
        if payload["kind"] == "image":
            import base64
            result = requester.push_image(base64.b64decode(payload["b64"]), payload["mime"])
        else:
            result = requester.push_text(payload["text"])
    except ConnectionRefusedError:
        print("无法连接剪贴板桥：对端 provider 未运行或 remote forwarding 未建立", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"推送失败: {exc}", file=sys.stderr)
        return 1
    if result.get("kind") != "ok":
        print(f"对端写入失败: {result.get('message', '未知错误')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
