#!/usr/bin/env bash
# Real installed libwayland; private socketpairs, no compositor or GUI.
set -euo pipefail
TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE="$(cd "$TEST_DIR/../../modules/ghostty" && pwd)"
WORK="$(mktemp -d /tmp/ghostty-wl-backpressure-test.XXXXXX)"
trap 'rm -rf -- "$WORK"' EXIT
command -v cc >/dev/null || { echo 'SKIP: no C compiler for Wayland backpressure test'; exit 0; }
[ -e /usr/lib/x86_64-linux-gnu/libwayland-client.so.0 ] || [ -e /usr/lib/aarch64-linux-gnu/libwayland-client.so.0 ] \
    || { echo 'SKIP: libwayland-client.so.0 absent'; exit 0; }
cc -shared -fPIC -O2 -Wall -Wextra -Werror -DMAX_WAIT_MS=500 \
    -o "$WORK/libwayland-backpressure.so" "$MODULE/wayland-backpressure.c" -ldl -pthread
cc -O2 -Wall -Wextra -Werror -o "$WORK/test" "$TEST_DIR/wayland-backpressure.c" \
    -Wl,-l:libwayland-client.so.0 -ldl -pthread
cc -O2 -Wall -Wextra -Werror -o "$WORK/late-load" "$TEST_DIR/wayland-late-load.c" -ldl
LD_PRELOAD="$WORK/libwayland-backpressure.so" "$WORK/late-load" > "$WORK/late-load.out"
grep -Fqx 'late-loaded Wayland connection works after unrelated sendmsg' "$WORK/late-load.out"
"$WORK/test" >"$WORK/baseline" 2>&1
EXPECT_WIRE=1 LD_PRELOAD="$WORK/libwayland-backpressure.so" "$WORK/test" >"$WORK/recovered" 2>&1
NO_DRAIN=1 LD_PRELOAD="$WORK/libwayland-backpressure.so" "$WORK/test" >"$WORK/timeout" 2>&1
for run in baseline recovered timeout; do
    grep -Eq '^unrelated ret=-1 errno=11 elapsed_ms=([0-9]|[1-9][0-9]|1[0-4][0-9])$' "$WORK/$run" \
        || { cat "$WORK/$run" >&2; echo "FAIL: unrelated socket changed ($run)" >&2; exit 1; }
    grep -Fqx 'closed_peer ret=-1 errno=32' "$WORK/$run" \
        || { cat "$WORK/$run" >&2; echo "FAIL: EPIPE changed ($run)" >&2; exit 1; }
done
grep -Eq '^requests=3[0-9]+ flush_ret=-1 errno=11 display_error=11' "$WORK/baseline" \
    || { cat "$WORK/baseline" >&2; echo 'FAIL: baseline did not latch EAGAIN' >&2; exit 1; }
grep -Eq '^requests=600 flush_ret=[0-9]+ errno=11 display_error=0 elapsed_ms=3[0-9][0-9]$' "$WORK/recovered" \
    || { cat "$WORK/recovered" >&2; echo 'FAIL: transient EAGAIN not recovered' >&2; exit 1; }
grep -Fq 'recovered transient Wayland backpressure' "$WORK/recovered" \
    || { cat "$WORK/recovered" >&2; echo 'FAIL: recovery diagnostic missing' >&2; exit 1; }
grep -Fqx 'wire: 600 ordered complete get_registry requests' "$WORK/recovered" \
    || { cat "$WORK/recovered" >&2; echo 'FAIL: bytes lost/reordered' >&2; exit 1; }
grep -Fqx 'fd_transport=sent:1 received:1' "$WORK/recovered" \
    || { cat "$WORK/recovered" >&2; echo 'FAIL: Wayland fd duplicated/lost on retry' >&2; exit 1; }
grep -Eq '^requests=3[0-9]+ flush_ret=-1 errno=11 display_error=11 elapsed_ms=5[0-9][0-9]$' "$WORK/timeout" \
    || { cat "$WORK/timeout" >&2; echo 'FAIL: persistent block did not time out' >&2; exit 1; }
grep -Fq 'Wayland socket remained blocked' "$WORK/timeout" \
    || { cat "$WORK/timeout" >&2; echo 'FAIL: timeout diagnostic missing' >&2; exit 1; }
echo 'PASS: native libwayland transient recovery, sticky-EAGAIN negative control, unrelated socket, EPIPE, bounded timeout'
