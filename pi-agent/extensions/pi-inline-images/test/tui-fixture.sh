#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
FIXTURE="$ROOT/test/fixtures/color-block.png"
TMP=$(mktemp -d "${TMPDIR:-/tmp}/pi-inline-images-tui.XXXXXX")
cleanup() { rm -rf -- "$TMP"; }
trap cleanup EXIT INT TERM
mkdir -p "$TMP/agent" "$TMP/sessions"
SESSION="$TMP/sessions/fixture.jsonl"
NOW=2026-09-10T00:00:00.000Z
cat >"$SESSION" <<EOF
{"type":"session","version":3,"id":"11111111-1111-4111-8111-111111111111","timestamp":"$NOW","cwd":"$ROOT"}
{"type":"message","id":"a1b2c3d4","parentId":null,"timestamp":"$NOW","message":{"role":"assistant","content":[{"type":"text","text":"Before fixture\\n\\n![red/blue fixture]($FIXTURE)\\n\\nAfter fixture"}],"api":"fixture","provider":"none","model":"none","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","timestamp":0}}
EOF

cat <<'EOF'
Opening an isolated, offline Pi TUI with a prebuilt assistant session. No prompt or
provider call is made. Run this inside the independent Ghostty/tmux pane used for
pixel validation; verify the red/blue image is between the two labels, resize the
pane, then press Ctrl+D. The harness does not send keys or touch the active desktop.
EOF
PI_CODING_AGENT_DIR="$TMP/agent" PI_OFFLINE=1 pi \
  --offline --no-extensions --extension "$ROOT/index.ts" \
  --no-skills --no-prompt-templates --no-themes --no-context-files --no-tools \
  --session-dir "$TMP/sessions" --session "$SESSION"
