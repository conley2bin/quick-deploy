#!/bin/bash
# Ghostty 模块的离线回归测试：只验证配置渲染、用户级 desktop 覆盖的生成/幂等/
# 拒绝语义、zsh hook 的实际行为与安装器的幂等/保护语义。
# 所有写入均在临时 HOME 中完成，桌面模板用临时 fixture。
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../modules/ghostty" && pwd)"
INSTALLER="$MODULE_DIR/install.sh"
WORK="$(mktemp -d /tmp/ghostty-module-test.XXXXXX)"
HOOK_SOURCE="$WORK/ghostty-ssh-mouse-reset.zsh"
PASTE_HOOK_SOURCE="$WORK/ghostty-local-paste.zsh"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

bash -n "$INSTALLER" || fail "install.sh 语法错误"
bash -n "${BASH_SOURCE[0]}" || fail "测试脚本语法错误"
mapfile -t module_shell_files < <(find "$MODULE_DIR" -type f \
    \( -name '*.sh' -o -name '*.bash' -o -name '*.zsh' \) | sort)
[ "${#module_shell_files[@]}" -eq 1 ] && [ "${module_shell_files[0]}" = "$INSTALLER" ] \
    || fail "Ghostty 模块不止 install.sh 一个 shell 源文件"
# 只取安装器函数定义，避免测试触发 main/apt/GUI；运行时 hook 从唯一
# shell 源文件渲染，不再依赖仓库中的第二个 .zsh 源文件。
sed '/^main "\$@"$/,$d' "$INSTALLER" > "$WORK/functions.sh"
HOME="$WORK/render-hook-home" bash -c 'source "$1"; render_ssh_mouse_hook' _ "$WORK/functions.sh" > "$HOOK_SOURCE"
HOME="$WORK/render-hook-home" bash -c 'source "$1"; render_local_paste_hook' _ "$WORK/functions.sh" > "$PASTE_HOOK_SOURCE"
HOME="$WORK/render-hook-home" bash -c 'source "$1"; render_local_paste_hook v1' \
    _ "$WORK/functions.sh" > "$WORK/ghostty-local-paste-v1.zsh"
printf '6d53e9c81646f1ebb72872df4ec9f014117957042bce2ccd33fa2bfcc43423eb  %s\n' \
    "$WORK/ghostty-local-paste-v1.zsh" | sha256sum --check --status \
    || fail "历史 v1 粘贴 hook 渲染不再是已部署的精确字节"
# 真正打包的模块副本只含 install.sh + C 源码：没有独立 .zsh/.sh 资源。
# 源码渲染应与原模块等同，并在 --check/正式安装共用的预检下完整。
mkdir -p "$WORK/packaged-module"
cp "$INSTALLER" "$MODULE_DIR/wayland-backpressure.c" "$WORK/packaged-module/"
sed '/^main "\$@"$/,$d' "$WORK/packaged-module/install.sh" > "$WORK/packaged-module/functions.sh"
HOME="$WORK/packaged-home" bash -c '
    source "$1"
    CHECK_ONLY=true
    preflight_commands
    render_ssh_mouse_hook
' _ "$WORK/packaged-module/functions.sh" > "$WORK/packaged-hook.zsh"
cmp -s "$HOOK_SOURCE" "$WORK/packaged-hook.zsh" || fail "精简打包丢失 SSH hook 源码"
HOME="$WORK/packaged-home" bash -c 'source "$1"; render_local_paste_hook' \
    _ "$WORK/packaged-module/functions.sh" > "$WORK/packaged-paste-hook.zsh"
cmp -s "$PASTE_HOOK_SOURCE" "$WORK/packaged-paste-hook.zsh" \
    || fail "精简打包丢失 zsh 粘贴 hook 源码"
# zsh 只影响 hook 语法、hook 行为与 PTY 三部分；其余测试（配置渲染、
# 显示会话矩阵、desktop 覆盖、安装器 helper、terminfo 验证）不依赖 zsh，
# 必须继续跑，不能被一道提前 exit 挡住。
HAVE_ZSH=false
if command -v zsh >/dev/null 2>&1; then
    HAVE_ZSH=true
    zsh -n "$HOOK_SOURCE" || fail "zsh SSH hook 语法错误"
    zsh -n "$PASTE_HOOK_SOURCE" || fail "zsh 粘贴 hook 语法错误"
    zsh -n "$WORK/ghostty-local-paste-v1.zsh" || fail "历史 v1 hook 语法错误"
else
    echo "SKIP: 未安装 zsh，跳过 zsh hook 语法、hook 行为与 PTY 测试（其余测试继续）"
fi

# setup.sh 打包只需模块内的 install.sh 与 C 源码。运行时 hook 由
# install.sh 生成；--check 与正式安装只需拒绝缺失的 C 源码。
HOME="$WORK/module-bundle" bash -c '
    set -euo pipefail
    source "$1"
    CHECK_ONLY=true
    BACKPRESSURE_SOURCE="$2"
    mkdir -p "$HOME"
    preflight_commands
    BACKPRESSURE_SOURCE="$HOME/missing-backpressure.c"
    ( preflight_commands ) > "$HOME/backpressure.err" 2>&1 && exit 1
    grep -Fq "模块不完整，缺少 Wayland 修复源码" "$HOME/backpressure.err"
    test ! -e "$HOME/.config/ghostty/config.ghostty"
' _ "$WORK/functions.sh" "$MODULE_DIR/wayland-backpressure.c" \
    || fail "模块资源缺失未在安装写入前显式拒绝"

# 配置必须保留 SSH 能力，同时提供不依赖 shell 的 reset 兜底；不要用
# mouse-reporting=false 牺牲 tmux 的鼠标功能。
# 显示会话环境矩阵：基准与 headless 用 env -u 真正清掉三个变量（“定义为空”
# 与“未定义”不是一回事，渲染若读它们会得到不同结果）；Wayland/X11 只设
# 自己那一组变量。四份渲染必须逐字节一致：这只证明配置渲染不按显示服务器
# 分叉，不证明任何剪贴板后端的实际行为。
render_variant() {
    local out="$1"
    shift
    env -u XDG_SESSION_TYPE -u WAYLAND_DISPLAY -u DISPLAY HOME="$WORK/render-home" "$@" \
        bash -c '
            source "$1"
            font_exists() { return 0; }
            resolved_working_directory() { printf "home\\n"; }
            render_config
        ' _ "$WORK/functions.sh" > "$out"
}
render_variant "$WORK/config.ghostty"
grep -Fqx 'keybind = ctrl+shift+r=reset' "$WORK/config.ghostty" || fail "缺少 reset keybind"
# 鼠标上报下普通拖选属于 Pi/tmux，Shift+拖选产生 Ghostty 本地选区，
# Ctrl+Shift+C 复制；Ctrl+Shift+V 向 Pi 发送原始 Ctrl+V。三行显式写入，
# never 还禁止程序用 XTSHIFTESCAPE 抢走 Shift，行为不随上游默认漂移。
grep -Fqx 'mouse-shift-capture = never' "$WORK/config.ghostty" || fail "缺少 mouse-shift-capture = never"
grep -Fqx 'keybind = ctrl+shift+c=copy_to_clipboard:mixed' "$WORK/config.ghostty" \
    || fail "缺少 ctrl+shift+c 复制绑定"
grep -Fqx 'keybind = ctrl+shift+v=text:\x16' "$WORK/config.ghostty" \
    || fail "缺少 ctrl+shift+v 原始 Ctrl+V 绑定"
! grep -Eq '^keybind = ctrl\+(c|v)=' "$WORK/config.ghostty" \
    || fail "Ctrl+C/V 不应被全局改绑"
# 只有 never 能保证程序不能通过 XTSHIFTESCAPE 改写这条本地选择路径。
! grep -Eq '^mouse-shift-capture[[:space:]]*=[[:space:]]*(true|false|always)[[:space:]]*$' "$WORK/config.ghostty" \
    || fail "mouse-shift-capture 必须固定为 never"
