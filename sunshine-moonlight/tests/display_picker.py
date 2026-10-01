#!/usr/bin/env python3
"""Owned PTYs and explicit native-console fixtures only; no live host/GUI actions.

Run directly with python3. The private --pty-child mode is a subprocess fixture,
not a public picker command/demo. Windows fixtures validate API wiring and ABI,
not native Windows or macOS acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import ctypes as C
import errno
import io
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import screen_picker as picker


def screen(ident, label, **changes):
    row = {"id": ident, "label": label, "primary": False, "connected": True,
           "width": 1920, "height": 1080, "refresh_num": 60, "refresh_den": 1,
           "requested_fps": 60, "selectable": True, "reason": "preparation_required",
           "selection_intent": "prepare", "capture_candidate": False,
           # Actual host.presentation extras must not require a different API.
           "selector": "DP-1", "selector_mapping": "unverified", "rotation": 0,
           "runtime_capture": "unverified", "mapping_reason": "effective_encoder_device_unknown",
           "native_reason": None, "selector_candidate": False}
    row.update(changes)
    return row


SCREENS = [screen("kms:card0:eDP-1", "内置 e\u0301", primary=True),
           screen("kms:card1:DP-3", "外接 🖥️", width=3840, height=2160,
                  refresh_num=60000, refresh_den=1001)]


@contextmanager
def native_width_oracle():
    """Independent libc measurement; never consult the picker's width helper."""
    if os.name != "posix":
        raise unittest.SkipTest("native POSIX wcwidth oracle required")
    import locale
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        try:
            locale.setlocale(locale.LC_CTYPE, "C.UTF-8")
        except locale.Error:
            raise unittest.SkipTest("C.UTF-8 width oracle unavailable")
        try:
            width = C.CDLL(None).wcwidth
        except AttributeError:
            raise unittest.SkipTest("native wcwidth unavailable")
        width.argtypes, width.restype = [C.c_wchar], C.c_int
        def cells(text):
            values = [width(ch) for ch in text]
            if any(v < 0 for v in values):
                raise AssertionError(f"nonprintable oracle input: {text!r}")
            return sum(values)
        yield cells
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


def fixture(variant):
    if variant == "disabled":
        return [*SCREENS, screen("win:inactive", "Inactive 显示器", selectable=False,
                                connected=False, width=None, height=None,
                                refresh_num=None, refresh_den=None, requested_fps=None,
                                reason="inactive_display_path", selection_intent=None)]
    if variant == "all-disabled":
        return [screen("mac:7", "Unknown", selectable=False, connected=None, primary=None,
                       width=2560, height=None, refresh_num=0, refresh_den=1,
                       requested_fps=None, capture_candidate=None, reason=None,
                       selection_intent=None)]
    if variant == "many":
        return [screen(f"mac:{i}", f"显示器 {i}") for i in range(32)]
    if variant == "injection":
        return [screen("mac:1", "中文 e\u0301\x1b[31mRED\x1b[0m\x1b]0;PWN\x07\u202eevil\x9b2J\nDone")]
    if variant == "spacing-marks":
        return [screen("mac:1", "\u302e\u302f" * 15)]
    if variant == "primary-second":
        return [dict(SCREENS[0], primary=False), dict(SCREENS[1], primary=True)]
    if variant == "duplicate-primary":
        return [dict(s, primary=True) for s in SCREENS]
    return SCREENS


def pty_child(variant, result_fd):
    """Entry reachable only through this test runner's owned subprocess."""
    import termios
    before = termios.tcgetattr(0)
    watched = [getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGTSTP", "SIGWINCH")
               if hasattr(signal, n)]
    handlers = {s: signal.getsignal(s) for s in watched}
    out = sys.stdout
    if variant in ("render-error", "setup-error", "always-write-error", "short-write", "flush-error"):
        class Output:
            def __init__(self):
                self.writes = 0

            def fileno(self):
                return out.fileno()

            def isatty(self):
                return True

            def write(self, text):
                self.writes += 1
                if variant not in ("short-write", "flush-error") and (
                        self.writes == (2 if variant == "render-error" else 1) or variant == "always-write-error"):
                    raise OSError("injected render failure")
                written = out.write(text)
                return written - 1 if variant == "short-write" and self.writes == 2 else written

            def flush(self):
                if variant == "flush-error" and self.writes == 2:
                    raise OSError("injected flush failure")
                out.flush()
        output = Output()
    else:
        output = out
    context = nullcontext()
    boundary = {}
    if variant == "signal-after-raw":
        original = termios.tcsetattr
        def signal_after_raw(fd, when, attrs):
            original(fd, when, attrs)
            if not boundary and not termios.tcgetattr(fd)[3] & termios.ICANON:
                boundary["raw_signal_injected"] = True
                os.kill(os.getpid(), signal.SIGTERM)
        context = mock.patch.object(termios, "tcsetattr", side_effect=signal_after_raw)
    elif variant == "read-eof":
        context = mock.patch.object(picker.os, "read", return_value=b"")
    elif variant in ("read-error", "read-eio"):
        error = OSError(errno.EIO, "injected hangup") if variant == "read-eio" else OSError("injected read failure")
        context = mock.patch.object(picker.os, "read", side_effect=error)
    elif variant in ("getter-error", "setter-error"):
        name = "tcgetattr" if variant == "getter-error" else "tcsetattr"
        original, failed = getattr(termios, name), False
        def fail_once(*args):
            nonlocal failed
            if not failed:
                failed = True
                if name == "tcsetattr":
                    original(*args)  # Also cover an error AFTER a real mode change.
                raise termios.error("injected termios failure")
            return original(*args)
        context = mock.patch.object(termios, name, side_effect=fail_once)
    elif variant == "resize-during-setup":
        class ResizeOutput:
            def fileno(self):
                return out.fileno()
            def isatty(self):
                return True
            def write(self, text):
                if "?1049h" in text:
                    os.kill(os.getpid(), signal.SIGWINCH)
                return out.write(text)
            def flush(self):
                out.flush()
        output = ResizeOutput()
    payload = boundary
    try:
        with context:
            payload["result"] = picker.choose_screen(fixture(variant), input_stream=sys.stdin, output_stream=output)
    except BaseException as exc:
        payload["exception"] = type(exc).__name__
        payload["signum"] = getattr(exc, "signum", None)
    payload["restored"] = termios.tcgetattr(0) == before
    payload["signals_restored"] = all(signal.getsignal(s) == h for s, h in handlers.items())
    os.write(result_fd, json.dumps(payload).encode())
    os.close(result_fd)


