#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_HOME=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
TARGET="$PI_HOME/extensions/pi-inline-images"
RULE=/extensions/pi-inline-images

# Ordinary Pi homes need no repository. Git-owned homes retain the additional
# index/exclude protections; target ownership is checked in either case.
IS_GIT_HOME=0
if git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  IS_GIT_HOME=1
  INDEXED=$(git -C "$PI_HOME" ls-files -- "extensions/pi-inline-images" "extensions/pi-inline-images/**")
  if [[ -n "$INDEXED" ]]; then
    echo "pi-inline-images: refusing Git-index-owned target: ${INDEXED%%$'\n'*}" >&2
    exit 1
  fi
fi

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  if [[ ! -L "$TARGET" ]]; then
    echo "pi-inline-images: refusing to replace non-symlink: $TARGET" >&2
    exit 1
  fi
  CURRENT=$(readlink -f -- "$TARGET" 2>/dev/null || true)
  if [[ "$CURRENT" != "$SOURCE_DIR" ]]; then
    echo "pi-inline-images: refusing to replace foreign symlink: $TARGET -> $(readlink -- "$TARGET")" >&2
    exit 1
  fi
fi

if [[ $IS_GIT_HOME == 1 ]]; then
  EXCLUDE=$(git -C "$PI_HOME" rev-parse --git-path info/exclude)
  if [[ "$EXCLUDE" != /* ]]; then
    GIT_DIR=$(git -C "$PI_HOME" rev-parse --absolute-git-dir)
    EXCLUDE="$GIT_DIR/${EXCLUDE#*/}"
  fi
  [[ -f "$EXCLUDE" && -w "$EXCLUDE" ]] || { echo "pi-inline-images: Git exclude is not writable: $EXCLUDE" >&2; exit 1; }
fi

# Dependencies remain beside the externally owned extension source.
(cd "$SOURCE_DIR" && npm ci --ignore-scripts)

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
[[ $(readlink -f -- "$TARGET") == "$SOURCE_DIR" ]] || { echo "pi-inline-images: link verification failed" >&2; exit 1; }
if [[ $IS_GIT_HOME == 1 ]]; then
  grep -Fqx -- "$RULE" "$EXCLUDE" || { echo "pi-inline-images: exclude verification failed" >&2; exit 1; }
fi

echo "pi-inline-images: installed $TARGET -> $SOURCE_DIR"
echo "Run /reload in Pi when you are ready; this installer does not reload or restart Pi."
