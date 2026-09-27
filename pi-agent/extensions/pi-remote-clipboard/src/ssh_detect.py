"""判断当前输入是否来自 SSH 终端。

规则：
- Pi 不在 tmux：看进程祖先链里是否有 sshd
- Pi 在 tmux：看当前输入对应的 tmux client 祖先链里是否有 sshd
"""
from __future__ import annotations

import os
import subprocess


def _ancestors(pid: int) -> list[str]:
    out: list[str] = []
    current = pid
    for _ in range(16):
        if current <= 1:
            break
        try:
            with open(f"/proc/{current}/stat", "r", encoding="utf-8", errors="replace") as f:
                parts = f.read().split()
            comm = parts[1].strip("()")
            out.append(comm)
            current = int(parts[3])
        except Exception:  # noqa: BLE001
            break
    return out


def _process_is_ssh_descendant(pid: int) -> bool:
    return any(comm == "sshd" for comm in _ancestors(pid))


def is_ssh_terminal() -> bool:
    tmux_env = os.environ.get("TMUX", "")
    tmux_pane = os.environ.get("TMUX_PANE", "")
    if not tmux_env or not tmux_pane:
        return _process_is_ssh_descendant(os.getppid())

    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", tmux_pane, "#{client_pid}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return False
    client_pid = result.stdout.strip()
    if not client_pid.isdigit():
        return False
    return _process_is_ssh_descendant(int(client_pid))
