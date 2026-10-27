#!/usr/bin/python3
"""Non-PTY, non-restarting user service jobs. Python standard library only."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

CAPACITY = 20 * 1024**3
DEFAULT_MAX = 8 * 1024**3
MIN_MAX = 32 * 1024**2
DEFAULT_SWAP = 1024**3
SCRIPT = str(Path(__file__).resolve())
PYTHON = str(Path(sys.executable).resolve())


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with open(tmp, "x", opener=lambda p, f: os.open(p, f, 0o600)) as out:
        json.dump(value, out, ensure_ascii=True, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(path)
    fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    return json.loads(Path(path).read_text())


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = path.lstat()
    if path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise RuntimeError(f"directory must be owned by this user and mode700: {path}")
    return path


def state_root():
    return private_dir(Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "workstation-jobs")


def amount(text):
    match = re.fullmatch(r"([0-9]+)([KMGT]?)(?:i?B)?", text, re.I)
    if not match:
        raise argparse.ArgumentTypeError("use integer bytes or K/M/G/T (binary units)")
    return int(match[1]) * 1024 ** ("KMGT".find(match[2].upper()) + 1 if match[2] else 0)


def control(*args):
    return subprocess.run(["systemctl", "--user", "--no-ask-password", *args], text=True,
                          capture_output=True, timeout=30)


def unit_info(unit):
    proc = control("show", unit, "--property=LoadState,ActiveState,SubState,MainPID,InvocationID,ControlGroup,Result")
    props = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    if "LoadState" not in props or (proc.returncode and props.get("LoadState") != "not-found"):
        raise RuntimeError(f"cannot query manager (reservation retained): {proc.stderr.strip()}")
    return props


def is_live(info):
    return info.get("LoadState") != "not-found" and info.get("ActiveState") not in ("inactive", "failed")


def admission_root():
    # One lock/ledger per local UID, even if callers choose different XDG state
    # homes for durable logs. Changing a command's environment cannot partition
    # the shared admission budget.
    return private_dir(Path(f"/run/user/{os.getuid()}/workstation-jobs"))


def locked(root):
    stream = open(admission_root() / "admission.lock", "a", opener=lambda p, f: os.open(p, f, 0o600))
    fcntl.flock(stream, fcntl.LOCK_EX)
    return stream


def run_dir(root, ident):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}-[0-9a-f]{16}", ident):
        raise RuntimeError("invalid run ID (use the exact ID printed by job run)")
    directory = root / ident
    if not (directory / "run.json").is_file():
        raise RuntimeError(f"unknown run: {ident}")
    return directory


def reservation_total(root):
    total = 0
    for path in admission_root().glob("*.reservation.json"):
        spec = read(path)
        info = unit_info(spec["unit"])
        # A submitted run whose unit vanished has no live reservation. Pending
        # submissions without a result fail closed until explicit job cancel.
        if is_live(info) or (spec["submission"] == "pending" and not (Path(spec["directory"]) / "result.json").exists()):
            total += spec["memoryMax"]
        else:
            path.unlink()
    return total


def versions(cwd, inputs):
    files = []
    for name in inputs:
        path = Path(name)
        path = (Path(cwd) / path).absolute() if not path.is_absolute() else path
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        files.append({"path": str(path), "sha256": digest.hexdigest(), "bytes": path.stat().st_size})
    git = None
    if shutil.which("git"):
        rev = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if rev.returncode == 0:
            dirty = subprocess.run(["git", "-C", cwd, "status", "--porcelain", "--untracked-files=no"],
                                   capture_output=True, text=True, timeout=10)
            git = {"commit": rev.stdout.strip(), "trackedDirty": bool(dirty.stdout)}
    return {"git": git, "inputs": files}


def systemd_word(value):
    # D-Bus transient command properties are not unit-file specifiers. The
    # command's ':' prefix disables environment expansion; only quote words.
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def local_result(directory, result, code, **extra):
    atomic(directory / "result.json", {"finishedAt": now(), "serviceResult": result, "exitCode": code, **extra})


def submit(args, root):
    if not args.name or len(args.name) > 128 or "\x00" in args.name:
        raise RuntimeError("name must contain 1..128 characters")
    high = args.high if args.high is not None else args.mem * 3 // 4
    if not MIN_MAX <= args.mem <= CAPACITY or not 0 < high <= args.mem or args.swap < 0:
        raise RuntimeError("require 32M <= mem <=20G, 0 < high <= mem and swap >=0")
    argv = args.command
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if not argv:
        raise RuntimeError("missing command after --")
    cwd = str(Path(args.cwd or os.getcwd()).resolve(strict=True))
    if not Path(cwd).is_dir():
        raise RuntimeError("cwd is not a directory")
    version = versions(cwd, args.input)
    label = re.sub(r"[^a-z0-9]+", "-", args.name.lower()).strip("-")[:32] or "job"
    ident = label + "-" + uuid.uuid4().hex[:16]
    directory = root / ident
    envfile = None
    with locked(root) as lock:
        used = reservation_total(root)
        if used + args.mem > CAPACITY:
            print(f"admission refused: reserved={used} requested={args.mem} capacity={CAPACITY}; wait for a job, then submit explicitly", file=sys.stderr)
            return 75
        private_dir(directory)
        runtime = admission_root()
        envfile = runtime / (ident + ".environment.json")
        # Whole caller environment is needed by venvs, credentials, toolchains.
        # It is private, ephemeral, and removed before executing the command.
        environment = dict(os.environ)
        for key in ("TMUX", "TMUX_PANE", "INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID", "SERVICE_RESULT", "EXIT_CODE", "EXIT_STATUS"):
            environment.pop(key, None)
        atomic(envfile, environment)
        unit = "workstation-job-" + ident + ".service"
        spec = {"id": ident, "name": args.name, "unit": unit, "submittedAt": now(), "submission": "pending",
                "cwd": cwd, "argv": argv, "memoryHigh": high, "memoryMax": args.mem, "memorySwapMax": args.swap,
                "versions": version, "environmentFile": str(envfile)}
        atomic(directory / "run.json", spec)
        reservation = runtime / (ident + ".reservation.json")
        reservation_spec = {"unit": unit, "memoryMax": args.mem, "submission": "pending", "directory": str(directory)}
        atomic(reservation, reservation_spec)
        (directory / "output.log").touch(mode=0o600)
        # A source update/entrypoint rollback must not change an in-flight
        # finalizer. This self-contained helper snapshot belongs to the run.
        runner = directory / "runner.py"
        shutil.copyfile(SCRIPT, runner)
        runner.chmod(0o600)
        finish = ":" + " ".join(systemd_word(s) for s in (PYTHON, str(runner), "_finish", str(directory)))
        cmd = ["systemd-run", "--user", "--no-ask-password", "--quiet", "--collect", "--service-type=exec",
               "--expand-environment=no", "--unit=" + unit, "--slice=jobs.slice",
               "--property=Restart=no", "--property=OOMPolicy=kill", "--property=KillMode=control-group",
               "--property=TimeoutStopSec=30s", "--property=StandardInput=null",
               "--property=MemoryHigh=" + str(high), "--property=MemoryMax=" + str(args.mem),
               "--property=MemorySwapMax=" + str(args.swap),
               "--property=StandardOutput=append:" + str(directory / "output.log"),
               "--property=StandardError=inherit", "--property=ExecStopPost=" + finish,
               "--", PYTHON, str(runner), "_exec", str(directory)]
        try:
            # The lock fd survives a killed submitter until systemd-run exits.
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=40, pass_fds=(lock.fileno(),))
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Timeout is ambiguous: never free a reservation or retry the work.
            spec["submissionError"] = str(exc)
            atomic(directory / "run.json", spec)
            print(f"{ident}\nsubmission uncertain; inspect status/cancel explicitly", file=sys.stderr)
            return 70
        if proc.returncode:
            info = unit_info(unit)
            if info["LoadState"] != "not-found" or (directory / "result.json").exists():
                # A failed acknowledgement cannot prove rejection of an
                # enqueued, running, or already completed command. Do not
                # invite a duplicate run; pending admission fails closed.
                spec["submissionError"] = proc.stderr.strip() or f"systemd-run exited {proc.returncode}"
                atomic(directory / "run.json", spec)
                print(f"{ident}\nsubmission uncertain; inspect status/cancel explicitly: {spec['submissionError']}", file=sys.stderr)
                return 70
            spec["submission"] = "rejected"
            atomic(directory / "run.json", spec)
            reservation_spec["submission"] = "rejected"
            atomic(reservation, reservation_spec)
            local_result(directory, "start-failed", 125, error=proc.stderr.strip())
            envfile.unlink(missing_ok=True)
            print(f"{ident}\nservice start failed: {proc.stderr.strip()}", file=sys.stderr)
            return 125
        spec["submission"] = "accepted"
        atomic(directory / "run.json", spec)
        reservation_spec["submission"] = "accepted"
        atomic(reservation, reservation_spec)
    print(ident, flush=True)
    return 0


def execute(directory):
    spec = read(directory / "run.json")
    envpath = Path(spec["environmentFile"])
    try:
        env = read(envpath)
    finally:
        envpath.unlink(missing_ok=True)
    # Keep the manager invocation marker, not a submitting pane's stale marker.
    if "INVOCATION_ID" in os.environ:
        env["INVOCATION_ID"] = os.environ["INVOCATION_ID"]
    try:
        os.chdir(spec["cwd"])
        atomic(directory / "start.json", {"startedAt": now(), "pid": os.getpid(),
                                          "invocationId": os.environ.get("INVOCATION_ID"),
                                          "cgroup": Path("/proc/self/cgroup").read_text().strip()})
        os.execvpe(spec["argv"][0], spec["argv"], env)
    except (OSError, ValueError) as exc:
        atomic(directory / "exec-error.json", {"error": str(exc)})
        print(f"job exec failed: {exc}", file=sys.stderr, flush=True)
        return 127


def finish(directory):
    spec = read(directory / "run.json")
    Path(spec["environmentFile"]).unlink(missing_ok=True)
    result = os.environ.get("SERVICE_RESULT", "unknown")
    kind, value = os.environ.get("EXIT_CODE"), os.environ.get("EXIT_STATUS")
    if kind == "exited" and value and value.isdigit():
        code = int(value)
    elif kind in ("killed", "dumped") and value:
        code = 128 + int(getattr(signal, "SIG" + value, 0))
    else:
        code = 0 if result == "success" else 125
    extra = {"managerExitCode": kind, "managerExitStatus": value, "invocationId": os.environ.get("INVOCATION_ID")}
    if (directory / "exec-error.json").exists():
        extra["execError"] = read(directory / "exec-error.json")["error"]
    try:
        cg = Path("/sys/fs/cgroup" + Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1])
        extra["memoryPeakBytes"] = int((cg / "memory.peak").read_text())
        extra["memoryEvents"] = dict(line.split() for line in (cg / "memory.events").read_text().splitlines())
    except (OSError, ValueError):
        pass
    local_result(directory, result, code, **extra)
    return 0


def status(directory):
    spec = read(directory / "run.json")
    info = unit_info(spec["unit"])
    resultpath = directory / "result.json"
    result = read(resultpath) if resultpath.exists() else None
    if result:
        state = "finished" if not is_live(info) else "finalizing"
    elif is_live(info):
        state = "running" if (directory / "start.json").exists() else "starting"
    else:
        state = "unknown"  # manager restart/collection must never imply success
    return {"id": spec["id"], "state": state, "unit": spec["unit"], "directory": str(directory),
            "memoryMax": spec["memoryMax"], "manager": info, "result": result}


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] in ("_exec", "_finish"):
        return (execute if sys.argv[1] == "_exec" else finish)(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="submit once; no PTY, queue, retries, or restart")
    run.add_argument("name")
    run.add_argument("--mem", type=amount, default=DEFAULT_MAX)
    run.add_argument("--high", type=amount)
    run.add_argument("--swap", type=amount, default=DEFAULT_SWAP)
    run.add_argument("--cwd")
    run.add_argument("--input", action="append", default=[], help="file to SHA256 before submission; repeat as needed")
    # Split at -- ourselves: argparse REMAINDER after name swallows options.
    statusp = sub.add_parser("status")
    statusp.add_argument("id", nargs="?")
    for command in ("log", "wait", "cancel"):
        p = sub.add_parser(command)
        p.add_argument("id")
        if command == "wait":
            p.add_argument("--timeout", type=float, default=86400)
    argv = sys.argv[1:]
    command = []
    if argv[:1] == ["run"]:
        if "--" not in argv:
            parser.error("job run requires -- before the command")
        split = argv.index("--")
        argv, command = argv[:split], argv[split + 1:]
    args = parser.parse_args(argv)
    root = state_root()
    if args.action == "run":
        args.command = command
        return submit(args, root)
    if args.action == "status":
        directories = [run_dir(root, args.id)] if args.id else sorted(p.parent for p in root.glob("*/run.json"))
        for directory in directories:
            print(json.dumps(status(directory), ensure_ascii=True))
        return 0
    directory = run_dir(root, args.id)
    if args.action == "log":
        with (directory / "output.log").open("rb") as stream:
            shutil.copyfileobj(stream, sys.stdout.buffer)
        return 0
    if args.action == "cancel":
        with locked(root):
            info = unit_info(read(directory / "run.json")["unit"])
            if is_live(info):
                proc = control("stop", read(directory / "run.json")["unit"])
                if proc.returncode:
                    raise RuntimeError(proc.stderr.strip())
            if not (directory / "result.json").exists():
                local_result(directory, "cancelled-or-lost", 125)
                Path(read(directory / "run.json")["environmentFile"]).unlink(missing_ok=True)
            (admission_root() / (args.id + ".reservation.json")).unlink(missing_ok=True)
        return 0
    deadline = time.monotonic() + max(0, args.timeout)
    while True:
        value = status(directory)
        if value["state"] == "finished":
            print(json.dumps(value["result"], ensure_ascii=True))
            return value["result"]["exitCode"]
        if value["state"] == "unknown":
            print("result missing; work is not retried. Inspect unit/journal; cancel to release an interrupted reservation.", file=sys.stderr)
            return 125
        if time.monotonic() >= deadline:
            print("wait timed out; job left running", file=sys.stderr)
            return 124
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"job: {error}", file=sys.stderr)
        sys.exit(70)
