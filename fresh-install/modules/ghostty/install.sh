#!/bin/bash
# Ghostty 终端安装配置脚本（Ubuntu 社区打包版）
# 仅支持 Ubuntu 24.04 及以上版本的 amd64 / arm64。
#
# Ghostty 上游不发布 Linux 二进制；这里优先使用 Mike Kasberg 维护的
# 第三方 PPA，让 apt 校验 PGP 签名并管理依赖。新机器若因代理尚未就绪
# 无法访问 Launchpad，则退回同一维护者的 GitHub .deb，并在安装前核对
# GitHub API 给出的 SHA-256 digest。
#
# 幂等语义：重跑 = 确保最新版 + 把配置重置为基准内容，并把 Ghostty
# SSH 鼠标自愈 hook 同步到用户 zsh；旧文件先备份。另外生成用户级 desktop
# 覆盖，让每个外部启动用自己的进程；该文件含本模块之外的改动时明确失败，
# 不覆盖也不静默降级。
# --check 只读，不添加源、不更新 apt、不下载或写入任何文件。

set -euo pipefail

CHECK_ONLY=false
PI_LAUNCHER_ONLY=false
PASTE_KEYBIND_ONLY=false
SHELL_PASTE_ONLY=false
# 默认接管 Ctrl+Alt+T。本模块刚引入时默认为否，是为了不和当时还在的
# kitty 模块争抢同一个包装脚本（两个模块都写就是后跑者赢，无告警）。
# kitty 模块已移除，Ghostty 是本仓库唯一的终端模块，该理由不再成立；
# 不接管反而会让新机器装完 Ctrl+Alt+T 仍指向 gnome-terminal。
DEFAULT_TERMINAL=true
INSTALL_MODE="auto"
SUDO_CMD="${SUDO_CMD:-sudo}"

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 共享等锁助手：新装系统首开机时后台自动更新会长时间持 apt 锁，
# 直接 add-apt-repository/apt-get 会撞锁失败，先等它结束。脚本被单独
# 拷出、助手缺失时定义空操作跳过等锁
APT_LOCK_WAIT_LIB="$SCRIPT_DIR/../../lib/apt-lock-wait.sh"
if [ -f "$APT_LOCK_WAIT_LIB" ]; then . "$APT_LOCK_WAIT_LIB"; else wait_for_apt_lock() { return 0; }; fi

PPA_NAME="ppa:mkasberg/ghostty-ubuntu"
PPA_MARKER="ppa.launchpadcontent.net/mkasberg/ghostty-ubuntu"
PPA_FINGERPRINT="0721FDF5FECB88DC6920361657C8EF455CEAE491"
GITHUB_API="https://api.github.com/repos/mkasberg/ghostty-ubuntu/releases/latest"
DESKTOP_ID="com.mitchellh.ghostty.desktop"
DESKTOP_FILE="/usr/share/applications/$DESKTOP_ID"
# 用户级同 ID 覆盖：XDG 查找用户数据目录先于 /usr/share，同一个 desktop ID
# 以本文件为准，软件包提供的系统文件保持原样（见 render_desktop_override）。
USER_DESKTOP_DIR="$HOME/.local/share/applications"
USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
GHOSTTY_BIN="/usr/bin/ghostty"
USER_GHOSTTY_LAUNCHER="$HOME/.local/bin/ghostty"
USER_PI_GHOSTTY_LAUNCHER="$HOME/.local/bin/ghostty-pi"
BACKPRESSURE_SOURCE="$SCRIPT_DIR/wayland-backpressure.c"
BACKPRESSURE_LIBRARY="$HOME/.local/lib/ghostty/libwayland-backpressure.so"
BACKPRESSURE_STATE="$HOME/.config/ghostty/wayland-backpressure"
CONFIG_DIR="$HOME/.config/ghostty"
CONFIG_FILE="$CONFIG_DIR/config.ghostty"
LOCAL_BIN_DIR="$HOME/.local/bin"
# SSH 异常断开后清理残留鼠标上报模式的 zsh hook。源码内嵌在
# render_ssh_mouse_hook，安装时写入用户目录；不会引用仓库运行时路径。
SSH_MOUSE_HOOK_FILENAME="ghostty-ssh-mouse-reset.zsh"
SSH_MOUSE_HOOK_CONFIG="$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME"
LOCAL_PASTE_HOOK_FILENAME="ghostty-local-paste.zsh"
LOCAL_PASTE_HOOK_CONFIG="$HOME/.config/ghostty/$LOCAL_PASTE_HOOK_FILENAME"
ZSHRC_BLOCK_BEGIN="# >>> fresh-install ghostty: ssh 鼠标复位 hook >>>"
ZSHRC_BLOCK_END="# <<< fresh-install ghostty: ssh 鼠标复位 hook <<<"
FONTCONFIG_CONF_DIR="$HOME/.config/fontconfig/conf.d"
# 89 > 69：必须排在 69-language-selector-zh-*.conf 之后才能盖过它。
ZH_MONO_CONF="$FONTCONFIG_CONF_DIR/89-ghostty-zh-mono.conf"
TERMINAL_WRAPPER="$LOCAL_BIN_DIR/x-terminal-emulator"
XDG_TERMINALS_FILE="$HOME/.config/xdg-terminals.list"
FONT_FAMILY="JetBrains Mono"
FONT_PACKAGE="fonts-jetbrains-mono"
CJK_FONT_FAMILY="Noto Sans Mono CJK SC"
CJK_FONT_PACKAGE="fonts-noto-cjk"
FONT_SIZE="12"
THEME="Catppuccin Frappe"
# 新终端的起始目录。空值 = 不写该行，沿用 Ghostty 默认（inherit）。
# 可写绝对路径、~/ 开头的路径，或特殊值 home / inherit。
WORKING_DIRECTORY="~/Documents"

# shell 集成特性：只写与默认值的**差异项**。
# 文档明确：省略某个特性就用它的默认值，所以不必抄全量串。
# 不抄全量是有意的：上游将来调整其它特性的默认值时，本模块能自动跟上，
# 而不会把一份过期的快照冻在用户配置里（特别是 no-sudo 这种安全相关项）。
#
#   ssh-terminfo —— SSH 到远端时自动用 infocmp|tic 安装 xterm-ghostty 记录。
#     远端 ncurses 普遍还没收录这条记录（需 >= 6.5-20241228），不装则
#     vim/htop 会报 unknown terminal type 或花屏。它装到远端的 ~/.terminfo，
#     不需要 sudo，也不影响其它用户。
#   ssh-env —— 配套项。terminfo 装不上时靠环境变量传递终端能力。
#
# 为什么敢默认开：官方文档声明两者同时开启时，安装失败会自动回退到
# xterm-256color，不会把用户卡在花屏状态。前提是远端有 infocmp 与 tic
# （ncurses 自带，绝大多数 Linux 都有）；极简容器镜像可能没有，那种情况下回退。
SHELL_INTEGRATION_FEATURES="ssh-env,ssh-terminfo"
# 所有普通 Ghostty 窗口将 Ctrl+Shift+V 交给终端内的 Ctrl+V 处理器。
# 历史基准 paste_from_clipboard 只用于窄迁移的逐字节核对。
GHOSTTY_PASTE_ACTION='text:\x16'

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

die() {
    echo -e "${RED}错误: $*${NC}" >&2
    exit 1
}

warn() {
    echo -e "${YELLOW}警告: $*${NC}" >&2
}

usage() {
    cat <<'EOF'
用法:
  ./install.sh [--check] [--no-default-terminal] [--ppa-only | --deb-only] [--help]
  ./install.sh --pi-launcher-only
  ./install.sh --paste-keybind-only
  ./install.sh --shell-paste-only

选项:
  --check                 只读预检：报告当前状态与计划，不安装、不修改
  --pi-launcher-only      只验证受保护启动器/背压库，再安装 Pi 专用启动器
  --paste-keybind-only    只迁移现有受管全局配置的一条粘贴按键绑定
  --shell-paste-only      只为现有受管 Ghostty/zsh 安装本地 Wayland 提示符粘贴 hook
  --no-default-terminal   不接管 Ctrl+Alt+T（默认会接管）
  --default-terminal      兼容旧命令行，默认已是开启状态
  --ppa-only              只使用第三方 PPA；失败时不退回 GitHub .deb
  --deb-only              跳过 PPA，直接安装经 SHA-256 核对的 GitHub .deb
  --help                  显示帮助

注意:
  不要用 sudo 运行本脚本；直接运行 ./install.sh，脚本会在 apt 步骤调用 sudo。
  默认接管 Ctrl+Alt+T（旧的包装脚本会先备份）。重跑会确保最新版，并把
  Ghostty 配置重置为本模块的基准内容；内容变化时会先保存 .bak.<时间戳> 备份。
  用户级 desktop 覆盖（每个外部启动用独立进程）含本模块之外的改动时，
  脚本会明确失败而不是覆盖，处理方式见 README 的对应章节。
  --pi-launcher-only 不执行软件包/编译器、配置、字体、桌面入口、默认终端
  或 GUI 步骤。--paste-keybind-only 只接受旧/新精确受管配置，拒绝自定义
  内容和符号链接；不执行软件包、启动器、字体、桌面入口、默认终端或 GUI
  步骤。--shell-paste-only 核对现有全局按键及 zsh 托管块，只写入粘贴 hook
  和必要时迁移 .zshrc 托管块；不执行其它安装步骤。窄模式不能组合。
EOF
}

parse_args() {
    local other_mode=false
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check) CHECK_ONLY=true; other_mode=true ;;
            --pi-launcher-only) PI_LAUNCHER_ONLY=true ;;
            --paste-keybind-only) PASTE_KEYBIND_ONLY=true ;;
            --shell-paste-only) SHELL_PASTE_ONLY=true ;;
            --default-terminal) DEFAULT_TERMINAL=true; other_mode=true ;;
            --no-default-terminal) DEFAULT_TERMINAL=false; other_mode=true ;;
            --ppa-only)
                [ "$INSTALL_MODE" != "deb" ] || die "--ppa-only 与 --deb-only 不能同时使用"
                INSTALL_MODE="ppa"
                other_mode=true
                ;;
            --deb-only)
                [ "$INSTALL_MODE" != "ppa" ] || die "--ppa-only 与 --deb-only 不能同时使用"
                INSTALL_MODE="deb"
                other_mode=true
                ;;
            --help|-h) usage; exit 0 ;;
            *) die "未知参数: $1（请用 --help 查看用法）" ;;
        esac
        shift
    done
    if [ "$PI_LAUNCHER_ONLY" = true ] && { [ "$PASTE_KEYBIND_ONLY" = true ] || [ "$SHELL_PASTE_ONLY" = true ] || [ "$other_mode" = true ]; }; then
        die "--pi-launcher-only 不能与其他安装或预检选项组合"
    fi
    if [ "$PASTE_KEYBIND_ONLY" = true ] && { [ "$SHELL_PASTE_ONLY" = true ] || [ "$other_mode" = true ]; }; then
        die "--paste-keybind-only 不能与其他安装或预检选项组合"
    fi
    if [ "$SHELL_PASTE_ONLY" = true ] && [ "$other_mode" = true ]; then
        die "--shell-paste-only 不能与其他安装或预检选项组合"
    fi
}

ensure_not_root() {
    if [ "${EUID:-$(id -u)}" -eq 0 ] || [ -n "${SUDO_USER:-}" ]; then
        die "请不要用 sudo 或 root 运行本脚本。请以普通用户直接运行 ./install.sh"
    fi
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "缺少必需命令: $1"
}

check_supported_system() {
    [ -r /etc/os-release ] || die "无法读取 /etc/os-release，无法确认系统版本"
    # shellcheck disable=SC1091
    . /etc/os-release

    [ "${ID:-}" = "ubuntu" ] || die "本脚本仅支持 Ubuntu 24.04 及以上版本；当前是 ${PRETTY_NAME:-unknown}"
    dpkg --compare-versions "${VERSION_ID:-0}" ge "24.04" \
        || die "本脚本仅支持 Ubuntu 24.04 及以上版本；当前是 ${PRETTY_NAME:-Ubuntu ${VERSION_ID:-unknown}}"

    ARCH="$(dpkg --print-architecture)"
    case "$ARCH" in
        amd64|arm64) ;;
        *) die "不支持的架构: $ARCH（仅支持 amd64、arm64）" ;;
    esac

    UBUNTU_VERSION="$VERSION_ID"
    OS_NAME="${PRETTY_NAME:-Ubuntu $VERSION_ID}"
}

preflight_commands() {
    # zsh 运行时 hook 在本文件中渲染；库需要独立 C 源码。
    # 打包漏掉源码必须在任何写入前拒绝，不能报告一个不完整的成功。
    [ -f "$BACKPRESSURE_SOURCE" ] || die "Ghostty 模块不完整，缺少 Wayland 修复源码: $BACKPRESSURE_SOURCE"
    local command_name
    for command_name in dpkg apt-cache grep awk sed cmp cp mv mktemp chmod mkdir date fc-list infocmp; do
        require_command "$command_name"
    done

    if [ "$CHECK_ONLY" = false ]; then
        for command_name in "$SUDO_CMD" apt-get; do
            require_command "$command_name"
        done
        if [ "$INSTALL_MODE" != "deb" ]; then
            for command_name in add-apt-repository gpg find; do
                require_command "$command_name"
            done
        fi
        if [ "$INSTALL_MODE" != "ppa" ]; then
            for command_name in curl python3 sha256sum; do
                require_command "$command_name"
            done
        fi
    fi
}

font_exists() {
    # 精确匹配 fontconfig 返回的 family 名，避免把「Noto Sans CJK SC」
    # 误当成这里要求的等宽「Noto Sans Mono CJK SC」。
    fc-list : family 2>/dev/null | grep -Fxi "$1" >/dev/null 2>&1
}

