#!/bin/bash
# Fcitx5 + Chinese Addons 中文输入法安装配置脚本
# 仅支持 Ubuntu 24.04 及以上版本 (支持 X11 和 Wayland)

set -euo pipefail

CHECK_ONLY=false

FCITX5_CONFIG_DIR="$HOME/.config/fcitx5"
FCITX5_PROFILE="$FCITX5_CONFIG_DIR/profile"

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

# 共享等锁助手：新装系统首开机时后台自动更新会长时间持 apt 锁，
# 直接 purge/update 会撞锁失败，先等它结束。脚本被单独拷出、
# 助手缺失时定义空操作跳过等锁
APT_LOCK_WAIT_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/apt-lock-wait.sh"
if [ -f "$APT_LOCK_WAIT_LIB" ]; then . "$APT_LOCK_WAIT_LIB"; else wait_for_apt_lock() { return 0; }; fi

# 快捷键与行为目标值 —— 写入与校验共用这一份，两者不会各自漂移
HOTKEY_ENUMERATE_FORWARD='Shift+Shift_L'   # 左 Shift: 按列表顺序循环切换输入法
HOTKEY_ALT_TRIGGER=''                      # 「临时切换到第一个输入法」: 不设快捷键
BEHAVIOR_ACTIVE_BY_DEFAULT='True'          # 起手即为激活态，即拼音直通

FCITX5_CONFIG="$FCITX5_CONFIG_DIR/config"
FCITX5_STARTED_NOW=false
IM_ENV_PUSHED=false

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
  ./install.sh [--check] [--help]

选项:
  --check   只检查系统、软件源和当前配置，不安装、不修改
  --help    显示帮助

注意:
  不要用 sudo 运行本脚本。直接运行 ./install.sh 即可，脚本会在需要时调用 sudo。

  本脚本是幂等的重置工具: 重跑会把输入法列表与快捷键重新写回基准状态，
  旧文件先备份为 <文件名>.bak.<时间戳>。
  只想调快捷键而不重置时，用 fcitx5-configtool 图形界面改。
EOF
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --check)
                CHECK_ONLY=true
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
    local package candidate policy
    local missing=()

    for package in "${REQUIRED_PACKAGES[@]}"; do
        # LC_ALL=C 不可省略: apt-cache policy 的输出是本地化的。
        # 中文 locale 下它输出 "候选：" 而不是 "Candidate:"，
        # 匹配 /Candidate:/ 会永远失配，把所有软件包误判为"没有候选版本"。
        #
        # 不能把 apt-cache 的错误丢进 /dev/null 再接管道：pipefail 下命令替换
        # 会返回非 0，set -e 直接终止脚本，用户看到的只是“预检查”标题后沉默退出。
        if ! policy="$(LC_ALL=C apt-cache policy "$package" 2>&1)"; then
            echo "错误: apt-cache policy $package 执行失败:"
            printf '%s\n' "$policy" | sed 's/^/  /'
            exit 1
        fi
        candidate="$(printf '%s\n' "$policy" | awk '/^ *Candidate:/ {print $2; exit}')"
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

    echo "Fcitx5 运行状态:"
    if pgrep -x fcitx5 >/dev/null 2>&1; then
        echo "  正在运行（PID $(pgrep -x fcitx5 | head -n 1)）"
    else
        echo "  未运行（正常安装完成后会立即启动）"
    fi
    if systemctl --user show-environment 2>/dev/null | grep -q '^GTK_IM_MODULE=fcitx$'; then
        echo "  会话激活环境: GTK_IM_MODULE=fcitx 已就位"
    else
        echo "  会话激活环境: 未含 GTK_IM_MODULE=fcitx（安装时将写入；登录时由 im-config 设置）"
    fi
    echo
}

