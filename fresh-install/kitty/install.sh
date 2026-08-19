#!/bin/bash
# kitty 终端安装配置脚本（官方最新版）
# 仅支持 Ubuntu 24.04 及以上版本（支持 X11 和 Wayland）
#
# 与同目录其它模块一致的约定：
#   - 幂等：重跑 = 重装最新版 + 配置重置为基准内容
#   - 旧配置文件先备份为 <文件名>.bak.<时间戳>，回退靠它，不靠拒绝执行
#   - --check 只读预检：检查系统、现状，不安装、不修改
#   - 写后回读校验，不信任"写入动作完成"本身
#
# 为什么不用 apt 装 kitty：apt 里的 0.32.x（2023 年）太旧，
# 官方 installer 装最新版到 ~/.local/kitty.app，不需要 sudo。
# 安装不上（多为无代理访问 GitHub 失败）就明确警告并退出，不降级装旧版。

set -euo pipefail

CHECK_ONLY=false
# 默认接管 Ctrl+Alt+T（单用户机器直接默认开启，--no-default-terminal 可关）
DEFAULT_TERMINAL=true

SUDO_CMD="${SUDO_CMD:-sudo}"

KITTY_APP_DIR="$HOME/.local/kitty.app"
KITTY_BIN_DIR="$KITTY_APP_DIR/bin"
LOCAL_BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/kitty"
CONFIG_FILE="$CONFIG_DIR/kitty.conf"
THEME_FILE="$CONFIG_DIR/theme.conf"
DESKTOP_SRC_DIR="$KITTY_APP_DIR/share/applications"
DESKTOP_DST_DIR="$HOME/.local/share/applications"
XDG_TERMINALS_FILE="$HOME/.config/xdg-terminals.list"
INSTALLER_URL="https://sw.kovidgoyal.net/kitty/installer.sh"

# 字体目标值：主字体与 CJK 回退字体都只在系统里真实存在时才写入配置，
# 缺失时跳过该行并提示安装命令，而不是写一个 kitty 找不到的字体名
# （kitty 找不到字体时会静默回退，用户看到的中文可能不是预期渲染）。
FONT_FAMILY="JetBrains Mono"
CJK_FONT_FAMILY="Noto Sans CJK SC"
FONT_SIZE="12.0"
CJK_RANGE="U+4E00-U+9FFF"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

section() {
    echo
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
  ./install.sh [--check] [--no-default-terminal] [--help]

选项:
  --check                 只检查系统与当前状态，不安装、不修改
  --no-default-terminal   不接管 Ctrl+Alt+T（默认会接管为 kitty）
  --help                  显示帮助

注意:
  不要用 sudo 运行本脚本。直接运行 ./install.sh 即可。
  kitty 本体安装到用户目录，不需要 sudo；仅当系统缺失 CJK 字体
  （fonts-noto-cjk）时会调用 sudo apt 安装。

  本脚本是幂等的重置工具：重跑会重装最新版 kitty，
  并把 ~/.config/kitty/kitty.conf 重置为基准内容（旧文件先备份）。
EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check)
                CHECK_ONLY=true
                ;;
            --no-default-terminal)
                DEFAULT_TERMINAL=false
                ;;
            --default-terminal)
                # 兼容旧命令行，默认已是开启状态
                DEFAULT_TERMINAL=true
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
    echo "显示服务器: ${XDG_SESSION_TYPE:-unknown（可能为 SSH 会话）}"
    echo
}

font_exists() {
    # fc-list 的输出是「路径: 字族名列表:style=...」，「: family」查询
    # 一行一个 family 名，配合 grep -Fxi 做精确匹配。
    # 不能拿整行 grep 字族名子串：一个字族名可能是另一个的前缀，
    # 例如「Noto Sans CJK SC」存在时，「Noto Sans」也会匹配到。
    fc-list : family 2>/dev/null | grep -Fxi "$1" >/dev/null 2>&1
}