# ============================================
# 字体：缺失即安装，而不是静默降级
# ============================================
# 两个字体都是基准配置的实质内容，不是可有可无的装饰：
#   - 等宽主字体决定终端的日常观感；
#   - CJK 等宽回退字体决定中文能否按预期渲染——写一个系统里不存在的
#     字体名，Ghostty 会静默回落到 fontconfig 的选择，用户看到的中文
#     不是配置声明的那个。
# 脚本本来就要为 apt 调 sudo（安装 Ghostty 本体），顺带补齐字体不引入
# 新的权限要求。apt 失败不终止整个安装：字体缺失只是观感降级，
# 不值得让已经可用的终端装不上，此时按 font_exists 的判断跳过该行。
ensure_font() {
    local family="$1" package="$2"

    if font_exists "$family"; then
        return 0
    fi

    echo "$family 未安装，尝试通过 apt 安装 $package ..."

    # 后台 apt 活动（新系统首开机自动更新）持锁时装不上；等一等，
    # 超时则按下方的字体缺失降级逻辑处理（只告警不中止）
    wait_for_apt_lock 600 || { warn "apt 锁被占用且等待超时，跳过 $package 安装。"; return 0; }

    local rc=0
    "$SUDO_CMD" apt-get install -y "$package" || rc=$?
    if [ "$rc" -ne 0 ]; then
        warn "$package 安装失败（退出码 $rc）；配置中将省略 $family。"
        warn "  稍后可手动执行: sudo apt install $package，然后重跑本脚本。"
        return 0
    fi

    # 不信任「apt 返回 0」本身：包名对但字族名不符时配置仍会写错。
    # fc-cache 由字体包的 postinst 触发，这里直接回读 fontconfig 校验。
    if font_exists "$family"; then
        echo "OK: $family 已安装"
    else
        warn "$package 已安装但未提供字族「$family」；配置中将省略该行。"
    fi
}

ensure_fonts() {
    ensure_font "$FONT_FAMILY" "$FONT_PACKAGE"
    ensure_font "$CJK_FONT_FAMILY" "$CJK_FONT_PACKAGE"
}

# ============================================
# 中文 locale 下等宽字体被劫持的修复
# ============================================
# 病灶（实测定位）：Ubuntu 的 language-selector-common 包在
# /etc/fonts/conf.d/69-language-selector-zh-cn.conf 里写了：
#   <test name="family">monospace</test> + <test name="lang">zh-cn</test>
#   -> <edit name="family" mode="prepend" binding="strong">DejaVu Sans Mono ...
# 而 Ghostty 的 src/font/discovery.zig 无条件给查询加 FC_SPACING=FC_MONO，
# 恰好命中这条规则；强绑定的 DejaVu 被插到最前，压过应用显式请求的
# 字体。结果：config 里写了 font-family = JetBrains Mono，实际渲染却是
# DejaVu Sans Mono，且完全静默。实测证据：同一份配置下
#   LANG=en_US.UTF-8 -> lsof 显示加载 JetBrainsMono-Regular.ttf
#   LANG=zh_CN.UTF-8 -> lsof 显示加载 DejaVuSansMono.ttf
# 该规则影响 zh 下**所有**等宽字体（Liberation/Nimbus/Noto Mono 均中招），
# 不是 JetBrains Mono 特有，也不是 Ghostty 的 bug。
#
# 为什么不用 LANG=en 启动 Ghostty 绕过：那会把终端里所有程序的 locale
# 一并换成英文（日期、报错、man 页），对刻意设了 zh_CN 的用户是倒退。
# 修在 fontconfig 层才是对症：病灶本身就是一条 fontconfig 规则。
#
# 规则写法上刻意保守：只在查询**已点名**该字体时才把它提前，
# 而不是对所有 zh+等宽查询无差别 prepend——后者会连别的程序请求别的
# 等宽字体时也被改成 JetBrains Mono。实测：写入后 monospace 与
# Liberation Mono 仍保持系统默认，不受干扰。
#
# （试过的弯路：想写“通用地撤销 DejaVu 强绑定”的规则，
# <edit mode="delete"> 语法不成立，结果选出楷体 ukai。通用规则难写对，
# 显式列名虽啰嗦但可靠。）

# fc-match 解析结果与请求的字族不一致 = 被劫持。
# 用实测症状做门控，而不是判断 locale 名：非中文机器不会被写入无用规则，
# 将来 Ubuntu 改了那条规则也能自动不再干预。
mono_family_hijacked() {
    local family="$1" got
    font_exists "$family" || return 1
    got="$(fc-match --format='%{family}' "$family:spacing=100" 2>/dev/null)"
    got="${got%%,*}"
    [ -n "$got" ] && [ "$got" != "$family" ]
}

any_mono_family_hijacked() {
    local family
    for family in "$FONT_FAMILY" "$CJK_FONT_FAMILY"; do
        mono_family_hijacked "$family" && return 0
    done
    return 1
}

render_zh_mono_conf() {
    local family
    printf '%s\n' '<?xml version="1.0"?>'
    printf '%s\n' '<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">'
    printf '%s\n' '<fontconfig>'
    printf '%s\n' '  <!-- 由 fresh-install/modules/ghostty/install.sh 生成与维护。 -->'
    printf '%s\n' '  <!-- 中文 locale 下 Ubuntu 的 69-language-selector-zh-*.conf 会给所有 -->'
    printf '%s\n' '  <!-- 等宽查询强绑定 prepend 「DejaVu Sans Mono」，压过应用显式请求的 -->'
    printf '%s\n' '  <!-- 字体。下面只在查询已点名该字体时把它拉回最前，不影响其它字体请求。 -->'
    for family in "$FONT_FAMILY" "$CJK_FONT_FAMILY"; do
        font_exists "$family" || continue
        printf '  <match target="pattern">\n'
        printf '    <test name="lang" compare="contains"><string>zh</string></test>\n'
        printf '    <test name="family" compare="contains"><string>%s</string></test>\n' "$family"
        printf '    <edit name="family" mode="assign_replace" binding="strong"><string>%s</string></edit>\n' "$family"
        printf '  </match>\n'
    done
    printf '%s\n' '</fontconfig>'
}

ensure_zh_mono_fontconfig() {
    local family remaining=''

    if ! any_mono_family_hijacked && [ ! -f "$ZH_MONO_CONF" ]; then
        echo "等宽字体解析正常，无需写 fontconfig 规则"
        return 0
    fi

    # 已被劫持，或规则文件已存在（后者下保持内容跟随 FONT_FAMILY 变更）。
    write_atomic_text "$ZH_MONO_CONF" "$(render_zh_mono_conf)"$'\n' 644

    # 回读校验：fc-match 每次都是新进程，会立即读到新规则（不需 fc-cache，
    # 因为没有新增字体文件，只是改排序规则）。
    for family in "$FONT_FAMILY" "$CJK_FONT_FAMILY"; do
        mono_family_hijacked "$family" && remaining="$remaining $family"
    done
    if [ -z "$remaining" ]; then
        echo "OK: 中文 locale 下等宽字体解析已恢复"
    else
        warn "写入规则后仍被劫持的字体:$remaining"
        warn "  终端可能仍渲染 DejaVu Sans Mono。字体只是观感问题，不阻断安装。"
    fi
}

ppa_present() {
    grep -Rqs --include='*.list' --include='*.sources' \
        "$PPA_MARKER" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null
}

installed_version() {
    # ghostty --version 会打印几十行构建信息；预检只需要首行的版本号。
    local bin=''
    if [ -x "$GHOSTTY_BIN" ]; then
        bin="$GHOSTTY_BIN"
    elif command -v ghostty >/dev/null 2>&1; then
        bin=ghostty
    else
        printf '未安装\n'
        return 0
    fi

    local line
    line="$("$bin" --version 2>/dev/null | head -n 1)"
    if [ -n "$line" ]; then
        printf '%s\n' "$line"
    else
        printf '已安装（版本读取失败）\n'
    fi
}

# apt 的中文输出用全角冒号（「候选：」），半角冒号的模式匹配不到。
# 强制 C 区域拿稳定的英文字段名，而不是去适配每一种本地化输出。
apt_candidate() {
    LC_ALL=C apt-cache policy ghostty 2>/dev/null \
        | awk '/Candidate:/ {print $2; exit}'
}

config_state() {
    if [ ! -e "$CONFIG_FILE" ]; then
        printf '不存在\n'
    elif [ ! -f "$CONFIG_FILE" ]; then
        printf '存在，但不是普通文件\n'
    elif [ ! -s "$CONFIG_FILE" ]; then
        printf '已存在，0 字节（Ghostty 会报告 error.FileIsEmpty；正常安装将先备份再覆盖）\n'
    else
        printf '已存在，%s 字节（正常安装将比较基准内容，变化时先备份再覆盖）\n' "$(wc -c < "$CONFIG_FILE")"
    fi
}

wrapper_state() {
    if [ ! -e "$TERMINAL_WRAPPER" ] && [ ! -L "$TERMINAL_WRAPPER" ]; then
        printf '未设置\n'
    elif [ -f "$TERMINAL_WRAPPER" ] && grep -Fq 'exec "$HOME/.local/bin/ghostty" "$@"' "$TERMINAL_WRAPPER" 2>/dev/null; then
        printf '已指向受保护的 Ghostty 启动器\n'
    elif [ -f "$TERMINAL_WRAPPER" ] && grep -Fq 'exec /usr/bin/ghostty "$@"' "$TERMINAL_WRAPPER" 2>/dev/null; then
        printf '旧版直连 Ghostty（正式安装将迁移）\n'
    elif grep -qi 'kitty' "$TERMINAL_WRAPPER" 2>/dev/null; then
        printf '当前由 kitty 接管（仅使用 --default-terminal 时才会备份并替换）\n'
    else
        printf '已存在，由其它程序管理（仅使用 --default-terminal 时才会备份并替换）\n'
    fi
}

xdg_terminal_state() {
    if [ ! -f "$XDG_TERMINALS_FILE" ]; then
        printf '未设置\n'
    elif grep -qx "$DESKTOP_ID" "$XDG_TERMINALS_FILE"; then
        printf '已指向 Ghostty\n'
    else
        printf '%s\n' "$(tr '\n' ' ' < "$XDG_TERMINALS_FILE")"
    fi
}

# xterm-ghostty 记录是否带 Ms（OSC 52 剪贴板）能力。
# Ms 是扩展能力，infocmp 必须加 -x 才会打印；行首用 [[:space:]] 而不是
# sed 的 \t，BSD 与 GNU 的差异不值得让这条检查依赖 sed 实现。
# 不用 grep -q：它一命中就退出，infocmp 可能因 SIGPIPE 非零退出，而本脚本
# 开了 pipefail，会把“确实有 Ms”误判成失败；grep >/dev/null 会读完输入。
xterm_ghostty_has_ms() {
    infocmp -x -1 xterm-ghostty 2>/dev/null \
        | grep '^[[:space:]]*Ms=' >/dev/null
}

show_check() {
    local candidate="无"
    candidate="$(apt_candidate)"
    [ -n "$candidate" ] || candidate="无"

    echo "系统与架构: $OS_NAME / $ARCH"
    echo "Ghostty: $(installed_version)"
    echo "apt 候选版本: $candidate"
    if ppa_present; then
        echo "第三方 PPA: 已出现在 apt 源中（$PPA_NAME）"
    else
        echo "第三方 PPA: 未出现在 apt 源中"
    fi
    echo "配置: $CONFIG_FILE —— $(config_state)"
    echo "Wayland 背压修复: $(backpressure_state)"
    echo "Pi 专用启动器: $USER_PI_GHOSTTY_LAUNCHER —— $(pi_launcher_state)"
    echo "用户级 desktop 覆盖: $USER_DESKTOP_FILE —— $(desktop_override_state)"
    echo "字体:"
    if font_exists "$FONT_FAMILY"; then
        echo "  $FONT_FAMILY: 已安装（将写入配置）"
    else
        echo "  $FONT_FAMILY: 未安装（正式安装将 apt 安装 $FONT_PACKAGE）"
    fi
    if font_exists "$CJK_FONT_FAMILY"; then
        echo "  $CJK_FONT_FAMILY: 已安装（将作为 CJK 回退字体写入）"
    else
        echo "  $CJK_FONT_FAMILY: 未安装（正式安装将 apt 安装 $CJK_FONT_PACKAGE）"
    fi

    echo "等宽字体解析（locale=${LANG:-未设置}）:"
    local family got
    for family in "$FONT_FAMILY" "$CJK_FONT_FAMILY"; do
        if ! font_exists "$family"; then
            echo "  $family: 未安装，跳过检测"
            continue
        fi
        got="$(fc-match --format='%{family}' "$family:spacing=100" 2>/dev/null)"
        got="${got%%,*}"
        if [ "$got" = "$family" ]; then
            echo "  $family: 正常"
        else
            echo "  $family: 被劫持 -> $got（Ubuntu zh 规则强插等宽字体）"
        fi
    done
    if [ -f "$ZH_MONO_CONF" ]; then
        echo "  修复规则: 已存在 $ZH_MONO_CONF"
    elif any_mono_family_hijacked; then
        echo "  修复规则: 不存在，正式安装将写入 $ZH_MONO_CONF"
    else
        echo "  修复规则: 不需要"
    fi
    if infocmp xterm-ghostty >/dev/null 2>&1; then
        echo "terminfo: xterm-ghostty 已可用"
        if xterm_ghostty_has_ms; then
            echo "OSC 52: xterm-ghostty terminfo 含 Ms（远端 tmux 复制回本机依赖它）"
        else
            echo "OSC 52: xterm-ghostty terminfo 不含 Ms（远端 tmux 需靠运行时识别或配置特性才有 Ms）"
        fi
    else
        echo "terminfo: xterm-ghostty 不可用（安装的系统级 .deb 应补齐）"
    fi
    echo "Ctrl+Alt+T: $(wrapper_state)"
    if command -v gsettings >/dev/null 2>&1; then
        echo "GSettings terminal exec: $(gsettings get org.gnome.desktop.default-applications.terminal exec 2>/dev/null)"
    fi
    echo "xdg-terminal-exec 默认项: $(xdg_terminal_state)"
    echo "SSH 断开后的鼠标状态自愈:"
    ssh_mouse_hook_state
    echo "  复位键: Ctrl+Shift+R -> Ghostty reset 动作"
    echo "本地 Wayland zsh 提示符粘贴: 正式安装会写入 $LOCAL_PASTE_HOOK_CONFIG 并迁移 .zshrc 托管块"

    case "$INSTALL_MODE" in
        ppa) echo "计划: 只走 PPA，更新 apt 并安装最新版 ghostty；失败即退出" ;;
        deb) echo "计划: 跳过 PPA，从 GitHub 选择严格匹配 $ARCH + Ubuntu $UBUNTU_VERSION 的最新版 .deb，核对 SHA-256 后安装" ;;
        auto)
            # 预检必须说出实际会发生的事：已是最新时主流程会短路跳过安装，
            # 写成「先走 PPA」就是误导。
            if ghostty_already_current; then
                echo "计划: Ghostty 已是最新版，将跳过包安装；仍会构建 Wayland 背压库并校验启动入口"
            else
                echo "计划: 先走 PPA；若 Launchpad 不可达或安装失败，再走严格匹配 $ARCH + Ubuntu $UBUNTU_VERSION 的 GitHub .deb"
            fi
            ;;
    esac
    if command -v cc >/dev/null 2>&1; then
        echo "编译器: cc 已就绪，将构建用户级 Wayland 修复库"
    else
        echo "编译器: 未找到 cc，正式安装将通过 apt 安装 build-essential"
    fi
    if [ "$DEFAULT_TERMINAL" = true ]; then
        echo "默认终端计划: 将备份并写入 x-terminal-emulator 与 xdg-terminals.list"
    else
        echo "默认终端计划: 保持原样（已显式传入 --no-default-terminal）"
    fi
    echo
    echo "检查完成。--check 模式没有安装、下载或修改任何内容。"
}

