# Y9000p Pi client link helper

This is the **Y9000p client half** of approved Ctrl+left link opening. It is not a Pi extension or a company-side opener. The company extension identifies the initiating SSH client and sends one verified target to this helper. A failure is shown to the user, never retried against the company desktop. Both sides must run protocol v2 together; the older v1 helper installed on Y9000p is incompatible until explicitly upgraded.

## Install and invocation

After this version is available on **conley-Y9000p**, explicitly run `./pi-agent/client-link-helper/install.sh` there. It creates only `~/.local/bin/pi-client-link-helper` as a symlink to this executable and refuses to replace a foreign target. This repository change does **not** run that installer or change any Y9000p session.

The company extension invokes a constant command (with the verified Y9000p peer/user/port pinned on SSH argv), writes one JSON request line to stdin, and **keeps stdin open**:

```sh
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=8 conley-Y9000p '~/.local/bin/pi-client-link-helper'
```

`ClearAllForwardings=yes` on that *control request* prevents unrelated SSH-config forwards; it is distinct from the helper's two-step Y9000p→company `-L`. The command and target host are fixed; the URL or pathname is JSON stdin, never part of a shell command. Protocol version 2 is a two-phase exchange on that **same live SSH channel**. Each line is one UTF-8 JSON object; input lines and cumulative stdout are bounded at 64 KiB. Unknown/duplicate keys fail on the helper; the dispatcher validates exact response fields and target identity.

```json
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-web","url":"http://127.0.0.1:8743/path?q=1#section"}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-file","kind":"file","path":"/home/conley/project/src/main.py","line":23,"column":4}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-file","kind":"directory","path":"/home/conley/project.v2"}
```

The helper validates the target, prepares GUI and (for company loopback) an owned tunnel, then writes `{"version":2,"phase":"prepared","nonce":"<32 lowercase hex>","ok":true,...target fields...}`. **No browser or Code is launched yet.** The company validates that target against its request, checks `OpenLifetime`, then writes `{"version":2,"phase":"commit","nonce":"<same nonce>"}` without closing stdin. A missing, early, malformed, mismatched or buffered duplicate COMMIT fails. The helper's monotonic preparation deadline is capped at 45 seconds; it polls lock acquisition without blocking indefinitely and checks channel EOF and premature bytes. Disconnect or company abort/timeout before accepted COMMIT prevents a later launch even if a queued lock becomes free. A transport disconnected after accepted COMMIT may leave the accepted desktop handoff running; company-side deadline/abort then reports failure without pretending it revoked the launch.

After handoff, the helper writes exactly one final `{"version":2,"phase":"result",...}` JSON line and exits. Errors before PREPARED yield only the final error line; errors after PREPARED yield PREPARED then the final error line. The dispatcher requires the correct line order, matching PREPARED/final target, exit status, and no trailing data. An SSH failure or absent/invalid response is an error, not permission to launch locally.

`kind` is mandatory (`file` or `directory`) and must come from the company's validated `stat`, not a laptop-side guess. `line` and `column` are positive integers for files only; `column` requires `line`. A path containing `:` with a requested location is rejected: Code's goto parser would reinterpret part of the filename as a line or report an argument error with exit 0. File paths are absolute POSIX paths **on company**. The helper never checks or opens that path on the laptop. The eventual company opener must continue its source/target and file-existence validation and send the resource kind. Browser input is HTTP(S) without userinfo; ambiguous browser-canonicalized hosts (including Unicode/IDNA forms, numeric shorthand, trailing-dot numeric forms, and unspecified addresses) fail before desktop launch.

The result's target fields are `{"ok":true,"action":"open-web","openedUrl":"...","tunnel":{...}}` or `{"ok":true,"action":"open-file","kind":"file","path":"..."}` with exit 0. Failure fields are `{"ok":false,"error":{"code":"...","message":"..."}}` with nonzero exit. `xdg-open` and `code` exit status attests that the desktop accepted a launch request; it does not attest subsequent page load or VS Code's eventual remote connection. The company integration must present errors and perform end-to-end acceptance later.

## Owned tunnel and desktop behavior

For a literal loopback IP, the helper imports the active GUI variables from `systemctl --user show-environment`, requires exactly one active local graphical session in `loginctl`, and checks X11 access (`xset q`) or the Wayland socket. An SSH shell's `DISPLAY` is not trusted. `xdg-open` uses Y9000p's default browser. For now, `localhost` URLs fail before PREPARED: if either client loopback family has a listener on the original port they return `port_conflict`; otherwise they return `unsupported_target` until hostname-preserving dual-stack forwarding is implemented. No IPv4-only `localhost` success or URL rewrite is permitted. File and directory actions use percent-encoded `vscode-remote://ssh-remote+conley-company/...` URIs and explicit Code resource flags:

```text
code --remote ssh-remote+conley-company --new-window [--goto] --file-uri <remote-file-uri[:line[:column]]>
code --remote ssh-remote+conley-company --new-window --folder-uri <remote-directory-uri>
```

Code's positional remote-path resolver guesses file/folder from the basename, misclassifying extensionless files and dotted directories. Explicit URI flags preserve the company-side kind without reading the laptop filesystem.

The client must already configure `conley-company` as a noninteractive, host-key-known SSH alias whose `ssh -G` HostName is one unambiguous literal IP with a valid User and Port. A private mode-0700 directory under its `XDG_RUNTIME_DIR` holds a lock, per-endpoint records, and SSH control sockets. With a finite, nonblocking, channel-liveness-checked lock acquisition, the helper:

1. Captures `ssh -G conley-company` HostName/User/Port, then starts a private `ssh -M -S <socket> -fN` master with **those captured values pinned in command-line options** and the original alias retained for IdentityFile and other authentication settings. It keeps `ClearAllForwardings=yes`, `BatchMode=yes`, strict host-key checking and failure-on-forward options. Config drift after capture cannot change the master endpoint.
2. Requests only the explicit local forward with `ssh -F /dev/null -S <socket> -O forward -L <source-IP>:<original-port>:<company-loopback>:<original-port>`. The `/dev/null` config avoids the existing implicit `-R` while preserving the explicit `-L`.
3. Checks the master's PID via `-O check` and matches that PID to the exact source-IP/original-port listener in `ss -H -ltnp` before recording/opening. Reuse requires an endpoint/identity-matching version-2 record and repeats both PID/listener checks. Old version-1 records were created without master endpoint pinning and are refused until their private master/record is explicitly retired; an unverified live mapping fails closed. A dead control socket is retired and recreated.
4. If a listener covers the requested literal client IP and original port (including wildcard/mapped bindings), the bind is unavailable, or a competing listener wins the forward race, returns actionable `port_conflict` before desktop launch. A listener on the other loopback family alone does not block an explicit IPv4/IPv6 source URL. It does not choose an alternate port or rewrite the browser URL. Clear the conflicting listener (after checking whether existing tabs use it) and retry.

All subprocesses use argument arrays, no target interpolation. Neither a decoy listener nor an unrelated existing `:8795` socket is touched. A newly established forward stays under the user's runtime directory for reuse until its SSH master ends or the runtime directory is cleared at logout/reboot. If the configured alias identity changes while a live record exists, the helper rejects the stale identity instead of silently reusing a tunnel to the old host.

## Validation boundary

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s pi-agent/client-link-helper/tests -v` in the repository. Isolated fake SSH, `ss`, login/session, browser and Code executables cover exact-port collision/race refusal before browser, positive exact-origin reuse, a company absolute redirect/decoy fixture, real `ssh -G` alias drift before fake master creation, stale/legacy records, localhost conservative failure, no-browser-on-failure, browser-normalized loopback rejection, complete inventory parsing beyond 4 KiB, and exact Code argv. A no-GUI probe evaluates the typed resource branches of the installed Code 1.135.0 resolver for extensionless files, dotted directories, encoded paths and goto locations; a real process-group regression checks that a TERM-ignoring descendant is gone before timeout returns. Command output is consumed completely up to 1 MiB for inventories, then rejected explicitly rather than silently clipped. The SSH two-step mechanism was separately exercised on the actual Y9000p using an isolated :54465 forward to company :8743 (HTTP 200), then cleaned up. An older v1 module was physically exercised on Y9000p with a company :8743 page and Remote-SSH Code. This v2 handoff has only isolated fake-command tests; it has **not** been installed there or exercised in a physical browser/Code. The company opener is wired in source but the running Pi was not reloaded. The screenshot's company :8795 service is currently live, but Y9000p :8795 has a separate manual SSH forward; do not retire it without checking existing tabs. This helper returns `port_conflict` there rather than claiming ownership. For future end-to-end acceptance, use a free original client port and a live controlled company service (e.g. :8743), then confirm content in the actual Y9000p browser. The helper verifies SSH bind/ownership but a company service that is already stopped (or later stops) may still leave a bound, unusable tunnel; the browser's final HTTP result is not synchronously available to this CLI. Dual-stack/TLS-preserving `localhost` support and physical v2 acceptance remain separate work.
