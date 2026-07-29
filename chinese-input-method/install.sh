#!/bin/bash
# Fcitx5 + Chinese Addons 中文输入法安装配置脚本
# 仅支持 Ubuntu 24.04 及以上版本 (支持 X11 和 Wayland)

set -euo pipefail

CHECK_ONLY=false
FORCE_OVERWRITE=false

REQUIRED_PACKAGES=(
    fcitx5
    fcitx5-chinese-addons
    fcitx5-config-qt
    fcitx5-frontend-all
    fcitx5-frontend-gtk2
    fcitx5-module-lua
    im-config
)

SUDO_CMD="${SUDO_CMD:-sudo}"

section() {
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "$1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo
}

backup_file() {
    local file="$1"

    if [ -f "$file" ]; then
        cp "$file" "${file}.bak.$(date +%Y%m%d%H%M%S)"
        echo "已备份: $file"
    fi
}

usage() {
    cat << 'EOF'
用法:
  ./install.sh [--check] [--force] [--help]

选项:
  --check   只检查系统、软件源和当前配置，不安装、不修改
  --force   覆盖已有 ~/.config/fcitx5/profile；未指定时会在已有配置时退出
  --help    显示帮助

注意:
  不要用 sudo 运行本脚本。直接运行 ./install.sh 即可，脚本会在需要时调用 sudo。
EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check)
                CHECK_ONLY=true
                ;;
            --force)
                FORCE_OVERWRITE=true
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                echo "错误: 未知参数: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done
}

ensure_not_root() {
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        echo "错误: 请不要用 sudo 运行本脚本。"
        echo "请直接运行: ./install.sh"
    echo "脚本会在需要安装软件包时调用 ${SUDO_CMD}。"
        exit 1
    fi
}

check_supported_system() {
    if [ ! -r /etc/os-release ]; then
        echo "错误: 无法读取 /etc/os-release，无法确认系统版本。"
        exit 1
    fi

    # shellcheck disable=SC1091
    . /etc/os-release

    if [ "${ID:-}" != "ubuntu" ]; then
        echo "错误: 本脚本仅支持 Ubuntu 24.04 及以上版本。"
        echo "当前系统: ${PRETTY_NAME:-unknown}"
        exit 1
    fi

    if ! dpkg --compare-versions "${VERSION_ID:-0}" ge "24.04"; then
        echo "错误: 本脚本仅支持 Ubuntu 24.04 及以上版本。"
        echo "当前系统: ${PRETTY_NAME:-Ubuntu ${VERSION_ID:-unknown}}"
        exit 1
    fi

    echo "当前系统: ${PRETTY_NAME:-Ubuntu $VERSION_ID}"
}

check_package_candidates() {
    local package candidate
    local missing=()

    for package in "${REQUIRED_PACKAGES[@]}"; do
        # LC_ALL=C 不可省略: apt-cache policy 的输出是本地化的。
        # 中文 locale 下它输出 "候选：" 而不是 "Candidate:"，
        # 匹配 /Candidate:/ 会永远失配，把所有软件包误判为"没有候选版本"。
        candidate="$(LC_ALL=C apt-cache policy "$package" 2>/dev/null \
            | awk '/^ *Candidate:/ {print $2; exit}')"
        if [ -z "$candidate" ] || [ "$candidate" = "(none)" ]; then
            missing+=("$package")
        fi
    done

    if [ "${#missing[@]}" -gt 0 ]; then
        echo "错误: 以下软件包没有可安装候选版本:"
        printf '  - %s\n' "${missing[@]}"
        echo
        echo "请确认 Ubuntu 24.04 的 universe 软件源已启用，然后重新运行。"
        exit 1
    fi

    echo "Fcitx5 相关软件包可安装"
}

show_current_state() {
    echo "当前输入法框架状态:"
    im-config -m || true
    echo

    echo "当前 GNOME 输入源:"
    gsettings get org.gnome.desktop.input-sources sources 2>/dev/null || true
    echo

    echo "当前 Fcitx/Fcitx5 软件包状态:"
    dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package} ${Version}\n' 'fcitx*' 'fcitx5*' 2>/dev/null \
        | awk '$1 ~ /^(ii|rc)/ {print}' \
        || true
    echo
}

