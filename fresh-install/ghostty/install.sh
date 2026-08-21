#!/bin/bash
# Ghostty 终端安装配置脚本（Ubuntu 社区打包版）
# 仅支持 Ubuntu 24.04 及以上版本的 amd64 / arm64。
#
# Ghostty 上游不发布 Linux 二进制；这里优先使用 Mike Kasberg 维护的
# 第三方 PPA，让 apt 校验 PGP 签名并管理依赖。新机器若因代理尚未就绪
# 无法访问 Launchpad，则退回同一维护者的 GitHub .deb，并在安装前核对
# GitHub API 给出的 SHA-256 digest。
#
# 幂等语义：重跑 = 确保最新版 + 把配置重置为基准内容；旧文件先备份。
# --check 只读，不添加源、不更新 apt、不下载或写入任何文件。

set -euo pipefail

CHECK_ONLY=false
# 默认接管 Ctrl+Alt+T。本模块刚引入时默认为否，是为了不和当时还在的
# kitty 模块争抢同一个包装脚本（两个模块都写就是后跑者赢，无告警）。
# kitty 模块已移除，Ghostty 是本仓库唯一的终端模块，该理由不再成立；
# 不接管反而会让新机器装完 Ctrl+Alt+T 仍指向 gnome-terminal。
DEFAULT_TERMINAL=true
INSTALL_MODE="auto"
SUDO_CMD="${SUDO_CMD:-sudo}"

PPA_NAME="ppa:mkasberg/ghostty-ubuntu"
PPA_MARKER="ppa.launchpadcontent.net/mkasberg/ghostty-ubuntu"
PPA_FINGERPRINT="0721FDF5FECB88DC6920361657C8EF455CEAE491"
GITHUB_API="https://api.github.com/repos/mkasberg/ghostty-ubuntu/releases/latest"
DESKTOP_ID="com.mitchellh.ghostty.desktop"
DESKTOP_FILE="/usr/share/applications/$DESKTOP_ID"
GHOSTTY_BIN="/usr/bin/ghostty"
CONFIG_DIR="$HOME/.config/ghostty"
CONFIG_FILE="$CONFIG_DIR/config.ghostty"
LOCAL_BIN_DIR="$HOME/.local/bin"
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

选项:
  --check                 只读预检：报告当前状态与计划，不安装、不修改
  --no-default-terminal   不接管 Ctrl+Alt+T（默认会接管）
  --default-terminal      兼容旧命令行，默认已是开启状态
  --ppa-only              只使用第三方 PPA；失败时不退回 GitHub .deb
  --deb-only              跳过 PPA，直接安装经 SHA-256 核对的 GitHub .deb
  --help                  显示帮助

注意:
  不要用 sudo 运行本脚本；直接运行 ./install.sh，脚本会在 apt 步骤调用 sudo。
  默认接管 Ctrl+Alt+T（旧的包装脚本会先备份）。重跑会确保最新版，并把
  Ghostty 配置重置为本模块的基准内容；内容变化时会先保存 .bak.<时间戳> 备份。
EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check) CHECK_ONLY=true ;;
            --default-terminal) DEFAULT_TERMINAL=true ;;
            --no-default-terminal) DEFAULT_TERMINAL=false ;;
            --ppa-only)
                [ "$INSTALL_MODE" != "deb" ] || die "--ppa-only 与 --deb-only 不能同时使用"
                INSTALL_MODE="ppa"
                ;;
            --deb-only)
                [ "$INSTALL_MODE" != "ppa" ] || die "--ppa-only 与 --deb-only 不能同时使用"
                INSTALL_MODE="deb"
                ;;
            --help|-h) usage; exit 0 ;;
            *) die "未知参数: $1（请用 --help 查看用法）" ;;
        esac
        shift
    done
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
    local command_name
    for command_name in dpkg apt-cache grep awk cmp cp mv mktemp chmod mkdir date fc-list infocmp; do
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
    printf '%s\n' '  <!-- 由 fresh-install/ghostty/install.sh 生成与维护。 -->'
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
    elif [ -f "$TERMINAL_WRAPPER" ] && grep -Fq 'exec /usr/bin/ghostty "$@"' "$TERMINAL_WRAPPER" 2>/dev/null; then
        printf '已指向 Ghostty\n'
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
    else
        echo "terminfo: xterm-ghostty 不可用（安装的系统级 .deb 应补齐）"
    fi
    echo "Ctrl+Alt+T: $(wrapper_state)"
    echo "xdg-terminal-exec 默认项: $(xdg_terminal_state)"

    case "$INSTALL_MODE" in
        ppa) echo "计划: 只走 PPA，更新 apt 并安装最新版 ghostty；失败即退出" ;;
        deb) echo "计划: 跳过 PPA，从 GitHub 选择严格匹配 $ARCH + Ubuntu $UBUNTU_VERSION 的最新版 .deb，核对 SHA-256 后安装" ;;
        auto)
            # 预检必须说出实际会发生的事：已是最新时主流程会短路跳过安装，
            # 写成「先走 PPA」就是误导。
            if ghostty_already_current; then
                echo "计划: 已是当前 apt 源可得的最新版，将跳过安装，仅重置配置与校验"
            else
                echo "计划: 先走 PPA；若 Launchpad 不可达或安装失败，再走严格匹配 $ARCH + Ubuntu $UBUNTU_VERSION 的 GitHub .deb"
            fi
            ;;
    esac
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
    echo "# 由 fresh-install/ghostty/install.sh 生成与维护。"
    echo "# 重跑脚本会把本文件重置为基准内容；手工修改请在脚本里改。"
    font_exists "$FONT_FAMILY" && echo "font-family = $FONT_FAMILY"
    font_exists "$CJK_FONT_FAMILY" && echo "font-family = $CJK_FONT_FAMILY"
    echo "font-size = $FONT_SIZE"
    echo "theme = $THEME"
    echo "keybind = f11=toggle_fullscreen"

    # 把 Ctrl+Alt+←/→ 明确让给终端内的 tmux。
    # Ghostty 默认把它们绑给 goto_split:left/right；即使无 split 时当前版本
    # 看起来会透传，也不能把隐式 fallback 当成稳定合同。显式 unbind 后，
    # 按键一定编码后送入 pty，fresh-install/tmux/tmux.conf.local 的 root 表
    # 再用 C-M-Left/Right 切 previous/next window。
    # 这不是“改绑 Ghostty 标签页”：Ghostty 标签页继续用上游默认
    # Ctrl+Tab / Ctrl+Shift+Tab；只把两个键的所有权交给 tmux。
    echo "keybind = ctrl+alt+arrow_left=unbind"
    echo "keybind = ctrl+alt+arrow_right=unbind"

    # 为什么必须显式写：working-directory 默认是 inherit，即继承启动进程的
    # 当前目录。Ghostty 仅在能识别出“从桌面启动器启动”时才自动改用 home；
    # 而 Ctrl+Alt+T 走的是 gsd-media-keys -> x-terminal-emulator 包装脚本，
    # 不是桌面启动器路径，检测不到，于是退回 inherit。
    # 叠上 gtk-single-instance=detect：新窗口请求会交给已在运行的实例，
    # 于是新窗口继承的是“当初那个实例启动时的目录”——用户在哪个
    # 目录里手动跑过 ghostty，以后所有快捷键窗口就黏在那里。
    # 显式声明后行为不再取决于启动路径和历史实例。
    echo "working-directory = $(resolved_working_directory)"
    [ -n "$SHELL_INTEGRATION_FEATURES" ] && \
        echo "shell-integration-features = $SHELL_INTEGRATION_FEATURES"
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
    help_output="$(ghostty --help 2>&1 || true)"
    if grep -Fq '+validate-config' <<< "$help_output"; then
        ghostty +validate-config >"$validate_log" 2>&1 || rc=$?
        VALIDATE_ACTION="ghostty +validate-config"
    else
        ghostty +show-config >"$validate_log" 2>&1 || rc=$?
        VALIDATE_ACTION="ghostty +show-config"
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

