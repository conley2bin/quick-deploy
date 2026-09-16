# pi-tmux-images 0.2.0 local replay repairs

## Active scope

This directory carries one active local repair for the installed `pi-tmux-images@0.2.0`: `pane-passthrough-policy.patch` changes only `src/capabilities.ts`. The earlier unconditional placement replay is retired. `retire-placement-replay.patch` is a one-way removal patch for installations that received it; it is not an installation patch for pristine packages.

`stage-c-recent-cache.patch` is deliberately **not deployed** until the later provenance/review slice. It is an exact-source guarded replay for a disposable package copy. The replay retains the newest 16 read resources, evicts the oldest resource before every later admission, restores the newest eligible entries after the latest clear marker, and leaves older custom history visible as wrapped expired notices. All preparation, rendering, release, reset, restore, and late binding use the public versioned `pi-inline-images` read bridge. The old runtime retains no terminal IDs, placement/upload maps, native `Image` branch, or graphics output fallback. Missing/version-mismatched bridges remain visible through `Text.render(width)`. `apply-stage-c-disposable.sh` refuses a different package version or source hash before applying.

The shared backend enforces independent owner residency: inline has 64 images/64 MiB and read has 16 images/64 MiB. Both use one bounded transport, while resource/owner cancellation removes only matching unsent jobs and preserves global backpressure and rate debt. Read-only resources invoke the same monitor lifecycle callback as Markdown resources; stopping the monitor invalidates stale readiness before a later resource can upload.

The active capability repair queries the originating pane's effective tmux policy with:

```bash
tmux show-options -Apv -t "$TMUX_PANE" allow-passthrough
```

It accepts `on`, `all`, and the previously accepted boolean aliases. It fails closed when `TMUX_PANE` is missing or malformed, the command errors or times out, or output is disabled or unknown. The result remains cached once at runtime construction. No tmux option is changed.

## Why placement replay was retired

The old local patch moved `a=p` outside the placement-geometry change branch, so every component render wrote a direct Kitty placement command. Those writes bypass Pi's line diff and its fullscreen clipping: even unchanged or clipped render output could cause terminal graphics side effects. This amplified graphics traffic without evidence that repeating an unacknowledged placement repaired the reported failure.

The capability-only runtime restores upstream placement semantics:

- initial render uploads the PNG and creates one placement;
- unchanged geometry returns the Unicode grid without direct terminal writes;
- geometry change deletes that runtime-owned prior placement, then creates the new one;
- `clear()` still deletes every runtime-owned image ID.

This removes a confirmed traffic amplifier. It does not prove the reported GTK Wayland flush `EAGAIN` crash is fixed.

Kitty commands still use `q=2`, so the runtime receives no upload or placement acknowledgement. Cached upload state can therefore disagree with terminal state. A lost upload is not repaired by a later placement, and detached/future clients still have no durable image queue. Do not add speculative retry or preserve upload caches across terminal/client lifecycles without an acknowledged reconciliation mechanism.

## Provenance and hashes

- npm package: `pi-tmux-images@0.2.0`
- Registry tarball: `https://registry.npmjs.org/pi-tmux-images/-/pi-tmux-images-0.2.0.tgz`
- Published integrity: `sha512-6a7iaYbN9WRbkLMgiYyrTjMQ34npFyBDPtzpw0GLiOIXjjYglBB2aQ8gaHbpoIBtw2qS9NO/4nf8iS1kELoY5Q==`
- Tarball SHA-256: `033c89008eaae419634cd51297a2bb4fd5c759fcb2a32fae04a6835881246db4`
- Upstream tag/commit: `v0.2.0` / `cab0433b173a3e09fae693240a63432d4d35c757`
- Pristine and capability-only `src/runtime.ts`: `4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d`
- Retired placement-replay `src/runtime.ts`: `7db69da01c21617b6ceed3f6f67ef7cc5d9428ee850008accd6dbcbcfa2bca25`
- Pristine `src/capabilities.ts`: `b9a498a9839ae04909995e530a8c052d0490653eadde7c7a5ec1941ea27357c8`
- Capability-patched `src/capabilities.ts`: `c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a`