backup_file() {
    local file="$1"
    if [ -f "$file" ] || [ -L "$file" ]; then
        cp -a "$file" "${file}.bak.$(date +%Y%m%d%H%M%S)"
        echo "已备份: $file"
    fi
}

key_file_has_fingerprint() {
    # 按完整指纹核对，而不是只信一个易碰撞的短 key id。
    gpg --batch --show-keys --with-colons "$1" 2>/dev/null \
        | awk -F: -v want="$PPA_FINGERPRINT" '$1 == "fpr" && toupper($10) == want { found = 1 } END { exit !found }'
}

# ============================================
# 内联 Signed-By：deb822 格式的公钥不在 keyring 目录里
# ============================================
# Ubuntu 24.04 的 add-apt-repository 写的是 deb822 `.sources`，公钥直接
# 内联在 `Signed-By:` 字段里，**不会**在 /etc/apt/keyrings 或
# /etc/apt/trusted.gpg.d 里落盘。只翻那两个目录会恒假，把一次完全
# 正常的 PPA 安装误判成失败。
#
# deb822 续行规则：续行以单个空格开头（需去掉），单独的 `.` 表示空行。
extract_inline_key() {
    local sources_file="$1" out="$2"
    awk '
        /^Signed-By:/ { collecting = 1; sub(/^Signed-By:[[:space:]]*/, ""); if ($0 != "") print; next }
        collecting && /^[[:space:]]/ {
            line = substr($0, 2)
            if (line == ".") print ""; else print line
            next
        }
        collecting { collecting = 0 }
    ' "$sources_file" > "$out"
    [ -s "$out" ] && grep -q 'BEGIN PGP PUBLIC KEY BLOCK' "$out"
}

ppa_key_is_trusted() {
    local keyring sources_file tmp_key rc
    command -v gpg >/dev/null 2>&1 || return 1

    # 路径 1：独立 keyring 文件（旧格式、或手工配置的源）
    while IFS= read -r -d '' keyring; do
        key_file_has_fingerprint "$keyring" && return 0
    done < <(find /etc/apt/keyrings /etc/apt/trusted.gpg.d -maxdepth 1 -type f -print0 2>/dev/null)

    # 路径 2：deb822 `.sources` 里的内联公钥（Ubuntu 24.04 的实际行为）
    while IFS= read -r -d '' sources_file; do
        grep -q "$PPA_MARKER" "$sources_file" 2>/dev/null || continue
        tmp_key="$(mktemp)"
        rc=1
        if extract_inline_key "$sources_file" "$tmp_key"; then
            key_file_has_fingerprint "$tmp_key" && rc=0
        fi
        rm -f "$tmp_key"
        [ "$rc" -eq 0 ] && return 0
    done < <(find /etc/apt/sources.list.d -maxdepth 1 -type f -name '*.sources' -print0 2>/dev/null)

    return 1
}

install_from_ppa() {
    local rc=0

    # add-apt-repository 与后续 apt-get update/install 都会与持锁的
    # 后台 apt 活动冲突；先等它结束。本函数失败本就只降级不中止，
    # 等锁超时也按同一语义返回失败
    wait_for_apt_lock || { warn "等待 apt 锁超时，无法走 PPA 安装"; return 1; }

    echo "添加/刷新第三方 PPA: $PPA_NAME"
    "$SUDO_CMD" add-apt-repository -y "$PPA_NAME" || rc=$?
    if [ "$rc" -ne 0 ]; then
        warn "添加 PPA 失败（退出码 $rc）"
        return "$rc"
    fi

    if ! ppa_key_is_trusted; then
        warn "apt keyring 中未找到预期 PPA 完整指纹 $PPA_FINGERPRINT"
        return 1
    fi
    echo "OK: PPA 公钥完整指纹与预期一致"

    rc=0
    "$SUDO_CMD" apt-get update || rc=$?
    if [ "$rc" -ne 0 ]; then
        warn "apt 更新失败（退出码 $rc），可能是当前网络无法访问 Launchpad"
        return "$rc"
    fi

    rc=0
    "$SUDO_CMD" apt-get install -y ghostty || rc=$?
    if [ "$rc" -ne 0 ]; then
        warn "通过 PPA 安装 ghostty 失败（退出码 $rc）"
        return "$rc"
    fi

    echo -e "${GREEN}✓ 已通过 PPA 安装 Ghostty（PGP 签名链由 apt 校验）${NC}"
}

api_request() {
    local token="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
    # GitHub 未认证配额是 60 次/小时/IP，耗尽时返回 403——跟「无权限」同码。
    # 写出 http 状态码才能把「限流（等一会自愈）」和「真的访问不了」分开。
    local token_args=()
    [ -n "$token" ] && token_args+=(-H "Authorization: Bearer $token")
    curl --location --silent --show-error --connect-timeout 20 \
        -w '\n%{http_code}' "${token_args[@]}" "$GITHUB_API"
}

die_on_api_failure() {
    local code="$1"

    if [ "$code" = "403" ] || [ "$code" = "429" ]; then
        echo -e "${RED}错误: GitHub API 返回 $code，最常见原因是未认证配额（60 次/小时/IP）耗尽。${NC}" >&2
        echo -e "${YELLOW}  这不是权限问题，也不需要改配置，等配额重置后重跑即可。${NC}" >&2
        echo -e "${YELLOW}  查看剩余配额: curl -s https://api.github.com/rate_limit${NC}" >&2
        echo -e "${YELLOW}  或设置 GITHUB_TOKEN / GH_TOKEN 提高配额。${NC}" >&2
        echo -e "${YELLOW}  也可改走 PPA 路径（不经过 GitHub API）: ./install.sh --ppa-only${NC}" >&2
        exit 1
    fi
    die "读取 GitHub 最新发布信息失败（HTTP ${code:-无响应}）: $GITHUB_API"
}

fetch_deb_release() {
    local release_json release_info raw http_code
    raw="$(api_request)" || raw=''
    http_code="${raw##*$'\n'}"
    release_json="${raw%$'\n'*}"
    if [ "$http_code" != "200" ]; then
        die_on_api_failure "$http_code"
    fi

    # JSON 经 stdin 交给 Python，避免把整份发布元数据塞进环境变量而触及
    # execve 的环境大小上限；脚本本体另走文件描述符 3，互不争用 stdin。
    release_info="$(python3 /dev/fd/3 "$ARCH" "$UBUNTU_VERSION" <<< "$release_json" 3<<'PY'
import json
import re
import sys

arch, ubuntu_version = sys.argv[1:]
try:
    release = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError) as error:
    raise SystemExit(f"GitHub 发布信息不是有效 JSON: {error}")
if not isinstance(release, dict):
    raise SystemExit("GitHub 发布信息格式错误：顶层不是对象")
if release.get("draft") is not False or release.get("prerelease") is not False:
    raise SystemExit("GitHub latest 不是稳定发布（draft/prerelease）")
tag = release.get("tag_name")
if not isinstance(tag, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-ppa[0-9]+", tag):
    raise SystemExit(f"GitHub tag 格式不符合预期: {tag!r}")
assets = release.get("assets")
if not isinstance(assets, list):
    raise SystemExit("GitHub 发布信息格式错误：assets 不是数组")
pattern = re.compile(r"^ghostty_(.+)_(amd64|arm64)_([0-9]+\.[0-9]+)\.deb$")
matches = []
variants = []
for asset in assets:
    if not isinstance(asset, dict):
        continue
    name = asset.get("name")
    if not isinstance(name, str):
        continue
    parsed = pattern.fullmatch(name)
    if not parsed:
        continue
    variants.append(f"{parsed.group(2)} / Ubuntu {parsed.group(3)} ({name})")
    expected_asset_version = tag.replace("-ppa", ".ppa")
    if (parsed.group(1) == expected_asset_version
            and parsed.group(2) == arch
            and parsed.group(3) == ubuntu_version):
        matches.append(asset)
if len(matches) != 1:
    print(f"没有唯一匹配 {arch} + Ubuntu {ubuntu_version} 的 .deb。", file=sys.stderr)
    print("此发布可用变体：", file=sys.stderr)
    for variant in sorted(variants):
        print(f"  - {variant}", file=sys.stderr)
    raise SystemExit(2)
asset = matches[0]
name = asset["name"]
url = asset.get("browser_download_url")
expected_prefix = f"https://github.com/mkasberg/ghostty-ubuntu/releases/download/{tag}/"
if url != expected_prefix + name:
    raise SystemExit(f"匹配资产的下载 URL 不符合预期: {url!r}")
digest = asset.get("digest")
if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9A-Fa-f]{64}", digest):
    raise SystemExit(f"匹配资产缺少有效的 sha256 digest: {name}")
print(tag)
print(name)
print(url)
print(digest.split(":", 1)[1].lower())
PY
    )" || die "$release_info"

    mapfile -t RELEASE_FIELDS <<< "$release_info"
    [ "${#RELEASE_FIELDS[@]}" -eq 4 ] || die "GitHub 发布信息缺少完整的资产字段"
    RELEASE_TAG="${RELEASE_FIELDS[0]}"
    DEB_NAME="${RELEASE_FIELDS[1]}"
    DEB_URL="${RELEASE_FIELDS[2]}"
    DEB_SHA256="${RELEASE_FIELDS[3]}"
}

install_from_deb() {
    local stage rc=0

    fetch_deb_release
    stage="$(mktemp -d /tmp/ghostty-deb.XXXXXX)"
    # 把已展开且经过 shell 转义的临时目录写进 trap；若函数返回失败后
    # set -e 才触发 EXIT，此时局部变量已经离开作用域，不能再引用 $stage。
    printf -v CLEANUP_COMMAND 'rm -rf -- %q' "$stage"
    trap "$CLEANUP_COMMAND" EXIT

    echo "下载第三方社区包: $DEB_NAME（发布 $RELEASE_TAG）"
    curl --fail --location --silent --show-error --connect-timeout 20 \
        "$DEB_URL" -o "$stage/$DEB_NAME" || die "下载失败: $DEB_URL"

    if ! printf '%s  %s\n' "$DEB_SHA256" "$stage/$DEB_NAME" | sha256sum --check --status -; then
        die "SHA-256 核对失败，拒绝安装 $DEB_NAME"
    fi
    echo "OK: SHA-256 与 GitHub API digest 一致"

    # 不能用裸 dpkg -i：Ghostty 依赖 libonig5 等动态库，apt 安装本地
    # .deb 才会自动解析和补齐 Depends。
    (
        cd "$stage"
        "$SUDO_CMD" apt-get install -y "./$DEB_NAME"
    ) || rc=$?
    if [ "$rc" -ne 0 ]; then
        warn "安装 GitHub .deb 失败（退出码 $rc）"
        return "$rc"
    fi

    rm -rf "$stage"
    trap - EXIT
    echo -e "${GREEN}✓ 已通过 GitHub .deb 安装 Ghostty（SHA-256 已核对）${NC}"
}

# 已装且已是 apt 候选版本时，不必再跑安装流程。
# 重跑本脚本的常见目的是重置配置，而不是重装；若不短路，每次重跑
# 都会去打 GitHub API，撞上未认证限流就会把一次本可完成的重跑变成失败。
ghostty_already_current() {
    local installed candidate

    command -v ghostty >/dev/null 2>&1 || return 1
    installed="$(dpkg-query -W -f='${Version}' ghostty 2>/dev/null)" || return 1
    [ -n "$installed" ] || return 1

    candidate="$(apt_candidate)"
    # 候选为 (none) 说明当前 apt 源没有更新的版本可换，已装即最新可得。
    case "$candidate" in
        ''|'(none)') return 0 ;;
    esac
    [ "$installed" = "$candidate" ]
}

install_ghostty() {
    local rc=0

    if ghostty_already_current; then
        echo "Ghostty 已安装且已是当前 apt 源可得的最新版本: $(dpkg-query -W -f='${Version}' ghostty 2>/dev/null)"
        echo "跳过安装步骤（重跑仍会重置配置）。需强制重装请用 --ppa-only 或 --deb-only。"
        return 0
    fi

    case "$INSTALL_MODE" in
        ppa)
            install_from_ppa
            ;;
        deb)
            install_from_deb
            ;;
        auto)
            install_from_ppa || rc=$?
            if [ "$rc" -ne 0 ]; then
                echo
                warn "PPA 路径失败，改用 Mike Kasberg 的 GitHub .deb 回退路径"
                install_from_deb
            fi
            ;;
    esac
}

# WORKING_DIRECTORY 可能指向一个该机器上不存在的目录（例如英文 locale 装的
# 系统没有 ~/Documents，或 XDG 目录被改名）。Ghostty 拿到不存在的路径会
# 开不出终端，为一个“默认起始目录”搭上可用性不值得。这里先用
# xdg-user-dir 取真实的文档目录（它感知本地化名称），都不存在就退回 home。
resolved_working_directory() {
    local want="$WORKING_DIRECTORY" expanded xdg_doc

    case "$want" in
        home|inherit) printf '%s\n' "$want"; return 0 ;;
    esac

    expanded="${want/#\~/$HOME}"
    if [ -d "$expanded" ]; then
        printf '%s\n' "$want"
        return 0
    fi

    xdg_doc="$(xdg-user-dir DOCUMENTS 2>/dev/null || true)"
    if [ -n "$xdg_doc" ] && [ -d "$xdg_doc" ] && [ "$xdg_doc" != "$HOME" ]; then
        printf '%s\n' "$xdg_doc"
        return 0
    fi

    printf 'home\n'
}

