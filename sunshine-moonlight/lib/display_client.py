"""Supervised stock-Moonlight client; the installed host owns the lifecycle.

ControllerTransport.call returns the FULL validated v1 success envelope.
ControllerError.remote_refusal is True only for a validated host refusal. A
refusal is not proof that a mutating operation left state unchanged. Capabilities
live only in this process and protected controller stdin, never local lock files.

CLI/catalogue contract: Moonlight Qt 6.1.0, commit
f786e94c7b2f943e24e65d7d74deb539b827fc84, app/cli/{commandlineparser,
listapps,startstream}.cpp. The catalogue is plain `list`, never `list --csv`:
printAppCSV starts a box-art fetch per app whose thread-pool join can deadlock
CLI shutdown (a Sunshine that never answers appasset leaves the process alive
with the CSV stuck in the stdout buffer, so no output is ever recoverable).
Name resolution mirrors stock getAppIndex: first str.lower title match wins.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

HEX = re.compile(r"[0-9a-f]{64}\Z")
QUERY_LIMIT = 65536
QUERY_TIMEOUT = 45  # stock computer search 30s + application search 10s
ACTIVE_TIMEOUT = 45
MAX_CATALOGUE_APPS = 128


class ClientError(Exception):
    """Public, capability-free error constructed only from local fixed messages."""


class Cancelled(ClientError):
    pass


class RpcFailure(ClientError):
    def __init__(self, action, code="unknown", remote_refusal=False):
        # Never include arbitrary exception text, reply objects or private fields.
        self.action = action
        self.code = code if type(code) is str else "unknown"
        self.remote_refusal = remote_refusal is True
        super().__init__(f"控制器 {action} {'拒绝请求' if self.remote_refusal else '响应不确定'}。")


def check_desktop_catalogue(catalogue: str) -> None:
    """Validate a fresh plain-list catalogue for one unambiguous Desktop.

    Plain `list` prints one raw app title per line and carries no app IDs, so
    the client cannot confirm the pinned ID before launch. Uniqueness of the
    stock first-str.lower-match title is the pre-launch ambiguity gate; the
    host then binds its inspected app ID to the observed currentgame at
    observe_active before any stream is accepted, so a wrong application can
    never be reported as the managed stream.
    """
    lines = catalogue.split("\n")
    if len(lines) > MAX_CATALOGUE_APPS + 1:
        raise ClientError("Moonlight 应用列表超出可靠核对范围；未启动串流。")
    matches = 0
    for index, line in enumerate(lines):
        if not line:
            if index == len(lines) - 1 and len(lines) > 1:
                continue  # one trailing newline from the final fprintf
            raise ClientError("Moonlight 应用列表存在无法可靠解析的空行；未启动串流。")
        if any(ord(char) < 32 or 127 <= ord(char) < 0xA1 for char in line):
            raise ClientError("Moonlight 应用列表存在含控制字符的标题；未启动串流。")
        if line.lower() == "desktop":
            matches += 1
    if matches == 0:
        raise ClientError("Moonlight 应用列表没有 Desktop；请在目标 Sunshine Applications 页面恢复无命令的 Desktop 后重试。")
    if matches > 1:
        raise ClientError("Moonlight 应用列表存在多个大小写不敏感 Desktop，无法确认官方 stream 会启动哪一个；未启动串流。")


def executable(explicit=None, *, platform=None, home=None, which=shutil.which) -> str:
    """Discover an installed executable, never a version-directory candidate.

    Linux retains the exact installer wrapper. macOS supports standard/user app
    bundles then PATH. Windows supports Moonlight.exe on PATH, or an explicit
    installed executable path; no guessed registry key/install directory scan.
    """
    platform = platform or sys.platform
    home = Path.home() if home is None else Path(home)
    if explicit is not None:
        candidates = [Path(explicit).absolute()]
    elif platform == "linux":
        candidates = [home / ".local/bin/moonlight"]
    elif platform == "darwin":
        candidates = [Path("/Applications/Moonlight.app/Contents/MacOS/Moonlight"),
                      home / "Applications/Moonlight.app/Contents/MacOS/Moonlight"]
        path = which("moonlight") or which("Moonlight")
        if path:
            candidates.append(Path(path))
    elif platform == "win32":
        path = which("Moonlight.exe")
        candidates = [Path(path)] if path else []
    else:
        raise ClientError("不支持此客户端平台；需要已安装的官方 Moonlight Qt 可执行文件。")
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        # Linux's exact wrapper must not silently fall back to a dormant version.
    raise ClientError("找不到本地可执行文件 Moonlight；Linux 使用 ~/.local/bin/moonlight，macOS 使用 Moonlight.app/PATH，Windows 使用 PATH 中的 Moonlight.exe；也可用 --moonlight-binary PATH 指定已安装的官方程序。")


@contextmanager
def host_lock(target):
    """Permanent non-secret kernel lock, shared by aliases for the same endpoint."""
    key = hashlib.sha256(f"{target['address']}:{target['port']}".encode("ascii")).hexdigest()
    if os.name == "nt":
        import ctypes as C
        from ctypes import wintypes as W
        api = C.WinDLL("kernel32", use_last_error=True)
        api.CreateMutexW.argtypes, api.CreateMutexW.restype = [W.LPVOID, W.BOOL, W.LPCWSTR], W.HANDLE
        api.WaitForSingleObject.argtypes, api.WaitForSingleObject.restype = [W.HANDLE, W.DWORD], W.DWORD
        api.ReleaseMutex.argtypes, api.ReleaseMutex.restype = [W.HANDLE], W.BOOL
        api.CloseHandle.argtypes, api.CloseHandle.restype = [W.HANDLE], W.BOOL
        handle = api.CreateMutexW(None, False, "Local\\QuickDeployDisplay-" + key)
        if not handle:
            raise ClientError("无法创建本机连接排他锁。")
        held = False
        try:
            result = api.WaitForSingleObject(handle, 0)
            if result == 258:
                raise ClientError("本机已有连接器正在操作同一主机；请使用原终端。")
            if result not in (0, 128):  # abandoned mutex grants lock, NOT a host lease
                raise ClientError("无法取得本机连接排他锁。")
            held = True
            yield
        finally:
            if held:
                api.ReleaseMutex(handle)
            api.CloseHandle(handle)
        return
    import fcntl
    path = Path(tempfile.gettempdir()) / f"quick-deploy-display-{os.geteuid()}-{key}.lock"
    fd = None
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ClientError("本机连接排他锁类型/权限不符合要求。")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ClientError("本机已有连接器正在操作同一主机；请使用原终端。") from None
        linked = path.lstat()
        if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
            raise ClientError("本机连接排他锁被替换；未操作主机。")
        yield
    except OSError:
        raise ClientError("无法取得本机连接排他锁；未启动串流。") from None
    finally:
        if fd is not None:
            os.close(fd)  # never unlink a live/permanent flock name


class Interrupts:
    """Record interrupts without tearing an in-flight durable RPC in half."""
    def __init__(self):
        self.signum = None
        self.previous = {}

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            raise ClientError("连接器必须在拥有交互终端的主线程运行。")
        try:
            for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"):
                sig = getattr(signal, name, None)
                if sig is not None and sig not in self.previous:
                    self.previous[sig] = signal.getsignal(sig)
                    signal.signal(sig, self.received)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def received(self, signum, _frame):
        if self.signum is None:
            self.signum = signum

    def check(self):
        if self.signum is not None:
            raise Cancelled("已取消。")

    def __exit__(self, *_):
        for sig, previous in self.previous.items():
            signal.signal(sig, previous)


def stop_child(child):
    """Stop/reap only our Popen child; never pkill/kill an unrelated GUI/service."""
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
    child.wait()


class OfficialClient:
    """Bounded list and supervised inherited-stdio pair/stream/quit processes."""
    def __init__(self, interrupts=None):
        self.interrupts = interrupts

    def check(self):
        if self.interrupts is not None:
            self.interrupts.check()

    def spawn(self, argv, **kwargs):
        try:
            return subprocess.Popen(argv, **kwargs)
        except OSError:
            raise ClientError("无法执行本地 Moonlight；未获得进程成功证据。") from None

    def foreground(self, argv, *, cleanup=False, timeout=None):
        if not cleanup:
            self.check()
        child = self.spawn(argv)  # environment and all three user stdio inherited
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while child.poll() is None:
                if not cleanup:
                    self.check()
                if deadline is not None and time.monotonic() >= deadline:
                    raise ClientError("Moonlight quit 超时；主机预约保留，需要可信对账。")
                time.sleep(0.05)
            return child.returncode
        finally:
            stop_child(child)

    def query(self, argv):
        self.check()
        child = self.spawn(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=dict(os.environ, LC_ALL="C"))
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()
        def drain(stream, sink):
            while True:
                data = stream.read(4096)
                if not data:
                    return
                remaining = QUERY_LIMIT - len(sink)
                sink.extend(data[:remaining])
                if len(data) > remaining:
                    overflow.set()
        readers = [threading.Thread(target=drain, args=(stream, sink), daemon=True)
                   for stream, sink in zip((child.stdout, child.stderr), buffers)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + QUERY_TIMEOUT
        try:
            while child.poll() is None or any(reader.is_alive() for reader in readers):
                self.check()
                if overflow.is_set():
                    raise ClientError("Moonlight list 输出超限；不能使用截断的应用列表。")
                if time.monotonic() >= deadline:
                    raise ClientError("Moonlight list 超时；未确认配对或应用身份。")
                time.sleep(0.01)
            if overflow.is_set():
                raise ClientError("Moonlight list 输出超限；不能使用截断的应用列表。")
            try:
                return child.returncode, buffers[0].decode("utf-8"), buffers[1].decode("utf-8")
            except UnicodeError:
                raise ClientError("Moonlight list 输出不是可验证的 UTF-8。") from None
        finally:
            stop_child(child)
            # Only our child is reaped. A foreign inherited-pipe writer must not
            # make cleanup block indefinitely (readers are bounded daemon threads).
            for reader in readers:
                reader.join(timeout=0.1)
            for stream, reader in zip((child.stdout, child.stderr), readers):
                if not reader.is_alive():
                    stream.close()


@dataclass(frozen=True)
class Ticket:
    revision: int
    phase: str
    attempt: int | None
    capability: str = field(repr=False)


def revision(value):
    if type(value) is not int or not 0 <= value < (1 << 63) - 1:
        raise RpcFailure("validation")
    return value


def mode_argv(display):
    """Current rational refresh -> host half-up integer FPS, no silent downgrade.

    Qt6.1 accepts integer resolution (no separate parser range), warns outside
    FPS 10..480 and bitrate 500..500000. Host modes are limited to 16..16384 and
    FPS <=240. Refuse a host mode outside their supported intersection.

    Starting bitrate: 20 Mbps at 3840x2160x60, proportional to pixel rate,
    clamped to Qt's 500..500000 Kbps range. This is an office starting policy,
    not a fidelity/network optimum. Explicit SDR/4:2:0 and codec auto keep saved
    HDR/YUV/forced-codec settings from silently changing that policy. Borderless
    is a supported LOCAL window mode, not a remote monitor selection API.
    """
    w, h, n, d, fps = (display.get(k) for k in
                      ("width", "height", "refresh_num", "refresh_den", "requested_fps"))
    if (any(type(v) is not int for v in (w, h, n, d, fps)) or
            not 16 <= w <= 16384 or not 16 <= h <= 16384 or n <= 0 or d <= 0
            or not 10 <= fps <= 240 or fps != (2 * n + d) // (2 * d)):
        raise ClientError("选中屏幕的当前模式无法按官方 CLI 支持范围请求；未降分辨率或降帧。")
    anchor = 3840 * 2160 * 60
    bitrate = max(500, min(500000, (20000 * w * h * fps + anchor // 2) // anchor))
    return ["--resolution", f"{w}x{h}", "--fps", str(fps), "--bitrate", str(bitrate),
            "--display-mode", "borderless", "--video-codec", "auto", "--no-hdr",
            "--no-yuv444", "--no-quit-after"]


def default_transport(machine, target):
    try:
        from display_transport import ControllerTransport
    except ImportError:
        raise ClientError("缺少 display_transport 控制器传输组件；请完成客户端安装/合并，未连接主机。") from None
    return ControllerTransport(machine["ssh"], machine["ssh_port"], target)


def default_chooser(screens, **kwargs):
    # Selection-only lazy import; help/list/SSH never import terminal UI.
    try:
        from screen_picker import choose_screen, ScreenPickerError
    except ImportError:
        raise ClientError("缺少 screen_picker 选择器组件；未选择默认屏幕。") from None
    try:
        return choose_screen(screens, **kwargs)
    except ScreenPickerError:
        raise ClientError("交互屏幕选择失败或中断；未选择默认屏幕。") from None


class Session:
    def __init__(self, name, target, transport, chooser, official, catalogue, interrupts,
                 input_stream, output_stream):
        self.name, self.target = name, target
        self.transport, self.chooser, self.official = transport, chooser, official
        self.catalogue, self.interrupts = catalogue, interrupts
        self.input, self.output = input_stream, output_stream
        self.host_id = None
        self.ticket = None
        self.uncertain = False
        self.launch_issued = False
        self.active = False
        self.child = None

    def say(self, message):
        print(message, file=self.output, flush=True)

    def rpc(self, action, **fields):
        try:
            result = self.transport.call(action, **fields)
        except Exception as exc:
            remote = getattr(exc, "remote_refusal", False) is True
            if not remote:
                self.uncertain = True
            raise RpcFailure(action, getattr(exc, "code", "unknown"), remote) from None
        if (type(result) is not dict or type(result.get("version")) is not int
                or result.get("version") != 1 or result.get("ok") is not True
                or result.get("action") != action):
            self.uncertain = True
            raise RpcFailure(action)
        if "target" in result and result["target"] != self.target:
            self.uncertain = True
            raise RpcFailure(action)
        if "host_uniqueid" in result:
            try:
                host = result["host_uniqueid"]
                if type(host) is not str or str(uuid.UUID(host)) != host:
                    raise ValueError()
            except (ValueError, AttributeError):
                self.uncertain = True
                raise RpcFailure(action) from None
            if self.host_id is not None and host != self.host_id:
                self.uncertain = True
                raise RpcFailure(action)
            self.host_id = host
        return result

    def owned(self, action, **fields):
        result = self.rpc(action, revision=self.ticket.revision,
                          capability=self.ticket.capability, **fields)
        return result

    def take_ticket(self, result, phase):
        try:
            rev, cap, attempt = revision(result.get("revision")), result.get("capability"), result.get("attempt_revision")
            if (result.get("phase") != phase or result.get("uncertainty") is not None
                    or type(cap) is not str or not HEX.fullmatch(cap)
                    or (self.ticket and (rev <= self.ticket.revision or cap != self.ticket.capability))
                    or (phase in ("selecting", "preparing") and attempt is not None)
                    or (phase in ("connecting", "active", "releasing") and
                        (type(attempt) is not int or not 1 <= attempt <= rev))
                    or (self.ticket and phase in ("active", "releasing") and
                        attempt != self.ticket.attempt)):
                raise ValueError()
        except (ValueError, RpcFailure):
            self.uncertain = True
            raise RpcFailure("validation") from None
        self.ticket = Ticket(rev, phase, attempt, cap)

    def status_unchanged(self):
        status = self.rpc("status")  # ownership ONLY; never used as a screen menu
        if (status.get("host_uniqueid") != self.host_id or status.get("target") != self.target
                or status.get("sampling") != "ownership" or
                revision(status.get("revision")) != self.ticket.revision or
                status.get("phase") != self.ticket.phase or status.get("uncertainty") is not None
                or status.get("attempt_revision") != self.ticket.attempt):
            self.uncertain = True
            raise ClientError("预约状态已变化或无法证明原票据；需要可信对账，未重试操作。")
        return status

    def menu(self, result):
        screens, snapshot = result.get("screens"), result.get("snapshot")
        if (type(screens) is not list or not 1 <= len(screens) <= 32
                or type(snapshot) is not str or not HEX.fullmatch(snapshot)):
            raise ClientError("主机没有返回可验证的实时屏幕菜单。")
        ids = [s.get("id") if type(s) is dict else None for s in screens]
        if any(type(i) is not str or not i for i in ids) or len(set(ids)) != len(ids):
            raise ClientError("主机屏幕身份缺失或重复；未选择默认屏幕。")
        if not any(s.get("selectable") is True for s in screens):
            raise ClientError("当前没有可准备的屏幕；请在主机检查当前模式/捕获后端，未选择默认屏幕。")
        return screens, snapshot

    def acquire(self):
        # A confirmed list/acquire race has issued no owner intent. Re-read only
        # after an explicit stale refusal; never retry lost mutations.
        for _ in range(3):
            self.interrupts.check()
            listed = self.rpc("list")
            if self.host_id is None or listed.get("target") != self.target:
                raise RpcFailure("list")
            if (listed.get("selection_ready") is not True or listed.get("phase") != "available"
                    or listed.get("uncertainty") is not None or listed.get("activity") != "idle_application"):
                raise ClientError("主机忙碌、未就绪、未安装/未登记或预约不确定；请让主机管理员检查控制器状态。不会接管已有串流，也不会自动 enroll/reset。")
            screens, snapshot = self.menu(listed)
            self.interrupts.check()
            try:
                acquired = self.rpc("acquire", revision=revision(listed.get("revision")), snapshot=snapshot)
            except RpcFailure as exc:
                if exc.remote_refusal and exc.code in ("stale_snapshot", "stale_revision"):
                    continue
                raise
            self.take_ticket(acquired, "selecting")
            if acquired.get("snapshot") != snapshot:
                self.uncertain = True
                raise RpcFailure("acquire")
            return screens, snapshot
        raise ClientError("主机实时状态连续变化；未选择默认屏幕，请重新运行。")

    def prepare(self, screens, snapshot):
        while True:
            self.interrupts.check()
            chosen = self.chooser(screens, title=f"{self.name}: 选择远端屏幕", input_stream=self.input,
                                  output_stream=self.output)
            if chosen is None:
                raise Cancelled("已取消选择。")
            matches = [s for s in screens if s["id"] == chosen and s.get("selectable") is True]
            if type(chosen) is not str or len(matches) != 1:
                raise ClientError("选择器没有返回当前菜单中的精确可选 ID；未启动串流。")
            mode_argv(matches[0])  # reject unsupported current FPS before host prepare
            self.interrupts.check()
            try:
                prepared = self.owned("prepare", snapshot=snapshot, display_id=chosen)
            except RpcFailure as exc:
                if exc.remote_refusal and exc.code == "stale_snapshot":
                    self.status_unchanged()
                    refreshed = self.owned("refresh")
                    self.take_ticket(refreshed, "selecting")
                    screens, snapshot = self.menu(refreshed)
                    self.say("屏幕实时状态已变化，请从刷新后的菜单重新选择。")
                    continue
                raise
            self.take_ticket(prepared, "preparing")
            display = prepared.get("display")
            if (prepared.get("launch_authorized") is not True or type(display) is not dict
                    or display.get("id") != chosen or prepared.get("desktop_title") != "Desktop"
                    or type(prepared.get("desktop_app_id")) is not int
                    or not 1 <= prepared["desktop_app_id"] < (1 << 31)):
                self.uncertain = True
                raise RpcFailure("prepare")
            mode_argv(display)
            return prepared

    def retain(self):
        # After an ambiguous response, even the last revision may be stale. Do
        # not guess it or retry a mutation. Known issued-but-unobserved launch
        # can be explicitly marked lost using its last acknowledged ticket.
        if self.ticket is not None and not self.uncertain and self.launch_issued:
            try:
                self.owned("control_lost")
            except RpcFailure:
                pass
        self.say("未确认释放主机预约：串流/控制/关闭证据不完整，按保留处理，需要主机可信对账后再连接；不会 quit 未证实属于本次的应用。")

    def abort(self):
        self.status_unchanged()
        result = self.owned("abort")  # host proves pristine/completed-prepared cleanup
        if (result.get("phase") != "available" or result.get("uncertainty") is not None
                or result.get("attempt_revision") is not None
                or revision(result.get("revision")) <= self.ticket.revision):
            self.uncertain = True
            raise RpcFailure("abort")
        self.ticket = None
        self.say("已确认取消本次启动前预约。")

    def release(self):
        release = self.owned("begin_release")  # positive owned app BEFORE official quit
        self.take_ticket(release, "releasing")
        quit_code = self.official.foreground(
            [self.program, "quit", "--", self.address], cleanup=True, timeout=QUERY_TIMEOUT)
        closed = self.owned("finish_release")  # same-generation closure, NOT quit exit status
        if (closed.get("phase") != "available" or closed.get("uncertainty") is not None
                or closed.get("attempt_revision") is not None
                or closed.get("closure_basis") != "same_generation_owned_desktop_cancelled"
                or revision(closed.get("revision")) <= self.ticket.revision):
            self.uncertain = True
            raise RpcFailure("finish_release")
        self.ticket = None
        self.say("已确认本次受管 Desktop 关闭并释放预约。")
        return quit_code

    def stream(self, prepared):
        self.interrupts.check()
        # The final, fresh official catalogue is after preparation / pairing
        # and before host launch intent or external stream dispatch. Plain list
        # carries no IDs: one unambiguous Desktop title is the pre-launch gate.
        check_desktop_catalogue(self.catalogue(self.program, self.official))
        options = mode_argv(prepared["display"])
        self.interrupts.check()
        launched = self.owned("launch")
        self.launch_issued = True  # acknowledged durable intent; not stream success
        self.take_ticket(launched, "connecting")
        if (launched.get("desktop_app_id") != prepared["desktop_app_id"]
                or launched.get("desktop_title") != "Desktop"
                or launched.get("quit_policy") != "explicit_quit" or
                launched.get("display") != prepared["display"]):
            self.uncertain = True
            raise RpcFailure("launch")
        self.interrupts.check()
        self.say(f"请求 {prepared['display']['width']}x{prepared['display']['height']} / {prepared['display']['requested_fps']} FPS（当前屏幕 Hz 经整数取整，不是实测视频 FPS）；码率按 4K60/20Mbps 像素率起点，SDR/4:2:0、auto codec、borderless。")
        self.child = self.official.spawn([self.program, "stream", *options, "--", self.address, "Desktop"])
        observed_live = False
        deadline = time.monotonic() + ACTIVE_TIMEOUT
        try:
            while self.child.poll() is None:
                self.interrupts.check()
                try:
                    observed = self.owned("observe_active")
                except RpcFailure as exc:
                    if not (exc.remote_refusal and exc.code == "launch_unconfirmed"):
                        raise
                    if time.monotonic() >= deadline:
                        raise ClientError("未在期限内观察到本次正数 Desktop；Moonlight 进程存在不等于串流成功。")
                    time.sleep(0.1)
                    continue
                self.take_ticket(observed, "active")
                self.active = True
                observed_live = self.child.poll() is None
                break
            if not self.active:
                raise ClientError("Moonlight 在正数 Desktop 活跃证据之前退出；包括 exit0 都不能证明串流成功。")
            while self.child.poll() is None:
                if self.interrupts.signum is not None:
                    stop_child(self.child)
                    break
                time.sleep(0.05)  # Stock CLI is single-shot; retain owner until this child exits.
            child_code = self.child.wait()
        finally:
            stop_child(self.child)
        quit_code = self.release()
        if not observed_live:
            raise ClientError("Moonlight 已退出才收到活跃观察；已完成受管关闭，但未证明进程运行期间串流成功。")
        if self.interrupts.signum is not None:
            return 128 + self.interrupts.signum
        if child_code != 0:
            return child_code if child_code > 0 else 128 - child_code
        return 0 if quit_code == 0 else (quit_code if quit_code > 0 else 128 - quit_code)

    def run(self, program):
        self.program = program
        self.address = f"{self.target['address']}:{self.target['port']}"
        result = 1
        try:
            screens, snapshot = self.acquire()
            prepared = self.prepare(screens, snapshot)
            return self.stream(prepared)
        except Cancelled as exc:
            self.say(str(exc))
            result = 128 + self.interrupts.signum if self.interrupts.signum else 0
        except KeyboardInterrupt:
            self.say("已取消。")
            result = 130
        except ClientError as exc:
            self.say("错误: " + str(exc))
        except SystemExit as exc:
            # Pairing helpers already printed bounded both-stream diagnostics.
            result = exc.code if type(exc.code) is int and exc.code != 0 else 1
        except Exception:
            self.say("错误: 本次客户端操作失败；不输出可能含能力的内部异常。")
        if self.child is not None:
            stop_child(self.child)
        if self.ticket is not None:
            if not self.launch_issued and not self.uncertain:
                try:
                    self.abort()
                except Exception:
                    self.retain()
                    result = 1
            else:
                self.retain()
                result = 1
        elif self.uncertain:
            self.say("主机请求结果不确定；不能证明没有取得/改变预约，请主机管理员可信对账。")
            result = 1
        return result


def connect(name, machine, catalogue, *, binary=None, transport_factory=default_transport,
            chooser=default_chooser, official=None, lock_factory=host_lock,
            input_stream=None, output_stream=None):
    """Actual entrypoint with narrow injected seams for isolated orchestration tests.

    catalogue(program, official)->fresh official plain app list; it owns the
    preserved list/unpaired/PIN/pair/fresh-list user workflow. No explicit
    resume/adoption or automatic enrollment API is provided by this client.
    """
    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stderr if output_stream is None else output_stream
    if not input_stream.isatty() or not output_stream.isatty():
        raise ClientError("需要交互终端选择远端屏幕/手工配对；未获取主机预约。请从终端运行 run_server NAME。")
    program = executable(binary)
    target = {"address": machine["tailnet_ip"], "port": machine["moonlight_port"]}
    with Interrupts() as interrupts, lock_factory(target):
        interrupts.check()
        transport = transport_factory(machine, target)
        session = Session(name, target, transport, chooser, official or OfficialClient(interrupts),
                          catalogue, interrupts, input_stream, output_stream)
        return session.run(program)
