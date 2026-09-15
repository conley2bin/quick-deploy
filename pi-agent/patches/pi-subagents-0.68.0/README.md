# pi-subagents 0.68.0 retained-resume startup repair

## Mechanism

A Pi process can keep the extension's parent-side sender loaded while npm replaces the package source that a new detached runner reads. The pre-`006ecf9` sender routes `ready → ack → acknowledged → proceed`; v0.68.0's runner requires `ready → ack → acknowledged → confirm → confirmed → proceed`. The mixed pair leaves the runner holding the canonical session lease and waiting for `confirm`, even though the old sender already wrote `proceed`.

This patch binds both sides to `lease-ack-confirm-proceed-v1`. The parent stamps that protocol into every revival config. The runner rejects an absent/wrong stamp before acquiring the lease. It does not infer confirmation or proceed by itself; only correctly tokened parent `ack`, `confirm`, and `proceed` controls can reach prompt execution.

## Replay on a disposable checkout

```bash
git clone https://github.com/nicobailon/pi-subagents.git /tmp/pi-subagents-original
git -C /tmp/pi-subagents-original checkout f3ccf47dc236b6c0fcc0d897cec4a9e6da3e916d
npm ci --ignore-scripts --prefix /tmp/pi-subagents-original
git -C /tmp/pi-subagents-original worktree add --detach /tmp/pi-subagents-fixed f3ccf47dc236b6c0fcc0d897cec4a9e6da3e916d
ln -s /tmp/pi-subagents-original/node_modules /tmp/pi-subagents-fixed/node_modules
./apply.sh /tmp/pi-subagents-fixed
./test/red-green.sh /tmp/pi-subagents-original /tmp/pi-subagents-fixed
```

The no-provider harness executes the real detached runner with a scripted child factory. It proves the original mixed-generation stall, aligned success, and fail-closed behavior for missing/wrong confirmation, missing proceed, signal cancellation, protocol mismatch, and competing/reclaimed session leases.

## Installed-source and activation boundary

`apply.sh` requires an explicit target. `apply.sh --check TARGET` performs guards without writing. It verifies package version, patch digest, exact pristine source hashes, and absence of the new file; an already-patched tree must match all after-hashes. Any other local modification is preserved by refusal. `package.json` and lock/version metadata are unchanged.

Do not apply this staged patch until review. After application to `~/.pi/agent/npm/node_modules/pi-subagents`, the current parent Pi must be explicitly reloaded or restarted: its process predates the installed v0.68.0 files and still owns the old sender closure. A disk patch cannot update that closure. No reload is performed by these scripts.