# ============================================
# 确保 CJK 字体可用（缺失时 apt 安装）
# ============================================
# 中文渲染是基准配置的硬前提：symbol_map 指向不存在的字体会让
# kitty 静默回退，用户看到的中文不是预期渲染。字体缺失时用 apt
# 安装（fresh-install 的 tsinghua-mirror 模块已换好源，国内可用）。
# apt 失败不终止整个脚本：降级为"不写 symbol_map + 提示"，
# 字体不是 kitty 能用与否的硬条件，不值得拖垮安装。
ensure_cjk_font() {
    if font_exists "$CJK_FONT_FAMILY"; then
        return 0
    fi

    if [ "$CHECK_ONLY" = true ]; then
        return 0
    fi

    echo "$CJK_FONT_FAMILY 未安装，尝试通过 apt 安装 fonts-noto-cjk..."
    if "$SUDO_CMD" apt-get update -qq && "$SUDO_CMD" apt-get install -y fonts-noto-cjk; then
        if font_exists "$CJK_FONT_FAMILY"; then
            echo "OK: $CJK_FONT_FAMILY 已安装"
            return 0
        fi
    fi

    echo -e "${YELLOW}警告: $CJK_FONT_FAMILY 安装失败或未生效。${NC}"
    echo -e "${YELLOW}  中文将走 fontconfig 自动回退，配置中不写 symbol_map。${NC}"
    echo -e "${YELLOW}  可稍后手动执行: sudo apt install fonts-noto-cjk，然后重跑本脚本。${NC}"
}

show_current_state() {
    local kitty_version

    echo "当前 kitty 安装状态:"
    if [ -x "$KITTY_BIN_DIR/kitty" ]; then
        kitty_version="$("$KITTY_BIN_DIR/kitty" --version 2>/dev/null || true)"
        echo "  ~/.local/kitty.app: ${kitty_version:-已安装（版本读取失败）}"
    else
        echo "  ~/.local/kitty.app: 未安装"
    fi

    if command -v kitty >/dev/null 2>&1; then
        echo "  PATH 中的 kitty: $(command -v kitty) ($(kitty --version 2>/dev/null))"
    else
        echo "  PATH 中的 kitty: 未找到"
    fi

    # apt 包是否共存：共存无害（不同目录），但显示出来方便用户识别
    # "kitty 到底来自哪里"这类疑惑。
    if dpkg-query -W -f='${Status} ${Version}' kitty 2>/dev/null | grep -q '^install ok'; then
        echo "  apt 包: 已安装 $(dpkg-query -W -f='${Version}' kitty 2>/dev/null)（与官方版共存）"
    else
        echo "  apt 包: 未安装"
    fi
    echo

    echo "当前配置文件:"
    if [ -f "$CONFIG_FILE" ]; then
        echo "  $CONFIG_FILE 已存在"
    else
        echo "  $CONFIG_FILE 不存在（将写入基准配置）"
    fi
    if [ -f "$XDG_TERMINALS_FILE" ]; then
        echo "  默认终端: $(tr '\n' ' ' < "$XDG_TERMINALS_FILE")"
    else
        echo "  默认终端: 未设置 kitty（xdg-terminals.list 不存在）"
    fi
    if [ -f "$THEME_FILE" ]; then
        echo "  主题文件: $THEME_FILE $(grep -m1 '^background' "$THEME_FILE" | awk '{print "(背景 " $2 ")"}')"
    else
        echo "  主题文件: 不存在（首次安装将写入 Catppuccin Frappé）"
    fi
    if [ -x "$LOCAL_BIN_DIR/x-terminal-emulator" ]; then
        echo "  Ctrl+Alt+T 接管: $LOCAL_BIN_DIR/x-terminal-emulator -> $KITTY_BIN_DIR/kitty"
    else
        echo "  Ctrl+Alt+T 接管: 未设置（x-terminal-emulator 包装脚本不存在）"
    fi
    echo

    echo "字体检查:"
    if font_exists "$FONT_FAMILY"; then
        echo "  $FONT_FAMILY: 已安装"
    else
        echo "  $FONT_FAMILY: 未安装（将使用 kitty 默认 monospace 字体）"
        echo "    提示: sudo apt install fonts-jetbrains-mono"
    fi
    if font_exists "$CJK_FONT_FAMILY"; then
        echo "  $CJK_FONT_FAMILY: 已安装"
    else
        echo "  $CJK_FONT_FAMILY: 未安装（中文将走 fontconfig 自动回退）"
        echo "    提示: sudo apt install fonts-noto-cjk"
    fi
    echo
}