render_config() {
    local paste_action="${1-$GHOSTTY_PASTE_ACTION}"
    echo "# 由 fresh-install/modules/ghostty/install.sh 生成与维护。"
    echo "# 重跑脚本会把本文件重置为基准内容；手工修改请在脚本里改。"
    font_exists "$FONT_FAMILY" && echo "font-family = $FONT_FAMILY"
    font_exists "$CJK_FONT_FAMILY" && echo "font-family = $CJK_FONT_FAMILY"
    echo "font-size = $FONT_SIZE"
    echo "theme = $THEME"
    echo "keybind = f11=toggle_fullscreen"

    # 把 Ctrl+Alt+←/→ 明确让给终端内的 tmux。
    # Ghostty 默认把它们绑给 goto_split:left/right；即使无 split 时当前版本
    # 看起来会透传，也不能把隐式 fallback 当成稳定合同。显式 unbind 后，
    # 按键一定编码后送入 pty，fresh-install/modules/tmux/tmux.conf.local 的 root 表
    # 再用 C-M-Left/Right 切 previous/next window。
    # 这不是“改绑 Ghostty 标签页”：Ghostty 标签页继续用上游默认
    # Ctrl+Tab / Ctrl+Shift+Tab；只把两个键的所有权交给 tmux。
    echo "keybind = ctrl+alt+arrow_left=unbind"
    echo "keybind = ctrl+alt+arrow_right=unbind"

    # 为 tmux 的 Ctrl+Alt+= / Ctrl+Alt++ 新建 window 绑定发送确定的 CSI-u
    # 序列。= 是 Unicode 61，+ 是 Unicode 43；修饰参数 7 表示 Shift+Alt+Ctrl。
    # 显式 csi 避免符号键在不同键盘布局或 Ghostty 编码路径下丢失修饰信息。
    echo "keybind = ctrl+alt+equal=csi:61;7u"
    echo "keybind = ctrl+alt+shift+equal=csi:43;7u"

    # SSH 异常断开时，远端 TUI 可能来不及关闭鼠标上报模式。Ghostty 无法
    # 从终端协议判断 SSH 已死亡，因此由 zsh hook 在 ssh 返回后的下一个
    # prompt 发出针对常见鼠标协议的 DECRST；Ctrl+Shift+R 是所有 shell
    # 和非交互式 SSH 启动方式都能使用的 Ghostty 原生 reset 兜底。
    echo "keybind = ctrl+shift+r=reset"

    # 鼠标拖选的所有权：Pi 全屏、tmux `mouse on` 都会申请鼠标上报，此时
    # 普通拖选被程序接管（Pi 自己画选区并在释放时复制），Ghostty 没有本地
    # 选区，Ctrl+Shift+C 就无可复制。never 的文档语义是「按住 Shift 的鼠标
    # 事件永远不按鼠标协议上报，而是扩展终端本地选区」，也就是把 Shift+拖选
    # 稳定留给 Ghostty；与上游默认 false 不同，程序不能再用 XTSHIFTESCAPE
    # 覆盖它。注意：这保障的是 Shift+拖选，不是普通拖选——程序申请了鼠标
    # 上报时，普通拖选仍属于程序。
    echo "mouse-shift-capture = never"

    # Ctrl+Shift+C 把 Ghostty 本地选区写进本机系统剪贴板。mixed 同时提供
    # 纯文本与带样式两种格式。Ctrl+Shift+V 发送原始 Ctrl+V (0x16)，绕过
    # Ghostty 可能陈旧的 GTK 粘贴视图；在 Pi 内由现有图片/文本粘贴动作处理。
    # 在本地 Wayland zsh 提示符由本模块 ZLE widget 读取当前剪贴板；
    # Pi/TUI 的原始按键输入不经过 zsh 的行编辑器。
    echo "keybind = ctrl+shift+c=copy_to_clipboard:mixed"
    echo "keybind = ctrl+shift+v=$paste_action"

    # 剪贴板策略：允许 OSC 52 写入，拒绝 OSC 52 读取。
    # 写入是远端复制路径的关键一环：远端 tmux 复制后发出的 OSC 52 经
    # SSH 到达本机 Ghostty，由它落到本机系统剪贴板。写入端是终端本身，
    # 不需要远端有 Wayland / X11 会话或任何剪贴板工具，也与本机跑在
    # Wayland 还是 X11 无关。
    # 拒绝读取：读取会让终端里的程序把本机剪贴板内容取走。右键或
    # Shift+Insert 仍可走 Ghostty 本地粘贴；Ctrl+Shift+V 是上面的原始
    # Ctrl+V 输入，Pi 再按自己已有的粘贴路径读取，不经过 OSC 52 查询。
    echo "clipboard-write = allow"
    echo "clipboard-read = deny"

    # working-directory 只决定没有可继承窗口时的默认目录。Ghostty 的
    # window-inherit-working-directory 默认为 true，且优先级更高：同一进程内
    # 新建窗口会继承该进程里焦点窗口的目录，导致新窗口长期黏在某个项目目录。
    # 明确关闭“新窗口继承”，让每个新窗口都从 WORKING_DIRECTORY 启动。
    # tab/split 仍保留上游默认继承，方便在同一项目中继续工作。
    echo "working-directory = $(resolved_working_directory)"
    echo "window-inherit-working-directory = false"

    # 每个外部启动都用独立进程：一个 Ghostty 进程崩溃只带走它自己那个窗口，
    # 同一进程内的窗口、tab、split 仍共享进程与生命周期。应用菜单那条路径由
    # 用户级 desktop 覆盖显式传 --gtk-single-instance=false
    # （见 render_desktop_override）；Ctrl+Alt+T 的包装脚本不带 CLI 参数，走的
    # 就是这里：命令行 --gtk-single-instance 的默认值 detect 只在配置值仍为
    # detect 时才生效，显式写 false 后不再参与 detect 判断。
    echo "# 独立外部启动使用独立进程；内部窗口、tab 和 split 仍可能共享进程。"
    echo "gtk-single-instance = false"
    [ -n "$SHELL_INTEGRATION_FEATURES" ] && \
        echo "shell-integration-features = $SHELL_INTEGRATION_FEATURES"
}

# 现有受管配置的单键迁移。完整安装会备份并重置任意配置、执行 apt 等步骤，
# 因此这里独立核对旧渲染的逐字节基准，仅替换这一行；已是新基准不动 inode。
# 字体/起始目录若自安装以来变化，基准不再匹配，明确拒绝而非重置其它行。
apply_paste_keybind_only() {
    local command_name tmp
    for command_name in cmp mktemp chmod mv rm sed; do require_command "$command_name"; done
    [ -d "$CONFIG_DIR" ] && [ ! -L "$CONFIG_DIR" ] \
        || die "Ghostty 配置目录缺失或是符号链接，拒绝窄迁移: $CONFIG_DIR"
    [ ! -L "$CONFIG_FILE" ] && [ -f "$CONFIG_FILE" ] \
        || die "Ghostty 配置缺失、是符号链接或不是普通文件，拒绝窄迁移: $CONFIG_FILE"
    if cmp -s "$CONFIG_FILE" <(render_config); then
        echo "配置按键已是当前基准，跳过写入: $CONFIG_FILE"
        return 0
    fi
    cmp -s "$CONFIG_FILE" <(render_config paste_from_clipboard) \
        || die "Ghostty 配置不等于旧版受管基准，拒绝覆盖用户内容: $CONFIG_FILE"

    tmp="$(mktemp "$CONFIG_FILE.tmp.XXXXXX")"
    # 从已核对的旧文件替换单行，避免生成期间字体/目录环境变化改动其它行。
    if ! sed 's|^keybind = ctrl+shift+v=paste_from_clipboard$|keybind = ctrl+shift+v=text:\\x16|' \
        "$CONFIG_FILE" > "$tmp" || ! chmod --reference="$CONFIG_FILE" "$tmp"; then
        rm -f -- "$tmp"
        die "无法生成新的 Ghostty 配置: $CONFIG_FILE"
    fi
    # 防止比较和原子替换之间已有别的写入；绝不根据过期的旧内容覆盖它。
    if [ -L "$CONFIG_FILE" ] || ! cmp -s "$CONFIG_FILE" <(render_config paste_from_clipboard); then
        rm -f -- "$tmp"
        die "Ghostty 配置在迁移期间变化，拒绝覆盖: $CONFIG_FILE"
    fi
    if ! mv -f -- "$tmp" "$CONFIG_FILE"; then
        rm -f -- "$tmp"
        die "无法替换 Ghostty 配置: $CONFIG_FILE"
    fi
    echo "已将全局 Ctrl+Shift+V 改为原始 Ctrl+V: $CONFIG_FILE"
}

write_config() {
    local tmp
    mkdir -p "$CONFIG_DIR"

    # 0 字节旧文件也必须备份：它虽然不能被 Ghostty 使用，却仍是用户
    # 先前实验留下的事实。配置只在同目录临时文件完整生成后原子替换。
    tmp="$(mktemp "$CONFIG_FILE.tmp.XXXXXX")"
    render_config > "$tmp"

    if [ -f "$CONFIG_FILE" ] && cmp -s "$tmp" "$CONFIG_FILE"; then
        rm -f "$tmp"
        echo "配置文件无变化，跳过写入: $CONFIG_FILE"
        return 0
    fi

    backup_file "$CONFIG_FILE"
    chmod --reference="$CONFIG_FILE" "$tmp" 2>/dev/null || chmod 644 "$tmp"
    mv -f "$tmp" "$CONFIG_FILE"
    echo "已写入: $CONFIG_FILE"
}

verify_config() {
    local expected actual help_output validate_log rc=0

    [ -f "$CONFIG_FILE" ] || die "配置文件写入后不存在: $CONFIG_FILE"
    expected="$(render_config)"
    actual="$(cat "$CONFIG_FILE")"
    [ "$actual" = "$expected" ] || die "配置文件回读内容与基准内容不一致"
    echo "OK: 配置文件回读与基准内容一致"

    validate_log="$(mktemp /tmp/ghostty-config-validate.XXXXXX)"
    help_output="$("$GHOSTTY_BIN" --help 2>&1 || true)"
    if grep -Fq '+validate-config' <<< "$help_output"; then
        "$GHOSTTY_BIN" +validate-config >"$validate_log" 2>&1 || rc=$?
        VALIDATE_ACTION="$GHOSTTY_BIN +validate-config"
    else
        "$GHOSTTY_BIN" +show-config >"$validate_log" 2>&1 || rc=$?
        VALIDATE_ACTION="$GHOSTTY_BIN +show-config"
    fi
    if [ "$rc" -ne 0 ]; then
        cat "$validate_log" >&2
        rm -f "$validate_log"
        die "$VALIDATE_ACTION 未通过（退出码 $rc），配置可能有解析错误"
    fi
    rm -f "$validate_log"
    echo "OK: $VALIDATE_ACTION 已通过"
}

write_atomic_text() {
    local target="$1" content="$2" mode="$3" tmp
    mkdir -p "$(dirname "$target")"
    tmp="$(mktemp "$target.tmp.XXXXXX")"
    printf '%s' "$content" > "$tmp"
    chmod "$mode" "$tmp"
    if [ -f "$target" ] && cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
        echo "文件无变化，跳过: $target"
        return 0
    fi
    backup_file "$target"
    mv -f "$tmp" "$target"
    echo "已写入: $target"
}

write_atomic_file_from_source() {
    local target="$1" source="$2" mode="$3" tmp
    mkdir -p "$(dirname "$target")"
    tmp="$(mktemp "$target.tmp.XXXXXX")"
    if ! cat "$source" > "$tmp" || ! chmod "$mode" "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if [ -f "$target" ] && cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
        echo "文件无变化，跳过: $target"
        return 0
    fi
    backup_file "$target"
    mv -f "$tmp" "$target"
    echo "已写入: $target"
}

write_atomic_file_keep_mode() {
    # 原子安装一个已经生成好的临时文件；用于保留用户文件的原始字节，
    # 避免命令替换吞掉 .zshrc 末尾换行。
    local target="$1" tmp="$2" default_mode="$3"
    if [ -f "$target" ]; then
        chmod --reference="$target" "$tmp" 2>/dev/null || chmod "$default_mode" "$tmp"
    else
        chmod "$default_mode" "$tmp"
    fi
    if [ -f "$target" ] && cmp -s "$tmp" "$target"; then
        rm -f "$tmp"
        echo "文件无变化，跳过: $target"
        return 0
    fi
    backup_file "$target"
    mv -f "$tmp" "$target"
    echo "已写入: $target"
}

# ============================================
# Ghostty 原生 Wayland 发送背压修复：仅一个安装入口
# ============================================
# GTK 4.14.5 在 wl_display_flush() 遇 EAGAIN 时调用 _exit(1)。库在更低的
# sendmsg 边界等待短时可写，防止 libwayland 的 4096B 环在 marshal 阶段先锁存
# EAGAIN。只编译用户级库；系统 GTK/Ghostty 二进制与显示后端均不变。
ensure_backpressure_compiler() {
    if command -v cc >/dev/null 2>&1; then return 0; fi
    echo "缺少 C 编译器，安装 Wayland 背压修复所需的 build-essential …"
    wait_for_apt_lock 600 || die "APT 锁等待超时，无法安装 build-essential"
    "$SUDO_CMD" apt-get install -y build-essential \
        || die "build-essential 安装失败，拒绝安装不受保护的 Ghostty 启动入口"
    command -v cc >/dev/null 2>&1 || die "build-essential 已安装，但仍未找到 cc"
}

render_backpressure_launcher() {
    cat <<'EOF'
#!/bin/sh
# 由 fresh-install/modules/ghostty/install.sh 生成与维护。
if [ -n "${WAYLAND_DISPLAY:-}" ]; then
    shim="$HOME/.local/lib/ghostty/libwayland-backpressure.so"
    [ -r "$shim" ] || { printf 'Ghostty Wayland backpressure shim missing: %s\n' "$shim" >&2; exit 1; }
    cmp -s "$shim" "$HOME/.config/ghostty/wayland-backpressure/enabled.library" || {
        printf 'Ghostty Wayland backpressure shim changed or state missing: %s\n' "$shim" >&2; exit 1;
    }
    LD_PRELOAD="$shim${LD_PRELOAD:+:$LD_PRELOAD}"
    export LD_PRELOAD
fi
exec /usr/bin/ghostty "$@"
EOF
}

