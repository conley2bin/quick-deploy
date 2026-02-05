#!/usr/bin/env bash
#
# Claude Code 快速部署脚本
#
# 作用：
# - 交互式选择要启用的 MCP servers
# - 自动写入/更新 ~/.claude/settings.local.json 里的 mcpServers 配置（只写入你选择的项）
# - 安装全局 CLAUDE.md（系统级）
# - 同步自定义 commands（以 /command-name 方式调用）
#
# 注意：
# - mu-mcp 需要 OPENROUTER_API_KEY 才能正常启动（脚本可选写入 ~/.zshrc，不会回显 key 内容）
# - 改完配置后，需要重启 claude 才会生效
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

CLAUDE_HOME="${CLAUDE_HOME:-${HOME}/.claude}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-${CLAUDE_HOME}/settings.local.json}"

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

mkdir -p "${CLAUDE_HOME}"

need_cmd claude
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

install_global_claude_md() {
  local src="${SCRIPT_DIR}/CLAUDE.md"
  local dst="${CLAUDE_HOME}/CLAUDE.md"

  [[ -f "${src}" ]] || die "找不到 ${src}"
  mkdir -p "${CLAUDE_HOME}"

  if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
    echo "Global CLAUDE.md: 未变化"
    return 0
  fi

  backup_file_if_exists "${dst}"
  cp -f "${src}" "${dst}"
  echo "Global CLAUDE.md: 已更新到 ${dst}"
}

