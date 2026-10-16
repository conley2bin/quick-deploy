# Pi paired-desktop link helper

Install this helper on **each desktop that opens links**: company and Y9000p. The Pi server extension identifies the initiating SSH desktop, validates the target on the server, and sends one typed request to that desktop's helper. The helper selects its opposite file/web server from the actual `socket.gethostname()`, case-insensitively:

| Helper runs on desktop | Opposite fixed SSH alias | Code remote authority |
|---|---|---|
| `conley-company` | `conley-Y9000p` | `ssh-remote+conley-Y9000p` |
| `conley-Y9000P` | `conley-company` | `ssh-remote+conley-company` |

An unknown hostname returns `unsupported_host` before GUI or tunnel preparation, including for external web URLs. Request fields and inherited `HOSTNAME`, `SSH_*`, or `DISPLAY` cannot select a role. A failure is shown to the user, with no local or other-desktop fallback.

Web links resolve the helper desktop's GUI default browser and use its explicit `--new-window` launch. Server file links use that desktop's VS Code Remote-SSH with `--new-window`, always targeting the opposite server in the table. A same-spelling desktop path cannot turn this into a local open. Ordinary left-click does not open a link. See the [short user runbook](../README.md) for `/reload`, `/copy-links`, and collision handling.

## Install on each desktop and check versions

On **both company and Y9000p**, obtain the reviewed checkout containing the matching helper and server extension, then run locally from that checkout's root (requires Bash and Python 3):

```sh
bash pi-agent/extensions/pi-copy-links/client/install.sh
# Only when deliberately repointing a separately versioned/foreign symlink:
bash pi-agent/extensions/pi-copy-links/client/install.sh --replace
```

The installer links `~/.local/bin/pi-client-link-helper` to **its own checkout's** canonical executable using a relative symlink. An existing link to that same source is a no-op, preserving its inode. An exact link to **this checkout's** old `pi-agent/client-link-helper/pi-client-link-helper` migrates automatically, even if dangling after the move; absolute and relative links and checkout/HOME aliases are supported. The old source directory is deleted, with no alias or wrapper.

Every other symlink, including an independent versioned deployment with identical bytes, is refused unless `--replace` is explicit. The report names the previous destination. Ordinary files/directories are refused even with `--replace`; the old destination itself is never modified or executed. The source must be an executable regular file before any directory/link mutation. Publication is atomic; setup/relink failure leaves the previous command in place. Observed source/link identity changes fail visibly, and cleanup never deletes/restores a successor command. Run one installer at a time: an uncooperative writer in the final check-to-rename interval cannot be excluded by pathname rechecks.

The main `pi-agent/install.sh` shallow `extensions/*/install.sh` scan **excludes this nested installer on either host**. Run it separately on each needed desktop. It does not fetch/copy code, install remotely, launch a helper, or change Pi extensions, SSH config, GUI/tunnels, or running sessions. Running an old checkout's installer only links that old checkout's helper; it does not upgrade the helper or the Pi server's renderer/path recognition. Update the reviewed server extension separately before expecting new clickable-path behavior.

**Before an idle Pi `/reload`, each installed desktop executable must match the reviewed helper source and its v2 protocol.** From the company repository root, inspect the Y9000p desktop command and compare it with the reviewed local source:

```sh
sha256sum pi-agent/extensions/pi-copy-links/client/pi-client-link-helper
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes conley-Y9000p \
  'test -L "$HOME/.local/bin/pi-client-link-helper" && test -x "$HOME/.local/bin/pi-client-link-helper" && readlink -f "$HOME/.local/bin/pi-client-link-helper" && sha256sum "$HOME/.local/bin/pi-client-link-helper"'
```

For the reverse direction, run the corresponding check from the reviewed Y9000p repository root against the company desktop:

```sh
sha256sum pi-agent/extensions/pi-copy-links/client/pi-client-link-helper
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes conley-company \
  'test -L "$HOME/.local/bin/pi-client-link-helper" && test -x "$HOME/.local/bin/pi-client-link-helper" && readlink -f "$HOME/.local/bin/pi-client-link-helper" && sha256sum "$HOME/.local/bin/pi-client-link-helper"'
```

