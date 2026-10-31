# Foreground management (`manage.sh`)

`bash vps/manage.sh` is a **foreground** program: no systemd unit, no install, no browser launch, no expiry timer.
Its lifetime is the lifetime of the page.

## What it does
1. Constructs `ManagementServer` on the fixed private port/key of `STATE/manage-service`: this takes the session flock and binds the port, so a second manager is refused before anything else happens.
2. Then, via the `prepare` callback, reads `VPS_SSH` from `vps/config` (or `--ssh TARGET`, which overrides it) and runs `vps.main --state-dir STATE connect --ssh TARGET [--identity] [--port] [--ssh-proxy] [--mihomo]`, which pulls the VPS's current native accounts. Skipped when `STATE/pending.json` exists: then it only serves the existing binding for explicit recovery. A failed connect aborts before anything is announced.
3. After bind, entry file written and a signed hello+state succeeded, prints `管理页面: file:///…/vps/index.html` (OSC 8 hyperlink on a TTY, plain URI otherwise; no key, query or fragment).
4. Waits. SIGINT, SIGTERM and SIGHUP (terminal closed) stop the API at once and release port and lock in `finally`.

## SSH route
The route is an invocation runtime parameter (`ManagementServer(runtime_carrier=…)`); `carrier.json` is not read or written and a historical one is not reused.
- default: no SSH proxy is specified. This does not prove where a system TUN actually routes it; the management path should be independent of the managed core, and reachability of long jobs stays your responsibility;
- `--ssh-proxy URL`: used for connect and every job; the page is read-only (`independent_carrier_required`) unless `--independent-carrier` is also given. Proxy credentials are never persisted.
`snapshot.service_mode` is `foreground` in this mode.

## Interrupting a job
Stop closes the API first. An active native job (own process group) gets `STOP_GRACE` seconds (20) to finish its receipt, then the group is terminated. Its record becomes `recovery_required` / `interrupted_unknown`; nothing is replayed. A native `pending.json` left behind is handled by the next `manage.sh` (recovery only) or `manage.sh recover`.

## Commands
`manage.sh [--state-dir DIR] [--ssh TARGET] [--identity KEY] [--port N] [--mihomo BIN] [connect]` (default), `devices list|add|rename|reset|remove`, `export`, `dashboard`, `status`, `backup`, `recover`. All but `recover` connect first (unless pending). `--no-open` is accepted and ignored. `install/start/stop/restart/uninstall/rotate/carrier/service/open` are refused with an explanation.

`lib/manage_service.py` remains in the tree as a deprecated internal module (its signed `query_state` is reused); `manage.sh` never routes to it.
