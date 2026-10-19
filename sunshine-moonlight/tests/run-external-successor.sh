#!/bin/bash
# Source/private fixtures only; no native user service or endpoint operations.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE="$(mktemp -d /tmp/qd-external-fixtures-XXXXXXXX)"
trap '/bin/rm -rf -- "$PRIVATE"' EXIT
mkdir -m700 "$PRIVATE/home" "$PRIVATE/runtime"
umask 022
unset PYTHONPATH PYTHONHOME XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME DBUS_SESSION_BUS_ADDRESS
export HOME="$PRIVATE/home" XDG_RUNTIME_DIR="$PRIVATE/runtime" PATH=/usr/bin:/bin
/usr/bin/python3 -I -B "$HERE/display_external_successor.py" "$@"
