#!/bin/bash
# 仅加载安装器函数，在隔离 HOME 中验证 Fcitx5 XDG autostart 的收敛行为。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/../install.sh"
WORK_DIR="$(mktemp -d)"
FUNCTIONS="$WORK_DIR/functions.sh"
trap 'rm -rf "$WORK_DIR"' EXIT

# 安装器的参数解析之后是有副作用的安装主流程；测试只需函数定义和常量。
sed '/^parse_args "\$@"$/,$d' "$INSTALLER" > "$FUNCTIONS"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_file_contains() {
    local file="$1" expected="$2"
    grep -qxF "$expected" "$file" || fail "$file 缺少: $expected"
}

run_helper() {
    HOME="$1" bash -c '
        source "$1"
        ensure_fcitx5_autostart
        verify_fcitx5_autostart
    ' _ "$FUNCTIONS"
}

HOME_DIR="$WORK_DIR/home"
AUTOSTART="$HOME_DIR/.config/autostart/fcitx5.desktop"
mkdir -p "$HOME_DIR"

# 空 HOME 首次创建受管、启用的 XDG autostart 文件。
run_helper "$HOME_DIR" >/dev/null
[ -f "$AUTOSTART" ] || fail "首次运行未创建 autostart 文件"
for line in \
    'Type=Application' \
    'Exec=/usr/bin/fcitx5 -d' \
    'Hidden=false' \
    'X-GNOME-Autostart-enabled=true' \
    'X-Quick-Deploy-Managed=true'; do
    assert_file_contains "$AUTOSTART" "$line"
done

# 再次运行字节级不变，且不会产生备份。
first_digest="$(sha256sum "$AUTOSTART" | awk '{print $1}')"
run_helper "$HOME_DIR" >/dev/null
second_digest="$(sha256sum "$AUTOSTART" | awk '{print $1}')"
[ "$first_digest" = "$second_digest" ] || fail "相同配置的重跑修改了 autostart 文件"
if compgen -G "$AUTOSTART.bak.*" >/dev/null; then
    fail "相同配置的重跑创建了备份"
fi

# 精确匹配的旧版 quick-deploy 文件会备份并迁移到受管定义。
rm -f "$AUTOSTART"
cat > "$AUTOSTART" << 'EOF'
[Desktop Entry]
Name=Fcitx 5
GenericName=Input Method
Comment=Start Input Method
Exec=fcitx5
Icon=fcitx
Terminal=false
Type=Application
Categories=System;Utility;
StartupNotify=false
X-GNOME-Autostart-Phase=Applications
X-GNOME-AutoRestart=false
X-GNOME-Autostart-Notify=false
X-KDE-autostart-after=panel
EOF
run_helper "$HOME_DIR" >/dev/null
assert_file_contains "$AUTOSTART" 'Exec=/usr/bin/fcitx5 -d'
compgen -G "$AUTOSTART.bak.*" >/dev/null || fail "旧版文件迁移前未备份"

# 已受管但被手动禁用的文件会被恢复为启用定义。
sed -i 's/^X-GNOME-Autostart-enabled=true$/X-GNOME-Autostart-enabled=false/' "$AUTOSTART"
run_helper "$HOME_DIR" >/dev/null
assert_file_contains "$AUTOSTART" 'X-GNOME-Autostart-enabled=true'

# 未知用户文件必须保留，不能被安装器静默覆盖。
printf '%s\n' '[Desktop Entry]' 'Exec=custom-fcitx-wrapper' > "$AUTOSTART"
if run_helper "$HOME_DIR" >/dev/null 2>&1; then
    fail "未知用户自启动文件被错误接受"
fi
assert_file_contains "$AUTOSTART" 'Exec=custom-fcitx-wrapper'

echo "PASS: fcitx5 autostart creation, idempotency, legacy migration, managed repair, and foreign-file protection"
