#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.internal/common.sh"

usage() {
  cat <<'EOF'
usage: ./setup.sh [--repo-url URL]

首次同步远程 main、从上游 .env.example 创建本地 .env、安装 Codoxear，
并向当前 shell rc 写入 codox/piox 函数。
EOF
}

main() {
  while (($#)); do
    case "$1" in
      --repo-url)
        [[ $# -ge 2 ]] || { echo "error: --repo-url requires a value" >&2; return 2; }
        UPSTREAM_REPO_URL="$2"
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        echo "error: 未知参数: $1" >&2
        usage >&2
        return 2
        ;;
    esac
  done

  need_cmd git
  need_cmd python3
  local revision
  revision="$(sync_remote_main)"
  ensure_env_file
  install_remote_main_if_needed "${revision}"

  local rc_file python
  case "$(basename "${SHELL:-}")" in
    bash) rc_file="${HOME}/.bashrc" ;;
    zsh) rc_file="${HOME}/.zshrc" ;;
    *) rc_file="${HOME}/.zshrc" ;;
  esac
  python="$(python_launcher)"
  "${python}" "${SCRIPT_DIR}/.internal/update_shell_rc.py" \
    "${rc_file}" "${python}" "${SCRIPT_DIR}/.internal/launch.py"

  echo "codoxear revision: ${revision}"
  echo "server: ${SCRIPT_DIR}/run_server.sh"
  echo "shell reload: source ${rc_file}"
}

main "$@"
