#!/bin/bash
# Run all pi-suspend-guard tests without touching the real Pi home or tmux server.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTENSION_DIR="$(cd "$HERE/.." && pwd)"

node --test "$HERE/core.test.mjs"
python3 "$HERE/topology.py" "$EXTENSION_DIR/guard.mjs" "$EXTENSION_DIR/index.ts"
"$HERE/installer.sh"

echo "PASS: pi-suspend-guard core, topology, and installer tests"
