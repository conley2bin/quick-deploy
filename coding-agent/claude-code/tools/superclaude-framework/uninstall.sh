#!/bin/bash
# ============================================================================
# SuperClaude Framework 卸载脚本
# ============================================================================
#
# 彻底删除 SuperClaude Framework 及其所有组件
#
# 卸载内容：
#   • superclaude 包（通过 pipx）
#   • 已安装的命令（~/.claude/commands/sc/）
#   • MCP 服务器配置
#   • 缓存和临时文件
#
# 文档：https://github.com/SuperClaude-Org/SuperClaude_Framework
# ============================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  SuperClaude Framework 卸载${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# 确认卸载
read -p "确定要卸载 SuperClaude Framework 吗？(y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}已取消卸载${NC}"
    exit 0
fi

echo ""

# 1. 卸载 superclaude 命令
if command -v superclaude &> /dev/null; then
    echo -e "${BLUE}正在卸载 superclaude 命令...${NC}"
    superclaude uninstall || true
    echo -e "${GREEN}✓ 命令已卸载${NC}"
else
    echo -e "${YELLOW}未检测到 superclaude 命令${NC}"
fi

echo ""

# 2. 使用 pipx 卸载包
if command -v pipx &> /dev/null; then
    echo -e "${BLUE}正在卸载 superclaude 包...${NC}"
    pipx uninstall superclaude || true
    echo -e "${GREEN}✓ 包已卸载${NC}"
else
    echo -e "${YELLOW}未检测到 pipx${NC}"
fi

echo ""

# 3. 删除命令目录
if [ -d "$HOME/.claude/commands/sc" ]; then
    echo -e "${BLUE}正在删除命令目录...${NC}"
    rm -rf "$HOME/.claude/commands/sc"
    echo -e "${GREEN}✓ 命令目录已删除${NC}"
else
    echo -e "${YELLOW}命令目录不存在${NC}"
fi

echo ""

# 4. 清理 MCP 配置（可选）
echo -e "${YELLOW}注意: MCP 服务器配置保留在 ~/.claude/config.json 中${NC}"
echo -e "${YELLOW}如需手动删除，请编辑该文件或使用 Claude Config Editor${NC}"

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${GREEN}  卸载完成!${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

echo -e "${BLUE}已删除内容:${NC}"
echo "  • superclaude 包"
echo "  • ~/.claude/commands/sc/ 目录"
echo "  • 所有 /sc: 命令"
echo ""

echo -e "${BLUE}保留内容:${NC}"
echo "  • ~/.claude/config.json (MCP 配置)"
echo "  • pipx (如需删除: pipx uninstall-all && pip uninstall pipx)"
echo ""