# 剪贴板策略：OSC 52 写允许、读拒绝。写入是远端 tmux 复制到达本机剪贴板的
# 入口；读取只服务于把本机剪贴板回传给终端里的程序，本模块不需要，
# 拒绝读取也暴露面最小。
grep -Fqx 'clipboard-write = allow' "$WORK/config.ghostty" || fail "缺少 clipboard-write = allow"
grep -Fqx 'clipboard-read = deny' "$WORK/config.ghostty" || fail "缺少 clipboard-read = deny"
render_variant "$WORK/config.headless.ghostty"
render_variant "$WORK/config.wayland.ghostty" XDG_SESSION_TYPE=wayland WAYLAND_DISPLAY=wayland-0
render_variant "$WORK/config.x11.ghostty" XDG_SESSION_TYPE=x11 DISPLAY=:0
cmp -s "$WORK/config.ghostty" "$WORK/config.headless.ghostty" \
    || fail "无显示会话变量时渲染结果与基准不一致"
cmp -s "$WORK/config.ghostty" "$WORK/config.wayland.ghostty" \
    || fail "Wayland 会话下渲染结果与基准不一致"
cmp -s "$WORK/config.ghostty" "$WORK/config.x11.ghostty" \
    || fail "X11 会话下渲染结果与基准不一致"


# write_config 幂等语义：首次写入基准内容（含剪贴板策略与鼠标/按键绑定），
# 重跑不重写、不备份、字节不变。
HOME="$WORK/config-home" bash -c '
    set -euo pipefail
    source "$1"
    font_exists() { return 0; }
    resolved_working_directory() { printf "home\\n"; }
    CONFIG_DIR="$HOME/.config/ghostty"
    CONFIG_FILE="$CONFIG_DIR/config.ghostty"
    mkdir -p "$HOME"
    write_config > "$HOME/first.out"
    grep -Fq "已写入" "$HOME/first.out"
    grep -Fqx "clipboard-write = allow" "$CONFIG_FILE"
    grep -Fqx "clipboard-read = deny" "$CONFIG_FILE"
    grep -Fqx "mouse-shift-capture = never" "$CONFIG_FILE"
    grep -Fqx "keybind = ctrl+shift+c=copy_to_clipboard:mixed" "$CONFIG_FILE"
    grep -Fqx '\''keybind = ctrl+shift+v=text:\x16'\'' "$CONFIG_FILE"
    digest=$(sha256sum "$CONFIG_FILE" | cut -d" " -f1)
    write_config > "$HOME/second.out"
    grep -Fq "配置文件无变化" "$HOME/second.out"
    test "$digest" = "$(sha256sum "$CONFIG_FILE" | cut -d" " -f1)"
    ! compgen -G "$CONFIG_FILE.bak.*" >/dev/null
' _ "$WORK/functions.sh" || fail "配置写入的幂等语义失败"
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
if [ -x /usr/bin/ghostty ]; then
    mkdir -p "$WORK/xdg/ghostty"
    cp "$WORK/config.ghostty" "$WORK/xdg/ghostty/config.ghostty"
    XDG_CONFIG_HOME="$WORK/xdg" HOME="$WORK/render-home" \
        /usr/bin/ghostty +validate-config >"$WORK/ghostty-validate.log" 2>&1 \
        || { cat "$WORK/ghostty-validate.log" >&2; fail "Ghostty 配置校验失败"; }
fi

# ============================================================
# Pi 专用入口：仅覆盖当前新进程的 Ctrl+Shift+V，普通启动器保持原样。
# 录制真实渲染脚本交给受保护启动器的 argv；不启动 GUI 或读写剪贴板。
# ============================================================
cat > "$WORK/ghostty-argv-recorder" <<'EOF'
#!/bin/sh
for arg in "$@"; do printf '%s\0' "$arg"; done
EOF
chmod 755 "$WORK/ghostty-argv-recorder"
HOME="$WORK/pi-launcher-home" bash -c '
    set -euo pipefail
    source "$1"
    mkdir -p "$(dirname "$USER_GHOSTTY_LAUNCHER")"
    cp "$2" "$USER_GHOSTTY_LAUNCHER"
    cp "$USER_GHOSTTY_LAUNCHER" "$HOME/normal-baseline"
    test "$(pi_launcher_state)" = "未安装（正式安装将生成）"
    install_pi_launcher > "$HOME/first.log"
    verify_pi_launcher >/dev/null
    sh -n "$USER_PI_GHOSTTY_LAUNCHER"
    cmp -s "$USER_PI_GHOSTTY_LAUNCHER" <(render_pi_launcher)
    cmp -s "$USER_GHOSTTY_LAUNCHER" "$HOME/normal-baseline"
    test "$(pi_launcher_state)" = "已是当前基准"
    digest=$(sha256sum "$USER_PI_GHOSTTY_LAUNCHER" | cut -d" " -f1)
    install_pi_launcher > "$HOME/second.log"
    grep -Fq "文件无变化" "$HOME/second.log"
    test "$digest" = "$(sha256sum "$USER_PI_GHOSTTY_LAUNCHER" | cut -d" " -f1)"
    ! compgen -G "$USER_PI_GHOSTTY_LAUNCHER.bak.*" >/dev/null
    cmp -s "$USER_GHOSTTY_LAUNCHER" "$HOME/normal-baseline"
    "$USER_PI_GHOSTTY_LAUNCHER" -e tmux attach-session -t quick-deploy_pi-agent > "$HOME/attach-argv"
    "$USER_PI_GHOSTTY_LAUNCHER" --working-directory=inherit > "$HOME/generic-argv"
    "$USER_GHOSTTY_LAUNCHER" > "$HOME/normal-argv"
' _ "$WORK/functions.sh" "$WORK/ghostty-argv-recorder" \
    || fail "Pi 专用启动器未安全渲染、转发参数或幂等安装"
python3 - "$WORK/pi-launcher-home" <<'PY'
from pathlib import Path
import sys

home = Path(sys.argv[1])
def argv(name):
    raw = (home / name).read_bytes()
    assert raw.endswith(b"\0"), (name, raw)
    return [value.decode() for value in raw[:-1].split(b"\0")]

special = ["--gtk-single-instance=false", r"--keybind=ctrl+shift+v=text:\x16"]
assert argv("attach-argv") == special + ["-e", "tmux", "attach-session", "-t", "quick-deploy_pi-agent"]
assert argv("generic-argv") == special + ["--working-directory=inherit"]
assert (home / "normal-argv").read_bytes() == b"", "ordinary Ghostty entry gained flags"
PY

for kind in custom symlink directory nonexec; do
    HOME="$WORK/pi-refuse-$kind" bash -c '
        set -euo pipefail
        source "$1"
        mkdir -p "$(dirname "$USER_PI_GHOSTTY_LAUNCHER")"
        case "$2" in
            custom) printf "KEEP\n" > "$USER_PI_GHOSTTY_LAUNCHER" ;;
            symlink) printf "KEEP\n" > "$HOME/target"; ln -s "$HOME/target" "$USER_PI_GHOSTTY_LAUNCHER" ;;
            directory) mkdir "$USER_PI_GHOSTTY_LAUNCHER"; printf "KEEP\n" > "$USER_PI_GHOSTTY_LAUNCHER/content" ;;
            nonexec) render_pi_launcher > "$USER_PI_GHOSTTY_LAUNCHER"; chmod 644 "$USER_PI_GHOSTTY_LAUNCHER" ;;
        esac
        case "$2" in
            custom) test "$(pi_launcher_state)" = "含非基准内容（正式安装将拒绝覆盖）" ;;
            symlink) test "$(pi_launcher_state)" = "符号链接（正式安装将拒绝覆盖）" ;;
            directory) test "$(pi_launcher_state)" = "不是普通文件（正式安装将拒绝覆盖）" ;;
            nonexec) test "$(pi_launcher_state)" = "内容相同但不可执行（正式安装将拒绝覆盖）" ;;
        esac
        ( install_pi_launcher ) > "$HOME/refuse.log" 2>&1 && exit 1
        grep -Fq "拒绝覆盖" "$HOME/refuse.log"
        ! compgen -G "$USER_PI_GHOSTTY_LAUNCHER.bak.*" >/dev/null
        case "$2" in
            custom) test "$(cat "$USER_PI_GHOSTTY_LAUNCHER")" = KEEP ;;
            symlink) test -L "$USER_PI_GHOSTTY_LAUNCHER" && test "$(cat "$HOME/target")" = KEEP ;;
            directory) test -d "$USER_PI_GHOSTTY_LAUNCHER" && test "$(cat "$USER_PI_GHOSTTY_LAUNCHER/content")" = KEEP ;;
            nonexec) test -f "$USER_PI_GHOSTTY_LAUNCHER" && test ! -x "$USER_PI_GHOSTTY_LAUNCHER" && cmp -s "$USER_PI_GHOSTTY_LAUNCHER" <(render_pi_launcher) ;;
        esac
    ' _ "$WORK/functions.sh" "$kind" || fail "Pi 专用启动器未拒绝 $kind 用户目标"
