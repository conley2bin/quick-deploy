#!/bin/bash
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== 安装 zsh 与 oh-my-zsh ===${NC}\n"

# 1. 更新包列表
echo -e "${YELLOW}[1/9] 更新包列表...${NC}"
sudo apt update

# 2. 安装 zsh
echo -e "\n${YELLOW}[2/9] 安装 zsh...${NC}"
sudo apt install -y zsh

# 验证安装
ZSH_VERSION=$(zsh --version)
echo -e "${GREEN}✓ Zsh 安装完成: $ZSH_VERSION${NC}"

# 3. 安装 oh-my-zsh
echo -e "\n${YELLOW}[3/9] 安装 oh-my-zsh...${NC}"
if [ -d "$HOME/.oh-my-zsh" ]; then
    echo -e "${YELLOW}oh-my-zsh 已存在，跳过安装${NC}"
else
    # 优先使用官方链接，失败则降级到镜像
    OFFICIAL_URL="https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh"
    MIRROR_URL="https://gitee.com/pocmon/ohmyzsh/raw/master/tools/install.sh"

    echo -e "尝试从官方源下载..."
    if curl -fsSL --connect-timeout 5 "$OFFICIAL_URL" -o /tmp/ohmyzsh-install.sh 2>/dev/null; then
        echo -e "${GREEN}✓ 使用官方源${NC}"
        RUNZSH=no CHSH=no sh /tmp/ohmyzsh-install.sh
    else
        echo -e "${YELLOW}官方源连接失败，使用国内镜像...${NC}"
        RUNZSH=no CHSH=no sh -c "$(curl -fsSL $MIRROR_URL)"
    fi

    rm -f /tmp/ohmyzsh-install.sh
    echo -e "${GREEN}✓ oh-my-zsh 安装完成${NC}"
fi

# 4. 安装 zsh-autosuggestions 插件
echo -e "\n${YELLOW}[4/9] 安装 zsh-autosuggestions 插件...${NC}"
PLUGIN_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-autosuggestions"
if [ -d "$PLUGIN_DIR" ]; then
    echo -e "${YELLOW}插件已存在，跳过安装${NC}"
else
    git clone https://github.com/zsh-users/zsh-autosuggestions "$PLUGIN_DIR"
    echo -e "${GREEN}✓ zsh-autosuggestions 安装完成${NC}"
fi

# 5. 安装 zsh-syntax-highlighting 插件
echo -e "\n${YELLOW}[5/9] 安装 zsh-syntax-highlighting 插件...${NC}"
PLUGIN_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting"
if [ -d "$PLUGIN_DIR" ]; then
    echo -e "${YELLOW}插件已存在，跳过安装${NC}"
else
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git "$PLUGIN_DIR"
    echo -e "${GREEN}✓ zsh-syntax-highlighting 安装完成${NC}"
fi

# 6-8. 配置 .zshrc
echo -e "\n${YELLOW}[6/9] 配置 .zshrc 文件...${NC}"
ZSHRC="$HOME/.zshrc"

if [ -f "$ZSHRC" ]; then
    # 备份原配置
    cp "$ZSHRC" "$ZSHRC.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${GREEN}✓ 已备份原配置文件${NC}"

    # 7. 修改主题
    echo -e "${YELLOW}[7/9] 设置主题为 af-magic...${NC}"
    sed -i 's/^ZSH_THEME=.*/ZSH_THEME="af-magic"/' "$ZSHRC"

    # 8. 修改插件配置
    echo -e "${YELLOW}[8/9] 配置插件...${NC}"
    sed -i 's/^plugins=.*/plugins=(git zsh-autosuggestions zsh-syntax-highlighting extract web-search)/' "$ZSHRC"

    # 9. 添加键绑定配置
    if ! grep -q "bindkey '\^U' backward-kill-line" "$ZSHRC"; then
        echo -e "${YELLOW}添加键绑定配置...${NC}"
        cat >> "$ZSHRC" << 'EOF'

# >>> zsh默认 ctrl+u删除整行，现在绑定成删除到行首 >>>
bindkey '^U' backward-kill-line
bindkey '^K' kill-line
# <<< zsh 默认 Ctrl+K 就是删除到行尾，但可能被插件覆盖，现在显式绑定成删除到行尾 <<<
EOF
        echo -e "${GREEN}✓ 已绑定 Ctrl+U → 删除到行首, Ctrl+K → 删除到行尾${NC}"
    else
        echo -e "${YELLOW}键绑定配置已存在，跳过${NC}"
    fi

    echo -e "${GREEN}✓ .zshrc 配置完成${NC}"
else
    echo -e "${RED}错误: .zshrc 文件不存在${NC}"
    exit 1
fi

# 10. 设置 zsh 为默认 shell
echo -e "\n${YELLOW}[9/9] 设置 zsh 为默认 shell...${NC}"
CURRENT_SHELL="$SHELL"
ZSH_PATH=$(which zsh)

if [ "$CURRENT_SHELL" != "$ZSH_PATH" ]; then
    chsh -s "$ZSH_PATH"
    echo -e "${GREEN}✓ 默认 shell 已设置为 zsh${NC}"
else
    echo -e "${YELLOW}zsh 已是默认 shell${NC}"
fi

# 显示系统信息
echo -e "\n${GREEN}=== 安装完成 ===${NC}"
echo -e "\n系统信息："
echo -e "  可用 shells: $(cat /etc/shells | grep -v '^#' | tr '\n' ' ')"
echo -e "  当前 shell: $SHELL"
echo -e "  zsh 路径: $ZSH_PATH"

# 已启用的插件说明
echo -e "\n${GREEN}已启用插件：${NC}"
echo -e "  • ${YELLOW}git${NC}: git 命令别名和提示"
echo -e "  • ${YELLOW}zsh-autosuggestions${NC}: 命令自动建议（按 → 接受）"
echo -e "  • ${YELLOW}zsh-syntax-highlighting${NC}: 语法高亮"
echo -e "  • ${YELLOW}extract${NC}: 通用解压命令"
echo -e "      用法: ${GREEN}x <压缩文件>${NC}"
echo -e "      支持: .tar.gz, .zip, .rar, .7z 等所有常见格式"
echo -e "  • ${YELLOW}web-search${NC}: 命令行搜索"
echo -e "      用法: ${GREEN}google <关键词>${NC} 或 ${GREEN}bing <关键词>${NC}"
echo -e "      自动打开浏览器搜索"

# 提示重新登录
echo -e "\n${YELLOW}重要提示：${NC}"
echo -e "  1. 请${RED}注销并重新登录${NC}以使默认 shell 更改生效"
echo -e "  2. 无需重启电脑，关闭所有终端窗口并重新打开即可"
echo -e "  3. 在 VSCode 中使用 ${YELLOW}Ctrl+Shift+P${NC} → ${YELLOW}Terminal: Select Default Profile${NC} 选择 zsh"
echo -e "\n  配置文件备份: $ZSHRC.backup.*"
