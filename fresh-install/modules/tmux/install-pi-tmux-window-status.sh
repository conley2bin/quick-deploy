#!/bin/bash
# Compatibility wrapper: the pi-tmux-window-status extension now owns its
# installer at pi-agent/extensions/pi-tmux-window-status/install.sh. This
# entry point remains for the fresh-install tmux module and maps its
# documented QUICK_DEPLOY_* test/isolation knobs onto the extension
# installer's interface.
set -euo pipefail
SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMPL="${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_INSTALLER:-$SCRIPT_DIR/../../../pi-agent/extensions/pi-tmux-window-status/install.sh}"

[ -f "$IMPL" ] || { echo "pi-tmux-window-status installer missing: $IMPL" >&2; exit 1; }

if [ -n "${QUICK_DEPLOY_PI_HOME:-}" ]; then
  export PI_CODING_AGENT_DIR="$QUICK_DEPLOY_PI_HOME"
fi
if [ -n "${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_SOURCE:-}" ]; then
  export PI_TMUX_WINDOW_STATUS_SOURCE="$QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_SOURCE"
fi
if [ -n "${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_TARGET:-}" ]; then
  export PI_TMUX_WINDOW_STATUS_TARGET="$QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_TARGET"
fi
if [ -n "${QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_LEGACY_TARGET:-}" ]; then
  export PI_TMUX_WINDOW_STATUS_LEGACY_TARGET="$QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_LEGACY_TARGET"
fi

exec "$IMPL"
