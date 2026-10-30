"""Pi 扩展的版本化图片制品入口：`ctrlv-artifact-v1.py DIR`（DIR 是扩展创建的私有暂存目录）。

旧版助手目录里没有这个文件，所以扩展指向旧目录时在任何生产者运行前就被拒绝；
新入口配旧的 ctrlv.py/requester.py（半更新）则在下面的契约检查处以退出码 70 拒绝，
先于任何 SSH/provider 读取和文件创建。退出码 2 在此协议中只可能是解释器/参数错误，
绝不表示“本地会话”（本地由回执 kind=local 表达）。
"""
from __future__ import annotations

import inspect
import sys


def _refuse(reason: str) -> None:
    print(f"pi-remote-clipboard artifact-1 契约不满足: {reason}", file=sys.stderr)
    raise SystemExit(70)


try:
    import ctrlv
    import requester

    if getattr(ctrlv, "ARTIFACT_API", None) != 1 or not callable(getattr(ctrlv, "artifact_paste", None)):
        raise ImportError("ctrlv.artifact_paste 缺失")
    if "staging" not in inspect.signature(requester.handle_response).parameters:
        raise ImportError("requester.handle_response 不支持 staging")
except Exception as exc:  # noqa: BLE001 - 任何导入/签名问题都是契约失败
    _refuse(f"{type(exc).__name__}: {exc}")

if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1]:
        print("用法: ctrlv-artifact-v1.py DIR", file=sys.stderr)
        raise SystemExit(64)
    raise SystemExit(ctrlv.artifact_paste(sys.argv[1]))