verify_installation() {
    local failed=false
    local gnome_sources

    section "安装后检查"

    for command in fcitx5 fcitx5-remote im-config; do
        if command -v "$command" >/dev/null 2>&1; then
            echo "OK: $command"
        else
            echo "错误: 找不到命令 $command"
            failed=true
        fi
    done

    if command -v fcitx5-configtool >/dev/null 2>&1; then
        echo "OK: fcitx5-configtool"
    elif command -v fcitx5-config-qt >/dev/null 2>&1; then
        echo "OK: fcitx5-config-qt"
    else
        echo "错误: 找不到 Fcitx5 配置工具命令"
        failed=true
    fi

    if [ -f "$HOME/.xinputrc" ] && grep -q '^run_im fcitx5$' "$HOME/.xinputrc"; then
        echo "OK: im-config 用户配置已设置为 fcitx5"
    else
        echo "警告: 未在 ~/.xinputrc 中确认 run_im fcitx5"
        im-config -m || true
    fi

    if [ -s "$HOME/.config/fcitx5/profile" ] && grep -q '^Name=pinyin$' "$HOME/.config/fcitx5/profile"; then
        echo "OK: Fcitx5 profile 已包含 pinyin"
    else
        echo "错误: Fcitx5 profile 未正确写入 pinyin"
        failed=true
    fi

    # GNOME 的输入源里若仍有 IBus 引擎，重新登录后会与 Fcitx5 抢占
    # Ctrl+Space，表现为"装了但切不出中文"。这里只提示，不自动修改
    # 用户现有输入法配置。
    gnome_sources="$(gsettings get org.gnome.desktop.input-sources sources 2>/dev/null || true)"
    if printf '%s' "$gnome_sources" | grep -q "'ibus'"; then
        echo
        echo "警告: GNOME 输入源仍包含 IBus 引擎: $gnome_sources"
        echo "      它会和 Fcitx5 争抢快捷键。确认要把中文输入交给 Fcitx5 时，可执行:"
        echo "        gsettings set org.gnome.desktop.input-sources sources \"[('xkb', 'us')]\""
    fi

    if [ "$failed" = true ]; then
        echo
        echo "安装后检查发现错误，请先处理以上问题。"
        exit 1
    fi
}

parse_args "$@"
ensure_not_root

echo "========================================"
echo "  Fcitx5 中文输入法安装"
echo "========================================"
echo
echo "将安装 fcitx5-chinese-addons 拼音输入法"
echo "仅支持 Ubuntu 24.04 及以上版本"
echo "支持 X11 和 Wayland，可选导入中文维基百科词库"
echo

check_supported_system

# 检测当前显示服务器
CURRENT_SESSION="${XDG_SESSION_TYPE:-unknown}"
echo "当前显示服务器: $CURRENT_SESSION"
echo

section "预检查"

check_package_candidates
show_current_state

if [ "$CHECK_ONLY" = true ]; then
    echo "检查完成。--check 模式不会安装或修改任何内容。"
    exit 0
fi

if [ "$SUDO_CMD" = "sudo" ]; then
    echo "验证 sudo 权限..."
    sudo -v
    echo "sudo 权限验证通过"
else
    echo "使用提权命令: $SUDO_CMD"
fi
echo

# ============================================
# 步骤 1: 删除冲突的 Fcitx4
# ============================================
# 说明: 只删除与 fcitx5 冲突的 fcitx4。
#       保留 GNOME 依赖的 IBus 核心包，不在此处自动清理其它输入法。
# ============================================
section "[1/5] 检查冲突的 Fcitx4"

# 查询已安装的 fcitx4 包。只匹配包名，不扫描述文本。
mapfile -t FCITX4_PACKAGES < <(
    dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package}\n' 'fcitx*' 2>/dev/null \
        | awk '$1 ~ /^ii/ && $2 ~ /^fcitx(:|$|-)/ && $2 !~ /^fcitx5(:|$|-)/ {print $2}' \
        || true
)

if [ "${#FCITX4_PACKAGES[@]}" -gt 0 ]; then
    echo "检测到 Fcitx4 (与 Fcitx5 冲突):"
    printf '  - %s\n' "${FCITX4_PACKAGES[@]}"
    echo
    echo "正在自动删除 Fcitx4..."
    "$SUDO_CMD" apt purge -y "${FCITX4_PACKAGES[@]}"
    echo "Fcitx4 已删除"
else
    echo "未检测到已安装的 Fcitx4 包"
fi

if [ -d "$HOME/.config/fcitx" ]; then
    rm -rf "$HOME/.config/fcitx"
    echo "已删除旧 Fcitx4 用户配置: $HOME/.config/fcitx"
fi

echo

# ============================================
# 步骤 2: 安装 Fcitx5 和中文输入法
# ============================================
# 说明: 安装 fcitx5 框架和 chinese-addons。
#       chinese-addons 包含拼音、双拼、五笔等输入法。
#       frontend-all 覆盖 GTK3/GTK4/Qt5/Qt6，GTK2 为老应用单独保留。
# ============================================
section "[2/5] 安装 Fcitx5 和中文输入法"

