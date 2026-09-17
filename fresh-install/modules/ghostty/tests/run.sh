#!/bin/bash
# Ghostty 模块的离线回归测试：只验证配置渲染、用户级 desktop 覆盖的生成/幂等/
# 拒绝语义、zsh hook 的实际行为与安装器的幂等/保护语义。
# 所有写入均在临时 HOME 中完成，桌面模板用临时 fixture。
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$MODULE_DIR/install.sh"
HOOK_SOURCE="$MODULE_DIR/ssh-mouse-reset.zsh"
WORK="$(mktemp -d /tmp/ghostty-module-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

bash -n "$INSTALLER" || fail "install.sh 语法错误"
bash -n "${BASH_SOURCE[0]}" || fail "测试脚本语法错误"
if ! command -v zsh >/dev/null 2>&1; then
    echo "SKIP: 未安装 zsh，无法执行 Ghostty zsh hook 回归测试"
    exit 0
fi
zsh -n "$HOOK_SOURCE" || fail "zsh hook 语法错误"

# 只取安装器函数定义，避免测试触发 main/apt/GUI。
sed '/^main "\$@"$/,$d' "$INSTALLER" > "$WORK/functions.sh"

# 配置必须保留 SSH 能力，同时提供不依赖 shell 的 reset 兜底；不要用
# mouse-reporting=false 牺牲 tmux 的鼠标功能。
HOME="$WORK/render-home" bash -c '
    source "$1"
    font_exists() { return 0; }
    resolved_working_directory() { printf "home\\n"; }
    render_config > "$2"
' _ "$WORK/functions.sh" "$WORK/config.ghostty"
grep -Fqx 'keybind = ctrl+shift+r=reset' "$WORK/config.ghostty" || fail "缺少 reset keybind"
grep -Fqx 'gtk-single-instance = false' "$WORK/config.ghostty" || fail "缺少 gtk-single-instance = false"
grep -Fqx '# 独立外部启动使用独立进程；内部窗口、tab 和 split 仍可能共享进程。' "$WORK/config.ghostty" \
    || fail "缺少独立进程注释行"
# 位置：必须紧邻 shell-integration-features 之前（与线上配置文件逐行一致）
awk '/^gtk-single-instance = false$/ { g = NR }
     /^shell-integration-features = / { s = NR }
     END { exit !(g && s && g + 1 == s) }' "$WORK/config.ghostty" \
    || fail "gtk-single-instance 必须紧邻 shell-integration-features 之前"
grep -Fqx 'shell-integration-features = ssh-env,ssh-terminfo' "$WORK/config.ghostty" || fail "SSH shell integration 被移除"
! grep -Fq 'mouse-reporting' "$WORK/config.ghostty" || fail "不应全局禁用 mouse-reporting"
if command -v ghostty >/dev/null 2>&1; then
    mkdir -p "$WORK/xdg/ghostty"
    cp "$WORK/config.ghostty" "$WORK/xdg/ghostty/config.ghostty"
    XDG_CONFIG_HOME="$WORK/xdg" HOME="$WORK/render-home" \
        ghostty +validate-config >"$WORK/ghostty-validate.log" 2>&1 \
        || { cat "$WORK/ghostty-validate.log" >&2; fail "Ghostty 配置校验失败"; }
fi

# ============================================================
# 用户级 desktop 覆盖：只改三处启动语义，幂等，拒绝覆盖带用户改动的文件
# ============================================================
# fixture 构造与系统模板同构的入口：两个 Exec= 带 --gtk-single-instance=true，
# DBusActivatable=true。全部读写都在临时 HOME + 临时模板上，不碰真实用户目录。
cat > "$WORK/vendor.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Name=Ghostty
Type=Application
TryExec=/usr/bin/ghostty
Exec=/usr/bin/ghostty --gtk-single-instance=true
Icon=com.mitchellh.ghostty
Categories=System;TerminalEmulator;
Actions=new-window;
DBusActivatable=true

[Desktop Action new-window]
Name=New Window
Exec=/usr/bin/ghostty --gtk-single-instance=true
EOF

# 首次写入 + 策略断言 + 幂等重跑 + --check 状态
HOME="$WORK/desktop-home" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    install_desktop_override >/dev/null
    verify_desktop_override >/dev/null
    test "$(grep -Fc -- "--gtk-single-instance=false" "$USER_DESKTOP_FILE")" -eq 2
    ! grep -Fq -- "--gtk-single-instance=true" "$USER_DESKTOP_FILE"
    ! grep -Fq "DBusActivatable=true" "$USER_DESKTOP_FILE"
    grep -Fqx "DBusActivatable=false" "$USER_DESKTOP_FILE"
    test "$(wc -l < "$USER_DESKTOP_FILE")" -eq "$(wc -l < "$DESKTOP_FILE")"
    grep -Fqx "Name=New Window" "$USER_DESKTOP_FILE"
    digest=$(sha256sum "$USER_DESKTOP_FILE" | cut -d" " -f1)
    install_desktop_override > "$HOME/rerun.out"
    grep -Fq "无变化" "$HOME/rerun.out"
    test "$digest" = "$(sha256sum "$USER_DESKTOP_FILE" | cut -d" " -f1)"
    ! compgen -G "$USER_DESKTOP_FILE.bak.*" >/dev/null
    test "$(desktop_override_state)" = "已是本次基准内容（两个 Exec 入口 --gtk-single-instance=false，DBusActivatable=false）"
