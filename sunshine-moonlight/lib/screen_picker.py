"""Bounded synchronous screen selector; no discovery, connection or default acceptance.

choose_screen(screens, *, title=None, input_stream=None, output_stream=None)
returns the ORIGINAL id on explicit Enter, or None on Esc. Up/Left move to the
previous row and Down/Right to the next, in host record order (no physical layout
claim). Movement includes disabled rows and wraps; Enter on those rows cannot
accept. An all-unavailable list remains inspectable/cancellable. Input is a
nonempty list/tuple of <=32 host presentation records; required fields are listed
in _FIELDS. Extra host fields are ignored.
Unknown numeric facts are None/0, unknown boolean facts may be None. IDs are never
rewritten. Labels/title/reasons are bounded, sanitized and clipped for display.

InvalidScreens: malformed/empty/oversized input. TerminalUnavailable: non-TTY,
unsupported console, non-main-thread use, or viewport below 24 columns/9 rows.
PickerEOF: EOF, Ctrl-D/Ctrl-Z, or POSIX input hangup. PickerInterrupted.signum:
SIGTERM/HUP/QUIT/TSTP (also Windows SIGBREAK). Ctrl-C/SIGINT raises KeyboardInterrupt.
TerminalFailure:
terminal I/O/setup/restoration failure (original exception chained). Windows close
keeps SIGINT deferred through the full handler handoff, with SIGINT restored last.
Pending handlers get a final completion pass before interruption propagation;
restoration errors
take precedence over a deferred interrupt. All other exceptions also run cleanup.
No exception returns a default screen.

POSIX requires a main-screen xterm-compatible VT terminal (1049 alternate screen,
DEC private cursor-mode save/restore), Unicode, and ambiguous characters occupying
one cell. Bare Esc is distinguished from arrows by a 200ms total prefix deadline.
Narrow rows are explicitly ellipsized; no terminal-dependent width guarantees for
nonstandard ambiguous-width/emoji rendering. Windows requires a native Windows
10+ VT console; redirected/MSYS pipe terminals explicitly refuse. Windows uses an owned native screen buffer and
ReadConsoleInputW, never getwch on an unrelated global input handle. No streams
are opened/closed by this function. Call synchronously in the main thread; signal
handlers and terminal state are borrowed only for the duration of the call.
"""
from __future__ import annotations

import ctypes as C
import errno
import os
import signal
import sys
import threading
import time
import unicodedata as U
from collections.abc import Mapping


class ScreenPickerError(Exception):
    """Explicit picker refusal/failure; never evidence of a selected screen."""


class InvalidScreens(ScreenPickerError):
    pass


class TerminalUnavailable(ScreenPickerError):
    pass


class TerminalFailure(ScreenPickerError):
    pass


class PickerEOF(ScreenPickerError):
    pass


class PickerInterrupted(ScreenPickerError):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"screen selection interrupted by signal {signum}")


class _Resize(Exception):
    pass


_FIELDS = ("id", "label", "primary", "connected", "width", "height", "refresh_num",
           "refresh_den", "requested_fps", "selectable", "reason", "selection_intent",
           "capture_candidate")


def _clean(text):
    """Remove VT sequences (including string payloads), C0/C1 and format controls."""
    result, i = [], 0
    while i < len(text):
        ch = text[i]
        i += 1
        if ch == "\x1b":
            if i == len(text):
                break
            ch = text[i]
            i += 1
            if ch not in "[]PX^_":
                while " " <= ch <= "/" and i < len(text):
                    ch = text[i]
                    i += 1
                continue
        elif ch not in "\x9b\x9d\x90\x98\x9e\x9f":
            if U.category(ch) not in ("Cc", "Cf", "Cs"):
                result.append(ch)
            continue
        if ch in "[\x9b":
            while i < len(text):
                final = text[i]
                i += 1
                if "@" <= final <= "~":
                    break
        else:
            while i < len(text):
                if text[i] in "\x07\x9c":
                    i += 1
                    break
                if text.startswith("\x1b\\", i):
                    i += 2
                    break
                i += 1
    return "".join(result)


