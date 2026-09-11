#!/usr/bin/env bash
# Install pi-suspend-guard through one managed symlink under the Pi agent dir.
# Conventions match pi-inline-images/install.sh: refuse Git-index-owned and
# foreign targets, add a git info/exclude rule, never reload Pi.
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_HOME=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
TARGET="$PI_HOME/extensions/pi-suspend-guard"
RULE=/extensions/pi-suspend-guard
# First deployment (commit a8c658d) linked from the tmux module; treat links
# pointing there as stale managed state and migrate them.
LEGACY_PATTERN='*/fresh-install/modules/tmux/pi-suspend-guard'
STAMP="$(date +%Y%m%d_%H%M%S)"

die() { echo "pi-suspend-guard: $*" >&2; exit 1; }

if ! git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  die "PI home is not a Git worktree: $PI_HOME"
fi

INDEXED=$(git -C "$PI_HOME" ls-files -- "extensions/pi-suspend-guard" "extensions/pi-suspend-guard/**")
if [[ -n "$INDEXED" ]]; then
  die "refusing Git-index-owned target: ${INDEXED%%$'\n'*}"
fi

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

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  if [[ ! -L "$TARGET" ]]; then
    die "refusing to replace non-symlink: $TARGET"
  fi
  CURRENT=$(readlink -f -- "$TARGET" 2>/dev/null || true)
  if [[ "$CURRENT" == "$SOURCE_DIR" ]]; then
    echo "pi-suspend-guard: already installed; skipped"
    exit 0
  fi
  case "$(readlink -- "$TARGET")" in
    $LEGACY_PATTERN|$LEGACY_PATTERN/)
      mv -- "$TARGET" "$TARGET.bak.$STAMP"
      echo "pi-suspend-guard: migrated legacy tmux-module link (backup: $TARGET.bak.$STAMP)" ;;
    *)
      die "refusing to replace foreign symlink: $TARGET -> $(readlink -- "$TARGET")" ;;
  esac
fi

mkdir -p -- "$PI_HOME/extensions"
ln -s -- "$SOURCE_DIR" "$TARGET"
[[ $(readlink -f -- "$TARGET") == "$SOURCE_DIR" ]] || die "link verification failed"
grep -Fqx -- "$RULE" "$EXCLUDE" || die "exclude verification failed"

echo "pi-suspend-guard: installed $TARGET -> $SOURCE_DIR"
echo "Run /reload in Pi when you are ready; this installer does not reload or restart Pi."
