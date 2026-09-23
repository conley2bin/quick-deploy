# Bounded Ghostty protocol probes (2026-09-23, Ghostty 1.3.1)

`probe-ghostty.py <mode> <png> <result-file>` prints kitty commands to stdout and
writes its observations to the result file. Run it in a **temporary** Ghostty window
so the user's panes are never involved:

```bash
ghostty --gtk-single-instance=false -e python3 test/probe-ghostty.py files fixture.png /tmp/probe.result
```

Modes: `direct` (chunked upload + placement + query), `files` (per-id oracle),
`tmux`/`tmux2`/`tmux3` (cells and passthrough through an attached tmux client).

## Measured results

| Question | Method | Result |
| --- | --- | --- |
| Does Ghostty render Unicode-placeholder cells? | `files` mode: cells for an id that was never uploaded | ✅ 10 × `warning(renderer_image): missing image for virtual placement, ignoring image_id=3` |
| Does direct chunked upload work? | `files` mode id 1 (uploaded) vs id 3 (never uploaded) | ✅ 0 warnings for id 1, 10 for id 3 |
| Does Kitty regular-file transfer (`t=f`) work? | `files` mode id 2 (real path) vs id 4 (missing path) | ✅ 0 warnings for id 2; id 4 gave 10 warnings + `warning(kitty_gfx): failed to get absolute path: error.FileNotFound` + `EINVAL: invalid data` (oracle validated) |
| Does Ghostty answer kitty commands? | `q=2` uploads and `a=q` queries, stdin in raw mode | ❌ `responses=bytearray(b'')` — no responses at all |
| Does passthrough delivery work through tmux? | wrapped `a=p` inside an attached, visible pane | ✅ `warning(kitty_gfx): erroneous kitty graphics response: ENOENT: image not found` |
| Do placeholder cells survive tmux? | wrapped placement + unwrapped cells (grid path) and wrapped cells (passthrough path) | ⚠️ no render warning for either variant in the synchronized run; `tmux capture-pane` also shows 0 cells. Cells may not reach the outer terminal through tmux 3.4 here. |

## Gap

All of the above is **byte-level / log-level** evidence. No probe verified *pixels* on
screen: rendering a real image through the tmux passthrough path and confirming it is
visible still requires a human look or an image-diff harness against a controlled
window. Passthrough is also visibility-gated — an early probe run that printed before
the client attached delivered nothing, which is why the probes synchronize first.