done

# ============================================================
# 真实窄安装入口：复制同一 install.sh 到无 C 源码的临时包，配备已托管的
# 启动器/库。若误走完整预检，会在任何写入前因缺 C 源码失败；apt/编译器/
# GSettings 均用拒绝命令封住，且没有显示会话。全程只在私有 HOME 写入。
# ============================================================
mkdir -p "$WORK/pi-only-package" "$WORK/pi-only-deny"
cp "$INSTALLER" "$WORK/pi-only-package/install.sh"
for blocked in apt-get cc gsettings curl update-desktop-database; do
    ln -s /bin/false "$WORK/pi-only-deny/$blocked"
done
prepare_pi_only_home() {
    HOME="$1" bash -c '
        set -euo pipefail
        source "$1"
        mkdir -p "$(dirname "$USER_GHOSTTY_LAUNCHER")" "$(dirname "$BACKPRESSURE_LIBRARY")" \
            "$BACKPRESSURE_STATE" "$CONFIG_DIR" "$USER_DESKTOP_DIR" \
            "$HOME/.config/fontconfig/conf.d" "$HOME/.config"
        render_backpressure_launcher > "$USER_GHOSTTY_LAUNCHER"
        chmod 755 "$USER_GHOSTTY_LAUNCHER"
        printf "SHIM-BASELINE\n" > "$BACKPRESSURE_LIBRARY"
        cp "$BACKPRESSURE_LIBRARY" "$BACKPRESSURE_STATE/enabled.library"
        printf "GLOBAL-CONFIG-KEEP\n" > "$CONFIG_FILE"
        printf "DESKTOP-KEEP\n" > "$USER_DESKTOP_FILE"
        printf "TERMINAL-KEEP\n" > "$TERMINAL_WRAPPER"
        printf "XDG-KEEP\n" > "$XDG_TERMINALS_FILE"
        printf "FONT-KEEP\n" > "$HOME/.config/fontconfig/conf.d/89-ghostty-zh-mono.conf"
        printf "ZSHRC-KEEP\n" > "$HOME/.zshrc"
    ' _ "$WORK/functions.sh"
}
snapshot_pi_only_home() {
    python3 - "$1" "${2:-.local/bin/ghostty-pi}" <<'PY'
from pathlib import Path
import hashlib
import stat
import sys

root = Path(sys.argv[1])
excluded = sys.argv[2]
for path in sorted(root.rglob("*")):
    rel = path.relative_to(root).as_posix()
    if rel == excluded:
        continue
    mode = stat.S_IMODE(path.lstat().st_mode)
    if path.is_symlink():
        value = ("link", str(path.readlink()))
    elif path.is_file():
        value = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
    else:
        value = ("directory",)
    print(repr((rel, mode, value)))
PY
}
run_pi_only() {
    local private_home="$1"
    shift
    env -u DISPLAY -u WAYLAND_DISPLAY HOME="$private_home" \
        PATH="$WORK/pi-only-deny:$PATH" SUDO_CMD=/bin/false GSETTINGS_BACKEND=memory \
        bash "$WORK/pi-only-package/install.sh" "$@"
}
if [ -x /usr/bin/ghostty ]; then
    PI_ONLY_HOME="$WORK/pi-only-live-entry"
    prepare_pi_only_home "$PI_ONLY_HOME"
    snapshot_pi_only_home "$PI_ONLY_HOME" > "$WORK/pi-only-before"
    run_pi_only "$PI_ONLY_HOME" --pi-launcher-only > "$WORK/pi-only-first.log" \
        || fail "Pi 窄安装未接受已托管的启动器与库"
    test -x "$PI_ONLY_HOME/.local/bin/ghostty-pi" || fail "Pi 窄安装未生成启动器"
    HOME="$PI_ONLY_HOME" bash -c '
        source "$1"
        cmp -s "$USER_PI_GHOSTTY_LAUNCHER" <(render_pi_launcher)
    ' _ "$WORK/functions.sh" || fail "Pi 窄安装目标内容不是受管基准"
    snapshot_pi_only_home "$PI_ONLY_HOME" > "$WORK/pi-only-after"
    cmp -s "$WORK/pi-only-before" "$WORK/pi-only-after" \
        || fail "Pi 窄安装修改了普通 Ghostty/配置/桌面或其他非目标文件"
    pi_inode=$(stat -c %i "$PI_ONLY_HOME/.local/bin/ghostty-pi")
    run_pi_only "$PI_ONLY_HOME" --pi-launcher-only > "$WORK/pi-only-second.log" \
        || fail "Pi 窄安装重跑失败"
    grep -Fq "文件无变化" "$WORK/pi-only-second.log" || fail "Pi 窄安装重跑未跳过相同目标"
    test "$pi_inode" = "$(stat -c %i "$PI_ONLY_HOME/.local/bin/ghostty-pi")" \
        || fail "Pi 窄安装重跑替换了相同目标"
    snapshot_pi_only_home "$PI_ONLY_HOME" > "$WORK/pi-only-rerun"
    cmp -s "$WORK/pi-only-before" "$WORK/pi-only-rerun" \
        || fail "Pi 窄安装重跑修改了非目标文件"
    ! compgen -G "$PI_ONLY_HOME/.local/bin/ghostty-pi.bak.*" >/dev/null \
        || fail "Pi 窄安装意外生成启动器备份"

    for other in --check --ppa-only --deb-only --default-terminal --no-default-terminal --paste-keybind-only --shell-paste-only; do
        if run_pi_only "$PI_ONLY_HOME" --pi-launcher-only "$other" > "$WORK/pi-only-args.log" 2>&1; then
            fail "Pi 窄安装接受了冲突选项 $other"
        fi
        grep -Fq "不能与其他安装或预检选项组合" "$WORK/pi-only-args.log" \
            || fail "Pi 窄安装冲突选项 $other 未明确拒绝"
    done

    for scenario in changed-launcher missing-copy mismatched-library symlink-library nonexec-launcher; do
        private_home="$WORK/pi-only-$scenario"
        prepare_pi_only_home "$private_home"
        case "$scenario" in
            changed-launcher) printf "USER-CHANGE\n" >> "$private_home/.local/bin/ghostty" ;;
            missing-copy) rm "$private_home/.config/ghostty/wayland-backpressure/enabled.library" ;;
            mismatched-library) printf "MISMATCH\n" >> "$private_home/.local/lib/ghostty/libwayland-backpressure.so" ;;
            symlink-library)
                mv "$private_home/.local/lib/ghostty/libwayland-backpressure.so" "$private_home/real-shim"
                ln -s "$private_home/real-shim" "$private_home/.local/lib/ghostty/libwayland-backpressure.so"
                ;;
            nonexec-launcher) chmod 644 "$private_home/.local/bin/ghostty" ;;
        esac
        snapshot_pi_only_home "$private_home" > "$WORK/$scenario.before"
        if run_pi_only "$private_home" --pi-launcher-only > "$WORK/$scenario.log" 2>&1; then
            fail "Pi 窄安装接受了损坏的前置状态 $scenario"
        fi
        test ! -e "$private_home/.local/bin/ghostty-pi" \
            || fail "Pi 窄安装在前置校验失败后写入启动器 $scenario"
        snapshot_pi_only_home "$private_home" > "$WORK/$scenario.after"
        cmp -s "$WORK/$scenario.before" "$WORK/$scenario.after" \
            || fail "Pi 窄安装失败后修改了文件 $scenario"
    done

    private_home="$WORK/pi-only-conflict"
    prepare_pi_only_home "$private_home"
    printf "KEEP\n" > "$private_home/.local/bin/ghostty-pi"
    snapshot_pi_only_home "$private_home" > "$WORK/pi-only-conflict.before"
    if run_pi_only "$private_home" --pi-launcher-only > "$WORK/pi-only-conflict.log" 2>&1; then
        fail "Pi 窄安装覆盖了自有 ghostty-pi 文件"
    fi
    grep -Fq "拒绝覆盖" "$WORK/pi-only-conflict.log" || fail "Pi 窄安装未说明拒绝原因"
    test "$(cat "$private_home/.local/bin/ghostty-pi")" = KEEP \
        || fail "Pi 窄安装改变了自有目标"
    snapshot_pi_only_home "$private_home" > "$WORK/pi-only-conflict.after"
    cmp -s "$WORK/pi-only-conflict.before" "$WORK/pi-only-conflict.after" \
        || fail "Pi 窄安装拒绝时修改了普通受管文件"
