# Complete read-chain duplicate fixture

This private provider-free fixture persists two distinct assistant `read` tool calls, two matching tool results containing identical PNG bytes, and two matching `pi-tmux-images.preview` entries. It launches offline Pi 0.85.1 in private Xvfb/DBus/Ghostty state with a disposable replay-patched old package and the current inline extension.

```bash
cd pi-agent/extensions/pi-inline-images
OUT=/tmp/pi-inline-duplicate-read
XVFB_BIN=/path/to/Xvfb test/private-fixture/duplicate-read/run.sh "$OUT"
```

`analyze.py` verifies the exact `(toolCallId, blockIndex)` chains and counts large colorful bitmap regions in the final raw screenshot. Acceptance is exactly two visible bitmap occurrences for the two distinct calls, even though their bytes are identical. The fixture preserves every raw session/tool block and makes no provider, user-display, settings, core, or ShellGate call.