Each local/remote digest pair must match. If a pair differs, obtain the reviewed matching checkout on that desktop, run the new installer there (using `--replace` only for the deliberate foreign-link case), then repeat these checks. Do not reload Pi against a mismatched helper.

## Maintainer: invocation and v2 exchange

The Pi server extension invokes a constant helper command on the verified opposite desktop (with that peer/user/port pinned on SSH argv), writes one JSON request line to stdin, and **keeps stdin open**. Company→Y9000p control example:

```sh
ssh -T -o BatchMode=yes -o ClearAllForwardings=yes -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=8 conley-Y9000p '~/.local/bin/pi-client-link-helper'
```

For Y9000p→company control, the selected desktop alias is `conley-company`; the helper command and v2 exchange are identical. `ClearAllForwardings=yes` on the *control request* prevents unrelated SSH-config forwards; it is distinct from the helper desktop's two-step `-L` back to the Pi server. The command and two-host role mapping are source-owned; the URL or pathname is JSON stdin, never part of a shell command. Protocol version 2 is a two-phase exchange on that **same live SSH channel**. Each line is one UTF-8 JSON object; input lines and cumulative stdout are bounded at 64 KiB. Unknown/duplicate keys fail on the helper; the dispatcher validates exact response fields and target identity.

```json
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-web","url":"http://127.0.0.1:8743/path?q=1#section"}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-web","url":"https://localhost:8744/path?q=1#section","backendHost":"::1"}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-file","kind":"file","path":"/home/conley/project/src/main.py","line":23,"column":4}
{"version":2,"phase":"request","prepareDeadlineMs":45000,"action":"open-file","kind":"directory","path":"/home/conley/project.v2"}
```

The helper validates the target, prepares GUI and (for web links) resolves a supported default-browser executable before writing `{"version":2,"phase":"prepared","nonce":"<32 lowercase hex>","ok":true,...target fields...}`. A server-loopback target also prepares its owned tunnel. **No browser or Code is launched yet.** The Pi server validates that target against its request, checks `OpenLifetime`, then writes `{"version":2,"phase":"commit","nonce":"<same nonce>"}` without closing stdin. A missing, early, malformed, mismatched or buffered duplicate COMMIT fails. The helper's monotonic preparation deadline is capped at 45 seconds; it polls lock acquisition without blocking indefinitely and checks channel EOF and premature bytes. Disconnect or server abort/timeout before accepted COMMIT prevents a later launch even if a queued lock becomes free. A transport disconnected after accepted COMMIT may leave the accepted desktop handoff running; server-side deadline/abort then reports failure without pretending it revoked the launch.

After handoff, the helper writes exactly one final `{"version":2,"phase":"result",...}` JSON line and exits. Errors before PREPARED yield only the final error line; errors after PREPARED yield PREPARED then the final error line. The dispatcher requires the correct line order, matching PREPARED/final target, exit status, and no trailing data. An SSH failure or absent/invalid response is an error, not permission to launch locally.

`kind` is mandatory (`file` or `directory`) and must come from the Pi server's validated `stat`, not a desktop-side guess. `line` and `column` are positive integers for files only; `column` requires `line`. A path containing `:` with a requested location is rejected: Code's goto parser would reinterpret part of the filename as a line or report an argument error with exit 0. File paths are absolute POSIX paths **on the opposite Pi server**. The helper never stats or opens that path on its own desktop filesystem. The server opener validates source/target and file existence and sends the resource kind. Browser input is HTTP(S) without userinfo; ambiguous browser-canonicalized hosts (including Unicode/IDNA forms, numeric shorthand, trailing-dot numeric forms, and unspecified addresses) fail before desktop launch. Supported loopback source authorities are `localhost`, `127.0.0.1`, and `[::1]`. Nonstandard 127/8 and IPv4-mapped IPv6 loopback literals fail **before PREPARED**: their exact source address could remain an unowned third destination after a same-port redirect. For a `localhost` URL, the Pi server has already selected a reachable numeric loopback backend: `backendHost` must be exactly `127.0.0.1` or `::1`. It is required for `localhost` and forbidden for explicit-IP or external URLs; the helper does not resolve the remote destination hostname. For an explicit standard IP, that same IP selects the numeric server backend. The original URL string, including hostname, port, path, query, and fragment, stays unchanged through PREPARED, result, and the browser's `--new-window` argv. The tunnel reply reports the selected numeric `remoteHost` and original `remotePort`/`localPort`.