else
    echo "SKIP: 本机未安装 /usr/bin/ghostty，真实 --pi-launcher-only 入口需要既有二进制"
fi

# ============================================================
# 真实全局单键窄迁移：同一 install.sh 副本无 C 源码，阻断 apt/cc/
# gsettings/GUI，只在私有 HOME 用实际渲染器生成历史逐字节基准。
# 迁移结果必须仅替换一行，不碰启动器、桌面、字体或任何其它文件。
# ============================================================
PASTE_ONLY_HOME="$WORK/paste-only-baseline"
prepare_pi_only_home "$PASTE_ONLY_HOME"
HOME="$PASTE_ONLY_HOME" bash -c '
    set -euo pipefail
    source "$1"
    render_config paste_from_clipboard > "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
' _ "$WORK/functions.sh" || fail "无法生成旧版受管配置"
cp "$PASTE_ONLY_HOME/.config/ghostty/config.ghostty" "$WORK/paste-old-baseline"
snapshot_pi_only_home "$PASTE_ONLY_HOME" .config/ghostty/config.ghostty > "$WORK/paste-only-before"
run_pi_only "$PASTE_ONLY_HOME" --paste-keybind-only > "$WORK/paste-only-first.log" \
    || fail "全局单键窄迁移未接受旧版受管基准"
python3 - "$WORK/paste-old-baseline" "$PASTE_ONLY_HOME/.config/ghostty/config.ghostty" <<'PY'
from pathlib import Path
import sys

before = Path(sys.argv[1]).read_bytes()
after = Path(sys.argv[2]).read_bytes()
old = b"keybind = ctrl+shift+v=paste_from_clipboard\n"
new = b"keybind = ctrl+shift+v=text:\\x16\n"
assert before.count(old) == 1, "old baseline lacks unique keybind"
assert after == before.replace(old, new), "migration changed more than one binding"
PY
test "$(stat -c %a "$PASTE_ONLY_HOME/.config/ghostty/config.ghostty")" = 600 \
    || fail "全局单键窄迁移未保留配置权限"
snapshot_pi_only_home "$PASTE_ONLY_HOME" .config/ghostty/config.ghostty > "$WORK/paste-only-after"
cmp -s "$WORK/paste-only-before" "$WORK/paste-only-after" \
    || fail "全局单键窄迁移修改了非目标文件（包括普通启动器）"
paste_inode=$(stat -c %i "$PASTE_ONLY_HOME/.config/ghostty/config.ghostty")
run_pi_only "$PASTE_ONLY_HOME" --paste-keybind-only > "$WORK/paste-only-second.log" \
    || fail "全局单键窄迁移重跑失败"
grep -Fq "已是当前基准，跳过写入" "$WORK/paste-only-second.log" \
    || fail "全局单键窄迁移重跑没有识别新基准"
test "$paste_inode" = "$(stat -c %i "$PASTE_ONLY_HOME/.config/ghostty/config.ghostty")" \
    || fail "全局单键窄迁移重跑替换了配置文件"
snapshot_pi_only_home "$PASTE_ONLY_HOME" .config/ghostty/config.ghostty > "$WORK/paste-only-rerun"
cmp -s "$WORK/paste-only-before" "$WORK/paste-only-rerun" \
    || fail "全局单键窄迁移重跑修改了其它文件"
! compgen -G "$PASTE_ONLY_HOME/.config/ghostty/config.ghostty.bak.*" >/dev/null \
    || fail "全局单键窄迁移写了非目标备份"

for other in --check --ppa-only --deb-only --default-terminal --no-default-terminal --pi-launcher-only --shell-paste-only; do
    if run_pi_only "$PASTE_ONLY_HOME" --paste-keybind-only "$other" > "$WORK/paste-only-args.log" 2>&1; then
        fail "全局单键窄迁移接受了冲突选项 $other"
    fi
    grep -Fq "不能与其他安装或预检选项组合" "$WORK/paste-only-args.log" \
        || fail "全局单键窄迁移未明确拒绝冲突选项 $other"
done

for scenario in custom-old custom-new symlink directory missing symlink-config-dir; do
    private_home="$WORK/paste-refuse-$scenario"
    prepare_pi_only_home "$private_home"
    HOME="$private_home" bash -c '
        set -euo pipefail
        source "$1"
        render_config "$2" > "$CONFIG_FILE"
    ' _ "$WORK/functions.sh" paste_from_clipboard \
        || fail "无法生成拒绝场景的受管配置 $scenario"
    config="$private_home/.config/ghostty/config.ghostty"
    case "$scenario" in
        custom-old) printf "# USER CUSTOM\n" >> "$config" ;;
        custom-new)
            HOME="$private_home" bash -c 'source "$1"; render_config > "$CONFIG_FILE"' _ "$WORK/functions.sh"
            printf "# USER CUSTOM\n" >> "$config" ;;
        symlink) mv "$config" "$private_home/original-config"; ln -s "$private_home/original-config" "$config" ;;
        directory) rm "$config"; mkdir "$config"; printf "KEEP\n" > "$config/content" ;;
        missing) rm "$config" ;;
        symlink-config-dir)
            mv "$private_home/.config/ghostty" "$private_home/real-ghostty-config"
            ln -s "$private_home/real-ghostty-config" "$private_home/.config/ghostty"
            ;;
    esac
    snapshot_pi_only_home "$private_home" > "$WORK/paste-$scenario.before"
    if run_pi_only "$private_home" --paste-keybind-only > "$WORK/paste-$scenario.log" 2>&1; then
        fail "全局单键窄迁移接受了用户内容/符号链接/缺失文件 $scenario"
    fi
    grep -Eq "拒绝(覆盖|窄迁移)" "$WORK/paste-$scenario.log" \
        || fail "全局单键窄迁移未明确拒绝 $scenario"
    snapshot_pi_only_home "$private_home" > "$WORK/paste-$scenario.after"
    cmp -s "$WORK/paste-$scenario.before" "$WORK/paste-$scenario.after" \
        || fail "全局单键窄迁移拒绝 $scenario 时修改了文件"
done

# ============================================================
# 已装机器的 zsh 粘贴窄模式：旧受管块逐字节迁移、幂等、拒绝用户内容。
# 使用无 C 源码的包 + 拒绝命令，不会 apt/编译/GSettings/启动 GUI。
# ============================================================
if [ "$HAVE_ZSH" = true ]; then
mkdir -p "$WORK/paste-fake-bin"
cat > "$WORK/paste-fake-bin/wl-paste" <<'EOF'
#!/bin/sh
[ "$*" = '--no-newline --type text' ] || exit 40
printf 'read\n' >> "$FAKE_WL_LOG"
case "$(cat "$FAKE_CLIPBOARD_MODE")" in
    fail) printf PARTIAL; exit 23 ;;
    timeout) sleep 5; exit 0 ;;
    *) cat "$FAKE_CLIPBOARD_TEXT" ;;
