# pi-inline-images

A display-only Pi extension that replaces image references in finalized assistant
Markdown with Kitty Unicode-placeholder grids at the same source position. Pi's
native Markdown component still renders all surrounding text. Session JSONL and
model context retain the original Markdown bytes.

## Requirements

- Pi `0.85.1` (`@earendil-works/pi-coding-agent` / `pi-tui` host peers)
- Node `>=22.19`
- Ghostty, Kitty, or WezTerm with Kitty graphics support
- Under tmux, the originating pane's effective `allow-passthrough` must be `on`
  or `all`. The viewer monitor never uploads while that pane is not visibly
  watched by compatible clients.
- Local extension dependencies installed with the checked-in lock file

The existing `pi-tmux-images` package may remain enabled. This extension does not
import its registrar, renderer, command, or runtime. It allocates IDs through the
host Pi TUI allocator and deletes only IDs it owns.

## Install

```bash
./pi-agent/extensions/pi-inline-images/install.sh
```

The installer runs `npm ci --ignore-scripts` in this source directory, creates
`~/.pi/agent/extensions/pi-inline-images` as a symlink, and adds the exact local
Git exclude rule `/extensions/pi-inline-images` through `git rev-parse --git-path
info/exclude`. It refuses unknown target files/directories/links and does not edit
`settings.json`, tracked `.gitignore`, Pi core, or the existing image package.
Repeated runs are idempotent. Run `/reload` yourself afterward.

## Behavior

- Parses finalized assistant Markdown with a position-aware remark/mdast tree plus
  GFM table nodes; fenced/indented code and inline code are excluded. Inline,
  reference-style, list, and quote image syntax retain source order. Images
  embedded in a sentence become a block at that point. Table cells retain their
  columns and show an explicit unsupported notice instead of a bitmap grid.
- Loads and validates resources in source order during the async `message_end`/restore
  preparation hook. Suitable unoriented PNGs retain their exact source bytes;
  other supported formats are auto-oriented and PNG-encoded at full resolution,
  without resizing or palette quantization. Images wait visibly in place until a
  compatible viewer is present.
- After each upload, preparation creates a finite catalog of every distinct
  `(columns, rows)` produced for available widths 1–80. Upload and catalog writes
  finish before the finalized grid can render, so portable Kitty order is always
  upload → virtual placement → placeholder cells. The synchronous transformer
  only selects an existing placement ID; 1000 stable/width-changing renders issue
  no file, decode, base64, transport, queue, placement, or timer work.
- Supports absolute/relative paths, `file:` URLs (empty/localhost host), HTTP(S),
  and PNG/JPEG/WebP data URLs. Encoded input is limited to 20 MiB, decoded input to
  32 MiPixels, and HTTP fetches to 10 seconds. Display geometry remains at most
  80×24 cells and never enlarges an image relative to its original pixel size.
- Failures remain visible at the original Markdown position: unsupported or
  missing sources, invalid content, preview/resident/transport limits, sink
  errors, drain timeout, unavailable Kitty/tmux passthrough, and the active-image
  cap are not silently converted to blank grids. Quiet mode `q=2` suppresses
  terminal responses; writable acceptance is never described as terminal receipt,
  and there is no speculative `ENOENT` retry.
- tmux `allow-passthrough all` forwards arbitrary DCS passthrough from invisible
  panes, not only image traffic. It still requires a ready, nonsuspended attached
  client whose session contains the window; it is not a durable upload queue for
  detached clients. Choose that policy deliberately and scope it appropriately.
- Current-branch assistant text is restored on session start. Same-runtime tree
  reconciliation cancels stale queued work but reuses unchanged prepared/uploaded
  resources, avoiding gratuitous transcode/upload. Shutdown or session replacement
  invalidates late loaders, cancels unsent jobs, orders owned deletion after any
  accepted-false write drains, and removes transport listeners/timers. A new
  extension factory starts with no terminal-cache identity and uploads again.
- Occurrence identity includes cwd, complete text-block Markdown, ordinal, URL,
  and source-content hash. Changed bytes receive a distinct immutable image ID;
  bytes/geometry behind an old cached grid are never overwritten. An already
  rendered same-width Markdown component can retain its cached historical grid.
