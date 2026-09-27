"""判断当前输入是否来自 SSH 终端。

规则：
- Pi 不在 tmux：看进程祖先链里是否有 sshd
- Pi 在 tmux：列出当前 session 的全部 client，取最近有输入（client_activity 最大）
  的那个，看它的祖先链里是否有 sshd。

为什么不用 display-message 的 #{client_pid}：实测它解析到的不是正在输入的
client（多 client 同 session 时返回别的 client），会让 SSH 判定随机失效。
按快捷键的那一刻，发起输入的 client 必然是最近活动的。
"""
from __future__ import annotations

import os
import subprocess


def _ancestors(pid: int) -> list[str]:
    """读取 /proc/<pid>/stat 的祖先链。comm 可能带空格（如 "tmux: client"），
    不能用 split()，必须按最后一个 ')' 定位。"""
    out: list[str] = []
    current = pid
    for _ in range(16):
        if current <= 1:
            break
        try:
            with open(f"/proc/{current}/stat", "r", encoding="utf-8", errors="replace") as f:
                stat = f.read()
            close = stat.rindex(")")
            comm = stat[stat.index("(") + 1:close]
            out.append(comm)
            # ')' 之后是 " state ppid ..."，ppid 是 rest 的第 2 个字段
            current = int(stat[close + 2:].split()[1])
        except Exception:  # noqa: BLE001
            break
    return out


def _process_is_ssh_descendant(pid: int) -> bool:
    return any(comm == "sshd" for comm in _ancestors(pid))


def _tmux(args: list[str], timeout: float = 2) -> str | None:
    try:
        result = subprocess.run(
            ["tmux", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _tmux_ssh_client(pane: str) -> bool:
    """pane 所在 session 中最近活动的 client 是否为 SSH 后代。"""
    session = _tmux(["display-message", "-p", "-t", pane, "#{session_name}"])
    if not session or not session.strip():
        return False
    listing = _tmux(["list-clients", "-t", session.strip(), "-F", "#{client_pid} #{client_activity}"])
    if not listing:
        return False
    best_pid = -1
    best_activity = -1
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            activity = int(parts[1])
        except ValueError:
            continue
        if activity > best_activity:
            best_activity = activity
            best_pid = pid
    if best_pid <= 0:
        return False
    return _process_is_ssh_descendant(best_pid)


def is_ssh_terminal() -> bool:
    tmux_env = os.environ.get("TMUX", "")
    tmux_pane = os.environ.get("TMUX_PANE", "")
    if not tmux_env or not tmux_pane:
        return _process_is_ssh_descendant(os.getppid())
    return _tmux_ssh_client(tmux_pane)
