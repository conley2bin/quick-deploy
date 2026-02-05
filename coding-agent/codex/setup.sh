#!/usr/bin/env bash
#
# Codex 快速部署脚本
#
# 作用：
# - 交互式选择要启用的 MCP servers
# - 自动写入/更新 ~/.codex/config.toml 里的 mcp_servers 配置（只写入你选择的项）
# - 安装全局 AGENTS.md（系统级）
# - 同步自定义 commands（以 /prompts:<name> 方式调用）
#
# 注意：
# - mu-mcp/tavily/morph 需要对应 API key 才能正常启动（脚本可选写入 ~/.zshrc，不会回显 key 内容）
# - 改完配置后，需要重启 codex 才会生效
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
CODEX_CLI_CONFIG="${CODEX_CLI_CONFIG:-${CODEX_HOME}/config.toml}"

PILOTY_DIR="${REPO_ROOT}/coding-agent/shared/MCP/PiloTY"
MU_MCP_DIR="${REPO_ROOT}/coding-agent/shared/MCP/mu-mcp"

ZSHRC_PATH="${ZSHRC_PATH:-${HOME}/.zshrc}"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"
}

mkdir -p "${CODEX_HOME}"

need_cmd codex
need_cmd python3
need_cmd zsh

if [[ ! -f "${ZSHRC_PATH}" ]]; then
  echo "warning: 未找到 ${ZSHRC_PATH}（将无法从 ~/.zshrc 检查/读取 API key）"
fi

backup_file_if_exists() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    cp -f "${path}" "${path}.backup.$(date +%Y%m%d_%H%M%S)"
  fi
}

install_global_agents() {
  local src="${REPO_ROOT}/coding-agent/codex/AGENTS.md"
  local dst="${CODEX_HOME}/AGENTS.md"
  local override="${CODEX_HOME}/AGENTS.override.md"

  [[ -f "${src}" ]] || die "找不到 ${src}"
  mkdir -p "${CODEX_HOME}"

  if [[ -f "${override}" ]]; then
    echo "note: 检测到 ${override}，Codex 将优先使用它而不是 ${dst}"
  fi

  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    echo "Global AGENTS.md: 未变化"
    return 0
  fi

  backup_file_if_exists "${dst}"
  cp -f "${src}" "${dst}"
  echo "Global AGENTS.md: 已更新到 ${dst}"
}