# ============================================
# 步骤 1: 下载并执行官方安装脚本
# ============================================
# 为什么不直接 curl ... | sh：管道下载中断时 sh 可能执行到半个脚本；
# 且安装过程若意外进入交互提示，管道里无法注入回答，可能挂住。
# 先落盘到临时文件，用 stdin=/dev/null 执行——脚本从文件读，
# 任何意外提示读到 EOF 会立即得到空回答，不会无限等待。
install_kitty() {
    local installer_tmp rc

    installer_tmp="$(mktemp /tmp/kitty-installer.XXXXXX.sh)"

    if ! curl -fsSL --connect-timeout 15 "$INSTALLER_URL" -o "$installer_tmp"; then
        rm -f "$installer_tmp"
        echo -e "${RED}✗ 下载安装脚本失败: $INSTALLER_URL${NC}" >&2
        echo -e "${YELLOW}  kitty 官方二进制托管在 GitHub，未配置代理时下载可能失败。${NC}" >&2
        echo -e "${YELLOW}  配好代理后重跑本脚本即可。本脚本不降级安装 apt 旧版。${NC}" >&2
        return 1
    fi

    # 安装脚本从 GitHub 下载二进制包。用 if 接住退出码再分支——set -e
    # 下裸调用失败会直接终止脚本，后面的失败提示根本不会执行。
    if ! bash "$installer_tmp" < /dev/null; then
        rc=$?
        rm -f "$installer_tmp"
        echo -e "${RED}✗ kitty 安装脚本执行失败（退出码 $rc）${NC}" >&2
        echo -e "${YELLOW}  多为下载 GitHub 二进制包失败，配好代理后重跑本脚本即可。${NC}" >&2
        return 1
    fi
    rm -f "$installer_tmp"
}

# ============================================
# 步骤 2: PATH 集成
# ============================================
# ln -sf 幂等：目标存在时静默替换，不存在时创建。
# 只做链接不改 shell 配置：fresh-install 的 zsh 模块已保证
# ~/.local/bin 在 PATH 里；单独跑本脚本时若不在 PATH，只警告。
integrate_path() {
    local wrapper="$LOCAL_BIN_DIR/kitty" tmp

    mkdir -p "$LOCAL_BIN_DIR"

    # kitty 入口必须是包装脚本而不是符号链接：X11 下 fcitx5 中文输入
    # 依赖 GLFW_IM_MODULE=ibus，而 GLFW 在进程初始化时读取该变量，
    # 只能来自 exec 时的真实环境。实测 kitty.conf 的 env 指令晚于
    # GLFW 初始化，不生效；从外部 shell/桌面/快捷键启动又各有各的
    # 环境。所有启动路径（shell、桌面图标、Ctrl+Alt+T 包装脚本）
    # 最终都走 $LOCAL_BIN_DIR/kitty，在这里 export 一处覆盖全部。
    tmp="$(mktemp "$wrapper.tmp.XXXXXX")"
    cat > "$tmp" << EOF
#!/bin/bash
# 由 fresh-install/kitty/install.sh 生成：设置 GLFW 输入法桥后启动 kitty
export GLFW_IM_MODULE=ibus
exec $KITTY_BIN_DIR/kitty "\$@"
EOF
    chmod 755 "$tmp"
    if [ -f "$wrapper" ] && cmp -s "$tmp" "$wrapper"; then
        rm -f "$tmp"
        echo "包装脚本无变化，跳过: $wrapper"
    else
        backup_file "$wrapper"
        mv -f "$tmp" "$wrapper"
        echo "已写入: $wrapper"
    fi

    ln -sf "$KITTY_BIN_DIR/kitten" "$LOCAL_BIN_DIR/kitten"
    echo "已创建链接: $LOCAL_BIN_DIR/kitten"

    # 用当前 PATH 判断。若用户在别的 shell 里跑过本脚本而当前 shell
    # 不是 zsh 配置过的环境，这里的结论可能比实际保守，只警告不误导。
    case ":$PATH:" in
        *":$LOCAL_BIN_DIR:"*) ;;
        *)
            echo -e "${YELLOW}警告: $LOCAL_BIN_DIR 不在当前 PATH 中。${NC}"
            echo -e "${YELLOW}  fresh-install 的 zsh 模块会配置它；单独使用本脚本时，${NC}"
            echo -e "${YELLOW}  请确认 shell 配置里包含 ~/.local/bin。${NC}"
            ;;
    esac
}