' _ "$WORK/functions.sh" "$WORK/vendor.desktop" || fail "desktop 覆盖首次写入/幂等/状态报告失败"

# 厂商原样副本（无用户自有内容）：先备份再替换，并提示状态
HOME="$WORK/desktop-vendor-copy" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$USER_DESKTOP_DIR"
    cp "$DESKTOP_FILE" "$USER_DESKTOP_FILE"
    test "$(desktop_override_state)" = "与系统模板只差这三处启动语义（正式安装将备份并替换）"
    install_desktop_override >/dev/null
    verify_desktop_override >/dev/null
    test "$(grep -Fc -- "--gtk-single-instance=false" "$USER_DESKTOP_FILE")" -eq 2
    compgen -G "$USER_DESKTOP_FILE.bak.*" >/dev/null
    cmp "$USER_DESKTOP_FILE.bak."* "$DESKTOP_FILE"
' _ "$WORK/functions.sh" "$WORK/vendor.desktop" || fail "desktop 覆盖未按预期替换厂商副本并备份"

# 用户改过的文件：明确拒绝，不改字节、不留备份
HOME="$WORK/desktop-user-file" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$USER_DESKTOP_DIR"
    render_desktop_override > "$USER_DESKTOP_FILE"
    printf "X-User-Custom=1\n" >> "$USER_DESKTOP_FILE"
    digest=$(sha256sum "$USER_DESKTOP_FILE" | cut -d" " -f1)
    test "$(desktop_override_state)" = "含本模块之外的内容（正式安装将拒绝覆盖）"
    ( install_desktop_override ) >/dev/null 2>"$HOME/refuse.err" && exit 1
    grep -Fq "拒绝覆盖" "$HOME/refuse.err"
    grep -Fq "X-User-Custom=1" "$USER_DESKTOP_FILE"
    test "$digest" = "$(sha256sum "$USER_DESKTOP_FILE" | cut -d" " -f1)"
    ! compgen -G "$USER_DESKTOP_FILE.bak.*" >/dev/null
' _ "$WORK/functions.sh" "$WORK/vendor.desktop" || fail "desktop 覆盖没有拒绝用户自有改动"

# 目录 / 符号链接：拒绝且不破坏
HOME="$WORK/desktop-hostile" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$USER_DESKTOP_DIR" "$USER_DESKTOP_FILE"
    ( install_desktop_override ) >/dev/null 2>"$HOME/dir.err" && exit 1
    grep -Fq "不是普通文件" "$HOME/dir.err"
    test -d "$USER_DESKTOP_FILE"
    rmdir "$USER_DESKTOP_FILE"
    printf "KEEP\n" > "$HOME/real.desktop"
    ln -s "$HOME/real.desktop" "$USER_DESKTOP_FILE"
    ( install_desktop_override ) >/dev/null 2>"$HOME/link.err" && exit 1
    grep -Fq "符号链接" "$HOME/link.err"
    test -L "$USER_DESKTOP_FILE"
    test "$(cat "$HOME/real.desktop")" = KEEP
' _ "$WORK/functions.sh" "$WORK/vendor.desktop" || fail "desktop 覆盖没有拒绝目录/符号链接"

# 模板结构不符合预期：明确失败，不写文件；裸 Exec= 则补上显式标志
printf "[Desktop Entry]\nName=Ghostty\nExec=/opt/ghostty --gtk-single-instance=true\nDBusActivatable=true\n" \
    > "$WORK/vendor-unknown.desktop"
HOME="$WORK/desktop-bad-template" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$HOME"
    ( install_desktop_override ) >/dev/null 2>"$HOME/bad.err" && exit 1
    grep -Fq "系统桌面入口结构不符合预期" "$HOME/bad.err"
    test ! -e "$USER_DESKTOP_FILE"
    case "$(desktop_override_state)" in *结构不符合预期*) ;; *) exit 1 ;; esac
' _ "$WORK/functions.sh" "$WORK/vendor-unknown.desktop" || fail "结构异常的模板没有被拒绝"

