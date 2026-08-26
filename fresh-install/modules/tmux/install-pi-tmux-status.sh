#!/bin/bash
# Install the tracked Pi→tmux status extension through one managed symlink.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${QUICK_DEPLOY_PI_TMUX_STATUS_SOURCE:-$SCRIPT_DIR/../../../pi-agent/extensions/quick-deploy-tmux-status}"
PI_HOME="${QUICK_DEPLOY_PI_HOME:-$HOME/.pi/agent}"
TARGET="${QUICK_DEPLOY_PI_TMUX_STATUS_TARGET:-$PI_HOME/extensions/quick-deploy-tmux-status}"
STAMP="$(date +%Y%m%d_%H%M%S)"

[ -d "$SOURCE_DIR" ] || { echo "quick-deploy Pi tmux status: source missing: $SOURCE_DIR" >&2; exit 1; }
mkdir -p "$(dirname "$TARGET")"
if [ -L "$TARGET" ]; then
  if [ "$(readlink -f "$TARGET" 2>/dev/null || true)" = "$(readlink -f "$SOURCE_DIR")" ]; then
    echo "Pi tmux status extension already linked; skipped"
    exit 0
  fi
  link_target="$(readlink "$TARGET")"
  case "$link_target" in
    *"/pi-agent/extensions/quick-deploy-tmux-status"|*"/pi-agent/extensions/quick-deploy-tmux-status/")
      mv "$TARGET" "$TARGET.bak.$STAMP"
      echo "Backed up stale managed Pi extension link to $TARGET.bak.$STAMP" ;;
    *) echo "Refusing to replace foreign Pi extension link: $TARGET -> $link_target" >&2; exit 1 ;;
  esac
elif [ -e "$TARGET" ]; then
  echo "Refusing to replace foreign Pi extension path: $TARGET" >&2
  exit 1
fi
ln -s "$SOURCE_DIR" "$TARGET"
echo "Installed Pi tmux status extension: $TARGET -> $SOURCE_DIR"
