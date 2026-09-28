#!/usr/bin/env bash
# Opt-in native-Wayland Ghostty workaround for GTK4 fatal-on-EAGAIN.
# Run the normal install.sh before enabling; --disable before rerunning it.
set -euo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEM_DESKTOP="/usr/share/applications/com.mitchellh.ghostty.desktop"
DESKTOP="$HOME/.local/share/applications/com.mitchellh.ghostty.desktop"
TERMINAL="$HOME/.local/bin/x-terminal-emulator"
LAUNCHER="$HOME/.local/bin/ghostty"
LIBRARY="$HOME/.local/lib/ghostty/libwayland-backpressure.so"
STATE="$HOME/.config/ghostty/wayland-backpressure"
fail() { printf '错误: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in --enable|--disable|--check) MODE="$1" ;; *) fail '用法: wayland-backpressure.sh --enable | --disable | --check' ;; esac
[ "$#" -eq 1 ] || fail '只接受一个参数'

baseline_terminal() {
    cat <<'EOF'
#!/bin/bash
# 由 fresh-install/modules/ghostty/install.sh 生成：让 GNOME 的 Ctrl+Alt+T 启动 Ghostty
exec /usr/bin/ghostty "$@"
EOF
}
render_launcher() {
    cat <<'EOF'
#!/bin/sh
# 由 quick-deploy Ghostty Wayland EAGAIN workaround 管理。
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
render_terminal() {
    baseline_terminal | sed 's|^exec /usr/bin/ghostty "\$@"$|exec "$HOME/.local/bin/ghostty" "$@"|'
}
render_baseline_desktop() {
    awk '
        BEGIN { bin="Exec=/usr/bin/ghostty" }
        $0 ~ "^" bin "([[:space:]]|$)" {
            if (match($0, /--gtk-single-instance=[a-z]+/))
                $0=substr($0,1,RSTART-1) "--gtk-single-instance=false" substr($0,RSTART+RLENGTH)
            else
                $0=bin " --gtk-single-instance=false" substr($0,length(bin)+1)
            count++
        }
        /^DBusActivatable=/ { $0="DBusActivatable=false" }
        { print }
        END { if (count!=2) exit 1 }
    ' "$SYSTEM_DESKTOP"
}
render_desktop() {
    render_baseline_desktop | awk -v launcher="$LAUNCHER" '
        /^Exec=\/usr\/bin\/ghostty([[:space:]]|$)/ {
            $0="Exec=" launcher substr($0,length("Exec=/usr/bin/ghostty")+1)
        }
        { print }
    '
}
show_state() {
    if [ -f "$STATE/baseline.desktop" ]; then
        printf '状态: 已启用；新启动的 Ghostty 使用原生 Wayland + 有界发送背压重试\n'
        for f in "$LAUNCHER" "$LIBRARY" "$DESKTOP" "$TERMINAL"; do
            if [ ! -f "$f" ]; then printf '缺失: %s\n' "$f"; return 1; fi
        done
        cmp -s "$STATE/enabled.desktop" "$DESKTOP" || fail 'desktop 启动路径有改动，须人工检查'
        cmp -s "$STATE/enabled.terminal" "$TERMINAL" || fail 'Ctrl+Alt+T 包装脚本有改动，须人工检查'
        cmp -s "$STATE/enabled.launcher" "$LAUNCHER" || fail 'Ghostty 启动包装脚本有改动，须人工检查'
        cmp -s "$STATE/enabled.library" "$LIBRARY" || fail 'shim 与已验证的二进制不同，须人工检查'
        printf 'GTK 子进程也继承 LD_PRELOAD；作用限于进程内已登记的 Wayland socket。已有窗口不受影响。\n'
    else
        printf '状态: 未启用；原模块生成的 Ghostty 启动路径不经背压修复\n'
    fi
}

if [ "$MODE" = --check ]; then show_state; exit; fi
if [ "$MODE" = --disable ]; then
    [ -f "$STATE/baseline.desktop" ] || fail '没有已启用的状态可恢复'
    show_state
    cmp -s "$STATE/baseline.desktop" <(render_baseline_desktop) \
        || fail '系统 desktop 模板改变，拒绝自动恢复；请人工检查'
    cmp -s "$STATE/enabled.library" "$LIBRARY" || fail '已安装 shim 与备份不一致，拒绝删除'
    cp -- "$STATE/baseline.desktop" "$DESKTOP.tmp.$$"
    cp -- "$STATE/baseline.terminal" "$TERMINAL.tmp.$$"
    mv -- "$DESKTOP.tmp.$$" "$DESKTOP"
    chmod 644 "$DESKTOP"
    mv -- "$TERMINAL.tmp.$$" "$TERMINAL"
    chmod 755 "$TERMINAL"
    rm -- "$LAUNCHER" "$LIBRARY"
    rm -r -- "$STATE"
    printf '已恢复 install.sh 原始入口。现有进程不受影响；今后新开的 Ghostty 不再受 shim 保护。\n'
    exit
fi

# Refuse any unsupported/partially installed state before writing to user files.
[ ! -e "$STATE" ] || fail "已存在启用状态；先 --check: $STATE"
[ ! -e "$LAUNCHER" ] && [ ! -L "$LAUNCHER" ] || fail "已有用户启动器，拒绝覆盖: $LAUNCHER"
[ -f "$DESKTOP" ] && [ ! -L "$DESKTOP" ] || fail "用户 desktop 缺失或为符号链接: $DESKTOP"
[ -f "$TERMINAL" ] && [ ! -L "$TERMINAL" ] || fail "默认终端包装脚本缺失或为符号链接: $TERMINAL"
[ "$HOME" = "${HOME// /}" ] || fail 'HOME 路径包含空格；desktop Exec 需要独立转义实现'
command -v cc >/dev/null || fail '缺少 C 编译器 cc；先安装 gcc'
[ -f "$SYSTEM_DESKTOP" ] || fail "系统 desktop 模板不存在: $SYSTEM_DESKTOP"
[ -f "$HERE/wayland-backpressure.c" ] || fail '背压修复源码不存在'
[ ! -e "$LIBRARY" ] && [ ! -L "$LIBRARY" ] || fail "已有本地 shim，拒绝覆盖: $LIBRARY"
cmp -s "$DESKTOP" <(render_baseline_desktop) || fail '用户 desktop 与原模块基准不符，拒绝覆盖'
cmp -s "$TERMINAL" <(baseline_terminal) || fail 'Ctrl+Alt+T 包装脚本与原模块基准不符，拒绝覆盖'

scratch="$(mktemp -d /tmp/ghostty-wayland-backpressure.XXXXXX)"
trap 'rm -rf -- "$scratch"' EXIT
cc -shared -fPIC -O2 -Wall -Wextra -Werror -o "$scratch/library.so" "$HERE/wayland-backpressure.c" -ldl -pthread
render_launcher > "$scratch/launcher"
render_terminal > "$scratch/terminal"
render_desktop > "$scratch/desktop"
[ "$(grep -c "^Exec=$LAUNCHER --gtk-single-instance=false" "$scratch/desktop")" -eq 2 ] \
    || fail 'desktop 两个入口未完整改用保护启动器'
grep -qx 'DBusActivatable=false' "$scratch/desktop" || fail 'D-Bus 激活绕过 Exec，拒绝写入'

mkdir -p "$(dirname "$LIBRARY")" "$(dirname "$LAUNCHER")" "$STATE"
cp -- "$DESKTOP" "$STATE/baseline.desktop"
cp -- "$TERMINAL" "$STATE/baseline.terminal"
cp -- "$scratch/desktop" "$STATE/enabled.desktop"
cp -- "$scratch/terminal" "$STATE/enabled.terminal"
cp -- "$scratch/launcher" "$STATE/enabled.launcher"
cp -- "$scratch/library.so" "$STATE/enabled.library"
# Install dependency before launcher; failure must leave a visible state, never a silent fallback.
install -m 644 "$scratch/library.so" "$LIBRARY"
install -m 755 "$scratch/launcher" "$LAUNCHER"
install -m 644 "$scratch/desktop" "$DESKTOP.tmp.$$"
install -m 755 "$scratch/terminal" "$TERMINAL.tmp.$$"
mv -- "$DESKTOP.tmp.$$" "$DESKTOP"
mv -- "$TERMINAL.tmp.$$" "$TERMINAL"
show_state
printf '已启用。仅未来新启动的 Ghostty 受到保护；未切换 X11，不关闭现有窗口。\n'
