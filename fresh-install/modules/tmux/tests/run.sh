#!/bin/bash
# 隔离验证 tmux 本地基线与输入路由，不触碰真实 tmux server。
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="$MODULE_DIR/tmux.conf.local"
WORK="$(mktemp -d /tmp/quick-deploy-tmux-test.XXXXXX)"
SOCKET="$WORK/tmux.sock"
SESSION="bindings"
ESCAPE_SESSION="escape-routing"
CLIP_SOCKET=""  # 由 next_clip_socket 分配；每个阶段一个全新 socket
CLIP_SESSION="osc52"
# 隔离 socket 用 -S 指向 $WORK 内的路径，不清 /tmp/tmux-<uid>/ 的 socket 节点；
# 同时清除外层 tmux 身份，允许在 tmux pane 内运行本测试。
tx() { env -u TMUX -u TMUX_PANE tmux -S "$SOCKET" "$@"; }
cleanup() {
    local sock
    for sock in "$WORK"/*.sock; do
        [ -e "$sock" ] || continue
        env -u TMUX -u TMUX_PANE tmux -S "$sock" kill-server 2>/dev/null || true
    done
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
passthrough="$(tx show-options -Apv -t "$SESSION" allow-passthrough)"
[ "$passthrough" = on ] || fail "allow-passthrough 有效值不是 on: $passthrough"
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

# 两张 copy-mode 表的 MouseDragEnd1Pane 必须仍是完整的自定义「复制后留在
# copy-mode」绑定（整行比对，不是子串）：OSC 52 行为在 tmux 默认的
# copy-selection-and-cancel 下也会发生，只有绑定文本与行为断言合起来才能
# 把行为归因到这条自定义绑定。
for table in copy-mode copy-mode-vi; do
    binding="$(tx list-keys -T "$table" MouseDragEnd1Pane 2>/dev/null | tr -s ' ' || true)"
    [ "$binding" = "bind-key -T $table MouseDragEnd1Pane send-keys -X copy-selection-no-clear \\; send-keys -X clear-selection" ] \
        || fail "$table 的 MouseDragEnd1Pane 不是完整的自定义绑定: [$binding]"
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

# 私有 sentinel 与真实 Esc 可能由内核合并写入；固定读取完整 9 字节，验证
# root/copy-mode 的实际字节顺序，而不是只检查 list-keys 文本。
cat > "$WORK/read-n.py" <<'PY'
import sys
import time
import tty

tty.setraw(sys.stdin.fileno())
remaining = int(sys.argv[1])
data = b""
while len(data) < remaining:
    chunk = sys.stdin.buffer.read(remaining - len(data))
    if not chunk:
        break
    data += chunk
sys.stdout.write("INPUT=" + data.hex() + "\r\n")
sys.stdout.flush()
time.sleep(0.5)
PY

ARMED_PANE="$(tx new-session -dP -F '#{pane_id}' -s "$ESCAPE_SESSION")"
OTHER_PANE="$(tx split-window -dP -F '#{pane_id}' -t "$ESCAPE_SESSION")"
SENTINEL_AND_ESC='1b5b3939373b317e1b'
tx set-option -w -t "$ESCAPE_SESSION" remain-on-exit on
tx set-option -w -t "$ESCAPE_SESSION" @quick_deploy_pi_error 1
tx set-option -p -t "$ARMED_PANE" @quick_deploy_pi_recovery_armed 1
[ -z "$(tx display-message -p -t "$OTHER_PANE" '#{@quick_deploy_pi_recovery_armed}')" ] \
    || fail "未 armed pane 意外继承 recovery marker"

pane_input() {
    tx capture-pane -p -t "$1" | tr -d '\r' | grep -o 'INPUT=[0-9a-f]*' || true
}

# root 表必须从真实 attached client 注入；tmux send-keys 在 normal mode 会绕过
# root key table，不能作为行为证据。
cat > "$WORK/root-escape-check.py" <<'PY'
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time

socket, session, armed, other, reader = sys.argv[1:]
env = os.environ.copy()
env.pop("TMUX", None)
env.pop("TMUX_PANE", None)
base = ["tmux", "-S", socket]


def reap(pid, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done == pid:
            return
        time.sleep(0.02)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def tx(*args, check=True):
    return subprocess.run(base + list(args), check=check, capture_output=True, text=True, env=env)

def drain(fd):
    while select.select([fd], [], [], 0)[0]:
        try:
            if not os.read(fd, 65536):
                return
        except OSError:
            return

def inject(fd, pane, count):
    tx("respawn-pane", "-k", "-t", pane, f"python3 '{reader}' {count}")
    tx("select-pane", "-t", pane)
    time.sleep(0.1)
    drain(fd)
    os.write(fd, b"\x1b")
    deadline = time.time() + 1.0
    while time.time() < deadline:
        output = tx("capture-pane", "-p", "-t", pane).stdout.replace("\r", "")
        for line in output.splitlines():
            if line.startswith("INPUT="):
                return line
        time.sleep(0.02)
    return ""

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
        if tx("list-clients", "-t", session, check=False).stdout.strip():
            break
        time.sleep(0.05)
    else:
        raise AssertionError("tmux client did not attach")
    time.sleep(0.2)
    print("ROOT_OTHER=" + inject(fd, other, 1))
    print("ROOT_ARMED=" + inject(fd, armed, 9))
finally:
    tx("detach-client", "-s", session, check=False)
    reap(pid)
PY

root_output="$(python3 "$WORK/root-escape-check.py" "$SOCKET" "$ESCAPE_SESSION" "$ARMED_PANE" "$OTHER_PANE" "$WORK/read-n.py")"
grep -Fqx 'ROOT_OTHER=INPUT=1b' <<<"$root_output" || fail "未 armed pane 的 root Esc 路由错误: $root_output"
grep -Fqx "ROOT_ARMED=INPUT=$SENTINEL_AND_ESC" <<<"$root_output" || fail "armed pane 的 root Esc 路由错误: $root_output"

# 两张 copy-mode 表都验证两 pane：window 虽红，未 armed pane 仍只退出且零注入；
# owning pane 则退出后投递 sentinel+Esc。
check_copy_mode_escape() {
    local mode="$1" after output
    tx set-option -w -t "$ESCAPE_SESSION" mode-keys "$mode"

    tx respawn-pane -k -t "$OTHER_PANE" "python3 '$WORK/read-n.py' 1"
    tx select-pane -t "$OTHER_PANE"
    sleep 0.1
    tx copy-mode -t "$OTHER_PANE"
    tx send-keys -t "$OTHER_PANE" Escape
    sleep 0.1
    after="$(tx display-message -p -t "$OTHER_PANE" '#{pane_in_mode}')"
    output="$(pane_input "$OTHER_PANE")"
    [ "$after" = 0 ] || fail "$mode 未 armed pane 按 Esc 后未退出 copy-mode"
    [ -z "$output" ] || fail "$mode 未 armed pane 被注入字节: $output"

    tx respawn-pane -k -t "$ARMED_PANE" "python3 '$WORK/read-n.py' 9"
    tx select-pane -t "$ARMED_PANE"
    sleep 0.1
    tx copy-mode -t "$ARMED_PANE"
    tx send-keys -t "$ARMED_PANE" Escape
    sleep 0.1
    after="$(tx display-message -p -t "$ARMED_PANE" '#{pane_in_mode}')"
    output="$(pane_input "$ARMED_PANE")"
    [ "$after" = 0 ] || fail "$mode armed pane 按 Esc 后未退出 copy-mode"
    [ "$output" = "INPUT=$SENTINEL_AND_ESC" ] || fail "$mode armed pane 路由错误: $output"
}
check_copy_mode_escape emacs
check_copy_mode_escape vi

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
import signal
import struct
import subprocess
import sys
import termios
import time

socket, session, one_id, two_id = sys.argv[1:]
env = os.environ.copy()
env.pop("TMUX", None)
env.pop("TMUX_PANE", None)
base = ["tmux", "-S", socket]

def reap(pid, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done == pid:
            return
        time.sleep(0.02)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass

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
    reap(pid)
PY

python3 "$WORK/mouse-check.py" "$SOCKET" click "$ONE_ID" "$TWO_ID" \
    || fail "状态栏左键释放切换行为失败"

# ============================================================
# OSC 52：外部剪贴板行为（隔离 server + PTY 客户端，不碰物理剪贴板）
# ============================================================
# 基线里 set-clipboard on 有两个方向，本段两个测试各守一个 tmux 边界：
#
#   边界 1 —— tmux 自己复制时发 OSC 52（下面 check_clip_mode）：tmux 3.4 对
#      on 和 external 都会发（源码 window_copy_copy_buffer 里的非零判断）。
#      是否真的向某个客户端发出序列，取决于那个客户端自己的 terminfo 是否
#      带 Ms 能力（源码 tty_set_selection 里的 tty_term_has(tty->term,
#      TTYC_MS)），而不是 tmux 被强制声明的 terminal-features。所以测试
#      编译两份只差 Ms 的 terminfo，让两个客户端同时显示同一个 pane 做
#      对照：同一个鼠标拖选动作后，带 Ms 的客户端必须收到唯一一条 OSC 52，
#      且 base64 解码后与选中文本逐字节相等；不带 Ms 的客户端必须一条都
#      收不到，而两者都渲染了同样的 pane 内容。拖动故意由不带 Ms 的客户端
#      发起：这样对照的不只是输入侧，而是 tmux 按接收方的客户端能力分发。
#
#   边界 2 —— pane 应用 / 内层 tmux 发来的 OSC 52（下面 check_clip_relay）：
#      取值 on 时，tmux 解析应用发来的序列并重新发出，同时写入自己的
#      buffer；external 或 off 会直接丢弃。这段的输出选择器直接复用边界 1
#      观测到的形式（为空），两段由此在真实接口上对接。
#
# 两段合起来只证明“两个 tmux 边界”：内层/发送端 tmux 发出的序列形式，以及
# 外层/中继端 tmux 对同形式序列的处理。SSH 传输与 Ghostty 终端的实际行为都
# 没有被这两段执行到（那需要真实主机与真实终端），不能据此宣称整条链路已在
# 测试里跑通。
#
# 另外两点边界（文档里也写明）：tmux 会按允许集合过滤并去重选择器字符、
# 把 base64 解码后重新编码，保留的只是“剪贴板类别 + 解码后的字节”，不是
# 发送端的字面文本；序列只发给每个 terminfo 带 Ms 且当前正显示源 window 的
# 已连接客户端，源 window 在后台时 buffer 仍会更新但没有终端剪贴板写入。
#
# 客户端全部跑在 PTY 上，序列在测试内解码，不经过物理剪贴板，也不碰任何
# 真实用户的 tmux server。
txc() { env -u TMUX -u TMUX_PANE TERMINFO="$WORK/terminfo" tmux -S "$CLIP_SOCKET" "$@"; }
# 每个阶段要一个干净 server：不复用同一个 socket 路径，而是换新路径启动。
# kill-server 返回后旧 server 可能还活着一小段时间，而 -S 的 socket 文件在
# server 退出后不会消失；此时复用同一路径，新客户端会连上正在退出的进程并
# 报 “server exited unexpectedly”（实测 6 次跑中出现 2 次）。换新路径从构造
# 上消除这个竞态，不依赖任何等待时长假设。
CLIP_SOCKET_SEQ=0
next_clip_socket() {
    CLIP_SOCKET_SEQ=$((CLIP_SOCKET_SEQ + 1))
    CLIP_SOCKET="$WORK/osc52-$CLIP_SOCKET_SEQ.sock"
    txc -f /dev/null new-session -d -s "$CLIP_SESSION"
    txc source-file "$CONF"
}
command -v tic >/dev/null 2>&1 || fail "未安装 tic（ncurses-bin），无法构造带 Ms 能力的客户端 terminfo"
command -v infocmp >/dev/null 2>&1 || fail "未安装 infocmp（ncurses-bin），无法构造客户端 terminfo"

# 从系统 xterm-256color 派生条目；名字取自定义值，不命中 tmux 按终端名
# 推断能力的表，避免把内置推断混进这份对照。删注释行与可能已有的 Ms 用
# grep + [[:space:]]（不依赖 GNU sed 的 \t 写法），追加能力用 printf 的 \t。
build_clip_terminfo() {
    local name="$1" with_ms="$2" src="$WORK/$1.ti"
    infocmp -x -1 xterm-256color 2>/dev/null \
        | sed -e '/^#/d' \
              -e "s/^xterm-256color[^,]*/${name}|isolated OSC 52 test entry/" \
        | grep -v '^[[:space:]]*Ms=' > "$src" || return 1
    [ -s "$src" ] || return 1
    if [ "$with_ms" = yes ]; then
        printf '\tMs=\\E]52;%%p1%%s;%%p2%%s\\007,\n' >> "$src"
    fi
    tic -x -o "$WORK/terminfo" "$src" >/dev/null
}
mkdir -p "$WORK/terminfo"
build_clip_terminfo qp-osc52-ms yes || fail "无法编译带 Ms 的测试 terminfo"
build_clip_terminfo qp-osc52-noms no || fail "无法编译不带 Ms 的对照 terminfo"
infocmp -x -A "$WORK/terminfo" qp-osc52-ms 2>/dev/null \
    | grep '^[[:space:]]*Ms=' >/dev/null \
    || fail "带 Ms 的测试 terminfo 未包含 Ms"
