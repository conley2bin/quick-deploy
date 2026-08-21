#!/bin/bash
# tmux 与 Oh my tmux!（gpakosz/.tmux）安装配置脚本
#
# 安装内容：
#   1. apt 安装 tmux、git（克隆用）、xclip / wl-clipboard —— gpakosz 配置的
#      “复制到系统剪贴板”功能在 Linux 上需要其中之一：X11 用 xclip，
#      Wayland 用 wl-copy，两个都装以覆盖两种会话
#   2. 克隆 https://github.com/gpakosz/.tmux 到 ~/.tmux（--single-branch）
#   3. 符号链接 ~/.tmux.conf → ~/.tmux/.tmux.conf
#   4. 符号链接 ~/.tmux.conf.local → 模块自带的 tmux.conf.local 基线
#      （上游规定定制写在 .tmux.conf.local；这里把它链到仓库文件，
#      定制即仓库改动，别的机器 git pull 本仓库即生效）
#
# 幂等语义：重跑 = 确保 apt 包已装、已有克隆用 git pull --ff-only 更新到最新；
# 替换任何既有 ~/.tmux.conf / ~/.tmux.conf.local / ~/.tmux 目录前先做时间戳备份。
#
# 失败语义：apt 失败或首次克隆失败 → 退出非零；已有克隆上的更新失败
# 只警告继续（离线重跑不应破坏已可用的安装）。在 setup.sh 中本步骤为
# tolerate：全新机器还没配代理时 GitHub 可能连不通，只提示不中止，
# 网络就绪后随时可单独重跑本脚本。

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_URL="https://github.com/gpakosz/.tmux.git"
TMUX_REPO_DIR="$HOME/.tmux"
TMUX_CONF="$HOME/.tmux.conf"
TMUX_CONF_LOCAL="$HOME/.tmux.conf.local"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BASELINE="$SCRIPT_DIR/tmux.conf.local"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

die() {
    echo -e "${RED}错误: $*${NC}" >&2
    exit 1
}

echo -e "${GREEN}=== 安装 tmux 与 gpakosz/.tmux 配置 ===${NC}\n"

# 1. 安装软件包
echo -e "${YELLOW}[1/4] 安装 tmux、git、剪贴板工具...${NC}"
sudo apt update
sudo apt install -y tmux git xclip wl-clipboard
TMUX_VERSION="$(tmux -V)"
echo -e "${GREEN}✓ $TMUX_VERSION 安装完成${NC}"

# 2. 克隆或更新 gpakosz/.tmux
echo -e "\n${YELLOW}[2/4] 安装 Oh my tmux! 配置仓库...${NC}"
if [ -d "$TMUX_REPO_DIR/.git" ]; then
    echo -e "${YELLOW}~/.tmux 已存在，用 git pull --ff-only 更新...${NC}"
    if git -C "$TMUX_REPO_DIR" pull --ff-only; then
        echo -e "${GREEN}✓ 配置仓库已是最新${NC}"
    else
        echo -e "${YELLOW}△ 更新失败（多为离线或本地有改动），保留现有版本继续${NC}"
    fi
else
    if [ -e "$TMUX_REPO_DIR" ]; then
        mv "$TMUX_REPO_DIR" "$TMUX_REPO_DIR.bak.$TIMESTAMP"
        echo -e "${YELLOW}已备份既有 ~/.tmux → ~/.tmux.bak.$TIMESTAMP${NC}"
    fi
    git clone --single-branch "$REPO_URL" "$TMUX_REPO_DIR" \
        || die "克隆 $REPO_URL 失败（全新机器多为尚无代理访问 GitHub）。网络就绪后重跑本脚本即可"
    echo -e "${GREEN}✓ 配置仓库克隆完成${NC}"
fi
[ -f "$TMUX_REPO_DIR/.tmux.conf" ] \
    || die "~/.tmux 中找不到 .tmux.conf，该目录不是预期的 gpakosz/.tmux 仓库"

# 3. 符号链接 ~/.tmux.conf
echo -e "\n${YELLOW}[3/4] 链接 ~/.tmux.conf...${NC}"
# 用 readlink -f 规范化比较，兼容相对链接（上游官方命令建的就是相对链接）
if [ -L "$TMUX_CONF" ] && [ "$(readlink -f "$TMUX_CONF")" = "$TMUX_REPO_DIR/.tmux.conf" ]; then
    echo -e "${YELLOW}~/.tmux.conf 已指向 ~/.tmux/.tmux.conf，跳过${NC}"
else
    if [ -e "$TMUX_CONF" ] || [ -L "$TMUX_CONF" ]; then
        mv "$TMUX_CONF" "$TMUX_CONF.bak.$TIMESTAMP"
        echo -e "${YELLOW}已备份既有 ~/.tmux.conf → ~/.tmux.conf.bak.$TIMESTAMP${NC}"
    fi
    ln -s "$TMUX_REPO_DIR/.tmux.conf" "$TMUX_CONF"
    echo -e "${GREEN}✓ ~/.tmux.conf → ~/.tmux/.tmux.conf${NC}"
fi

# 4. 链接 ~/.tmux.conf.local 到模块基线（单一事实源：改动即仓库改动）
echo -e "\n${YELLOW}[4/4] 链接 ~/.tmux.conf.local...${NC}"
if [ -L "$TMUX_CONF_LOCAL" ] && [ "$(readlink -f "$TMUX_CONF_LOCAL")" = "$LOCAL_BASELINE" ]; then
    echo -e "${YELLOW}~/.tmux.conf.local 已指向模块基线，跳过${NC}"
else
    if [ -e "$TMUX_CONF_LOCAL" ] || [ -L "$TMUX_CONF_LOCAL" ]; then
        mv "$TMUX_CONF_LOCAL" "$TMUX_CONF_LOCAL.bak.$TIMESTAMP"
        echo -e "${YELLOW}已备份既有 ~/.tmux.conf.local → ~/.tmux.conf.local.bak.$TIMESTAMP${NC}"
    fi
    ln -s "$LOCAL_BASELINE" "$TMUX_CONF_LOCAL"
    echo -e "${GREEN}✓ ~/.tmux.conf.local → $LOCAL_BASELINE${NC}"
fi

echo -e "\n${GREEN}=== 安装完成 ===${NC}"
echo -e "\n使用要点："
echo -e "  • 前缀键保留默认 ${GREEN}Ctrl+b${NC}，同时新增第二前缀 ${GREEN}Ctrl+a${NC}"
echo -e "  • 定制改 ${GREEN}$LOCAL_BASELINE${NC}，或在 tmux 里按 ${GREEN}<前缀> e${NC} —— 经符号链接是同一个文件"
echo -e "  • 改完按 ${GREEN}<前缀> r${NC} 重载生效；${GREEN}git commit${NC} 后别的机器 git pull 即同步"
echo -e "  • 鼠标模式开关：${GREEN}<前缀> m${NC}"
echo -e "  • 全部可选项见上游模板 ~/.tmux/.tmux.conf.local 和上游 README："
echo -e "    ${GREEN}https://github.com/gpakosz/.tmux${NC}"
