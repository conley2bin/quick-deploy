# Y9000p Pi client link helper

This is the **Y9000p client half** of Pi's fullscreen Ctrl+left Markdown link opening. The company extension identifies the initiating SSH client and sends one verified target here. A failure is shown to the user, never retried against the company desktop. Y9000p web links resolve the GUI session's default browser and use its explicit `--new-window` launch, so GNOME places the new toplevel on the workspace the user is viewing. Company file links use Y9000p VS Code Remote-SSH with `--new-window`. Ordinary left-click does not open a link. See the [short user runbook](../README.md) for `/reload`, `/copy-links`, and collision handling.

## Install and version check

On **Y9000p**, first obtain the reviewed checkout matching the company helper source, then run from that checkout's root (requires Bash and Python 3):

```sh
bash pi-agent/extensions/pi-copy-links/client/install.sh
# Only when deliberately repointing a separately versioned/foreign symlink:
bash pi-agent/extensions/pi-copy-links/client/install.sh --replace
```

The installer links `~/.local/bin/pi-client-link-helper` to **its own checkout's** canonical executable using a relative symlink. An existing link to that same source is a no-op, preserving its inode. An exact link to **this checkout's** old `pi-agent/client-link-helper/pi-client-link-helper` migrates automatically, even if dangling after the move; absolute and relative links and checkout/HOME aliases are supported. The old source directory is deleted, with no alias or wrapper.

Every other symlink, including an independent versioned deployment with identical bytes, is refused unless `--replace` is explicit. The report names the previous destination. Ordinary files/directories are refused even with `--replace`; the old destination itself is never modified or executed. The source must be an executable regular file before any directory/link mutation. Publication is atomic; setup/relink failure leaves the previous command in place. Observed source/link identity changes fail visibly, and cleanup never deletes/restores a successor command. Run one installer at a time: an uncooperative writer in the final check-to-rename interval cannot be excluded by pathname rechecks.

This nested installer is **Y9000p-only**: the company's `pi-agent/install.sh` shallow `extensions/*/install.sh` scan excludes it. It does not fetch/copy code, install remotely, launch a helper, or change Pi extensions, SSH config, GUI/tunnels, or running sessions. Running an old Y9000p checkout's installer does not upgrade its code.

**Before an idle Pi `/reload`, the installed Y9000p executable must match the company source and its v2 protocol.** From the company repository root, compare checksums and inspect the intended reviewed client destination:

```sh
sha256sum pi-agent/extensions/pi-copy-links/client/pi-client-link-helper
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes conley-Y9000p \
  'test -L "$HOME/.local/bin/pi-client-link-helper" && test -x "$HOME/.local/bin/pi-client-link-helper" && readlink -f "$HOME/.local/bin/pi-client-link-helper" && sha256sum "$HOME/.local/bin/pi-client-link-helper"'
```

The two `sha256sum` digest fields must match. If they differ, obtain the reviewed matching checkout on Y9000p, run the new installer (using `--replace` only for the deliberate foreign-link case), then repeat these checks. Do not reload Pi against a mismatched helper.

## Maintainer: invocation and v2 exchange

The company extension invokes a constant command (with the verified Y9000p peer/user/port pinned on SSH argv), writes one JSON request line to stdin, and **keeps stdin open**:

```sh
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=8 conley-Y9000p '~/.local/bin/pi-client-link-helper'
```

`ClearAllForwardings=yes` on that *control request* prevents unrelated SSH-config forwards; it is distinct from the helper's two-step Y9000p→company `-L`. The command and target host are fixed; the URL or pathname is JSON stdin, never part of a shell command. Protocol version 2 is a two-phase exchange on that **same live SSH channel**. Each line is one UTF-8 JSON object; input lines and cumulative stdout are bounded at 64 KiB. Unknown/duplicate keys fail on the helper; the dispatcher validates exact response fields and target identity.

```json
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-web","url":"http://127.0.0.1:8743/path?q=1#section"}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-web","url":"https://localhost:8744/path?q=1#section","backendHost":"::1"}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-file","kind":"file","path":"/home/conley/project/src/main.py","line":23,"column":4}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-file","kind":"directory","path":"/home/conley/project.v2"}
```

The helper validates the target, prepares GUI and (for web links) resolves a supported default-browser executable before writing `{"version":2,"phase":"prepared","nonce":"<32 lowercase hex>","ok":true,...target fields...}`. A company-loopback target also prepares its owned tunnel. **No browser or Code is launched yet.** The company validates that target against its request, checks `OpenLifetime`, then writes `{"version":2,"phase":"commit","nonce":"<same nonce>"}` without closing stdin. A missing, early, malformed, mismatched or buffered duplicate COMMIT fails. The helper's monotonic preparation deadline is capped at 45 seconds; it polls lock acquisition without blocking indefinitely and checks channel EOF and premature bytes. Disconnect or company abort/timeout before accepted COMMIT prevents a later launch even if a queued lock becomes free. A transport disconnected after accepted COMMIT may leave the accepted desktop handoff running; company-side deadline/abort then reports failure without pretending it revoked the launch.