class OwnedPTY:
    def __init__(self, variant="normal", *, columns=100, rows=24):
        import fcntl
        import pty
        import termios
        self.master, self.slave = pty.openpty()
        self.result, child_result = os.pipe()
        self.output = b""
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.before = termios.tcgetattr(self.slave)
        self.process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()),
                                         "--pty-child", variant, str(child_result)],
                                        stdin=self.slave, stdout=self.slave, stderr=subprocess.PIPE,
                                        pass_fds=(child_result,), close_fds=True)
        os.close(child_result)

    def collect_until(self, marker, timeout=3):
        deadline = time.monotonic() + timeout
        while marker not in self.output:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"PTY never displayed {marker!r}; output {self.output[-1000:]!r}")
            ready = select.select([self.master, self.result], [], [], remaining)[0]
            if self.master in ready:
                self.output += os.read(self.master, 16384)
            if self.result in ready and marker not in self.output:
                raise AssertionError(f"child finished before display: {os.read(self.result, 4096)!r}")
        return self.output

    def ready(self):
        return self.collect_until(b"Esc: cancel")

    def send(self, data):
        os.write(self.master, data)

    def resize(self, columns, rows):
        import fcntl
        import termios
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        os.kill(self.process.pid, signal.SIGWINCH)

    def finish(self):
        import termios
        deadline, data = time.monotonic() + 3, b""
        while not data:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("child did not finish")
            ready = select.select([self.master, self.result], [], [], remaining)[0]
            if self.master in ready:
                self.output += os.read(self.master, 16384)
            if self.result in ready:
                data = os.read(self.result, 4096)
                if not data:
                    self.process.wait(timeout=3)
                    after = termios.tcgetattr(self.slave)
                    raise AssertionError(f"child exit {self.process.returncode}; termios restored {after == self.before}; "
                                         f"lflag {self.before[3]} -> {after[3]}")
        self.process.wait(timeout=3)
        while select.select([self.master], [], [], 0)[0]:
            self.output += os.read(self.master, 16384)
        if self.process.returncode != 0:
            raise AssertionError(self.process.stderr.read().decode())
        payload = json.loads(data)
        if termios.tcgetattr(self.slave) != self.before or not payload["restored"] or not payload["signals_restored"]:
            raise AssertionError(f"terminal/handler restoration failed: {payload}")
        return payload

    def close(self):
        if self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self.process.stderr.close()
        for fd in (self.master, self.slave, self.result):
            os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@unittest.skipUnless(os.name == "posix", "actual owned POSIX PTYs required")
