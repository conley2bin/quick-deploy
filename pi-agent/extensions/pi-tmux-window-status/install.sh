#!/usr/bin/env bash
# Install pi-tmux-window-status through one managed symlink under the Pi agent dir.
#
# Ownership semantics match the historical fresh-install tmux module installer:
# exact link -> skip; stale managed link (old checkout) -> backup and repair;
# legacy quick-deploy-tmux-status managed link -> backup and migrate; any
# foreign file, directory, or link on either side -> fail without mutation.
# When the Pi home is a Git worktree, additionally refuse Git-index-owned
# targets and ensure a git info/exclude rule. Without Git, those Git-only
# steps are skipped so fresh machines still work.
set -euo pipefail

SOURCE_DIR="${PI_TMUX_WINDOW_STATUS_SOURCE:-$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
PI_HOME="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
TARGET="${PI_TMUX_WINDOW_STATUS_TARGET:-$PI_HOME/extensions/pi-tmux-window-status}"
LEGACY_TARGET="${PI_TMUX_WINDOW_STATUS_LEGACY_TARGET:-$PI_HOME/extensions/quick-deploy-tmux-status}"
RULE=/extensions/pi-tmux-window-status
STAMP="$(date +%Y%m%d_%H%M%S)"

die() { echo "pi-tmux-window-status: $*" >&2; exit 1; }

[ -d "$SOURCE_DIR" ] || die "source missing: $SOURCE_DIR"
SOURCE_CANON="$(readlink -f "$SOURCE_DIR")"

IS_GIT_HOME=0
if git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  IS_GIT_HOME=1
else
  echo "pi-tmux-window-status: $PI_HOME is not a Git worktree; skipping index/exclude checks"
fi

# A managed link is a symlink whose (possibly dangling) raw text points at this
# repo's tracked extension — current name or the legacy quick-deploy-tmux-status
# name. Raw text also covers links left dangling by a checkout move.
managed_link() {
  [ -L "$1" ] || return 1
  local text
  text="$(readlink "$1" 2>/dev/null || true)"
  case "$text" in
    *"/pi-agent/extensions/pi-tmux-window-status"|*"/pi-agent/extensions/pi-tmux-window-status/"|*"/pi-agent/extensions/quick-deploy-tmux-status"|*"/pi-agent/extensions/quick-deploy-tmux-status/") return 0 ;;
  esac
  return 1
}

index_owned() {
  local indexed
  indexed=$(git -C "$PI_HOME" ls-files -- "${1#"$PI_HOME"/}" "${1#"$PI_HOME"/}/**" 2>/dev/null)
  [ -n "$indexed" ]
}

if [ "$IS_GIT_HOME" = 1 ]; then
  index_owned "$TARGET" && die "refusing Git-index-owned target: $TARGET"
  if [ -e "$LEGACY_TARGET" ] || [ -L "$LEGACY_TARGET" ]; then
    index_owned "$LEGACY_TARGET" && die "refusing Git-index-owned legacy target: $LEGACY_TARGET"
  fi
fi

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

if [ "$IS_GIT_HOME" = 1 ]; then
  EXCLUDE=$(git -C "$PI_HOME" rev-parse --git-path info/exclude)
  if [[ "$EXCLUDE" != /* ]]; then
    GIT_DIR=$(git -C "$PI_HOME" rev-parse --absolute-git-dir)
    EXCLUDE="$GIT_DIR/${EXCLUDE#*/}"
  fi
  [[ -f "$EXCLUDE" && -w "$EXCLUDE" ]] || die "Git exclude is not writable: $EXCLUDE"
  if ! grep -Fqx -- "$RULE" "$EXCLUDE"; then
    TMP=$(mktemp "${EXCLUDE}.tmp.XXXXXX")
    trap 'rm -f -- "${TMP:-}"' EXIT
    cat -- "$EXCLUDE" >"$TMP"
    [[ ! -s "$TMP" || $(tail -c 1 "$TMP" | wc -l) -eq 1 ]] || printf '\n' >>"$TMP"
    printf '%s\n' "$RULE" >>"$TMP"
    mv -- "$TMP" "$EXCLUDE"
    trap - EXIT
  fi
fi

if [ "$new_state" = exact ] && [ "$legacy_state" = absent ]; then
  echo "pi-tmux-window-status: already installed; skipped"
  exit 0
fi

mkdir -p -- "$(dirname "$TARGET")"
if [ "$legacy_state" = managed ]; then
  mv "$LEGACY_TARGET" "$LEGACY_TARGET.bak.$STAMP"
  echo "pi-tmux-window-status: backed up legacy managed link to $LEGACY_TARGET.bak.$STAMP"
fi
if [ "$new_state" = stale ]; then
  mv "$TARGET" "$TARGET.bak.$STAMP"
  echo "pi-tmux-window-status: backed up stale managed link to $TARGET.bak.$STAMP"
fi
if [ "$new_state" != exact ]; then
  ln -s -- "$SOURCE_DIR" "$TARGET"
fi
[ "$(readlink -f "$TARGET")" = "$SOURCE_CANON" ] || die "link verification failed"
if [ "$IS_GIT_HOME" = 1 ]; then
  grep -Fqx -- "$RULE" "$EXCLUDE" || die "exclude verification failed"
fi

echo "pi-tmux-window-status: installed $TARGET -> $SOURCE_DIR"
echo "Run /reload in Pi when you are ready; this installer does not reload or restart Pi."
