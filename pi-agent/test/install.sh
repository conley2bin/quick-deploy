#!/bin/bash
# Smoke test for pi-agent/install.sh in a temporary PI home. A stubbed npm
# keeps pi-inline-images' dependency step offline; Pi and tmux are untouched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d /tmp/pi-agent-installer-test.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

PI_HOME="$WORK/home/.pi/agent"
git init -q "$PI_HOME"

BIN="$WORK/bin"
mkdir -p "$BIN"
cat > "$BIN/npm" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >> "$NPM_LOG"
exit 0
SH
chmod 755 "$BIN/npm"
export NPM_LOG="$WORK/npm.log"
export PATH="$BIN:$PATH"

run_one_click() {
  env PI_CODING_AGENT_DIR="$PI_HOME" PATH="$PATH" bash "$ROOT/install.sh"
}

output="$(run_one_click)"
for name in pi-inline-images pi-suspend-guard pi-tmux-window-status; do
  target="$PI_HOME/extensions/$name"
  [ -L "$target" ] || fail "one-click install did not create $name link"
  [ "$(readlink -f "$target")" = "$(readlink -f "$ROOT/extensions/$name")" ] \
    || fail "$name link target mismatch"
  case "$output" in
    *"--- $name ---"*) ;;
    *) fail "orchestrator output missing section for $name" ;;
  esac
done

# pi-inline-images depends on npm; prove its installer ran under the stub.
grep -q '^ci ' "$NPM_LOG" || fail "pi-inline-images npm ci did not run under the stub"

# Skills installer is interactive; default run must only print the hint.
case "$output" in
  *"skills/install-skills.sh"*) ;;
  *) fail "orchestrator did not print the skills hint" ;;
esac

run_one_click >/dev/null || fail "one-click re-run failed (not idempotent)"

echo 'PASS: pi-agent one-click installer links all extensions, is idempotent, prints skills hint'
