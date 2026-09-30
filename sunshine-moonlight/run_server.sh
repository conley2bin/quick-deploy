#!/bin/bash
# Connect locally to one inventory machine; never provision or configure the remote host.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
用法: ./run_server.sh [--config PATH] [--moonlight | --ssh] <机器名>
       ./run_server.sh [--config PATH] --list

默认以本机 Moonlight 连接指定主机的 Desktop；--moonlight 与默认相同。
连接前先用 Moonlight 查询该主机的配对状态：
- 已配对：直接启动 Desktop 串流。
- 明确未配对：打印目标主机、Sunshine Web UI 地址 https://<IP>:<基准端口+1>、本机随机
  生成的 4 位 PIN 和编号步骤，再运行 Moonlight 配对；配对返回后重新确认已配对，
  确认通过才进入串流。
- 无法确认（网络/未知输出）：打印 Moonlight 诊断与目标地址后以非零状态退出，
  不会静默配对或串流。
PIN 只打印在本终端，不写入清单或任何文件；不会自动打开浏览器，也不修改远端主机。
--ssh 仅打开到该主机的交互式 SSH 登录。--list 只列出清单名称。
默认清单是本目录的 machines.yaml：由一次成功的模块根安装器 ./install.sh 在所选安装阶段
全部成功后生成/刷新的全字段中文通用占位清单，安装成功后直接编辑它填写真实机器信息。
全新 clone 在首次成功安装前没有该文件，此时先按实际用途运行一次 ./install.sh。
--config 指定其它清单，PATH 按当前工作目录解析。
清单由你编辑维护：连接器不生成也不改写任何清单，不探测 Tailscale，也不读取或改写 SSH 配置
（写别名时由系统 ssh 按你自己的配置解析）。
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
esac

exec /usr/bin/python3 "$SCRIPT_DIR/service/run-server.py" "$@"
