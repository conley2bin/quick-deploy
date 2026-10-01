#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_HOME=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
TARGET="$PI_HOME/extensions/pi-inline-images"
RULE=/extensions/pi-inline-images

git_inspection_failed() { echo "pi-inline-images: cannot inspect Git ownership of $PI_HOME: $*" >&2; exit 1; }
command -v git >/dev/null 2>&1 || git_inspection_failed "git is unavailable"

# A failed query is not evidence that no index owns the target. Accept ordinary
# absence only after checking accessible physical ancestors and explicit context;
# never classify by Git's localized stderr or clear the caller's Git variables.
IS_GIT_HOME=0
if git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  IS_GIT_HOME=1
  INDEXED=$(git -C "$PI_HOME" ls-files -- "extensions/pi-inline-images" "extensions/pi-inline-images/**")
  if [[ -n "$INDEXED" ]]; then
    echo "pi-inline-images: refusing Git-index-owned target: ${INDEXED%%$'\n'*}" >&2
    exit 1
  fi
else
  GIT_STATUS=$?
  [[ $GIT_STATUS == 128 ]] || git_inspection_failed "Git query failed (exit $GIT_STATUS)"
  for variable in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES; do
    [[ ! -v "$variable" ]] || git_inspection_failed "explicit $variable context is unresolved"
  done
  INSPECT_PATH="$PI_HOME"
  while [[ ! -d "$INSPECT_PATH" ]]; do
    [[ ! -e "$INSPECT_PATH" && ! -L "$INSPECT_PATH" ]] || git_inspection_failed "unsettled destination path: $INSPECT_PATH"
    PARENT=$(dirname -- "$INSPECT_PATH")
    [[ "$PARENT" != "$INSPECT_PATH" ]] || git_inspection_failed "destination ancestors are unavailable"
    INSPECT_PATH="$PARENT"
  done
  [[ -r "$INSPECT_PATH" && -x "$INSPECT_PATH" ]] || git_inspection_failed "directory lookup unavailable: $INSPECT_PATH"
  INSPECT_PATH=$(CDPATH= cd -P -- "$INSPECT_PATH" && pwd -P) || git_inspection_failed "physical destination is unavailable"
  while :; do
    [[ -r "$INSPECT_PATH" && -x "$INSPECT_PATH" ]] || git_inspection_failed "ancestor lookup unavailable: $INSPECT_PATH"
    [[ ! -e "$INSPECT_PATH/.git" && ! -L "$INSPECT_PATH/.git" ]] || git_inspection_failed "repository marker exists: $INSPECT_PATH/.git"
    if [[ -e "$INSPECT_PATH/HEAD" && -d "$INSPECT_PATH/objects" && -d "$INSPECT_PATH/refs" ]]; then
      git_inspection_failed "repository metadata exists: $INSPECT_PATH"
    fi
    [[ "$INSPECT_PATH" == / ]] && break
    INSPECT_PATH=$(dirname -- "$INSPECT_PATH")
  done
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
