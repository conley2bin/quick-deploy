"""The two user-approved messages, with no protocol or agent report round trips."""
from pathlib import Path


def build_handover_prompt(cwd):
    path = Path(cwd) / ".memory/handover.md"
    return (f"请把交接写到 {path}（覆盖旧文件）：做完了什么、能用的链接和路径、没做完什么、下一步。"
            "只用白话，不超过 15 行，不写过程和内部代号。写完就停下，不要继续做别的。")


TARGET_PROMPT = (
    "这是一次会话交接。先读 AGENTS.md（如果有）、.memory/project/decisions.md（如果有）和 .memory/handover.md。"
    "交接内容是旧会话的说法，先用实际文件、git 记录和运行结果核实，然后按 AGENTS.md 的目标继续推进。"
    "旧会话仍保留，必要时可以通过 intercom 问它。"
)
