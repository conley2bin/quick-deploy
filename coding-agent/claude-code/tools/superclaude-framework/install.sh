#!/bin/bash
# ============================================================================
# SuperClaude Framework 安装脚本
# ============================================================================
#
# 元编程框架：将 Claude Code 转变为结构化开发平台
#
# 核心组件：
#   • 3 个插件（PM Agent、Research、Index）
#   • 16 个智能代理（领域专家型 AI）
#   • 7 种运行模式（Quick/Standard/Deep/Exhaustive）
#   • 8 个 MCP 服务器（Tavily、Serena、Mindbase）
#
# 性能优化：Token 减少 94%（58K → 3K），速度提升 2-3 倍
# 文档：https://github.com/SuperClaude-Org/SuperClaude_Framework
# ============================================================================

set -e

echo "============================================================"
echo "  SuperClaude Framework 安装"
echo "============================================================"
echo ""

# 检查并安装 pipx
if ! command -v pipx &> /dev/null; then
    echo "未检测到 pipx，正在安装..."

    # 检测操作系统
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        if command -v apt &> /dev/null; then
            sudo apt update && sudo apt install -y pipx
        elif command -v dnf &> /dev/null; then
            sudo dnf install -y pipx
        elif command -v yum &> /dev/null; then
            sudo yum install -y pipx
        else
            # 使用 pip 安装
            python3 -m pip install --user pipx
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install pipx
        else
            python3 -m pip install --user pipx
        fi
    else
        # 其他系统使用 pip
        python3 -m pip install --user pipx
    fi

    # 配置 PATH
    pipx ensurepath

    # 重新加载 PATH
    export PATH="$HOME/.local/bin:$PATH"

    echo "pipx 安装完成"
    echo ""
fi

# 安装 superclaude
echo "正在安装 superclaude..."
pipx install superclaude

echo ""
echo "正在安装命令..."
superclaude install

echo ""
echo "============================================================"
echo "  安装完成!"
echo "============================================================"
echo ""
echo "可选: 安装 MCP 服务器（增强性能）"
echo "  查看可用服务器: superclaude mcp --list"
echo "  安装所有服务器: superclaude mcp"
echo "  安装指定服务器: superclaude mcp --servers tavily --servers context7"
echo ""
echo "验证安装:"
echo "  superclaude install --list"
echo "  superclaude doctor"
echo ""