#!/usr/bin/env bash

RUN_SERVER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${RUN_SERVER_LIB_DIR}/common.sh"

run_server_usage() {
  cat <<'EOF'
usage: ./run_server.sh [--skip-sync]
EOF
}

parse_run_server_args() {
  RUN_SERVER_SYNC=1
  RUN_SERVER_SHOW_HELP=0

  while (($#)); do
    case "$1" in
      --skip-sync)
        RUN_SERVER_SYNC=0
        ;;
      -h|--help)
        RUN_SERVER_SHOW_HELP=1
        ;;
      *)
        echo "error: 未知参数: $1" >&2
        run_server_usage >&2
        return 2
        ;;
    esac
    shift
  done
}

run_server_main() {
  parse_run_server_args "$@" || return $?

  if [[ "${RUN_SERVER_SHOW_HELP}" == "1" ]]; then
    run_server_usage
    return 0
  fi

  need_cmd git

  if [[ "${RUN_SERVER_SYNC}" == "1" ]]; then
    sync_upstream
  else
    require_local_upstream_checkout
  fi

  ensure_codoxear_install
  load_runtime_env_from_env
  launch_codoxear_server
}
