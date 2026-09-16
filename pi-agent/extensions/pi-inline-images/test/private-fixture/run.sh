#!/usr/bin/env bash
set -euo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
REPO=$(git -C "$ROOT" rev-parse --show-toplevel)
OUT=${1:?usage: run.sh OUTPUT_DIR}
XVFB_BIN=${XVFB_BIN:-/tmp/pi-inline-pixel-lane-20260910192606/root/usr/bin/Xvfb}
XWD_BIN=${XWD_BIN:-/usr/bin/xwd}
PI_ROOT=${PI_ROOT:-$HOME/.nvm/versions/node/v22.23.1/lib/node_modules/@earendil-works/pi-coding-agent}
OLD_INSTALLED=${PI_TMUX_IMAGES_ROOT:-$HOME/.pi/agent/npm/node_modules/pi-tmux-images}
REPLAY="$ROOT/patches/pi-tmux-images-0.2.0/replay-stage-c.sh"
BUILD="$ROOT/test/private-fixture/build-session.mjs"
ANALYZE="$ROOT/test/private-fixture/analyze-wire.py"
CAPTURE="$ROOT/test/private-fixture/capture.py"

[ ! -e "$OUT" ] || { echo "refusing existing output directory: $OUT" >&2; exit 1; }
[ -x "$XVFB_BIN" ] || { echo "missing private Xvfb: $XVFB_BIN" >&2; exit 1; }
[ -x "$XWD_BIN" ] || { echo "missing xwd: $XWD_BIN" >&2; exit 1; }
[ -x /usr/bin/ghostty ] || { echo "missing Ghostty" >&2; exit 1; }
[ -x /usr/bin/tmux ] || { echo "missing tmux" >&2; exit 1; }
[ -x /usr/bin/dbus-run-session ] || { echo "missing dbus-run-session" >&2; exit 1; }
[ -x /usr/bin/ffmpeg ] || { echo "missing ffmpeg for XWD conversion" >&2; exit 1; }
mkdir -p "$OUT" "$OUT/work" "$OUT/artifacts" "$OUT/logs" "$OUT/config/ghostty" "$OUT/cache" "$OUT/state" "$OUT/data" "$OUT/empty-share" "$OUT/runtime" "$OUT/fb"
chmod 700 "$OUT/runtime"
WORK="$OUT/work"
SOCK_DIR=$(mktemp -d /tmp/pi-inline-c3-tmux.XXXXXX)
SOCK="$SOCK_DIR/tmux.sock"
PTY_LOG="$OUT/artifacts/pty-output.log"
OLD_COPY="$WORK/pi-tmux-images-0.2.0"
cp -a "$OLD_INSTALLED" "$OLD_COPY"
"$REPLAY" apply "$OLD_COPY" >"$OUT/artifacts/replay-apply.txt"
"$REPLAY" check "$OLD_COPY" >"$OUT/artifacts/replay-check.txt"
mkdir -p "$OLD_COPY/node_modules/@earendil-works"
ln -s "$PI_ROOT" "$OLD_COPY/node_modules/@earendil-works/pi-coding-agent"
ln -s "$PI_ROOT/node_modules/@earendil-works/pi-tui" "$OLD_COPY/node_modules/@earendil-works/pi-tui"
ln -s "$HOME/.pi/agent/npm/node_modules/sharp" "$OLD_COPY/node_modules/sharp"
node "$BUILD" "$WORK" >"$OUT/artifacts/session-path.txt"
SESSION=$(cat "$OUT/artifacts/session-path.txt")
MANIFEST="$WORK/manifest.json"
EXPECTED=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["expectedUploadsPerViewer"])' "$MANIFEST")

DISPLAY_NUM=
for number in $(seq 190 219); do
  if [ ! -S "/tmp/.X11-unix/X$number" ] && [ ! -e "/tmp/.X${number}-lock" ]; then DISPLAY_NUM=":$number"; break; fi
