#!/usr/bin/env bash
# Isolated byte-level end-to-end check for the hardened engine.
#
# Runs the driver inside a private tmux server (own socket, config = /dev/null) so the
# engine's real `tmux show-options -Apv -t $TMUX_PANE` probe is exercised for three
# policies: default(off), on, and all. Captures the exact bytes the engine wrote, the
# pane grid, and the pipe-pane stream, then tears the server down. The user's tmux
# server is never touched and no user pane receives input.
set -euo pipefail

HERE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
DRIVER="$HERE/e2e-driver.ts"
COUNTER="$HERE/count.py"
WORK=$(mktemp -d)
KEEP=${E2E_KEEP:-0}
STATUS=0
SOCKETS=()
index=0

cleanup() {
	for sock in ${SOCKETS[@]+"${SOCKETS[@]}"}; do
		tmux -L "$sock" kill-server 2>/dev/null || true
	done
	[[ "$KEEP" == "1" ]] || rm -rf -- "$WORK"
}
trap cleanup EXIT

run_case() {
	local policy=$1 expect_mode=$2 expect_writes=$3 expect_kitty=$4
	local sock="pitmux-e2e-$$-$index" out="$WORK/$policy.out" pane="$WORK/$policy.pane"
	index=$((index + 1))
	SOCKETS+=("$sock")

	tmux -L "$sock" -f /dev/null new-session -d -s e2e -x 100 -y 30
	if [[ "$policy" != "default" ]]; then
		tmux -L "$sock" set-option -w -t e2e:0 allow-passthrough "$policy"
	fi
	tmux -L "$sock" send-keys -t e2e:0 "E2E_WIDTH=78 node --experimental-strip-types '$DRIVER' > '$out' 2>&1" Enter
	sleep 3
	tmux -L "$sock" capture-pane -p -t e2e:0 -S -40 > "$pane" 2>/dev/null || true

	local effective result writes kitty cells cells_out
	effective=$(tmux -L "$sock" show-options -Apv -t e2e:0 allow-passthrough 2>/dev/null || echo "?")
	result=$(python3 "$COUNTER" result "$out")
	writes=$(printf '%s' "$result" | sed -n 's/.*writes=\([0-9]*\).*/\1/p')
	kitty=$(python3 "$COUNTER" bytes "$out")
	cells=$(python3 "$COUNTER" cells "$pane")
	cells_out=$(python3 "$COUNTER" cells "$out")

	printf 'case=%-8s tmux_policy=%-8s %s\n' "$policy" "$effective" "${result:-<no result>}"
	printf '           writes=%s kitty_commands=%s cells_emitted=%s placeholder_cells_in_grid=%s\n' \
		"${writes:-?}" "${kitty:-?}" "${cells_out:-?}" "${cells:-?}"

	[[ "${writes:-}" == "$expect_writes" ]] || { echo "  FAIL: expected writes=$expect_writes" >&2; STATUS=1; }
	[[ "$result" == *"mode=$expect_mode"* ]] || { echo "  FAIL: expected mode=$expect_mode" >&2; STATUS=1; }
	if [[ "$expect_kitty" == "yes" ]]; then
		[[ "${kitty:-0}" -ge 3 ]] || { echo "  FAIL: expected kitty commands (sync+upload+placement)" >&2; STATUS=1; }
		[[ "${cells_out:-0}" -ge 1 ]] || { echo "  FAIL: expected placeholder cells in the emitted stream" >&2; STATUS=1; }
	else
		[[ "${kitty:-1}" -eq 0 ]] || { echo "  FAIL: expected no kitty bytes" >&2; STATUS=1; }
		[[ "${cells_out:-1}" -eq 0 ]] || { echo "  FAIL: expected no placeholder cells emitted" >&2; STATUS=1; }
	fi
}

run_case default text 0 no
run_case on kitty-placeholder 1 yes
run_case all kitty-placeholder 1 yes

echo
echo "workdir=$WORK (E2E_KEEP=1 keeps it)"
if [[ "$STATUS" -eq 0 ]]; then echo "E2E PASS"; else echo "E2E FAIL"; fi
exit "$STATUS"
