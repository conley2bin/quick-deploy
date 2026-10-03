#!/bin/bash
# One selected-screen managed session, or unchanged interactive SSH routing.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'USAGE'
用法: ./run_server.sh [--config PATH] [--moonlight] [--moonlight-binary PATH] <机器名>
       ./run_server.sh [--config PATH] --ssh <机器名>
       ./run_server.sh [--config PATH] --list

默认从交互终端连接：读取主机实时屏幕 → 获取预约 → 上下键（左右键同义）选择、Enter 连接、Esc 取消。
主机需要已安装并明确登记的 quick-deploy-display-control；忙碌/未知状态拒绝，不接管已有串流。
获取预约后、准备屏幕前，先用 Moonlight 查询配对状态；明确未配对才打印随机 4 位 PIN 和
目标 Sunshine Web UI https://<IP>:<基准端口+1> 的手工配对步骤，并重新查询确认配对
（官方配对会保存 Sunshine 实例 UUID，受控重启还会验证同一身份）。准备精确屏幕后再次
查询应用列表，确认唯一大小写不敏感 Desktop 标题，才持有预约监督官方串流。
主机报告实例身份已变化时停止并保留预约，按提示在主机本地运行身份对账。
请求选中屏幕当前尺寸/整数 FPS；码率以 4K60/20Mbps 为像素率起点并限制在官方范围，
SDR/4:2:0、auto codec、borderless 是初始策略，不保证实际视频 FPS/码率最优。
退出前明确记录受管关闭意图，再用官方 quit 并确认同代关闭；exit0 不是成功证明。
控制/启动/关闭不确定时保留预约，需要主机可信对账；不会默认切屏、强制重置或取消其它会话。
PIN 只打印在终端；不会自动打开浏览器或手工改写 Moonlight.conf。
Linux 默认使用已安装的 ~/.local/bin/moonlight；可用 --moonlight-binary 指定本地官方程序。
macOS 可用同一 Python 核心的 Moonlight.app/PATH；Windows 使用 run_server.cmd 和
PATH 中的 Moonlight.exe 或 --moonlight-binary。主机生命周期目前仅 Linux 适配器。
--ssh 仅打开到该主机的交互式 SSH 登录。--list 只列出清单名称，不接触控制器或 GUI。
默认清单是本目录的 machines.yaml：由一次成功的模块根安装器 ./install.sh 在所选安装阶段
全部成功且文件缺失时新建的全字段中文通用占位清单；已有普通清单会原样保留。
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