def _cell_width(text, i):
    ch = text[i]
    # Canonical combining class orders marks; spacing Mc can still occupy cells.
    if U.category(ch) in ("Mn", "Me"):
        return 0
    return 2 if U.east_asian_width(ch) in ("W", "F") or text[i + 1:i + 2] == "\ufe0f" else 1


def _clip(text, cells):
    total = sum(_cell_width(text, i) for i in range(len(text)))
    if total <= cells:
        return text
    result, used = [], 0
    for i, ch in enumerate(text):
        width = _cell_width(text, i)
        if used + width > cells - 1:
            break
        result.append(ch)
        used += width
    return "".join(result) + "…"


def _records(screens, title):
    if type(screens) not in (list, tuple) or not 1 <= len(screens) <= 32:
        raise InvalidScreens("expected 1..32 screen records")
    if title is not None and (type(title) is not str or len(title) > 1024):
        raise InvalidScreens("title must be a string of at most 1024 characters")
    result, seen = [], set()
    for row in screens:
        if not isinstance(row, Mapping) or any(k not in row for k in _FIELDS):
            raise InvalidScreens("missing screen presentation fields")
        row = {k: row[k] for k in _FIELDS}
        ident = row["id"]
        if (type(ident) is not str or not 1 <= len(ident) <= 100 or ident in seen
                or any(U.category(c) in ("Cc", "Cf", "Cs") for c in ident)):
            raise InvalidScreens("invalid or duplicate screen id")
        seen.add(ident)
        for k in ("label", "reason", "selection_intent"):
            val = row[k]
            if val is None and k != "label":
                continue
            if type(val) is not str or len(val) > 1024:
                raise InvalidScreens(f"invalid {k}")
            if k != "selection_intent":
                row[k] = _clip(_clean(val), 128)
        if row["selection_intent"] not in (None, "prepare"):
            raise InvalidScreens("unsupported selection intent")
        for k in ("primary", "connected", "capture_candidate"):
            if row[k] is not None and type(row[k]) is not bool:
                raise InvalidScreens(f"invalid {k}")
        if type(row["selectable"]) is not bool or (row["selectable"] and row["connected"] is False):
            raise InvalidScreens("invalid selectable/connected facts")
        for k, maximum in (("width", 16384), ("height", 16384), ("refresh_num", 2**63 - 1),
                           ("refresh_den", 2**63 - 1), ("requested_fps", 240)):
            val = row[k]
            if val is not None and (type(val) is not int or not 0 <= val <= maximum):
                raise InvalidScreens(f"invalid {k}")
        result.append(row)
    return result, _clip(_clean(title or "Choose remote screen"), 128)


def _yes(value):
    return "unknown" if value is None else "yes" if value else "no"


