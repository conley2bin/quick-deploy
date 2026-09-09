#!/bin/bash
# Install the module-owned Pi suspend guard through one managed symlink.
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${QUICK_DEPLOY_PI_SUSPEND_GUARD_SOURCE:-$SCRIPT_DIR/pi-suspend-guard}"
PI_HOME="${QUICK_DEPLOY_PI_HOME:-$HOME/.pi/agent}"
TARGET="${QUICK_DEPLOY_PI_SUSPEND_GUARD_TARGET:-$PI_HOME/extensions/pi-suspend-guard}"
STAMP="$(date +%Y%m%d_%H%M%S)"

die() { echo "quick-deploy Pi suspend guard: $*" >&2; exit 1; }

[ -d "$SOURCE_DIR" ] || die "source missing: $SOURCE_DIR"
SOURCE_CANON="$(readlink -f "$SOURCE_DIR")"

managed_link() {
  [ -L "$1" ] || return 1
  case "$(readlink "$1" 2>/dev/null || true)" in
    */fresh-install/modules/tmux/pi-suspend-guard|*/fresh-install/modules/tmux/pi-suspend-guard/) return 0 ;;
  esac
  return 1
}

if [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET" 2>/dev/null || true)" = "$SOURCE_CANON" ]; then
  echo "Pi suspend guard extension already linked; skipped"
  exit 0
fi

if [ -L "$TARGET" ] || [ -e "$TARGET" ]; then
  if managed_link "$TARGET"; then
    mv "$TARGET" "$TARGET.bak.$STAMP"
    echo "Backed up stale managed Pi suspend guard link to $TARGET.bak.$STAMP"
  else
    die "refusing to replace foreign Pi extension path: $TARGET"
  fi
fi

mkdir -p "$(dirname "$TARGET")"
ln -s "$SOURCE_DIR" "$TARGET"
echo "Installed Pi suspend guard extension: $TARGET -> $SOURCE_DIR"
