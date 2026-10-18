#!/bin/bash
# Source-only private fixtures; never call a real service/controller/vendor.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PRIVATE="$(mktemp -d /tmp/qd-attempt-fixtures-XXXXXXXX)"
trap '/bin/rm -rf -- "$PRIVATE"' EXIT
mkdir -m700 "$PRIVATE/home" "$PRIVATE/runtime"
# Synthetic deb's public DEBIAN directory must be0755; its enclosing private
# TemporaryDirectory stays0700. Do not leak caller umask077 into dpkg fixtures.
umask 022
unset PYTHONPATH PYTHONHOME XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME DBUS_SESSION_BUS_ADDRESS
export HOME="$PRIVATE/home" XDG_RUNTIME_DIR="$PRIVATE/runtime" PATH=/usr/bin:/bin
/usr/bin/python3 -I -B "$HERE/display_attempt_retirement.py" "$@"