`installed-before.sha256` records the integrity-verified pristine package. `installed-after.sha256` records the capability-only installed state; only `src/capabilities.ts` differs.

## Guarded retirement for an already placement-patched installation

Run from the root of the installed `pi-tmux-images` package. Set `PATCH_DIR` to this directory. Every hash check must succeed; stop and inspect rather than overwrite if it does not.

```bash
test "$(node -p "require('./package.json').version")" = 0.2.0
test "$(sha256sum src/runtime.ts | cut -d' ' -f1)" = \
  7db69da01c21617b6ceed3f6f67ef7cc5d9428ee850008accd6dbcbcfa2bca25
patch --dry-run -p1 < "$PATCH_DIR/retire-placement-replay.patch"
patch -p1 < "$PATCH_DIR/retire-placement-replay.patch"
test "$(sha256sum src/runtime.ts | cut -d' ' -f1)" = \
  4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d

cap_hash="$(sha256sum src/capabilities.ts | cut -d' ' -f1)"
if test "$cap_hash" = b9a498a9839ae04909995e530a8c052d0490653eadde7c7a5ec1941ea27357c8; then
  patch --dry-run -p1 < "$PATCH_DIR/pane-passthrough-policy.patch"
  patch -p1 < "$PATCH_DIR/pane-passthrough-policy.patch"
else
  test "$cap_hash" = c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a
fi
test "$(sha256sum src/capabilities.ts | cut -d' ' -f1)" = \
  c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a
```

A running Pi process must reload or restart before it uses changed package source. This procedure itself must not write terminal escapes or alter tmux policy.

## Pristine installation: capability repair only

Run from an unmodified `pi-tmux-images@0.2.0` package. Do not apply a placement patch.

```bash
test "$(node -p "require('./package.json').version")" = 0.2.0
test "$(sha256sum src/runtime.ts | cut -d' ' -f1)" = \
  4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d
test "$(sha256sum src/capabilities.ts | cut -d' ' -f1)" = \
  b9a498a9839ae04909995e530a8c052d0490653eadde7c7a5ec1941ea27357c8
patch --dry-run -p1 < "$PATCH_DIR/pane-passthrough-policy.patch"
patch -p1 < "$PATCH_DIR/pane-passthrough-policy.patch"
test "$(sha256sum src/runtime.ts | cut -d' ' -f1)" = \
  4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d
test "$(sha256sum src/capabilities.ts | cut -d' ' -f1)" = \
  c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a
```

An npm reinstall/update can overwrite the local capability repair. Re-verify the exact package version and pristine hashes before replaying it.

## Captured-output regression tests

The tests use injected output sinks and tmux probes; they do not launch Ghostty or Pi, write escapes to a terminal, probe a live pane, or make model calls.

```bash
NODE_PATH="$(npm root -g)/@earendil-works/pi-coding-agent/node_modules:$HOME/.pi/agent/npm/node_modules" \
PI_TMUX_IMAGES_ROOT="$HOME/.pi/agent/npm/node_modules/pi-tmux-images" \
  pi-agent/extensions/pi-inline-images/node_modules/.bin/tsx --test \
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/placement-lifecycle.test.ts \
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/pane-passthrough-policy.test.ts \
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/stage-c-replay.test.ts \
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/stage-c-behavior.test.ts
```

`placement-lifecycle.test.ts` checks the installed capability-only runtime's bounded placement lifecycle. `pane-passthrough-policy.test.ts` checks the pane-effective command, enabled values, and fail-closed cases. `stage-c-replay.test.ts` applies only to a disposable copy and rejects retained native graphics paths. `stage-c-behavior.test.ts` runs the patched extension/runtime against the real shared backend and a captured sink: 20 automatic entries, 16-resource eviction, restore preparation, inline-preserving clear, late bridge binding, and width-16/40 CJK notices.

## Boundary of the capability repair

Effective `allow-passthrough=all` forwards arbitrary DCS passthrough from invisible panes, not only image commands. It still requires a ready, nonsuspended attached client whose session contains the window; it is not storage for detached or future clients. `on` remains visibility-gated. Choosing the policy remains an operator decision outside this patch.
