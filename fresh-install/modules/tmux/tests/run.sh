#!/bin/bash
# 隔离验证 tmux 本地基线的输入路由，不触碰真实 tmux server。
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$MODULE_DIR/tmux.conf.local"
WORK="$(mktemp -d /tmp/quick-deploy-tmux-test.XXXXXX)"
SOCKET="quick-deploy-tmux-test-$$-$RANDOM"
SESSION="bindings"

# 显式选择隔离 socket，并清除外层 tmux 身份，允许在 tmux pane 内运行本测试。
tx() { env -u TMUX -u TMUX_PANE tmux -L "$SOCKET" "$@"; }
cleanup() {
    tx kill-session -t click 2>/dev/null || true
    tx kill-session -t "$SESSION" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

command -v tmux >/dev/null 2>&1 || fail "未安装 tmux"
command -v python3 >/dev/null 2>&1 || fail "未安装 python3"

# 不加载用户配置或 Oh My Tmux，只 source 仓库基线本身。
tx -f /dev/null new-session -d -s "$SESSION"
tx source-file "$CONF"

[ "$(tx show-options -gv mouse)" = on ] || fail "mouse 未开启"
if tx list-keys -T root MouseDown1Status >/dev/null 2>&1; then
    fail "MouseDown1Status 仍有绑定"
fi
up_binding="$(tx list-keys -T root MouseUp1Status 2>/dev/null || true)"
case "$up_binding" in
    *"select-window -t ="*) ;;
    *) fail "MouseUp1Status 未按鼠标目标切换 window" ;;
esac
for key in WheelUpStatus WheelDownStatus; do
    if tx list-keys -T root "$key" >/dev/null 2>&1; then
        fail "$key 仍绑定为状态栏切换 window"
    fi
done

# 两张 copy-mode 表的 52 个大小写字母都必须保存同一条两步命令列表。
lower='a b c d e f g h i j k l m n o p q r s t u v w x y z'
upper='A B C D E F G H I J K L M N O P Q R S T U V W X Y Z'
for table in copy-mode copy-mode-vi; do
    for key in $lower $upper; do
        binding="$(tx list-keys -T "$table" "$key" 2>/dev/null || true)"
        case "$binding" in
            *"send-keys -X cancel \\; send-keys -l $key") ;;
            *) fail "$table 的 $key 未绑定为退出后原样输入: $binding" ;;
        esac
    done
done

# 用 raw-tty 单字节接收器验证命令顺序与真实 pane 投递，不把结构检查当行为证据。
cat > "$WORK/read-one.py" <<'PY'
import sys
import time
import tty

tty.setraw(sys.stdin.fileno())
data = sys.stdin.buffer.read(1)
sys.stdout.write("BYTE=" + data.hex() + "\r\n")
sys.stdout.flush()
time.sleep(0.5)
PY

tx set-option -w -t "$SESSION" remain-on-exit on
check_letter() {
    local mode="$1" key="$2" expected_hex="$3" before after output
    tx set-option -w -t "$SESSION" mode-keys "$mode"
    tx respawn-pane -k -t "$SESSION" "python3 '$WORK/read-one.py'"
    sleep 0.1
    tx copy-mode -t "$SESSION"
    before="$(tx display-message -p -t "$SESSION" '#{pane_in_mode}')"
    tx send-keys -t "$SESSION" "$key"
    sleep 0.1
    after="$(tx display-message -p -t "$SESSION" '#{pane_in_mode}')"
    output="$(tx capture-pane -p -t "$SESSION" | tr -d '\r' | grep -o 'BYTE=[0-9a-f]*' || true)"
    [ "$before" = 1 ] || fail "$mode 模式未进入 copy-mode"
    [ "$after" = 0 ] || fail "$mode 按 $key 后未退出 copy-mode"
    [ "$output" = "BYTE=$expected_hex" ] || fail "$mode 按 $key 后收到 $output"
}
check_letter emacs q 71
check_letter vi Z 5a

# 真实 pty 注入 SGR 鼠标按下和释放。按下必须不切换，释放必须按 status
# 的 window range 选择目标，这也覆盖快速点击时的事件分类差异。
ONE_ID="$(tx new-session -dP -F '#{window_id}' -s click -n one)"
TWO_ID="$(tx new-window -dP -F '#{window_id}' -t click: -n two)"
tx set-option -t click status-position bottom
tx set-option -t click status-justify left
tx set-option -t click status-left ''
tx set-option -t click status-right ''
tx set-option -t click window-status-separator ''
tx set-option -t click window-status-format ' W#{window_index} '
tx set-option -t click window-status-current-format ' W#{window_index} '
tx set-option -t click mouse on
tx select-window -t "$ONE_ID"

cat > "$WORK/mouse-check.py" <<'PY'
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

socket, session, one_id, two_id = sys.argv[1:]
env = os.environ.copy()
env.pop("TMUX", None)
env.pop("TMUX_PANE", None)
base = ["tmux", "-L", socket]

def tx(*args, check=True):
    return subprocess.run(base + list(args), check=check, capture_output=True,
                          text=True, env=env)

def active_id():
    return tx("display-message", "-p", "-t", session, "#{window_id}").stdout.strip()

def drain(fd):
    while True:
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return
        try:
            if not os.read(fd, 65536):
                return
        except OSError:
            return

pid, fd = pty.fork()
if pid == 0:
    os.environ.clear()
    os.environ.update(env)
    os.environ["TERM"] = "xterm-256color"
    os.execvp("tmux", base + ["attach-session", "-t", session])

try:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    deadline = time.time() + 3
    while time.time() < deadline:
        clients = tx("list-clients", "-t", session, check=False).stdout.strip()
        if clients:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("tmux client did not attach")
    time.sleep(0.25)
    drain(fd)

    hits = []
    for x in range(1, 31):
        tx("select-window", "-t", one_id)
        time.sleep(0.015)
        drain(fd)
        os.write(fd, f"\x1b[<0;{x};24M\x1b[<0;{x};24m".encode())
        time.sleep(0.015)
        if active_id() == two_id:
            hits.append(x)
    if len(hits) < 2:
        raise AssertionError(f"no clickable target range for second window: {hits}")

    x = hits[len(hits) // 2]
    tx("select-window", "-t", one_id)
    time.sleep(0.05)
    drain(fd)
    os.write(fd, f"\x1b[<0;{x};24M".encode())
    time.sleep(0.05)
    if active_id() != one_id:
        raise AssertionError("mouse down switched before the completed click")
    os.write(fd, f"\x1b[<0;{x};24m".encode())
    time.sleep(0.05)
    if active_id() != two_id:
        raise AssertionError("mouse up did not select the ranged window")

    # Wheel events on the status row must be inert; pane wheel bindings are
    # intentionally left untouched by this change.
    for button in (64, 65):
        tx("select-window", "-t", one_id)
        time.sleep(0.05)
        drain(fd)
        os.write(fd, f"\x1b[<{button};{x};24M".encode())
        time.sleep(0.05)
        if active_id() != one_id:
            raise AssertionError(f"status wheel button {button} unexpectedly switched window")
finally:
    tx("detach-client", "-s", session, check=False)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
PY

python3 "$WORK/mouse-check.py" "$SOCKET" click "$ONE_ID" "$TWO_ID" \
    || fail "状态栏左键释放切换行为失败"

echo "PASS: tmux 状态栏点击切换与 copy-mode 字母原样输入"