# ============================================
# 步骤 3: 写 kitty.conf
# ============================================
# 无条件重写（先备份）：与 fcitx5 模块相同的"幂等重置"语义。
# 字体行按存在性条件写入，缺失时跳过并已在预检里提示安装命令。
write_kitty_config() {
    local tmp

    mkdir -p "$CONFIG_DIR"

    # 主题文件只在不存在时写入（首次安装 = Catppuccin Frappé 基准主题）。
    # 之后用户用 kitten themes 切换主题会更新这个文件——重跑本脚本
    # 绝不覆盖它：主题是用户选择，不在"重置基准"范围内。
    if [ ! -f "$THEME_FILE" ]; then
        cat > "$THEME_FILE" << 'THEME_EOF'
# Catppuccin Kitty Frappé
# upstream: https://github.com/catppuccin/kitty/blob/main/themes/frappe.conf
# 由 fresh-install/kitty/install.sh 在首次安装时写入，
# 之后由 kitten themes 管理，重跑本脚本不会覆盖。
foreground              #c6d0f5
background              #303446
selection_foreground    #303446
selection_background    #f2d5cf
cursor                  #f2d5cf
cursor_text_color       #303446
scrollbar_handle_color  #949cbb
scrollbar_track_color   #51576d
url_color               #f2d5cf
active_border_color     #babbf1
inactive_border_color   #737994
bell_border_color       #e5c890
active_tab_foreground   #232634
active_tab_background   #ca9ee6
inactive_tab_foreground #c6d0f5
inactive_tab_background #292c3c
tab_bar_background      #232634
mark1_foreground #303446
mark1_background #babbf1
mark2_foreground #303446
mark2_background #ca9ee6
mark3_foreground #303446
mark3_background #85c1dc
color0  #51576d
color1  #e78284
color2  #a6d189
color3  #e5c890
color4  #8caaee
color5  #f4b8e4
color6  #81c8be
color7  #b5bfe2
color8  #626880
color9  #e78284
color10 #a6d189
color11 #e5c890
color12 #8caaee
color13 #f4b8e4
color14 #81c8be
color15 #a5adce
THEME_EOF
        echo "已写入默认主题 (Catppuccin Frappé): $THEME_FILE"
    else
        echo "主题文件已存在，保持不动: $THEME_FILE"
        echo "（kitten themes 的用户选择不会被重跑重置）"
    fi

    # 同目录 mktemp + mv：重定向写入会先截断目标文件，磁盘满或写失败时
    # 原文件被截成空。mv 是同一文件系统内的原子替换，失败则原文件完好。
    tmp="$(mktemp "$CONFIG_FILE.tmp.XXXXXX")"

    {
        echo "# 由 fresh-install/kitty/install.sh 生成与维护。"
        echo "# 重跑脚本会把本文件重置为基准内容；手工修改请在脚本里改。"
        if font_exists "$FONT_FAMILY"; then
            echo "font_family      $FONT_FAMILY"
        fi
        echo "font_size        $FONT_SIZE"
        if font_exists "$CJK_FONT_FAMILY"; then
            echo "symbol_map       $CJK_RANGE $CJK_FONT_FAMILY"
        fi
        # 注意：GLFW_IM_MODULE 不写在这里。kitty.conf 的 env 指令在
        # GLFW 初始化之后才生效（实测确认），输入法桥改由
        # $LOCAL_BIN_DIR/kitty 包装脚本在 exec 前 export（见 integrate_path）。

        # 主题文件放在最后 include：theme.conf 由首次安装写入 Frappé，
        # 之后 kitten themes 切换主题会更新它，重跑本脚本只重置基准
        # 配置、不动用户主题。
        echo "include theme.conf"
    } > "$tmp"

    if [ -f "$CONFIG_FILE" ] && cmp -s "$tmp" "$CONFIG_FILE"; then
        rm -f "$tmp"
        echo "配置文件无变化，跳过写入: $CONFIG_FILE"
        return 0
    fi

    backup_file "$CONFIG_FILE"
    chmod --reference="$CONFIG_FILE" "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$CONFIG_FILE"
    echo "已写入: $CONFIG_FILE"
}

