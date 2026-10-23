#!/usr/bin/env bash
# Independent dependencies only. Does not start or alter Codoxear.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing --python python3 "${HERE}/.venv"
  uv pip install --python "${HERE}/.venv/bin/python" -r "${HERE}/requirements.txt"
else
  python3 -m venv "${HERE}/.venv"
  "${HERE}/.venv/bin/python" -m pip install -r "${HERE}/requirements.txt"
fi
printf '\nReady. Optional activation at next approved restart:\n  %s/../run_server.sh --owner-groups\n' "${HERE}"
