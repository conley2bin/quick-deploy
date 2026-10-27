#!/usr/bin/python3
"""Narrow user-only installation. No tmux/server/job starts, stops or restarts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

SOURCE = Path(__file__).resolve().parent / "runtime"


def manager(*args):
    return subprocess.run(["systemctl", "--user", "--no-ask-password", *args], capture_output=True, text=True, timeout=30)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tmux", default=shutil.which("tmux"), help="absolute executable for new managed servers; saved only on first install")
    p.add_argument("--retire-mover", action="store_true", help="disable ONLY tmux-cgroup-enforce.timer, wait for oneshot; never stop keeper")
    p.add_argument("--jobs-only", action="store_true", help="install only job CLI and jobs.slice; no tmux config or pane policy")
    p.add_argument("--uninstall", action="store_true", help="remove owned entrypoints/config only; leave all live units and run data alone")
    p.add_argument("--no-reload", action="store_true", help="offline installer tests only; do not contact user manager")
    args = p.parse_args()
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    configdir = config / "workstation-resilience"
    settings = configdir / "tmux.json"
    manifest = configdir / "install.json"
    links = {
        home / ".local/bin/job": SOURCE / "job.py",
        home / ".local/bin/tmux-managed": SOURCE / "tmux_managed.py",
        config / "systemd/user/jobs.slice": SOURCE / "jobs.slice",
        config / "systemd/user/tmux-spawn-.scope.d/50-workstation-resilience.conf": SOURCE / "50-workstation-resilience.conf",
    }
    if args.jobs_only:
        links = {target: source for target, source in links.items() if target.name in ("job", "jobs.slice")}
    # Refuse conflicts before touching *any* managed path. No backup-and-replace
    # of foreign links, user data, or configuration from another owner.
    for target, source in links.items():
        if os.path.lexists(target) and not (target.is_symlink() and target.resolve() == source.resolve()):
            raise RuntimeError(f"foreign install path; preserved without change: {target}")
    installed = json.loads(manifest.read_text()) if manifest.exists() else None
    if installed and installed.get("source") != str(SOURCE):
        raise RuntimeError("installer manifest belongs to another checkout; preserve it and inspect migration explicitly")
    if settings.exists() and not installed and not args.jobs_only:
        raise RuntimeError(f"foreign config preserved: {settings}")
    if args.retire_mover and (args.no_reload or args.jobs_only):
        raise RuntimeError("--retire-mover requires a live full-runtime install")
    if args.uninstall:
        retained_policy = False
        for target in links:
            if target.is_symlink():
                if target.name == "jobs.slice":
                    # Keep aggregate limits through daemon-reload, including
                    # jobs already launching as entrypoints are removed. No
                    # check-then-unlink race and no implicit active-policy loss.
                    retained_policy = True
                    print("retained aggregate policy (remove only after jobs drain):", target)
                    continue
                target.unlink()
                print("removed owned link:", target)
        if not args.jobs_only:
            if installed and settings.exists() and settings.read_text() == installed["initialConfig"]:
                settings.unlink()
            elif settings.exists():
                print("preserved user-edited config:", settings)
            if manifest.exists() and not retained_policy:
                manifest.unlink()
        if retained_policy:
            print("retained policy ownership manifest:", manifest)
        print("Live managed services, pane scopes, legacy servers, keeper, and job data are untouched. Mover timer is NOT re-enabled.")
    else:
        executable = Path(args.tmux or "").resolve()
        if not args.jobs_only and (not executable.is_file() or not os.access(executable, os.X_OK)):
            raise RuntimeError("choose an installed tmux using --tmux /absolute/path")
        if not args.no_reload and not args.jobs_only:
            probe = "resilience-scope-probe-" + uuid.uuid4().hex[:12]
            tested = subprocess.run(["systemd-run", "--user", "--no-ask-password", "--scope", "--quiet",
                                     "--unit=" + probe, "--property=OOMPolicy=continue", "--", "/bin/true"],
                                    capture_output=True, text=True, timeout=15)
            if tested.returncode:
                raise RuntimeError("local transient scope OOMPolicy unsupported; no files installed: " + tested.stderr.strip())
        if args.retire_mover:
            state = manager("show", "tmux-cgroup-enforce.timer", "--property=LoadState", "--value")
            if state.stdout.strip() != "not-found":
                proc = manager("disable", "--now", "tmux-cgroup-enforce.timer")
                if proc.returncode:
                    raise RuntimeError("could not disable mover timer: " + proc.stderr.strip())
            deadline = time.monotonic() + 30
            while True:
                proc = manager("show", "tmux-cgroup-enforce.service", "--property=ActiveState", "--value")
                if proc.stdout.strip() in ("inactive", "failed"):
                    break
                if proc.returncode or time.monotonic() >= deadline:
                    raise RuntimeError("oneshot has not finished; left it and keeper untouched; inspect before new server starts")
                time.sleep(0.2)
        for target, source in links.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_symlink():
                target.symlink_to(source)
                print("linked:", target, "->", source)
        configdir.mkdir(parents=True, exist_ok=True)
        if args.jobs_only:
            installed = installed or {"source": str(SOURCE), "initialConfig": None}
        elif not settings.exists():
            initial = json.dumps({"executable": str(executable), "config_file": None}, indent=2) + "\n"
            settings.write_text(initial)
            installed = {"source": str(SOURCE), "initialConfig": initial}
        elif installed is None:
            raise RuntimeError("missing manifest for existing settings")
        manifest.write_text(json.dumps(installed, indent=2) + "\n")
        if not args.jobs_only:
            print("Configured tmux:", json.loads(settings.read_text())["executable"])
            print("Sockets: default -> $XDG_RUNTIME_DIR/tmux-managed/managed-default; pi-agent -> .../managed-pi-agent")
        print("No managed server started; existing default/pi-agent endpoints are unchanged.")
    if not args.no_reload:
        proc = manager("daemon-reload")
        if proc.returncode:
            raise RuntimeError("files changed but daemon-reload failed: " + proc.stderr.strip())
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"install-runtime: {exc}", file=sys.stderr)
        sys.exit(1)
