# Persistent local management

Install once, then open the private `vps/index.html` whenever you want to manage devices. No terminal needs to stay open.

## What runs
A per-user systemd unit, `quick-deploy-vps-manage.service` (`~/.config/systemd/user/`), runs the loopback manager on a fixed port (default 47831, stored in `state/manage-service/config.json`). `Type=notify`, `Restart=always`. No root, no sudo, no browser on login, and no linger change. Without linger the service runs from login until logout; `manage.sh status` shows the current linger value, and enabling it is your choice.

Secrets live outside Git in `<state-dir>/manage-service/` (0700): `master.key` (0600, created once) and `config.json`. The generated `vps/index.html` (0600, git-ignored) embeds only a key derived from the master; the master never appears in argv, logs, or HTTP.

## Commands (`manage.sh [--state-dir DIR] [command]`)
| command | effect |
|---|---|
| *(none)* | install/start the service, then ask the system opener to open the `file://` entry page (once). `--no-open` skips the browser. |
| `install` | write/update the unit, enable and start it. Idempotent; never rotates the key. Refuses a foreign, symlinked, or loosely-permissioned unit file. |
| `start` / `open` | start an installed service / open the entry page. |
| `status` | unit, active/enabled, port, linger, carrier, signed service state (busy/writable). Warns when the repo moved: run `install` again. |
| `stop` / `restart` / `uninstall` | refused unless the running service positively reports no active job. `uninstall` keeps private state. |
| `rotate` | replace the master key (old HTML stops working); same idle rule. |
| `carrier set --ssh-proxy URL --independent-carrier` | write `carrier.json` (`socks5://` or `http://127.0.0.1:PORT`). The flag acknowledges the route is independent of every managed VPS entry. |
| `carrier set --direct --independent-carrier` | explicit direct SSH (`ssh_proxy: null`), exclusive with `--ssh-proxy`. Reachability is not tested; `status` says so. Never an automatic fallback. |
| `carrier clear` | remove it. |

`serve` is internal (the unit's entry point). Old `--mihomo` and session flags are gone; `--ssh-proxy`/`--independent-carrier` now belong to `carrier set`.

## Carrier and writes
No `carrier.json` means read-only: subscriptions and the committed model are visible, writes are disabled. The tool never reuses an old lease port or assumes the GUI proxy (7897) is independent, and starts no proxy of its own. A configured carrier whose listener is down is reported by the backend as unavailable.

## Legacy sessions
A running foreground manager (session flock held while our unit is inactive) makes `install`/`start` refuse; it is never stopped for you.

## Failure notes
- Opener missing, no display, or non-zero exit: the command prints the entry path and exits 1; the service keeps running.
- An OS shutdown during a job leaves an unknown outcome handled by the existing journal/receipt recovery; jobs are never replayed.

## Updating the software
After pulling or editing manager code (`vps/lib/*.py`, `vps/assets/manage.*`, `dashboard.css`), run `manage.sh install` (or plain `manage.sh`). The unit records a digest of that code; when it changed, `install` rewrites the unit and restarts the service, which regenerates `index.html`. The restart happens only when the service reports no active job; otherwise `install` refuses and you retry later. `status` warns that the unit is stale until then. Key, port and carrier are untouched. `index.html`, keys, `domain.txt` and runtime state are not part of the digest.
