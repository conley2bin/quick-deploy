#!/bin/bash
# Connect locally to one inventory machine; never provision or configure the remote host.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
用法: ./run_server.sh [--config PATH] [--moonlight | --ssh] <机器名>
       ./run_server.sh [--config PATH] --list

默认以本机 Moonlight 连接指定主机的 Desktop；--moonlight 与默认相同。
--ssh 仅打开到该主机的交互式 SSH 登录。--list 只列出清单名称。
默认清单是本目录的 machines.local.yaml；--config 按当前工作目录解析。
先运行 ./install.sh，并在 Moonlight 中完成与 Sunshine 的独立配对。
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

exec /usr/bin/python3 "$SCRIPT_DIR/service/run-server.py" "$@"