verify_config() {
    local actual

    # 回读磁盘校验，防"写入动作成功但内容被外部改掉"（如并发跑了
    # 两个本脚本，或用户在写入瞬间手改文件）。
    if [ ! -f "$CONFIG_FILE" ]; then
        echo -e "${RED}错误: $CONFIG_FILE 不存在${NC}"
        return 1
    fi

    actual="$(awk '/^font_size[[:space:]]/ {print $2; exit}' "$CONFIG_FILE")"
    if [ "$actual" = "$FONT_SIZE" ]; then
        echo "OK: font_size = $actual"
    else
        echo -e "${RED}错误: font_size 期望 $FONT_SIZE，实际 ${actual:-(空)}${NC}"
        return 1
    fi

    if grep -q '^include[[:space:]]*theme.conf$' "$CONFIG_FILE"; then
        echo "OK: include theme.conf 已写入（主题文件引用）"
    else
        echo -e "${RED}错误: kitty.conf 中未找到 include theme.conf${NC}"
        return 1
    fi

    if [ -f "$THEME_FILE" ] && grep -q '^background[[:space:]]*#303446$' "$THEME_FILE"; then
        echo "OK: 主题文件存在（首次安装写入的 Frappé 或用户自选主题均满足）"
    else
        echo -e "${YELLOW}警告: $THEME_FILE 缺失，主题回退为 kitty 默认配色。${NC}"
        echo -e "${YELLOW}  首次安装会写入 Frappé；若这是重跑且文件被手动删除，${NC}"
        echo -e "${YELLOW}  重跑本脚本可恢复。${NC}"
    fi
}