The result's target fields are `{"ok":true,"action":"open-web","openedUrl":"...","tunnel":{...}}` or `{"ok":true,"action":"open-file","kind":"file","path":"..."}` with exit 0. Failure fields are `{"ok":false,"error":{"code":"...","message":"..."}}` with nonzero exit. A browser exit 0 during the grace, a still-running primary browser, or Code exit 0 attests only that the desktop handoff was accepted; it does not attest subsequent page load, window placement, or VS Code's eventual remote connection. The Pi server integration presents errors. A live user Pi Ctrl+left gesture after `/reload` remains the final acceptance boundary.

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

For **every supported loopback source** (`localhost`, `127.0.0.1`, `[::1]`), one `-L localhost:<original-port>:<numeric-backend>:<original-port>` on a pinned private master must create **both** exact `127.0.0.1` and `::1` helper-desktop listeners to the **same opposite-server backend**. This covers a same-port redirect from an explicit IP to `localhost` regardless of the browser's address-family choice. The helper checks both families against the same master PID before PREPARED and again before browser handoff. An occupied/foreign-owned family returns `port_conflict`; a missing family without a foreign listener returns `tunnel_unverified`. It never opens a single-family loopback tunnel or rewrites the URL. File and directory actions use percent-encoded remote URIs and explicit Code resource flags. On the company desktop:

```text
code --remote ssh-remote+conley-Y9000p --new-window [--goto] --file-uri vscode-remote://ssh-remote+conley-Y9000p/<encoded-path>[:line[:column]]
code --remote ssh-remote+conley-Y9000p --new-window --folder-uri vscode-remote://ssh-remote+conley-Y9000p/<encoded-path>
```

On Y9000p, both the `--remote` argument and URI authority are `ssh-remote+conley-company`. URI encoding preserves literal Unicode, spaces, quotes, `%`, `#`, `?`, and non-goto `:` filename data. Code's positional remote-path resolver guesses file/folder from the basename, misclassifying extensionless files and dotted directories. Explicit URI flags preserve the server-side kind without reading the desktop filesystem. VS Code may focus the existing window when the same folder is already open, even with `--new-window`; that existing window can be on another workspace.

Each desktop must already configure its **opposite fixed alias from the role table** as noninteractive and host-key-known. That alias's `ssh -G` HostName must be one unambiguous literal IP with a valid User and Port. A private mode-0700 directory under its `XDG_RUNTIME_DIR` holds a lock, per-endpoint records, and SSH control sockets. With a finite, nonblocking, channel-liveness-checked lock acquisition, the helper:

