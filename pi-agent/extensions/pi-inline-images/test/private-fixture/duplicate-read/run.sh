#!/usr/bin/env bash
set -euo pipefail
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)
SELF="$ROOT/test/private-fixture/duplicate-read"
OUT=${1:?usage: run.sh OUTPUT_DIR}
XVFB_BIN=${XVFB_BIN:-/tmp/pi-inline-pixel-lane-20260910192606/root/usr/bin/Xvfb}
PI_ROOT=${PI_ROOT:-$HOME/.nvm/versions/node/v22.23.1/lib/node_modules/@earendil-works/pi-coding-agent}
OLD_INSTALLED=${PI_TMUX_IMAGES_ROOT:-$HOME/.pi/agent/npm/node_modules/pi-tmux-images}
REPLAY="$ROOT/patches/pi-tmux-images-0.2.0/replay-stage-c.sh"
[ ! -e "$OUT" ] || { echo "refusing existing output directory: $OUT" >&2; exit 1; }
[ -x "$XVFB_BIN" ] && [ -x /usr/bin/ghostty ] && [ -x /usr/bin/xwd ] && [ -x /usr/bin/ffmpeg ]
mkdir -p "$OUT"/{work,artifacts,logs,config/ghostty,cache,state,data,empty-share,runtime,fb}; chmod 700 "$OUT/runtime"
WORK="$OUT/work"; PTY_LOG="$OUT/artifacts/pty-output.log"; OLD_COPY="$WORK/pi-tmux-images-0.2.0"
cp -a "$OLD_INSTALLED" "$OLD_COPY"; "$REPLAY" apply "$OLD_COPY" >"$OUT/artifacts/replay-apply.txt"; "$REPLAY" check "$OLD_COPY" >"$OUT/artifacts/replay-check.txt"
mkdir -p "$OLD_COPY/node_modules/@earendil-works"
ln -s "$PI_ROOT" "$OLD_COPY/node_modules/@earendil-works/pi-coding-agent"
ln -s "$PI_ROOT/node_modules/@earendil-works/pi-tui" "$OLD_COPY/node_modules/@earendil-works/pi-tui"
ln -s "$HOME/.pi/agent/npm/node_modules/sharp" "$OLD_COPY/node_modules/sharp"
python3 "$SELF/build-session.py" "$WORK" >"$OUT/artifacts/session-path.txt"; SESSION=$(cat "$OUT/artifacts/session-path.txt")
DISPLAY_NUM=
for number in $(seq 220 239); do if [ ! -S "/tmp/.X11-unix/X$number" ] && [ ! -e "/tmp/.X${number}-lock" ]; then DISPLAY_NUM=":$number"; break; fi; done
[ -n "$DISPLAY_NUM" ] || { echo "no private X display available" >&2; exit 1; }; export DISPLAY="$DISPLAY_NUM"
XVPID= GHOST_PID=
cleanup(){ set +e; if [ -n "${GHOST_PID:-}" ]; then kill -- "-$GHOST_PID" 2>/dev/null || true; wait "$GHOST_PID" 2>/dev/null || true; fi; if [ -n "${XVPID:-}" ]; then kill "$XVPID" 2>/dev/null || true; wait "$XVPID" 2>/dev/null || true; fi; }
trap cleanup EXIT INT TERM
"$XVFB_BIN" "$DISPLAY_NUM" -screen 0 1200x900x24 -nolisten tcp -noreset -fbdir "$OUT/fb" >"$OUT/logs/xvfb.log" 2>&1 & XVPID=$!
for _ in $(seq 1 200); do [ -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ] && break; sleep 0.05; done; [ -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ]
cat >"$WORK/run-pi.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec script -q -f -c 'env PI_CODING_AGENT_DIR="$WORK/agent" PI_OFFLINE=1 PI_IMAGE_PROTOCOL=kitty pi --offline --no-extensions --extension "$OLD_COPY/extensions/index.ts" --extension "$ROOT/index.ts" --no-skills --no-prompt-templates --no-themes --no-context-files --no-tools --session-dir "$WORK/sessions" --session "$SESSION"' "$PTY_LOG"
EOF
chmod 700 "$WORK/run-pi.sh"
setsid env -u TMUX -u TMUX_PANE DISPLAY="$DISPLAY" GDK_BACKEND=x11 LIBGL_ALWAYS_SOFTWARE=1 GDK_DEBUG=gl-disable-gles,gl-no-fractional,vulkan-disable GDK_DISABLE=color-mgmt GTK_USE_PORTAL=0 GIO_USE_VFS=local NO_AT_BRIDGE=1 GHOSTTY_RESOURCES_DIR=/usr/share/ghostty XDG_RUNTIME_DIR="$OUT/runtime" XDG_CONFIG_HOME="$OUT/config" XDG_CACHE_HOME="$OUT/cache" XDG_STATE_HOME="$OUT/state" XDG_DATA_HOME="$OUT/data" XDG_DATA_DIRS="$OUT/empty-share" dbus-run-session -- ghostty --gtk-single-instance=false --window-save-state=never --window-width=1080 --window-height=820 --font-size=8 -e "$WORK/run-pi.sh" >"$OUT/logs/ghostty.log" 2>&1 & GHOST_PID=$!
for _ in $(seq 1 500); do grep -a -q 'DUPLICATE-READ-END' "$PTY_LOG" 2>/dev/null && break; sleep 0.05; done
grep -a -q 'DUPLICATE-READ-END' "$PTY_LOG" || { echo "Pi session did not render" >&2; exit 1; }; sleep 5
/usr/bin/xwd -silent -root -display "$DISPLAY" -out "$OUT/artifacts/duplicate-read.xwd"; /usr/bin/ffmpeg -v error -y -i "$OUT/artifacts/duplicate-read.xwd" "$OUT/artifacts/duplicate-read.png"; rm "$OUT/artifacts/duplicate-read.xwd"
python3 "$ROOT/test/private-fixture/capture.py" "$OUT/artifacts/duplicate-read.png" "$OUT/artifacts/duplicate-read.png" >"$OUT/artifacts/duplicate-read.capture.json"
python3 "$SELF/analyze.py" "$OUT/artifacts/duplicate-read.png" "$SESSION" "$WORK/manifest.json" "$OUT/artifacts/visible-occurrence-summary.json" >"$OUT/artifacts/visible-occurrence.stdout.json"
echo "duplicate read fixture passed: $OUT"
