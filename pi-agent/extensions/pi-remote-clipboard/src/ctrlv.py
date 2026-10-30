"""Pi Ctrl+V 集成的 CLI 端点。

Pi 扩展通过调用本命令完成 Ctrl+V：
- 本地：走原生 wl-paste/xclip 路径（由 Pi 自己处理，不经过本模块）
- SSH：向 remote-forwarded 的 provider 请求剪贴板

手动运行：stdout 是要插入 Pi 输入框的内容（图片为写在系统临时目录的 0600 文件路径）；
空 stdout 表示没有可插入内容。

Pi 扩展运行（`--artifact-staging DIR`，DIR 是扩展为这次操作创建的私有目录，本命令从不创建它）：
stdout 第一行是版本化回执 JSON，其后是原文：
- 文本：`{"protocol":"pi-remote-clipboard/artifact-1","kind":"text"}\n<剪贴板文本原样>`
- 图片：`{"protocol":...,"kind":"image","name":"image.<ext>"}\n`，文件写在 DIR 内；
扩展据回执而不是内容猜测区分图片与文本，并负责 DIR 的清理与交付。

退出码 2 表示本地会话；其他非 0 时 stderr 携带错误原因。
"""
from __future__ import annotations

import json
import sys

from requester import ARTIFACT_PROTOCOL, DEFAULT_PORT, handle_response, request_clipboard
from ssh_detect import is_ssh_terminal


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    staging: str | None = None
    if args:
        if len(args) != 2 or args[0] != "--artifact-staging" or not args[1]:
            print("用法: ctrlv.py [--artifact-staging DIR]", file=sys.stderr)
            return 64
        staging = args[1]

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

    try:
        text, error = handle_response(payload, staging)
    except OSError as exc:
        print(f"剪贴板图片写入失败: {exc}", file=sys.stderr)
        return 1
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if text is None:
        return 0
    if staging is not None:
        receipt = {"protocol": ARTIFACT_PROTOCOL, "kind": payload["kind"]}
        if payload["kind"] == "image":
            receipt["name"] = text
            text = ""
        sys.stdout.write(json.dumps(receipt) + "\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
