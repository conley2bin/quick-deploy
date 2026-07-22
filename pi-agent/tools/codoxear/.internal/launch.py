#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

HELPER_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = HELPER_DIR / ".env"
VENV_BIN = HELPER_DIR / ".runtime" / "venv" / "bin"


def parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"找不到 env 文件: {path}")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    for key, value in parse_env(ENV_FILE).items():
        env.setdefault(key, value)
    for key in ("CODEX_HOME", "PI_HOME"):
        value = env.get(key)
        if value:
            env[key] = str(Path(value).expanduser())
    return env


def validate_env() -> None:
    env = runtime_env()
    password = env.get("CODEX_WEB_PASSWORD", "").strip()
    if not password or password == "change-me":
        raise RuntimeError(f"请在 {ENV_FILE} 中设置有效的 CODEX_WEB_PASSWORD")
    port = env.get("CODEX_WEB_PORT", "").strip()
    if port and not port.isdecimal():
        raise RuntimeError(f"CODEX_WEB_PORT 必须是整数: {port}")


def exec_program(name: str, args: list[str], env: dict[str, str]) -> None:
    program = VENV_BIN / name
    if not program.is_file():
        raise RuntimeError(f"Codoxear 尚未安装: {program}")
    os.execve(program, [str(program), *args], env)


def access_path(env: dict[str, str]) -> str:
    prefix = env.get("CODEX_WEB_URL_PREFIX", "").strip()
    if not prefix or prefix == "/":
        return "/"
    return "/" + prefix.strip("/") + "/"


def print_usage(revision: str) -> None:
    env = runtime_env()
    host = env.get("CODEX_WEB_HOST", "::").strip() or "::"
    port = env.get("CODEX_WEB_PORT", "8743").strip() or "8743"
    path = access_path(env)
    default_backend = env.get("CODEX_WEB_DEFAULT_AGENT_BACKEND", "codex").strip() or "codex"
    cookie_secure = env.get("CODEX_WEB_COOKIE_SECURE", "0").strip() == "1"
    local_url = f"http://127.0.0.1:{port}{path}"

    print()
    print("=" * 72)
    print("Codoxear Web server 即将启动")
    print("=" * 72)
    print(f"远程 main revision : {revision}")
    print(f"监听地址             : {host}:{port}")
    print(f"本机浏览器           : {local_url}")
    if host in {"::", "0.0.0.0"}:
        print(f"局域网浏览器         : http://<本机局域网 IP>:{port}{path}")
        print("网络范围             : 当前监听所有网络接口")
    elif host in {"127.0.0.1", "localhost", "::1"}:
        print("网络范围             : 仅本机；远程访问需要 SSH tunnel、VPN 或反向代理")
    else:
        print(f"配置地址             : http://{host}:{port}{path}")
    print(f"网页默认后端         : {default_backend}")
    print(f"登录密码             : 使用 {ENV_FILE} 中的 CODEX_WEB_PASSWORD（值不显示）")
    print()
    print("终端会话：")
    print("  codox [Codex 参数...]   启动 terminal-owned Codex 会话")
    print("  piox  [Pi 参数...]      启动 terminal-owned Pi 会话")
    print("  如果命令尚未生效，请重新打开终端或 source 对应的 .zshrc/.bashrc。")
    print()
    print("网页操作：")
    print("  1. 打开上面的浏览器地址并输入 CODEX_WEB_PASSWORD。")
    print("  2. 选择 T 标记的 terminal-owned 会话，或点击 New session 新建会话。")
    print("  3. New session 可选择 Codex/Pi；启用 Create in tmux 时可用")
    print("     `tmux attach -t codoxear` 从本机观察对应终端。")
    print("  4. 网页中的 Delete 会终止底层 broker 和 agent 进程，不只是隐藏记录。")
    print()
    print("进程控制：")
    print("  Ctrl-C 只停止 Web server；已有会话日志保留，terminal-owned 会话继续运行。")
    print("  重新执行 ./run_server.sh 会再次同步远程 main 后启动 server。")
    print()
    if cookie_secure:
        print("传输提示：CODEX_WEB_COOKIE_SECURE=1；请通过 HTTPS 反向代理/VPN 地址访问。")
        print("          Codoxear 本身仍提供 HTTP，不会自行终止 TLS。")
    else:
        print("传输提示：当前是明文 HTTP。密码和会话内容不应暴露到不可信网络。")
        print("          远程使用建议通过 Tailscale、VPN、SSH tunnel 或 HTTPS 反向代理。")
    print("=" * 72)
    print(flush=True)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "validate":
        validate_env()
        return
    if command == "describe":
        revision = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        print_usage(revision)
        return

    env = runtime_env()
    if command == "server":
        validate_env()
        os.chdir(HELPER_DIR)
        exec_program("codoxear-server", [], env)
    if command in {"codex", "pi"}:
        if command == "pi":
            env["CODEX_WEB_AGENT_BACKEND"] = "pi"
        exec_program("codoxear-broker", ["--", *sys.argv[2:]], env)
    raise RuntimeError("usage: launch.py validate|describe|server|codex|pi [args ...]")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