# 让运行中的 fcitx5 干净退出，之后才能安全写配置。
#
# fcitx5 退出时会用内存里的配置回写 ~/.config/fcitx5/，所以顺序必须是
# 「先让它退出（此时它保存当前配置）→ 再写我们的文件 → 再启动」。
# 反过来做（先写再重启）会被它退出时的保存动作覆盖掉，而脚本照样报成功。
# profile 写入（步骤 5）同样受此影响，只是全新安装时 fcitx5 还未启动，未暴露。
stop_fcitx5_before_write() {
    if ! pgrep -x fcitx5 >/dev/null 2>&1; then
        return 0
    fi

    echo "检测到 Fcitx5 正在运行，先让它退出再写配置"
    echo "（它退出时会回写配置目录，不先停会覆盖掉本脚本的写入）"

    if command -v gdbus >/dev/null 2>&1; then
        gdbus call --session --dest org.fcitx.Fcitx5 --object-path /controller \
            --method org.fcitx.Fcitx.Controller1.Exit >/dev/null 2>&1 || true
    fi

    local waited=0
    while [ "$waited" -lt 10 ]; do
        if ! pgrep -x fcitx5 >/dev/null 2>&1; then
            echo "Fcitx5 已退出"
            return 0
        fi
        sleep 0.5
        waited=$((waited + 1))
    done

    echo "警告: Fcitx5 未能退出，写入的配置可能被它覆盖。"
    echo "      本步骤结束后会回读文件校验，若不一致会明确报错。"
}

# 安装完成时让输入法立即可用，而不是强制注销重登。
# 补的是 im-config 登录钩子 /usr/share/im-config/data/23_fcitx5.rc 的两件事：
#   它的 IM_CONFIG_PHASE=2 —— 启动 fcitx5 守护进程（fcitx5 -d）
#   它的 IM_CONFIG_PHASE=1 —— 设置 GTK_IM_MODULE 等环境变量
# 环境变量无法改已经在运行的进程（包括 gnome-shell 自己），但
# dbus-update-activation-environment --systemd 能更新 systemd --user 管理器
# 与 D-Bus 激活环境，让「之后新启动」的桌面应用带上这些变量。
graphical_session_available() {
    [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]
}

ensure_fcitx5_running() {
    if ! graphical_session_available; then
        echo "当前不是图形会话（可能是 SSH），跳过立即启动；"
        echo "登录桌面时 im-config 会自动启动 Fcitx5。"
        return 0
    fi

    if pgrep -x fcitx5 >/dev/null 2>&1; then
        echo "Fcitx5 已在运行"
        return 0
    fi

    if fcitx5 -d >/dev/null 2>&1; then
        sleep 1
        if pgrep -x fcitx5 >/dev/null 2>&1; then
            FCITX5_STARTED_NOW=true
            echo "Fcitx5 已启动（无需注销重登）"
            return 0
        fi
    fi

    echo "警告: 未能自动启动 Fcitx5。可手动运行 fcitx5 -d，"
    echo "      或注销重登后由 im-config 自动启动。"
}

push_im_env_to_session() {
    if ! graphical_session_available; then
        return 0
    fi

    # 判据是激活环境里的实际值，而不是「刚才是不是我们启动的」：
    # 手工启动过 fcitx5 但缺变量的会话同样需要补写；已就位则幂等跳过。
    if systemctl --user show-environment 2>/dev/null | grep -q '^GTK_IM_MODULE=fcitx$'; then
        return 0
    fi

    # 与 23_fcitx5.rc 的 IM_CONFIG_PHASE=1 保持一致
    local vars=(
        "GTK_IM_MODULE=fcitx"
        "QT_IM_MODULE=fcitx"
        "XMODIFIERS=@im=fcitx"
        "CLUTTER_IM_MODULE=xim"
        "SDL_IM_MODULE=fcitx"
    )

    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        if dbus-update-activation-environment --systemd "${vars[@]}" >/dev/null 2>&1; then
            IM_ENV_PUSHED=true
        fi
    fi
    # 兜底：dbus-update 失败时直接写 systemd --user 管理器环境
    if [ "$IM_ENV_PUSHED" != true ] \
        && systemctl --user set-environment "${vars[@]}" 2>/dev/null; then
        IM_ENV_PUSHED=true
    fi

    if [ "$IM_ENV_PUSHED" = true ]; then
        # 回读校验：写入动作返回 0 ≠ 变量真的进了管理器环境
        if systemctl --user show-environment 2>/dev/null | grep -q '^GTK_IM_MODULE=fcitx$'; then
            echo "已把输入法环境变量写入当前会话激活环境（之后新启动的应用直接可用）"
        else
            IM_ENV_PUSHED=false
            echo "警告: 激活环境回读不到 GTK_IM_MODULE=fcitx，写入未生效；"
            echo "      注销重登后由 im-config 统一设置。"
        fi
    else
        echo "警告: 未能写入会话激活环境；注销重登后由 im-config 统一设置。"
    fi
}

