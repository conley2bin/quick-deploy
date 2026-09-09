#!/usr/bin/env python3
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import sys
import termios
import time
from pathlib import Path


def receive(fd: int, deadline: float, marker: bytes = b"SUSPEND_GUARD_TOPOLOGY=") -> str:
    out = b""
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        if marker in out:
            break
    return out.decode("utf-8", errors="replace")


def result(output: str) -> dict:
    for line in output.replace("\r", "").splitlines():
        marker = "SUSPEND_GUARD_TOPOLOGY="
        if marker in line:
            return json.loads(line.split(marker, 1)[1])
    raise AssertionError(f"classifier result missing: {output!r}")


def start_pi(pi: str, args: list[str], env: dict[str, str]) -> tuple[int, int]:
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe(pi, [pi, *args], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    return pid, fd


def run_direct(pi: str, fixture: str, env: dict[str, str]) -> dict:
    pid, fd = start_pi(pi, ["--no-extensions", "--no-session", "--offline", "--extension", fixture], env)
    reaped = False
    try:
        output = receive(fd, time.time() + 8)
        exited = wait_for_exit(pid, time.time() + 2)
        if exited is None:
            raise AssertionError("direct topology fixture did not exit")
        reaped = True
        return result(output)
    finally:
        if not reaped:
            terminate_and_reap(pid)
        os.close(fd)


def run_shell(pi: str, fixture: str, env: dict[str, str]) -> dict:
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("bash", ["bash", "--noprofile", "--norc", "-i"], env)
    reaped = False
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
        receive(fd, time.time() + 2)
        os.write(fd, f"{pi} --no-extensions --no-session --offline --extension {fixture}\n".encode())
        output = receive(fd, time.time() + 8)
        os.write(fd, b"exit\n")
        exited = wait_for_exit(pid, time.time() + 3)
        if exited is None:
            raise AssertionError("interactive-shell topology fixture did not exit")
        reaped = True
        return result(output)
    finally:
        if not reaped:
            terminate_and_reap(pid)
        os.close(fd)


def wait_for_exit(pid: int, deadline: float) -> tuple[int, int] | None:
    while time.time() < deadline:
        got, status = os.waitpid(pid, os.WNOHANG)
        if got:
            return got, status
        time.sleep(0.05)
    return None


def terminate_and_reap(pid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            return
        if wait_for_exit(pid, time.time() + 2) is not None:
            return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def assert_guard_end_to_end(pi: str, index: str, env: dict[str, str]) -> None:
    pid, fd = start_pi(pi, ["--no-extensions", "--no-session", "--offline", "--extension", index], env)
    reaped = False
    try:
        ready = receive(fd, time.time() + 8, b"pi-suspend-guard")
        if "pi-suspend-guard" not in ready:
            raise AssertionError(f"guard extension did not reach TUI readiness: {ready!r}")
        got, _ = os.waitpid(pid, os.WNOHANG)
        if got:
            reaped = True
            raise AssertionError("Pi exited before the suspend guard test")

        os.write(fd, b"\x1a")
        after_suspend = receive(fd, time.time() + 5, b"Suspend unavailable")
        if "Suspend unavailable: this process group has no controlling job owner." not in after_suspend:
            raise AssertionError(f"guard notice missing after Ctrl-Z: {after_suspend!r}")
        got, _ = os.waitpid(pid, os.WNOHANG)
        if got:
            reaped = True
            raise AssertionError("Pi exited after guarded Ctrl-Z")
        lflag = termios.tcgetattr(fd)[3]
        if lflag & (termios.ICANON | termios.ECHO):
            raise AssertionError("guarded Ctrl-Z switched the inner PTY to cooked echoing mode")

        os.write(fd, b"\x04")
        if wait_for_exit(pid, time.time() + 5) is None:
            raise AssertionError("Pi did not accept Ctrl-D after guarded Ctrl-Z without SIGCONT")
        reaped = True
    finally:
        if not reaped:
            terminate_and_reap(pid)
        os.close(fd)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: pi-suspend-guard-topology.py GUARD_MODULE EXTENSION_INDEX")
    pi = shutil.which("pi")
    if not pi:
        raise SystemExit("pi is required for topology validation")
    guard = Path(sys.argv[1]).resolve()
    index = str(Path(sys.argv[2]).resolve())
    fixture = Path(os.environ.get("TMPDIR", "/tmp")) / f"pi-suspend-guard-topology-{os.getpid()}.mjs"
    fixture.write_text(
        "import { classifyCurrentProcessGroup } from " + json.dumps(guard.as_uri()) + ";\n"
        "process.stdout.write('SUSPEND_GUARD_TOPOLOGY=' + JSON.stringify(classifyCurrentProcessGroup()) + '\\n');\n"
        "export default function () { process.exit(0); }\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    try:
        direct = run_direct(pi, str(fixture), env)
        shell = run_shell(pi, str(fixture), env)
        assert_guard_end_to_end(pi, index, env)
    finally:
        fixture.unlink(missing_ok=True)
    if direct != {"supported": True, "orphaned": True}:
        raise AssertionError(f"forkpty Pi topology wrong: {direct}")
    if shell != {"supported": True, "orphaned": False}:
        raise AssertionError(f"shell-job Pi topology wrong: {shell}")
    print("PASS: direct Pi shell-job and forkpty process groups classify non-orphan and orphan; orphan Pi TUI consumes Ctrl-Z and remains responsive")


if __name__ == "__main__":
    main()
