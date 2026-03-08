#!/bin/bash

# OpenAI Codex CLI 安装脚本 (npm 方式)
# 用于Ubuntu/Linux系统安装或更新Codex

set -e

echo "=========================================="
echo "OpenAI Codex CLI 安装脚本 (npm)"
echo "=========================================="
echo ""

# 检查是否已安装 npm
if ! command -v npm &> /dev/null; then
    echo "error: 未检测到 npm。请先安装 Node.js (含 npm)。" >&2
    exit 1
fi

echo "安装/更新 OpenAI Codex CLI..."

# 检查Codex是否已安装
if command -v codex &> /dev/null; then
    CURRENT_VERSION=$(codex --version 2>/dev/null || echo "未知版本")
    echo "检测到Codex已安装: $CURRENT_VERSION"
    echo "更新到最新版..."
    npm i -g @openai/codex@latest
else
    echo "首次安装Codex..."
    npm i -g @openai/codex
fi

echo ""
echo "=========================================="
echo ""
echo "当前版本:"
codex --version

echo ""
echo "使用方法:"
echo "  直接运行: codex"
echo ""
echo "首次运行需要登录:"
echo "  - 使用ChatGPT账号登录"
echo "  - 或配置OpenAI API密钥"
echo ""
echo "官方文档: https://developers.openai.com/codex/cli/"
echo "=========================================="