# 只改这两个键，不整文件覆盖 —— config 里还有 DefaultPageSize、ActiveByDefault
# 等用户可能调过的值，整写会连坐。
write_hotkey_config() {
    local tmp

    mkdir -p "$FCITX5_CONFIG_DIR"
    [ -f "$FCITX5_CONFIG" ] || : > "$FCITX5_CONFIG"

    tmp="$(mktemp "$FCITX5_CONFIG.tmp.XXXXXX")"

    # 逐节处理：[Hotkey] 内替换 AltTriggerKeys，[Hotkey/EnumerateForwardKeys] 内
    # 把整个按键列表换成单条目标值，[Behavior] 内替换 ActiveByDefault。
    # 离开某节而目标键未出现过时补写；整个文件都没有该节时在末尾补节。
    awk -v fwd="$HOTKEY_ENUMERATE_FORWARD" -v alt="$HOTKEY_ALT_TRIGGER" \
        -v act="$BEHAVIOR_ACTIVE_BY_DEFAULT" '
        function flush_pending() {
            if (cur == "hotkey"   && !alt_done) { print "AltTriggerKeys=" alt;  alt_done=1 }
            if (cur == "fwd"      && !fwd_done) { print "0=" fwd;               fwd_done=1 }
            if (cur == "behavior" && !act_done) { print "ActiveByDefault=" act; act_done=1 }
        }
        /^\[.*\]$/ {
            flush_pending()
            if      ($0 == "[Hotkey]")                     { cur="hotkey";   saw_hotkey=1 }
            else if ($0 == "[Hotkey/EnumerateForwardKeys]") { cur="fwd";      saw_fwd=1 }
            else if ($0 == "[Behavior]")                   { cur="behavior"; saw_behavior=1 }
            else                                           { cur="other" }
            print
            next
        }
        cur == "hotkey" && /^[[:space:]]*AltTriggerKeys[[:space:]]*=/ {
            print "AltTriggerKeys=" alt
            alt_done=1
            next
        }
        cur == "fwd" && /^[[:space:]]*[0-9]+[[:space:]]*=/ {
            if (!fwd_done) { print "0=" fwd; fwd_done=1 }
            next
        }
        cur == "behavior" && /^[[:space:]]*ActiveByDefault[[:space:]]*=/ {
            print "ActiveByDefault=" act
            act_done=1
            next
        }
        { print }
        END {
            flush_pending()
            if (!saw_hotkey)   { print ""; print "[Hotkey]";                     print "AltTriggerKeys=" alt }
            if (!saw_fwd)      { print ""; print "[Hotkey/EnumerateForwardKeys]"; print "0=" fwd }
            if (!saw_behavior) { print ""; print "[Behavior]";                   print "ActiveByDefault=" act }
        }
    ' "$FCITX5_CONFIG" > "$tmp"

    if [ ! -s "$tmp" ] && [ -s "$FCITX5_CONFIG" ]; then
        rm -f "$tmp"
        echo "错误: 生成快捷键配置失败，原文件未改动"
        exit 1
    fi

    # 内容无变化就不写也不备份，否则每跑一次就多一个同内容备份。
    if cmp -s "$tmp" "$FCITX5_CONFIG"; then
        rm -f "$tmp"
        return 0
    fi

    backup_file "$FCITX5_CONFIG"
    chmod --reference="$FCITX5_CONFIG" "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$FCITX5_CONFIG"
}

