#!/usr/bin/env bash
# Install source dependencies and one managed directory symlink. Never change
# settings, restart Pi, replace another owner's files, or edit Pi's npm bundle.
set -euo pipefail
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_HOME=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
TARGET="$PI_HOME/extensions/pi-copy-links"
RULE=/extensions/pi-copy-links

die() { echo "pi-copy-links: $*" >&2; exit 1; }
[[ $# == 0 ]] || die "no arguments expected; PI_CODING_AGENT_DIR overrides the destination"

IS_GIT_HOME=0
if git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  IS_GIT_HOME=1
  INDEXED=$(git -C "$PI_HOME" ls-files -- extensions/pi-copy-links 'extensions/pi-copy-links/**')
  [[ -z "$INDEXED" ]] || die "refusing Git-index-owned target: ${INDEXED%%$'\n'*}"
  EXCLUDE=$(git -C "$PI_HOME" rev-parse --path-format=absolute --git-path info/exclude)
  [[ -f "$EXCLUDE" && -w "$EXCLUDE" ]] || die "Git exclude is not writable: $EXCLUDE"
fi
if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  [[ -L "$TARGET" ]] || die "refusing to replace non-symlink: $TARGET"
  [[ $(readlink -f -- "$TARGET" 2>/dev/null) == "$SOURCE_DIR" ]] || die "refusing to replace foreign symlink: $TARGET"
fi

# Keep dependency installation in the source checkout, not the Pi home.
(cd "$SOURCE_DIR" && npm ci --ignore-scripts --no-audit --no-fund)
mkdir -p -- "$PI_HOME/extensions"
if [[ $IS_GIT_HOME == 1 ]] && ! grep -Fqx -- "$RULE" "$EXCLUDE"; then
  TMP=$(mktemp "${EXCLUDE}.tmp.XXXXXX")
  trap 'rm -f -- "${TMP:-}"' EXIT
  cat -- "$EXCLUDE" >"$TMP"
  [[ ! -s "$TMP" || $(tail -c 1 "$TMP" | wc -l) -eq 1 ]] || printf '\n' >>"$TMP"
  printf '%s\n' "$RULE" >>"$TMP"
  mv -- "$TMP" "$EXCLUDE"
  trap - EXIT
fi
[[ -L "$TARGET" ]] || ln -s -- "$SOURCE_DIR" "$TARGET"
[[ $(readlink -f -- "$TARGET") == "$SOURCE_DIR" ]] || die "link verification failed"
echo "pi-copy-links: installed $TARGET -> $SOURCE_DIR"
echo 'Run /reload, then /settings → TUI mode → fullscreen. Use /copy-links to check.'
echo 'Settings and the running Pi session were not changed.'
