#!/usr/bin/env python3
"""Render `discover_apps` JSON as the human-readable option-3 report."""
from __future__ import annotations

import json
import sys
from typing import Any

NAMES = {
    "baidunetdisk": "百度网盘",
    "wemeet": "腾讯会议",
    "feishu": "飞书",
    "wechat": "微信",
    "spark-store": "星火应用商店",
}


def report_lines(document: Any) -> list[str]:
    results = document if isinstance(document, list) else []
    lines = ["已支持的应用 ID（只读识别，不会启动应用，也不会改动配置）:"]
    if not results:
        lines.append("  （没有任何识别结果）")
    for item in results:
        if not isinstance(item, dict):
            continue
        app_id = str(item.get("id", "?"))
        status = str(item.get("status", "?"))
        label = NAMES.get(app_id, "")
        lines.append("  " + app_id + (f"（{label}）" if label else "") + ": " + status)
        for executable in item.get("executables") or []:
            if not isinstance(executable, dict):
                continue
            path = executable.get("path")
            if not path:
                continue
            lines.append("      " + str(executable.get("role") or "program") + ": " + str(path))
        if status != "resolved":
            reason = item.get("reason") or "安装形态不受支持或未安装"
            lines.append("      原因: " + str(reason))
    lines.append("")
    lines.append("识别结果只描述当前安装证据；不代表生成 Script 已重载，或某个连接已被路由。")
    return lines


def main() -> int:
    try:
        document = json.loads(sys.stdin.read())
    except (TypeError, ValueError):
        print("应用识别输出无法解析；未改动任何配置。")
        return 0
    print("\n".join(report_lines(document)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
