#!/bin/bash
# Isolated contract test for pi-tmux-window-status/install.sh. Uses temporary
# fake PI homes; never touches the real ~/.pi/agent.
set -euo pipefail

EXTENSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$EXTENSION_DIR/install.sh"
WORK="$(mktemp -d /tmp/pi-tmux-window-status-installer-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Git home: full contract ------------------------------------------------

PI_HOME="$WORK/git home/.pi/agent"
TARGET="$PI_HOME/extensions/pi-tmux-window-status"
LEGACY="$PI_HOME/extensions/quick-deploy-tmux-status"
git init -q "$PI_HOME"

run_git() {
  env PI_CODING_AGENT_DIR="$PI_HOME" "$INSTALLER"
}

run_git >/dev/null
[ -L "$TARGET" ] || fail "initial install did not create a symlink"
[ "$(readlink -f "$TARGET")" = "$EXTENSION_DIR" ] || fail "initial link target mismatch"
run_git | grep -Fq 'already installed; skipped' || fail "exact managed link was not idempotent"

EXCLUDE="$(git -C "$PI_HOME" rev-parse --absolute-git-dir)/info/exclude"
grep -Fqx '/extensions/pi-tmux-window-status' "$EXCLUDE" || fail "git exclude rule missing"

# Legacy name migration: managed old link is backed up, new link created.
rm "$TARGET"
ln -s "/old/checkout/pi-agent/extensions/quick-deploy-tmux-status" "$LEGACY"
run_git >/dev/null
[ -L "$TARGET" ] || fail "legacy migration did not create the new link"
[ "$(readlink -f "$TARGET")" = "$EXTENSION_DIR" ] || fail "legacy migration target mismatch"
[ ! -e "$LEGACY" ] && [ ! -L "$LEGACY" ] || fail "legacy link was not moved aside"
compgen -G "$LEGACY.bak.*" >/dev/null || fail "legacy link was not backed up"

# Stale managed link (old checkout path) is repaired with backup.
rm "$TARGET"
ln -s "/old/checkout/pi-agent/extensions/pi-tmux-window-status" "$TARGET"
run_git >/dev/null
[ "$(readlink -f "$TARGET")" = "$EXTENSION_DIR" ] || fail "stale managed link was not repaired"
compgen -G "$TARGET.bak.*" >/dev/null || fail "stale managed link was not backed up"

# Foreign file must be refused and left untouched.
rm "$TARGET"
printf 'foreign file\n' > "$TARGET"
if run_git >/dev/null 2>&1; then fail "foreign file was unexpectedly replaced"; fi
grep -Fqx 'foreign file' "$TARGET" || fail "foreign file was modified"
rm "$TARGET"

# Foreign legacy + absent new must fail without mutation.
printf 'foreign legacy\n' > "$LEGACY"
if run_git >/dev/null 2>&1; then fail "foreign legacy path was unexpectedly accepted"; fi
grep -Fqx 'foreign legacy' "$LEGACY" || fail "foreign legacy was modified"
[ ! -e "$TARGET" ] && [ ! -L "$TARGET" ] || fail "new link created despite foreign legacy"
rm "$LEGACY"

# --- Non-git home: link still installs, git-only steps skipped ---------------

PLAIN_HOME="$WORK/plain home/.pi/agent"
PLAIN_TARGET="$PLAIN_HOME/extensions/pi-tmux-window-status"
mkdir -p "$PLAIN_HOME"
env PI_CODING_AGENT_DIR="$PLAIN_HOME" "$INSTALLER" >/dev/null
[ -L "$PLAIN_TARGET" ] || fail "non-git home install did not create a symlink"
[ "$(readlink -f "$PLAIN_TARGET")" = "$EXTENSION_DIR" ] || fail "non-git home link target mismatch"
env PI_CODING_AGENT_DIR="$PLAIN_HOME" "$INSTALLER" | grep -Fq 'already installed; skipped' \
  || fail "non-git home re-run was not idempotent"

echo 'PASS: pi-tmux-window-status installer exact/stale/legacy/foreign contracts and non-git fallback'
