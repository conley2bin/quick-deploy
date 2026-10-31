# Base domain file

`vps/domain.txt` holds one line: the public base domain, for example `conley2bin.com`.
No URL, port, path, IP address, spaces or comments; a trailing LF or CRLF is fine.

When `--domain` is omitted it supplies the target hostnames for **new** work:

| Command | Default |
| --- | --- |
| `deploy`, `replace`, `preflight` | `sub.<base>` (subscription) |
| `cdn` | `cdn.<base>` |

- `--domain X` always wins, never reads or changes the file, and works if the file is missing or invalid.
- The REALITY target is separate (`--reality-target`) and is never derived from this file.
- A deployed node's saved config stays the authority. `status`, `export`, `devices`, `recover`, `backup`
  never read the file, so editing it cannot change existing links, certificates or accounts.
- Editing the file is not a migration. If `deploy`/`replace`/`preflight` would default to a domain different
  from the saved one, it stops with `current X, requested Y; migration is needed`. Pass the saved domain
  explicitly to keep working with the old deployment. A differing committed CDN domain is likewise refused.
- Changing the file does not touch DNS, certificates or servers.

Changing the file for an already-live hostname is not an automatic migration and does not bypass the refusal above;
it only sets the default for new deployments.
