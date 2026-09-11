#!/bin/bash
# Isolated contract test for pi-suspend-guard/install.sh. Uses a temporary
# fake PI home Git worktree; never touches the real ~/.pi/agent.
set -euo pipefail

EXTENSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$EXTENSION_DIR/install.sh"
WORK="$(mktemp -d /tmp/pi-suspend-guard-installer-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

PI_HOME="$WORK/home with spaces/.pi/agent"
TARGET="$PI_HOME/extensions/pi-suspend-guard"
git init -q "$PI_HOME"
printf '{"owned":"user"}\n' > "$PI_HOME/settings.json"

run_installer() {
  env PI_CODING_AGENT_DIR="$PI_HOME" "$INSTALLER"
}

run_installer >/dev/null
[ -L "$TARGET" ] || fail "initial install did not create a symlink"
[ "$(readlink -f "$TARGET")" = "$EXTENSION_DIR" ] || fail "initial link target mismatch"
run_installer | grep -Fq 'already installed; skipped' || fail "exact managed link was not idempotent"

EXCLUDE="$(git -C "$PI_HOME" rev-parse --absolute-git-dir)/info/exclude"
grep -Fqx '/extensions/pi-suspend-guard' "$EXCLUDE" || fail "git exclude rule missing"

# Legacy first-deployment link (from the tmux module) must be migrated with backup.
rm "$TARGET"
ln -s "/old/checkout/fresh-install/modules/tmux/pi-suspend-guard" "$TARGET"
run_installer >/dev/null
[ "$(readlink -f "$TARGET")" = "$EXTENSION_DIR" ] || fail "legacy link was not migrated"
compgen -G "$TARGET.bak.*" >/dev/null || fail "legacy link was not backed up"

# Foreign regular file must be refused and left untouched.
rm "$TARGET"
printf 'foreign file\n' > "$TARGET"
if run_installer >/dev/null 2>&1; then fail "foreign file was unexpectedly replaced"; fi
grep -Fqx 'foreign file' "$TARGET" || fail "foreign file was modified"

# Foreign symlink must be refused and left untouched.
rm "$TARGET"
ln -s /foreign/extension "$TARGET"
if run_installer >/dev/null 2>&1; then fail "foreign link was unexpectedly replaced"; fi
[ "$(readlink "$TARGET")" = /foreign/extension ] || fail "foreign link was modified"

# Non-git PI home: link installs with git-only steps skipped, stays idempotent.
PLAIN_HOME="$WORK/plain home/.pi/agent"
PLAIN_TARGET="$PLAIN_HOME/extensions/pi-suspend-guard"
mkdir -p "$PLAIN_HOME"
env PI_CODING_AGENT_DIR="$PLAIN_HOME" "$INSTALLER" >/dev/null
[ -L "$PLAIN_TARGET" ] || fail "non-git home install did not create a symlink"
[ "$(readlink -f "$PLAIN_TARGET")" = "$EXTENSION_DIR" ] || fail "non-git home link target mismatch"
env PI_CODING_AGENT_DIR="$PLAIN_HOME" "$INSTALLER" | grep -Fq 'already installed; skipped' \
  || fail "non-git home re-run was not idempotent"

echo 'PASS: pi-suspend-guard installer exact, legacy-migration, exclude, foreign-path, and non-git contracts'
