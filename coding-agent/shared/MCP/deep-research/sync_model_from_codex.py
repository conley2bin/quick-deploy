#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
MODEL_RE = re.compile(r'^\s*model\s*=\s*"([^"]+)"\s*$')


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    default_env = (script_dir / ".env").resolve()
    default_codex = (Path.home() / ".codex" / "config.toml").resolve()

    p = argparse.ArgumentParser(
        description="从 ~/.codex/config.toml 同步 model 到 deep-research .env"
    )
    p.add_argument(
        "--codex-config",
        type=Path,
        default=default_codex,
        help=f"Codex 配置文件路径（默认: {default_codex}）",
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=default_env,
        help=f".env 文件路径（默认: {default_env}）",
    )
    return p.parse_args()


def read_codex_model(path: Path) -> str:
    if not path.is_file():
        raise SystemExit(f"error: 找不到 Codex 配置文件: {path}")

    section = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        m_section = SECTION_RE.match(line)
        if m_section:
            section = m_section.group(1)
            continue
        if section is not None:
            continue
        m_model = MODEL_RE.match(line)
        if m_model:
            return m_model.group(1)

    raise SystemExit(f'error: {path} 缺少顶层字段 "model"')


def upsert_env_key(lines: list[str], key: str, value: str) -> list[str]:
    target = f"{key}={value}\n"
    for i, line in enumerate(lines):
        m = ENV_LINE_RE.match(line.strip())
        if not m:
            continue
        if m.group(1) == key:
            lines[i] = target
            return lines
    lines.append(target)
    return lines


def update_env(path: Path, model: str) -> None:
    if not path.is_file():
        raise SystemExit(
            f"error: 找不到 .env 文件: {path}\n"
            "请先创建并填写该 .env 文件"
        )

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines = upsert_env_key(lines, "MCP_THINKING_MODEL", model)
    lines = upsert_env_key(lines, "MCP_TASK_MODEL", model)
    path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    codex_config = args.codex_config.expanduser().resolve()
    env_file = args.env_file.expanduser().resolve()

    model = read_codex_model(codex_config)
    update_env(env_file, model)
    print(f"model={model}")
    print(f"updated={env_file}")


if __name__ == "__main__":
    main()
