#!/usr/bin/env bash
# Managed-symlink installer for the local pi-tmux-images fork.
#
# Follows the pi-agent/extensions convention used by the other in-repo extensions:
# link the source directory into $PI_CODING_AGENT_DIR/extensions/<name>, keep the
# link out of Git via info/exclude, refuse unknown targets, and never reload Pi.
# This installer is NOT run automatically; adoption is an operator decision.
set -euo pipefail

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_HOME=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
TARGET="$PI_HOME/extensions/pi-tmux-images"
RULE=/extensions/pi-tmux-images
NPM_PACKAGE=pi-tmux-images

if ! git -C "$PI_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  echo "pi-tmux-images: PI home is not a Git worktree: $PI_HOME" >&2
  exit 1
fi

INDEXED=$(git -C "$PI_HOME" ls-files -- "extensions/pi-tmux-images" "extensions/pi-tmux-images/**")
if [[ -n "$INDEXED" ]]; then
  echo "pi-tmux-images: refusing Git-index-owned target: ${INDEXED%%$'\n'*}" >&2
  exit 1
fi

if [[ -e "$TARGET" || -L "$TARGET" ]]; then
  if [[ ! -L "$TARGET" ]]; then
    echo "pi-tmux-images: refusing to replace non-symlink: $TARGET" >&2
    exit 1
  fi
  CURRENT=$(readlink -f -- "$TARGET" 2>/dev/null || true)
  if [[ "$CURRENT" != "$SOURCE_DIR" ]]; then
    echo "pi-tmux-images: refusing to replace foreign symlink: $TARGET -> $(readlink -- "$TARGET")" >&2
    exit 1
  fi
fi

# Only one package may own read previews and /image; the npm registration is the
# upstream deployment that this fork replaces.
SETTINGS="$PI_HOME/settings.json"
if [[ -f "$SETTINGS" ]] && grep -q "npm:$NPM_PACKAGE" "$SETTINGS"; then
  echo "pi-tmux-images: $SETTINGS still registers npm:$NPM_PACKAGE." >&2
  echo "  Remove that entry (or run 'pi uninstall npm:$NPM_PACKAGE') before installing the fork," >&2
  echo "  so the read-preview and /image owner stays unique." >&2
  exit 1
fi

EXCLUDE=$(git -C "$PI_HOME" rev-parse --git-path info/exclude)
if [[ "$EXCLUDE" != /* ]]; then
  GIT_DIR=$(git -C "$PI_HOME" rev-parse --absolute-git-dir)
  EXCLUDE="$GIT_DIR/${EXCLUDE#*/}"
fi
[[ -f "$EXCLUDE" && -w "$EXCLUDE" ]] || { echo "pi-tmux-images: Git exclude is not writable: $EXCLUDE" >&2; exit 1; }

# Dependencies stay beside the externally owned extension source. The engine itself
# needs none: `sharp` is imported lazily and only when an image must be bounded.
if [[ ! -d "$SOURCE_DIR/node_modules/sharp" ]]; then
  if command -v npm >/dev/null 2>&1; then
    (cd "$SOURCE_DIR" && npm install --omit=dev --ignore-scripts --no-audit --no-fund) || {
      echo "pi-tmux-images: npm install failed (offline?); images above the payload ceiling will render as text" >&2
    }
  fi
fi

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
[[ $(readlink -f -- "$TARGET") == "$SOURCE_DIR" ]] || { echo "pi-tmux-images: link verification failed" >&2; exit 1; }
grep -Fqx -- "$RULE" "$EXCLUDE" || { echo "pi-tmux-images: exclude verification failed" >&2; exit 1; }

echo "pi-tmux-images: installed $TARGET -> $SOURCE_DIR"
echo "Run /reload in Pi when you are ready; this installer does not reload or restart Pi."