printf "[Desktop Entry]\nName=Ghostty\nExec=/usr/bin/ghostty\nDBusActivatable=true\n" > "$WORK/vendor-bare.desktop"
HOME="$WORK/desktop-bare" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$HOME"
    install_desktop_override >/dev/null
    grep -Fqx "Exec=/usr/bin/ghostty --gtk-single-instance=false" "$USER_DESKTOP_FILE"
    grep -Fqx "DBusActivatable=false" "$USER_DESKTOP_FILE"
' _ "$WORK/functions.sh" "$WORK/vendor-bare.desktop" || fail "裸 Exec= 模板没有被补上显式标志"

# 模板 Exec= 带后续参数时只改标志值，不丢参数
printf "[Desktop Entry]\nName=Ghostty\nExec=/usr/bin/ghostty --gtk-single-instance=true %%U\nDBusActivatable=true\n" \
    > "$WORK/vendor-suffix.desktop"
HOME="$WORK/desktop-suffix" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$HOME"
    install_desktop_override >/dev/null
    grep -Fqx "Exec=/usr/bin/ghostty --gtk-single-instance=false %U" "$USER_DESKTOP_FILE"
' _ "$WORK/functions.sh" "$WORK/vendor-suffix.desktop" || fail "带参数的 Exec= 没有被正确改写"

# 真实系统模板（装了 ghostty 的机器上）：必须能通过生成与策略校验
if [ -f /usr/share/applications/com.mitchellh.ghostty.desktop ]; then
    HOME="$WORK/desktop-real-template" bash -c '
        set -euo pipefail
        source "$1"
        DESKTOP_FILE=/usr/share/applications/com.mitchellh.ghostty.desktop
        USER_DESKTOP_DIR="$HOME/.local/share/applications"
        USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
        mkdir -p "$HOME"
        install_desktop_override >/dev/null
        verify_desktop_override >/dev/null
    ' _ "$WORK/functions.sh" || fail "真实系统 desktop 模板无法生成独立进程覆盖"
fi

