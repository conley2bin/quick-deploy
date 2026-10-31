# Deployment config (`vps/config.txt`)

`vps/config.txt` is the one plain, editable file for the VPS target and the public base domain. It replaces the
former one-line `vps/domain.txt` (removed; there is no fallback to it):

```
VPS_SSH=root@136.0.5.229
DOMAIN=conley2bin.com
```

- Exactly the keys `VPS_SSH` and `DOMAIN`, each once, as ASCII `KEY=value` (no spaces around `=`, LF or CRLF).
  Blank lines and whole-line `#` comments are allowed. Nothing is sourced, expanded or evaluated.
- `VPS_SSH` is `user@public-IPv4` or a trusted SSH alias: no port, URL, command or option. It is never resolved or
  connected to while the file is read. `DOMAIN` is a base DNS domain: no URL, port, path or IP.
- The file is public. Keep keys, tokens and proxy credentials out of it. It is located next to the scripts, not in the CWD.
- Each accessor judges only its own value: `manage.sh` reads `VPS_SSH` to reach the configured VPS and ignores
  `DOMAIN`'s value (the live server is the authority for the deployed domain); both keys must still be present.

## Where it is used (setup.sh only)

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
- The REALITY target is separate (`--reality-target`) and not derived from this file.
- A deployed node's saved config remains the authority. `status`, `export`, `devices`, `recover`, `backup` do not read
  `DOMAIN`, so editing it cannot change live links, certificates or accounts.
- Editing `DOMAIN` is not a migration: a default that differs from the saved domain stops with
  `current X, requested Y; migration is needed`. Pass the saved domain explicitly to keep working with the old
  deployment. A differing committed CDN domain is likewise refused. DNS, certificates and servers are never touched.

`setup.sh` runs only deployment commands (`deploy preflight cdn status backup recover restore replace`);
`connect`, `devices`, `export`, `dashboard` live in `manage.sh`.
