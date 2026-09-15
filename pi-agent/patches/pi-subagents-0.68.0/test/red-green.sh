#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 /path/to/original-v0.68.0 /path/to/patched-v0.68.0" >&2
  exit 64
fi

self_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
original=$(cd -- "$1" && pwd -P)
patched=$(cd -- "$2" && pwd -P)
red_log=$(mktemp)
trap 'rm -f "$red_log"' EXIT

if node "$self_dir/startup-handshake.test.mjs" --source "$original" --case mixed-generation >"$red_log" 2>&1; then
  echo "RED failed: original source unexpectedly accepted the regression contract" >&2
  cat "$red_log" >&2
  exit 1
fi
grep -Fq "stalled after legacy ack/proceed routing" "$red_log" || {
  echo "RED failed for an unexpected reason" >&2
  cat "$red_log" >&2
  exit 1
}
echo "RED: original v0.68.0 stalls at acknowledged under a legacy loaded sender"

node "$self_dir/startup-handshake.test.mjs" --source "$patched"
npm run typecheck --prefix "$patched"
echo "GREEN: patched startup protocol and typecheck pass"
