# Managed tmux and bounded jobs

New noninteractive computation uses [`job`](JOBS.md). Interactive work uses
`tmux-managed`. These are explicit entrypoints, not a global tmux interceptor.
Existing legacy sessions retain their original sockets and ownership.

## Narrow install (no root, apt, Pi reload, or existing-server restart)

From an inspected checkout:

```bash
bash fresh-install/modules/tmux/install.sh --runtime-only --tmux /usr/bin/tmux
# On the approved migration host with the old PID-mover timer:
bash fresh-install/modules/tmux/install.sh --runtime-only --retire-mover
# Job-only bootstrap, without tmux config/pane policy:
bash fresh-install/modules/tmux/install.sh --runtime-only --jobs-only
```

This installs only these owned links plus a small installation manifest/config:

| user path | source |
|---|---|
| `~/.local/bin/job` | `runtime/job.py` |
| `~/.local/bin/tmux-managed` | `runtime/tmux_managed.py` |
| `~/.config/systemd/user/jobs.slice` | `runtime/jobs.slice` |
| `~/.config/systemd/user/tmux-spawn-.scope.d/50-workstation-resilience.conf` | directed `[Scope] OOMPolicy=continue` |

`~/.config/workstation-resilience/tmux.json` pins an absolute executable and an
optional `config_file` (null means normal user tmux config). First install uses
`--tmux`/current executable resolution. Reinstall preserves the existing config;
edit it deliberately to change executable/config for **future** servers. A live
server executable mismatch refuses, never restarts it. Runtime code remains linked
to the inspected checkout. No PATH or TMUX mutation is installed.

Foreign files/links/config cause refusal before installation. Exact owned links
are idempotent. The installer tests local transient-scope OOMPolicy support before
installing the pane drop-in, then daemon-reloads without restarting existing units.
`--retire-mover` disables **only** `tmux-cgroup-enforce.timer` and waits at most30s
for its in-flight oneshot to finish. It never stops the old keeper
`tmux-cgroup.service`, enforcement oneshot, old servers, or panes. Without this
explicit retirement, managed startup refuses while a mover is active.

## Select new versus legacy work

```bash
~/.local/bin/tmux-managed --socket pi-agent status
~/.local/bin/tmux-managed --socket pi-agent start
~/.local/bin/tmux-managed --socket pi-agent -- new-session -d -s my-project -c "$PWD"
~/.local/bin/tmux-managed --socket pi-agent -- list-panes -a
~/.local/bin/tmux-managed --socket pi-agent -- attach-session -t my-project
```

| logical selector | physical endpoint | service / user slice |
|---|---|---|
| `default` | `$XDG_RUNTIME_DIR/tmux-managed/managed-default` | `tmux-managed-default.service` / `tmuxdefault.slice` |
| `pi-agent` | `$XDG_RUNTIME_DIR/tmux-managed/managed-pi-agent` | `tmux-managed-pi-agent.service` / `tmuxagent.slice` |

Usually XDG_RUNTIME_DIR is `/run/user/UID`. `status` prints the actual mapping,
MainPID, socket peerPID, executable and cgroup. Physical managed names are **not**
`-L` names in `/tmp/tmux-UID`. For native diagnostics use `tmux -N -S <printed-path>`.
Old sessions remain explicitly addressable with `tmux -N -L default ...` or
`tmux -N -L pi-agent ...`; they have not migrated. Do not point those old commands
at new work just because they share a logical label.

Every managed socket owns a foreground `tmux -D` service. A distinct startup
flock serializes cold callers without colliding with tmux's own `<socket>.lock`.
A zero-session server is ready when its actual Unix peerPID equals service MainPID,
its executable matches, and /proc cgroup matches the intended slice/unit. All
clients use `-N`: loss of service fails rather than silently creating a fallback.
There is no socket activation or automatic restart. First cold caller's environment
is transferred privately then deleted; tmux's normal environment/session behavior
applies afterward. Credentials are not copied into durable ownership metadata.

Global tmux client flags (`-L`, `-S`, `-f`, etc.) are unsupported through forwarding;
choose the wrapper selector/config instead. Ordinary tmux command args follow `--`.
Existing/unowned endpoints are never adopted or removed. For an **explicitly
inspected, recorded owned, inactive** leftover inode only:

```bash
tmux-managed --socket pi-agent clear-stale
```

This refuses any active unit, missing ownership record, changed inode, or live
listener. It never cleans a foreign stale socket. A live owned tmux server may
rotate its socket inode after its standard SIGUSR1 recovery. Once nonce, MainPID,
executable, cgroup and peerPID all match, `status`/`start`/client launch records the
new owned inode under the startup lock without stopping sessions. This does not
relax stale-file cleanup checks or authorize signalling production servers. Test selectors
`test-<8..32 lowercase alphanumeric>` get private endpoints/slices and90s maximum
service lifetime; production selectors have no imposed runtime/memory cap.

## What the isolation changes

On the supported Ubuntu tmux build, server MainPID lives in its socket service;
ordinary pane `tmux-spawn-*.scope` units become siblings in the same user slice.
The pane handoff is asynchronous to the client reply: an immediate /proc read may
briefly show the service. The targeted scope policy avoids systemd's secondary
shell cleanup after a child OOM. It does not protect a shell from direct kernel
selection, and does not constrain an interactive pane's workload memory.

Only submitted jobs get the explicit single/aggregate memory budgets. Interactive
slices, desktop and UID receive no new hard caps, oomd tuning, or server immunity.
Bare/absolute tmux, GUI launchers, upstream ShellGate cowork and arbitrary direct
commands can bypass these entrypoints. User-manager exit/reboot/global OOM remain
failure boundaries. No existing session migration or all-GUI coverage is claimed.

## Non-destructive rollback and tests

```bash
bash fresh-install/modules/tmux/install.sh --runtime-only --uninstall
```

Rollback removes exact owned entrypoint/pane-policy links and unchanged initial
config, preserving edited config and all durable run data. It explicitly **retains
`jobs.slice` and its ownership manifest**, printing their paths, so daemon-reload
cannot drop aggregate policy under active or in-flight jobs. Retention is deliberate
even when the slice appears idle, avoiding a check-then-launch race. Remove retained
policy manually only after jobs drain and submitters are quiescent, or reinstall
normally to restore entrypoints. Rollback does not stop/restart any service,
server, pane or job, and does not re-enable the retired mover. In-flight job helpers
are snapshotted inside each run; their final result survives entrypoint removal.
Reinstall restores future routing without replacing an active server. Keep run
data and the running tmux binary until work drains; removal is not migration.

Fast checks: `python3 tests/test_job.py`, `python3 tests/test_review.py`,
`python3 tests/test_installer.py`.
Opt-in live suites: `tests/test_job_live.py`, `tests/test_tmux_live.py`,
`tests/test_rollback_live.py`, `tests/test_aggregate_live.py`. Run long tests via tmux/pi-notify or an established
job runner. Full job admission tests require enough free declared capacity for
two8GiB tiny sleepers; do not run concurrently with real reserved heavy jobs.
Private scope OOM uses64MiB/swap0; cleanup targets exact unique IDs, never legacy
or production managed servers. The live rollback suite removes/reinstalls an
isolated HOME's actual links while retaining aggregate policy and its private
job/server continue. The scaled aggregate fixture caps a private workload slice
at64MiB, with two48MiB services and an independent16MiB control. Parent-local
memory.events distinguish its aggregate limit from a child cap; no particular
victim or single-victim outcome is promised.
