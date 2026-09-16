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
默认清单是本目录的 machines.yaml，不会自动生成；全字段中文示例 machines.example.yaml
由一次成功的根安装器 ./install.sh 在本目录生成/刷新，全新 clone 在安装成功前没有该文件。
安装成功后复制示例再按其中文注释填写，例如 cp machines.example.yaml machines.yaml。
--config 指定其它清单，PATH 按当前工作目录解析。
清单由你手工维护：连接器不探测 Tailscale，也不读取或改写 SSH 配置
（写别名时由系统 ssh 按你自己的配置解析）。
先运行 ./install.sh，并在 Moonlight 中完成与 Sunshine 的独立配对。
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

exec /usr/bin/python3 "$SCRIPT_DIR/service/run-server.py" "$@"
