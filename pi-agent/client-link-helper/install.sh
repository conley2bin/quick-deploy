#!/usr/bin/env bash
# Explicit Y9000p-only install: no Pi extension, SSH config, service, or host deploy.
set -euo pipefail
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SOURCE="$SOURCE_DIR/pi-client-link-helper"
TARGET="$HOME/.local/bin/pi-client-link-helper"

[[ $# == 0 ]] || { echo 'pi-client-link-helper: no arguments expected' >&2; exit 2; }
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  [[ -L "$TARGET" && $(readlink -f -- "$TARGET" 2>/dev/null) == "$SOURCE" ]] || {
    echo "pi-client-link-helper: refusing to replace foreign target: $TARGET" >&2
    exit 1
  }
fi
mkdir -p -- "$(dirname -- "$TARGET")"
[[ -L "$TARGET" ]] || ln -s -- "$SOURCE" "$TARGET"
[[ -x "$TARGET" ]] || { echo 'pi-client-link-helper: installed target is not executable' >&2; exit 1; }
printf 'pi-client-link-helper: installed %s -> %s\n' "$TARGET" "$SOURCE"
printf '%s\n' 'No SSH config, Pi extension, or running session was changed.'
