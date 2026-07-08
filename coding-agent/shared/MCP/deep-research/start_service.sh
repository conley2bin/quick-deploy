#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_DIR="${SCRIPT_DIR}"
COMPOSE_FILE="${MCP_DIR}/docker-compose.yml"
ENV_FILE="${MCP_DIR}/.env"
CODEX_CONFIG_FILE="${CODEX_CLI_CONFIG:-$HOME/.codex/config.toml}"
SYNC_MODEL_SCRIPT="${MCP_DIR}/sync_model_from_codex.py"

need_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "error: 缺少命令 ${cmd}"
    exit 1
  fi
}

need_compose() {
  if ! docker compose version >/dev/null 2>&1; then
    echo "error: 当前 docker 不支持 'docker compose'"
    exit 1
  fi
}

ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    create_env_template
    echo "warning: 已创建 ${ENV_FILE} 模板，请先填写后再重试"
    echo "note: 至少填写 OPENAI_COMPATIBLE_API_KEY；若 MCP_SEARCH_PROVIDER=tavily 还需填写 TAVILY_API_KEY"
    exit 1
  fi
}

read_codex_model() {
  local cfg="$1"
  python3 - "${cfg}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1]).expanduser()
if not path.is_file():
    raise SystemExit(2)

section_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")
model_re = re.compile(r'^\s*model\s*=\s*"([^"]+)"\s*$')
section = None
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    m_section = section_re.match(line)
    if m_section:
        section = m_section.group(1)
        continue
    if section is not None:
        continue
    m_model = model_re.match(line)
    if m_model:
        print(m_model.group(1))
        raise SystemExit(0)

raise SystemExit(3)
PY
}

create_env_template() {
  local default_model="gpt-5.5"
  local codex_model
  local openai_base_url="${OPENAI_COMPATIBLE_API_BASE_URL:-}"
  local openai_key="${OPENAI_COMPATIBLE_API_KEY:-}"
  local tavily_key="${TAVILY_API_KEY:-}"
  if codex_model="$(read_codex_model "${CODEX_CONFIG_FILE}" 2>/dev/null)"; then
    default_model="${codex_model}"
  fi

  cat >"${ENV_FILE}" <<EOF
# OpenAI 兼容网关配置（优先使用当前终端环境变量）
OPENAI_COMPATIBLE_API_BASE_URL=${openai_base_url}
OPENAI_COMPATIBLE_API_KEY=${openai_key}
TAVILY_API_KEY=${tavily_key}

# 可选：MCP 访问密码
ACCESS_PASSWORD=

# 宿主机端口映射
DEEP_RESEARCH_PORT=3000

# Deep Research MCP 配置
# MCP_AI_PROVIDER: AI 后端提供方。当前模板使用 OpenAI 兼容网关，
# 因此固定为 openaicompatible；实际网关地址和 key 来自上面的
# OPENAI_COMPATIBLE_API_BASE_URL / OPENAI_COMPATIBLE_API_KEY。
MCP_AI_PROVIDER=openaicompatible

# MCP_SEARCH_PROVIDER: 研究过程使用的搜索来源。
# 默认 tavily；可选 model, tavily, firecrawl, exa, bocha, searxng。
# 如果当前终端没有 TAVILY_API_KEY，start_service.sh 会在启动前自动
# 将 tavily 改为 model，避免服务因缺少 Tavily key 启动失败。
MCP_SEARCH_PROVIDER=tavily

# MCP_THINKING_MODEL: deep-research 用于规划、推理和拆解研究任务的模型。
# 来源：start_service.sh 首次创建 .env 时读取 Codex 配置文件顶层 model；
# 已有 .env 时，sync_model_from_codex.py 会继续从同一位置同步该字段。
# 当前读取的 Codex 配置文件：${CODEX_CONFIG_FILE}
MCP_THINKING_MODEL=${default_model}

# MCP_TASK_MODEL: deep-research 用于执行子任务、整理和生成报告的模型。
# 来源同 MCP_THINKING_MODEL，默认与 Codex 当前模型保持一致；如需让
# deep-research 使用不同模型，可手动修改此值。
MCP_TASK_MODEL=${default_model}
EOF
}