esac
EOF
chmod 755 "$WORK/paste-fake-bin/wl-paste"
prepare_shell_paste_home() {
    local home="$1"
    prepare_pi_only_home "$home"
    HOME="$home" bash -c '
        set -euo pipefail
        source "$1"
        render_config > "$CONFIG_FILE"
        render_ssh_mouse_hook > "$SSH_MOUSE_HOOK_CONFIG"
        printf '\''PS1="READY:%%? > "\nexport USER_CONTENT=1\n'\'' > "$HOME/.zshrc"
        render_legacy_zshrc_hook_block >> "$HOME/.zshrc"
        cat >> "$HOME/.zshrc" <<'\''ZSHRC_TAIL'\''
_capture_zle_buffer() {
    print -rn -- "$BUFFER" > "$HOME/zle-buffer.tmp"
    mv -f -- "$HOME/zle-buffer.tmp" "$HOME/zle-buffer"
}
_reset_zle_buffer() { BUFFER='' }
zle -N _capture_zle_buffer
zle -N _reset_zle_buffer
bindkey '^G' _capture_zle_buffer
bindkey '^X' _reset_zle_buffer
ZSHRC_TAIL
        chmod 600 "$HOME/.zshrc"
    ' _ "$WORK/functions.sh"
}
run_shell_only() {
    local private_home="$1"
    shift
    env -u DISPLAY -u WAYLAND_DISPLAY HOME="$private_home" \
        PATH="$WORK/paste-fake-bin:$WORK/pi-only-deny:$PATH" SUDO_CMD=/bin/false GSETTINGS_BACKEND=memory \
        bash "$WORK/pi-only-package/install.sh" "$@"
}
SHELL_ONLY_HOME="$WORK/shell-paste-home"
prepare_shell_paste_home "$SHELL_ONLY_HOME"
cp "$SHELL_ONLY_HOME/.zshrc" "$WORK/zshrc-old"
cp "$SHELL_ONLY_HOME/.config/ghostty/config.ghostty" "$WORK/shell-global-config"
snapshot_pi_only_home "$SHELL_ONLY_HOME" > "$WORK/shell-before"
run_shell_only "$SHELL_ONLY_HOME" --shell-paste-only > "$WORK/shell-first.log" \
    || fail "zsh 粘贴窄模式未接受旧版托管块"
PASTE_INSTALLED="$SHELL_ONLY_HOME/.config/ghostty/ghostty-local-paste.zsh"
cmp -s "$PASTE_INSTALLED" "$PASTE_HOOK_SOURCE" || fail "粘贴 hook 未按单一模块源码渲染"
cmp -s "$SHELL_ONLY_HOME/.config/ghostty/config.ghostty" "$WORK/shell-global-config" \
    || fail "zsh 窄模式改动了全局 Ghostty 键绑定"
test "$(stat -c %a "$SHELL_ONLY_HOME/.zshrc")" = 600 \
    || fail "zsh 窄模式未保留 .zshrc 的私有权限"
python3 - "$WORK/zshrc-old" "$SHELL_ONLY_HOME/.zshrc" "$WORK/shell-before" <<'PY'
from pathlib import Path
import ast
import sys

before, after, snapshot = (Path(path) for path in sys.argv[1:])
old = before.read_bytes()
new = after.read_bytes()
old_source = (b'if [ -f "$HOME/.config/ghostty/ghostty-ssh-mouse-reset.zsh" ]; then\n'
              b'    source "$HOME/.config/ghostty/ghostty-ssh-mouse-reset.zsh"\nfi\n')
added = (b'if [ -f "$HOME/.config/ghostty/ghostty-local-paste.zsh" ]; then\n'
         b'    source "$HOME/.config/ghostty/ghostty-local-paste.zsh"\nfi\n')
assert old.count(old_source) == 1
assert new == old.replace(old_source, old_source + added, 1), "user .zshrc bytes changed"
root = after.parent
backups = list(root.glob('.zshrc.bak.*'))
assert len(backups) == 1 and backups[0].read_bytes() == old
before_paths = {row[0] for row in map(ast.literal_eval, snapshot.read_text().splitlines())}
after_paths = {p.relative_to(root).as_posix() for p in root.rglob('*')}
assert after_paths - before_paths == {
    '.config/ghostty/ghostty-local-paste.zsh', backups[0].name
}, after_paths - before_paths
PY
shell_rc_inode=$(stat -c %i "$SHELL_ONLY_HOME/.zshrc")
shell_hook_inode=$(stat -c %i "$PASTE_INSTALLED")
snapshot_pi_only_home "$SHELL_ONLY_HOME" > "$WORK/shell-after"
run_shell_only "$SHELL_ONLY_HOME" --shell-paste-only > "$WORK/shell-second.log" \
    || fail "zsh 粘贴窄模式重跑失败"
test "$shell_rc_inode" = "$(stat -c %i "$SHELL_ONLY_HOME/.zshrc")" \
    || fail "zsh 粘贴窄模式重跑改写了 .zshrc"
test "$shell_hook_inode" = "$(stat -c %i "$PASTE_INSTALLED")" \
    || fail "zsh 粘贴窄模式重跑改写了 hook"
snapshot_pi_only_home "$SHELL_ONLY_HOME" > "$WORK/shell-rerun"
cmp -s "$WORK/shell-after" "$WORK/shell-rerun" || fail "zsh 粘贴窄模式重跑改动了用户文件"
test "$(find "$SHELL_ONLY_HOME" -name '.zshrc.bak.*' | wc -l)" -eq 1 \
    || fail "zsh 粘贴窄模式重跑生成了额外备份"

# Live v1 has the already-migrated .zshrc block. Rerunning the narrow mode
# must recognize the exact historical hook, replace only that file, and keep
# .zshrc inode/user text plus global Ghostty config entirely untouched.
cp "$WORK/ghostty-local-paste-v1.zsh" "$PASTE_INSTALLED"
chmod 600 "$PASTE_INSTALLED"
snapshot_pi_only_home "$SHELL_ONLY_HOME" > "$WORK/shell-v1-before"
run_shell_only "$SHELL_ONLY_HOME" --shell-paste-only > "$WORK/shell-v1-migrate.log" \
    || fail "已部署的 v1 hook 无法窄迁移"
cmp -s "$PASTE_INSTALLED" "$PASTE_HOOK_SOURCE" || fail "v1 未迁移为 v2 hook"
test "$(stat -c %a "$PASTE_INSTALLED")" = 600 || fail "v1 迁移未保留 hook 权限"
test "$shell_rc_inode" = "$(stat -c %i "$SHELL_ONLY_HOME/.zshrc")" \
    || fail "v1 hook 迁移错误改写 .zshrc"
cmp -s "$SHELL_ONLY_HOME/.config/ghostty/config.ghostty" "$WORK/shell-global-config" \
    || fail "v1 hook 迁移错误改写全局 Ghostty 配置"
snapshot_pi_only_home "$SHELL_ONLY_HOME" > "$WORK/shell-v1-after"
python3 - "$SHELL_ONLY_HOME" "$WORK/shell-v1-before" "$WORK/shell-v1-after" "$WORK/ghostty-local-paste-v1.zsh" <<'PY'
from pathlib import Path
import ast
import sys

home, before_file, after_file, old_hook = (Path(path) for path in sys.argv[1:])
backup = list((home / '.config/ghostty').glob('ghostty-local-paste.zsh.bak.*'))
assert len(backup) == 1 and backup[0].read_bytes() == old_hook.read_bytes()
before = {row[0]: row for row in map(ast.literal_eval, before_file.read_text().splitlines())}
after = {row[0]: row for row in map(ast.literal_eval, after_file.read_text().splitlines())}
changed = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
assert changed == {
    '.config/ghostty/ghostty-local-paste.zsh',
    backup[0].relative_to(home).as_posix(),
}, changed
PY
migrated_hook_inode=$(stat -c %i "$PASTE_INSTALLED")
run_shell_only "$SHELL_ONLY_HOME" --shell-paste-only > "$WORK/shell-v1-rerun.log" \
    || fail "v1→v2 窄迁移重跑失败"