# ============================================
# 步骤 4: 桌面集成
# ============================================
# 官方 installer 自带的 desktop 文件里 Exec=kitty 依赖 PATH
# （步骤 2 的链接已保证），但 Icon=kitty 这种非绝对路径在 GNOME
# 应用菜单里显示不出图标，必须改成安装目录里的绝对路径。
# kitty FAQ 官方文档即如此建议。
integrate_desktop() {
    local src dst

    mkdir -p "$DESKTOP_DST_DIR"
    for src in "$DESKTOP_SRC_DIR"/*.desktop; do
        [ -f "$src" ] || continue
        dst="$DESKTOP_DST_DIR/$(basename "$src")"

        sed -e "s|^Icon=kitty$|Icon=$KITTY_APP_DIR/share/icons/hicolor/256x256/apps/kitty.png|" \
            -e "s|^TryExec=kitty$|TryExec=$KITTY_BIN_DIR/kitty|" \
            "$src" > "$dst"
        chmod --reference="$src" "$dst" 2>/dev/null || true
        echo "已安装桌面入口: $dst"
    done

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DST_DIR" >/dev/null 2>&1 || true
    fi
}

# ============================================
# 步骤 5: 默认终端接管（--no-default-terminal 时不执行）
# ============================================
# 目标：Ctrl+Alt+T 打开 kitty。
#
# 为什么不用 update-alternatives：它需要 sudo 改 /etc/alternatives，
# 而 gsd-media-keys（GNOME 快捷键守护进程，Ctrl+Alt+T 的处理者）
# 启动 x-terminal-emulator 时走的是 PATH 查找——已用 XTEST 注入
# 真实按键实测验证。Ubuntu 默认的 ~/.profile 会把 ~/.local/bin
# 放进登录会话 PATH（GDM 登录时 source，再经 pam 进入 systemd
# 用户会话），所以用户目录里的同名包装脚本即可接管，不需要 root。
#
# 为什么还要写 xdg-terminals.list：它对 Ctrl+Alt+T 无效（同样实测
# 验证过），但它影响遵守 xdg-terminal-exec 规范的程序（如文件管理器
# 的“在终端中打开”），两者各管各的场景，都写上。
set_default_terminal() {
    local wrapper="$LOCAL_BIN_DIR/x-terminal-emulator" tmp

    mkdir -p "$LOCAL_BIN_DIR"

    # 用临时文件写而非直接重定向：写一半失败不会留下残缺脚本；
    # mv 是同文件系统内的原子替换。
    tmp="$(mktemp "$wrapper.tmp.XXXXXX")"
    cat > "$tmp" << EOF
#!/bin/bash
# 由 fresh-install/kitty/install.sh 生成：把 x-terminal-emulator 指向 kitty
# 不直接 exec 二进制，而是走 kitty 包装脚本：那里的 export GLFW_IM_MODULE
# 是中文输入的必要条件。
exec $LOCAL_BIN_DIR/kitty "\$@"
EOF
    chmod 755 "$tmp"
    if [ -f "$wrapper" ] && cmp -s "$tmp" "$wrapper"; then
        rm -f "$tmp"
        echo "包装脚本无变化，跳过: $wrapper"
    else
        backup_file "$wrapper"
        mv -f "$tmp" "$wrapper"
        echo "已写入: $wrapper"
    fi

    mkdir -p "$(dirname "$XDG_TERMINALS_FILE")"
    if [ -f "$XDG_TERMINALS_FILE" ] && cmp -s <(printf 'kitty.desktop\n') "$XDG_TERMINALS_FILE"; then
        echo "默认终端已是 kitty，跳过"
    else
        backup_file "$XDG_TERMINALS_FILE"
        printf 'kitty.desktop\n' > "$XDG_TERMINALS_FILE"
        echo "已写入默认终端: $XDG_TERMINALS_FILE"
    fi

    # 登录会话的 gsd-media-keys 由 systemd 用户管理器启动，PATH 继承自
    # 登录时的会话环境。标准 Ubuntu 的 ~/.profile 会加入 ~/.local/bin；
    # 若用户的 ~/.profile 改过，重新登录后 Ctrl+Alt+T 会退回系统
    # x-terminal-emulator（gnome-terminal）。这里提前检查并警告。
    if ! systemctl --user show-environment 2>/dev/null | grep '^PATH=' | grep -qF "$LOCAL_BIN_DIR"; then
        echo -e "${YELLOW}警告: systemd 用户会话 PATH 中未包含 $LOCAL_BIN_DIR。${NC}"
        echo -e "${YELLOW}  重新登录后 Ctrl+Alt+T 可能仍启动旧终端。${NC}"
        echo -e "${YELLOW}  请确认 ~/.profile 中保留以下配置块（Ubuntu 默认就有）:${NC}"
        echo -e "${YELLOW}    if [ -d \"\$HOME/.local/bin\" ] ; then${NC}"
        echo -e "${YELLOW}        PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
        echo -e "${YELLOW}    fi${NC}"
    fi
}

# ============================================
# 步骤 6: 启动冒烟测试
# ============================================
# 只检查"窗口能开起来、配置能被解析"。exit 124 = 被 timeout 杀掉，
# 说明 kitty 在时限内正常存活，视为通过；任何提前退出都是失败。
# 无显示环境（SSH）时跳过：kitty 打不开窗口不是安装问题。
#
# stderr 不参与判定：kitty 在部分环境下启动会打良性警告
# （如 "No directories to watch provided"），当作失败会误杀。
smoke_test() {
    local rc

    if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
        echo -e "${YELLOW}跳过: 无显示环境（DISPLAY/WAYLAND_DISPLAY 均未设置）${NC}"
        return 0
    fi

    echo "启动 kitty 做冒烟测试（3 秒后自动关闭）..."

    # 必须走 $LOCAL_BIN_DIR/kitty 包装脚本启动：要验证的不只是"能开窗"，
    # 还有 GLFW_IM_MODULE 是否真的进入了 kitty 的 exec 环境——那是
    # fcitx5 中文输入的前提。包装脚本 exec 替换自身，$! 就是 kitty 进程。
    "$LOCAL_BIN_DIR/kitty" --config "$CONFIG_FILE" --single-instance=n >/dev/null 2>&1 &
    local kp=$!
    sleep 3

    if ! kill -0 "$kp" 2>/dev/null; then
        echo -e "${RED}✗ 冒烟测试失败：kitty 提前退出。${NC}"
        echo -e "${YELLOW}  可能是 GPU/驱动问题：可尝试在 kitty.conf 加${NC}"
        echo -e "${YELLOW}  linux_display_server x11 强制 X11 渲染路径。${NC}"
        return 1
    fi

    # /proc/PID/environ 显示的是 exec 时的初始环境：能看到这个变量，
    # 才证明 GLFW 初始化时读得到它（与此对照，kitty.conf 的 env 指令
    # 用 setenv 修改进程环境，在这里是看不到的——那正是它无效的原因）。
    if tr '\0' '\n' < "/proc/$kp/environ" 2>/dev/null | grep -qx 'GLFW_IM_MODULE=ibus'; then
        echo "OK: kitty 正常启动，且 GLFW_IM_MODULE=ibus 已进入进程环境（中文输入前提）"
    else
        echo -e "${YELLOW}警告: kitty 进程环境中未发现 GLFW_IM_MODULE=ibus${NC}"
        echo -e "${YELLOW}  包装脚本可能被绕过或内容有误，kitty 里中文输入会失效。${NC}"
    fi

    kill "$kp" 2>/dev/null || true
    wait "$kp" 2>/dev/null || true
}

verify_installation() {
    local failed=false version

    section "安装后检查"

    if [ -x "$KITTY_BIN_DIR/kitty" ]; then
        version="$("$KITTY_BIN_DIR/kitty" --version 2>/dev/null)"
        echo "OK: $KITTY_BIN_DIR/kitty —— $version"
    else
        echo -e "${RED}错误: $KITTY_BIN_DIR/kitty 不存在${NC}"
        failed=true
    fi

    if [ -x "$LOCAL_BIN_DIR/kitty" ] && grep -q 'GLFW_IM_MODULE=ibus' "$LOCAL_BIN_DIR/kitty"; then
        echo "OK: $LOCAL_BIN_DIR/kitty 包装脚本（含 GLFW_IM_MODULE=ibus）"
    else
        echo -e "${RED}错误: $LOCAL_BIN_DIR/kitty 包装脚本缺失或不含输入法桥${NC}"
        failed=true
    fi

    if [ -L "$LOCAL_BIN_DIR/kitten" ]; then
        echo "OK: $LOCAL_BIN_DIR/kitten -> $(readlink "$LOCAL_BIN_DIR/kitten")"
    else
        echo -e "${RED}错误: $LOCAL_BIN_DIR/kitten 不是符号链接${NC}"
        failed=true
    fi

    if [ -f "$DESKTOP_DST_DIR/kitty.desktop" ]; then
        echo "OK: 桌面入口 kitty.desktop"
    else
        echo -e "${RED}错误: $DESKTOP_DST_DIR/kitty.desktop 不存在${NC}"
        failed=true
    fi

    if [ "$DEFAULT_TERMINAL" = true ]; then
        if [ -x "$LOCAL_BIN_DIR/x-terminal-emulator" ]; then
            echo "OK: $LOCAL_BIN_DIR/x-terminal-emulator（接管 Ctrl+Alt+T）"
        else
            echo -e "${RED}错误: $LOCAL_BIN_DIR/x-terminal-emulator 不存在${NC}"
            failed=true
        fi
        if [ -f "$XDG_TERMINALS_FILE" ] && grep -qx 'kitty.desktop' "$XDG_TERMINALS_FILE"; then
            echo "OK: 默认终端 xdg-terminals.list = kitty.desktop"
        else
            echo -e "${RED}错误: $XDG_TERMINALS_FILE 未指向 kitty.desktop${NC}"
            failed=true
        fi
    fi

    if [ "$failed" = true ]; then
        echo
        echo "安装后检查发现错误，请先处理以上问题。"
        return 1
    fi
}

parse_args "$@"
ensure_not_root

echo "========================================"
echo "  kitty 终端安装（官方最新版）"
echo "========================================"
echo
echo "安装目标: $KITTY_APP_DIR"
echo "仅支持 Ubuntu 24.04 及以上版本，支持 X11 和 Wayland"
echo

check_supported_system
ensure_cjk_font
show_current_state

if [ "$CHECK_ONLY" = true ]; then
    echo "检查完成。--check 模式不会安装或修改任何内容。"
    exit 0
fi

TOTAL=5
STEP=0

section "[$((++STEP))/$TOTAL] 下载安装最新版 kitty"
install_kitty

section "[$((++STEP))/$TOTAL] PATH 集成"
integrate_path

section "[$((++STEP))/$TOTAL] 写入 kitty 配置"
write_kitty_config
verify_config

section "[$((++STEP))/$TOTAL] 桌面集成与默认终端"
integrate_desktop
if [ "$DEFAULT_TERMINAL" = true ]; then
    set_default_terminal
fi

section "[$((++STEP))/$TOTAL] 启动冒烟测试"
smoke_test

verify_installation

echo
echo "========================================"
echo "  安装完成"
echo "========================================"
echo
if [ "$DEFAULT_TERMINAL" = true ]; then
    echo "Ctrl+Alt+T: 已通过 ~/.local/bin/x-terminal-emulator 包装脚本接管，"
    echo "  立即生效（gsd-media-keys 每次按键实时解析，无需注销）"
else
    echo "Ctrl+Alt+T: 保持系统原样（--no-default-terminal）"
fi
echo
echo "验证清单:"
echo "  1. 按 Ctrl+Alt+T —— 应打开 kitty"
echo "  2. 键盘协议: kitty 里运行 kitten show-key -m kitty，"
echo "     按 shift+enter 应显示带 ;2 修饰位的 CSI u 序列（而非裸回车）"
echo "     —— 这是 pi 等 CLI agent 里 shift+enter 换行生效的前提"
echo "  3. 中文输入: kitty 里 Ctrl+Space 切到拼音输入一段中文。"
echo "     输入法桥已由 $LOCAL_BIN_DIR/kitty 包装脚本注入"
echo "     （启动时 export GLFW_IM_MODULE=ibus），冒烟测试已确认"
echo "     该变量进入 kitty 进程环境。若仍无法输入，检查 fcitx5 是否"
echo "     在运行（pgrep fcitx5），并确认没有别处覆盖该变量。"
echo
echo "SSH 提示: ssh 到远端建议用 kitten ssh 而不是 ssh，"
echo "它会自动带上 kitty terminfo，避免远端报 TERM 未知。"
echo
echo "主题: 已默认启用 Catppuccin Frappé。换主题运行 kitten themes"
echo "（实时预览），切换后的选择写在 $THEME_FILE，重跑本脚本不会重置。"
echo
echo "清理提示: 重跑本脚本 = 重装最新版 + 重置配置。"
echo "旧配置备份在 $CONFIG_FILE.bak.<时间戳>，确认无误可自行删除。"