read_env_value() {
  local key="$1"
  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    echo ""
    return
  fi
  line="${line#*=}"
  line="$(printf '%s' "${line}" | sed -E 's/^[[:space:]]+//;s/[[:space:]]+$//')"
  if [[ "${line}" =~ ^\".*\"$ ]]; then
    line="${line:1:${#line}-2}"
  elif [[ "${line}" =~ ^\'.*\'$ ]]; then
    line="${line:1:${#line}-2}"
  fi
  echo "${line}"
}

upsert_env_key() {
  local key="$1"
  local value="$2"
  python3 - "${ENV_FILE}" "${key}" "${value}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
lines = path.read_text(encoding="utf-8").splitlines()
for i, line in enumerate(lines):
    if pat.match(line):
        lines[i] = f"{key}={value}"
        break
else:
    lines.append(f"{key}={value}")

path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

overlay_runtime_env() {
  if [[ -n "${OPENAI_COMPATIBLE_API_KEY:-}" ]]; then
    upsert_env_key "OPENAI_COMPATIBLE_API_KEY" "${OPENAI_COMPATIBLE_API_KEY}"
  fi
  if [[ -n "${TAVILY_API_KEY:-}" ]]; then
    upsert_env_key "TAVILY_API_KEY" "${TAVILY_API_KEY}"
  fi
}

adjust_search_provider() {
  local provider
  provider="$(read_env_value MCP_SEARCH_PROVIDER)"
  if [[ -z "${provider}" ]]; then
    provider="tavily"
    upsert_env_key "MCP_SEARCH_PROVIDER" "${provider}"
  fi

  if [[ "${provider}" != "tavily" ]]; then
    return
  fi

  if [[ -n "${TAVILY_API_KEY:-}" ]]; then
    return
  fi

  upsert_env_key "MCP_SEARCH_PROVIDER" "model"
  echo "warning: 当前终端环境变量缺少 TAVILY_API_KEY，已将 MCP_SEARCH_PROVIDER 从 tavily 切换为 model"
  echo "note: MCP_SEARCH_PROVIDER 可选值：model, tavily, firecrawl, exa, bocha, searxng"
}

sync_model_from_codex() {
  if [[ ! -f "${CODEX_CONFIG_FILE}" ]]; then
    echo "warning: 找不到 ${CODEX_CONFIG_FILE}，跳过 model 同步"
    return
  fi
  if [[ ! -f "${SYNC_MODEL_SCRIPT}" ]]; then
    echo "error: 找不到 ${SYNC_MODEL_SCRIPT}"
    exit 1
  fi
  python3 "${SYNC_MODEL_SCRIPT}" \
    --codex-config "${CODEX_CONFIG_FILE}" \
    --env-file "${ENV_FILE}"
}

validate_env() {
  local openai_key
  local search_provider
  local tavily_key

  openai_key="$(read_env_value OPENAI_COMPATIBLE_API_KEY)"
  if [[ -z "${openai_key}" ]]; then
    echo "error: ${ENV_FILE} 未设置 OPENAI_COMPATIBLE_API_KEY"
    exit 1
  fi

  search_provider="$(read_env_value MCP_SEARCH_PROVIDER)"
  if [[ "${search_provider}" == "tavily" ]]; then
    tavily_key="$(read_env_value TAVILY_API_KEY)"
    if [[ -z "${tavily_key}" ]]; then
      echo "error: MCP_SEARCH_PROVIDER=tavily 时必须设置 TAVILY_API_KEY"
      exit 1
    fi
  fi
}

enable_docker_boot() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "warning: 未检测到 systemctl，跳过 docker 开机自启设置"
    return
  fi

  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl enable --now docker
    return
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    echo "warning: 当前非 root 且无 sudo，跳过 docker 开机自启设置"
    return
  fi

  sudo systemctl enable --now docker
}

start_service() {
  docker compose \
    -f "${COMPOSE_FILE}" \
    --project-directory "${MCP_DIR}" \
    up -d
}

print_hints() {
  echo ""
  echo "MCP 服务状态:"
  docker compose -f "${COMPOSE_FILE}" --project-directory "${MCP_DIR}" ps
  echo ""
  echo "中断/停止服务命令:"
  echo "  cd ${MCP_DIR} && docker compose stop"
  echo "  cd ${MCP_DIR} && docker compose down"
  echo ""
  echo "查看实时日志（退出日志查看用 Ctrl+C）:"
  echo "  cd ${MCP_DIR} && docker compose logs -f"
  echo ""
  echo "取消自启命令:"
  echo "  docker update --restart=no deep-research"
  echo "  sudo systemctl disable --now docker"
  echo ""
  echo "恢复自启命令:"
  echo "  docker update --restart=unless-stopped deep-research"
  echo "  sudo systemctl enable --now docker"
}

main() {
  need_cmd python3
  ensure_env_file
  need_cmd docker
  need_compose
  sync_model_from_codex
  overlay_runtime_env
  adjust_search_provider
  validate_env
  enable_docker_boot
  start_service
  print_hints
}

main "$@"
