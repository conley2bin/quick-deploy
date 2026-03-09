#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

if [[ -z "${CODEX_HOME:-}" && -f "${SCRIPT_DIR}/.env" ]]; then
  _cxh_line="$(grep -E '^[[:space:]]*CODEX_HOME[[:space:]]*=' "${SCRIPT_DIR}/.env" | tail -n 1 || true)"
  if [[ -n "${_cxh_line}" ]]; then
    CODEX_HOME="${_cxh_line#*=}"
    CODEX_HOME="${CODEX_HOME#\"}"
    CODEX_HOME="${CODEX_HOME%\"}"
    CODEX_HOME="${CODEX_HOME#\'}"
    CODEX_HOME="${CODEX_HOME%\'}"
    export CODEX_HOME
  fi
fi

if [[ -n "${CODEX_HOME:-}" ]]; then
  CODEX_HOME="${CODEX_HOME//\$\{HOME\}/${HOME}}"
  CODEX_HOME="${CODEX_HOME//\$HOME/${HOME}}"
  case "${CODEX_HOME}" in
    "~") CODEX_HOME="${HOME}" ;;
    "~/"*) CODEX_HOME="${HOME}/${CODEX_HOME#\~/}" ;;
  esac
  export CODEX_HOME
fi

if command -v codoxear-server >/dev/null 2>&1; then
  exec codoxear-server
fi

if [[ -x "${REPO_ROOT}/.venv/bin/codoxear-server" ]]; then
  exec "${REPO_ROOT}/.venv/bin/codoxear-server"
fi

echo "error: codoxear-server 未找到。请先运行 ./coding-agent/shared/tools/codoxear/setup.sh"
exit 127