class PosixPTYTests(unittest.TestCase):
    def assert_vt_restored(self, owned):
        self.assertIn(b"\x1b[?25s\x1b[?1049h\x1b[?25l", owned.output)
        self.assertTrue(owned.output.endswith(b"\x1b[?1049l\x1b[?25r"), owned.output[-200:])

    def test_nonfirst_right_enter_exact_id_and_current_fractional_facts(self):
        with OwnedPTY() as p:
            text = p.ready().decode()
            self.assertIn("2 screens | 2 available", text)
            self.assertIn("3840x2160 @ 59.940 Hz (60000/1001)", text)
            self.assertIn("capture candidate no", text)
            self.assertIn("request 60 FPS", text)
            p.send(b"\x1b[C\r")
            self.assertEqual(p.finish()["result"], SCREENS[1]["id"])
            self.assert_vt_restored(p)

    def test_left_wrap_and_application_arrow(self):
        for keys in (b"\x1b[D\r", b"\x1bOD\r"):
            with self.subTest(keys=keys), OwnedPTY() as p:
                p.ready()
                p.send(keys)
                self.assertEqual(p.finish()["result"], SCREENS[1]["id"])
                self.assert_vt_restored(p)

    def test_fragmented_real_arrow_bytes_do_not_cancel(self):
        with OwnedPTY() as p:
            p.ready()
            for fragment in (b"\x1b", b"[", b"C"):
                p.send(fragment)
                self.assertFalse(select.select([p.result], [], [], 0.025)[0])
            p.send(b"\r")
            self.assertEqual(p.finish()["result"], SCREENS[1]["id"])
            self.assert_vt_restored(p)

    def test_no_implicit_acceptance_primary_and_duplicate_primary(self):
        for variant, expected in (("primary-second", SCREENS[1]["id"]),
                                  ("duplicate-primary", SCREENS[0]["id"])):
            with self.subTest(variant=variant), OwnedPTY(variant) as p:
                p.ready()
                self.assertFalse(select.select([p.result], [], [], 0.025)[0])
                p.send(b"\r")
                self.assertEqual(p.finish()["result"], expected)

    def test_bare_escape_cancel_restores(self):
        with OwnedPTY() as p:
            p.ready()
            p.send(b"\x1b")
            self.assertIsNone(p.finish()["result"])
            self.assert_vt_restored(p)

    def test_escape_followed_immediately_by_enter_cancels_not_accepts(self):
        with OwnedPTY() as p:
            p.ready()
            p.send(b"\x1b\r")
            self.assertIsNone(p.finish()["result"])
            self.assert_vt_restored(p)

    def test_ctrl_c_even_inside_arrow_fragment(self):
        for data in (b"\x03", b"\x1b[\x03"):
            with self.subTest(data=data), OwnedPTY() as p:
                p.ready()
                p.send(data)
                self.assertEqual(p.finish()["exception"], "KeyboardInterrupt")
                self.assert_vt_restored(p)

    def test_async_signals_restore_modes_and_original_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGTSTP):
            with self.subTest(sig=sig), OwnedPTY() as p:
                p.ready()
                os.kill(p.process.pid, sig)
                result = p.finish()
                self.assertEqual(result["exception"], "KeyboardInterrupt" if sig == signal.SIGINT else "PickerInterrupted")
                if sig != signal.SIGINT:
                    self.assertEqual(result["signum"], sig)
                self.assert_vt_restored(p)

    def test_sigterm_exactly_after_real_raw_application_restores(self):
        with OwnedPTY("signal-after-raw") as p:
            result = p.finish()
            self.assertTrue(result["raw_signal_injected"])
            self.assertEqual(result["exception"], "PickerInterrupted")
            self.assertEqual(result["signum"], signal.SIGTERM)
            self.assertNotIn("result", result)
            self.assertNotIn(b"?1049h", p.output)  # No VT mutation preceded the boundary.

    def test_spacing_marks_fit_actual_pty_using_native_width(self):
        with native_width_oracle() as cells, OwnedPTY("spacing-marks", columns=24, rows=9) as p:
            self.assertEqual((cells("\u302e"), cells("\u302f")), (2, 2))
            p.ready()
            p.send(b"\x1b")
            self.assertIsNone(p.finish()["result"])
            frame = p.output.split(b"\x1b[H\x1b[2J", 1)[1].split(b"\x1b[?1049l", 1)[0].decode()
            lines = frame.split("\r\n")
            self.assertEqual(len(lines), 8)
            for line in lines:
                self.assertLessEqual(cells(line), 23, line)
            self.assertIn("\u302e\u302f", lines[2])
            self.assertTrue(lines[2].endswith("…"))
            self.assert_vt_restored(p)

    def test_eof_and_io_errors_in_actual_raw_pty_restore(self):
        for variant, keys, error in (("normal", b"\x04", "PickerEOF"),
                                     ("read-eof", b"x", "PickerEOF"),
                                     ("read-error", b"x", "TerminalFailure"),
                                     ("read-eio", b"x", "PickerEOF"),
                                     ("getter-error", None, "TerminalFailure"),
                                     ("setter-error", None, "TerminalFailure"),
                                     ("render-error", None, "TerminalFailure"),
                                     ("short-write", None, "TerminalFailure"),
                                     ("flush-error", None, "TerminalFailure"),
                                     ("setup-error", None, "TerminalFailure"),
                                     ("always-write-error", None, "TerminalFailure")):
            with self.subTest(variant=variant), OwnedPTY(variant) as p:
                if keys is not None:
                    p.ready()
                    p.send(keys)
                self.assertEqual(p.finish()["exception"], error)
                if variant not in ("always-write-error", "getter-error", "setter-error"):
                    self.assertTrue(p.output.endswith(b"\x1b[?1049l\x1b[?25r"))

    def test_disabled_row_visible_and_enter_refuses_then_other_selection(self):
        with OwnedPTY("disabled") as p:
            self.assertIn(b"UNAVAILABLE: inactive_display_path", p.ready())
            p.send(b"\x1b[D\r")
            p.collect_until(b"Enter refused: unavailable screen")
            self.assertFalse(select.select([p.result], [], [], 0.025)[0])
            p.send(b"\x1b[D\r")
            self.assertEqual(p.finish()["result"], SCREENS[1]["id"])
            self.assert_vt_restored(p)

    def test_all_unavailable_unknown_facts_never_accept(self):
        with OwnedPTY("all-disabled") as p:
            text = p.ready().decode()
            self.assertIn("1 screens | 0 available", text)
            self.assertIn("2560x? @ unknown Hz", text)
            self.assertIn("UNAVAILABLE: reason unknown", text)
            self.assertIn("primary unknown | connected unknown", text)
            p.send(b"\r")
            p.collect_until(b"Enter refused: unavailable screen")
            p.send(b"\x1b")
            self.assertIsNone(p.finish()["result"])

    def test_unicode_and_terminal_injection_real_output(self):
        with OwnedPTY("injection") as p:
            text = p.ready().decode()
            self.assertIn("中文 e\u0301REDevilDone", text)
            for bad in ("PWN", "\u202e", "\x1b[31m", "\x9b"):
                self.assertNotIn(bad, text)
            p.send(b"\r")
            self.assertEqual(p.finish()["result"], "mac:1")

    def test_bounded_viewport_wrap_last_and_resize(self):
        with OwnedPTY("many", columns=32, rows=9) as p:
            p.ready()
            self.assertLess(len(p.output), 700)
            p.send(b"\x1b[D")
            p.collect_until(b"> 32.")
            p.resize(70, 13)
            p.collect_until(b"rows 31-32")
            p.send(b"\r")
            self.assertEqual(p.finish()["result"], "mac:31")
            for frame in p.output.split(b"\x1b[H\x1b[2J")[1:]:
                self.assertLess(len(frame), 1800)
            self.assert_vt_restored(p)

    def test_resize_during_setup_is_not_a_private_exception(self):
        with OwnedPTY("resize-during-setup") as p:
            p.ready()
            p.send(b"\x1b[C\r")
            self.assertEqual(p.finish()["result"], SCREENS[1]["id"])
            self.assert_vt_restored(p)

    def test_resize_too_small_and_initial_too_small_restore(self):
        with OwnedPTY() as p:
            p.ready()
            p.resize(12, 5)
            self.assertEqual(p.finish()["exception"], "TerminalUnavailable")
            self.assert_vt_restored(p)
        with OwnedPTY(columns=12, rows=5) as p:
            self.assertEqual(p.finish()["exception"], "TerminalUnavailable")
            self.assert_vt_restored(p)

    def test_mismatched_or_redirected_owned_terminals_refuse_without_changes(self):
        import pty
        import termios
        a, b = pty.openpty()
        c, d = pty.openpty()
        try:
            before_a, before_d = termios.tcgetattr(b), termios.tcgetattr(d)
            for inp, out in ((FixtureStream(b), FixtureStream(d)),
                             (FixtureStream(b), io.StringIO()),
                             (io.StringIO(), FixtureStream(d))):
                with self.subTest(), self.assertRaises(picker.TerminalUnavailable):
                    picker.choose_screen(SCREENS, input_stream=inp, output_stream=out)
            self.assertEqual(termios.tcgetattr(b), before_a)
            self.assertEqual(termios.tcgetattr(d), before_d)
        finally:
            for fd in (a, b, c, d):
                os.close(fd)

    def test_unknown_and_truncated_escape_never_select(self):
        with OwnedPTY() as p:
            p.ready()
            p.send(b"\x1b[999~")
            self.assertFalse(select.select([p.result], [], [], 0.025)[0])
            p.send(b"\x1b[")
            self.assertFalse(select.select([p.result], [], [], 0.25)[0])
            p.send(b"\x1b[C\r")
            self.assertEqual(p.finish()["result"], SCREENS[1]["id"])