install_custom_commands() {
  local src_dir="${SCRIPT_DIR}/commands"
  local dst_dir="${CLAUDE_HOME}/commands"

  [[ -d "${src_dir}" ]] || die "找不到目录 ${src_dir}"
  mkdir -p "${dst_dir}"

  shopt -s nullglob
  local files=("${src_dir}"/*.md)
  shopt -u nullglob

  if ((${#files[@]} == 0)); then
    echo "Custom commands: 未找到 ${src_dir}/*.md"
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

  echo "Custom commands: 已安装到 ${dst_dir}（更新/覆盖 ${copied} 个文件）"
  if ((${#names[@]} > 0)); then
    echo "自定义 commands 使用方式："
    for n in "${names[@]}"; do
      echo "  - /${n}"
    done
  fi
}

# 固定顺序：
# 1) 安装系统级 CLAUDE.md
# 2) 同步自定义 commands
# 3) 再做 MCP 配置
echo "[1/3] install system CLAUDE.md"
install_global_claude_md
echo ""
echo "[2/3] sync custom commands"
install_custom_commands
echo ""
echo "[3/3] configure MCP servers"

echo "请选择要配置到 Claude Code 的 MCP servers："
echo ""
echo "  1) piloty               (本地，PTY 终端控制)"
echo "  2) mu_mcp               (本地，OpenRouter 聊天；需要 OPENROUTER_API_KEY)"
echo ""
echo "输入：1,2    或 all    或 n"
printf "> "
read -r selection_raw || true
selection_raw="$(echo "${selection_raw:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"

if [[ "${selection_raw}" == "n" || -z "${selection_raw}" ]]; then
  echo "未选择任何 MCP（跳过 MCP 配置）。"
  exit 0
fi

selected_csv=""
if [[ "${selection_raw}" == "all" ]]; then
  selected_csv="1,2"
else
  if [[ ! "${selection_raw}" =~ ^[0-9,]+$ ]]; then
    die "输入格式错误：${selection_raw}（应为 1,2 / all / n）"
  fi
  selected_csv="${selection_raw}"
fi

has_choice() {
  # usage: has_choice 2
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
  # 注意：这里只做"读取"，不向当前 shell 注入环境变量
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

marker_begin = "# >>> claude mcp api keys >>>"
marker_end = "# <<< claude mcp api keys <<<"

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

# 只针对"你选择的项"检查是否缺 key，并提供补全
missing_pairs=()
missing_hints=()
if has_choice 2; then
  if [[ -z "${OPENROUTER_API_KEY-}" && -z "$(zshrc_value OPENROUTER_API_KEY)" ]]; then
    missing_pairs+=("OPENROUTER_API_KEY")
    missing_hints+=("mu_mcp:OPENROUTER_API_KEY")
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
fi

# 依赖检查（只检查你选择的项）
if has_choice 1 || has_choice 2; then
  need_cmd uv
  need_cmd git
fi

if has_choice 1 || has_choice 2; then
  if [[ -d "${REPO_ROOT}/.git" ]]; then
    git -C "${REPO_ROOT}" submodule update --init --recursive >/dev/null
  fi
  [[ -f "${PILOTY_DIR}/pyproject.toml" ]] || die "找不到 ${PILOTY_DIR}/pyproject.toml（子模块未检出？）"
  [[ -f "${MU_MCP_DIR}/server.py" ]] || die "找不到 ${MU_MCP_DIR}/server.py（子模块未检出？）"
fi

# 使用 claude mcp add 命令注册 MCP 服务器
if has_choice 1; then
  echo "注册 piloty MCP 服务器..."
  # 如果已存在则先删除
  claude mcp remove piloty -s user 2>/dev/null || true
  (cd "${PILOTY_DIR}" && claude mcp add --scope user piloty -- uv run piloty) || die "piloty 注册失败"
fi

if has_choice 2; then
  echo "注册 mu_mcp MCP 服务器..."
  # 如果已存在则先删除
  claude mcp remove mu_mcp -s user 2>/dev/null || true
  (cd "${MU_MCP_DIR}" && claude mcp add --scope user mu_mcp -- uv run python server.py) || die "mu_mcp 注册失败"
fi

# 手动添加 working_directory 和环境变量配置到 ~/.claude.json
# 注意：Claude Code 不支持 working_directory 字段，需要使用 shell 包装器
python3 - "${PILOTY_DIR}" "${MU_MCP_DIR}" "${selected_csv}" <<'PY'
import json
import sys
from pathlib import Path

piloty_dir = sys.argv[1]
mu_dir = sys.argv[2]
selected_csv = sys.argv[3]

want = {int(x) for x in selected_csv.split(",") if x}

claude_json = Path.home() / ".claude.json"
if not claude_json.exists():
    sys.exit(0)

config = json.loads(claude_json.read_text(encoding="utf-8"))
if "mcpServers" not in config:
    sys.exit(0)

modified = False

# 使用 shell 包装器指定工作目录
if 1 in want and "piloty" in config["mcpServers"]:
    config["mcpServers"]["piloty"]["command"] = "bash"
    config["mcpServers"]["piloty"]["args"] = ["-c", f"cd {piloty_dir} && uv run piloty"]
    modified = True
    print(f"已配置 piloty 工作目录: {piloty_dir}")

if 2 in want and "mu_mcp" in config["mcpServers"]:
    config["mcpServers"]["mu_mcp"]["command"] = "bash"
    config["mcpServers"]["mu_mcp"]["args"] = ["-c", f"cd {mu_dir} && uv run python server.py"]
    config["mcpServers"]["mu_mcp"]["env"] = {"OPENROUTER_API_KEY": "${OPENROUTER_API_KEY}"}
    modified = True
    print(f"已配置 mu_mcp 工作目录: {mu_dir}")
    print("已添加 mu_mcp 环境变量配置")

if modified:
    claude_json.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

is_key_set() {
  local env_var="$1"
  [[ -n "${!env_var-}" ]] && return 0
  [[ -n "$(zshrc_value "${env_var}")" ]] && return 0
  return 1
}

echo ""
echo "已注册 MCP 服务器到 ~/.claude.json"

missing_selected=()
if has_choice 2 && ! is_key_set "OPENROUTER_API_KEY"; then
  missing_selected+=("mu_mcp(OPENROUTER_API_KEY)")
fi

if ((${#missing_selected[@]} > 0)); then
  echo ""
  echo "warning: 你选择的 MCP 仍有缺失的 API key（可能导致启动握手失败）："
  for item in "${missing_selected[@]}"; do
    echo "  - ${item}"
  done
  echo "  建议：在 ${ZSHRC_PATH} 补全后，执行：source ${ZSHRC_PATH}（或新开终端）再重启 claude。"
fi

echo ""
echo "下一步："
echo "  1) 重启 claude"
echo "  2) 验证：claude mcp list"
echo ""
echo "Note: Claude Code Tools 可以单独运行 tools.sh 安装"