if infocmp -x -A "$WORK/terminfo" qp-osc52-noms 2>/dev/null \
    | grep '^[[:space:]]*Ms=' >/dev/null; then
    fail "不带 Ms 的对照 terminfo 意外包含 Ms"
fi

# 拖动覆盖整行标记文本，释放列取文本末尾之后。鼠标选区在 tmux 内部按
# mode-keys 收尾（window_copy_get_selection：emacs 丢右下角字符并去掉行尾
# 换行，vi 保留右下角字符与行尾换行），所以复制整行时两种键表的字节不同：
# emacs 得到标记本身，vi 得到标记加一个换行。预期字节按模式分别算出，
# 断言仍然逐字节精确对比，而不是用 $(...) 去尾换行后的字符串比较。
CLIP_MARKER="osc52-payload-$$-$RANDOM"
CLIP_ROW=5
CLIP_END_COL=$((4 + ${#CLIP_MARKER}))
clip_hex_of() { python3 -c 'import sys; sys.stdout.write(sys.argv[1].encode().hex())' "$1"; }
CLIP_EXPECTED_HEX_EMACS="$(clip_hex_of "$CLIP_MARKER")"
CLIP_EXPECTED_HEX_VI="${CLIP_EXPECTED_HEX_EMACS}0a"
# 边界 1 实际发出的选择器由 check_clip_mode 断言为空并记在这里；边界 2 的
# 输入直接复用它，两段在真实接口上对接，而不是各自写死一个常量。
CLIP_COPY_FLAGS=""

cat > "$WORK/osc52-check.py" <<'PY'
import base64
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

socket_name, terminfo, session, marker, row_s, start_s, end_s, actor = sys.argv[1:9]
row, start_col, end_col = int(row_s), int(start_s), int(end_s)

env = os.environ.copy()
env.pop("TMUX", None)
env.pop("TMUX_PANE", None)
env["TERMINFO"] = terminfo
base = ["tmux", "-S", socket_name]


def tx(*args, check=True):
    return subprocess.run(base + list(args), check=check, capture_output=True,
                          text=True, env=env)


def reap(pid, timeout=2.0):
    # 有界收尾：客户端卡住时不能无期限 waitpid，先轮询再强杀。
    end = time.time() + timeout
    while time.time() < end:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done == pid:
            return
        time.sleep(0.02)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def spawn(term):
    pid, fd = pty.fork()
    if pid == 0:
        child_env = dict(env)
        child_env["TERM"] = term
        os.execvpe("tmux", base + ["attach-session", "-t", session], child_env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    return pid, fd


def drain(fd, seconds):
    buf = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        buf.extend(data)
    return bytes(buf)


def wait_clients(count, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        out = tx("list-clients", "-t", session, check=False).stdout
        if len(out.splitlines()) >= count:
            return
        time.sleep(0.05)
    raise AssertionError("tmux clients did not attach")


ms_pid, ms_fd = spawn("qp-osc52-ms")
noms_pid, noms_fd = spawn("qp-osc52-noms")
try:
    wait_clients(2)
    time.sleep(0.4)
    rendered = {"ms": drain(ms_fd, 0.4), "noms": drain(noms_fd, 0.4)}
    # Both clients must have drawn the pane content before the copy, so the
    # no-Ms client is a real attached viewer of the same selection, not an
    # idle client that simply has not rendered yet.
    for key, fd in (("ms", ms_fd), ("noms", noms_fd)):
        end = time.time() + 3
        while marker.encode() not in rendered[key] and time.time() < end:
            rendered[key] += drain(fd, 0.1)

    actor_fd = {"ms": ms_fd, "noms": noms_fd}[actor]
    os.write(actor_fd, f"\x1b[<0;{start_col};{row}M".encode())
    time.sleep(0.05)
    for col in range(start_col + 1, end_col + 1):
        os.write(actor_fd, f"\x1b[<32;{col};{row}M".encode())
        time.sleep(0.01)
    time.sleep(0.05)
    os.write(actor_fd, f"\x1b[<0;{end_col};{row}m".encode())

    out = {"ms": drain(ms_fd, 0.8), "noms": drain(noms_fd, 0.8)}

    # 自定义 MouseDragEnd1Pane 的语义是“复制后留在 copy-mode 并清除选区”：
    # 按截止时间轮询确认这一点。tmux 默认的 copy-selection-and-cancel 会退出
    # copy-mode，所以这条行为断言（配合文件顶部的绑定文本整行断言）才把行为
    # 归因到自定义绑定，而不是只看“buffer 里有了内容”。
    state = ""
    end = time.time() + 2.0
    while time.time() < end:
        state = tx("display-message", "-p", "-t", session,
                   "#{pane_in_mode}:#{selection_present}").stdout.strip()
        if state == "1:0":
            break
        time.sleep(0.05)
    pane_mode, selection = (state.split(":", 1) + [""])[:2]
finally:
    tx("detach-client", "-s", session, check=False)
    for pid in (ms_pid, noms_pid):
        reap(pid)

seq_re = re.compile(rb"\x1b\]52;([^;]*);([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)")
print("ACTOR=" + actor)
print("COPY_MODE=" + ("yes" if pane_mode == "1" else "no"))
print("SELECTION_CLEARED=" + ("yes" if selection == "0" else "no"))
for name, key in (("MS", "ms"), ("NOMS", "noms")):
    matches = seq_re.findall(out[key])
    print(f"{name}_OSC52_COUNT={len(matches)}")
    for flags, payload in matches:
        decoded = base64.b64decode(payload)
        print(f"{name}_OSC52_FLAGS={flags.decode()}")
        print(f"{name}_OSC52_PAYLOAD_HEX={decoded.hex()}")
        print(f"{name}_OSC52_PAYLOAD={decoded!r}")
    print(f"{name}_MARKER_RENDERED={'yes' if marker.encode() in rendered[key] else 'no'}")
PY

check_clip_mode() {
    local mode="$1" expected_hex="$2" result payload_hex buffer_hex
    next_clip_socket
    [ "$(txc show-options -gv set-clipboard)" = on ] \
        || fail "$mode 模式：set-clipboard 有效值不是 on"
    txc set-option -w -t "$CLIP_SESSION" mode-keys "$mode"
    txc respawn-pane -k -t "$CLIP_SESSION" \
        "printf '\n\n\n\n%s\n' '$CLIP_MARKER'; sleep 300"
    sleep 0.3
    if ! result="$(python3 "$WORK/osc52-check.py" "$CLIP_SOCKET" "$WORK/terminfo" \
        "$CLIP_SESSION" "$CLIP_MARKER" "$CLIP_ROW" 1 "$CLIP_END_COL" noms)"; then
        fail "$mode 模式：OSC 52 对照脚本执行失败"
    fi
    echo "$result"
    grep -Fqx "MS_MARKER_RENDERED=yes" <<<"$result" \
        || fail "$mode 模式：带 Ms 的客户端未渲染 pane 内容，对照无效"
    grep -Fqx "NOMS_MARKER_RENDERED=yes" <<<"$result" \
        || fail "$mode 模式：不带 Ms 的客户端未渲染 pane 内容，对照无效"
    # 自定义绑定语义：复制后留在 copy-mode、选区被清除。
    grep -Fqx "COPY_MODE=yes" <<<"$result" \
        || fail "$mode 模式：复制后未留在 copy-mode（自定义绑定语义）: $result"
    grep -Fqx "SELECTION_CLEARED=yes" <<<"$result" \
        || fail "$mode 模式：复制后选区未清除: $result"
    grep -Fqx "MS_OSC52_COUNT=1" <<<"$result" \
        || fail "$mode 模式：带 Ms 的客户端未收到唯一一条 OSC 52 序列: $result"
    # 边界 1 发出的选择器必须是空（tmux 自己复制时传的就是空 flags）：
    # 它也是边界 2 中继测试要注入的形式，两段在真实接口上对接。
    grep -Fqx "MS_OSC52_FLAGS=" <<<"$result" \
        || fail "$mode 模式：tmux 自复制发出的选择器不是空: $result"
    # 不用 sed | head：head 提前退出会让 sed 收到 SIGPIPE，pipefail 下会
    # 把一次成功提取变成失败。断言已限定只有一条匹配，sed 直接读完整输入。
    CLIP_COPY_FLAGS="$(sed -n 's/^MS_OSC52_FLAGS=//p' <<<"$result")"
    payload_hex="$(sed -n 's/^MS_OSC52_PAYLOAD_HEX=//p' <<<"$result")"
    [ -n "$payload_hex" ] || fail "$mode 模式：对照脚本没有报告 OSC 52 载荷: $result"
    [ "$payload_hex" = "$expected_hex" ] \
        || fail "$mode 模式：OSC 52 载荷字节与精确选中内容不一致（期望 $expected_hex，实际 $payload_hex）"
    grep -Fqx "NOMS_OSC52_COUNT=0" <<<"$result" \
        || fail "$mode 模式：不带 Ms 的客户端也收到了 OSC 52: $result"
    # window_copy_copy_buffer 把同一个 buf 同时交给 OSC 52 和 tmux buffer，
    # 因此两者字节必须相等：序列里出的就是存进 buffer 的那份选中内容。
    buffer_hex="$(txc show-buffer | python3 -c 'import sys; sys.stdout.write(sys.stdin.buffer.read().hex())')"
    [ "$buffer_hex" = "$payload_hex" ] \
        || fail "$mode 模式：OSC 52 载荷与 tmux buffer 字节不一致（payload $payload_hex，buffer $buffer_hex）"
}

# 两张 copy-mode 键表下各跑一次鼠标拖选，覆盖 MouseDragEnd1Pane 的自定义
# 绑定；binding 文本断言在文件顶部，两者合起来才能把行为归因到该绑定。
check_clip_mode emacs "$CLIP_EXPECTED_HEX_EMACS"
check_clip_mode vi "$CLIP_EXPECTED_HEX_VI"

# ---- 边界 2：pane 应用 / 内层 tmux 发来的 OSC 52 被外层 tmux 解析并重发 ----
# 输入直接复用边界 1 观测到的形式（选择器为空，见 CLIP_COPY_FLAGS），断言重发
# 后选择器仍为空、解码后的字节逐字节相等。注意 tmux 不是字节级转发：它按
# input_osc_52 的允许集合过滤并去重选择器字符，再把 base64 解码后重新编码，
# 保留的只是“剪贴板类别 + 解码后的字节”。另外，输入缓冲上限 1 MiB
# （INPUT_BUF_LIMIT），超长 OSC 会被静默丢弃（INPUT_DISCARD）；按 base64
# 的 4/3 膨胀，可中继的原文上限约 768 KiB。这里不用超大载荷做边界回归——
# 它确定但成本高，且本次修改不接触该路径。
CLIP_RELAY_NONCE="osc52-relay-$$-$RANDOM"
CLIP_RELAY_B64="$(python3 -c 'import base64,sys; sys.stdout.write(base64.b64encode(sys.argv[1].encode()).decode())' "$CLIP_RELAY_NONCE")"
CLIP_RELAY_HEX="$(clip_hex_of "$CLIP_RELAY_NONCE")"
CLIP_RELAY_CMD="printf '\033]52;$CLIP_COPY_FLAGS;$CLIP_RELAY_B64\007'; sleep 300"

cat > "$WORK/osc52-relay-check.py" <<'PY'
import base64
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

socket_name, terminfo, session, marker, trigger = sys.argv[1:6]

env = os.environ.copy()
env.pop("TMUX", None)
env.pop("TMUX_PANE", None)
env["TERMINFO"] = terminfo
base = ["tmux", "-S", socket_name]


def tx(*args, check=True):
    return subprocess.run(base + list(args), check=check, capture_output=True,
                          text=True, env=env)


def reap(pid, timeout=2.0):
    # 有界收尾：客户端卡住时不能无期限 waitpid，先轮询再强杀。
    end = time.time() + timeout
    while time.time() < end:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if done == pid:
            return
        time.sleep(0.02)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def spawn(term):
    pid, fd = pty.fork()
    if pid == 0:
        child_env = dict(env)
        child_env["TERM"] = term
        os.execvpe("tmux", base + ["attach-session", "-t", session], child_env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    return pid, fd


def drain(fd, seconds):
    buf = bytearray()
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        buf.extend(data)
    return bytes(buf)


def wait_clients(count, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        out = tx("list-clients", "-t", session, check=False).stdout
        if len(out.splitlines()) >= count:
            return
        time.sleep(0.05)
    raise AssertionError("tmux clients did not attach")


ms_pid, ms_fd = spawn("qp-osc52-ms")
noms_pid, noms_fd = spawn("qp-osc52-noms")
try:
    wait_clients(2)
    time.sleep(0.4)
    rendered = {"ms": drain(ms_fd, 0.4), "noms": drain(noms_fd, 0.4)}
    for key, fd in (("ms", ms_fd), ("noms", noms_fd)):
        end = time.time() + 3
        while marker.encode() not in rendered[key] and time.time() < end:
            rendered[key] += drain(fd, 0.1)
    # The pane emits the sequence only once both clients are attached and
    # rendering, so the capture covers the relay of a pane-originated OSC 52.
    tx("respawn-pane", "-k", "-t", session, trigger)
    time.sleep(0.2)
    out = {"ms": drain(ms_fd, 0.9), "noms": drain(noms_fd, 0.9)}
finally:
    tx("detach-client", "-s", session, check=False)
    for pid in (ms_pid, noms_pid):
        reap(pid)

seq_re = re.compile(rb"\x1b\]52;([^;]*);([A-Za-z0-9+/=]*)(?:\x07|\x1b\\)")
for name, key in (("MS", "ms"), ("NOMS", "noms")):
    matches = seq_re.findall(out[key])
    print(f"{name}_RELAY_COUNT={len(matches)}")
    for flags, payload in matches:
        decoded = base64.b64decode(payload)
        print(f"{name}_RELAY_FLAGS={flags.decode()}")
        print(f"{name}_RELAY_PAYLOAD_HEX={decoded.hex()}")
        print(f"{name}_RELAY_PAYLOAD={decoded!r}")
    print(f"{name}_MARKER_RENDERED={'yes' if marker.encode() in rendered[key] else 'no'}")
PY

check_clip_relay() {
    local result payload_hex buffer_hex
    next_clip_socket
    txc respawn-pane -k -t "$CLIP_SESSION" \
        "printf '\n\n\n\n%s\n' '$CLIP_MARKER'; sleep 300"
    sleep 0.3
    if ! result="$(python3 "$WORK/osc52-relay-check.py" "$CLIP_SOCKET" "$WORK/terminfo" \
        "$CLIP_SESSION" "$CLIP_MARKER" "$CLIP_RELAY_CMD")"; then
        fail "OSC 52 中继对照脚本执行失败"
    fi
    echo "$result"
    grep -Fqx "MS_MARKER_RENDERED=yes" <<<"$result" \
        || fail "中继：带 Ms 的客户端未先渲染 pane 内容，对照无效"
    grep -Fqx "NOMS_MARKER_RENDERED=yes" <<<"$result" \
        || fail "中继：不带 Ms 的客户端未先渲染 pane 内容，对照无效"
    grep -Fqx "MS_RELAY_COUNT=1" <<<"$result" \
        || fail "中继：带 Ms 的客户端未收到唯一一条重发的 OSC 52: $result"
    # 边界 1 与边界 2 在接口处对齐：注入的就是边界 1 观测到的空选择器，
    # 重发后仍为空（tmux 不过滤出额外的选择器字符）。
    grep -Fqx "MS_RELAY_FLAGS=" <<<"$result" \
        || fail "中继：重发的选择器不是空: $result"
    payload_hex="$(sed -n 's/^MS_RELAY_PAYLOAD_HEX=//p' <<<"$result")"
    [ "$payload_hex" = "$CLIP_RELAY_HEX" ] \
        || fail "中继：重发的 OSC 52 解码字节与 pane 发出的内容不一致（期望 $CLIP_RELAY_HEX，实际 $payload_hex）"
    grep -Fqx "NOMS_RELAY_COUNT=0" <<<"$result" \
        || fail "中继：不带 Ms 的客户端也收到了重发的 OSC 52: $result"
    # on 的代价在 README 里说明，这里行为上确认：pane 发来的内容确实会改写
    # 外层 tmux 自己的 buffer（应用用 OSC 52 查询读到的就是这个 buffer）。
    buffer_hex="$(txc show-buffer | python3 -c 'import sys; sys.stdout.write(sys.stdin.buffer.read().hex())')"
    [ "$buffer_hex" = "$CLIP_RELAY_HEX" ] \
        || fail "中继：pane 发来的 OSC 52 未进入外层 tmux buffer（buffer $buffer_hex）"
}

check_clip_relay

echo "PASS: tmux passthrough、状态栏点击、copy-mode 输入、pane-owned Esc sentinel 路由与 OSC 52 复制发送/中继校验"
