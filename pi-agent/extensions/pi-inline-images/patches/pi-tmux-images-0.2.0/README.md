# pi-tmux-images 0.2.0 local repairs

## Scope and mechanism

This directory carries two replayable local repairs for the installed `pi-tmux-images` 0.2.0 Kitty Unicode-placeholder implementation. `placement-redraw.patch` changes only `src/runtime.ts`; `pane-passthrough-policy.patch` changes only `src/capabilities.ts`.

### Placement refresh

`emitPlaceholder()` sends Kitty commands with `q=2`, so the terminal does not acknowledge an upload or placement. The unmodified runtime cached placement geometry immediately after its first `a=p` write and suppressed every later same-geometry placement. If tmux/Ghostty lost that first placement, subsequent TUI renders kept returning the Unicode grid but never repaired the missing terminal placement; the result was a correctly sized blank slot.

The repair keeps PNG upload deduplication and resize cleanup unchanged. It re-emits this runtime-owned `a=p` placement on every render. A geometry change still emits `a=d,d=i` before the new placement. A same-geometry redraw emits one placement command, does not re-upload bytes, does not delete a placement, and does not touch any other image ID.

No separate rehydrate invalidation was added. The extension's `rebuild()` path already calls `runtime.clear()` immediately before `runtime.rehydrate()`, which deletes owned Kitty image IDs and clears `uploaded` and `placements`. Changing `rehydrate()` would widen the repair without affecting the actual lifecycle.

### Pane-effective passthrough policy

A controlled tmux 3.4 experiment reproduced the upload failure: with effective `allow-passthrough=on`, a hidden pane emitted complete `a=t` uploads and `a=p` placements but Ghostty painted zero pixels; a later placement-only redraw remained blank. Visible clear+rehydrate restored the images. With effective `all`, hidden-first rendered both fixtures.

The original probe used `tmux show-options -gv allow-passthrough`, so it ignored pane overrides and rejected the valid `all` value. The policy patch instead runs:

```bash
tmux show-options -Apv -t "$TMUX_PANE" allow-passthrough
```

In tmux 3.4, `-p -t` selects the originating pane and `-A` resolves inherited pane/window/global-window values. The probe accepts `on`, `all`, and the previously accepted boolean aliases. It fails closed when `TMUX_PANE` is missing or malformed, the command errors or times out, or output is disabled/unknown. The result remains cached once at runtime construction; no tmux option is changed by either patch.

## Provenance

- npm package: `pi-tmux-images@0.2.0`
- Registry tarball: `https://registry.npmjs.org/pi-tmux-images/-/pi-tmux-images-0.2.0.tgz`
- Published integrity: `sha512-6a7iaYbN9WRbkLMgiYyrTjMQ34npFyBDPtzpw0GLiOIXjjYglBB2aQ8gaHbpoIBtw2qS9NO/4nf8iS1kELoY5Q==`
- Tarball SHA-256: `033c89008eaae419634cd51297a2bb4fd5c759fcb2a32fae04a6835881246db4`
- Upstream tag: `v0.2.0`, commit `cab0433b173a3e09fae693240a63432d4d35c757`
- Pristine/installed-before `src/runtime.ts` SHA-256: `4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d`
- Placement-patched/live `src/runtime.ts` SHA-256: `7db69da01c21617b6ceed3f6f67ef7cc5d9428ee850008accd6dbcbcfa2bca25`
- Pristine/installed-before `src/capabilities.ts` SHA-256: `b9a498a9839ae04909995e530a8c052d0490653eadde7c7a5ec1941ea27357c8`
- Policy-patched/live `src/capabilities.ts` SHA-256: `c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a`
- `placement-redraw.patch` SHA-256: `80f06f118f7dc09511cbe29854d53f031e0e3fc667e44bfdb342edecbfb233bd`
- `pane-passthrough-policy.patch` SHA-256: `ae4a74b8488ae6d693d6c183f68fc73b0cfbbfbe9854ba9b20625715b7fd7be3`

