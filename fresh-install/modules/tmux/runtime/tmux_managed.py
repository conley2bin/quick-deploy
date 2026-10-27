#!/usr/bin/python3
"""Explicit foreground-service tmux entrypoint; never adopts an existing endpoint."""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

SCRIPT = str(Path(__file__).resolve())
PYTHON = str(Path(sys.executable).resolve())


def atomic(path, data):
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with open(tmp, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(data, stream, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def read(path):
    return json.loads(path.read_text())


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"need a user-owned mode700 directory: {path}")
    return path


def ctl(*args):
    return subprocess.run(["systemctl", "--user", "--no-ask-password", *args], capture_output=True, text=True, timeout=30)


def info(unit):
    proc = ctl("show", unit, "--property=LoadState,ActiveState,SubState,MainPID,ControlGroup,Description,Result")
    value = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    if "LoadState" not in value or (proc.returncode and value["LoadState"] != "not-found"):
        raise RuntimeError("cannot query user manager: " + proc.stderr.strip())
    return value


def live(value):
    return value["LoadState"] != "not-found" and value["ActiveState"] not in ("inactive", "failed")


def mapping(name):
    if name not in ("default", "pi-agent") and not re.fullmatch(r"test-[a-z0-9]{8,32}", name):
        raise RuntimeError("socket must be default, pi-agent, or a unique test-<8..32 lowercase alphanumeric> name")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    directory = private_dir(runtime / "tmux-managed")
    slices = {"default": "tmuxdefault.slice", "pi-agent": "tmuxagent.slice"}
    return {"logicalSocket": name, "socketPath": str(directory / ("managed-" + name)),
            "unit": "tmux-managed-" + name + ".service",
            "slice": slices.get(name, "tmuxtest" + hashlib.sha256(name.encode()).hexdigest()[:16] + ".slice"),
            "legacySocket": f"/tmp/tmux-{os.getuid()}/{name}" if name in slices else None}


def peer(path):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(1)
        client.connect(str(path))
        return struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def verified(mapping, ownership, manager, executable):
    if not live(manager) or not ownership:
        raise RuntimeError("no active owned foreground service")
    if manager["Description"] != "tmux-managed:" + ownership["nonce"]:
        raise RuntimeError("active unit is not this wrapper's recorded owner")
    pid = int(manager["MainPID"])
    if pid <= 0:
        raise RuntimeError("service has no MainPID yet")
    actual_exe = os.readlink(f"/proc/{pid}/exe")
    if actual_exe != executable or ownership["executable"] != executable:
        raise RuntimeError(f"tmux executable mismatch: active={actual_exe}, configured={executable}; no automatic restart")
    cg = Path(f"/proc/{pid}/cgroup").read_text().strip().split("::", 1)[1]
    if cg != manager["ControlGroup"] or "/" + mapping["slice"] + "/" not in cg:
        raise RuntimeError(f"service cgroup mismatch: {cg}")
    sockpath = Path(mapping["socketPath"])
    st = sockpath.lstat()
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError("endpoint is not a user-owned socket")
    pid_peer, uid_peer, _ = peer(sockpath)
    if (pid_peer, uid_peer) != (pid, os.getuid()):
        raise RuntimeError(f"socket peerPID={pid_peer} does not match service MainPID={pid}")
    return {"serverPID": pid, "peerPID": pid_peer, "cgroup": cg, "socketIdentity": [st.st_dev, st.st_ino]}


def guard_stopped():
    for unit in ("tmux-cgroup-enforce.timer", "tmux-cgroup-enforce.service"):
        value = info(unit)
        if live(value):
            raise RuntimeError(f"{unit} is active and would steal new servers. Disable ONLY its timer and let the oneshot finish; keep tmux-cgroup.service/legacy servers alive")


def ensure(mapping, ownerpath, ownership, config, lock):
    executable = config["executable"]
    manager = info(mapping["unit"])
    if live(manager):
        result = verified(mapping, ownership, manager, executable)
        if ownership.get("socketIdentity") != result["socketIdentity"]:
            # tmux may rebind after SIGUSR1. The live nonce/PID/exe/cgroup/peer
            # checks above establish ownership independently of the old inode.
            ownership["socketIdentity"] = result["socketIdentity"]
            atomic(ownerpath, ownership)
        return result
    endpoint = Path(mapping["socketPath"])
    if endpoint.exists() or endpoint.is_symlink():
        raise RuntimeError(f"endpoint already exists: {endpoint}; refusing to unlink/adopt it (inspect status; clear-stale is explicit and only for a recorded owned inode)")
    guard_stopped()
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError("configured tmux executable is not executable")
    conf = config.get("config_file")
    if conf is not None and conf != "/dev/null" and not Path(conf).is_file():
        raise RuntimeError("configured tmux config_file does not exist")
    envpath = ownerpath.with_suffix(".environment.json")
    environment = dict(os.environ)
    for key in ("TMUX", "TMUX_PANE", "INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID"):
        environment.pop(key, None)
    atomic(envpath, environment)
    ownership = {**mapping, "nonce": uuid.uuid4().hex, "executable": executable, "config_file": conf,
                 "environmentFile": str(envpath)}
    atomic(ownerpath, ownership)
    test_lifetime = ["--property=RuntimeMaxSec=90s"] if mapping["logicalSocket"].startswith("test-") else []
    proc = subprocess.run(["systemd-run", "--user", "--no-ask-password", "--quiet", "--collect", "--service-type=exec",
                           "--expand-environment=no", "--unit=" + mapping["unit"], "--slice=" + mapping["slice"],
                           "--description=tmux-managed:" + ownership["nonce"], "--property=Restart=no",
                           "--property=KillMode=control-group", "--property=StandardInput=null", *test_lifetime,
                           "--", PYTHON, SCRIPT, "_server", str(ownerpath)],
                          capture_output=True, text=True, timeout=30, pass_fds=(lock.fileno(),))
    if proc.returncode:
        envpath.unlink(missing_ok=True)
        raise RuntimeError("foreground service start failed (no fallback): " + proc.stderr.strip())
    end = time.monotonic() + 10
    error = "socket not ready"
    while time.monotonic() < end:
        manager = info(mapping["unit"])
        if not live(manager):
            envpath.unlink(missing_ok=True)
            raise RuntimeError("foreground service exited during startup; inspect journal for " + mapping["unit"])
        try:
            result = verified(mapping, ownership, manager, executable)
            ownership["socketIdentity"] = result["socketIdentity"]
            atomic(ownerpath, ownership)
            return result
        except (OSError, RuntimeError) as exc:
            error = str(exc)
        time.sleep(0.05)
    raise RuntimeError("foreground service not ready (left for inspection, no fallback): " + error)


def server(ownerpath):
    ownership = read(ownerpath)
    envpath = Path(ownership["environmentFile"])
    try:
        env = read(envpath)
    finally:
        envpath.unlink(missing_ok=True)
    args = [ownership["executable"], "-D", "-S", ownership["socketPath"]]
    if ownership["config_file"] is not None:
        args += ["-f", ownership["config_file"]]
    # -D disables exit-empty, so a no-session server is a legitimate ready
    # endpoint. Do not create an anchor shell as a readiness surrogate.
    os.chdir(env.get("HOME", str(Path.home())))
    os.execve(args[0], args, env)


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == "_server":
        return server(Path(sys.argv[2]))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--socket", default="default")
    p.add_argument("--config", default=str(Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "workstation-resilience/tmux.json"))
    p.add_argument("command", nargs=argparse.REMAINDER, help="status | start | clear-stale | -- TMUX_COMMAND ARGS...")
    args = p.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or command[0].startswith("-"):
        p.error("supply status, start, clear-stale, or -- followed by a tmux command; global tmux flags are unsupported")
    action = command[0]
    if action in ("status", "start", "clear-stale") and len(command) != 1:
        p.error("wrapper lifecycle commands accept no extra arguments")
    config = read(Path(args.config))
    config["executable"] = str(Path(config["executable"]).resolve())
    m = mapping(args.socket)
    endpoint = Path(m["socketPath"])
    ownerpath = endpoint.with_name(endpoint.name + ".owner.json")
    # tmux itself flocks <socket>.lock: never contend with its inner lock.
    with open(endpoint.with_name(endpoint.name + ".startup.lock"), "a", opener=lambda path, flags: os.open(path, flags, 0o600)) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ownership = read(ownerpath) if ownerpath.exists() else None
        manager = info(m["unit"])
        if action == "status":
            output = {**m, "executable": config["executable"], "manager": manager, "state": "inactive"}
            try:
                if live(manager):
                    result = verified(m, ownership, manager, config["executable"])
                    if ownership.get("socketIdentity") != result["socketIdentity"]:
                        ownership["socketIdentity"] = result["socketIdentity"]
                        atomic(ownerpath, ownership)
                    output.update(result)
                    output["state"] = "ready"
                elif endpoint.exists() or endpoint.is_symlink():
                    output["state"] = "existing-endpoint-refused"
            except (RuntimeError, OSError) as exc:
                output.update(state="ownership-refused", error=str(exc))
            print(json.dumps(output, ensure_ascii=True))
            return 0
        if action == "clear-stale":
            if live(manager) or not ownership or not ownership.get("socketIdentity"):
                raise RuntimeError("refusing cleanup: active unit or no recorded owned socket identity")
            st = endpoint.lstat()
            if not stat.S_ISSOCK(st.st_mode) or ownership["socketIdentity"] != [st.st_dev, st.st_ino]:
                raise RuntimeError("refusing cleanup: inode is not the recorded owned socket")
            try:
                peer(endpoint)
            except OSError as exc:
                if exc.errno != errno.ECONNREFUSED:
                    raise
            else:
                raise RuntimeError("refusing cleanup: endpoint has a live listener")
            endpoint.unlink()
            print("removed recorded stale socket only: " + str(endpoint))
            return 0
        result = ensure(m, ownerpath, ownership, config, lock)
        if action == "start":
            print(json.dumps({**m, **result, "state": "ready"}, ensure_ascii=True))
            return 0
        # Never pass global -S/-L/-f flags; command arguments start at argv[4].
        # -N prevents a dead/missing endpoint from causing an unmanaged server.
        client = [config["executable"], "-N", "-S", m["socketPath"], *command]
    os.execv(client[0], client)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"tmux-managed: {error}", file=sys.stderr)
        sys.exit(70)