install_custom_prompts() {
  local src_dir="${REPO_ROOT}/coding-agent/codex/commands"
  local dst_dir="${CODEX_HOME}/prompts"

  [[ -d "${src_dir}" ]] || die "找不到目录 ${src_dir}"
  mkdir -p "${dst_dir}"

  shopt -s nullglob
  local files=("${src_dir}"/*.md)
  shopt -u nullglob

  if ((${#files[@]} == 0)); then
    echo "Custom prompts: 未找到 ${src_dir}/*.md"
    return 0
  fi

  local copied=0
  local names=()
  for f in "${files[@]}"; do
    local base
    base="$(basename "${f}")"
    names+=("${base%.md}")
    if [[ -f "${dst_dir}/${base}" ]] && cmp -s "${f}" "${dst_dir}/${base}"; then
      continue
    fi
    cp -f "${f}" "${dst_dir}/${base}"
    copied=$((copied + 1))
  done

  echo "Custom prompts: 已安装到 ${dst_dir}（更新/覆盖 ${copied} 个文件）"
  if ((${#names[@]} > 0)); then
    echo "自定义 commands 使用方式："
    for n in "${names[@]}"; do
      echo "  - /prompts:${n}"
    done
  fi
}

# 固定顺序：
# 1) 安装系统级 AGENTS.md
# 2) 同步自定义 commands
# 3) 再做 MCP 配置
echo "[1/3] install system AGENTS.md"
install_global_agents
echo ""
echo "[2/3] sync custom commands"
install_custom_prompts
echo ""
echo "[3/3] configure MCP servers"

echo "请选择要配置到 Codex 的 MCP servers："
echo ""
echo "  1) piloty               (本地，PTY 终端控制)"
echo "  2) mu_mcp               (本地，OpenRouter 聊天；需要 OPENROUTER_API_KEY)"
echo "  3) sequential-thinking  (STDIO，npx @modelcontextprotocol/server-sequential-thinking)"
echo "  4) tavily               (HTTP，Tavily 搜索 MCP；需要 Tavily API key)"
echo "  5) morphllm-fast-apply  (STDIO，需要 MORPH_API_KEY)"
echo "  6) context7             (STDIO，最新文档检索)"
echo "  7) serena               (STDIO，代码语义工具；需要 uvx)"
echo ""
echo "输入：1,3,5    或 all    或 n"
printf "> "
read -r selection_raw || true
selection_raw="$(echo "${selection_raw:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"

if [[ "${selection_raw}" == "n" || -z "${selection_raw}" ]]; then
  echo "未选择任何 MCP（跳过 MCP 配置）。"
  exit 0
fi

selected_csv=""
if [[ "${selection_raw}" == "all" ]]; then
  selected_csv="1,2,3,4,5,6,7"
else
  if [[ ! "${selection_raw}" =~ ^[0-9,]+$ ]]; then
    die "输入格式错误：${selection_raw}（应为 1,3,5 / all / n）"
  fi
  selected_csv="${selection_raw}"
fi

has_choice() {
  # usage: has_choice 3
  local n="$1"
  IFS=',' read -r -a arr <<<"${selected_csv}"
  for x in "${arr[@]}"; do
    [[ "${x}" == "${n}" ]] && return 0
  done
  return 1
}

zshrc_value() {
  local var="$1"
  [[ "${var}" =~ ^[A-Z0-9_]+$ ]] || die "非法 env var 名称：${var}"
  # 注意：这里只做“读取”，不向当前 shell 注入环境变量
  zsh -lc "source \"${ZSHRC_PATH}\" >/dev/null 2>&1 || true; print -r -- \"\${${var}:-}\"" 2>/dev/null || true
}

show_key_status() {
  local mcp_name="$1"
  local env_var="$2"
  local v_env="${!env_var-}"
  local v_zsh=""
  v_zsh="$(zshrc_value "${env_var}")"

  if [[ -n "${v_env}" ]]; then
    printf "  - %-8s 需要 %-20s : 已设置（当前环境）\n" "${mcp_name}" "${env_var}"
  elif [[ -n "${v_zsh}" ]]; then
    printf "  - %-8s 需要 %-20s : 已设置（~/.zshrc）\n" "${mcp_name}" "${env_var}"
  else
    printf "  - %-8s 需要 %-20s : 缺失\n" "${mcp_name}" "${env_var}"
  fi
}

upsert_keys_to_zshrc() {
  local zshrc="$1"
  shift
  (("$#" > 0)) || die "内部错误：没有要写入的 key"

  python3 - "${zshrc}" "$@" <<'PY'
import re
import sys
from datetime import datetime
from pathlib import Path

zshrc_path = Path(sys.argv[1]).expanduser()
pair_args = sys.argv[2:]

marker_begin = "# >>> codex mcp api keys >>>"
marker_end = "# <<< codex mcp api keys <<<"

def escape_for_double_quotes(value: str) -> str:
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    value = value.replace("$", "\\$")
    value = value.replace("`", "\\`")
    return value

pairs = []
for item in pair_args:
    if "=" not in item:
        raise SystemExit(f"invalid pair: {item}")
    k, v = item.split("=", 1)
    if not re.fullmatch(r"[A-Z0-9_]+", k):
        raise SystemExit(f"invalid env var name: {k}")
    pairs.append((k, v))

if not pairs:
    raise SystemExit("no pairs")

if zshrc_path.exists():
    original = zshrc_path.read_text(encoding="utf-8", errors="replace")
else:
    original = ""

backup_path = zshrc_path.with_name(zshrc_path.name + ".backup." + datetime.now().strftime("%Y%m%d_%H%M%S"))
if original:
    backup_path.write_text(original, encoding="utf-8")

def render_block(pairs: list[tuple[str, str]]) -> str:
    lines = [marker_begin]
    for k, v in pairs:
        lines.append(f'export {k}="{escape_for_double_quotes(v)}"')
    lines.append(marker_end)
    return "\n".join(lines) + "\n"

block = render_block(pairs)

pat = re.compile(rf"(?ms)^{re.escape(marker_begin)}\n.*?^{re.escape(marker_end)}\n?")
if pat.search(original):
    updated = pat.sub(block, original, count=1)
else:
    updated = original
    if updated and not updated.endswith("\n"):
        updated += "\n"
    if updated and not updated.endswith("\n\n"):
        updated += "\n"
    updated += block

zshrc_path.parent.mkdir(parents=True, exist_ok=True)
zshrc_path.write_text(updated, encoding="utf-8")
print(str(zshrc_path))
PY
}

# 运行后输出：需要 key 的 MCP 及当前是否已设置（不回显 key 内容）
echo ""
echo "API key 检查结果（只检查你本机环境/~/.zshrc；不会输出 key 内容）："
show_key_status "mu_mcp" "OPENROUTER_API_KEY"
show_key_status "tavily" "TAVILY_API_KEY"
show_key_status "morph" "MORPH_API_KEY"

# 只针对“你选择的项”检查是否缺 key，并提供补全
missing_pairs=()
missing_hints=()
if has_choice 2; then
  if [[ -z "${OPENROUTER_API_KEY-}" && -z "$(zshrc_value OPENROUTER_API_KEY)" ]]; then
    missing_pairs+=("OPENROUTER_API_KEY")
    missing_hints+=("mu_mcp:OPENROUTER_API_KEY")
  fi
fi
if has_choice 4; then
  if [[ -z "${TAVILY_API_KEY-}" && -z "$(zshrc_value TAVILY_API_KEY)" ]]; then
    missing_pairs+=("TAVILY_API_KEY")
    missing_hints+=("tavily:TAVILY_API_KEY")
  fi
fi
if has_choice 5; then
  if [[ -z "${MORPH_API_KEY-}" && -z "$(zshrc_value MORPH_API_KEY)" ]]; then
    missing_pairs+=("MORPH_API_KEY")
    missing_hints+=("morph:MORPH_API_KEY")
  fi
fi

if ((${#missing_pairs[@]} > 0)); then
  echo ""
  echo "检测到你选择的 MCP 缺少 API key："
  for item in "${missing_hints[@]}"; do
    mcp="${item%%:*}"
    var="${item##*:}"
    echo "  - ${mcp} 需要 ${var}"
  done
  echo ""
  printf "是否现在填写并写入 %s ？[y/N] " "${ZSHRC_PATH}"
  read -r fill_now || true
  fill_now="$(echo "${fill_now:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"

  if [[ "${fill_now}" == "y" || "${fill_now}" == "yes" ]]; then
    [[ -f "${ZSHRC_PATH}" ]] || die "找不到 ${ZSHRC_PATH}（请先创建或使用 ZSHRC_PATH 指定路径）"

    to_write_args=()
    did_write_keys="0"
    for item in "${missing_hints[@]}"; do
      mcp="${item%%:*}"
      var="${item##*:}"
      echo ""
      echo "请粘贴 ${mcp} 的 ${var}："
      printf "> "
      read -r val || true
      val="${val:-}"
      if [[ -z "${val}" ]]; then
        echo "跳过：${var} 为空"
        continue
      fi
      to_write_args+=("${var}=${val}")
    done

    if ((${#to_write_args[@]} > 0)); then
      echo ""
      echo "正在写入 ${ZSHRC_PATH}（会自动备份为 *.backup.TIMESTAMP）..."
      _written_path="$(upsert_keys_to_zshrc "${ZSHRC_PATH}" "${to_write_args[@]}")"
      did_write_keys="1"
      echo "已写入：${_written_path}"
      echo "提示：新开终端或执行：source ${ZSHRC_PATH}"
    fi
  else
    echo "已选择不填写 key：将继续配置 MCP（缺 key 的 MCP 可能无法握手启动）。"
  fi
fi

if [[ "${did_write_keys:-0}" == "1" ]]; then
  echo ""
  echo "API key 检查结果（更新后）："
  show_key_status "mu_mcp" "OPENROUTER_API_KEY"
  show_key_status "tavily" "TAVILY_API_KEY"
  show_key_status "morph" "MORPH_API_KEY"
fi

# 依赖检查（只检查你选择的项）
if has_choice 1 || has_choice 2; then
  need_cmd uv
  need_cmd git
fi
if has_choice 3 || has_choice 5 || has_choice 6; then
  need_cmd npx
fi
if has_choice 7; then
  need_cmd uvx
fi

if has_choice 1 || has_choice 2; then
  if [[ -d "${REPO_ROOT}/.git" ]]; then
    git -C "${REPO_ROOT}" submodule update --init --recursive >/dev/null
  fi
  [[ -f "${PILOTY_DIR}/pyproject.toml" ]] || die "找不到 ${PILOTY_DIR}/pyproject.toml（子模块未检出？）"
  [[ -f "${MU_MCP_DIR}/server.py" ]] || die "找不到 ${MU_MCP_DIR}/server.py（子模块未检出？）"
fi

tavily_key="${TAVILY_API_KEY:-}"
if [[ -z "${tavily_key}" ]]; then
  tavily_key="$(zshrc_value TAVILY_API_KEY)"
fi

python3 - "${CODEX_CLI_CONFIG}" "${PILOTY_DIR}" "${MU_MCP_DIR}" "${selected_csv}" "${tavily_key}" <<'PY'
import re
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
piloty_dir = sys.argv[2]
mu_dir = sys.argv[3]
selected_csv = sys.argv[4]
tavily_key = sys.argv[5]

config_path.parent.mkdir(parents=True, exist_ok=True)
text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

want = {int(x) for x in selected_csv.split(",") if x}

def selected(n: int) -> bool:
    return n in want

def upsert_mcp_stdio(
    text: str,
    name: str,
    command: str,
    args_toml: str,
    cwd: str | None = None,
    env_vars_toml: str | None = None,
    startup_timeout_sec: int | None = None,
) -> str:
    header = f"[mcp_servers.{name}]"
    block_lines: list[str] = [
        header,
        f'command = "{command}"',
        f"args = {args_toml}",
    ]
    if cwd:
        block_lines.append(f'cwd = "{cwd}"')
    if env_vars_toml:
        block_lines.append(f"env_vars = {env_vars_toml}")
    if startup_timeout_sec is not None:
        block_lines.append(f"startup_timeout_sec = {startup_timeout_sec}")
    block = "\n".join(block_lines) + "\n"

    pat = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    if pat.search(text):
        return pat.sub(block + "\n", text, count=1)

    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# MCP servers\n" + block
    return text

def upsert_mcp_http_url(text: str, name: str, url: str) -> str:
    header = f"[mcp_servers.{name}]"
    block = f'{header}\nurl = "{url}"\n'

    pat = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    if pat.search(text):
        return pat.sub(block + "\n", text, count=1)

    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# MCP servers\n" + block
    return text

if selected(1):
    text = upsert_mcp_stdio(
        text,
        name="piloty",
        command="uv",
        args_toml='["run","python","-m","piloty.mcp_server"]',
        cwd=piloty_dir,
    )

if selected(2):
    text = upsert_mcp_stdio(
        text,
        name="mu_mcp",
        command="uv",
        args_toml='["run","python","server.py"]',
        cwd=mu_dir,
        env_vars_toml='["OPENROUTER_API_KEY"]',
    )

if selected(3):
    text = upsert_mcp_stdio(
        text,
        name="sequentialthinking",
        command="npx",
        args_toml='["-y","@modelcontextprotocol/server-sequential-thinking"]',
        startup_timeout_sec=60,
    )

if selected(4) and tavily_key.strip():
    url = f"https://mcp.tavily.com/mcp/?tavilyApiKey={tavily_key.strip()}"
    text = upsert_mcp_http_url(text, name="tavily", url=url)

if selected(5):
    text = upsert_mcp_stdio(
        text,
        name="morph",
        command="npx",
        args_toml='["-y","@morphllm/morphmcp"]',
        env_vars_toml='["MORPH_API_KEY"]',
        # npx 首次启动可能需要较长时间下载/安装依赖（例如 ripgrep 预构建包）
        startup_timeout_sec=300,
    )

if selected(6):
    text = upsert_mcp_stdio(
        text,
        name="context7",
        command="npx",
        args_toml='["-y","@upstash/context7-mcp"]',
        startup_timeout_sec=60,
    )

if selected(7):
    text = upsert_mcp_stdio(
        text,
        name="serena",
        command="uvx",
        args_toml='["--from","git+https://github.com/oraios/serena","serena","start-mcp-server","--context","codex"]',
        startup_timeout_sec=180,
    )

config_path.write_text(text, encoding="utf-8")
print(f"Wrote {config_path}")
PY

is_key_set() {
  local env_var="$1"
  [[ -n "${!env_var-}" ]] && return 0
  [[ -n "$(zshrc_value "${env_var}")" ]] && return 0
  return 1
}

echo ""
echo "wrote Codex config: ${CODEX_CLI_CONFIG}"
echo "wrote MCP config sections for selected servers"

skipped=()
if has_choice 4 && [[ -z "${tavily_key}" ]]; then
  skipped+=("tavily")
fi

missing_selected=()
if has_choice 2 && ! is_key_set "OPENROUTER_API_KEY"; then
  missing_selected+=("mu_mcp(OPENROUTER_API_KEY)")
fi
if has_choice 4 && ! is_key_set "TAVILY_API_KEY"; then
  missing_selected+=("tavily(TAVILY_API_KEY)")
fi
if has_choice 5 && ! is_key_set "MORPH_API_KEY"; then
  missing_selected+=("morph(MORPH_API_KEY)")
fi

if ((${#skipped[@]} > 0)); then
  echo ""
  echo "warning: 本次有部分 MCP 配置未写入（因为缺少必需的 API key）："
  for name in "${skipped[@]}"; do
    echo "  - ${name}"
  done
fi

if ((${#missing_selected[@]} > 0)); then
  echo ""
  echo "warning: 你选择的 MCP 仍有缺失的 API key（可能导致启动握手失败）："
  for item in "${missing_selected[@]}"; do
    echo "  - ${item}"
  done
  echo "  建议：在 ${ZSHRC_PATH} 补全后，执行：source ${ZSHRC_PATH}（或新开终端）再重启 codex。"
fi

echo ""
echo "下一步："
echo "  1) 重启 codex"
echo "  2) 验证：codex mcp list"