class ValidationAndDisplayTests(unittest.TestCase):
    def test_non_tty_refuses_both_channels_without_output(self):
        inp, out = io.StringIO(), io.StringIO()
        with self.assertRaises(picker.TerminalUnavailable):
            picker.choose_screen(SCREENS, input_stream=inp, output_stream=out)
        self.assertEqual(out.getvalue(), "")

    def test_empty_oversize_and_malformed_fields_explicit(self):
        invalid = [[], [*SCREENS] * 17, iter(SCREENS), [SCREENS[0], SCREENS[0]], [{}],
                   [dict(SCREENS[0], id="x\x1b[2J")], [dict(SCREENS[0], selectable=1)],
                   [dict(SCREENS[0], refresh_den=True)], [dict(SCREENS[0], width=-1)],
                   [dict(SCREENS[0], label="x" * 1025)],
                   [dict(SCREENS[0], selectable=True, connected=False)],
                   [dict(SCREENS[0], selection_intent="launch")],
                   [dict(SCREENS[0], selection_intent="\x1b[31mprepare")]]
        for rows in invalid:
            with self.subTest(rows=str(rows)[:80]), self.assertRaises(picker.InvalidScreens):
                picker.choose_screen(rows, input_stream=io.StringIO(), output_stream=io.StringIO())

    def test_thread_refusal_explicit(self):
        errors = []
        def run():
            try:
                picker.choose_screen(SCREENS)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertIsInstance(errors[0], picker.TerminalUnavailable)

    def test_cell_width_and_bounded_text(self):
        self.assertEqual(picker._clip("中文e\u0301abc", 7), "中文e\u0301a…")
        self.assertEqual(picker._clip("🖥️abcdef", 4), "🖥️a…")
        records, title = picker._records([screen("mac:1", "中文" * 500)], "\x1b]0;evil\x07标题\u2066")
        frame = picker._frame(records, 0, title, 25, 9, False)
        self.assertLess(len(frame.encode()), 800)
        for line in frame.removeprefix("\x1b[H\x1b[2J").split("\r\n"):
            self.assertLessEqual(sum(picker._cell_width(line, i) for i in range(len(line))), 24)
        self.assertIn("标题", frame)
        self.assertNotIn("evil", frame)

    def test_native_spacing_and_zero_marks_classification_and_preclip(self):
        with native_width_oracle() as cells:
            # Independent libc widths discriminate Mc/W from ordinary Mn/Me,
            # including zero-CCC and wide nonspacing marks.
            for ch, expected in (("\u302e", 2), ("\u302f", 2), ("\u0301", 0),
                                 ("\u034f", 0), ("\u20dd", 0), ("\u3099", 0)):
                with self.subTest(ch=hex(ord(ch))):
                    self.assertEqual(cells(ch), expected)
                    self.assertEqual(picker._cell_width(ch, 0), cells(ch))
            self.assertEqual(picker._clip("e\u0301\u20dd", 1), "e\u0301\u20dd")
            records, _ = picker._records([screen("mac:1", "\u302e\u302f" * 100)], None)
            self.assertLessEqual(cells(records[0]["label"]), 128)
            self.assertTrue(records[0]["label"].endswith("…"))

    def test_host_data_not_mutated_no_capture_claim_and_zero_unknown(self):
        before = json.dumps(SCREENS)
        rows, title = picker._records(SCREENS, None)
        frame = picker._frame(rows, 0, title, 100, 24, False)
        self.assertIn("AVAILABLE: prepare | capture candidate no", frame)
        self.assertNotIn("verified", frame)
        self.assertEqual(json.dumps(SCREENS), before)
        rows[0].update(width=0, refresh_num=0, refresh_den=0)
        self.assertIn("?x1080 @ unknown Hz", picker._frame(rows, 0, title, 100, 24, False))


class FixtureStream:
    def __init__(self, fd):
        self.fd = fd

    def fileno(self):
        return self.fd

    def isatty(self):
        return True


def win_key(vk, char=0, *, down=1, repeat=1, control=0):
    rec = picker._Input()
    rec.kind = 1
    rec.event.key = picker._Key(down, repeat, vk, 0, char, control)
    return rec


