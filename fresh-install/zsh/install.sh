#!/bin/bash
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== 安装 zsh 与 oh-my-zsh ===${NC}\n"

# 等后台 apt 活动结束（新装系统首开机自动更新常见持锁），否则下面 apt update 会撞锁失败。
# 脚本被单独拷出、助手缺失时定义空操作跳过等锁
APT_LOCK_WAIT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/apt-lock-wait.sh"
if [ -f "$APT_LOCK_WAIT_LIB" ]; then . "$APT_LOCK_WAIT_LIB"; else wait_for_apt_lock() { return 0; }; fi
wait_for_apt_lock || { echo -e "${RED}✗ 等待 apt 锁超时，请稍后重跑${NC}" >&2; exit 1; }

# 1. 更新包列表
echo -e "${YELLOW}[1/10] 更新包列表...${NC}"
sudo apt update

# 2. 安装 zsh
echo -e "\n${YELLOW}[2/10] 安装 zsh...${NC}"
sudo apt install -y zsh

# 验证安装
ZSH_VERSION=$(zsh --version)
echo -e "${GREEN}✓ Zsh 安装完成: $ZSH_VERSION${NC}"

# 3. 安装 oh-my-zsh
echo -e "\n${YELLOW}[3/10] 安装 oh-my-zsh...${NC}"
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
echo -e "\n${YELLOW}[4/10] 安装 zsh-autosuggestions 插件...${NC}"
PLUGIN_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-autosuggestions"
if [ -d "$PLUGIN_DIR" ]; then
    echo -e "${YELLOW}插件已存在，跳过安装${NC}"
else
    git clone https://github.com/zsh-users/zsh-autosuggestions "$PLUGIN_DIR"
    echo -e "${GREEN}✓ zsh-autosuggestions 安装完成${NC}"
fi

# 5. 安装 zsh-syntax-highlighting 插件
echo -e "\n${YELLOW}[5/10] 安装 zsh-syntax-highlighting 插件...${NC}"
PLUGIN_DIR="${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting"
if [ -d "$PLUGIN_DIR" ]; then
    echo -e "${YELLOW}插件已存在，跳过安装${NC}"
else
    git clone https://github.com/zsh-users/zsh-syntax-highlighting.git "$PLUGIN_DIR"
    echo -e "${GREEN}✓ zsh-syntax-highlighting 安装完成${NC}"
fi

# 6-8. 配置 .zshrc
echo -e "\n${YELLOW}[6/10] 配置 .zshrc 文件...${NC}"
ZSHRC="$HOME/.zshrc"

if [ -f "$ZSHRC" ]; then
    # 备份原配置
    cp "$ZSHRC" "$ZSHRC.backup.$(date +%Y%m%d_%H%M%S)"
    echo -e "${GREEN}✓ 已备份原配置文件${NC}"

    # 7. 修改主题
    echo -e "${YELLOW}[7/10] 设置主题为 af-magic...${NC}"
    sed -i 's/^ZSH_THEME=.*/ZSH_THEME="af-magic"/' "$ZSHRC"

    # 8. 修改插件配置
    echo -e "${YELLOW}[8/10] 配置插件...${NC}"
    sed -i 's/^plugins=.*/plugins=(git zsh-autosuggestions zsh-syntax-highlighting extract web-search)/' "$ZSHRC"

    # 顺带添加键绑定配置
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

# 9. 设置 zsh 为默认 shell
echo -e "\n${YELLOW}[9/10] 设置 zsh 为默认 shell...${NC}"
# 读 /etc/passwd 里的持久状态（而不是 $SHELL 环境变量），
# 这样 chsh 之后、重新登录之前的重跑也能正确识别“已是 zsh”
CURRENT_SHELL=$(getent passwd "$USER" | cut -d: -f7)
ZSH_PATH=$(which zsh)

