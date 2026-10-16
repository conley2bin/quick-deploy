#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
umask 077
exec python3 -B "$SCRIPT_DIR/lib/vps.py" "$@"
