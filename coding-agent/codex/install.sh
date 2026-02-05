#!/bin/bash

# OpenAI Codex CLI 安装脚本 (Homebrew方式)
# 用于Ubuntu/Linux系统安装最新版本的Codex

set -e

echo "=========================================="
echo "OpenAI Codex CLI 安装脚本"
echo "=========================================="
echo ""

# 检查是否已安装Homebrew
if ! command -v brew &> /dev/null; then
    echo "[1/3] 未检测到Homebrew，开始安装..."
    echo ""

    # 安装Homebrew
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Linux 需要额外配置 PATH
    echo ""
    echo "配置 Homebrew 环境变量..."

    # 检测 shell 类型并添加到配置文件
    if [ -n "$BASH_VERSION" ]; then
        SHELL_RC="$HOME/.bashrc"
    elif [ -n "$ZSH_VERSION" ]; then
        SHELL_RC="$HOME/.zshrc"
    else
        SHELL_RC="$HOME/.profile"
    fi

    # 添加 Homebrew 到 PATH
    echo 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"' >> "$SHELL_RC"

    # 立即生效
    eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"

    echo "Homebrew安装完成"
else
    echo "[1/3] 检测到Homebrew已安装"
    brew --version
fi

echo ""
echo "[2/2] 安装/更新 OpenAI Codex CLI..."

# 检查Codex是否已安装
if command -v codex &> /dev/null; then
    CURRENT_VERSION=$(codex --version 2>/dev/null || echo "未知版本")
    echo "检测到Codex已安装: $CURRENT_VERSION"
    echo "更新到最新版..."
    brew upgrade codex
else
    echo "首次安装Codex..."
    brew install codex
fi

echo ""
echo "=========================================="
echo "完成！"
echo "=========================================="
echo ""
echo "当前版本:"
codex --version

echo ""
echo "使用方法:"
echo "  直接运行: codex"
echo ""
echo "首次运行需要登录:"
echo "  - 使用ChatGPT账号登录 (推荐)"
echo "  - 或配置OpenAI API密钥"
echo ""
echo "官方文档: https://github.com/openai/codex"
echo "=========================================="
