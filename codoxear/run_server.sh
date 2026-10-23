#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.internal/common.sh"

usage() {
  cat <<'EOF'
usage: ./run_server.sh [--owner-groups]

每次运行都 fetch 远程 origin/main，切换到最新提交，必要时重新安装，
然后启动 codoxear-server。无法确认远程最新版本时直接失败。
--owner-groups: 可选独立负责人分组适配器，保留现有前端地址，
               上游改为私有 loopback 后端。先运行 owner-groups/setup.sh。
EOF
}

main() {
  local owner_groups=0
  if (($#)); then
    case "$1" in
      -h|--help) usage; return 0 ;;
      --owner-groups) owner_groups=1; shift ;;
      *) echo "error: 未知参数: $1" >&2; usage >&2; return 2 ;;
    esac
    if (($#)); then
      echo "error: 多余参数: $1" >&2; return 2
    fi
  fi
  if ((owner_groups)); then
    [[ -x "${SCRIPT_DIR}/owner-groups/.venv/bin/python" ]] || {
      echo "error: 请先运行 ${SCRIPT_DIR}/owner-groups/setup.sh" >&2; return 1
    }
    "${SCRIPT_DIR}/owner-groups/.venv/bin/python" -c 'import aiohttp' || return 1
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
  if ((owner_groups)); then
    exec "${SCRIPT_DIR}/owner-groups/.venv/bin/python" "${SCRIPT_DIR}/owner-groups/proxy.py" --managed-upstream
  fi
  exec "${python}" "${SCRIPT_DIR}/.internal/launch.py" server
}

main "$@"
