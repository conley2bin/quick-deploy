# pi-tmux-images (local fork: atomic transactions)

This directory is a **durable local fork** of the official npm package
`pi-tmux-images@0.2.0`, maintained in this repository so the hardening is reviewable,
testable and revertible instead of being patched into `node_modules` in place.

Provenance (also recorded in `package.json` → `fork`):

| Item | Value |
| --- | --- |
| Upstream package | `pi-tmux-images@0.2.0` (MIT, author Alex Furrier) |
| Registry tarball | `https://registry.npmjs.org/pi-tmux-images/-/pi-tmux-images-0.2.0.tgz` |
| Published integrity | `sha512-6a7iaYbN9WRbkLMgiYyrTjMQ34npFyBDPtzpw0GLiOIXjjYglBB2aQ8gaHbpoIBtw2qS9NO/4nf8iS1kELoY5Q==` |
| Upstream tag / commit | `v0.2.0` / `cab0433b173a3e09fae693240a63432d4d35c757` |
| Local version | `0.2.0-local.1` |

`LICENSE`, `NOTICE`, `assets/` and the upstream `README` content below are preserved
unchanged; only `version`, `type`, `scripts` and the `fork` metadata block were added
to the manifest.

## Why this fork exists

Ghostty 1.3.1 (GTK/Wayland, OpenGL renderer) exits through GTK's
`Gdk: Error flushing display: Resource temporarily unavailable` path after kitty
graphics traffic reaches it through tmux (see `ghostty-org/ghostty#11909`). The
trigger traffic on this machine was the read-preview / `/image` path, which wrote
**one `process.stdout.write()` per 4096-character chunk** plus separate placement and
deletion writes, with no acceptance gating and no payload ceiling. A lost or
truncated upload therefore left the terminal drawing placeholder cells for an image
it never received — the `ENOENT` / `missing image for virtual placement` storm that
saturates Ghostty's log loop and kills the window.

Hardening implemented in this fork (each item has a test):

1. **One write per complete transaction** — teardown, upload chunks and placement are
   assembled into a single string inside `\x1b[?2026h … \x1b[?2026l` and written once
   (`src/kitty-placeholder.ts: transaction`, `src/runtime.ts: emitPlaceholder`).
2. **Strict payload ceiling** — `boundedPayload()` returns `undefined` when the PNG
   cannot be brought under `MAX_TRANSACTION_BASE64` (256 KiB). It never falls back to
   the oversized original.
3. **Cells only after the write was accepted** — placeholder cells are returned only
   when the transaction for the current payload was written in this render (a write
   that is merely queued behind backpressure still counts as accepted; a throwing
   sink does not).
4. **Geometry-only resize keeps image data** — a resize emits `deletePlacement` +
   `placement` and never `deleteImage` (which would free the image and make the
   following placement reference a deleted id).
5. **`allow-passthrough=all` accepted** — the probe queries the originating pane
   (`tmux show-options -Apv -t "$TMUX_PANE" allow-passthrough`) and accepts
   `on|all|yes|true|1`; it fails closed for a missing/malformed pane, a failed or
   throwing command, or any other value. It runs once per runtime, never during a
   render.
6. **Text fallback on preparation or limit failure** — an over-limit or unloadable
   payload renders a readable notice (`runtime.notice()`) instead of graphics bytes.
7. **Coalesced teardown** — `clear()`/release emit every owned `deleteImage` in one
   write.
8. **Optional regular-file transfer (Kitty `t=f`)** — when enabled, the wire carries
   one short command with a base64 file path instead of every chunk
   (`fileTransferCommands`). Measured on this machine: Ghostty 1.3.1 **does** read the
   file (see `test/PROBES.md`); a bad path produces
   `warning(kitty_gfx): failed to get absolute path: error.FileNotFound`. Enable with
   `new PreviewRuntime({ fileTransfer: true })` once a deployment wants it; the
   default stays on bounded direct transfer so remote/unsupported hosts keep working.

Additional fork behaviour: the extension asks the in-repo `pi-inline-images` bridge
whether a host graphics owner is live (`pi-inline-images:graphics-owner:request` /
`read-preview-coordination`) and **withholds its own rendering** when one is, so two
owners never allocate terminal image ids at the same time.

## Layout

```text
extensions/index.ts      Pi extension entry (host wiring + bridge guard)
src/kitty-placeholder.ts Kitty sequence builders, transaction framing, bounding
src/runtime.ts           Engine: upload/placement/teardown invariants
src/capabilities.ts      pane-scoped passthrough probe + render mode
src/host.ts              the only module importing @earendil-works/pi-tui
src/loader.ts, src/*     upstream preview bookkeeping (sharp import is now lazy)
test/*.test.ts           node:test suite, no external dependencies
test/e2e-tmux.sh         isolated tmux byte-level end-to-end check
test/probe-ghostty.py    bounded Ghostty protocol probe (t=f, placeholders)
test/PROBES.md           probe results and remaining visual-evidence gap
```

## Tests

```bash
cd pi-agent/extensions/pi-tmux-images
npm test                      # 30 focused tests, no dependencies required
bash test/e2e-tmux.sh         # isolated tmux socket, byte-level capture
```

## Activation (not performed by the author of this fork)

The user's `~/.pi/agent/settings.json` is **not** touched by this change, and the
default tmux runtime is left alone. To adopt the fork:

1. `pi uninstall npm:pi-tmux-images` (or remove `npm:pi-tmux-images@0.2.0` from the
   `packages` array) so only one package owns read previews and `/image`.
2. `pi install /home/conley/quick-deploy/pi-agent/extensions/pi-tmux-images`, then
   `/reload` in Pi. Alternatively run `./install.sh`, which installs a managed
   symlink under `$PI_CODING_AGENT_DIR/extensions/pi-tmux-images` following the
   repo's `pi-agent/extensions` convention.
3. Re-enable passthrough **only for the panes you want images in**, and prefer
   `all`/`on` per window rather than globally:
   `tmux set-option -w -t <session>:<window> allow-passthrough on`.
4. Roll back with `pi uninstall /home/conley/quick-deploy/pi-agent/extensions/pi-tmux-images`
   plus `pi install npm:pi-tmux-images@0.2.0`; the tmux side rolls back with
   `tmux set-option -gw allow-passthrough off` (the committed default on this
   machine) or by editing `fresh-install/modules/tmux/tmux.conf.local`.

## Known limits (see `test/PROBES.md`)

- Ghostty 1.3.1 delivered **no** kitty responses to the program in our probe, even in
  raw mode with `q=2` and explicit `a=q` queries. Upload acceptance therefore cannot
  be verified by the terminal; the fork gates on the write being accepted instead.
- Placeholder (U+10EEEE) cells did not survive to the outer terminal through tmux
  (both unwrapped and passthrough-wrapped variants produced no render warning), so
  images drawn *only* as placeholder grids may remain invisible under tmux on this
  machine. Direct transmit+display through passthrough (`a=T`, optionally with `t=f`)
  is the path that did work in the probes.

---

The upstream README follows, unchanged apart from this fork section.
