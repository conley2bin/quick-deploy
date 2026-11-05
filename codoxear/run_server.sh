#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.internal/common.sh"

usage() {
  cat <<'EOF'
usage: ./run_server.sh [--no-extensions]

默认启用共享 Web 宿主及会话接力扩展，保留现有前端地址，上游运行在私有 loopback。
每次启动前都 fetch 远程 origin/main，切换到最新提交，必要时重新安装。
无法确认远程最新版本、共享宿主依赖缺失或启动失败时直接报错，不自动回退。
先运行 ./setup.sh 安装全部依赖；已有在线服务需安排重启才切换。
--no-extensions:   显式回退到原生 codoxear-server，不依赖任何扩展。
EOF
}

main() {
  local extensions=1 host_python="${SCRIPT_DIR}/web-host/.venv/bin/python"
  if (($#)); then
    if (($# > 1)); then
      echo "error: 多余参数: $2" >&2; usage >&2; return 2
    fi
    case "$1" in
      -h|--help) usage; return 0 ;;
      --no-extensions) extensions=0 ;;
      *) echo "error: 未知参数: $1" >&2; usage >&2; return 2 ;;
    esac
  fi
  if ((extensions)); then
    [[ -x "${host_python}" ]] || {
      echo "error: 共享宿主依赖未安装，请先运行 ${SCRIPT_DIR}/setup.sh" >&2; return 1
    }
    if ! "${host_python}" -c 'import aiohttp'; then
      echo "error: 共享宿主依赖不可用，请重新运行 ${SCRIPT_DIR}/setup.sh" >&2
      return 1
    fi
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
  if ((extensions)); then
    exec "${host_python}" "${SCRIPT_DIR}/web-host/host.py" --managed-upstream
  fi
  exec "${python}" "${SCRIPT_DIR}/.internal/launch.py" server
}

main "$@"