# 2026-09-28 短暂启用过独立脚本，只有与旧脚本的精确输出一致才迁移；
# 其余已有 ~/.local/bin/ghostty 是用户内容，明确拒绝覆盖。
render_legacy_backpressure_launcher() {
    render_backpressure_launcher | sed \
        's|^# 由 fresh-install/modules/ghostty/install.sh 生成与维护。$|# 由 quick-deploy Ghostty Wayland EAGAIN workaround 管理。|'
}

backpressure_state() {
    if [ ! -e "$USER_GHOSTTY_LAUNCHER" ] && [ ! -e "$BACKPRESSURE_LIBRARY" ]; then
        echo "未安装（正式安装将构建库并生成启动器）"
    elif [ -f "$USER_GHOSTTY_LAUNCHER" ] && [ -f "$BACKPRESSURE_LIBRARY" ] &&
         [ -f "$BACKPRESSURE_STATE/enabled.library" ] &&
         cmp -s "$BACKPRESSURE_LIBRARY" "$BACKPRESSURE_STATE/enabled.library" &&
         cmp -s "$USER_GHOSTTY_LAUNCHER" <(render_backpressure_launcher); then
        echo "已是当前基准（原生 Wayland，有界 EAGAIN 重试）"
    elif [ -f "$USER_GHOSTTY_LAUNCHER" ] &&
         cmp -s "$USER_GHOSTTY_LAUNCHER" <(render_legacy_backpressure_launcher); then
        echo "旧独立脚本的托管启动器（正式安装将原位迁移）"
    else
        echo "存在非基准文件（正式安装将拒绝覆盖）"
    fi
}

install_backpressure() (
    set -e
    [ -f "$BACKPRESSURE_SOURCE" ] || die "Wayland 背压修复源码缺失: $BACKPRESSURE_SOURCE"
    [ "$HOME" = "${HOME// /}" ] || die "HOME 含空格，desktop Exec 无法安全呈现"
    [ ! -L "$USER_GHOSTTY_LAUNCHER" ] || die "Ghostty 启动器是符号链接，拒绝覆盖"
    if [ -e "$USER_GHOSTTY_LAUNCHER" ]; then
        [ -f "$USER_GHOSTTY_LAUNCHER" ] || die "Ghostty 启动器不是普通文件，拒绝覆盖"
        if ! cmp -s "$USER_GHOSTTY_LAUNCHER" <(render_backpressure_launcher) &&
           ! cmp -s "$USER_GHOSTTY_LAUNCHER" <(render_legacy_backpressure_launcher); then
            die "用户已有自定义 Ghostty 启动器，拒绝覆盖: $USER_GHOSTTY_LAUNCHER"
        fi
    fi
    [ ! -L "$BACKPRESSURE_LIBRARY" ] || die "Wayland 背压库是符号链接，拒绝覆盖"
    [ ! -e "$BACKPRESSURE_LIBRARY" ] || [ -f "$BACKPRESSURE_LIBRARY" ] \
        || die "Wayland 背压库不是普通文件，拒绝覆盖"
    [ ! -L "$BACKPRESSURE_STATE" ] || die "Wayland 背压状态目录是符号链接，拒绝写入"
    [ ! -e "$BACKPRESSURE_STATE" ] || [ -d "$BACKPRESSURE_STATE" ] \
        || die "Wayland 背压状态路径不是目录，拒绝写入"
    ensure_backpressure_compiler
    local scratch
    scratch="$(mktemp -d /tmp/ghostty-wayland-backpressure.XXXXXX)"
    trap 'rm -rf -- "$scratch"' EXIT
    cc -shared -fPIC -O2 -Wall -Wextra -Werror -o "$scratch/library.so" "$BACKPRESSURE_SOURCE" -ldl -pthread \
        || die "Wayland 背压库编译失败，拒绝安装未受保护的入口"
    mkdir -p "$(dirname "$BACKPRESSURE_LIBRARY")" "$BACKPRESSURE_STATE"
    write_atomic_file_from_source "$BACKPRESSURE_STATE/enabled.library" "$scratch/library.so" 644
    write_atomic_file_from_source "$BACKPRESSURE_LIBRARY" "$scratch/library.so" 644
    write_atomic_text "$USER_GHOSTTY_LAUNCHER" "$(render_backpressure_launcher)"$'\n' 755
)

verify_backpressure() {
    [ -f "$BACKPRESSURE_LIBRARY" ] && [ -f "$BACKPRESSURE_STATE/enabled.library" ] \
        || die "Wayland 背压库或校验副本缺失"
    cmp -s "$BACKPRESSURE_LIBRARY" "$BACKPRESSURE_STATE/enabled.library" \
        || die "Wayland 背压库与校验副本不一致"
    [ -x "$USER_GHOSTTY_LAUNCHER" ] &&
        cmp -s "$USER_GHOSTTY_LAUNCHER" <(render_backpressure_launcher) \
        || die "Ghostty 启动器未完整加载 Wayland 修复库"
    echo "OK: 原生 Wayland 背压修复库与启动器已托管（新启动的窗口生效）"
}

# 专用入口保留兼容：全局基准现已发送同样的 Ctrl+V 原始字节；CLI 选项
# 对旧配置启动的窗口也生效。仍须经过用户级启动器保留 Wayland 背压保护，
# -e 及后续参数原样传给 Ghostty；普通启动器/desktop 不被专用入口改写。
render_pi_launcher() {
    cat <<'EOF'
#!/bin/sh
# 由 fresh-install/modules/ghostty/install.sh 生成与维护。
exec "$HOME/.local/bin/ghostty" --gtk-single-instance=false '--keybind=ctrl+shift+v=text:\x16' "$@"
EOF
}

pi_launcher_state() {
    if [ ! -e "$USER_PI_GHOSTTY_LAUNCHER" ] && [ ! -L "$USER_PI_GHOSTTY_LAUNCHER" ]; then
        echo "未安装（正式安装将生成）"
    elif [ -L "$USER_PI_GHOSTTY_LAUNCHER" ]; then
        echo "符号链接（正式安装将拒绝覆盖）"
    elif [ ! -f "$USER_PI_GHOSTTY_LAUNCHER" ]; then
        echo "不是普通文件（正式安装将拒绝覆盖）"
    elif cmp -s "$USER_PI_GHOSTTY_LAUNCHER" <(render_pi_launcher); then
        if [ -x "$USER_PI_GHOSTTY_LAUNCHER" ]; then
            echo "已是当前基准"
        else
            echo "内容相同但不可执行（正式安装将拒绝覆盖）"
        fi
    else
        echo "含非基准内容（正式安装将拒绝覆盖）"
    fi
}

install_pi_launcher() {
    [ ! -L "$USER_PI_GHOSTTY_LAUNCHER" ] || die "Pi 专用 Ghostty 启动器是符号链接，拒绝覆盖: $USER_PI_GHOSTTY_LAUNCHER"
    if [ -e "$USER_PI_GHOSTTY_LAUNCHER" ]; then
        [ -f "$USER_PI_GHOSTTY_LAUNCHER" ] || die "Pi 专用 Ghostty 启动器不是普通文件，拒绝覆盖: $USER_PI_GHOSTTY_LAUNCHER"
        cmp -s "$USER_PI_GHOSTTY_LAUNCHER" <(render_pi_launcher) \
            || die "用户已有自定义 Pi 专用 Ghostty 启动器，拒绝覆盖: $USER_PI_GHOSTTY_LAUNCHER"
        [ -x "$USER_PI_GHOSTTY_LAUNCHER" ] \
            || die "Pi 专用 Ghostty 启动器内容相同但不可执行，拒绝覆盖: $USER_PI_GHOSTTY_LAUNCHER"
    fi
    write_atomic_text "$USER_PI_GHOSTTY_LAUNCHER" "$(render_pi_launcher)"$'\n' 755
}

verify_pi_launcher() {
    [ -x "$USER_PI_GHOSTTY_LAUNCHER" ] &&
        cmp -s "$USER_PI_GHOSTTY_LAUNCHER" <(render_pi_launcher) \
        || die "Pi 专用 Ghostty 启动器不是可执行的受管基准: $USER_PI_GHOSTTY_LAUNCHER"
    echo "OK: Pi 专用启动器经受保护的 Ghostty 启动器新开独立进程"
}

# 窄安装只核对现有受保护路径，既不重建库也不加载完整安装预检。
# 受保护启动器内容、可执行性以及库/校验副本均须保持原来的受管基准。
verify_pi_launcher_prerequisites() {
    local command_name
    for command_name in cat cmp mktemp chmod mkdir mv rm cp date dirname; do
        require_command "$command_name"
    done
    [ -x "$GHOSTTY_BIN" ] || die "Pi 专用启动器依赖已安装的 Ghostty: $GHOSTTY_BIN"
    [ -f "$USER_GHOSTTY_LAUNCHER" ] && [ ! -L "$USER_GHOSTTY_LAUNCHER" ] \
        || die "受保护的 Ghostty 启动器缺失或不是受管普通文件: $USER_GHOSTTY_LAUNCHER"
    [ -f "$BACKPRESSURE_LIBRARY" ] && [ ! -L "$BACKPRESSURE_LIBRARY" ] \
        || die "Wayland 背压库缺失或不是受管普通文件: $BACKPRESSURE_LIBRARY"
    [ -d "$BACKPRESSURE_STATE" ] && [ ! -L "$BACKPRESSURE_STATE" ] &&
        [ -f "$BACKPRESSURE_STATE/enabled.library" ] && [ ! -L "$BACKPRESSURE_STATE/enabled.library" ] \
        || die "Wayland 背压校验副本缺失或不是受管普通文件: $BACKPRESSURE_STATE/enabled.library"
    verify_backpressure
}

# ============================================
# 用户级 desktop 覆盖：外部启动不复用已有进程
# ============================================
# 软件包的 $DESKTOP_FILE 在两个启动入口把 --gtk-single-instance=true 写死在
# Exec= 里。CLI 参数优先于配置文件，所以只写 gtk-single-instance = false 还
# 不够：从应用菜单启动仍会复用已有进程，崩溃时一起被带走。
#
# 用户级 desktop 用同一个 ID 覆盖：XDG 按 XDG_DATA_HOME → XDG_DATA_DIRS
# 顺序查找，~/.local/share/applications 先于 /usr/share/applications 命中，
# 系统文件不动。覆盖内容 = 系统模板逐行继承，只改启动语义：
#   1. 两个 Exec= 从 /usr/bin/ghostty 改为托管的用户级 Wayland 启动器，
#      同时把 --gtk-single-instance=true 改为 false；
#   2. DBusActivatable=true → false，保证桌面不绕过 Exec=。
# DBusActivatable=false 不可省：true 时桌面外壳可以走 D-Bus 激活
# （系统里确实装着 com.mitchellh.ghostty.service），那条路径根本不会使用
# Exec= 里的参数，必须显式关掉才能保证入口按 Exec 启动。
#
# Ctrl+Alt+T 走本模块写入的包装脚本（exec "$HOME/.local/bin/ghostty" "$@"），
# 最终通过同一个托管启动器，没有 CLI 参数，因此读的是配置文件里的 false。
# 实测：配置写 true 时，即使命令行带
# 参数（detect 本该判为单实例），解析结果仍是 true——显式配置值不参与
# detect 判断（ghostty +show-config，本机 1.3.1~ppa2-noble1）。
#
# 覆盖范围要说清：一个外部启动一个进程、一个进程一个崩溃域；同一进程内的
# 窗口、tab、split 仍然同生共死。

# 生成用户级覆盖内容：逐行继承系统模板，只改启动语义。
# 模板结构与预期不符（认不出 /usr/bin/ghostty 的 Exec=，或仍残留
# --gtk-single-instance=true、DBusActivatable=true）时退出非零，由调用方明确
# 失败：宁可报错，也不写一个“看起来成功、实际仍复用进程”的覆盖。
render_desktop_override() {
    local launcher="${1:-$USER_GHOSTTY_LAUNCHER}"
    awk -v launcher="$launcher" '
        BEGIN { bin = "Exec=/usr/bin/ghostty" }
        $0 ~ "^" bin "([[:space:]]|$)" {
            if (match($0, /--gtk-single-instance=[a-z]+/)) {
                $0 = substr($0, 1, RSTART - 1) "--gtk-single-instance=false" substr($0, RSTART + RLENGTH)
            } else {
                $0 = bin " --gtk-single-instance=false" substr($0, length(bin) + 1)
            }
            $0 = "Exec=" launcher substr($0, length(bin) + 1)
            exec_lines++
        }
        /^DBusActivatable=/ { $0 = "DBusActivatable=false" }
        {
            if ($0 ~ /--gtk-single-instance=true/ || $0 ~ /^DBusActivatable=true$/) leftover = 1
            print
        }
        END {
            if (leftover || exec_lines == 0) {
                print "系统 desktop 模板结构不符合预期：认不出 /usr/bin/ghostty 的 Exec=，或仍残留强制复用已有进程的入口" > "/dev/stderr"
                exit 1
            }
        }
    ' "$DESKTOP_FILE"
}

# 现有用户级覆盖只接受不存在、新基准、旧模块独立进程基准、与厂商
# 模板等价的旧副本；每一种都要逐字节证实，用户自有改动一律拒绝。
desktop_override_replaceable() {
    if [ ! -e "$USER_DESKTOP_FILE" ] && [ ! -L "$USER_DESKTOP_FILE" ]; then
        return 0
    fi
    [ -f "$USER_DESKTOP_FILE" ] && [ ! -L "$USER_DESKTOP_FILE" ] || return 1
    [ -f "$DESKTOP_FILE" ] || return 1
    # 本次基准、此前 install.sh 的独立进程基准、厂商模板的旧等价副本：
    # 仅这些完全可验证的来源可自动迁移；其它内容视为用户自有改动。
    render_desktop_override | cmp -s - "$USER_DESKTOP_FILE" && return 0
    render_desktop_override "$GHOSTTY_BIN" | cmp -s - "$USER_DESKTOP_FILE" && return 0
    sed -e 's|--gtk-single-instance=false|--gtk-single-instance=true|' \
        -e 's|^DBusActivatable=false$|DBusActivatable=true|' \
        "$USER_DESKTOP_FILE" | cmp -s - "$DESKTOP_FILE"
}