test "$migrated_hook_inode" = "$(stat -c %i "$PASTE_INSTALLED")" \
    || fail "v1→v2 窄迁移重跑改写了 hook"
snapshot_pi_only_home "$SHELL_ONLY_HOME" > "$WORK/shell-v1-rerun"
cmp -s "$WORK/shell-v1-after" "$WORK/shell-v1-rerun" \
    || fail "v1→v2 窄迁移重跑修改了文件"

for other in --check --ppa-only --deb-only --default-terminal --no-default-terminal --paste-keybind-only --pi-launcher-only; do
    if run_shell_only "$SHELL_ONLY_HOME" --shell-paste-only "$other" > "$WORK/shell-args.log" 2>&1; then
        fail "zsh 粘贴窄模式接受了冲突选项 $other"
    fi
    grep -Fq '不能与其他安装或预检选项组合' "$WORK/shell-args.log" \
        || fail "zsh 粘贴窄模式未明确拒绝冲突选项 $other"
done

for scenario in old-global custom-global custom-zshrc symlink-zshrc malformed-zshrc custom-hook custom-v1-hook symlink-hook custom-mouse; do
    private_home="$WORK/shell-refuse-$scenario"
    prepare_shell_paste_home "$private_home"
    config="$private_home/.config/ghostty/config.ghostty"
    zshrc="$private_home/.zshrc"
    paste_hook="$private_home/.config/ghostty/ghostty-local-paste.zsh"
    case "$scenario" in
        old-global) sed -i 's|^keybind = ctrl+shift+v=text:\\x16$|keybind = ctrl+shift+v=paste_from_clipboard|' "$config" ;;
        custom-global) printf '# user config\n' >> "$config" ;;
        custom-zshrc) sed -i 's|# 由 fresh-install/modules/ghostty/install.sh 管理|# user edit inside managed block|' "$zshrc" ;;
        symlink-zshrc) mv "$zshrc" "$private_home/real-zshrc"; ln -s "$private_home/real-zshrc" "$zshrc" ;;
        malformed-zshrc) sed -i '/^# <<< fresh-install ghostty: ssh 鼠标复位 hook <<</d' "$zshrc" ;;
        custom-hook) printf 'KEEP\n' > "$paste_hook" ;;
        custom-v1-hook) cp "$WORK/ghostty-local-paste-v1.zsh" "$paste_hook"; printf '# user addition\n' >> "$paste_hook" ;;
        symlink-hook) printf 'KEEP\n' > "$private_home/real-hook"; ln -s "$private_home/real-hook" "$paste_hook" ;;
        custom-mouse) printf 'KEEP\n' >> "$private_home/.config/ghostty/ghostty-ssh-mouse-reset.zsh" ;;
    esac
    snapshot_pi_only_home "$private_home" > "$WORK/shell-$scenario.before"
    if run_shell_only "$private_home" --shell-paste-only > "$WORK/shell-$scenario.log" 2>&1; then
        fail "zsh 粘贴窄模式错误接受 $scenario"
    fi
    grep -Fq '拒绝' "$WORK/shell-$scenario.log" || fail "zsh 粘贴窄模式未说明拒绝 $scenario"
    snapshot_pi_only_home "$private_home" > "$WORK/shell-$scenario.after"
    cmp -s "$WORK/shell-$scenario.before" "$WORK/shell-$scenario.after" \
        || fail "zsh 粘贴窄模式拒绝 $scenario 后改动了用户文件"
done

# 全新安装的正常路径先装 SSH hook，再写入 zsh 粘贴；它直接生成新的合并块。
HOME="$WORK/shell-fresh" bash -c '
    set -euo pipefail
    source "$1"
    mkdir -p "$CONFIG_DIR"
    render_config > "$CONFIG_FILE"
    install_ssh_mouse_hook >/dev/null
    install_local_paste_hook >/dev/null
    cmp -s "$LOCAL_PASTE_HOOK_CONFIG" <(render_local_paste_hook)
    test "$(local_paste_zshrc_state)" = current
    ! compgen -G "$HOME/.zshrc.bak.*" >/dev/null
' _ "$WORK/functions.sh" || fail "全新安装未生成合并的 zsh 托管块与粘贴 hook"
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
    test "$(grep -Fc "Exec=$USER_GHOSTTY_LAUNCHER " "$USER_DESKTOP_FILE")" -eq 2
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
    test "$(desktop_override_state)" = "已是本次基准内容（两个 Exec 均使用 Wayland 启动器、独立进程）"
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
    test "$(desktop_override_state)" = "旧模块基准或等价厂商副本（正式安装将备份并迁移到 Wayland 启动器）"
    install_desktop_override >/dev/null
    verify_desktop_override >/dev/null
    test "$(grep -Fc -- "--gtk-single-instance=false" "$USER_DESKTOP_FILE")" -eq 2
    compgen -G "$USER_DESKTOP_FILE.bak.*" >/dev/null
    cmp "$USER_DESKTOP_FILE.bak."* "$DESKTOP_FILE"
' _ "$WORK/functions.sh" "$WORK/vendor.desktop" || fail "desktop 覆盖未按预期替换厂商副本并备份"

# 旧 install.sh 的基准 Exec=/usr/bin/ghostty 与当前独立 opt-in 桌面文件
# 都是已知的模块输出，允许直接升级，不要求人工先禁用防护。
HOME="$WORK/desktop-legacy" bash -c '
    set -euo pipefail
    source "$1"
    DESKTOP_FILE="$2"
    USER_DESKTOP_DIR="$HOME/.local/share/applications"
    USER_DESKTOP_FILE="$USER_DESKTOP_DIR/$DESKTOP_ID"
    mkdir -p "$USER_DESKTOP_DIR"
    render_desktop_override "$GHOSTTY_BIN" > "$USER_DESKTOP_FILE"
    cp "$USER_DESKTOP_FILE" "$HOME/old.desktop"
    test "$(desktop_override_state)" = "旧模块基准或等价厂商副本（正式安装将备份并迁移到 Wayland 启动器）"
    install_desktop_override >/dev/null
    verify_desktop_override >/dev/null
    cmp "$HOME/old.desktop" "$USER_DESKTOP_FILE.bak."*
    grep -Fqx "Exec=$USER_GHOSTTY_LAUNCHER --gtk-single-instance=false" "$USER_DESKTOP_FILE"
' _ "$WORK/functions.sh" "$WORK/vendor.desktop" || fail "旧模块 desktop 未无损升级"

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
    grep -Fqx "Exec=$USER_GHOSTTY_LAUNCHER --gtk-single-instance=false" "$USER_DESKTOP_FILE"
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
    grep -Fqx "Exec=$USER_GHOSTTY_LAUNCHER --gtk-single-instance=false %U" "$USER_DESKTOP_FILE"
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

# ============================================================
# 安装后 terminfo 验证：xterm-ghostty 必须含 Ms（OSC 52）
# ============================================================
# verify_system_integration 还要求 GHOSTTY_BIN 可执行与系统 desktop 入口，
# 两者用临时 fixture；infocmp 用函数 stub 分别模拟“含 Ms”与“不含 Ms”。
printf '#!/bin/sh\necho "Ghostty 1.3.1-test"\n' > "$WORK/fake-ghostty"
chmod +x "$WORK/fake-ghostty"

HOME="$WORK/verify-home" bash -c '
    set -euo pipefail
    source "$1"
    GHOSTTY_BIN="$2"
    DESKTOP_FILE="$3"
    mkdir -p "$HOME"
    infocmp() {
        # 可用性检查不带 -x；只有 Ms 探针会带 -x -1。
        case "$1" in
            -x) printf "\tMs=\\E]52;%%p1%%s;%%p2%%s\\007,\n" ;;
        esac
        return 0
    }
    verify_system_integration > "$HOME/ok.out"
    grep -Fq "含 Ms" "$HOME/ok.out"