1. Captures `ssh -G <opposite-alias>` HostName/User/Port, then starts a private `ssh -M -S <socket> -fN` master with **those captured values pinned in command-line options** and the original alias retained for IdentityFile and other authentication settings. It keeps `ClearAllForwardings=yes`, `BatchMode=yes`, strict host-key checking and failure-on-forward options. Config drift after capture cannot change the master endpoint.
2. Requests only the explicit local forward with `ssh -F /dev/null -S <socket> -O forward -L localhost:<original-port>:<numeric-server-backend>:<original-port>`. The `/dev/null` config avoids the existing implicit `-R` while preserving the explicit `-L`. For an IPv6 backend, OpenSSH receives `[::1]` in the destination field.
3. Checks the master's PID via `-O check` and matches **both** exact-port `ss -H -ltnp` listeners in a single inventory. Reuse requires matching SSH identity and source endpoint/backend, plus live dual-listener PID ownership. `localhost` retains version-3 records with the chosen `backendHost`; explicit-IP records require new version-4 records including their numeric `backendHost` and source IP. Older single-family version-2 explicit-IP records, old/mismatched `localhost` records, and source/backend mismatches fail without reusing or closing an existing master. A dead control socket with a matching record can be retired and recreated. Ownership is rechecked immediately before PREPARED and browser handoff; a changed listener suppresses launch.
4. If a listener covers either client family at the original port (including wildcard/mapped bindings), the bind is unavailable, or a competing listener wins the forward race, returns actionable `port_conflict` before desktop launch. A forward reporting success with a foreign PID returns `port_conflict`; a missing family with no foreign listener returns `tunnel_unverified`. Either failure closes only the newly created private master. It does not choose an alternate port or rewrite the browser URL. Identify the conflicting listener and dependent tabs; close it only with its owner's approval, then retry the original URL.

All subprocesses use argument arrays, no target interpolation. Neither a decoy listener nor an unrelated existing `:8795` socket is touched. A newly established forward stays under the user's runtime directory for reuse until its SSH master ends or the runtime directory is cleared at logout/reboot. If the configured alias identity changes while a live record exists, the helper rejects the stale identity instead of silently reusing a tunnel to the old host.

## Validation boundary

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s pi-agent/extensions/pi-copy-links/client/tests -v
```

Installer tests use temporary checkout/HOME/bin fixtures, preserve old targets, and inject staging/relink failures and successor races without executing a receiver. Helper tests derive their paths from this directory and replay every contract with explicit company and Y9000p hostname fixtures, independent of the execution machine. A test-only `runpy` wrapper patches `socket.gethostname()` before running the exact production source; production has no test/environment role override. Tests also cover case normalization, hostile inherited hostname/SSH/display values, unknown-host refusal, rejected request-controlled authority, exact opposite Code file/folder/goto URIs, literal Unicode, local kind-mismatched decoys, no local resource stat, opposite `ssh -G`/master pinning, and record identity across roles. Fake SSH/`ss`/desktop commands test both required listeners and server-backend selection, opposite-family decoys and bind races, old-record rejection, before-COMMIT listener takeover/cancellation, unchanged URL, supported/unsupported default-browser resolution, browser new-window arguments and long-lived primary-process handoff, and preserved `localhost` and Code routing. `xdg-open` is a failing tripwire. Real local HTTP relays model both listener families: an explicit-IP `/start` returns a same-port `localhost` 302, and the opposite-family `/done` returns the company fixture body. The relays are **not** an actual SSH tunnel or desktop browser. The suite also exercises the installed Code resolver without launching Code, bounded process cleanup, and SSH alias drift in both roles. The bidirectional component stage does not install helpers, launch real desktop applications, or reload user Pi; real reverse-direction GUI acceptance requires later reviewed deployment.

Historical one-direction acceptance: the earlier corrected helper was installed on Y9000p and exercised through real company dispatcher requests with a tagged SSH client origin. A controlled opposite-family listener caused `port_conflict` before PREPARED with no browser launch; after removing that controlled listener, Chrome followed an explicit-IP page's same-port 302 to `localhost` and displayed the **company final page**, with both client loopback families served by one owned forward. A separate company IPv6-only `localhost` page displayed correctly through the same dual-family client design. Code Remote-SSH file opening was physically checked in an earlier v2 run. Those controlled fixtures/masters were removed. That historical evidence establishes the company-server→Y9000p-desktop direction; it does not establish the reverse-direction GUI behavior of this paired-role implementation. A real reverse-direction Pi Ctrl+left remains the deployment acceptance boundary.

A manually managed forward such as the previously observed Y9000p `:8795` remains separate; the helper returns `port_conflict` instead of adopting or closing it. Identify its owner and dependent tabs before explicitly retiring it with owner approval. Both-family ownership covers the initial port and same-port `localhost` redirects; redirects to other ports/authorities are outside this guarantee, and a server service can stop after the tunnel is prepared.