done
[ -n "$DISPLAY_NUM" ] || { echo "no private X display number available" >&2; exit 1; }
export DISPLAY="$DISPLAY_NUM"
XVPID= GHOST_PID= PANE_ID=
cleanup() {
  set +e
  if [ -n "${GHOST_PID:-}" ]; then kill -- "-$GHOST_PID" 2>/dev/null || true; wait "$GHOST_PID" 2>/dev/null || true; fi
  env -u TMUX tmux -S "$SOCK" kill-server 2>/dev/null || true
  rm -rf "$SOCK_DIR"
  if [ -n "${XVPID:-}" ]; then kill "$XVPID" 2>/dev/null || true; wait "$XVPID" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM
"$XVFB_BIN" "$DISPLAY_NUM" -screen 0 1200x900x24 -nolisten tcp -noreset -fbdir "$OUT/fb" >"$OUT/logs/xvfb.log" 2>&1 &
XVPID=$!
for _ in $(seq 1 200); do [ -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ] && break; sleep 0.05; done
[ -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ] || { echo "Xvfb did not become ready" >&2; exit 1; }

cat >"$WORK/run-pi.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec script -q -f -c 'env PI_CODING_AGENT_DIR="$WORK/agent" PI_OFFLINE=1 pi --offline --no-extensions --extension "$OLD_COPY/extensions/index.ts" --extension "$ROOT/index.ts" --no-skills --no-prompt-templates --no-themes --no-context-files --no-tools --session-dir "$WORK/sessions" --session "$SESSION"' "$PTY_LOG"
EOF
chmod 700 "$WORK/run-pi.sh"
printf '%q ' env PI_CODING_AGENT_DIR="$WORK/agent" PI_OFFLINE=1 pi --offline --no-extensions --extension "$OLD_COPY/extensions/index.ts" --extension "$ROOT/index.ts" --no-skills --no-prompt-templates --no-themes --no-context-files --no-tools --session-dir "$WORK/sessions" --session "$SESSION" >"$OUT/artifacts/pi-command.txt"
printf '\n' >>"$OUT/artifacts/pi-command.txt"
CREATED=$(env -u TMUX TERM_PROGRAM=ghostty GHOSTTY_RESOURCES_DIR=/usr/share/ghostty tmux -f /dev/null -S "$SOCK" new-session -d -P -F '#{pane_id}' -s fixture -x 150 -y 84 -- "$WORK/run-pi.sh")
case "$CREATED" in %*) PANE_ID=$CREATED ;; *) echo "invalid fixture pane: $CREATED" >&2; exit 1 ;; esac
env -u TMUX tmux -S "$SOCK" set-option -g allow-passthrough all
for _ in $(seq 1 300); do grep -a -q 'NATIVE-MARKDOWN-END' "$PTY_LOG" 2>/dev/null && break; sleep 0.05; done
grep -a -q 'NATIVE-MARKDOWN-END' "$PTY_LOG" || { echo "Pi fixture did not render the prepared session" >&2; exit 1; }
cp "$PTY_LOG" "$OUT/artifacts/pre-attach-pty.log"
PRE=$(python3 "$ANALYZE" "$OUT/artifacts/pre-attach-pty.log" --count)
[ "$PRE" -eq 0 ] || { echo "hidden-first phase uploaded $PRE images" >&2; exit 1; }