if command -v zsh >/dev/null 2>&1; then
    mkdir -p "$WORK/ghostty-resources"
    HOME="$WORK/matcher-home" GHOSTTY_RESOURCES_DIR="$WORK/ghostty-resources" \
        TERM_PROGRAM=ghostty zsh -f -i -c '
            source "$1"
            for line in \
                "ssh host" "command ssh host" "builtin ssh host" \
                "env -u FOO ssh host" "sudo -u root ssh host" \
                "FOO=1 ssh host"; do
                _quick_deploy_ghostty_ssh_preexec "$line"
                (( _quick_deploy_ghostty_ssh_mouse_pending == 1 )) || exit 1
            done
            for line in "echo ssh" "ssh-keygen host" "echo x; ssh host"; do
                _quick_deploy_ghostty_ssh_preexec "$line"
                (( _quick_deploy_ghostty_ssh_mouse_pending == 0 )) || exit 1
            done
            pre_before=${#preexec_functions}
            post_before=${#precmd_functions}
            source "$1"
            (( ${#preexec_functions} == pre_before && ${#precmd_functions} == post_before )) || exit 1
        ' _ "$HOOK_SOURCE" || fail "zsh ssh 命令识别失败"
fi

# 安装器 helper 的隔离 HOME 测试：首次写入、重复运行不备份，漂移先备份，
# .zshrc 的其它行/权限保留，符号链接拒绝改写。
HOME="$WORK/install-home" bash -c '
    set -euo pipefail
    source "$1"
    SSH_MOUSE_HOOK_SOURCE="$2"
    SSH_MOUSE_HOOK_CONFIG="$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME"
    mkdir -p "$HOME"
    printf "export KEEP_ME=1\\n" > "$HOME/.zshrc"
    chmod 600 "$HOME/.zshrc"
    install_ssh_mouse_hook >/dev/null
    test -f "$SSH_MOUSE_HOOK_CONFIG"
    grep -Fqx "export KEEP_ME=1" "$HOME/.zshrc"
    grep -Fqx "$ZSHRC_BLOCK_BEGIN" "$HOME/.zshrc"
    test "$(stat -c %a "$HOME/.zshrc")" = 600
    digest=$(sha256sum "$HOME/.zshrc" | cut -d" " -f1)
    install_ssh_mouse_hook >/dev/null
    test "$digest" = "$(sha256sum "$HOME/.zshrc" | cut -d" " -f1)"
    test "$(grep -Fc "$ZSHRC_BLOCK_BEGIN" "$HOME/.zshrc")" -eq 1
    printf "\\n# drift\\n" >> "$SSH_MOUSE_HOOK_CONFIG"
    install_ssh_mouse_hook >/dev/null
    cmp "$SSH_MOUSE_HOOK_SOURCE" "$SSH_MOUSE_HOOK_CONFIG"
    compgen -G "$SSH_MOUSE_HOOK_CONFIG.bak.*" >/dev/null
    rm -rf "$SSH_MOUSE_HOOK_CONFIG"
    mkdir "$SSH_MOUSE_HOOK_CONFIG"
    install_ssh_mouse_hook >/dev/null 2>"$HOME/directory.err"
    test -d "$SSH_MOUSE_HOOK_CONFIG"
    grep -Fq "不是普通文件" "$HOME/directory.err"
    rm -rf "$SSH_MOUSE_HOOK_CONFIG"
    printf "%s\\nKEEP" "$ZSHRC_BLOCK_BEGIN" > "$HOME/.zshrc"
    digest=$(sha256sum "$HOME/.zshrc" | cut -d" " -f1)
    install_ssh_mouse_hook >/dev/null 2>"$HOME/marker.err"
    test "$digest" = "$(sha256sum "$HOME/.zshrc" | cut -d" " -f1)"
    grep -Fq "标记不成对" "$HOME/marker.err"
    real="$HOME/real-zshrc"
    printf "KEEP\\n" > "$real"
    rm -f "$HOME/.zshrc"
    ln -s "$real" "$HOME/.zshrc"
    install_ssh_mouse_hook >/dev/null 2>"$HOME/symlink.err"
    test -L "$HOME/.zshrc"
    test "$(cat "$real")" = KEEP
    grep -Fq "符号链接" "$HOME/symlink.err"
' _ "$WORK/functions.sh" "$HOOK_SOURCE"

# 用 pty 驱动交互式 zsh，验证：Ghostty 守卫、ssh 命令返回后的 DECRST、
# 退出码不被 hook 改写，以及普通命令不产生复位。
cat > "$WORK/pty-check.py" <<'PY'
import os
import pty
import select
import subprocess
import tempfile
import time
import fcntl
import termios
import struct
import sys

hook, ghostty_resources, expect_reset = sys.argv[1:]
root = tempfile.mkdtemp(prefix="ghostty-pty-")
home = os.path.join(root, "home")
os.makedirs(home)
with open(os.path.join(home, ".zshrc"), "w") as f:
    f.write('PS1="Q%? > "\n')
    f.write(f'source "{hook}"\n')
    f.write('ssh() { printf "\\033[?1000h\\033[?1002h\\033[?1006h"; return 7 }\n')

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
env = os.environ.copy()
env.update(HOME=home, ZDOTDIR=home, TERM="xterm-256color",
           TERM_PROGRAM=("ghostty" if expect_reset == "yes" else "vscode"))
if expect_reset == "yes":
    env["GHOSTTY_RESOURCES_DIR"] = ghostty_resources
else:
    env.pop("GHOSTTY_RESOURCES_DIR", None)
proc = subprocess.Popen(["zsh", "-i"], stdin=slave, stdout=slave, stderr=slave,
                        env=env, close_fds=True)
os.close(slave)
buf = bytearray()

def drain(seconds=0.05):
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([master], [], [], 0.02)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                return
            if not data:
                return
            buf.extend(data)

def wait_for(marker, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if marker in buf:
            return
        drain()
    raise AssertionError(f"timed out waiting for {marker!r}; tail={bytes(buf[-500:])!r}")

try:
    wait_for(b"Q0 > ")
    buf.clear()
    os.write(master, b"ssh fake\n")
    wait_for(b"Q7 > ")
    segment = bytes(buf)
    reset = (b"\x1b[?1000l\x1b[?1001l\x1b[?1002l\x1b[?1003l"
             b"\x1b[?1005l\x1b[?1006l\x1b[?1015l\x1b[?1016l")
    count = segment.count(reset)
    if expect_reset == "yes":
        assert count == 1, (count, segment)
        assert b"\x1b[?1000h\x1b[?1002h\x1b[?1006h" in segment
        assert b"Q7 > " in segment
    else:
        assert count == 0, segment
    buf.clear()
    os.write(master, b"echo plain\n")
    wait_for(b"Q0 > ")
    assert reset not in bytes(buf), bytes(buf)
finally:
    try:
        os.write(master, b"exit\n")
    except OSError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    os.close(master)
    import shutil
    shutil.rmtree(root, ignore_errors=True)
PY
GHOSTTY_RESOURCES_DIR="$WORK/ghostty-resources"
mkdir -p "$GHOSTTY_RESOURCES_DIR"
python3 "$WORK/pty-check.py" "$HOOK_SOURCE" "$GHOSTTY_RESOURCES_DIR" yes
python3 "$WORK/pty-check.py" "$HOOK_SOURCE" "$GHOSTTY_RESOURCES_DIR" no

echo "PASS: Ghostty SSH mouse cleanup, reset fallback, syntax, idempotency, isolation, and desktop override"
