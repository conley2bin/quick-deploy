# pi-tmux-images 0.2.0 local replay repairs

## Active scope

This directory carries one active local repair for the installed `pi-tmux-images@0.2.0`: `pane-passthrough-policy.patch` changes only `src/capabilities.ts`. The earlier unconditional placement replay is retired. `retire-placement-replay.patch` is a one-way removal patch for installations that received it; it is not an installation patch for pristine packages.

`stage-c-recent-cache.patch` is the cumulative exact-source Stage C replay. It retains the newest 16 read resources, evicts the oldest resource before later admission, restores newest entries incrementally within the 64 MiB decoded-PNG budget, and leaves older custom history visible as wrapped expired notices. Suitable PNGs are fully decoded for validity and then retain their exact encoded bytes and bit depth. Automatic failures persist as wrapped entries instead of disappearing; encoded-input overflow records bounded origin/length metadata without payload hashing, decoding, or copying. All graphics use the public versioned `pi-inline-images` bridge; the old runtime retains no terminal IDs, placement/upload maps, native `Image` branch, or writer fallback.

`stage-c-review-fixes.patch` and `stage-c-ownership-fixes.patch` upgrade the two exact earlier deployments without reverting the capability repair. The ownership upgrade fails closed until the Pi 0.85.1 inline adapter proves a native tool-row/custom-entry bijection; coordinated tool rows show the custom full-resolution bitmap while native display is disabled. User-origin previews remain independent because no native tool row competes with them. Unsupported mappings or explicit host image-off keep custom bitmaps withheld with a notice. `replay-stage-c.sh check PACKAGE_ROOT` accepts only pristine, legacy-patched, previous-patched, or fully patched sources. `apply` advances an accepted state to final patched and is byte-idempotent there. The locally installed package intentionally remains `previous-patched` until independent review accepts this candidate.

Original-file substitution is display-only and fail-closed. At `tool_call`, the tracker accepts only Pi's exact selected builtin-read source tuple and freezes a stable, canonical, bounded local image identity/hash/bytes. At `tool_result`, it requires the source to remain unchanged and the received block to equal either the original or the public host `resizeImage` result. Persisted proof records tool call/block identity, received hash/MIME, and original path/identity/hash metadata without changing the result or model message. Restore repeats the tool-source, file, block, and resize relation checks. ShellGate and other overrides therefore keep the actual received pixels and show a wrapped original-resolution-unverified notice, even when a same-path local derivative is byte-identical.

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
- Stage C `extensions/index.ts`: `c387310e555708f5d8a9f110cc5b70f9b103f59a69d78a59b2a4c466928063eb`
- Stage C `src/automatic.ts`: `34978081cc6e08a4dec9e5415dbea0ca469b2ae7731b9503d49acb38a9aa0c13`
- Stage C `src/loader.ts`: `0b3996ff6fc475fac5985f1fa56a76c9009f86524b02f047812b9c0a7848ad1d`
- Stage C `src/runtime.ts`: `0f13f7c4212b14a3065bff2a65a833eae67a4a242ae514ba962ecf0e690946b9`
- Stage C `src/renderer.ts`: `491a90b3b88a46f38e3669a45ce98ca5a2e610b633ec98a1c65b7935d64debb5`
- Stage C `src/transcript-entry.ts`: `ef9c21f1406c9a041c8db35f19fd0bfccbddabb9233ba25f585e986840103d14`

`installed-before.sha256` records the integrity-verified pristine package. `installed-after.sha256` records the capability-only state that preceded Stage C; the replay script is the authoritative Stage C manifest.

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
cd pi-agent/extensions/pi-inline-images
NODE_PATH="$(npm root -g)/@earendil-works/pi-coding-agent/node_modules:$HOME/.pi/agent/npm/node_modules" \
PI_TMUX_IMAGES_ROOT="$HOME/.pi/agent/npm/node_modules/pi-tmux-images" \
  node --test --import tsx patches/pi-tmux-images-0.2.0/*.test.ts
```

`placement-lifecycle.test.ts` validates capability-only and shared-handle-only lifecycles. `pane-passthrough-policy.test.ts` checks pane-effective policy and fail-closed cases. `stage-c-replay.test.ts` proves both guarded upgrade stages, pristine apply, idempotence, and unknown-edit refusal. `stage-c-behavior.test.ts` covers byte-exact 8/16-bit PNG wire bytes, wrapped automatic failures, coordination gating, incremental restore, recent-20 eviction, clear, both factory orders, and width-16/40 notices. `stage-c-provenance.test.ts` covers builtin-original proof, resize relation, wrapper/missing/changed rejection, restore, alpha/dimensions, immutable raw blocks, and wrapped provenance notices. The real complete-chain visual fixture is `test/private-fixture/duplicate-read/`.

## Boundary of the capability repair

Effective `allow-passthrough=all` forwards arbitrary DCS passthrough from invisible panes, not only image commands. It still requires a ready, nonsuspended attached client whose session contains the window; it is not storage for detached or future clients. `on` remains visibility-gated. Choosing the policy remains an operator decision outside this patch.
