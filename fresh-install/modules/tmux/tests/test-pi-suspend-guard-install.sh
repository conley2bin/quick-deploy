#!/bin/bash
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$MODULE_DIR/install-pi-suspend-guard.sh"
SOURCE="$MODULE_DIR/pi-suspend-guard"
WORK="$(mktemp -d /tmp/quick-deploy-pi-suspend-guard-install.XXXXXX)"
TARGET="$WORK/home/.pi/agent/extensions/pi-suspend-guard"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
run_installer() {
  QUICK_DEPLOY_PI_HOME="$WORK/home/.pi/agent" \
  QUICK_DEPLOY_PI_SUSPEND_GUARD_SOURCE="$SOURCE" \
  QUICK_DEPLOY_PI_SUSPEND_GUARD_TARGET="$TARGET" \
  "$INSTALLER"
}

run_installer >/dev/null
[ -L "$TARGET" ] || fail "initial install did not create a symlink"
[ "$(readlink -f "$TARGET")" = "$(readlink -f "$SOURCE")" ] || fail "initial link target mismatch"
run_installer | grep -Fq 'already linked; skipped' || fail "exact managed link was not idempotent"

rm "$TARGET"
ln -s "/old/checkout/fresh-install/modules/tmux/pi-suspend-guard" "$TARGET"
run_installer >/dev/null
[ "$(readlink -f "$TARGET")" = "$(readlink -f "$SOURCE")" ] || fail "stale managed link was not repaired"
compgen -G "$TARGET.bak.*" >/dev/null || fail "stale managed link was not backed up"

rm "$TARGET"
printf 'foreign file\n' > "$TARGET"
if run_installer >/dev/null 2>&1; then fail "foreign file was unexpectedly replaced"; fi
grep -Fqx 'foreign file' "$TARGET" || fail "foreign file was modified"

rm "$TARGET"
ln -s /foreign/extension "$TARGET"
if run_installer >/dev/null 2>&1; then fail "foreign link was unexpectedly replaced"; fi
[ "$(readlink "$TARGET")" = /foreign/extension ] || fail "foreign link was modified"

echo 'PASS: Pi suspend guard installer exact, stale, and foreign-path contracts'