- Placement geometry is bound to the exact pixel cell dimensions captured during
  preparation. If a later font/DPI change changes them, render emits an explicit
  reload-required notice instead of selecting a missing placement or silently
  cropping. Reload rebuilds the catalog for the new cell aspect ratio; Kitty fits
  the image without distortion inside each prepared rectangle.

## Full-resolution and transport limits

Preview geometry and upload fidelity are separate. Layout remains at most 80×24
cells, while upload retains every source pixel. Supported limits are explicit:

- **20 MiB** encoded input, **32 MiPixels** decoded input, and **32 MiB** full PNG
- **64 MiB** aggregate resident PNG state and **64** immutable resources
- **44 MiB** maximum atomic upload-plus-catalog write, **96 MiB** admitted wire
  budget including an accepted `write(false)` awaiting drain, and **64** queued jobs
- **8 MiB/s** sustained transaction pacing after one bounded burst, at least 50 ms
  between starts, and a 60-second drain deadline

Each upload reserves its exact wire size before constructing base64. All 4096-byte
Kitty chunks and the complete placement catalog are one Buffer write; continuations
contain only `m`, and renders never emit graphics commands.

When resources exist, a single 1.5-second monitor checks the current tmux window,
pane passthrough policy, and attached client identities. No viewer, failed snapshot,
or an incompatible visible client keeps PNG bytes pending with zero upload. A hidden
viewer returning with the same identity does not re-upload; a new identity starts a
new receiver epoch and receives complete uploads. Meaningful preparation/viewer
changes coalesce one public TUI invalidate/requestRender wakeup through a nonvisual
widget; stable polls do not repaint.


Precreating bounded placements is lower risk than dynamic placement: official
Kitty ordering requires a virtual placement before its placeholder cells, while
Pi exposes no public way to invalidate a cached Markdown grid after a later
placement. The catalog makes render pure without a new core hook or stdout
interception.

`write(false)` means Node accepted the complete current transaction. Drain
observation is armed before calling the potentially reentrant sink; a synchronous
`drain` cannot be lost, and reentrant enqueue cannot recursively pump another
write. Preparation also waits for `drain` before returning, so native TUI output
cannot overtake accepted-false graphics bytes. Generation cancellation rejects
queued work but does not cancel sink flow control: an internally observed false
return remains blocked until actual `drain`, `error`, or `close`, regardless of an
optional `writableNeedDrain` value. Accepted bytes cannot be retracted. Sink
error, close, or slow drain rejects every unsent job and a fatal transport is not
silently restarted.

Cleanup admits and drains one owned delete at a time, so it remains within the
configured job/byte queue limits (including a one-job queue). Ownership and
resident accounting are released only after that delete is accepted and drained;
a cleanup error rejects explicitly and retains all unresolved IDs for diagnosis
or a later owner decision. Final teardown (`clear(true)`) disposes in `finally`,
so even a failed delete releases every drain/error/close listener and timer;
ordinary `clear(false)` keeps the transport reusable.

These limits reduce and bound extension-generated traffic. They do **not** prove
or claim that Ghostty's GTK 4 Wayland `EAGAIN` process exit has been cured; that
causal question requires a separate controlled Wayland test.

## Validation

```bash
cd pi-agent/extensions/pi-inline-images
npm run check
```

The provider-free captured-sink integration harness can be run alone:

```bash
node --test --import tsx test/integration.test.ts
```

It exercises a real large Sharp preview, many distinct ~384 KiB PNG previews,
actual Kitty APC decoding/byte counts, multi-image FIFO, placement-before-grid,
80-entry/4480-byte catalog bounds, 1000 pure width-changing renders, custom
placement/image-high-bit mapping, cell-metric change failure, old-plugin command
coexistence, aggregate rejection, same-runtime reuse/new-factory reupload,
late-load and queued-catalog cancellation, accepted-false ordering, and
listener/timer cleanup. Other tests retain installed Pi native Markdown rendering, raw message
preservation, parser/reference/table behavior, cancelled-switch behavior, immutable
cached resources, and isolated installer ownership.

For a later independent pixel check, use a disposable private Ghostty/tmux setup;
do not run the fixture against an active desktop. This Stage 2 source validation
performs no GUI action.