' _ "$WORK/functions.sh" "$WORK/fake-ghostty" "$WORK/vendor.desktop" \
    || fail "terminfo 含 Ms 时安装后验证未通过"

HOME="$WORK/verify-fail-home" bash -c '
    set -euo pipefail
    source "$1"
    GHOSTTY_BIN="$2"
    DESKTOP_FILE="$3"
    mkdir -p "$HOME"
    infocmp() { return 0; }
    ( verify_system_integration ) >/dev/null 2>"$HOME/fail.err" && exit 1
    grep -Fq "缺少 Ms" "$HOME/fail.err"
' _ "$WORK/functions.sh" "$WORK/fake-ghostty" "$WORK/vendor.desktop" \
    || fail "terminfo 缺 Ms 时安装后验证未明确失败"

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

# 首次写入/重复运行/漂移备份这类断言依赖安装器真的装了 hook，而安装器
# 在没有 zsh 时会干净跳过；因此这一段只在 zsh 可用时跑，无 zsh 时改测
# “跳过且不动用户文件”。
if [ "$HAVE_ZSH" = true ]; then
    # 安装器 helper 的隔离 HOME 测试：首次写入、重复运行不备份，漂移先备份，
    # .zshrc 的其它行/权限保留，符号链接拒绝改写。
    HOME="$WORK/install-home" bash -c '
        set -euo pipefail
        source "$1"
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
        cmp "$2" "$SSH_MOUSE_HOOK_CONFIG"
        cmp -s "$SSH_MOUSE_HOOK_CONFIG" <(render_ssh_mouse_hook)
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
else
    HOME="$WORK/nozsh-home" bash -c '
        set -euo pipefail
        source "$1"
        SSH_MOUSE_HOOK_CONFIG="$HOME/.config/ghostty/$SSH_MOUSE_HOOK_FILENAME"
        mkdir -p "$HOME"
        printf "KEEP\n" > "$HOME/.zshrc"
        install_ssh_mouse_hook >"$HOME/skip.out" 2>&1
        test ! -e "$SSH_MOUSE_HOOK_CONFIG"
        test "$(cat "$HOME/.zshrc")" = KEEP
    ' _ "$WORK/functions.sh" || fail "无 zsh 时安装器没有干净跳过 hook"
fi

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
if [ "$HAVE_ZSH" = true ]; then
    python3 "$WORK/pty-check.py" "$HOOK_SOURCE" "$GHOSTTY_RESOURCES_DIR" yes
    python3 "$WORK/pty-check.py" "$HOOK_SOURCE" "$GHOSTTY_RESOURCES_DIR" no
fi

# PTY 的输入只送 fake wl-paste；不运行真实安装器、不读写真实剪贴板。
# ZLE 测试 widget 直接将 BUFFER 输出到文件，不按 Enter；这既验证精确
# 插入字节，也能检测粘贴里的换行是否提前执行了 shell 命令。
cat > "$WORK/paste-pty-check.py" <<'PY'
import fcntl
import os
from pathlib import Path
import pty
import select
import socket
import struct
import subprocess
import sys
import termios
import time

work, hook, fake_bin, home = map(Path, sys.argv[1:])
# This HOME is the private --shell-paste-only result, including its migrated
# managed .zshrc block; the PTY loads the real installed source lines.
run = home / ".run"
run.mkdir()
display = socket.socket(socket.AF_UNIX)
display.bind(str(run / "wayland-0"))
display.listen(1)
resources = home / "ghostty-resources"
resources.mkdir()
clip = home / "fake-clipboard"
mode = home / "fake-mode"
log = home / "fake-reads"
clip.write_bytes(b"FIRST-current")
mode.write_text("ok")
log.touch()
env = os.environ.copy()
for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "DISPLAY"):
    env.pop(key, None)
env.update(HOME=str(home), ZDOTDIR=str(home), XDG_RUNTIME_DIR=str(run),
           WAYLAND_DISPLAY="wayland-0", TERM="xterm-256color", HISTFILE="/dev/null",
           TERM_PROGRAM="ghostty", GHOSTTY_RESOURCES_DIR=str(resources),
           SSH_CONNECTION="stale-from-launcher", SSH_CLIENT="stale-from-launcher",
           PATH=f"{fake_bin}:{env['PATH']}", FAKE_CLIPBOARD_TEXT=str(clip),
           FAKE_CLIPBOARD_MODE=str(mode), FAKE_WL_LOG=str(log))