env -u TMUX tmux -S "$SOCK" capture-pane -p -e -t "$PANE_ID" -S -120 >"$OUT/artifacts/pane-hidden.txt"
start_ghostty() {
  setsid env DISPLAY="$DISPLAY" GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 GDK_DEBUG=gl-disable-gles,gl-no-fractional,vulkan-disable GDK_DISABLE=color-mgmt \
    GTK_USE_PORTAL=0 GIO_USE_VFS=local NO_AT_BRIDGE=1 GHOSTTY_RESOURCES_DIR=/usr/share/ghostty XDG_RUNTIME_DIR="$OUT/runtime" XDG_CONFIG_HOME="$OUT/config" XDG_CACHE_HOME="$OUT/cache" XDG_STATE_HOME="$OUT/state" XDG_DATA_HOME="$OUT/data" XDG_DATA_DIRS="$OUT/empty-share" \
    dbus-run-session -- ghostty --gtk-single-instance=false --window-save-state=never --window-width=1080 --window-height=820 --font-size=8 \
    -e env -u TMUX tmux -S "$SOCK" attach-session -t fixture >"$OUT/logs/ghostty-$1.log" 2>&1 &
  GHOST_PID=$!
}
stop_ghostty() {
  kill -- "-$GHOST_PID" 2>/dev/null || true
  wait "$GHOST_PID" 2>/dev/null || true
  GHOST_PID=
  for _ in $(seq 1 200); do
    attached=$(env -u TMUX tmux -S "$SOCK" display-message -p -t fixture '#{session_attached}' 2>/dev/null || echo 0)
    [ "$attached" = 0 ] && break
    sleep 0.05
  done
  [ "${attached:-1}" = 0 ]
}
wait_attached() {
  for _ in $(seq 1 400); do
    attached=$(env -u TMUX tmux -S "$SOCK" display-message -p -t fixture '#{session_attached}' 2>/dev/null || echo 0)
    [ "$attached" != 0 ] && return 0
    sleep 0.05
  done
  return 1
}
wire_count() { python3 "$ANALYZE" "$PTY_LOG" --count; }
wait_uploads() {
  target=$1
  for _ in $(seq 1 800); do count=$(wire_count); [ "$count" -ge "$target" ] && return 0; sleep 0.05; done
  return 1
}
capture_screen() {
  name=$1
  min_colorful=${2:-0}
  "$XWD_BIN" -silent -root -display "$DISPLAY" -out "$OUT/artifacts/$name.xwd"
  /usr/bin/ffmpeg -v error -y -i "$OUT/artifacts/$name.xwd" "$OUT/artifacts/$name.png"
  python3 "$CAPTURE" "$OUT/artifacts/$name.png" "$OUT/artifacts/$name.png" --min-colorful-pixels "$min_colorful" >"$OUT/artifacts/$name.capture.json"
  rm "$OUT/artifacts/$name.xwd"
}

start_ghostty first
wait_attached || { echo "first Ghostty client did not attach" >&2; exit 1; }
wait_uploads "$EXPECTED" || { echo "first viewer did not receive $EXPECTED uploads" >&2; exit 1; }
sleep 1
FIRST=$(wire_count)
[ "$FIRST" -eq "$EXPECTED" ] || { echo "first viewer upload count $FIRST != $EXPECTED" >&2; exit 1; }
capture_screen first-client-native-markdown 60000
env -u TMUX tmux -S "$SOCK" capture-pane -p -e -t "$PANE_ID" -S -240 >"$OUT/artifacts/pane-first-client.txt"
env -u TMUX tmux -S "$SOCK" send-keys -t "$PANE_ID" PPage
sleep 1
capture_screen first-client-read-previews
sleep 3.5
STABLE=$(wire_count)
[ "$STABLE" -eq "$FIRST" ] || { echo "stable viewer repeated uploads: $FIRST -> $STABLE" >&2; exit 1; }

mapfile -t CLIENT_NAMES < <(env -u TMUX tmux -S "$SOCK" list-clients -F '#{client_name}')
[ "${#CLIENT_NAMES[@]}" -eq 1 ] || { echo "expected one attached fixture client" >&2; exit 1; }
CLIENT_NAME=${CLIENT_NAMES[0]}
FIXTURE_WINDOW=$(env -u TMUX tmux -S "$SOCK" display-message -p -t "$PANE_ID" '#{window_id}')
HOLD_WINDOW=$(env -u TMUX tmux -S "$SOCK" new-window -d -P -F '#{window_id}' -t fixture -n fixture-hold 'sleep 300')
env -u TMUX tmux -S "$SOCK" switch-client -c "$CLIENT_NAME" -t "$HOLD_WINDOW"
sleep 3.5
HIDDEN=$(wire_count)
[ "$HIDDEN" -eq "$FIRST" ] || { echo "same attached hidden viewer changed uploads: $FIRST -> $HIDDEN" >&2; exit 1; }
env -u TMUX tmux -S "$SOCK" list-clients -F '#{client_name}\t#{client_pid}\t#{window_id}\t#{client_flags}' >"$OUT/artifacts/first-client-hidden-snapshot.txt"
env -u TMUX tmux -S "$SOCK" switch-client -c "$CLIENT_NAME" -t "$FIXTURE_WINDOW"
sleep 3.5
RESHOWN=$(wire_count)
[ "$RESHOWN" -eq "$FIRST" ] || { echo "same attached reshown viewer repeated uploads: $FIRST -> $RESHOWN" >&2; exit 1; }
env -u TMUX tmux -S "$SOCK" send-keys -t "$PANE_ID" End
sleep 1
capture_screen first-client-after-hide-show 60000

