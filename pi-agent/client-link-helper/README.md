# Y9000p Pi client link helper

This is the **client half** of approved Ctrl+left link opening. It is not a Pi extension or a company-side opener. Do not install it on conley-company: the company integration must first identify the initiating SSH client and send one verified target to this helper. A failure must be shown to the user, never retried against the company desktop.

## Install and invocation

After this code is available on **conley-Y9000p**, explicitly run `./pi-agent/client-link-helper/install.sh` there. It creates only `~/.local/bin/pi-client-link-helper` as a symlink to this executable and refuses to replace a foreign target. This repository change does **not** run that installer or change any Y9000p session.

The later company-side integration can use one constant command (with the existing configured `conley-Y9000p` SSH alias), write a JSON request to its stdin, and close stdin:

```sh
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=8 conley-Y9000p '~/.local/bin/pi-client-link-helper'
```

`ClearAllForwardings=yes` on that *control request* prevents unrelated SSH-config forwards; it is distinct from the helper's two-step Y9000p→company `-L`. The command and target host are fixed; the URL or pathname is JSON stdin, never part of a shell command. `ssh` must deliver the helper's one stdout JSON line and exit status. An SSH failure or absent/invalid JSON reply is an error, not permission to launch locally.

Protocol version 1 (one object, at most 64 KiB, no duplicate/unknown keys):

```json
{"version":1,"action":"open-web","url":"http://127.0.0.1:8743/path?q=1#section"}
{"version":1,"action":"open-file","path":"/home/conley/project/src/main.py","line":23,"column":4}
```

`line` and `column` are positive integers; `column` requires `line`. File paths are absolute POSIX paths **on company**. The helper never checks or opens that path on the laptop. The eventual company opener should continue its own source/target and file-existence validation. Browser input is HTTP(S) without userinfo; malformed/ambiguous authorities are rejected.

Success is `{"ok":true,"action":"open-web","openedUrl":"...","tunnel":{...}}` or `{"ok":true,"action":"open-file","path":"..."}` with exit 0. Failure is `{"ok":false,"error":{"code":"...","message":"..."}}` with nonzero exit. `xdg-open` and `code` exit status attests that the desktop accepted a launch request; it does not attest subsequent page load or VS Code's eventual remote connection. The company integration must present errors and perform end-to-end acceptance later.

## Owned tunnel and desktop behavior

For a literal loopback IP or `localhost`, the helper imports the active GUI variables from `systemctl --user show-environment`, requires exactly one active local graphical session in `loginctl`, and checks X11 access (`xset q`) or the Wayland socket. An SSH shell's `DISPLAY` is not trusted. `xdg-open` uses Y9000p's default browser. File actions invoke argv equivalent to:

```text
code --remote ssh-remote+conley-company --new-window [--goto] -- /company/absolute/path[:line[:column]]
```

The client must already configure `conley-company` as a noninteractive, host-key-known SSH alias. A private mode-0700 directory under its `XDG_RUNTIME_DIR` holds a lock, per-endpoint records, and SSH control sockets. Under the lock the helper:

1. Starts a private `ssh -M -S <socket> -fN` master to the configured alias with `ClearAllForwardings=yes`, `BatchMode=yes`, strict host-key checking and failure-on-forward options.
2. Requests only the explicit local forward with `ssh -F /dev/null -S <socket> -O forward -L <loopback>:<client-port>:<company-loopback>:<server-port>`. The `/dev/null` config avoids the existing implicit `-R` while preserving the explicit `-L`.
3. Checks the master's PID via `-O check` and matches that PID to the exact loopback listener in `ss -H -ltnp` before recording/opening. Reuse repeats both checks against the private endpoint record; an unverified live mapping fails closed. A dead control socket is retired and recreated.
4. If **any** service occupies the desired client port (or a low port cannot be bound by the user), binds an owned alternate and rewrites the browser URL's authority port while retaining the original path, query, fragment, delimiters and encoding. A race is retried only if a fresh listener observation explains the bind failure. `localhost` is rewritten to literal `127.0.0.1`, because otherwise a browser could choose an unrelated `::1` service while the helper owns only IPv4.

All subprocesses use argument arrays, no target interpolation. Neither a decoy listener nor an unrelated existing `:8795` socket is touched. A newly established forward stays under the user's runtime directory for reuse until its SSH master ends or the runtime directory is cleared at logout/reboot. If the configured alias identity changes while a live record exists, the helper rejects the stale identity instead of silently reusing a tunnel to the old host.

## Validation boundary

Run `python3 -m unittest discover -s pi-agent/client-link-helper/tests -v` in the repository. Isolated fake SSH, `ss`, login/session, browser and Code executables cover collision/race, mapped reuse, concurrent requests, stale sockets, no-browser-on-failure, URL suffix preservation and exact Code argv. The SSH two-step mechanism was separately exercised on the actual Y9000p using an isolated :54465 forward to company :8743 (HTTP 200), then cleaned up. This module is **not yet installed or end-to-end exercised** on Y9000p; no company opener is wired yet. The screenshot's company :8795 service is stopped and the laptop's :8795 is occupied by an unrelated listener. For future end-to-end acceptance, use a live controlled company service (e.g. :8743) and confirm its content loaded in the actual Y9000p browser. The helper verifies SSH bind/ownership but a company service that is already stopped (or later stops) may still leave a bound, unusable tunnel; the browser's final HTTP result is not synchronously available to this CLI. A different local port changes the browser origin, which can affect absolute URLs, cookies, CORS and TLS certificates; that application-level behavior needs end-to-end validation.
