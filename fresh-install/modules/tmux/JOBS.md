# Bounded service jobs

`job` submits a command once as a non-PTY user transient service, independent of
its launching terminal. It never restarts failed work. The runtime uses Python's
standard library, `systemd-run`, and the local user manager (Ubuntu24.04+).

```bash
job run probe-entry --cwd /path/to/project --input probe.py --input model.stl -- /path/to/venv/bin/python probe.py
# stdout: probe-entry-<unique16hex>
job status RUN_ID
job log RUN_ID
job wait RUN_ID --timeout 3600
job cancel RUN_ID
```

Options precede `--`; command arguments follow it verbatim, without a shell.
Names may contain Unicode/spaces; the returned ASCII run ID is the selector.
Use `-- /bin/sh -c '...'` only when shell evaluation is intentional. Stdin is
`/dev/null`; interactive programs belong in managed tmux instead.

## Budget and admission

| boundary | MemoryHigh | MemoryMax | MemorySwapMax |
|---|---:|---:|---:|
| default job |6GiB|8GiB|1GiB|
| jobs.slice |20GiB|24GiB|2GiB|

`--mem`, `--high`, `--swap` accept integer binary K/M/G/T units. Omitted
`--high` is75% of declared Max:8GiB yields6GiB;16GiB yields12GiB. `--mem`
must be at least32MiB so the Python launcher/finalizer can run. Declared Max reservations
must fit20GiB: two default jobs fit; a third is refused with exit75. There is no
implicit queue or retry. Even callers with different XDG state homes share the
same perUID runtime lock/ledger. Admission includes starting and finalizing
services, not just their current RSS. Commands outside this entrypoint are not
part of this reservation budget. The slice hard cap can select any job as an OOM
victim; it does not promise to kill the newest submission.

## Durable identity and outcomes

`$XDG_STATE_HOME/workstation-jobs/RUN_ID/` (default `~/.local/state/...`) contains:

- `run.json`: exact argv/cwd, resource declarations, submission state, Git HEAD
  and tracked-dirty flag when applicable, optional streamed SHA256 of `--input`
  files. Include the script and relevant input files explicitly for reproducibility;
  Git HEAD alone does not snapshot dirty code or environment/package versions.
- `start.json`: mainPID, invocation identity and actual cgroup before exec.
- `output.log`: combined stdout/stderr, independent of journal retention.
- `result.json`: final manager reason, exit/signal status, invocation identity,
  memory peak/events when available; survives transient-unit collection.
- `runner.py`: private snapshot of the manager helper, keeping an in-flight
  run's launch/finalizer code stable across source updates and entrypoint removal.

The entire caller environment is transferred through a mode600 ephemeral file
under `/run/user/UID/workstation-jobs`, removed before command exec. Inherited
`TMUX`/`TMUX_PANE` and stale systemd invocation markers are discarded, so a detached
command does not impersonate its submitting pane. Other required environment is
preserved. It is never dumped into durable metadata or systemd Environment properties. Directories are
mode700 and records600. **Argv and command output are recorded**: pass credentials
via environment/credential files, not command arguments, and do not print them.

ExecStopPost records failures after a cgroup OOM; OOMPolicy=kill cleans the job's
process tree. Exit0, nonzero, signals and failed exec are distinct. systemd regards
some termination signals as clean (`serviceResult=success`); the separate numeric
`exitCode`/manager signal fields remain authoritative (`wait` returns143 for TERM).
Definite launch rejection returns125; capacity refusal75. A nonzero launch
acknowledgement while its unit exists/queues or a result already exists returns70
(`submission uncertain`), retaining pending identity rather than inviting a duplicate
run. Inspect that run's status/result; never automatically resubmit it. A successful
submission means service ownership was accepted, not that computation succeeded.

If submission was interrupted or the manager/result writer vanished, status is
`unknown`, never inferred success. A pending uncertain reservation is held until
explicit `job cancel RUN_ID`: under the same admission lock it stops only that
unique run's unit, records `cancelled-or-lost` if necessary, and releases its
reservation. It never repeats the command. `wait` timeout124 leaves work running.
The finalizer has a30s stop timeout. Residual charged shmem/tmpfs pages can still
leave it insufficient headroom, or severe I/O pressure can prevent its result write;
that outcome remains explicitly `unknown`. Jobs do not survive reboot/user-manager
exit, but completed records do.

## Checks

```bash
python3 tests/test_job.py
python3 tests/test_review.py
# Live opt-in suite; run under tmux/pi-notify or an established job runner:
python3 tests/test_job_live.py
```

The live suite uses unique units/private records. Only the OOM fixture allocates
stress memory (64MiB cap, swap0); its independent64MiB sentinel survives. Admission
fixtures declare8GiB but are tiny bounded sleepers with RLIMIT_AS64MiB. Cleanup
stops exact owned IDs only. It does not stress desktop/UID memory or touch legacy
tmux servers.