stop_ghostty
start_ghostty second
wait_attached || { echo "second Ghostty client did not attach" >&2; exit 1; }
TARGET=$((EXPECTED * 2))
wait_uploads "$TARGET" || { echo "new viewer did not receive a full resend" >&2; exit 1; }
sleep 1
SECOND=$(wire_count)
[ "$SECOND" -eq "$TARGET" ] || { echo "second viewer upload count $SECOND != $TARGET" >&2; exit 1; }
capture_screen second-client-resend 60000
sleep 3.5
SECOND_STABLE=$(wire_count)
[ "$SECOND_STABLE" -eq "$SECOND" ] || { echo "second stable viewer repeated uploads: $SECOND -> $SECOND_STABLE" >&2; exit 1; }
python3 "$ANALYZE" "$PTY_LOG" --out "$OUT/artifacts/wire" --manifest "$MANIFEST" >"$OUT/artifacts/wire-summary.stdout.json"
python3 - "$OUT/artifacts/wire/wire-summary.json" "$EXPECTED" <<'PY'
import json,sys
summary=json.load(open(sys.argv[1])); expected=int(sys.argv[2])
assert summary["uploads"] == expected * 2, summary
assert summary["largeMatches"] == 2, summary
assert not summary["errors"], summary
assert sum(1 for group in summary["groups"] if group["width"] == 1920 and group["height"] == 1080) == 2, summary
PY
env -u TMUX tmux -S "$SOCK" capture-pane -p -e -t "$PANE_ID" -S -240 >"$OUT/artifacts/pane-second-client.txt"
python3 - "$OUT/artifacts/pane-first-client.txt" "$OUT/artifacts/pane-second-client.txt" "$OUT/artifacts/read-preview-visible-summary.json" <<'PY'
import json,sys
summaries=[]
for path in sys.argv[1:3]:
    text=open(path,encoding="utf-8",errors="replace").read()
    summary={"path":path,"expiredNotices":text.count("Expired from"),"withheldNotices":text.count("Custom bitmap withheld"),"originalNotices":text.count("Original resolution unavailable/unverified"),"placeholderGlyphs":text.count("\U0010eeee")}
    assert summary["expiredNotices"] == 4, summary
    assert summary["withheldNotices"] == 0, summary
    assert summary["originalNotices"] >= 1, summary
    assert summary["placeholderGlyphs"] > 1840, summary
    summaries.append(summary)
open(sys.argv[3],"w").write(json.dumps(summaries,indent=2)+"\n")
PY
python3 - "$OUT/artifacts/fixture-summary.json" "$DISPLAY" "$PRE" "$FIRST" "$STABLE" "$HIDDEN" "$RESHOWN" "$SECOND" "$SECOND_STABLE" <<'PY'
import json,sys
path,display,*values=sys.argv[1:]
keys=["preAttachUploads","firstClientUploads","firstStableUploads","sameClientHiddenUploads","sameClientReshownUploads","secondClientUploads","secondStableUploads"]
open(path,"w").write(json.dumps({"display":display,**dict(zip(keys,map(int,values)))},indent=2)+"\n")
PY
stop_ghostty
env -u TMUX tmux -S "$SOCK" send-keys -t "$PANE_ID" Escape C-d 2>/dev/null || true
sleep 0.5
printf 'private fixture passed: %s\n' "$OUT"