desktop_override_state() {
    # 模板问题优先报：--check 要说清“正式安装会不会成功”，而不是只描述现状。
    if [ ! -f "$DESKTOP_FILE" ]; then
        printf '系统模板 %s 缺失，无法生成覆盖\n' "$DESKTOP_FILE"
    elif ! render_desktop_override >/dev/null 2>&1; then
        printf '系统模板 %s 结构不符合预期（正式安装将拒绝生成覆盖）\n' "$DESKTOP_FILE"
    elif [ ! -e "$USER_DESKTOP_FILE" ] && [ ! -L "$USER_DESKTOP_FILE" ]; then
        printf '不存在（正式安装将按系统模板生成）\n'
    elif [ -L "$USER_DESKTOP_FILE" ]; then
        printf '符号链接（正式安装将拒绝覆盖）\n'
    elif [ ! -f "$USER_DESKTOP_FILE" ]; then
        printf '存在，但不是普通文件（正式安装将拒绝覆盖）\n'
    elif desktop_override_replaceable; then
        if render_desktop_override | cmp -s - "$USER_DESKTOP_FILE"; then
            printf '已是本次基准内容（两个 Exec 均使用 Wayland 启动器、独立进程）\n'
        else
            printf '旧模块基准或等价厂商副本（正式安装将备份并迁移到 Wayland 启动器）\n'
        fi
    else
        printf '含本模块之外的内容（正式安装将拒绝覆盖）\n'
    fi
}

# 原子写法与配置文件一致：同目录临时文件完整生成、内容变化前先备份、mv 替换。
install_desktop_override() {
    local tmp

    [ -f "$DESKTOP_FILE" ] || die "系统桌面入口不存在，无法生成独立进程覆盖: $DESKTOP_FILE"

    if [ -L "$USER_DESKTOP_FILE" ]; then
        die "$USER_DESKTOP_FILE 是符号链接，拒绝覆盖；外部启动仍会复用已有进程"
    fi
    if [ -e "$USER_DESKTOP_FILE" ] && [ ! -f "$USER_DESKTOP_FILE" ]; then
        die "$USER_DESKTOP_FILE 不是普通文件，拒绝覆盖；外部启动仍会复用已有进程"
    fi
    if ! desktop_override_replaceable; then
        die "用户级桌面覆盖含本模块之外的内容，拒绝覆盖: $USER_DESKTOP_FILE
  只接受不存在、新基准、旧模块基准或等价的厂商副本。
  先看差异: diff '$USER_DESKTOP_FILE' '$DESKTOP_FILE'
  确认差异可以丢弃后，移开该文件再重跑: mv '$USER_DESKTOP_FILE' '$USER_DESKTOP_FILE.manual'"
    fi

    mkdir -p "$USER_DESKTOP_DIR"
    tmp="$(mktemp "$USER_DESKTOP_FILE.tmp.XXXXXX")"
    if ! render_desktop_override > "$tmp"; then
        rm -f "$tmp"
        die "系统桌面入口结构不符合预期，拒绝生成独立进程覆盖: $DESKTOP_FILE"
    fi
    write_atomic_file_keep_mode "$USER_DESKTOP_FILE" "$tmp" 644

    # 桌面数据库只是查找缓存，刷新失败不影响覆盖是否生效，所以不参与成败
    # 判定；也不是所有环境都装了 update-desktop-database。
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$USER_DESKTOP_DIR" >/dev/null 2>&1 \
            || warn "update-desktop-database 未成功；桌面入口仍按文件生效"
    fi
}

verify_desktop_override() {
    local expected expected_execs

    [ -f "$USER_DESKTOP_FILE" ] || die "用户级桌面覆盖写入后不存在: $USER_DESKTOP_FILE"
    expected="$(render_desktop_override)" || die "系统桌面入口结构不符合预期: $DESKTOP_FILE"
    [ "$(cat "$USER_DESKTOP_FILE")" = "$expected" ] \
        || die "用户级桌面覆盖回读内容与系统模板生成的基准内容不一致"

    expected_execs="$(grep -Ec '^Exec=/usr/bin/ghostty' "$DESKTOP_FILE")"
    [ "$(grep -Ec "^Exec=$USER_GHOSTTY_LAUNCHER --gtk-single-instance=false" "$USER_DESKTOP_FILE")" -eq "$expected_execs" ] \
        || die "用户级桌面覆盖没有让每个 ghostty 入口使用 Wayland 启动器与独立进程"
    ! grep -Fq -- '--gtk-single-instance=true' "$USER_DESKTOP_FILE" \
        || die "用户级桌面覆盖仍残留 --gtk-single-instance=true"
    ! grep -Fxq 'DBusActivatable=true' "$USER_DESKTOP_FILE" \
        || die "用户级桌面覆盖的 DBusActivatable 仍为 true，D-Bus 激活会绕过 Exec= 参数"

    echo "OK: 用户级 desktop 覆盖已就位（$expected_execs 个 Exec 入口均为 --gtk-single-instance=false，DBusActivatable=false）"
}

# ============================================
# SSH 异常断开后的鼠标状态自愈
# ============================================
# 远端 tmux/vim/htop 等程序通过 DECSET 打开鼠标上报后，SSH 若因断网、
# 超时或进程被杀而结束，负责发送 DECRST 的程序已经不存在。Ghostty 不
# 能知道哪个 SSH 通道拥有当前终端状态，于是下一次点击会把 SGR 鼠标报文
# 送进本地 zsh，显示为 0;列;行M/m。hook 只在交互式 ssh 命令回到 prompt
# 时清理，既保留活动中的 tmux/TUI 鼠标功能，也不碰其它键盘/终端模式。

# zsh 代码只有这一个仓库来源；install.sh 写出运行时 hook。
render_ssh_mouse_hook() {
    cat <<'ZSH_MOUSE_HOOK'
# SSH mouse-state cleanup for Ghostty's local zsh.
# Installed and maintained by fresh-install/modules/ghostty/install.sh.
#
# A remote TUI can leave DEC mouse-tracking modes enabled when an SSH channel
# dies before the TUI sends its cleanup sequence.  Ghostty cannot associate
# terminal modes with an SSH process, so the surviving shell clears the common
# modes when an interactive ssh command returns to its prompt.

# This file may be sourced from a shared zsh startup file, including on remote
# hosts. Keep it inert unless Ghostty supplied its child-shell resource path.
[[ -o interactive ]] || return 0
# Ghostty exports this resource path to its child shell. tmux may replace
# TERM_PROGRAM with `tmux`, so the resource path is the stable marker.
[[ -n ${GHOSTTY_RESOURCES_DIR:-} && -d ${GHOSTTY_RESOURCES_DIR:-} ]] || return 0
if (( ! ${+_quick_deploy_ghostty_ssh_mouse_loaded} )); then
    typeset -g _quick_deploy_ghostty_ssh_mouse_loaded=1
    typeset -gi _quick_deploy_ghostty_ssh_mouse_pending=0
fi

_quick_deploy_ghostty_ssh_is_assignment() {
    [[ "$1" =~ '^[[:alpha:]_][[:alnum:]_]*=' ]]
}

_quick_deploy_ghostty_ssh_preexec() {
    _quick_deploy_ghostty_ssh_mouse_pending=0
    local command="${1##[[:space:]]#}"
    local -a words
    setopt localoptions noglob
    words=("${(@z)command}")

    local i=1 word
    while (( i <= ${#words} )); do
        word="${words[i]}"

        case "$word" in
            ';'|'&&'|'||'|'|'|'&')
                return 0
                ;;
        esac

        if _quick_deploy_ghostty_ssh_is_assignment "$word"; then
            i=$((i + 1))
            continue
        fi

        case "$word" in
            command|builtin|noglob)
                i=$((i + 1))
                continue
                ;;
            env)
                i=$((i + 1))
                while (( i <= ${#words} )); do
                    word="${words[i]}"
                    case "$word" in
                        --|'')
                            i=$((i + 1))
                            break
                            ;;
                        -u|--unset|-C|--chdir)
                            i=$((i + 2))
                            ;;
                        -*|[[:alpha:]_][[:alnum:]_]*=*)
                            i=$((i + 1))
                            ;;
                        *)
                            break
                            ;;
                    esac
                done
                continue
                ;;
            sudo)
                i=$((i + 1))
                while (( i <= ${#words} )); do
                    word="${words[i]}"
                    case "$word" in
                        --|'')
                            i=$((i + 1))
                            break
                            ;;
                        -u|-g|-C|-R|-r|-t|-p|-h|--user|--group|--chdir|--role|--type|--prompt|--host|--close-from)
                            i=$((i + 2))
                            ;;
                        -*|[[:alpha:]_][[:alnum:]_]*=*)
                            i=$((i + 1))
                            ;;
                        *)
                            break
                            ;;
                    esac
                done
                continue
                ;;
        esac

        if [[ "${word##*/}" == ssh ]]; then
            _quick_deploy_ghostty_ssh_mouse_pending=1
        fi
        return 0
    done

    return 0
}

_quick_deploy_ghostty_ssh_precmd() {
    local _quick_deploy_status=$?
    if (( _quick_deploy_ghostty_ssh_mouse_pending )); then
        _quick_deploy_ghostty_ssh_mouse_pending=0

        # tmux parses pane output and continues to own its outer mouse mode.
        # GNU screen's ownership is not assumed, so skip it conservatively.
        if [[ -z ${STY:-} && -t 1 && ${TERM:-} != dumb ]]; then
            # 1000/1001/1002/1003 cover click, highlight, button-event, and
            # any-event tracking. 1005/1006/1015/1016 cover common encodings.
            # Do not reset bracketed paste, focus reporting, or keyboard modes.
            printf '\033[?1000l\033[?1001l\033[?1002l\033[?1003l\033[?1005l\033[?1006l\033[?1015l\033[?1016l' || :
        fi
    fi
    return $_quick_deploy_status
}

# The hook must be loaded after the shell's normal setup; it only registers
# functions and never changes the active terminal until an ssh command returns.
# add-zsh-hook de-duplicates entries, so re-sourcing .zshrc can restore the
# registration if another startup file rebuilt the hook arrays.
autoload -Uz add-zsh-hook
add-zsh-hook preexec _quick_deploy_ghostty_ssh_preexec
add-zsh-hook precmd _quick_deploy_ghostty_ssh_precmd
ZSH_MOUSE_HOOK
}

# Ctrl+Shift+V 和 Ctrl+V 都到达原始 ^V；只有 ZLE 正在编辑本地 Wayland
# shell 提示符时才读取剪贴板。Pi 等 raw-mode 程序不经过 ZLE。
render_local_paste_hook() {
    cat <<'ZSH_LOCAL_PASTE_HEADER'
# Local Wayland zsh prompt paste for Ghostty's raw Ctrl+V key route.
# Installed and maintained by fresh-install/modules/ghostty/install.sh.
[[ -o interactive && -n ${WAYLAND_DISPLAY:-} && -n ${XDG_RUNTIME_DIR:-} ]] || return 0
ZSH_LOCAL_PASTE_HEADER
    case "${1:-current}" in
        v1)
            # Exact first-installed guard. Keep the shared body below unchanged
            # until this migration is retired; its historical digest is tested.
            echo '[[ -z ${SSH_CONNECTION:-} && -z ${SSH_CLIENT:-} && -z ${SSH_TTY:-} ]] || return 0'
            ;;
        v2)
            # Exact deployed direct-Ghostty guard; its digest is pinned below.
            cat <<'ZSH_LOCAL_PASTE_GUARD_V2'
# Ghostty sets these for its direct shell even if the launching process has
# inherited an obsolete SSH_CONNECTION. SSH_TTY identifies an actual SSH pty.
[[ ${TERM_PROGRAM:-} == ghostty && -n ${GHOSTTY_RESOURCES_DIR:-} && -d ${GHOSTTY_RESOURCES_DIR:-} ]] || return 0
[[ -z ${SSH_TTY:-} ]] || return 0
ZSH_LOCAL_PASTE_GUARD_V2
            ;;
        current)
            cat <<'ZSH_LOCAL_PASTE_GUARD'
# tmux replaces TERM_PROGRAM but inherits Ghostty's local shell resources.
# These markers allow reading the shell host clipboard, not routing by client.
[[ ${TERM_PROGRAM:-} == ghostty || ( ${TERM_PROGRAM:-} == tmux && -n ${TMUX:-} ) ]] || return 0
[[ -n ${GHOSTTY_RESOURCES_DIR:-} && -d ${GHOSTTY_RESOURCES_DIR:-} ]] || return 0
# Stale SSH_CONNECTION/SSH_CLIENT may be inherited; SSH_TTY is an actual SSH pty.
[[ -z ${SSH_TTY:-} ]] || return 0
ZSH_LOCAL_PASTE_GUARD
            ;;
        *) die "未知的 zsh 粘贴 hook 渲染版本: $1" ;;
    esac
    cat <<'ZSH_LOCAL_PASTE_HOOK'

