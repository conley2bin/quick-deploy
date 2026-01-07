#!/bin/bash
# ============================================================================
# Claude Code - Official CLI Installation Script
# ============================================================================
#
# Purpose: Install Anthropic's official Claude Code CLI
# Method: Homebrew (recommended for best performance)
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

# 检查 Homebrew
echo -e "${YELLOW}[1/3] 检查 Homebrew...${NC}"
if ! command -v brew &> /dev/null; then
    echo -e "${YELLOW}未检测到 Homebrew，正在自动安装...${NC}"
    echo ""

    # 自动安装 Homebrew
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Linux 需要额外配置 PATH
    if [[ "$OS" == "Linux" ]]; then
        echo ""
        echo -e "${YELLOW}配置 Homebrew 环境变量...${NC}"

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
    fi

    echo ""
    echo -e "${GREEN}✓ Homebrew 安装完成${NC}"
fi

BREW_VERSION=$(brew --version | head -n 1)
echo -e "${GREEN}✓ Homebrew 已安装: $BREW_VERSION${NC}"
echo ""

# 检查是否已安装 Claude Code
if command -v claude &> /dev/null; then
    CURRENT_VERSION=$(claude --version 2>/dev/null || echo "未知版本")
    echo -e "${YELLOW}检测到已安装 Claude Code: $CURRENT_VERSION${NC}"
    echo ""
    echo -n "是否重新安装/更新？[y/N]: "
    read -r reinstall
    if [[ ! "$reinstall" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}跳过安装${NC}"
        exit 0
    fi
    echo ""
fi

# 安装 Claude Code
echo -e "${YELLOW}[2/3] 安装 Claude Code...${NC}"
echo ""

brew install --cask claude-code

echo ""
echo -e "${GREEN}✓ Claude Code 安装完成${NC}"
echo ""

# 验证安装
echo -e "${YELLOW}[3/3] 验证安装...${NC}"
if command -v claude &> /dev/null; then
    INSTALLED_VERSION=$(claude --version)
    echo -e "${GREEN}✓ 安装成功: $INSTALLED_VERSION${NC}"

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
echo -e "${GREEN}  安装完成！${NC}"
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
