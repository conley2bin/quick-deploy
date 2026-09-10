# pi-inline-images

A display-only Pi extension that replaces image references in finalized assistant
Markdown with Kitty Unicode-placeholder grids at the same source position. Pi's
native Markdown component still renders all surrounding text. Session JSONL and
model context retain the original Markdown.

## Requirements

- Pi `0.85.1` (`@earendil-works/pi-coding-agent` / `pi-tui` host peers)
- Node `>=22.19`
- Ghostty, Kitty, or WezTerm with Kitty graphics support
- Under tmux, `allow-passthrough` must be `on`
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

- Parses finalized assistant Markdown with Marked; fenced code and inline code are
  excluded. Inline, reference-style, list, and quote image syntax retain source
  order. Images embedded in a sentence become a block at that point.
- Loads resources asynchronously at `message_end`; the synchronous display
  transformer performs no file, network, or decode work.
- Supports absolute/relative paths, `file:` URLs (empty/localhost host), HTTP(S),
  and PNG/JPEG/WebP data URLs. Encoded input is limited to 20 MB, decoded images
  to 32 MP, HTTP fetches to 10 seconds, and displayed geometry to 80×24 cells.
- Failures remain visible in place. Unsupported schemes/formats, bad content,
  missing files, unavailable Kitty/tmux passthrough, and the 64-active-image cap
  are never silently dropped.
- Current-branch assistant text is reloaded on session start and tree navigation.
  Session switch/shutdown clears owned terminal images. Resize redraw deletes an
  old owned placement before creating its new geometry.
- Occurrence identity includes cwd, complete text-block Markdown, ordinal, and
  URL, so the same path in different replies is not globally suppressed. Because
  the public transformer has no message ID, byte-identical repeated text blocks
  share identity; reloading a changed local file updates all such identical
  occurrences rather than preserving historical pixels.

## Validation

```bash
npm run check
```

Tests include the installed Pi native Markdown renderer at widths 12/7/6, resource
loading, code exclusion, ordering, nested prefixes, explicit capacity failures,
resize cleanup, active-branch restore, and an isolated installer HOME with spaces.

For an independent, provider-free pixel check, open a disposable Ghostty/tmux pane
(not the active desktop) and run:

```bash
./pi-agent/extensions/pi-inline-images/test/tui-fixture.sh
```

It opens an offline Pi TUI from a deterministic prebuilt session and never sends a
prompt, model request, or synthetic key input. Confirm the 99-byte red/blue PNG is
between “Before fixture” and “After fixture”, resize the pane, then exit with
Ctrl+D.
