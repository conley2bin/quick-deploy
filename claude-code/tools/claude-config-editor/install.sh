#!/bin/bash
# ============================================================================
# Claude Config Editor 安装脚本
# ============================================================================
#
# 【项目简介】
#   Claude Config Editor - Web GUI for Claude configuration management
#   解决 Claude 配置文件膨胀问题,提供可视化管理界面
#
# 【核心功能】
#   • 可视化查看项目占用空间(按大小排序)
#   • 批量删除旧项目(删除前 10 个最大项目 = 释放 90% 空间)
#   • 导出重要对话记录(保留有价值的内容)
#   • MCP 服务器管理(无需手动编辑 JSON)
#   • 自动备份(所有操作前自动备份,支持撤销)
#
# 【典型效果】
#   • 配置文件从 17 MB 减少到 732 KB (减少 95.7%)
#   • Claude 启动速度显著提升
#   • 零外部依赖,仅需 Python 3.7+
#
# 【安装位置】
#   项目根目录: claude-config-editor/
#
# 【使用方法】
#   安装后运行:
#     python3 claude-config-editor/server.py
#   然后浏览器打开:
#     http://localhost:8080
#
# 【详细文档】
#   GitHub: https://github.com/gagarinyury/claude-config-editor
#   Stars: 400+
#   License: MIT
# ============================================================================

set -e

# 配置
REPO_URL="https://github.com/gagarinyury/claude-config-editor.git"
# 获取项目根目录（脚本在 tools/claude-config-editor/，向上两级到根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="$PROJECT_ROOT/claude-config-editor"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  Claude Config Editor 安装${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# 检查 git 是否安装
if ! command -v git &> /dev/null; then
    echo -e "${RED}错误: 未找到 git 命令${NC}"
    echo -e "${YELLOW}请先安装 git:${NC}"
    echo "  Ubuntu/Debian: sudo apt-get install git"
    echo "  macOS: brew install git"
    exit 1
fi

# 检查 Python 是否安装
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: 未找到 python3 命令${NC}"
    echo -e "${YELLOW}请先安装 Python 3.7+${NC}"
    exit 1
fi

# 检查 Python 版本
PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo -e "${BLUE}检测到 Python 版本: ${PYTHON_VERSION}${NC}"

# 检查安装目录
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}检测到已安装版本${NC}"
    echo -e "${BLUE}正在更新...${NC}"
    echo ""

    cd "$INSTALL_DIR"

    # 保存本地更改(如果有)
    if ! git diff-index --quiet HEAD --; then
        echo -e "${YELLOW}检测到本地更改,正在保存...${NC}"
        git stash
    fi

    # 拉取最新版本
    git pull origin main

    # 恢复本地更改(如果有)
    if git stash list | grep -q "stash@{0}"; then
        echo -e "${YELLOW}恢复本地更改...${NC}"
        git stash pop
    fi

    echo ""
    echo -e "${GREEN}✓ 更新成功!${NC}"
else
    echo -e "${BLUE}正在克隆 claude-config-editor 仓库...${NC}"
    echo ""

    # 创建父目录
    mkdir -p "$(dirname "$INSTALL_DIR")"

    # 克隆仓库
    git clone "$REPO_URL" "$INSTALL_DIR"

    echo ""
    echo -e "${GREEN}✓ 安装成功!${NC}"
fi

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  安装完成!${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

echo -e "${YELLOW}启动方式:${NC}"
echo -e "  ${GREEN}python3 $INSTALL_DIR/server.py${NC}"
echo ""

echo -e "${BLUE}首次使用提示:${NC}"
echo "  1. 建议先点击 'Export Config' 导出配置备份"
echo "  2. 查看项目大小排序,识别占用空间最大的项目"
echo "  3. 删除项目前可以先导出对话记录"
echo "  4. 工具会自动备份,支持撤销操作"
echo ""

echo -e "${BLUE}典型效果:${NC}"
echo "  配置文件从 17 MB → 732 KB (减少 95.7%)"
echo "  Claude 启动速度显著提升"
echo ""

echo -e "${BLUE}更多信息:${NC}"
echo "  GitHub: https://github.com/gagarinyury/claude-config-editor"
echo "  本地文档: $(dirname "$0")/README.md"
echo ""