if [ "$CURRENT_SHELL" != "$ZSH_PATH" ]; then
    # sudo 下 chsh 不会再询问密码（PAM rootok 短路），消除交互停顿；
    # 但 root 会绕过 /etc/shells 校验，先手动等价校验一次
    grep -qxF "$ZSH_PATH" /etc/shells || { echo -e "${RED}错误: $ZSH_PATH 不在 /etc/shells 中${NC}"; exit 1; }
    sudo chsh -s "$ZSH_PATH" "$USER"
    echo -e "${GREEN}✓ 默认 shell 已设置为 zsh${NC}"
else
    echo -e "${YELLOW}zsh 已是默认 shell${NC}"
fi

# 10. 配置 VSCode 默认终端为 zsh（等价于手动 Ctrl+Shift+P → Terminal: Select Default Profile）
# 机制：该选择的持久化形式就是 settings.json 里的 terminal.integrated.defaultProfile.linux 字段
echo -e "\n${YELLOW}[10/10] 配置 VSCode 默认终端...${NC}"
VSCODE_SETTINGS="$HOME/.config/Code/User/settings.json"

if ! command -v code &> /dev/null && [ ! -d "$HOME/.config/Code" ]; then
    echo -e "${YELLOW}未检测到 VSCode，跳过（以后安装 VSCode 后重跑本脚本即可自动配置）${NC}"
    VSCODE_SUMMARY="未配置（未检测到 VSCode，补装后重跑本脚本即可）"
else
    mkdir -p "$(dirname "$VSCODE_SETTINGS")"
    VSCODE_RC=0
    VSCODE_RESULT=$(python3 - "$VSCODE_SETTINGS" << 'PYEOF'
import json, os, sys, tempfile

path = sys.argv[1]
key = "terminal.integrated.defaultProfile.linux"

if os.path.islink(path):
    # stow/chezmoi 类工具管理的符号链接：os.replace 会把链接本身换成普通文件，
    # 静默切断同步——拒绝硬改
    print("PARSE_ERROR")
    sys.exit(2)

settings = {}
if os.path.exists(path) and os.path.getsize(path) > 0:
    try:
        with open(path, encoding="utf-8") as f:
            settings = json.load(f)
    except json.JSONDecodeError:
        # settings.json 允许注释和尾逗号（JSONC），严格解析失败说明用户手改过。
        # 不猜测、不硬改——搞坏用户的 settings 比没配上 terminal 严重得多
        print("PARSE_ERROR")
        sys.exit(2)

if not isinstance(settings, dict):
    print("PARSE_ERROR")
    sys.exit(2)

if settings.get(key) == "zsh":
    print("ALREADY_SET")
    sys.exit(0)

settings[key] = "zsh"
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
try:
    os.fchmod(fd, 0o644)  # mkstemp 默认权限 600，settings.json 惯例是 644
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)  # 同目录 rename，原子替换
except OSError:
    os.unlink(tmp)
    raise
print("CONFIGURED")
PYEOF
) || VSCODE_RC=$?

    case "$VSCODE_RESULT" in
        CONFIGURED)
            echo -e "${GREEN}✓ VSCode 默认终端已设置为 zsh${NC}"
            VSCODE_SUMMARY="已自动配置为 zsh" ;;
        ALREADY_SET)
            echo -e "${YELLOW}VSCode 默认终端已是 zsh，跳过${NC}"
            VSCODE_SUMMARY="已是 zsh（无需改动）" ;;
        PARSE_ERROR)
            echo -e "${YELLOW}settings.json 含注释或是符号链接，未自动修改（为避免破坏你的配置）${NC}"
            echo -e "  手动设置: Ctrl+Shift+P → Terminal: Select Default Profile → zsh"
            VSCODE_SUMMARY="未自动配置，需手动设置（见上方提示）" ;;
        *)
            echo -e "${YELLOW}VSCode 配置失败 (退出码 $VSCODE_RC)，不影响 zsh 本身${NC}"
            echo -e "  手动设置: Ctrl+Shift+P → Terminal: Select Default Profile → zsh"
            VSCODE_SUMMARY="未自动配置，需手动设置（见上方提示）" ;;
    esac
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
echo -e "  3. VSCode 默认终端: ${VSCODE_SUMMARY}"
echo -e "\n  配置文件备份: $ZSHRC.backup.*"
