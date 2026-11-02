# Deployment config (`vps/config`)

`vps/config` is the one plain, editable file for the VPS target and the public base domain. It replaces the
former one-line `vps/domain.txt` (removed; there is no fallback to it):

```
VPS_SSH=root@136.0.5.229
DOMAIN=conley2bin.com
REALITY_TARGET=www.apple.com
```

Then run `bash vps/setup.sh` with no command (see below).

- The keys `VPS_SSH` and `DOMAIN` (required) and `REALITY_TARGET` (optional), each at most once, as ASCII `KEY=value` (no spaces around `=`, LF or CRLF).
  Blank lines and whole-line `#` comments are allowed. Nothing is sourced, expanded or evaluated.
- `VPS_SSH` is `user@public-IPv4` or a trusted SSH alias: no port, URL, command or option. It is never resolved or
  connected to while the file is read. `DOMAIN` is a base DNS domain: no URL, port, path or IP.
- The file is public. Keep keys, tokens and proxy credentials out of it. It is located next to the scripts, not in the CWD.
- Each accessor judges only its own value: `manage.sh` reads `VPS_SSH` to reach the configured VPS and ignores
  `DOMAIN`'s value (the live server is the authority for the deployed domain); both keys must still be present.

- `REALITY_TARGET` is a TLS1.3+h2 camouflage hostname you have verified. It is needed only to **install a fresh
  machine**; it is never derived from `DOMAIN`. On an already deployed node the server's saved target is the authority:
  a configured value that differs from it refuses before any change (nothing is retargeted). `manage.sh` and the other
  accessors ignore its value, so an unused or invalid target never blocks management.

## Bare `setup.sh` (default)

`bash vps/setup.sh` reads this file and converges the configured VPS; you do not pick a subcommand. It first reads the
server over SSH, read-only (the configured target is compared with the saved endpoint before anything else), then does exactly one of:

- **healthy and local cache current**: prints SKIP; the server is not prepared, backed up, restarted or re-keyed.
- **healthy, local snapshot absent or stale**: read-only refresh/attach of the local snapshot (no server change).
- **front drift, or an older single-transport node**: the existing guarded transaction repairs it, preserving accounts.
- **nothing deployed (fresh machine)**: the existing full deployment (needs `REALITY_TARGET`).
- **pending or busy**: a local pending transaction is recovered by the original recovery; a remote one is only reported
  (exit 75, retry later). Nothing is replayed or rolled back on your behalf.
- **foreign owner/machine/node/config, or a broken runtime**: refuses with the concrete reason (exit 1). So does a server
  node that is unpublished with no transaction record: there is nothing to wait for, inspect the original deployment/backup.

It also refuses while a foreground `manage.sh` holds the same state directory. CDN is optional and is not added
automatically; the run prints the `cdn` command to use. The explicit verbs below remain for advanced maintenance
and cold recovery.

One advanced exception: `bash vps/setup.sh --artifacts-dir DIR` passes a directory holding the original SHA-pinned release
files (verified exactly as for `deploy --artifacts-dir`) when a pinned download is unavailable. No mirror or version fallback.

## Where it is used (explicit verbs)

| Command | When the option is omitted |
| --- | --- |
| `deploy`, `preflight`, `replace` | `--ssh` = `VPS_SSH`, always (a saved node does not take over the omitted target) |
| `deploy`, `replace`, `preflight` | `--domain` = `sub.<DOMAIN>` |
| `cdn` | `--domain` = `cdn.<DOMAIN>` |
| `restore` | never defaulted: `--ssh` is always explicit (a fresh host is dangerous) |

- `--ssh` and `--domain` override independently, bypass the file (it may be missing or invalid) and never change it.
- For `deploy` and `preflight`, if the omitted-`--ssh` default resolves to a different endpoint than the deployed node, the command refuses before any SSH
  (fix `VPS_SSH`, or use another `--state-dir` for a new VPS); it never silently retargets the old node. `replace` is the
  command designed to move to a new node, so it is not subject to this refusal.
- `--reality-target` overrides `REALITY_TARGET` for explicit `deploy`/`preflight`.
- A deployed node's saved config remains the authority. `status`, `export`, `devices`, `recover`, `backup` do not read
  `DOMAIN`, so editing it cannot change live links, certificates or accounts.
- Editing `DOMAIN` is not a migration: a default that differs from the saved domain stops with
  `current X, requested Y; migration is needed`. Pass the saved domain explicitly to keep working with the old
  deployment. A differing committed CDN domain is likewise refused. DNS, certificates and servers are never touched.

`setup.sh` runs only deployment commands (`deploy preflight cdn status backup recover restore replace`);
`connect`, `devices`, `export`, `dashboard` live in `manage.sh`.
