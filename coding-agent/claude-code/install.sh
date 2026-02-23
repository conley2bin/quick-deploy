#!/bin/bash
# ============================================================================
# Claude Code - Official CLI Installation Script
# ============================================================================
#
# Purpose: Install Anthropic's official Claude Code CLI
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# ============================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================================================${NC}"
echo -e "${BLUE}  Claude Code - Official CLI Installation${NC}"
echo -e "${BLUE}============================================================================${NC}"
echo ""

# 检查系统
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="Linux"
else
    echo -e "${RED}错误: 不支持的操作系统 ($OSTYPE)${NC}"
    echo "Claude Code 仅支持 macOS 和 Linux"
    exit 1
fi

echo -e "${GREEN}检测到系统: $OS${NC}"
echo ""

# 安装/更新 Claude Code
echo -e "${YELLOW}[1/1] 安装/更新 Claude Code...${NC}"
echo ""

# 检查 Claude Code 是否已安装
if command -v claude &> /dev/null; then
    CURRENT_VERSION=$(claude --version 2>/dev/null || echo "未知版本")
    echo -e "${GREEN}检测到 Claude Code 已安装: $CURRENT_VERSION${NC}"
    echo "使用官方安装脚本更新到最新版..."
    echo ""
else
    echo "使用官方安装脚本安装 Claude Code..."
    echo ""
fi

# 使用官方安装脚本
curl -fsSL https://claude.ai/install.sh | bash

echo ""
echo -e "${GREEN}✓ Claude Code 安装完成${NC}"

# 验证安装
echo ""
echo -e "${GREEN}当前版本:${NC}"
if command -v claude &> /dev/null; then
    claude --version

    # 检查安装类型
    echo ""
    echo "运行诊断检查..."
    claude doctor || true
else
    echo -e "${RED}✗ 安装失败: claude 命令不可用${NC}"
    exit 1
fi

# 输出使用提示
echo ""
echo -e "${BLUE}============================================================================${NC}"
echo -e "${GREEN}  完成！${NC}"
echo -e "${BLUE}============================================================================${NC}"
echo ""

echo -e "${YELLOW}快速开始：${NC}"
echo ""
echo "  1. 进入项目目录："
echo "     ${GREEN}cd /path/to/your/project${NC}"
echo ""
echo "  2. 启动 Claude Code："
echo "     ${GREEN}claude${NC}"
echo ""
echo "  3. 首次使用需要登录："
echo "     在 Claude Code 中输入 ${GREEN}/login${NC}"
echo "     或设置环境变量: ${GREEN}export ANTHROPIC_API_KEY=<your-key>${NC}"
echo ""

echo -e "${YELLOW}常用命令：${NC}"
echo ""
echo "  ${GREEN}claude${NC}                    启动 Claude Code"
echo "  ${GREEN}claude doctor${NC}             检查安装状态"
echo "  ${GREEN}claude --version${NC}          查看版本"
echo "  ${GREEN}claude --help${NC}             查看帮助"
echo ""

echo -e "${YELLOW}Claude Code 内部命令：${NC}"
echo ""
echo "  ${GREEN}/login${NC}                    登录 Claude 账户"
echo "  ${GREEN}/logout${NC}                   退出登录"
echo "  ${GREEN}/status${NC}                   查看认证状态"
echo "  ${GREEN}/help${NC}                     查看所有可用命令"
echo "  ${GREEN}/clear${NC}                    清空对话历史"
echo ""

echo -e "${YELLOW}更新 Claude Code：${NC}"
echo "  ${GREEN}brew upgrade claude-code${NC}"
echo ""

echo -e "${YELLOW}卸载 Claude Code：${NC}"
echo "  ${GREEN}brew uninstall --cask claude-code${NC}"
echo "  ${GREEN}rm -rf ~/.claude${NC}          (删除配置和缓存)"
echo ""

echo -e "${YELLOW}文档和资源：${NC}"
echo "  官方文档: ${BLUE}https://docs.anthropic.com/en/docs/claude-code${NC}"
echo "  本地 README: ${BLUE}./README.md${NC}"
echo ""

echo -e "${GREEN}安装脚本执行完成！${NC}"