class ConsoleFixture:
    """Explicit Windows API fixture: handles 10=input, 20=original, 30=owned."""
    def __init__(self, events=(), fail=None):
        self.events, self.fail = list(events), fail
        self.modes, self.initial = {10: 0x257, 20: 0x03}, {10: 0x257, 20: 0x03}
        self.handlers = {getattr(signal, name): signal.getsignal(getattr(signal, name))
                         for name in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, name)}
        self.active, self.original_cursor = 20, (37, 0)
        self.closed, self.calls, self.output = [], [], []

    def api(self, name, *args):
        self.calls.append((name, *args))
        if name == self.fail:
            # One injected failure; restoration is an independent attempt.
            self.fail = None
            return 0
        if name == "GetConsoleMode":
            C.cast(args[1], C.POINTER(C.c_uint32))[0] = self.modes[args[0]]
        elif name == "SetConsoleMode":
            self.modes[args[0]] = args[1]
        elif name == "CreateConsoleScreenBuffer":
            self.modes[30] = 0x03
            return 30
        elif name == "GetConsoleCursorInfo":
            C.cast(args[1], C.POINTER(picker._CursorInfo))[0] = picker._CursorInfo(37, 1)
        elif name == "SetConsoleCursorInfo":
            assert args[0] == 30  # Original cursor and buffer must be untouched.
        elif name == "SetConsoleActiveScreenBuffer":
            self.active = args[0]
        elif name == "GetConsoleScreenBufferInfo":
            info = picker._BufferInfo()
            info.window = picker._Rect(0, 0, 99, 23)
            C.cast(args[1], C.POINTER(picker._BufferInfo))[0] = info
        elif name == "WriteConsoleW":
            self.output.append(C.string_at(args[1], args[2] * 2).decode("utf-16-le"))
            C.cast(args[3], C.POINTER(C.c_uint32))[0] = args[2]
        elif name == "ReadConsoleInputW":
            if self.events:
                C.cast(args[1], C.POINTER(picker._Input))[0] = self.events.pop(0)
                C.cast(args[3], C.POINTER(C.c_uint32))[0] = 1
            else:
                C.cast(args[3], C.POINTER(C.c_uint32))[0] = 0
        elif name == "CloseHandle":
            self.closed.append(args[0])
        else:
            raise AssertionError(name)
        return 1

    def __getattr__(self, name):
        return lambda *args: self.api(name, *args)

    def terminal(self):
        with mock.patch.object(picker.os, "isatty", return_value=True):
            return picker._WindowsTerminal(FixtureStream(1), FixtureStream(2), api=self,
                                            handle_for_fd=lambda fd: {1: 10, 2: 20}[fd])


class NativeHandoffConsoleFixture(ConsoleFixture):
    """Normal constructor/signature path, independent bytes and >32-bit handles."""
    handles = {10: 0x100000010, 20: 0x100000020, 30: 0x100000030}

    def __init__(self):
        super().__init__()
        self.records = [struct.pack("<H2xiHHHHI", 1, 1, 1, vk, 0, 0, 0)
                        for vk in (0x27, 0x0d)]
        self.functions = {}
        self.native_calls = []

    def __getattr__(self, name):
        if name not in self.functions:
            def call(*args):
                self.native_calls.append((name, *args))
                normalized = list(args)
                reverse = {value: key for key, value in self.handles.items()}
                if name != "CreateConsoleScreenBuffer":
                    normalized[0] = reverse[args[0]]
                if name == "ReadConsoleInputW":
                    raw = self.records.pop(0)
                    C.memmove(args[1], raw, len(raw))
                    C.cast(args[3], C.POINTER(C.c_uint32))[0] = 1
                    return 1
                if name == "GetConsoleScreenBufferInfo":
                    raw = struct.pack("<hhhhHhhhhhh", 100, 24, 0, 0, 7, 0, 0, 99, 23, 100, 24)
                    C.memmove(args[1], raw, len(raw))
                    return 1
                result = self.api(name, *normalized)
                return self.handles[result] if name == "CreateConsoleScreenBuffer" else result
            self.functions[name] = call
        return self.functions[name]