Before the first repair, the installed 17-file package was copied to `/tmp/pi-tmux-images-0.2.0-installed-before`, hashed into `installed-before.sha256`, and compared recursively with the integrity-verified npm tarball; there was no difference. Before the policy repair, `src/capabilities.ts` still matched that pristine manifest and `src/runtime.ts` matched the placement-patched hash. `installed-after.sha256` records the live package after both repairs. Comparing the manifests shows only those two source files changed. Package version, metadata, and lock data were not changed.

## Replay

Apply from the root of an unmodified `pi-tmux-images@0.2.0` package:

```bash
patch -p1 < /path/to/placement-redraw.patch
patch -p1 < /path/to/pane-passthrough-policy.patch
sha256sum src/runtime.ts src/capabilities.ts
# expected runtime.ts:      7db69da01c21617b6ceed3f6f67ef7cc5d9428ee850008accd6dbcbcfa2bca25
# expected capabilities.ts: c7272ee59ebc5f78c96c1740fcbcdf57cdc2480475241292cb91d3e158e0625a
```

The patches are generated directly between the integrity-verified npm tarball's corresponding source and the live repaired files. Package reinstall/update can overwrite either local repair; re-verify the upstream version and pristine hashes before replaying both.

## Regression test

`placement-redraw.test.ts` is a controlled test that imports a package root named by `PI_TMUX_IMAGES_ROOT`. It verifies:

1. after two images are initially rendered, redrawing the second emits exactly its existing `a=p` command;
2. the redraw emits no upload/delete and contains no older image ID;
3. resize still deletes the old placement before placing the new geometry;
4. a later same-size render refreshes only that new placement.

Example against the live package from the quick-deploy repository root:

```bash
NODE_PATH="$(npm root -g)/@earendil-works/pi-coding-agent/node_modules:$HOME/.pi/agent/npm/node_modules" \
PI_TMUX_IMAGES_ROOT="$HOME/.pi/agent/npm/node_modules/pi-tmux-images" \
  pi-agent/extensions/pi-inline-images/node_modules/.bin/tsx --test \
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/placement-redraw.test.ts \
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/pane-passthrough-policy.test.ts
```

The test fails twice against the pristine snapshot because both same-geometry redraws emit zero commands, and passes twice against the repaired live runtime.

`pane-passthrough-policy.test.ts` imports the same selected package root. It verifies the exact pane-effective command, acceptance of `on`/`all` and preserved aliases, and fail-closed behavior for off/unknown output, command failure, missing or malformed `TMUX_PANE`, and thrown errors.

For package-wide validation, both repaired source files were overlaid onto a clean checkout of upstream `v0.2.0`. The one upstream assertion that deliberately required zero same-geometry redraw bytes was changed in that disposable checkout to require one placement and no duplicate upload. `npm run check` then passed formatting, lint, TypeScript, all 27 tests, and the package dry-run build. The durable focused contracts are the two local regressions above; the published npm tarball intentionally contains no tests.

## Boundary

This repair prevents the directly reproduced attached-client hidden-pane drop only when the effective pane policy is deliberately set to `all`. That setting forwards arbitrary DCS passthrough from invisible panes—not only image commands—and still requires a ready, nonsuspended attached client whose session contains the window. It is not a durable queue for detached or future clients.

No upload retry was added. The evidence identifies tmux's visibility gate, and `all` removes that gate; an unconditioned retry can repeatedly retransmit PNG bytes while still hidden under `on`, then poison the same optimistic cache again. A retry policy would need separate evidence and a reliable visibility/attachment epoch. Already-poisoned runtime state still requires visible clear+rehydrate (normally `/reload` or restart). The placement patch remains unchanged and continues to address lost placement while uploaded bytes exist. No live tmux policy, UI, or user session was changed here.
