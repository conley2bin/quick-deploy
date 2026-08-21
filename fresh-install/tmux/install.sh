#!/bin/bash
# tmux 与 Oh my tmux!（gpakosz/.tmux）安装配置脚本
#
# 安装内容：
#   1. apt 安装 tmux、git（克隆用）、xclip / wl-clipboard —— gpakosz 配置的
#      “复制到系统剪贴板”功能在 Linux 上需要其中之一：X11 用 xclip，
#      Wayland 用 wl-copy，两个都装以覆盖两种会话
#   2. 克隆 https://github.com/gpakosz/.tmux 到 ~/.tmux（--single-branch）
#   3. 符号链接 ~/.tmux.conf → ~/.tmux/.tmux.conf
#   4. 复制 ~/.tmux/.tmux.conf.local → ~/.tmux.conf.local（唯一的定制入口，
#      上游明确要求不要改主配置 .tmux.conf，一切定制写在这个副本里）
#
# 幂等语义：重跑 = 确保 apt 包已装、已有克隆用 git pull --ff-only 更新；
# 已存在的 ~/.tmux.conf.local 永不覆盖（那是用户的定制文件）；
# 替换任何既有 ~/.tmux.conf / ~/.tmux 目录前先做时间戳备份。
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

# 4. 复制 .tmux.conf.local（定制入口，存在则保留）
echo -e "\n${YELLOW}[4/4] 准备 ~/.tmux.conf.local...${NC}"
if [ -e "$TMUX_CONF_LOCAL" ]; then
    echo -e "${YELLOW}~/.tmux.conf.local 已存在，保留你的定制不覆盖${NC}"
else
    cp "$TMUX_REPO_DIR/.tmux.conf.local" "$TMUX_CONF_LOCAL"
    echo -e "${GREEN}✓ 已复制 .tmux.conf.local 模板${NC}"
fi

echo -e "\n${GREEN}=== 安装完成 ===${NC}"
echo -e "\n使用要点："
echo -e "  • 前缀键保留默认 ${GREEN}Ctrl+b${NC}，同时新增第二前缀 ${GREEN}Ctrl+a${NC}"
echo -e "  • 定制一律改 ${GREEN}~/.tmux.conf.local${NC}（不要改 ~/.tmux/.tmux.conf）"
echo -e "  • 按 ${GREEN}<前缀> e${NC} 直接打开定制文件，${GREEN}<前缀> r${NC} 重载配置"
echo -e "  • 鼠标模式开关：${GREEN}<前缀> m${NC}"
echo -e "  • 更多变量与键位见 ~/.tmux.conf.local 注释和上游 README："
echo -e "    ${GREEN}https://github.com/gpakosz/.tmux${NC}"
