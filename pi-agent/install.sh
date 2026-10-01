#!/usr/bin/env bash
# One-click installer for everything pi-agent owns.
#
#   pi-agent/install.sh           install extensions, persist global fullscreen
#   pi-agent/install.sh --skills  also run the interactive skills installer
#
# Requires Node.js >=22.19, npm and an installed Pi; installs none of them.
# Discovers extensions/<name>/install.sh; leaf installers remain settings-free.
# After installation, only global tuiMode is set; existing fullscreen is a no-op.
# Project/CLI overrides remain separate. No live mode switch/reload/restart.
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

with_skills=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skills) with_skills=1; shift ;;
    --help|-h)
      sed -n '2,10p' "${BASH_SOURCE[0]}"
      exit 0 ;;
    *)
      echo "pi-agent install: unknown option: $1" >&2
      exit 2 ;;
  esac
done

command -v node >/dev/null 2>&1 || { echo 'pi-agent install: Node.js >=22.19 and npm must already be installed' >&2; exit 1; }
PI_EXECUTABLE=$(type -P pi) || { echo 'pi-agent install: Pi must already be installed and on PATH' >&2; exit 1; }

# Pi accepts a leading ~ in PI_CODING_AGENT_DIR; normalize it for leaf scripts
# too, so installation and configuration cannot land in different homes.
case "${PI_CODING_AGENT_DIR:-}" in
  '~') export PI_CODING_AGENT_DIR="$HOME" ;;
  '~/'*) export PI_CODING_AGENT_DIR="$HOME/${PI_CODING_AGENT_DIR:2}" ;;
esac

installers=()
for candidate in "$SCRIPT_DIR"/extensions/*/install.sh; do
  [[ -f "$candidate" ]] || continue
  installers+=("$candidate")
done

if [[ ${#installers[@]} -eq 0 ]]; then
  echo "pi-agent install: no extension installers found under $SCRIPT_DIR/extensions" >&2
  exit 1
fi

echo "Installing Pi extensions:"
for installer in "${installers[@]}"; do
  name="$(basename "$(dirname "$installer")")"
  echo
  echo "--- $name ---"
  bash "$installer"
done

echo
echo "Extensions installed."
if [[ $with_skills -eq 1 ]]; then
  echo
  echo "--- skills (interactive selection) ---"
  bash "$SCRIPT_DIR/skills/install-skills.sh"
else
  echo "Skills were not touched. To install them interactively, run:"
  echo "  $SCRIPT_DIR/skills/install-skills.sh"
fi
echo
node "$SCRIPT_DIR/configure-fullscreen.mjs" "$PI_EXECUTABLE"
echo "New Pi sessions default to fullscreen (project settings or --tui-mode can override)."
echo "The running Pi session was not changed. When ready, run /reload or restart it to load updates."
