#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.internal/common.sh"

usage() {
  cat <<'EOF'
usage: ./run_server.sh

每次运行都 fetch 远程 origin/main，切换到最新提交，必要时重新安装，
然后启动 codoxear-server。无法确认远程最新版本时直接失败。
EOF
}

main() {
  if (($#)); then
    case "$1" in
      -h|--help) usage; return 0 ;;
      *) echo "error: 未知参数: $1" >&2; usage >&2; return 2 ;;
    esac
  fi

  [[ -f "${ENV_FILE}" ]] || {
    echo "error: 找不到 ${ENV_FILE}，请先运行 ./setup.sh" >&2
    return 1
  }
  validate_env

  local revision python
  revision="$(sync_remote_main)"
  install_remote_main_if_needed "${revision}"
  python="$(python_launcher)"
  "${python}" "${SCRIPT_DIR}/.internal/launch.py" describe "${revision}"
  exec "${python}" "${SCRIPT_DIR}/.internal/launch.py" server
}

main "$@"