# 回读磁盘上的实际值。写入成功 ≠ 生效：fcitx5 若在写入后回写配置，
# 这里会看到自己的值被换掉，从而抓住“报了成功其实没改上”。
read_hotkey_value() {
    local section="$1" key="$2"

    [ -f "$FCITX5_CONFIG" ] || return 0

    awk -v want_section="$section" -v want_key="$key" '
        /^\[.*\]$/ { in_section = ($0 == want_section); next }
        in_section {
            line = $0
            sub(/^[[:space:]]+/, "", line)
            if (index(line, want_key "=") == 1) {
                print substr(line, length(want_key) + 2)
                exit
            }
        }
    ' "$FCITX5_CONFIG"
}

verify_hotkey_config() {
    local actual_fwd actual_alt actual_act failed=false

    actual_fwd="$(read_hotkey_value '[Hotkey/EnumerateForwardKeys]' '0')"
    actual_alt="$(read_hotkey_value '[Hotkey]' 'AltTriggerKeys')"
    actual_act="$(read_hotkey_value '[Behavior]' 'ActiveByDefault')"

    if [ "$actual_fwd" = "$HOTKEY_ENUMERATE_FORWARD" ]; then
        echo "OK: 左 Shift 循环切换输入法 (EnumerateForwardKeys=$actual_fwd)"
    else
        echo "错误: EnumerateForwardKeys 期望 $HOTKEY_ENUMERATE_FORWARD，实际 ${actual_fwd:-(空)}"
        failed=true
    fi

    if [ -z "$actual_alt" ]; then
        echo "OK: 「临时切换到第一个输入法」已无快捷键 (AltTriggerKeys 为空)"
    else
        echo "错误: AltTriggerKeys 期望为空，实际 $actual_alt"
        failed=true
    fi

    if [ "$actual_act" = "$BEHAVIOR_ACTIVE_BY_DEFAULT" ]; then
        echo "OK: 起手即拼音直通 (ActiveByDefault=$actual_act)"
    else
        echo "错误: ActiveByDefault 期望 $BEHAVIOR_ACTIVE_BY_DEFAULT，实际 ${actual_act:-(空)}"
        failed=true
    fi

    if [ "$failed" = true ]; then
        echo
        echo "快捷键未写入成功。若 Fcitx5 在写入期间仍在运行，它退出时会用内存中的"
        echo "配置覆盖磁盘文件。请完全退出 Fcitx5 后重新运行本脚本。"
        exit 1
    fi
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

    # 运行态：装完应当立即可用。缺进程或缺激活环境变量只告警不报错——
    # 非图形会话（SSH）下两者都合法地缺席，由登录时的 im-config 补上。
    if pgrep -x fcitx5 >/dev/null 2>&1; then
        echo "OK: Fcitx5 正在运行"
    elif graphical_session_available; then
        echo "警告: Fcitx5 未在运行；注销重登后会由 im-config 启动"
    else
        echo "提示: 非图形会话，Fcitx5 将在登录桌面时由 im-config 启动"
    fi

    if systemctl --user show-environment 2>/dev/null | grep -q '^GTK_IM_MODULE=fcitx$'; then
        echo "OK: 会话激活环境已含 GTK_IM_MODULE=fcitx"
    elif graphical_session_available; then
        echo "警告: 会话激活环境缺 GTK_IM_MODULE=fcitx，之后新启动的应用拿不到输入法变量"
    fi

    # 上面那条只是回读本脚本自己刚写的文件，证明不了 pinyin 能用。
    # fcitx5 在加载时会把找不到对应 inputmethod 注册文件的条目直接从组里删掉，
    # 不报错、不告警——表现就是“profile 看着对，但输入法列表里没有拼音”。
    # 所以这里校验引擎本身是否存在，这是独立于本脚本写入动作的事实。
    if [ -f /usr/share/fcitx5/inputmethod/pinyin.conf ]; then
        echo "OK: pinyin 输入法引擎已注册"
    else
        echo "错误: 找不到 /usr/share/fcitx5/inputmethod/pinyin.conf，pinyin 引擎缺失"
        echo "      profile 里写了 pinyin 也会被 fcitx5 在加载时静默丢弃。"
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

echo "当前显示服务器: ${XDG_SESSION_TYPE:-unknown}"
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

# purge / update / install 都会与持锁的后台 apt 活动冲突，先等它结束
wait_for_apt_lock || { echo "✗ 等待 apt 锁超时，请稍后重跑" >&2; exit 1; }

# ============================================
# 步骤 1: 删除冲突的 Fcitx4
# ============================================
# 说明: 只删除与 fcitx5 冲突的 fcitx4。
#       保留 GNOME 依赖的 IBus 核心包，不在此处自动清理其它输入法。
# ============================================
section "[1/5] 检查冲突的 Fcitx4"

# 查询已安装的 fcitx4 包。只匹配包名，不扫描述文本。
# 用 ${db:Status-Status} 而不是 Status-Abbrev 的 ^ii：被 apt-mark hold 的包状态
# 缩写是 "hi"，依然是已安装，匹配 ^ii 会漏掉它，导致后面 apt 才报冲突。
# 2>/dev/null || true 是必要的：glob 无匹配时 dpkg-query 本就返回非 0，
# 而"未安装任何 fcitx4"正是最常见的情形。
mapfile -t FCITX4_PACKAGES < <(
    dpkg-query -W -f='${db:Status-Status} ${binary:Package}\n' 'fcitx*' 2>/dev/null \
        | awk '$1 == "installed" && $2 ~ /^fcitx(:|$|-)/ && $2 !~ /^fcitx5(:|$|-)/ {print $2}' \
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

# 改名而不是 rm -rf：里面可能有用户多年的自造词库和配置，
# Fcitx5 配不成时还需要回退。永久删除交给用户自己决定。
if [ -d "$HOME/.config/fcitx" ]; then
    FCITX4_BACKUP="$HOME/.config/fcitx.bak.$(date +%Y%m%d%H%M%S)"
    mv "$HOME/.config/fcitx" "$FCITX4_BACKUP"
    echo "已备份旧 Fcitx4 用户配置: $FCITX4_BACKUP"
    echo "（确认无需回退后可自行删除）"
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

wait_for_apt_lock || { echo "✗ 等待 apt 锁超时，请稍后重跑" >&2; exit 1; }
"$SUDO_CMD" apt update
"$SUDO_CMD" apt install -y "${REQUIRED_PACKAGES[@]}"

echo "软件包安装完成"
echo

# ============================================
# 步骤 3: 配置输入法框架
# ============================================
# 说明: 使用 im-config 将 fcitx5 设置为用户默认输入法框架。
#       im-config 负责「下次登录」：设置环境变量并启动 fcitx5。
#       「本次会话」的启动与环境变量由步骤 5 之后立即补上，不必等重登。
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

# 不能用 sed '/起始/,/结束/d'：范围删除在找不到结束行时会一路删到文件末尾。
# 旧实现只 grep 了起始标记就执行删除，实测：用户若把 SDL 那行改掉或删掉，
# 12 行的 ~/.profile 会被删到只剩 3 行，连后面的 EDITOR、cargo env 一起没，
# 而脚本打印的是“已移除环境变量块”。现在要求两个标记都在才动手。
PROFILE_BEGIN='# Fcitx5 输入法环境变量'
PROFILE_END='export SDL_IM_MODULE=fcitx'

# 不能用 sed '/起始/,/结束/d'，也不能靠两个独立的 grep 做守卫。
# 范围删除在找不到结束行时会一路删到文件末尾；而两个 grep 只能证明
# 两个标记都"存在"，不能证明它们"按顺序配对"。实测反例：
#     before
#     export SDL_IM_MODULE=fcitx     ← 结束标记在前
#     manual-setting
#     # Fcitx5 输入法环境变量   ← 起始标记在后，之后再无结束行
#     EDITOR=vim
# 两个 grep 都通过，awk 进了 skip 就出不来，EDITOR=vim 被删。
# 现在把配对判定交给同一遍解析：awk 退出码 0 才代表找到了完整闭合的块。
if [ -f "$HOME/.profile" ] && grep -qF -x "$PROFILE_BEGIN" "$HOME/.profile"; then
    PROFILE_TMP="$(mktemp "$HOME/.profile.tmp.XXXXXX")"
    if awk -v b="$PROFILE_BEGIN" -v e="$PROFILE_END" '
            $0 == b        { if (skip) exit 1; skip=1; removed=1; next }
            skip && $0 == e { skip=0; next }
            !skip          { print }
            END            { if (skip || !removed) exit 1 }
        ' "$HOME/.profile" > "$PROFILE_TMP"
    then
        backup_file "$HOME/.profile"
        # 同目录 mktemp + mv：重定向写入会先截断目标文件，磁盘满或写失败时
        # ~/.profile 会被截成空。mv 是同一文件系统内的原子替换，失败则原文件完好。
        chmod --reference="$HOME/.profile" "$PROFILE_TMP" 2>/dev/null || true
        mv -f "$PROFILE_TMP" "$HOME/.profile"
        echo "已移除 ~/.profile 中旧版脚本写入的 Fcitx5 环境变量块"
    else
        rm -f "$PROFILE_TMP"
        echo "警告: ~/.profile 里有旧版起始标记，但找不到与之配对的结束行："
        echo "        $PROFILE_END"
        echo "      无法确定块的边界，为避免误删你自己的配置，已跳过，文件未动。"
        echo "      请手动检查并删除该块。"
    fi
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
# 步骤 5: 配置输入法列表与快捷键
# ============================================
# 说明: 写 ~/.config/fcitx5/profile（键盘 US + 拼音）与
#       ~/.config/fcitx5/config（快捷键）。
#       两个文件都必须在 fcitx5 退出后写，否则会被它回写覆盖。
# ============================================
section "[5/5] 配置输入法列表与快捷键"

mkdir -p "$FCITX5_CONFIG_DIR"

# 无条件重写。本脚本的定位是幂等的重置工具：第一次运行是安装，
# 之后重跑就是“把输入法改回基准状态”。旧文件由 backup_file 存成
# <文件名>.bak.<时间戳>，回退靠它，不靠拒绝执行。
stop_fcitx5_before_write

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

# 快捷键：fcitx5 自带的是 Ctrl+Space 循环切换、左 Shift 临时切到第一个输入法。
# 本脚本把左 Shift 改为循环切换，并取消“临时切换”的快捷键（置空）。
# Ctrl+Space 不变，EnumerateWithTriggerKeys 保持默认，仍然按列表顺序轮换。
write_hotkey_config
echo "已写入快捷键配置（左 Shift 循环切换；临时切换无快捷键）"

# 启动 + 写会话环境变量：让之后新开的应用立即可用输入法。
ensure_fcitx5_running
push_im_env_to_session
echo

verify_hotkey_config
echo

verify_installation

# ============================================
# 完成提示
# ============================================
#
# 下方提示文本保持原样。其中第 4 条描述的是 fcitx5 的出厂默认快捷键：
#     Ctrl+Space  按列表顺序循环切换输入法
#     Left Shift  临时切换到第一个输入法
#
# 本脚本的步骤 5 会把它们改成：
#     Ctrl+Space  不变，仍为按列表顺序循环切换
#     Left Shift  改为按列表顺序循环切换（EnumerateForwardKeys=Shift+Shift_L）
#     「临时切换到第一个输入法」取消快捷键（AltTriggerKeys 置空）
#
# 即安装完成后左 Shift 与 Ctrl+Space 等效，都是循环切换。
# 实际写入值由 verify_hotkey_config 回读磁盘校验，不一致会报错。
# ============================================
echo "========================================"
echo "  安装完成"
echo "========================================"
echo
if [ "$FCITX5_STARTED_NOW" = true ] || [ "$IM_ENV_PUSHED" = true ]; then
    echo "输入法已即时生效（无需等注销重登）:"
    echo "  - Fcitx5 已启动，输入法环境变量已写入当前会话激活环境"
    echo "  - 之后新打开的应用可直接输入中文"
    echo "  - 已经在运行的程序环境不可改（含本终端已开的窗口），需重启该程序"
    echo "  - 注销重登一次仍是让所有程序彻底一致的最干净路径:"
    echo "      gnome-session-quit --logout --no-prompt"
else
    echo "重要: 注销并重新登录以启动输入法并设置环境变量"
    echo "     手动操作或者使用命令: gnome-session-quit --logout --no-prompt"
fi
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
echo "  运行: $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/import-dict.sh"
echo
echo "清理提示:"
echo "  本脚本不会自动执行 apt autoremove。确认系统无异常后，可手动运行:"
echo "  sudo apt autoremove"
echo

# 上方提示文本保持原样，其中第 4 条描述的是 fcitx5 的出厂默认值。
# 本脚本已经把它改掉了，所以必须在后面说清楚 —— 否则用户会按上面
# 那句去理解左 Shift，而实际行为已经不同。
echo "========================================"
echo "  快捷键已被本脚本修改"
echo "========================================"
echo
echo "上方第 4 条列的是 fcitx5 出厂默认值，本脚本已将其改为:"
echo
echo "  Ctrl+Space  不变，仍为按列表顺序循环切换输入法"
echo "  Left Shift  已改为按列表顺序循环切换输入法"
echo "              (EnumerateForwardKeys=$HOTKEY_ENUMERATE_FORWARD)"
echo "  「临时切换到第一个输入法」已取消快捷键 (AltTriggerKeys 置空)"
echo "  起手即拼音直通，无需先按切换键 (ActiveByDefault=$BEHAVIOR_ACTIVE_BY_DEFAULT)"
echo
echo "即左 Shift 与 Ctrl+Space 现在等效，都是循环切换。"
echo "上面第 4 条里“Left Shift: 临时切换到第一个输入法”已不再适用。"
echo "实际写入值已由上方安装后检查回读磁盘校验。"
echo
echo "配置文件: $FCITX5_CONFIG"
echo "如需微调或恢复默认: fcitx5-configtool 的 Global Options → Hotkey"
echo

# 输入法列表的两个状态很容易被误解，且重跑即重置的语义必须说清楚。
echo "========================================"
echo "  输入法列表说明"
echo "========================================"
echo
echo "fcitx5 有两个状态，各自用哪个输入法由 profile 决定:"
echo "  未激活 (托盘显示关) → 列表第一项 Items/0，应为英文直通"
echo "  已激活 (托盘显示开) → DefaultIM，应为拼音"
echo
echo "本脚本写入的是: Items/0=keyboard-us、Items/1=pinyin、DefaultIM=pinyin"
echo "配上 ActiveByDefault=$BEHAVIOR_ACTIVE_BY_DEFAULT，新窗口起手就是激活态的拼音；"
echo "按切换键转到未激活态时是英文。用 fcitx5-remote 可查当前状态:"
echo "  fcitx5-remote      输出 0=关闭 1=未激活 2=已激活"
echo "  fcitx5-remote -n   输出该状态下实际使用的输入法"
echo "若 fcitx5-remote 返回 1 而 -n 返回 pinyin，说明两个状态被调反了。"
echo
echo "重跑本脚本 = 重置输入法。profile 与快捷键都会被无条件写回上述基准状态，"
echo "旧文件先备份为 <文件名>.bak.<时间戳>。只想微调而不重置时用 fcitx5-configtool。"
echo
