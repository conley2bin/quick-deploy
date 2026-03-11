#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.internal/common.sh"

main() {
  need_cmd git
  sync_upstream
  ensure_codoxear_install
  load_runtime_env_from_env
  launch_codoxear_server
}

main "$@"