# The local direct Ghostty shell deliberately carries stale SSH_CONNECTION:
# it MUST bind ^V. A missing Ghostty marker or genuine SSH_TTY must stay inert.
def check_binding(overrides, command, expected_stdout, expected_stderr=None):
    case_env = env.copy()
    case_env.update(overrides)
    result = subprocess.run(["zsh", "-f", "-i", "-c", command], env=case_env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result
    assert expected_stdout in result.stdout, result
    if expected_stderr is not None:
        assert expected_stderr in result.stderr, result

check_binding({"WAYLAND_DISPLAY": "absent"},
              f'source "{hook}"; bindkey -M main "^V"', '"^V" quoted-insert')
check_binding({}, f'source "{hook}"; bindkey -M main "^V"',
              '"^V" _quick_deploy_ghostty_paste')
check_binding({"SSH_TTY": "/dev/pts/12"},
              f'source "{hook}"; bindkey -M main "^V"', '"^V" quoted-insert')
check_binding({"TERM_PROGRAM": "tmux"},
              f'source "{hook}"; bindkey -M main "^V"', '"^V" quoted-insert')
check_binding({"GHOSTTY_RESOURCES_DIR": str(home / "missing-resources")},
              f'source "{hook}"; bindkey -M main "^V"', '"^V" quoted-insert')
check_binding({}, f'bindkey -e; source "{hook}"; source "{hook}"; '
                  'bindkey -M main "^V"; bindkey -M main "^[^V"',
              '"^[^V" quoted-insert')
check_binding({}, f'bindkey -v; source "{hook}"; '
                  'bindkey -M main "^V"; bindkey -M main "^[^V"',
              '"^[^V" vi-quoted-insert')
check_binding({}, f'bindkey -e; bindkey -M main "^[^V" backward-char; '
                  f'source "{hook}"; bindkey -M main "^V"',
              '"^V" quoted-insert', 'refusing to override')
noninteractive = subprocess.run(["zsh", "-f", "-c", 'source "$1"; print INERT',
                                  "_", str(hook)], env=env, capture_output=True,
                                 text=True, timeout=5)
assert noninteractive.returncode == 0 and noninteractive.stdout.strip() == "INERT", noninteractive

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
proc = subprocess.Popen(["zsh", "-i"], stdin=slave, stdout=slave, stderr=slave,
                        env=env, close_fds=True)
os.close(slave)
output = bytearray()

def drain(seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ready, _, _ = select.select([master], [], [], 0.02)
        if ready:
            try:
                data = os.read(master, 65536)
            except OSError:
                return
            if data:
                output.extend(data)

def wait_for(marker, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if marker in output:
            return
        drain()
    raise AssertionError(f"timed out for {marker!r}; tail={bytes(output[-500:])!r}")

def capture():
    result = home / "zle-buffer"
    result.unlink(missing_ok=True)
    os.write(master, b"\x07")  # private capture widget, not accept-line
    end = time.monotonic() + 5
    while time.monotonic() < end and not result.exists():
        drain()
    assert result.exists(), bytes(output[-500:])
    return result.read_bytes()

def clear():
    os.write(master, b"\x18")  # private reset widget, no command acceptance
    assert capture() == b"", bytes(output[-500:])
    output.clear()

try:
    wait_for(b"READY:0 > ")
    output.clear()
    os.write(master, b"\x16")
    assert capture() == b"FIRST-current", bytes(output[-500:])
    clear()

    # Same PTY byte represents either physical key under the unchanged global
    # Ghostty binding; changing fake clipboard must be observed on next ^V.
    clip.write_bytes(b"SECOND-now")
    os.write(master, b"\x16")
    assert capture() == b"SECOND-now"
    clear()
    assert log.read_text().splitlines() == ["read", "read"]

    clip.write_bytes(b"-INSERT-")
    os.write(master, b"leftRIGHT" + b"\x02" * 5 + b"\x16")
    assert capture() == b"left-INSERT-RIGHT", "ZLE paste lost text after cursor"
    clear()

    executed = home / "MUST-NOT-RUN"
    payload = f"touch {executed}\nprintf 'next line'\n".encode()
    clip.write_bytes(payload)
    os.write(master, b"\x16")
    assert capture() == payload, repr((home / "zle-buffer").read_bytes())
    assert not executed.exists(), "pasted newline executed a command"
    clear()

    # Preserve the separate literal insertion action on a sequence verified
    # undefined in the original emacs and vi-insert keymaps.
    os.write(master, b"\x1b\x16\x01")  # Esc Ctrl+V, then literal Ctrl+A
    assert capture() == b"\x01", bytes(output[-500:])
    clear()

    mode.write_text("fail")
    os.write(master, b"keep-\x16")
    wait_for(b"Clipboard paste failed")
    assert capture() == b"keep-", "failed read inserted partial or stale data"
    clear()

    mode.write_text("timeout")
    os.write(master, b"stay-\x16")
    wait_for(b"Clipboard paste failed", seconds=4)
    assert capture() == b"stay-", "timed-out read inserted data"
    clear()
    assert not executed.exists(), "some pasted command ran"
finally:
    try:
        os.write(master, b"exit\n")
    except OSError:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    os.close(master)
    display.close()
PY
if [ "$HAVE_ZSH" = true ]; then
    python3 "$WORK/paste-pty-check.py" "$WORK" "$PASTE_INSTALLED" "$WORK/paste-fake-bin" "$SHELL_ONLY_HOME" \
        || fail "私有 PTY 未验证当前剪贴板文本/多行安全、quoted-insert 或读取失败"
fi

bash "$(dirname "${BASH_SOURCE[0]}")/wayland-backpressure.sh"

# setup.sh must have exactly one Ghostty installation route. Test the installer
# functions in fake HOME; no apt, GSettings, or actual GUI modifications.
grep -Fq '"modules/ghostty/install.sh|安装配置 Ghostty 终端|tolerate"' \
    "$MODULE_DIR/../../setup.sh" || fail 'setup.sh 未调用唯一 Ghostty 安装器'
if command -v cc >/dev/null 2>&1; then
    HOME="$WORK/wayland-fresh" bash -c '
        set -euo pipefail
        source "$1"
        BACKPRESSURE_SOURCE="$2"
        DESKTOP_FILE="$3"
        mkdir -p "$HOME"
        install_backpressure > "$HOME/first.log"
        verify_backpressure >/dev/null
        mkdir -p "$USER_DESKTOP_DIR"
        install_desktop_override >/dev/null
        verify_desktop_override >/dev/null
        render_terminal_wrapper > "$HOME/terminal-wrapper"
        grep -Fqx '\''exec "$HOME/.local/bin/ghostty" "$@"'\'' "$HOME/terminal-wrapper"
        test "$(backpressure_state)" = "已是当前基准（原生 Wayland，有界 EAGAIN 重试）"
        digest=$(sha256sum "$BACKPRESSURE_LIBRARY" | cut -d" " -f1)
        install_backpressure > "$HOME/second.log"
        grep -Fq "无变化" "$HOME/second.log"
        test "$digest" = "$(sha256sum "$BACKPRESSURE_LIBRARY" | cut -d" " -f1)"
        ! compgen -G "$BACKPRESSURE_LIBRARY.bak.*" >/dev/null
        ! compgen -G "$USER_GHOSTTY_LAUNCHER.bak.*" >/dev/null
        mv "$BACKPRESSURE_LIBRARY" "$HOME/missing.so"
        if HOME="$HOME" WAYLAND_DISPLAY=wayland-0 "$USER_GHOSTTY_LAUNCHER" +version >"$HOME/missing.log" 2>&1; then exit 1; fi
        grep -Fq "shim missing" "$HOME/missing.log"
        mv "$HOME/missing.so" "$BACKPRESSURE_LIBRARY"
    ' _ "$WORK/functions.sh" "$MODULE_DIR/wayland-backpressure.c" "$WORK/vendor.desktop" \
        || fail '全新安装未构建/验证/幂等托管受保护入口'

    # Existing one-off install: the old launcher and already protected desktop
    # migrate in place, while its library/check copy remain byte-identical.
    HOME="$WORK/wayland-legacy" bash -c '
        set -euo pipefail
        source "$1"
        BACKPRESSURE_SOURCE="$2"
        DESKTOP_FILE="$3"
        mkdir -p "$(dirname "$USER_GHOSTTY_LAUNCHER")" "$USER_DESKTOP_DIR"
        render_legacy_backpressure_launcher > "$USER_GHOSTTY_LAUNCHER"
        chmod 755 "$USER_GHOSTTY_LAUNCHER"
        render_desktop_override > "$USER_DESKTOP_FILE"
        cp "$USER_GHOSTTY_LAUNCHER" "$HOME/legacy.launcher"
        test "$(backpressure_state)" = "旧独立脚本的托管启动器（正式安装将原位迁移）"
        install_backpressure >/dev/null
        install_desktop_override > "$HOME/desktop.log"
        verify_backpressure >/dev/null
        verify_desktop_override >/dev/null
        cmp "$HOME/legacy.launcher" "$USER_GHOSTTY_LAUNCHER.bak."*
        grep -Fq "无变化" "$HOME/desktop.log"
    ' _ "$WORK/functions.sh" "$MODULE_DIR/wayland-backpressure.c" "$WORK/vendor.desktop" \
        || fail '已启用旧独立脚本的安装未被无损迁移'

    HOME="$WORK/wayland-custom" bash -c '
        set -euo pipefail
        source "$1"
        BACKPRESSURE_SOURCE="$2"
        mkdir -p "$(dirname "$USER_GHOSTTY_LAUNCHER")"
        printf "KEEP\n" > "$USER_GHOSTTY_LAUNCHER"
        ( install_backpressure ) > "$HOME/refuse.log" 2>&1 && exit 1
        grep -Fq "拒绝覆盖" "$HOME/refuse.log"
        test "$(cat "$USER_GHOSTTY_LAUNCHER")" = KEEP
        test ! -e "$BACKPRESSURE_LIBRARY"
    ' _ "$WORK/functions.sh" "$MODULE_DIR/wayland-backpressure.c" \
        || fail '自定义启动器未被保护'

    printf '#!/bin/sh\nexit 9\n' > "$WORK/cc"
    chmod +x "$WORK/cc"
    HOME="$WORK/wayland-compile-failure" PATH="$WORK:$PATH" bash -c '
        set -euo pipefail
        source "$1"
        BACKPRESSURE_SOURCE="$2"
        mkdir -p "$HOME"
        ( install_backpressure ) > "$HOME/compile.log" 2>&1 && exit 1
        grep -Fq "编译失败" "$HOME/compile.log"
        test ! -e "$USER_GHOSTTY_LAUNCHER"
        test ! -e "$BACKPRESSURE_LIBRARY"
    ' _ "$WORK/functions.sh" "$MODULE_DIR/wayland-backpressure.c" \
        || fail '编译失败后写入了未保护的启动器'
fi

if [ "$HAVE_ZSH" = true ]; then
    echo "PASS: Ghostty single-installer Wayland backpressure, guarded global and zsh paste modes, private ZLE PTY/current clipboard/newline/error, Pi launcher, desktop/font/SSH integration"
else
    echo "PASS (zsh 未安装，hook 行为与 PTY 部分已跳过): Ghostty single-installer Wayland backpressure, guarded global paste/only-mode, Pi-scoped launcher/only-mode, desktop migration, font/clipboard integration"
fi
