#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="/home/conley/quick-deploy/coding-agent/shared/tools/codoxear"
cd "/home/conley/quick-deploy/coding-agent/shared/tools/codoxear"

if command -v codoxear-server >/dev/null 2>&1; then
  exec codoxear-server
fi

if [[ -x "/home/conley/quick-deploy/.venv/bin/codoxear-server" ]]; then
  exec "/home/conley/quick-deploy/.venv/bin/codoxear-server"
fi

echo "error: codoxear-server 未找到。请先运行 ./coding-agent/shared/tools/codoxear/setup.sh"
exit 127