set_default_terminal() {
    local wrapper_content terminal_list_content
    wrapper_content='#!/bin/bash
# 由 fresh-install/ghostty/install.sh 生成：让 GNOME 的 Ctrl+Alt+T 启动 Ghostty
exec /usr/bin/ghostty "$@"
'
    terminal_list_content="$DESKTOP_ID
"

    # 调用链（实测）：gsd-media-keys 读 GSettings
    #   org.gnome.desktop.default-applications.terminal exec = 'x-terminal-emulator'
    # 然后按 PATH 启动它。xdg-terminals.list 另管遵守 xdg-terminal-exec
    # 的程序（如文件管理器的“在终端中打开”）。两处缺一都会留下启动入口不一致。
    #
    # 与 X11/Wayland 无关：名字里的 “x-” 是 Debian alternatives 的历史命名
    # （X terminal emulator），不是对 X11 API 的依赖。抢占的是“GSettings 值
    # + 进程启动”这条路，两种显示服务器下完全相同；不同的只是按键抓取
    # （X11 走 XGrabKey，Wayland 由 Mutter 路由到同一个处理函数）。
    write_atomic_text "$TERMINAL_WRAPPER" "$wrapper_content" 755
    write_atomic_text "$XDG_TERMINALS_FILE" "$terminal_list_content" 644

    # Ubuntu 默认 ~/.profile 会把 ~/.local/bin 注入登录会话；若用户删过
    # 这段，重新登录后 gsd-media-keys 会绕过上面的用户级包装脚本。
    if ! systemctl --user show-environment 2>/dev/null \
        | grep '^PATH=' | grep -qF "$LOCAL_BIN_DIR"; then
        warn "systemd 用户会话 PATH 中没有 $LOCAL_BIN_DIR，重新登录后 Ctrl+Alt+T 可能退回旧终端"
        echo "请确认 ~/.profile 保留 Ubuntu 默认的 ~/.local/bin PATH 配置块。"
    fi
}

verify_system_integration() {
    [ -x "$GHOSTTY_BIN" ] || die "$GHOSTTY_BIN 不存在或不可执行"
    echo "OK: $GHOSTTY_BIN —— $(installed_version)"

    [ -f "$DESKTOP_FILE" ] || die "系统桌面入口不存在: $DESKTOP_FILE"
    echo "OK: 系统桌面入口 $DESKTOP_FILE"

    infocmp xterm-ghostty >/dev/null 2>&1 || die "安装后仍找不到 xterm-ghostty terminfo"
    echo "OK: xterm-ghostty terminfo 已可用"

}

verify_default_terminal() {
    grep -Fq 'exec /usr/bin/ghostty "$@"' "$TERMINAL_WRAPPER" \
        || die "$TERMINAL_WRAPPER 未正确指向 Ghostty"
    grep -qx "$DESKTOP_ID" "$XDG_TERMINALS_FILE" \
        || die "$XDG_TERMINALS_FILE 未正确指向 $DESKTOP_ID"
    echo "OK: 默认终端包装脚本与 xdg-terminals.list 均已指向 Ghostty"
}

smoke_test() {
    local log pid
    if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
        warn "跳过 GUI 冒烟测试：DISPLAY/WAYLAND_DISPLAY 均未设置（可能是 SSH 会话）"
        return 0
    fi

    log="$(mktemp /tmp/ghostty-smoke.XXXXXX.log)"
    echo "启动 Ghostty 做 3 秒进程存活、stderr 与 fcitx5 GTK4 immodule 检查……"
    "$GHOSTTY_BIN" --gtk-single-instance=false > /dev/null 2>"$log" &
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

    section "[1/7] 安装最新版 Ghostty"
    install_ghostty

    # 字体必须先于写配置：render_config 用 font_exists 决定要不要写
    # font-family 行，顺序反了就会把刚装上的字体漏写。
    section "[2/7] 确保字体可用"
    ensure_fonts

    # 必须在 ensure_fonts 之后：规则只为实际存在的字体生成。
    section "[3/7] 修复中文 locale 下的等宽字体劫持"
    ensure_zh_mono_fontconfig

    section "[4/7] 写入并验证基准配置"
    write_config
    verify_config

    section "[5/7] 验证桌面入口与 terminfo"
    verify_system_integration

    section "[6/7] 默认终端（接管 Ctrl+Alt+T）"
    if [ "$DEFAULT_TERMINAL" = true ]; then
        set_default_terminal
        verify_default_terminal
    else
        echo "保持 Ctrl+Alt+T 与 xdg-terminals.list 原样。"
        echo "需要 Ghostty 接管时重跑: ./install.sh --default-terminal"
    fi

    section "[7/7] GUI 冒烟测试"
    smoke_test

    echo
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Ghostty 安装完成${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    echo "版本: $(installed_version)"
    echo "配置: $CONFIG_FILE"
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
}

main "$@"
