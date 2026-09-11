#!/usr/bin/env bash
# One-click installer for everything pi-agent owns.
#
#   pi-agent/install.sh           install every extension that ships install.sh
#   pi-agent/install.sh --skills  then also run the interactive skills installer
#
# Extensions are discovered generically as pi-agent/extensions/<name>/install.sh;
# adding an extension with its own install.sh makes it part of the one-click
# flow with no edits here. Sub-installers are idempotent and refuse foreign or
# Git-index-owned targets. Nothing here reloads or restarts Pi.
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
echo "When ready, run /reload in Pi (or restart it) to load updated extensions and skills."