_quick_deploy_ghostty_local_wayland() {
    local socket="$WAYLAND_DISPLAY"
    [[ $socket == /* ]] || socket="$XDG_RUNTIME_DIR/$socket"
    [[ -S $socket ]]
}
_quick_deploy_ghostty_local_wayland || return 0

# Do not take over a user's own ^V or the alternate quoted-insert binding.
# In default emacs/vi-insert maps, Esc then Ctrl+V (^[^V) is undefined.
# At source time verify BOTH keys in the active `main` ZLE map before binding.
_quick_deploy_ghostty_ctrlv_widget="${$(bindkey -M main '^V')##* }"
_quick_deploy_ghostty_quote_widget="${$(bindkey -M main '^[^V')##* }"
if [[ $_quick_deploy_ghostty_ctrlv_widget == _quick_deploy_ghostty_paste &&
      ( $_quick_deploy_ghostty_quote_widget == quoted-insert ||
        $_quick_deploy_ghostty_quote_widget == vi-quoted-insert ) ]]; then
    unset _quick_deploy_ghostty_ctrlv_widget _quick_deploy_ghostty_quote_widget
    return 0
fi
if [[ ( $_quick_deploy_ghostty_ctrlv_widget != quoted-insert &&
        $_quick_deploy_ghostty_ctrlv_widget != vi-quoted-insert ) ||
      $_quick_deploy_ghostty_quote_widget != undefined-key ]]; then
    print -u2 'Ghostty local paste: ^V or Esc Ctrl+V already has a custom ZLE binding; refusing to override it.'
    unset _quick_deploy_ghostty_ctrlv_widget _quick_deploy_ghostty_quote_widget
    return 1
fi

_quick_deploy_ghostty_paste() {
    local clipboard
    # wl-paste -n avoids an artificial newline. The sentinel preserves real
    # trailing newlines through command substitution, which otherwise strips them.
    # Append it only after a successful read; a failed/timed-out read never
    # reuses a previous value or inserts a partial selection.
    if clipboard="$(timeout 2s wl-paste --no-newline --type text && printf '\001')"; then
        LBUFFER+="${clipboard%$'\001'}"
    else
        zle -M 'Clipboard paste failed or timed out (wl-paste); editor unchanged'
        return 1
    fi
}
zle -N _quick_deploy_ghostty_paste
bindkey -M main '^[^V' "$_quick_deploy_ghostty_ctrlv_widget"
bindkey -M main '^V' _quick_deploy_ghostty_paste
unset _quick_deploy_ghostty_ctrlv_widget _quick_deploy_ghostty_quote_widget
ZSH_LOCAL_PASTE_HOOK
}

# Historical exact block for guarded migration; existing user text outside it
# is copied byte-for-byte, and edits *inside* the marked block are refused.
render_legacy_zshrc_hook_block() {
    cat <<EOF
$ZSHRC_BLOCK_BEGIN
# 由 fresh-install/modules/ghostty/install.sh 管理；其余 .zshrc 内容不动。
if [ -f "\$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME" ]; then
    source "\$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME"
fi
$ZSHRC_BLOCK_END
EOF
}

render_zshrc_hook_block() {
    cat <<EOF
$ZSHRC_BLOCK_BEGIN
# 由 fresh-install/modules/ghostty/install.sh 管理；其余 .zshrc 内容不动。
if [ -f "\$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME" ]; then
    source "\$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME"
fi
if [ -f "\$HOME/.config/ghostty/$LOCAL_PASTE_HOOK_FILENAME" ]; then
    source "\$HOME/.config/ghostty/$LOCAL_PASTE_HOOK_FILENAME"
fi
$ZSHRC_BLOCK_END
EOF
}

zshrc_hook_markers_valid() {
    awk -v begin="$ZSHRC_BLOCK_BEGIN" -v end="$ZSHRC_BLOCK_END" '
        $0 == begin { if (begin_line || bad) bad = 1; begin_line = NR; next }
        $0 == end   { if (!begin_line || end_line || bad) bad = 1; end_line = NR; next }
        END { exit !(begin_line && end_line && begin_line < end_line && !bad) }
    ' "$1"
}

ensure_zshrc_hook_block() {
    local target="$HOME/.zshrc" block tmp

    if [ -L "$target" ]; then
        warn "$target 是符号链接，拒绝改写；hook 文件已写入，请在其源文件中加入："
        warn "  [ -f \"\$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME\" ] && source \"\$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME\""
        return 0
    fi
    if [ -e "$target" ] && [ ! -f "$target" ]; then
        warn "$target 不是普通文件，拒绝改写；请手动 source Ghostty SSH hook"
        return 0
    fi

    if [ -f "$target" ]; then
        if zshrc_hook_markers_valid "$target"; then
            echo "$target 已有 Ghostty SSH hook 托管加载块，保持原位置不动"
            return 0
        fi
        if grep -Fqx "$ZSHRC_BLOCK_BEGIN" "$target" || grep -Fqx "$ZSHRC_BLOCK_END" "$target"; then
            warn "$target 中 Ghostty hook 标记不成对、乱序或重复，拒绝改写以保护用户配置"
            return 0
        fi
    fi

    block="$(render_zshrc_hook_block)"
    tmp="$(mktemp "$target.tmp.XXXXXX")"
    if [ -f "$target" ]; then
        # 先原样复制用户文件，再只追加本模块自己的内容；不会丢失或
        # 重写用户原有的尾部换行、CRLF 或其它字节。
        if ! cat "$target" > "$tmp" || ! printf '\n' >> "$tmp"; then
            rm -f "$tmp"
            return 1
        fi
    fi
    if ! printf '%s\n' "$block" >> "$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    write_atomic_file_keep_mode "$target" "$tmp" 644
}

install_ssh_mouse_hook() {
    if ! command -v zsh >/dev/null 2>&1; then
        warn "未找到 zsh，跳过 SSH 鼠标自愈 hook（Ctrl+Shift+R 仍可手动复位）"
        return 0
    fi
    if [ -L "$SSH_MOUSE_HOOK_CONFIG" ]; then
        warn "$SSH_MOUSE_HOOK_CONFIG 是符号链接，拒绝覆盖；SSH 鼠标自愈 hook 未启用"
        warn "  手动恢复: Ctrl+Shift+R，或运行 reset"
        return 0
    fi
    if [ -e "$SSH_MOUSE_HOOK_CONFIG" ] && [ ! -f "$SSH_MOUSE_HOOK_CONFIG" ]; then
        warn "$SSH_MOUSE_HOOK_CONFIG 不是普通文件，拒绝覆盖；SSH 鼠标自愈 hook 未启用"
        warn "  手动恢复: Ctrl+Shift+R，或运行 reset"
        return 0
    fi
    write_atomic_text "$SSH_MOUSE_HOOK_CONFIG" "$(render_ssh_mouse_hook)"$'\n' 644
    ensure_zshrc_hook_block
    if [ ! -f "$SSH_MOUSE_HOOK_CONFIG" ] || ! zsh -n "$SSH_MOUSE_HOOK_CONFIG"; then
        die "SSH 鼠标自愈 hook 写入后校验失败: $SSH_MOUSE_HOOK_CONFIG"
    fi
    if [ -f "$HOME/.zshrc" ] && [ ! -L "$HOME/.zshrc" ] && zshrc_hook_markers_valid "$HOME/.zshrc"; then
        echo "OK: SSH 鼠标自愈 hook 已写入、已登记到 .zshrc，语法检查通过: $SSH_MOUSE_HOOK_CONFIG"
    else
        warn "SSH 鼠标自愈 hook 文件已写入，但 .zshrc 未登记；请按上方提示手动 source"
    fi
}

# Verify all prerequisites before either of the two target files is written.
# A narrow run must not silently reset a user-customized Ghostty config/hook
# or add a second .zshrc block when the existing managed block has drifted.
local_paste_zshrc_state() {
    local old new
    old="$(render_legacy_zshrc_hook_block)"
    new="$(render_zshrc_hook_block)"
    python3 - "$HOME/.zshrc" "$old" "$new" <<'PY'
from pathlib import Path
import os
import sys

body = Path(sys.argv[1]).read_bytes()
old, new = (os.fsencode(text) + b"\n" for text in sys.argv[2:])
if body.count(new) == 1:
    print("current")
elif body.count(old) == 1:
    print("old")
else:
    raise SystemExit("marked .zshrc block differs from both managed baselines")
PY
}

# Only exact deployed v1/v2 bytes and the current hook may be replaced.
# Pin the historical digests independently of the renderer's shared body so
# future edits cannot silently redefine which installed files are managed.
local_paste_hook_state() {
    local digest
    if [ -L "$LOCAL_PASTE_HOOK_CONFIG" ]; then
        die "shell 粘贴 hook 是符号链接，拒绝覆盖: $LOCAL_PASTE_HOOK_CONFIG"
    fi
    if [ ! -e "$LOCAL_PASTE_HOOK_CONFIG" ]; then
        echo absent
    elif [ ! -f "$LOCAL_PASTE_HOOK_CONFIG" ]; then
        die "shell 粘贴 hook 不是普通文件，拒绝覆盖: $LOCAL_PASTE_HOOK_CONFIG"
    elif cmp -s "$LOCAL_PASTE_HOOK_CONFIG" <(render_local_paste_hook); then
        echo current
    else
        digest="$(sha256sum "$LOCAL_PASTE_HOOK_CONFIG")"
        case "${digest%% *}" in
            6d53e9c81646f1ebb72872df4ec9f014117957042bce2ccd33fa2bfcc43423eb) echo v1 ;;
            f6e9f1b772c21c82b99ce922fd819ec9f2a1f006a6255f31b8399c6679ea9fc2) echo v2 ;;
            *) die "shell 粘贴 hook 含非基准内容，拒绝覆盖: $LOCAL_PASTE_HOOK_CONFIG" ;;
        esac
    fi
}

verify_local_paste_prerequisites() {
    local command_name
    for command_name in zsh wl-paste timeout python3 cmp sha256sum mktemp chmod mv cp rm; do
        require_command "$command_name"
    done
    [ -d "$CONFIG_DIR" ] && [ ! -L "$CONFIG_DIR" ] \
        || die "Ghostty 配置目录缺失或是符号链接，拒绝安装 shell 粘贴: $CONFIG_DIR"
    [ -f "$CONFIG_FILE" ] && [ ! -L "$CONFIG_FILE" ] && cmp -s "$CONFIG_FILE" <(render_config) \
        || die "全局按键不是当前逐字节受管基准，拒绝安装 shell 粘贴: $CONFIG_FILE"
    [ -f "$SSH_MOUSE_HOOK_CONFIG" ] && [ ! -L "$SSH_MOUSE_HOOK_CONFIG" ] &&
        cmp -s "$SSH_MOUSE_HOOK_CONFIG" <(render_ssh_mouse_hook) \
        || die "SSH 鼠标 hook 不是受管基准，拒绝迁移其 .zshrc 块: $SSH_MOUSE_HOOK_CONFIG"
    [ -f "$HOME/.zshrc" ] && [ ! -L "$HOME/.zshrc" ] && zshrc_hook_markers_valid "$HOME/.zshrc" \
        || die ".zshrc 缺失、是符号链接或托管标记异常，拒绝安装 shell 粘贴"
    LOCAL_PASTE_HOOK_STATE="$(local_paste_hook_state)" \
        || die "shell 粘贴 hook 目标不符合受管版本，拒绝迁移"
    LOCAL_PASTE_ZSHRC_STATE="$(local_paste_zshrc_state)" \
        || die ".zshrc 托管块含非基准内容，拒绝迁移"
}

install_local_paste_hook() {
    local hook_tmp hook_state_now zshrc_tmp zshrc_before old new
    verify_local_paste_prerequisites
    hook_tmp="$(mktemp "$LOCAL_PASTE_HOOK_CONFIG.tmp.XXXXXX")"
    render_local_paste_hook > "$hook_tmp"
    if ! zsh -n "$hook_tmp"; then
        rm -f -- "$hook_tmp"
        die "shell 粘贴 hook 语法检查失败，未安装"
    fi
    if [ "$LOCAL_PASTE_ZSHRC_STATE" = old ]; then
        old="$(render_legacy_zshrc_hook_block)"
        new="$(render_zshrc_hook_block)"
        zshrc_tmp="$(mktemp "$HOME/.zshrc.tmp.XXXXXX")"
        zshrc_before="$(mktemp "$HOME/.zshrc.before.XXXXXX")"
        cp -- "$HOME/.zshrc" "$zshrc_before"
        # Only replace the exact old block. Python copies all user-owned bytes,
        # including CRLF/trailing newlines outside the managed block, untouched.
        if ! python3 - "$zshrc_before" "$zshrc_tmp" "$old" "$new" <<'PY'
from pathlib import Path
import os
import sys

source, target = (Path(path) for path in sys.argv[1:3])
old, new = (os.fsencode(text) + b"\n" for text in sys.argv[3:])
body = source.read_bytes()
if body.count(old) != 1:
    raise SystemExit("managed .zshrc block changed during migration")
target.write_bytes(body.replace(old, new, 1))
PY
        then
            rm -f -- "$hook_tmp" "$zshrc_tmp" "$zshrc_before"
            die ".zshrc 托管块不再符合旧版基准，拒绝安装 shell 粘贴"
        fi
    fi
    hook_state_now="$(local_paste_hook_state)" || hook_state_now=''
    if [ "$hook_state_now" != "$LOCAL_PASTE_HOOK_STATE" ]; then
        rm -f -- "$hook_tmp"
        if [ -n "${zshrc_tmp:-}" ]; then rm -f -- "$zshrc_tmp" "$zshrc_before"; fi
        die "shell 粘贴 hook 在迁移期间变化，拒绝覆盖"
    fi
    write_atomic_file_keep_mode "$LOCAL_PASTE_HOOK_CONFIG" "$hook_tmp" 644
    rm -f -- "$hook_tmp"
    if [ "$LOCAL_PASTE_ZSHRC_STATE" = old ]; then
        # Recheck immediately before replacing the user's file. A concurrent
        # change must not be replaced using an out-of-date baseline snapshot.
        if [ -L "$HOME/.zshrc" ] || ! cmp -s "$HOME/.zshrc" "$zshrc_before"; then
            rm -f -- "$zshrc_tmp" "$zshrc_before"
            die ".zshrc 在迁移期间变化，拒绝覆盖"
        fi
        rm -f -- "$zshrc_before"
        write_atomic_file_keep_mode "$HOME/.zshrc" "$zshrc_tmp" 644
    fi
    [ "$(local_paste_zshrc_state)" = current ] &&
        cmp -s "$LOCAL_PASTE_HOOK_CONFIG" <(render_local_paste_hook) \
        || die "shell 粘贴 hook 或 .zshrc 托管块写入后校验失败"
    echo "OK: 本地 Wayland zsh 的 ^V 从当前剪贴板插入；Esc Ctrl+V 保留 quoted-insert"
}

ssh_mouse_hook_state() {
    if ! command -v zsh >/dev/null 2>&1; then
        echo "  zsh hook: 跳过（未安装 zsh）"
        return 0
    fi
    if [ -L "$SSH_MOUSE_HOOK_CONFIG" ]; then
        echo "  hook 文件: $SSH_MOUSE_HOOK_CONFIG —— 符号链接（正式安装将拒绝覆盖）"
    elif [ -e "$SSH_MOUSE_HOOK_CONFIG" ] && [ ! -f "$SSH_MOUSE_HOOK_CONFIG" ]; then
        echo "  hook 文件: $SSH_MOUSE_HOOK_CONFIG —— 不是普通文件（正式安装将跳过）"
    elif [ -f "$SSH_MOUSE_HOOK_CONFIG" ] && cmp -s "$SSH_MOUSE_HOOK_CONFIG" <(render_ssh_mouse_hook); then
        echo "  hook 文件: $SSH_MOUSE_HOOK_CONFIG —— 已是当前基准"
    elif [ -f "$SSH_MOUSE_HOOK_CONFIG" ]; then
        echo "  hook 文件: $SSH_MOUSE_HOOK_CONFIG —— 已存在但内容漂移（正式安装将备份）"
    else
        echo "  hook 文件: $SSH_MOUSE_HOOK_CONFIG —— 不存在（正式安装将写入）"
    fi
    if [ -L "$HOME/.zshrc" ]; then
        echo "  .zshrc: 符号链接（正式安装将跳过并提示手动 source）"
    elif [ -e "$HOME/.zshrc" ] && [ ! -f "$HOME/.zshrc" ]; then
        echo "  .zshrc: 不是普通文件（正式安装将跳过）"
    elif [ -f "$HOME/.zshrc" ] && zshrc_hook_markers_valid "$HOME/.zshrc"; then
        echo "  .zshrc: 已有托管加载块"
    elif [ -f "$HOME/.zshrc" ] && { grep -Fqx "$ZSHRC_BLOCK_BEGIN" "$HOME/.zshrc" || grep -Fqx "$ZSHRC_BLOCK_END" "$HOME/.zshrc"; }; then
        echo "  .zshrc: hook 标记异常（正式安装将拒绝改写并提示）"
    else
        echo "  .zshrc: 正式安装将追加托管加载块"
    fi
}

render_terminal_wrapper() {
    cat <<'EOF'
#!/bin/bash
# 由 fresh-install/modules/ghostty/install.sh 生成：让 GNOME 的 Ctrl+Alt+T 启动 Ghostty
exec "$HOME/.local/bin/ghostty" "$@"
EOF
}

set_default_terminal() {
    local wrapper_content terminal_list_content
    wrapper_content="$(render_terminal_wrapper)"$'\n'
    terminal_list_content="$DESKTOP_ID
"

    # 调用链（实测）：gsd-media-keys 读 GSettings
    #   org.gnome.desktop.default-applications.terminal exec
    # 按值启动终端。xdg-terminals.list 另管遵守 xdg-terminal-exec
    # 的程序（如文件管理器的“在终端中打开”）。两处缺一都会留下启动入口不一致。
    #
    # 为什么 exec 写绝对路径而不是包装脚本名：gsd-media-keys 是 systemd 用户
    # 服务，它的 PATH 来自 systemd 用户管理器，而 GDM 的 Wayland 会话不
    # source ~/.profile（Xsession 不参与），profile 里的 ~/.local/bin 块到不了
    # 图形会话。实测（Ubuntu 24.04 Wayland）：systemd 用户会话 PATH 就是
    # /etc/environment 的原样，按名字解析会回落到 /usr/bin/x-terminal-emulator
    # （alternatives → gnome-terminal）。绝对路径绕开 PATH 解析，立即生效。
    #
    # 为什么不能用 environment.d 补 PATH：systemd 把 /etc/environment 经
    # /usr/lib/environment.d/99-environment.conf 符号链接当成 environment.d 文件
    # 读，且该目录优先级最高、同一变量先写者赢，用户的 ~/.config/environment.d
    # 再也改不动 PATH（实测 PATH=ZZZ:${PATH} 被完全忽略）。
    #
    # 与 X11/Wayland 无关：名字里的 “x-” 是 Debian alternatives 的历史命名
    # （X terminal emulator），不是对 X11 API 的依赖。
    write_atomic_text "$TERMINAL_WRAPPER" "$wrapper_content" 755
    write_atomic_text "$XDG_TERMINALS_FILE" "$terminal_list_content" 644

    if command -v gsettings >/dev/null 2>&1; then
        # gsd-media-keys 每次按键时现读该键，写完立即生效，不用重启也不用重登。
        # exec-arg 保持 Ubuntu 默认 '-e'：Ghostty 与 gnome-terminal 一样接受 -e。
        gsettings set org.gnome.desktop.default-applications.terminal exec "$TERMINAL_WRAPPER"
        echo "已设置 GSettings terminal exec -> $TERMINAL_WRAPPER（绝对路径，不依赖 PATH）"
    else
        warn "找不到 gsettings，无法把 Ctrl+Alt+T 指到包装脚本绝对路径"
    fi
}

verify_system_integration() {
    [ -x "$GHOSTTY_BIN" ] || die "$GHOSTTY_BIN 不存在或不可执行"
    echo "OK: $GHOSTTY_BIN —— $(installed_version)"

    [ -f "$DESKTOP_FILE" ] || die "系统桌面入口不存在: $DESKTOP_FILE"
    echo "OK: 系统桌面入口 $DESKTOP_FILE"

    infocmp xterm-ghostty >/dev/null 2>&1 || die "安装后仍找不到 xterm-ghostty terminfo"
    echo "OK: xterm-ghostty terminfo 已可用"

    # 远端 tmux 靠客户端 terminfo 的 Ms 决定要不要发 OSC 52；缺了它，
    # “远端复制回本机剪贴板”这条路径会静默失效，所以和记录缺失一样
    # 明确失败，而不是只留一行提示。
    xterm_ghostty_has_ms \
        || die "xterm-ghostty terminfo 缺少 Ms（OSC 52）能力；远端 tmux 复制回本机在正常路径下将不可用"
    echo "OK: xterm-ghostty terminfo 含 Ms（OSC 52）"

}

verify_default_terminal() {
    grep -Fq 'exec "$HOME/.local/bin/ghostty" "$@"' "$TERMINAL_WRAPPER" \
        || die "$TERMINAL_WRAPPER 未正确指向受保护的 Ghostty"
    grep -qx "$DESKTOP_ID" "$XDG_TERMINALS_FILE" \
        || die "$XDG_TERMINALS_FILE 未正确指向 $DESKTOP_ID"
    if command -v gsettings >/dev/null 2>&1; then
        [ "$(gsettings get org.gnome.desktop.default-applications.terminal exec)" = "'$TERMINAL_WRAPPER'" ] \
            || die "GSettings terminal exec 未指向 $TERMINAL_WRAPPER"
    fi
    echo "OK: 默认终端包装脚本、xdg-terminals.list 与 GSettings exec 均已指向 Ghostty"
}

smoke_test() {
    local log pid
    if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
        warn "跳过 GUI 冒烟测试：DISPLAY/WAYLAND_DISPLAY 均未设置（可能是 SSH 会话）"
        return 0
    fi

    log="$(mktemp /tmp/ghostty-smoke.XXXXXX.log)"
    echo "启动 Ghostty 做 3 秒进程存活、stderr 与 fcitx5 GTK4 immodule 检查……"
    # 显式带上登录会话的 IM 环境变量：冒烟测试要验证的是「配置生效后
    # Ghostty 能否加载 fcitx5 immodule」，不该依赖运行安装脚本的那个终端
    # 恰好有这些变量——全新首开机跑 setup.sh 时就没有（im-config 要等下次
    # 登录才设置），会把一次正常安装误判成冒烟失败。
    # 这里的 --gtk-single-instance=false 只服务这一次冒烟：让探针进程独立于
    # 用户已经在运行的 Ghostty。没有它，单实例模式下新进程会把请求交给已有
    # 实例后立即退出，下面的存活判定随即误报“提前退出”。模块的日常策略同样
    # 是独立进程（见 render_config 与 render_desktop_override），但这里的显式
    # 参数不能挪走：冒烟测试要独立于配置文件是否正确，自己保证探针独立。
    GTK_IM_MODULE=fcitx QT_IM_MODULE=fcitx XMODIFIERS=@im=fcitx \
        "$USER_GHOSTTY_LAUNCHER" --gtk-single-instance=false > /dev/null 2>"$log" &
    pid=$!
    sleep 3

    if ! kill -0 "$pid" 2>/dev/null; then
        cat "$log" >&2
        rm -f "$log"
        die "Ghostty 在冒烟测试期间提前退出"
    fi

    # 只认 Ghostty 自己标记为错误级别的行（行首 err/error 前缀），
    # 而不是任何含 "error" 字样的文本。否则会误杀两类良性日志：
    #   warning(glib): ... Theme parser error: ...（GTK 4.14 解析系统主题的
    #     CSS 新语法产生的警告，本质是 warning，与 Ghostty 无关）
    #   任何提到 "error" 的 info 行
    # 配置解析已由第 3 步的 +validate-config 单独把关；这里的核心判据
    # 是“进程能启动并持续存活”（与 kitty 模块的同一教训：stderr 良性
    # 警告不参与判定）。
    if grep -Eq '^(err|error)[[:space:](:]' "$log"; then
        cat "$log" >&2
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        rm -f "$log"
        die "Ghostty 启动日志中出现错误级别行（err/error），请检查上面日志"
    fi
    echo "OK: Ghostty 持续存活，stderr 未发现错误级别日志"
    if [ -n "${WAYLAND_DISPLAY:-}" ] && ! grep -Fq "$BACKPRESSURE_LIBRARY" "/proc/$pid/maps" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        cat "$log" >&2
        rm -f "$log"
        die "Ghostty 在 Wayland 下启动但未加载背压库，启动器可能被绕过"
    fi
    # 良性警告（如 GTK 主题解析）不影响成败，但提示一下，不静默吞掉。
    if grep -Eqi 'theme parser error|hsl\(\) argument' "$log"; then
        warn "stderr 含 GTK 主题解析警告（GTK 4.14 解析系统主题的 CSS），与 Ghostty 无关，不影响使用。"
    fi

    if grep -Fq 'libim-fcitx5.so' "/proc/$pid/maps" 2>/dev/null; then
        echo "OK: fcitx5 GTK4 immodule（libim-fcitx5.so）已载入进程"
    else
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        cat "$log" >&2
        rm -f "$log"
        die "Ghostty 进程未载入 libim-fcitx5.so；中文输入环境可能未生效"
    fi

    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -f "$log"
}

main() {
    parse_args "$@"
    ensure_not_root
    if [ "$PI_LAUNCHER_ONLY" = true ]; then
        verify_pi_launcher_prerequisites
        install_pi_launcher
        verify_pi_launcher
        echo "Pi 专用启动器安装完成: $USER_PI_GHOSTTY_LAUNCHER（其他 Ghostty 状态未改）"
        return 0
    fi
    if [ "$PASTE_KEYBIND_ONLY" = true ]; then
        apply_paste_keybind_only
        echo "已完成全局按键迁移；现有 Ghostty 需正常重载配置或开启新进程"
        return 0
    fi
    if [ "$SHELL_PASTE_ONLY" = true ]; then
        install_local_paste_hook
        echo "已安装本地 Wayland zsh 提示符粘贴；请开启新的交互式 shell"
        return 0
    fi
    check_supported_system
    preflight_commands

    echo "========================================"
    echo "  Ghostty 终端安装（第三方社区包）"
    echo "========================================"
    echo
    echo "系统: $OS_NAME / $ARCH"
    echo "上游不发布 Linux 二进制；包维护者: Mike Kasberg（非 Ghostty 项目）"
    echo

    if [ "$CHECK_ONLY" = true ]; then
        show_check
        exit 0
    fi

    section "[1/10] 安装最新版 Ghostty"
    install_ghostty

    section "[2/10] 构建并托管原生 Wayland 背压修复和 Pi 专用启动器"
    install_backpressure
    verify_backpressure
    install_pi_launcher
    verify_pi_launcher

    # 字体必须先于写配置：render_config 用 font_exists 决定要不要写
    # font-family 行，顺序反了就会把刚装上的字体漏写。
    section "[3/10] 确保字体可用"
    ensure_fonts

    # 必须在 ensure_fonts 之后：规则只为实际存在的字体生成。
    section "[4/10] 修复中文 locale 下的等宽字体劫持"
    ensure_zh_mono_fontconfig

    section "[5/10] 写入并验证基准配置"
    write_config
    verify_config

    section "[6/10] 配置 SSH 鼠标自愈与本地 zsh 粘贴"
    install_ssh_mouse_hook
    if command -v zsh >/dev/null 2>&1; then
        install_local_paste_hook
    else
        warn "未安装 zsh，跳过本地提示符粘贴 hook；安装 zsh 后重跑本模块"
    fi

    section "[7/10] 验证系统桌面入口与 terminfo"
    verify_system_integration

    section "[8/10] 写入并验证独立进程 desktop 覆盖"
    install_desktop_override
    verify_desktop_override

    section "[9/10] 默认终端（接管 Ctrl+Alt+T）"
    if [ "$DEFAULT_TERMINAL" = true ]; then
        set_default_terminal
        verify_default_terminal
    else
        echo "保持 Ctrl+Alt+T 与 xdg-terminals.list 原样。"
        echo "需要 Ghostty 接管时重跑: ./install.sh --default-terminal"
    fi

    section "[10/10] GUI 冒烟测试"
    smoke_test

    echo
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Ghostty 安装完成${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    echo "版本: $(installed_version)"
    echo "配置: $CONFIG_FILE"
    echo "Wayland 修复启动器: $USER_GHOSTTY_LAUNCHER"
    echo "Pi 专用启动器: $USER_PI_GHOSTTY_LAUNCHER"
    echo "外部启动: 每个窗口独立进程（$USER_DESKTOP_FILE）"
    if [ "$DEFAULT_TERMINAL" = true ]; then
        echo "Ctrl+Alt+T: 已通过 $TERMINAL_WRAPPER 接管"
    else
        echo "Ctrl+Alt+T: 保持原样"
    fi
    echo
    echo "验证提示:"
    echo "  1. 从应用菜单启动 Ghostty，确认窗口和 Catppuccin Frappe 主题正常。"
    echo "  2. 按 F11，确认全屏切换。"
    echo "  3. 在 Ghostty 中用 fcitx5 输入一段中文。"
    echo "  4. SSH terminfo 已由 ssh-env + ssh-terminfo 自动处理；"
    echo "     极简远端缺 infocmp/tic 时会回退 xterm-256color。手动兜底:"
    echo "     infocmp -x xterm-ghostty | ssh HOST -- tic -x -"
    echo "  5. SSH 异常断开后点击若出现 0;xx;xxM 乱码，按 Ctrl+Shift+R；"
    echo "     zsh hook 会在 ssh 返回到本地提示符时自动清理常见鼠标模式。"
    echo "  6. 本地 Wayland zsh 提示符中 Ctrl+Shift+V/Ctrl+V 粘贴当前文本；"
    echo "     Esc 后按 Ctrl+V 仍可 quoted-insert，Pi 的原始 Ctrl+V 不变。"
}

main "$@"