class WindowsConsoleFixtureTests(unittest.TestCase):
    def choose(self, fixture, rows=SCREENS):
        term = fixture.terminal()
        with mock.patch.object(picker.os, "name", "nt"), mock.patch.object(picker, "_WindowsTerminal", return_value=term):
            return picker.choose_screen(rows, input_stream=FixtureStream(1), output_stream=FixtureStream(2))

    def restored(self, fixture):
        self.assertEqual(fixture.active, 20)
        self.assertEqual(fixture.modes[10], fixture.initial[10])
        self.assertEqual(fixture.modes[20], fixture.initial[20])
        self.assertEqual(fixture.original_cursor, (37, 0))
        self.assertTrue(all(signal.getsignal(s) == h for s, h in fixture.handlers.items()))
        if any(call[0] == "CreateConsoleScreenBuffer" for call in fixture.calls) and 30 in fixture.modes:
            self.assertEqual(fixture.closed, [30])
        self.assertFalse(any(call[0] == "SetConsoleCursorInfo" and call[1] == 20 for call in fixture.calls))

    def test_independent_win32_abi_offsets(self):
        self.assertEqual(C.sizeof(picker._Input), 20)
        self.assertEqual(picker._Input.event.offset, 4)
        self.assertEqual(C.sizeof(picker._Key), 16)
        self.assertEqual(picker._Key.char.offset, 10)
        self.assertEqual(C.sizeof(picker._BufferInfo), 22)
        self.assertEqual(C.sizeof(picker._CursorInfo), 8)
        raw = struct.pack("<H2xiHHHHI", 1, 1, 2, 0x27, 0, 0, 0)
        rec = picker._Input.from_buffer_copy(raw)
        self.assertEqual((rec.kind, rec.event.key.vk, rec.event.key.repeat), (1, 0x27, 2))

    def test_nonfirst_unicode_native_wide_write_and_input_mode(self):
        f = ConsoleFixture([win_key(0x27), win_key(0x0d)])
        self.assertEqual(self.choose(f), SCREENS[1]["id"])
        self.assertIn("外接 🖥️", "".join(f.output))
        modes = [c[2] for c in f.calls if c[0] == "SetConsoleMode" and c[1] == 10]
        self.assertEqual(modes[0] & 0x47, 0)
        self.assertEqual(modes[0] & 0x88, 0x88)
        self.restored(f)

    def test_left_wrap_keyup_focus_resize_repeat_and_escape(self):
        resize, focus = picker._Input(), picker._Input()
        resize.kind, focus.kind = 4, 16
        f = ConsoleFixture([win_key(0x27, down=0), focus, resize, win_key(0x25, repeat=3), win_key(0x0d)])
        self.assertEqual(self.choose(f), SCREENS[1]["id"])
        self.restored(f)
        f = ConsoleFixture([win_key(0x1b)])
        self.assertIsNone(self.choose(f))
        self.restored(f)

    def test_ctrl_c_eof_and_disabled_enter_restore(self):
        for key, error in ((win_key(0, 3), KeyboardInterrupt),
                           (win_key(0x43, control=0x08), KeyboardInterrupt),
                           (win_key(0, 26), picker.PickerEOF), (None, picker.PickerEOF)):
            f = ConsoleFixture([] if key is None else [key])
            with self.subTest(key=key), self.assertRaises(error):
                self.choose(f)
            self.restored(f)
        f = ConsoleFixture([win_key(0x0d), win_key(0x1b)])
        self.assertIsNone(self.choose(f, fixture("all-disabled")))
        self.assertIn("Enter refused: unavailable screen", "".join(f.output))
        self.restored(f)

    def test_setup_input_render_failure_restore_independent_steps(self):
        for operation in ("GetConsoleMode", "CreateConsoleScreenBuffer", "SetConsoleMode",
                          "GetConsoleCursorInfo", "SetConsoleCursorInfo", "SetConsoleActiveScreenBuffer",
                          "GetConsoleScreenBufferInfo", "WriteConsoleW", "ReadConsoleInputW"):
            f = ConsoleFixture([win_key(0x0d)], fail=operation)
            with self.subTest(operation=operation), self.assertRaises(picker.ScreenPickerError):
                self.choose(f)
            self.restored(f)

    def test_native_console_without_vt_refuses_and_restores(self):
        f = ConsoleFixture()
        original = f.api
        def no_vt(name, *args):
            if name == "SetConsoleMode" and args[0] == 30:
                return 0
            return original(name, *args)
        with mock.patch.object(f, "api", side_effect=no_vt), self.assertRaises(picker.TerminalUnavailable):
            self.choose(f)
        self.restored(f)

    def test_console_signal_exception_restores(self):
        f = ConsoleFixture()
        original = f.api
        def interrupt(name, *args):
            if name == "ReadConsoleInputW":
                os.kill(os.getpid(), signal.SIGTERM)
            return original(name, *args)
        with mock.patch.object(f, "api", side_effect=interrupt), self.assertRaises(picker.PickerInterrupted):
            self.choose(f)
        self.restored(f)

    def test_actual_sigint_between_handler_calls_preserves_prior_handlers(self):
        originals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        close_code = picker._WindowsTerminal.close.__code__
        previous_trace = sys.gettrace()
        for behavior in ("default", "custom-raise", "custom-return"):
            with self.subTest(behavior=behavior):
                seen, boundary = [], []
                interruption = picker.PickerInterrupted(signal.SIGINT)
                def prior_int(sig, _frame):
                    seen.append(("INT", sig))
                    if behavior == "custom-raise":
                        raise interruption
                def prior_term(sig, _frame):
                    seen.append(("TERM", sig))
                prior = signal.default_int_handler if behavior == "default" else prior_int
                signal.signal(signal.SIGINT, prior)
                signal.signal(signal.SIGTERM, prior_term)
                fixture = NativeHandoffConsoleFixture()
                fake_msvcrt = types.ModuleType("msvcrt")
                fake_msvcrt.get_osfhandle = lambda fd: fixture.handles[{1: 10, 2: 20}[fd]]
                def trace(frame, event, _arg):
                    if (event == "line" and frame.f_code is close_code and not boundary
                            and signal.getsignal(signal.SIGINT) == prior):
                        # On the reviewed source this is exactly the next loop
                        # transition: INT is original, TERM still belongs to picker.
                        boundary.append((frame.f_lineno, signal.getsignal(signal.SIGTERM) == prior_term))
                        os.kill(os.getpid(), signal.SIGINT)
                    return trace
                try:
                    with mock.patch.object(picker.os, "isatty", return_value=True), \
                         mock.patch.object(picker.os, "name", "nt"), \
                         mock.patch.object(picker.C, "WinDLL", return_value=fixture, create=True), \
                         mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                        sys.settrace(trace)
                        try:
                            if behavior == "custom-return":
                                self.assertEqual(picker.choose_screen(SCREENS, input_stream=FixtureStream(1),
                                                  output_stream=FixtureStream(2)), SCREENS[1]["id"])
                            else:
                                kind = KeyboardInterrupt if behavior == "default" else picker.PickerInterrupted
                                with self.assertRaises(kind) as raised:
                                    picker.choose_screen(SCREENS, input_stream=FixtureStream(1),
                                                         output_stream=FixtureStream(2))
                                if behavior == "custom-raise":
                                    self.assertIs(raised.exception, interruption)
                        finally:
                            sys.settrace(previous_trace)
                    self.assertEqual(len(boundary), 1)
                    self.assertTrue(boundary[0][1], f"TERM pending at interrupted handoff: {boundary}")
                    self.restored(fixture)
                    self.assertTrue(fixture.functions["ReadConsoleInputW"].argtypes)
                    self.assertTrue(any(call[1] > 2**32 for call in fixture.native_calls
                                        if call[0] == "SetConsoleActiveScreenBuffer"))
                    os.kill(os.getpid(), signal.SIGTERM)
                    self.assertEqual(seen[-1], ("TERM", signal.SIGTERM))
                    if behavior != "default":
                        self.assertIn(("INT", signal.SIGINT), seen)
                finally:
                    sys.settrace(previous_trace)
                    for sig, handler in originals.items():
                        signal.signal(sig, handler)

    def test_close_interrupts_do_not_skip_remaining_restoration(self):
        boundaries = (("SetConsoleActiveScreenBuffer", 20), ("SetConsoleMode", 10), ("CloseHandle", 30))
        for boundary in boundaries:
            for kind in (KeyboardInterrupt, picker.PickerInterrupted):
                with self.subTest(boundary=boundary, kind=kind.__name__):
                    f = ConsoleFixture([win_key(0x27), win_key(0x0d)])
                    original, fired = f.api, False
                    def interrupt_after(name, *args):
                        nonlocal fired
                        result = original(name, *args)
                        is_cleanup = ((name, args[0]) == boundary and
                                      (name != "SetConsoleMode" or args[1] == f.initial[10]))
                        if is_cleanup and not fired:
                            fired = True
                            raise KeyboardInterrupt() if kind is KeyboardInterrupt else picker.PickerInterrupted(signal.SIGTERM)
                        return result
                    try:
                        with mock.patch.object(f, "api", side_effect=interrupt_after), self.assertRaises(kind):
                            self.choose(f)
                        self.assertTrue(fired)
                        self.restored(f)
                    finally:
                        # An old implementation must not leak its Python handlers
                        # from this intentionally failing console-only fixture.
                        for sig, handler in f.handlers.items():
                            signal.signal(sig, handler)

    def test_own_signals_during_close_defer_until_operation_and_cleanup_finish(self):
        boundaries = (("SetConsoleActiveScreenBuffer", 20), ("SetConsoleMode", 10), ("CloseHandle", 30))
        for boundary in boundaries:
            for sig in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(boundary=boundary, sig=sig):
                    f = ConsoleFixture([win_key(0x27), win_key(0x0d)])
                    original, fired, completed = f.api, False, False
                    def signal_before(name, *args):
                        nonlocal fired, completed
                        is_cleanup = ((name, args[0]) == boundary and
                                      (name != "SetConsoleMode" or args[1] == f.initial[10]))
                        if is_cleanup and not fired:
                            fired = True
                            os.kill(os.getpid(), sig)
                            result = original(name, *args)
                            completed = True
                            return result
                        return original(name, *args)
                    kind = KeyboardInterrupt if sig == signal.SIGINT else picker.PickerInterrupted
                    try:
                        with mock.patch.object(f, "api", side_effect=signal_before), self.assertRaises(kind) as raised:
                            self.choose(f)
                        self.assertTrue(fired and completed)  # Interrupt did not abort this native step.
                        if sig == signal.SIGTERM:
                            self.assertEqual(raised.exception.signum, sig)
                        self.restored(f)
                    finally:
                        for watched, handler in f.handlers.items():
                            signal.signal(watched, handler)

    def test_interrupts_in_handler_restoration_do_not_skip_remaining_handlers(self):
        original_signal = signal.signal
        for boundary in (signal.SIGINT, signal.SIGTERM):
            for kind in (KeyboardInterrupt, picker.PickerInterrupted):
                with self.subTest(boundary=boundary, kind=kind.__name__):
                    f = ConsoleFixture([win_key(0x0d)])
                    fired = False
                    def interrupt_after_handler(sig, handler):
                        nonlocal fired
                        previous = original_signal(sig, handler)
                        if sig == boundary and handler == f.handlers[sig] and not fired:
                            fired = True
                            raise KeyboardInterrupt() if kind is KeyboardInterrupt else picker.PickerInterrupted(signal.SIGTERM)
                        return previous
                    try:
                        with mock.patch.object(picker.signal, "signal", side_effect=interrupt_after_handler), self.assertRaises(kind):
                            self.choose(f)
                        self.assertTrue(fired)
                        self.restored(f)
                    finally:
                        for sig, handler in f.handlers.items():
                            original_signal(sig, handler)

    def test_handoff_keeps_sigint_deferred_between_other_handler_returns(self):
        originals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        previous_trace, fired, received = sys.gettrace(), [], []
        close_code = picker._WindowsTerminal.close.__code__
        def prior_int(sig, _frame):
            received.append(sig)
        def prior_term(_sig, _frame):
            pass
        def trace(frame, event, _arg):
            if (event == "line" and frame.f_code.co_name == "handoff"
                    and frame.f_back.f_code is close_code and not fired
                    and signal.getsignal(signal.SIGTERM) == prior_term
                    and signal.getsignal(signal.SIGINT) != prior_int):
                fired.append(frame.f_lineno)
                os.kill(os.getpid(), signal.SIGINT)
            return trace
        try:
            signal.signal(signal.SIGINT, prior_int)
            signal.signal(signal.SIGTERM, prior_term)
            fixture = ConsoleFixture([win_key(0x0d)])
            sys.settrace(trace)
            try:
                with self.assertRaises(KeyboardInterrupt):
                    self.choose(fixture)
            finally:
                sys.settrace(previous_trace)
            self.assertEqual(len(fired), 1)
            self.assertEqual(received, [])  # Still borrowed: the picker defers it.
            self.restored(fixture)
            os.kill(os.getpid(), signal.SIGINT)
            self.assertEqual(received, [signal.SIGINT])
        finally:
            sys.settrace(previous_trace)
            for sig, handler in originals.items():
                signal.signal(sig, handler)

    def test_prior_term_interrupt_outside_restore_call_completes_pending_handlers(self):
        originals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        previous_trace, fired, received = sys.gettrace(), [], []
        close_code = picker._WindowsTerminal.close.__code__
        interruption = picker.PickerInterrupted(signal.SIGTERM)
        def prior_term(sig, _frame):
            received.append(sig)
            raise interruption
        def trace(frame, event, _arg):
            if (event == "line" and frame.f_code.co_name == "handoff"
                    and frame.f_back.f_code is close_code and not fired
                    and signal.getsignal(signal.SIGTERM) == prior_term
                    and signal.getsignal(signal.SIGINT) != signal.default_int_handler):
                fired.append(frame.f_lineno)
                os.kill(os.getpid(), signal.SIGTERM)
            return trace
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, prior_term)
            fixture = ConsoleFixture([win_key(0x0d)])
            sys.settrace(trace)
            try:
                with self.assertRaises(picker.PickerInterrupted) as raised:
                    self.choose(fixture)
            finally:
                sys.settrace(previous_trace)
            self.assertIs(raised.exception, interruption)
            self.assertEqual(len(fired), 1)
            self.restored(fixture)
            with self.assertRaises(picker.PickerInterrupted):
                os.kill(os.getpid(), signal.SIGTERM)
            self.assertEqual(received, [signal.SIGTERM, signal.SIGTERM])
        finally:
            sys.settrace(previous_trace)
            for sig, handler in originals.items():
                signal.signal(sig, handler)

    def test_between_handoff_sigint_preserves_first_deferred_interrupt(self):
        originals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        close_code, previous_trace = picker._WindowsTerminal.close.__code__, sys.gettrace()
        fired, delivered = [], []
        def prior_term(sig, _frame):
            delivered.append(sig)
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, prior_term)
            fixture = NativeHandoffConsoleFixture()
            original_api = fixture.api
            def first_interrupt(name, *args):
                if name == "SetConsoleActiveScreenBuffer" and args[0] == 20:
                    os.kill(os.getpid(), signal.SIGTERM)
                return original_api(name, *args)
            fake_msvcrt = types.ModuleType("msvcrt")
            fake_msvcrt.get_osfhandle = lambda fd: fixture.handles[{1: 10, 2: 20}[fd]]
            def trace(frame, event, _arg):
                if (event == "line" and frame.f_code is close_code and not fired
                        and signal.getsignal(signal.SIGINT) == signal.default_int_handler):
                    fired.append(frame.f_lineno)
                    os.kill(os.getpid(), signal.SIGINT)
                return trace
            with mock.patch.object(fixture, "api", side_effect=first_interrupt), \
                 mock.patch.object(picker.os, "isatty", return_value=True), \
                 mock.patch.object(picker.os, "name", "nt"), \
                 mock.patch.object(picker.C, "WinDLL", return_value=fixture, create=True), \
                 mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}):
                sys.settrace(trace)
                try:
                    with self.assertRaises(picker.PickerInterrupted) as raised:
                        picker.choose_screen(SCREENS, input_stream=FixtureStream(1), output_stream=FixtureStream(2))
                finally:
                    sys.settrace(previous_trace)
            self.assertEqual(raised.exception.signum, signal.SIGTERM)
            self.assertEqual(len(fired), 1)
            self.restored(fixture)
            self.assertEqual(delivered, [])
            os.kill(os.getpid(), signal.SIGTERM)
            self.assertEqual(delivered, [signal.SIGTERM])
        finally:
            sys.settrace(previous_trace)
            for sig, handler in originals.items():
                signal.signal(sig, handler)

    def test_multiple_cleanup_interruptions_propagate_first_after_restoration(self):
        f = ConsoleFixture([win_key(0x0d)])
        original, boundaries = f.api, []
        def interrupt_after(name, *args):
            result = original(name, *args)
            if name == "SetConsoleActiveScreenBuffer" and args[0] == 20:
                boundaries.append("buffer")
                raise KeyboardInterrupt
            if name == "SetConsoleMode" and args[0] == 10 and args[1] == f.initial[10]:
                boundaries.append("mode")
                raise picker.PickerInterrupted(signal.SIGTERM)
            return result
        try:
            with mock.patch.object(f, "api", side_effect=interrupt_after), self.assertRaises(KeyboardInterrupt):
                self.choose(f)
            self.assertEqual(boundaries, ["buffer", "mode"])
            self.restored(f)
        finally:
            for sig, handler in f.handlers.items():
                signal.signal(sig, handler)

    def test_restoration_error_outranks_deferred_interrupt_without_success(self):
        f = ConsoleFixture([win_key(0x0d)], fail="CloseHandle")
        original = f.api
        def signal_before(name, *args):
            if name == "SetConsoleActiveScreenBuffer" and args[0] == 20:
                os.kill(os.getpid(), signal.SIGINT)
            return original(name, *args)
        try:
            with mock.patch.object(f, "api", side_effect=signal_before), self.assertRaises(picker.TerminalFailure) as raised:
                self.choose(f)
            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertEqual(f.active, 20)
            self.assertEqual(f.modes[10], f.initial[10])
            self.assertTrue(all(signal.getsignal(s) == h for s, h in f.handlers.items()))
            self.assertIn(("CloseHandle", 30), f.calls)
            self.assertEqual(f.closed, [])  # Native failure is explicit, not claimed restored.
        finally:
            for sig, handler in f.handlers.items():
                signal.signal(sig, handler)

    def test_short_write_and_restoration_error_are_explicit(self):
        f = ConsoleFixture([win_key(0x0d)])
        original = f.api
        def short(name, *args):
            result = original(name, *args)
            if name == "WriteConsoleW":
                C.cast(args[3], C.POINTER(C.c_uint32))[0] = args[2] - 1
            return result
        with mock.patch.object(f, "api", side_effect=short), self.assertRaises(picker.TerminalFailure):
            self.choose(f)
        self.restored(f)
        f = ConsoleFixture([win_key(0x0d)], fail="CloseHandle")
        with self.assertRaises(picker.TerminalFailure):
            self.choose(f)
        self.assertEqual(f.active, 20)
        self.assertEqual(f.modes[10], f.initial[10])


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--pty-child":
        pty_child(sys.argv[2], int(sys.argv[3]))
    else:
        unittest.main()
