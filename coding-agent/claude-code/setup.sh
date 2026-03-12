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
# - 改完配置后，需要重启 claude 才会生效
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

CLAUDE_HOME="${CLAUDE_HOME:-${HOME}/.claude}"
CLAUDE_SETTINGS="${CLAUDE_SETTINGS:-${CLAUDE_HOME}/settings.local.json}"

PILOTY_DIR="${REPO_ROOT}/coding-agent/shared/MCP/PiloTY"

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
echo ""
echo "输入：1    或 all    或 n"
printf "> "
read -r selection_raw || true
selection_raw="$(echo "${selection_raw:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"

if [[ "${selection_raw}" == "n" || -z "${selection_raw}" ]]; then
  echo "未选择任何 MCP（跳过 MCP 配置）。"
  exit 0
fi

selected_csv=""
if [[ "${selection_raw}" == "all" ]]; then
  selected_csv="1"
else
  if [[ ! "${selection_raw}" =~ ^[0-9,]+$ ]]; then
    die "输入格式错误：${selection_raw}（应为 1 / all / n）"
  fi
  selected_csv="${selection_raw}"
fi

has_choice() {
  # usage: has_choice 1
  local n="$1"
  IFS=',' read -r -a arr <<<"${selected_csv}"
  for x in "${arr[@]}"; do
    [[ "${x}" == "${n}" ]] && return 0
  done
  return 1
}

IFS=',' read -r -a selected_arr <<<"${selected_csv}"
for x in "${selected_arr[@]}"; do
  [[ "${x}" == "1" ]] || die "输入包含无效选项：${x}（有效范围：1）"
done

# 依赖检查（只检查你选择的项）
if has_choice 1; then
  need_cmd uv
  need_cmd git
fi

if has_choice 1; then
  if [[ -d "${REPO_ROOT}/.git" ]]; then
    git -C "${REPO_ROOT}" submodule update --init --recursive >/dev/null
  fi
  [[ -f "${PILOTY_DIR}/pyproject.toml" ]] || die "找不到 ${PILOTY_DIR}/pyproject.toml（子模块未检出？）"
fi

# 使用 claude mcp add 命令注册 MCP 服务器
if has_choice 1; then
  echo "注册 piloty MCP 服务器..."
  # 如果已存在则先删除
  claude mcp remove piloty -s user 2>/dev/null || true
  (cd "${PILOTY_DIR}" && claude mcp add --scope user piloty -- uv run piloty) || die "piloty 注册失败"
fi

# 手动添加 working_directory 和环境变量配置到 ~/.claude.json
# 注意：Claude Code 不支持 working_directory 字段，需要使用 shell 包装器
python3 - "${PILOTY_DIR}" "${selected_csv}" <<'PY'
import json
import sys
from pathlib import Path

piloty_dir = sys.argv[1]
selected_csv = sys.argv[2]

want = {int(x) for x in selected_csv.split(",") if x}

claude_json = Path.home() / ".claude.json"
if not claude_json.exists():
    sys.exit(0)

config = json.loads(claude_json.read_text(encoding="utf-8"))
if "mcpServers" not in config:
    sys.exit(0)

modified = False

# 使用 shell 包装器指定工作目录，并添加启动超时配置
if 1 in want and "piloty" in config["mcpServers"]:
    config["mcpServers"]["piloty"]["command"] = "bash"
    config["mcpServers"]["piloty"]["args"] = ["-c", f"cd {piloty_dir} && uv run piloty"]
    config["mcpServers"]["piloty"]["startup_timeout_sec"] = 60
    modified = True
    print(f"已配置 piloty 工作目录: {piloty_dir}")

if modified:
    claude_json.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

echo ""
echo "已注册 MCP 服务器到 ~/.claude.json"

echo ""
echo "下一步："
echo "  1) 重启 claude"
echo "  2) 验证：claude mcp list"
echo ""
echo "Note: Claude Code Tools 可以单独运行 tools.sh 安装"
