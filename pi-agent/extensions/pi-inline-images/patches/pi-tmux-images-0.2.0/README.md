# pi-tmux-images 0.2.0 placement-redraw repair

## Scope and mechanism

This is a local repair for the installed `pi-tmux-images` 0.2.0 Kitty Unicode-placeholder runtime. It changes only `src/runtime.ts`.

`emitPlaceholder()` sends Kitty commands with `q=2`, so the terminal does not acknowledge an upload or placement. The unmodified runtime cached placement geometry immediately after its first `a=p` write and suppressed every later same-geometry placement. If tmux/Ghostty lost that first placement, subsequent TUI renders kept returning the Unicode grid but never repaired the missing terminal placement; the result was a correctly sized blank slot.

The repair keeps PNG upload deduplication and resize cleanup unchanged. It re-emits this runtime-owned `a=p` placement on every render. A geometry change still emits `a=d,d=i` before the new placement. A same-geometry redraw emits one placement command, does not re-upload bytes, does not delete a placement, and does not touch any other image ID.

No separate rehydrate invalidation was added. The extension's `rebuild()` path already calls `runtime.clear()` immediately before `runtime.rehydrate()`, which deletes owned Kitty image IDs and clears `uploaded` and `placements`. Changing `rehydrate()` would widen the repair without affecting the actual lifecycle.

## Provenance

- npm package: `pi-tmux-images@0.2.0`
- Registry tarball: `https://registry.npmjs.org/pi-tmux-images/-/pi-tmux-images-0.2.0.tgz`
- Published integrity: `sha512-6a7iaYbN9WRbkLMgiYyrTjMQ34npFyBDPtzpw0GLiOIXjjYglBB2aQ8gaHbpoIBtw2qS9NO/4nf8iS1kELoY5Q==`
- Tarball SHA-256: `033c89008eaae419634cd51297a2bb4fd5c759fcb2a32fae04a6835881246db4`
- Upstream tag: `v0.2.0`, commit `cab0433b173a3e09fae693240a63432d4d35c757`
- Pristine/installed-before `src/runtime.ts` SHA-256: `4d9aadc3b5f6fd76f3a90cadb071a6b49c66a3e86063285a863e01c0bbd4580d`
- Patched/live `src/runtime.ts` SHA-256: `7db69da01c21617b6ceed3f6f67ef7cc5d9428ee850008accd6dbcbcfa2bca25`
- Patch SHA-256: `80f06f118f7dc09511cbe29854d53f031e0e3fc667e44bfdb342edecbfb233bd`

Before editing, the installed 17-file package was copied to `/tmp/pi-tmux-images-0.2.0-installed-before`, hashed into `installed-before.sha256`, and compared recursively with the integrity-verified npm tarball; there was no difference. `installed-after.sha256` records the live package after repair. Comparing the manifests shows only `src/runtime.ts` changed. Package version and lock data were not changed.

## Replay

Apply from the root of an unmodified `pi-tmux-images@0.2.0` package:

```bash
patch -p1 < /path/to/placement-redraw.patch
sha256sum src/runtime.ts
# expected: 7db69da01c21617b6ceed3f6f67ef7cc5d9428ee850008accd6dbcbcfa2bca25
```

The patch is generated directly between the integrity-verified npm tarball's `src/runtime.ts` and the live repaired file. Package reinstall/update can overwrite this local runtime repair; re-verify the upstream version and pristine hash before replaying it.

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
  pi-agent/extensions/pi-inline-images/patches/pi-tmux-images-0.2.0/placement-redraw.test.ts
```

The test fails twice against the pristine snapshot because both same-geometry redraws emit zero commands, and passes twice against the repaired live runtime.

For package-wide validation, the exact repaired runtime was overlaid onto a clean checkout of upstream `v0.2.0`. The one upstream assertion that deliberately required zero same-geometry redraw bytes was changed in that disposable checkout to require one placement and no duplicate upload. `npm run check` then passed formatting, lint, TypeScript, all 27 tests, and the package dry-run build. The durable focused contract is the local regression above; the published npm tarball intentionally contains no tests.

## Boundary

This repair addresses silent placement loss while the uploaded Kitty image remains available. If the terminal loses the image upload itself, repeated `a=p` cannot reconstruct the PNG; that distinct failure would require evidence before adding bounded re-upload behavior. No live UI or user session was exercised in this change.