def _frame(screens, selected, title, columns, rows, blocked):
    if columns < 24 or rows < 9:
        raise TerminalUnavailable("picker needs at least 24 columns and 9 rows")
    # Cap one repaint independently of an unusually large terminal. Never write
    # the final column/row, so autowrap/scrolling cannot enlarge the viewport.
    columns, rows = min(columns - 1, 160), min(rows - 1, 48)
    count = min(len(screens), (rows - 4) // 4)
    start = min(max(0, selected - count // 2), len(screens) - count)
    lines = [title, f"{len(screens)} screens | {sum(s['selectable'] for s in screens)} available | rows {start + 1}-{start + count}"]
    for i in range(start, start + count):
        s = screens[i]
        num, den = s["refresh_num"], s["refresh_den"]
        hz = "unknown Hz" if not num or not den else (f"{num // den} Hz" if num % den == 0
                    else f"{num / den:.3f} Hz ({num}/{den})")
        mode = f"{s['width'] or '?'}x{s['height'] or '?'} @ {hz}"
        status = (f"AVAILABLE: {s['selection_intent'] or 'intent unknown'} | capture candidate {_yes(s['capture_candidate'])}"
                  if s["selectable"] else f"UNAVAILABLE: {s['reason'] or 'reason unknown'}")
        lines.extend([f"{'>' if i == selected else ' '} {i + 1}. {s['label'] or '(label unknown)'}",
                      "  " + mode, "  " + status,
                      f"  request {s['requested_fps'] or '?'} FPS | primary {_yes(s['primary'])} | connected {_yes(s['connected'])}"])
    movement_hint = ("Up/Down (Left/Right): screen | Enter: select" if columns >= 45
                     else "Up/Down: screen | Enter: select")
    lines.extend(["Enter refused: unavailable screen" if blocked else movement_hint,
                  "Esc: cancel | Hz = physical, FPS = request"])
    return "\x1b[H\x1b[2J" + "\r\n".join(_clip(line, columns) for line in lines)


def _fds(inp, out):
    try:
        a, b = inp.fileno(), out.fileno()
        if not inp.isatty() or not out.isatty() or not os.isatty(a) or not os.isatty(b):
            raise TerminalUnavailable("interactive input AND output terminals are required")
        return a, b
    except (OSError, ValueError, AttributeError) as exc:
        raise TerminalUnavailable("usable terminal file descriptors are required") from exc


class _PosixTerminal:
    def __init__(self, inp, out):
        import select
        import termios
        self.select, self.termios = select.select, termios
        self.a, self.b = _fds(inp, out)
        if os.fstat(self.a).st_rdev != os.fstat(self.b).st_rdev:
            raise TerminalUnavailable("input and output must belong to the same terminal")
        self.out, self.saved, self.handlers = out, None, {}
        self.active, self.pending, self.deadline = False, bytearray(), None
        self.reading, self.resized = False, False

    def size(self):
        return os.get_terminal_size(self.b)

    def write(self, text):
        if self.out.write(text) != len(text):
            raise OSError("short terminal write")
        self.out.flush()

    def start(self):
        # Protect catchable termination BEFORE borrowing any persistent tty mode.
        # If setup is interrupted partway through, close() has the original state.
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGTSTP", "SIGWINCH"):
            sig = getattr(signal, name, None)
            if sig is not None:
                self.handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._signal)
        try:
            self.saved = self.termios.tcgetattr(self.a)
            attrs = [*self.saved[:6], self.saved[6][:]]
            attrs[0] &= ~(self.termios.IXON | self.termios.ICRNL | self.termios.INLCR | self.termios.IGNCR)
            attrs[1] &= ~self.termios.OPOST
            attrs[3] &= ~(self.termios.ECHO | self.termios.ICANON | self.termios.ISIG | self.termios.IEXTEN)
            attrs[6][self.termios.VMIN], attrs[6][self.termios.VTIME] = 1, 0
            self.termios.tcsetattr(self.a, self.termios.TCSANOW, attrs)
        except self.termios.error as exc:
            raise TerminalFailure("termios setup failed") from exc
        # Mark before write: even a partial setup write requires VT cleanup.
        self.active = True
        self.write("\x1b[?25s\x1b[?1049h\x1b[?25l")

    def _signal(self, sig, _frame):
        if sig == signal.SIGINT:
            raise KeyboardInterrupt
        if sig == getattr(signal, "SIGWINCH", None):
            self.resized = True
            if self.reading:
                raise _Resize
            return
        raise PickerInterrupted(sig)

    def read_key(self):
        try:
            self.reading = True
            return self._read_key()
        finally:
            self.reading = False

    def _read_key(self):
        while True:
            if self.resized:
                self.resized = False
                return "resize", 1
            timeout = None if not self.pending else max(0, self.deadline - time.monotonic())
            if not self.select([self.a], [], [], timeout)[0]:
                key = "esc" if self.pending == b"\x1b" else "ignore"
                self.pending.clear()
                return key, 1
            try:
                ch = os.read(self.a, 1)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    raise PickerEOF("input terminal hung up") from exc
                raise
            if not ch:
                raise PickerEOF("terminal input ended")
            if ch == b"\x03":
                raise KeyboardInterrupt
            if ch in (b"\x04", b"\x1a"):
                raise PickerEOF("terminal input ended")
            if not self.pending:
                if ch in (b"\r", b"\n"):
                    return "enter", 1
                if ch != b"\x1b":
                    return "ignore", 1
                self.deadline = time.monotonic() + 0.2
            self.pending.extend(ch)
            seq = bytes(self.pending)
            if seq[:2] in (b"\x1b[", b"\x1bO") and len(seq) == 3 and seq[2:] in b"ABCD":
                self.pending.clear()
                return ("next" if seq[2:] in b"BC" else "previous"), 1
            if seq in (b"\x1b", b"\x1b[", b"\x1bO"):
                continue
            if len(seq) == 2:
                self.pending.clear()
                return "esc", 1
            # Swallow a bounded unknown CSI rather than treating its tail as keys.
            if seq.startswith(b"\x1b[") and len(seq) < 16 and not 0x40 <= seq[-1] <= 0x7e:
                continue
            self.pending.clear()
            return "ignore", 1

    def close(self):
        errors = []
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, self.handlers) if self.handlers else None
        try:
            if self.saved is not None:
                try:
                    self.termios.tcsetattr(self.a, self.termios.TCSANOW, self.saved)
                except (OSError, self.termios.error) as exc:
                    errors.append(exc)
            if self.active:
                try:
                    self.write("\x1b[?1049l\x1b[?25r")
                except Exception as exc:
                    errors.append(exc)
            for sig, handler in self.handlers.items():
                signal.signal(sig, handler)
        finally:
            if mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        if errors:
            raise TerminalFailure("terminal restoration failed") from errors[0]


# Fixed Windows ABI widths also make non-Windows console fixtures meaningful.
class _Coord(C.Structure):
    _fields_ = [("X", C.c_int16), ("Y", C.c_int16)]


class _Rect(C.Structure):
    _fields_ = [(k, C.c_int16) for k in ("Left", "Top", "Right", "Bottom")]


class _BufferInfo(C.Structure):
    _fields_ = [("size", _Coord), ("cursor", _Coord), ("attributes", C.c_uint16),
                ("window", _Rect), ("maximum", _Coord)]


class _CursorInfo(C.Structure):
    _fields_ = [("size", C.c_uint32), ("visible", C.c_int32)]


class _Key(C.Structure):
    _fields_ = [("down", C.c_int32), ("repeat", C.c_uint16), ("vk", C.c_uint16),
               ("scan", C.c_uint16), ("char", C.c_uint16), ("control", C.c_uint32)]


class _Event(C.Union):
    _fields_ = [("key", _Key), ("size", _Coord), ("padding", C.c_byte * 16)]


class _Input(C.Structure):
    _fields_ = [("kind", C.c_uint16), ("event", _Event)]


class _WindowsTerminal:
    def __init__(self, inp, out, *, api=None, handle_for_fd=None):
        a, b = _fds(inp, out)
        if api is None:
            import msvcrt
            api = C.WinDLL("kernel32", use_last_error=True)
            handle_for_fd = msvcrt.get_osfhandle
            signatures = {
                "GetConsoleMode": ([C.c_void_p, C.POINTER(C.c_uint32)], C.c_int32),
                "SetConsoleMode": ([C.c_void_p, C.c_uint32], C.c_int32),
                "CreateConsoleScreenBuffer": ([C.c_uint32, C.c_uint32, C.c_void_p, C.c_uint32, C.c_void_p], C.c_void_p),
                "SetConsoleActiveScreenBuffer": ([C.c_void_p], C.c_int32),
                "GetConsoleScreenBufferInfo": ([C.c_void_p, C.POINTER(_BufferInfo)], C.c_int32),
                "GetConsoleCursorInfo": ([C.c_void_p, C.POINTER(_CursorInfo)], C.c_int32),
                "SetConsoleCursorInfo": ([C.c_void_p, C.POINTER(_CursorInfo)], C.c_int32),
                "ReadConsoleInputW": ([C.c_void_p, C.POINTER(_Input), C.c_uint32, C.POINTER(C.c_uint32)], C.c_int32),
                "WriteConsoleW": ([C.c_void_p, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p], C.c_int32),
                "CloseHandle": ([C.c_void_p], C.c_int32),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(api, name)
                fn.argtypes, fn.restype = args, result
        self.api, self.a, self.b = api, handle_for_fd(a), handle_for_fd(b)
        self.mode, self.buffer, self.active = None, None, False
        self.handlers = {}
        self.closing, self.interruption = False, None

    def call(self, name, *args):
        if not getattr(self.api, name)(*args):
            raise OSError(f"{name} failed")

    def start(self):
        mode, output_mode = C.c_uint32(), C.c_uint32()
        try:
            self.call("GetConsoleMode", self.a, C.byref(mode))
            self.call("GetConsoleMode", self.b, C.byref(output_mode))
        except OSError as exc:
            raise TerminalUnavailable("native Windows input/output console required") from exc
        self.mode = mode.value
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                self.handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, self._signal)
        # No line/echo/processed/quick-edit mode: Ctrl-C is a key event. Enable
        # window events for blocking resize notification; keep other flags.
        self.call("SetConsoleMode", self.a, (mode.value | 0x88) & ~0x47)
        self.buffer = self.api.CreateConsoleScreenBuffer(0xc0000000, 3, None, 1, None)
        if self.buffer in (None, 0, C.c_void_p(-1).value):
            self.buffer = None
            raise TerminalUnavailable("cannot create private console screen buffer")
        try:
            self.call("SetConsoleMode", self.buffer, output_mode.value | 0x05)
        except OSError as exc:
            raise TerminalUnavailable("Windows console must support VT rendering") from exc
        cursor = _CursorInfo()
        self.call("GetConsoleCursorInfo", self.buffer, C.byref(cursor))
        cursor.visible = 0
        self.call("SetConsoleCursorInfo", self.buffer, C.byref(cursor))
        self.active = True
        self.call("SetConsoleActiveScreenBuffer", self.buffer)

    def _signal(self, sig, _frame):
        interruption = KeyboardInterrupt() if sig == signal.SIGINT else PickerInterrupted(sig)
        if self.closing:
            if self.interruption is None:
                self.interruption = interruption
            return
        raise interruption

    def size(self):
        info = _BufferInfo()
        self.call("GetConsoleScreenBufferInfo", self.buffer, C.byref(info))
        return info.window.Right - info.window.Left + 1, info.window.Bottom - info.window.Top + 1

    def write(self, text):
        # WCHAR is two bytes on Windows, including surrogate pairs. This also
        # tests the correct length for non-BMP labels on Linux fixtures.
        data = text.encode("utf-16-le")
        buf, written = C.create_string_buffer(data), C.c_uint32()
        self.call("WriteConsoleW", self.buffer, buf, len(data) // 2, C.byref(written), None)
        if written.value != len(data) // 2:
            raise OSError("short console write")

    def read_key(self):
        while True:
            event, read = _Input(), C.c_uint32()
            self.call("ReadConsoleInputW", self.a, C.byref(event), 1, C.byref(read))
            if read.value != 1:
                raise PickerEOF("console input ended")
            if event.kind == 4:
                return "resize", 1
            key = event.event.key
            if event.kind != 1 or not key.down:
                continue
            if key.char == 3 or (key.vk == 0x43 and key.control & 0x0c):
                raise KeyboardInterrupt
            if key.char in (4, 26):
                raise PickerEOF("console input ended")
            action = {0x25: "previous", 0x26: "previous", 0x27: "next", 0x28: "next",
                      0x0d: "enter", 0x1b: "esc"}.get(key.vk)
            if action:
                return action, max(1, key.repeat)

    def close(self):
        self.closing = True
        errors = []
        # Retain the SIGINT deferrer until every other handler is returned. A
        # caller's SIGINT can then raise only after the complete table is back.
        pending = {sig: handler for sig, handler in self.handlers.items() if sig != signal.SIGINT}
        if signal.SIGINT in self.handlers:
            pending[signal.SIGINT] = self.handlers[signal.SIGINT]

        def interrupted(exc):
            if self.interruption is None:
                self.interruption = exc

        def attempt(fn, *args):
            try:
                fn(*args)
            except (KeyboardInterrupt, PickerInterrupted) as exc:
                interrupted(exc)
            except OSError as exc:
                errors.append(exc)

        def handoff():
            # Protect the loop/control transitions, not only signal.signal().
            try:
                for sig, handler in tuple(pending.items()):
                    if sig == signal.SIGINT and any(s != sig for s in pending):
                        continue
                    if signal.getsignal(sig) != handler:
                        attempt(signal.signal, sig, handler)
                    if signal.getsignal(sig) == handler:
                        pending.pop(sig)
            except (KeyboardInterrupt, PickerInterrupted) as exc:
                interrupted(exc)

        try:
            if self.active:
                attempt(self.call, "SetConsoleActiveScreenBuffer", self.b)
            if self.mode is not None:
                attempt(self.call, "SetConsoleMode", self.a, self.mode)
            if self.buffer is not None:
                attempt(self.call, "CloseHandle", self.buffer)
            handoff()
            if not pending:
                self.closing = False  # Protected return-to-caller transition.
        except (KeyboardInterrupt, PickerInterrupted) as exc:
            interrupted(exc)
        finally:
            # One bounded completion pass, only for unacknowledged handlers.
            # A signal may have interrupted bookkeeping after installation.
            try:
                handoff()
            except (KeyboardInterrupt, PickerInterrupted) as exc:
                interrupted(exc)
            finally:
                self.closing = False
        if pending:
            errors.append(OSError("signal handler restoration did not complete"))
        if errors:
            raise TerminalFailure("console restoration failed") from errors[0]
        if self.interruption is not None:
            raise self.interruption


def choose_screen(screens, *, title=None, input_stream=None, output_stream=None) -> str | None:
    """Inspect host presentation records and require explicit Enter to return an id."""
    screens, title = _records(screens, title)
    if threading.current_thread() is not threading.main_thread():
        raise TerminalUnavailable("picker must run in the main thread")
    inp = sys.stdin if input_stream is None else input_stream
    out = sys.stdout if output_stream is None else output_stream
    terminal = _WindowsTerminal(inp, out) if os.name == "nt" else _PosixTerminal(inp, out)
    primaries = [i for i, s in enumerate(screens) if s["primary"] and s["selectable"]]
    selected = primaries[0] if len(primaries) == 1 else next((i for i, s in enumerate(screens) if s["selectable"]), 0)
    blocked = False
    try:
        terminal.start()
        while True:
            try:
                terminal.write(_frame(screens, selected, title, *terminal.size(), blocked))
                action, repeat = terminal.read_key()
            except _Resize:
                continue
            if action == "esc":
                return None
            if action == "enter":
                if screens[selected]["selectable"]:
                    return screens[selected]["id"]
                blocked = True
            elif action in ("previous", "next"):
                selected = (selected + (repeat if action == "next" else -repeat)) % len(screens)
                blocked = False
    except ScreenPickerError:
        raise
    except (OSError, ValueError) as exc:
        raise TerminalFailure("terminal operation failed") from exc
    finally:
        terminal.close()