After handoff, the helper writes exactly one final `{"version":2,"phase":"result",...}` JSON line and exits. Errors before PREPARED yield only the final error line; errors after PREPARED yield PREPARED then the final error line. The dispatcher requires the correct line order, matching PREPARED/final target, exit status, and no trailing data. An SSH failure or absent/invalid response is an error, not permission to launch locally.

`kind` is mandatory (`file` or `directory`) and must come from the company's validated `stat`, not a laptop-side guess. `line` and `column` are positive integers for files only; `column` requires `line`. A path containing `:` with a requested location is rejected: Code's goto parser would reinterpret part of the filename as a line or report an argument error with exit 0. File paths are absolute POSIX paths **on company**. The helper never checks or opens that path on the laptop. The company opener validates source/target and file existence and sends the resource kind. Browser input is HTTP(S) without userinfo; ambiguous browser-canonicalized hosts (including Unicode/IDNA forms, numeric shorthand, trailing-dot numeric forms, and unspecified addresses) fail before desktop launch. Supported loopback source authorities are `localhost`, `127.0.0.1`, and `[::1]`. Nonstandard 127/8 and IPv4-mapped IPv6 loopback literals fail **before PREPARED**: their exact source address could remain an unowned third destination after a same-port redirect. For a `localhost` URL, the company side has already selected a reachable numeric loopback backend: `backendHost` must be exactly `127.0.0.1` or `::1`. It is required for `localhost` and forbidden for explicit-IP or external URLs; the helper does not resolve the remote destination hostname. For an explicit standard IP, that same IP selects the numeric company backend. The original URL string, including hostname, port, path, query, and fragment, stays unchanged through PREPARED, result, and the browser's `--new-window` argv. The tunnel reply reports the selected numeric `remoteHost` and original `remotePort`/`localPort`.

The result's target fields are `{"ok":true,"action":"open-web","openedUrl":"...","tunnel":{...}}` or `{"ok":true,"action":"open-file","kind":"file","path":"..."}` with exit 0. Failure fields are `{"ok":false,"error":{"code":"...","message":"..."}}` with nonzero exit. A browser exit 0 during the grace, a still-running primary browser, or Code exit 0 attests only that the desktop handoff was accepted; it does not attest subsequent page load, window placement, or VS Code's eventual remote connection. The company integration presents errors. A live user Pi Ctrl+left gesture after `/reload` remains the final acceptance boundary.

## Maintainer: owned tunnel and desktop behavior

For every web target, the helper imports the active GUI variables from `systemctl --user show-environment`, requires exactly one active local graphical session in `loginctl`, and checks X11 access (`xset q`) or the Wayland socket. An SSH shell's `DISPLAY` is not trusted. In that GUI environment it runs `xdg-settings get default-web-browser`, requires one syntactically valid desktop ID, maps the supported ID to an executable, and resolves that executable against the GUI `PATH` before PREPARED. The supported mappings are:

| Desktop ID | Executable |
|---|---|
| `google-chrome.desktop` | `google-chrome-stable` |
| `google-chrome-beta.desktop` | `google-chrome-beta` |
| `google-chrome-unstable.desktop` | `google-chrome-unstable` |
| `chromium.desktop` | `chromium` |
| `chromium-browser.desktop` | `chromium-browser` |
| `chromium_chromium.desktop` | `chromium` |
| `firefox.desktop`, `firefox_firefox.desktop`, `firefox-esr.desktop` | `firefox` |
| `microsoft-edge.desktop` | `microsoft-edge-stable` |
| `brave-browser.desktop` | `brave-browser` |

Every mapping launches `EXECUTABLE --new-window URL`; there is no `xdg-open` fallback. An invalid or unknown desktop ID, a failed `xdg-settings` query, or a missing mapped executable returns `browser_unsupported` before PREPARED and names the relevant desktop ID when one was available. After COMMIT the helper starts the browser in a new session with stdin and both output streams connected to `/dev/null`. Exit 0 during a five-second grace means an existing browser accepted the request; a nonzero exit returns `browser_failed`; a process still running after the grace is treated as the new primary browser instance and is deliberately left running.

For **every supported loopback source** (`localhost`, `127.0.0.1`, `[::1]`), one `-L localhost:<original-port>:<numeric-backend>:<original-port>` on a pinned private master must create **both** exact `127.0.0.1` and `::1` Y9000p listeners to the **same company backend**. This covers a same-port redirect from an explicit IP to `localhost` regardless of the browser's address-family choice. The helper checks both families against the same master PID before PREPARED and again before browser handoff. An occupied/foreign-owned family returns `port_conflict`; a missing family without a foreign listener returns `tunnel_unverified`. It never opens a single-family loopback tunnel or rewrites the URL. File and directory actions use percent-encoded `vscode-remote://ssh-remote+conley-company/...` URIs and explicit Code resource flags:

```text
code --remote ssh-remote+conley-company --new-window [--goto] --file-uri <remote-file-uri[:line[:column]]>
code --remote ssh-remote+conley-company --new-window --folder-uri <remote-directory-uri>
```

Code's positional remote-path resolver guesses file/folder from the basename, misclassifying extensionless files and dotted directories. Explicit URI flags preserve the company-side kind without reading the laptop filesystem. VS Code may focus the existing window when the same folder is already open, even with `--new-window`; that existing window can be on another workspace.

The client must already configure `conley-company` as a noninteractive, host-key-known SSH alias whose `ssh -G` HostName is one unambiguous literal IP with a valid User and Port. A private mode-0700 directory under its `XDG_RUNTIME_DIR` holds a lock, per-endpoint records, and SSH control sockets. With a finite, nonblocking, channel-liveness-checked lock acquisition, the helper:

1. Captures `ssh -G conley-company` HostName/User/Port, then starts a private `ssh -M -S <socket> -fN` master with **those captured values pinned in command-line options** and the original alias retained for IdentityFile and other authentication settings. It keeps `ClearAllForwardings=yes`, `BatchMode=yes`, strict host-key checking and failure-on-forward options. Config drift after capture cannot change the master endpoint.
2. Requests only the explicit local forward with `ssh -F /dev/null -S <socket> -O forward -L localhost:<original-port>:<numeric-company-backend>:<original-port>`. The `/dev/null` config avoids the existing implicit `-R` while preserving the explicit `-L`. For an IPv6 backend, OpenSSH receives `[::1]` in the destination field.
3. Checks the master's PID via `-O check` and matches **both** exact-port `ss -H -ltnp` listeners in a single inventory. Reuse requires matching SSH identity and source endpoint/backend, plus live dual-listener PID ownership. `localhost` retains version-3 records with the chosen `backendHost`; explicit-IP records require new version-4 records including their numeric `backendHost` and source IP. Older single-family version-2 explicit-IP records, old/mismatched `localhost` records, and source/backend mismatches fail without reusing or closing an existing master. A dead control socket with a matching record can be retired and recreated. Ownership is rechecked immediately before PREPARED and browser handoff; a changed listener suppresses launch.
4. If a listener covers either client family at the original port (including wildcard/mapped bindings), the bind is unavailable, or a competing listener wins the forward race, returns actionable `port_conflict` before desktop launch. A forward reporting success with a foreign PID returns `port_conflict`; a missing family with no foreign listener returns `tunnel_unverified`. Either failure closes only the newly created private master. It does not choose an alternate port or rewrite the browser URL. Identify the conflicting listener and dependent tabs; close it only with its owner's approval, then retry the original URL.

All subprocesses use argument arrays, no target interpolation. Neither a decoy listener nor an unrelated existing `:8795` socket is touched. A newly established forward stays under the user's runtime directory for reuse until its SSH master ends or the runtime directory is cleared at logout/reboot. If the configured alias identity changes while a live record exists, the helper rejects the stale identity instead of silently reusing a tunnel to the old host.

## Validation boundary

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s pi-agent/extensions/pi-copy-links/client/tests -v
```

Installer tests use temporary checkout/HOME/bin fixtures, preserve old targets, and inject staging/relink failures and successor races without executing a receiver. The helper tests derive their paths from this directory. Fake SSH/`ss`/desktop commands test both required listeners and company-backend selection, opposite-family decoys and bind races, old-record rejection, before-COMMIT listener takeover/cancellation, unchanged URL, supported/unsupported default-browser resolution, browser new-window arguments and long-lived primary-process handoff, and preserved `localhost` and Code routing. `xdg-open` is a failing tripwire. Real local HTTP relays model both listener families: an explicit-IP `/start` returns a same-port `localhost` 302, and the opposite-family `/done` returns the company fixture body. The relays are **not** an actual SSH tunnel or Y9000p browser. The suite also exercises the installed Code resolver, bounded process cleanup, and SSH alias drift.

The corrected helper was installed on Y9000p and exercised through real company dispatcher requests with a tagged SSH client origin. A controlled opposite-family listener caused `port_conflict` before PREPARED with no browser launch; after removing that controlled listener, Chrome followed an explicit-IP page's same-port 302 to `localhost` and displayed the **company final page**, with both client loopback families served by one owned forward. A separate company IPv6-only `localhost` page displayed correctly through the same dual-family client design. Code Remote-SSH file opening was physically checked in an earlier v2 run. Those controlled fixtures/masters were removed; at this documentation checkpoint the active user Pi has **not** yet been `/reload`ed or activated by a physical Ctrl+left gesture. That live click remains the acceptance boundary.

An existing manual Y9000p `:8795` forward is still separate; the helper returns `port_conflict` instead of adopting or closing it. Identify its owner and dependent tabs before explicitly retiring it with owner approval. Both-family ownership covers the initial port and same-port `localhost` redirects; redirects to other ports/authorities are outside this guarantee, and a company service can stop after the tunnel is prepared.