"$SUDO_CMD" apt update
"$SUDO_CMD" apt install -y "${REQUIRED_PACKAGES[@]}"

echo "软件包安装完成"
echo

# ============================================
# 步骤 3: 配置输入法框架
# ============================================
# 说明: 使用 im-config 将 fcitx5 设置为用户默认输入法框架。
#       im-config 会在下次登录时设置环境变量并启动 fcitx5。
# ============================================
section "[3/5] 配置输入法框架"

im-config -n fcitx5
echo "已设置 Fcitx5 为默认输入法框架"
echo

# ============================================
# 步骤 4: 清理旧版脚本的手写启动配置
# ============================================
# 说明: Ubuntu 的 im-config 已经会设置环境变量并启动 fcitx5。
#       这里仅移除旧版脚本可能写入的重复配置。
# ============================================
section "[4/5] 清理旧版脚本重复配置"

if [ -f "$HOME/.profile" ] && grep -q "^# Fcitx5 输入法环境变量$" "$HOME/.profile"; then
    backup_file "$HOME/.profile"
    sed -i '/^# Fcitx5 输入法环境变量$/,/^export SDL_IM_MODULE=fcitx$/d' "$HOME/.profile"
    echo "已移除 ~/.profile 中旧版脚本写入的 Fcitx5 环境变量块"
else
    echo "未发现旧版 ~/.profile 环境变量块"
fi

OLD_AUTOSTART="$HOME/.config/autostart/fcitx5.desktop"
if [ -f "$OLD_AUTOSTART" ] && grep -q "^Exec=fcitx5$" "$OLD_AUTOSTART"; then
    rm -f "$OLD_AUTOSTART"
    echo "已删除旧版脚本创建的重复 autostart: $OLD_AUTOSTART"
else
    echo "未发现旧版 fcitx5 autostart 文件"
fi

echo

# ============================================
# 步骤 5: 配置输入法列表
# ============================================
# 说明: 创建 fcitx5 配置文件，添加拼音输入法。
#       配置文件: ~/.config/fcitx5/profile
#       包含: 键盘 (US) + 拼音输入法。
# ============================================
section "[5/5] 配置输入法列表"

FCITX5_CONFIG_DIR="$HOME/.config/fcitx5"
FCITX5_PROFILE="$FCITX5_CONFIG_DIR/profile"

mkdir -p "$FCITX5_CONFIG_DIR"

if [ -f "$FCITX5_PROFILE" ] && [ "$FORCE_OVERWRITE" != true ]; then
    echo "错误: 已存在 Fcitx5 配置文件: $FCITX5_PROFILE"
    echo "为避免覆盖现有输入法设置，脚本已停止。"
    echo "确认要覆盖时请重新运行: ./install.sh --force"
    exit 1
fi

backup_file "$FCITX5_PROFILE"

cat > "$FCITX5_PROFILE" << 'EOF'
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=pinyin

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=pinyin
Layout=

[GroupOrder]
0=Default
EOF

echo "已添加拼音输入法到配置"
echo

verify_installation

# ============================================
# 完成提示
# ============================================
echo "========================================"
echo "  安装完成"
echo "========================================"
echo
echo "重要: 注销并重新登录以使环境变量生效"
echo "     手动操作或者使用命令: gnome-session-quit --logout --no-prompt"
echo
echo "首次配置 (必须):"
echo "  1. 运行: fcitx5-configtool (若该命令不存在，运行: fcitx5-config-qt)"
echo "  2. 在 Input Method 标签页中:"
echo "     - 右侧 Available Input Method 找到 'Pinyin'"
echo "     - 双击 'Pinyin' 添加拼音输入法"
echo "     - 添加 'Keyboard - English (US)' 英文键盘"
echo "     - 调整顺序 (推荐):"
echo "       * Pinyin 在第一位 (默认中文输入)"
echo "       * English 在第二位"
echo "     - 点击 'Apply' 保存"
echo "  3. 删除多余的 Group (如果有 Group 2):"
echo "     - 在 Group 下拉菜单中选择要删除的 Group"
echo "     - 点击下拉菜单右侧的 '-' 按钮"
echo "     - 点击 'Apply' 保存"
echo "  4. fcitx5 默认快捷键:"
echo "     - Ctrl+Space: 按列表顺序循环切换输入法"
echo "     - Left Shift: 临时切换到第一个输入法"
echo
echo "导入词库 (可选):"
echo "  运行: ./chinese-input-method/import-dict.sh"
echo
echo "清理提示:"
echo "  本脚本不会自动执行 apt autoremove。确认系统无异常后，可手动运行:"
echo "  sudo apt autoremove"
echo
