#!/bin/bash
# Install the tracked Pi→tmux window status extension through one managed symlink.
#
# Runtime contract — deliberately unchanged by the quick-deploy-tmux-status →
# pi-tmux-window-status rename: tmux window options stay @quick_deploy_pi_*
# and the private lease directory stays quick-deploy/pi-tmux-status, so
# already-running Pi processes and outstanding leases keep working and no
# duplicate runtime state is created. Only the managed symlink name under
# ~/.pi/agent/extensions changes.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_SOURCE:-$SCRIPT_DIR/../../../pi-agent/extensions/pi-tmux-window-status}"
PI_HOME="${QUICK_DEPLOY_PI_HOME:-$HOME/.pi/agent}"
TARGET="${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_TARGET:-$PI_HOME/extensions/pi-tmux-window-status}"
LEGACY_TARGET="${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_LEGACY_TARGET:-$PI_HOME/extensions/quick-deploy-tmux-status}"
STAMP="$(date +%Y%m%d_%H%M%S)"

die() { echo "quick-deploy Pi tmux window status: $*" >&2; exit 1; }

[ -d "$SOURCE_DIR" ] || die "source missing: $SOURCE_DIR"
SOURCE_CANON="$(readlink -f "$SOURCE_DIR")"

# A managed link is a symlink whose (possibly dangling) raw text points at this
# repo's tracked extension — the current pi-tmux-window-status path or the
# legacy quick-deploy-tmux-status path. Matching raw text also covers links left
# dangling by a checkout move.
managed_link() {
  [ -L "$1" ] || return 1
  local text
  text="$(readlink "$1" 2>/dev/null || true)"
  case "$text" in
    *"/pi-agent/extensions/pi-tmux-window-status"|*"/pi-agent/extensions/pi-tmux-window-status/"|*"/pi-agent/extensions/quick-deploy-tmux-status"|*"/pi-agent/extensions/quick-deploy-tmux-status/") return 0 ;;
  esac
  return 1
}

new_state=absent
if [ -L "$TARGET" ] || [ -e "$TARGET" ]; then
  if [ -L "$TARGET" ] && [ "$(readlink -f "$TARGET" 2>/dev/null || true)" = "$SOURCE_CANON" ]; then new_state=exact
  elif managed_link "$TARGET"; then new_state=stale
  else new_state=foreign; fi
fi

legacy_state=absent
if [ -L "$LEGACY_TARGET" ] || [ -e "$LEGACY_TARGET" ]; then
  if managed_link "$LEGACY_TARGET"; then legacy_state=managed
  else legacy_state=foreign; fi
fi

# Both old and new present: proceed only when every present path is a known
# managed link; a foreign path on either side is a real conflict and must stay
# untouched (fail without mutation).
if [ "$new_state" != absent ] && [ "$legacy_state" != absent ] && { [ "$new_state" = foreign ] || [ "$legacy_state" = foreign ]; }; then
  die "conflict: refusing to mutate $TARGET or $LEGACY_TARGET because not both are known managed links"
fi
if [ "$legacy_state" = foreign ]; then
  die "conflict: legacy Pi extension path is not a managed quick-deploy link and is left untouched: $LEGACY_TARGET"
fi
if [ "$new_state" = foreign ]; then
  die "refusing to replace foreign Pi extension path: $TARGET"
fi

if [ "$new_state" = exact ] && [ "$legacy_state" = absent ]; then
  echo "Pi tmux window status extension already linked; skipped"
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
if [ "$legacy_state" = managed ]; then
  mv "$LEGACY_TARGET" "$LEGACY_TARGET.bak.$STAMP"
  echo "Backed up legacy managed Pi extension link to $LEGACY_TARGET.bak.$STAMP"
fi
if [ "$new_state" = stale ]; then
  mv "$TARGET" "$TARGET.bak.$STAMP"
  echo "Backed up stale managed Pi extension link to $TARGET.bak.$STAMP"
fi
if [ "$new_state" != exact ]; then
  ln -s "$SOURCE_DIR" "$TARGET"
  echo "Installed Pi tmux window status extension: $TARGET -> $SOURCE_DIR"
fi
