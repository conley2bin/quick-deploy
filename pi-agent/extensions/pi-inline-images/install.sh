#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_HOME=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
TARGET="$PI_HOME/extensions/pi-inline-images"
RULE=/extensions/pi-inline-images

if ! git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  echo "pi-inline-images: PI home is not a Git worktree: $PI_HOME" >&2
  exit 1
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

EXCLUDE=$(git -C "$PI_HOME" rev-parse --git-path info/exclude)
if [[ "$EXCLUDE" != /* ]]; then
  GIT_DIR=$(git -C "$PI_HOME" rev-parse --absolute-git-dir)
  EXCLUDE="$GIT_DIR/${EXCLUDE#*/}"
fi
[[ -f "$EXCLUDE" && -w "$EXCLUDE" ]] || { echo "pi-inline-images: Git exclude is not writable: $EXCLUDE" >&2; exit 1; }

# Dependencies remain beside the externally owned extension source.
(cd "$SOURCE_DIR" && npm ci --ignore-scripts)

mkdir -p -- "$PI_HOME/extensions"
if ! grep -Fqx -- "$RULE" "$EXCLUDE"; then
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
grep -Fqx -- "$RULE" "$EXCLUDE" || { echo "pi-inline-images: exclude verification failed" >&2; exit 1; }

echo "pi-inline-images: installed $TARGET -> $SOURCE_DIR"
echo "Run /reload in Pi when you are ready; this installer does not reload or restart Pi."
