"""Pi Ctrl+V 集成的 CLI 端点。

Pi 扩展通过调用本命令完成 Ctrl+V：
- 本地：走原生 wl-paste/xclip 路径（由 Pi 自己处理，不经过本模块）
- SSH：向 remote-forwarded 的 provider 请求剪贴板

本命令的 stdout 是要插入 Pi 输入框的内容；空 stdout 表示没有可插入内容。
退出码非 0 时 stderr 携带错误原因。
"""
from __future__ import annotations

import sys

from requester import DEFAULT_PORT, handle_response, request_clipboard
from ssh_detect import is_ssh_terminal


def main() -> int:
    if not is_ssh_terminal():
        # 本地 Pi：不接管，返回约定空输出让调用方回退原生逻辑
        return 2

    try:
        payload = request_clipboard()
    except ConnectionRefusedError:
        print("无法连接剪贴板桥：对端 provider 未运行或 remote forwarding 未建立", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"剪贴板桥请求失败: {exc}", file=sys.stderr)
        return 1

    text, error = handle_response(payload)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if text is None:
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
