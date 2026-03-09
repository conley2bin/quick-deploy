#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_DIR="${SCRIPT_DIR}"
UPSTREAM_DIR="${HELPER_DIR}/upstream/codoxear"
UPSTREAM_REPO_URL="${CODOXEAR_REPO_URL:-https://github.com/yiwenlu66/codoxear}"
ENV_FILE="${HELPER_DIR}/.env"
RUN_SCRIPT="${HELPER_DIR}/run_server.sh"

WRAPPER_MARKER_BEGIN="# >>> codoxear codex wrapper >>>"
WRAPPER_MARKER_END="# <<< codoxear codex wrapper <<<"

need_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "error: 缺少命令 ${cmd}"
    exit 1
  fi
}

normalize_home_path() {
  local raw="$1"
  local out="${raw//\$\{HOME\}/${HOME}}"
  out="${out//\$HOME/${HOME}}"
  case "${out}" in
    "~")
      out="${HOME}"
      ;;
    "~/"*)
      out="${HOME}/${out#\~/}"
      ;;
  esac
  printf '%s' "${out}"
}

read_env_value() {
  local key="$1"
  local file="$2"
  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "${file}" | tail -n 1 || true)"
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

default_branch() {
  local repo_dir="$1"
  local out
  out="$(git -C "${repo_dir}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [[ "${out}" == origin/* ]]; then
    echo "${out#origin/}"
    return
  fi
  echo "main"
}

sync_upstream() {
  if [[ -e "${UPSTREAM_DIR}" && ! -d "${UPSTREAM_DIR}/.git" ]]; then
    echo "error: 路径存在但不是 git 仓库: ${UPSTREAM_DIR}"
    exit 1
  fi

  if [[ ! -d "${UPSTREAM_DIR}" ]]; then
    mkdir -p "$(dirname "${UPSTREAM_DIR}")"
    git clone "${UPSTREAM_REPO_URL}" "${UPSTREAM_DIR}"
  fi

  git -C "${UPSTREAM_DIR}" fetch --prune origin

  local branch
  branch="$(default_branch "${UPSTREAM_DIR}")"

  local status
  status="$(git -C "${UPSTREAM_DIR}" status --porcelain)"
  if [[ -n "${status}" ]]; then
    echo "error: 上游仓库有未提交修改，停止自动更新: ${UPSTREAM_DIR}"
    exit 1
  fi

  git -C "${UPSTREAM_DIR}" checkout --detach "origin/${branch}"

  local rev
  rev="$(git -C "${UPSTREAM_DIR}" rev-parse --short HEAD)"
  echo "codoxear repo: branch=${branch}, commit=${rev}"
}

install_codoxear() {
  if python3 -m pip --version >/dev/null 2>&1; then
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
      python3 -m pip install -e "${UPSTREAM_DIR}"
      echo "installer: python3 -m pip -e (venv)"
    else
      python3 -m pip install --user -e "${UPSTREAM_DIR}"
      echo "installer: python3 -m pip --user -e"
    fi
    return
  fi

  echo "error: python3 -m pip 不可用"
  echo "note: 请先配置主仓库 uv 环境并激活，再重试"
  exit 1
}

create_env_template() {
  local password="${CODEX_WEB_PASSWORD:-}"
  local host="${CODEX_WEB_HOST:-::}"
  local port="${CODEX_WEB_PORT:-8743}"
  local codex_home_raw="${CODEX_HOME:-$HOME/.codex}"
  local codex_home
  codex_home="$(normalize_home_path "${codex_home_raw}")"
  local codex_bin="${CODEX_BIN:-codex}"

  cat >"${ENV_FILE}" <<EOF
# Required
CODEX_WEB_PASSWORD=${password}

# Optional
CODEX_WEB_HOST=${host}
CODEX_WEB_PORT=${port}
CODEX_HOME=${codex_home}
CODEX_BIN=${codex_bin}
EOF
}

ensure_env_file() {
  if [[ ! -f "${ENV_FILE}" ]]; then
    create_env_template
  fi
}

normalize_env_file() {
  python3 - "${ENV_FILE}" "${HOME}" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
home = sys.argv[2]

if not path.exists():
    raise SystemExit(0)

raw = path.read_text(encoding="utf-8")
lines = raw.splitlines()
out = []
changed = False
seen = False

def normalize(v: str) -> str:
    s = str(v).strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    elif s.startswith("'") and s.endswith("'") and len(s) >= 2:
        s = s[1:-1]
    s = s.replace("${HOME}", home).replace("$HOME", home)
    if s == "~":
        s = home
    elif s.startswith("~/"):
        s = home + "/" + s[2:]
    return s

pat = re.compile(r"^\s*CODEX_HOME\s*=(.*)$")
for line in lines:
    m = pat.match(line)
    if not m:
        out.append(line)
        continue
    seen = True
    v0 = m.group(1).strip()
    v1 = normalize(v0)
    if v1 != v0:
        changed = True
    out.append(f"CODEX_HOME={v1}")

if not seen:
    out.append(f"CODEX_HOME={home}/.codex")
    changed = True

if changed:
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}

validate_env() {
  local password
  local port

  password="$(read_env_value CODEX_WEB_PASSWORD "${ENV_FILE}")"
  if [[ -z "${password}" ]]; then
    echo "warning: ${ENV_FILE} 里 CODEX_WEB_PASSWORD 为空"
    echo "note: 请填写密码后重试 setup.sh"
    exit 1
  fi

  port="$(read_env_value CODEX_WEB_PORT "${ENV_FILE}")"
  if [[ -n "${port}" && ! "${port}" =~ ^[0-9]+$ ]]; then
    echo "error: CODEX_WEB_PORT 必须是整数: ${port}"
    exit 1
  fi
}

target_rc_file() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "${shell_name}" in
    zsh) echo "${HOME}/.zshrc" ;;
    bash) echo "${HOME}/.bashrc" ;;
    *) echo "${HOME}/.zshrc" ;;
  esac
}

install_wrapper() {
  local rc_file="$1"
  local upstream_dir="$2"
  python3 - "${rc_file}" "${WRAPPER_MARKER_BEGIN}" "${WRAPPER_MARKER_END}" "${upstream_dir}" "${HELPER_DIR}" <<'PY'
from pathlib import Path
from datetime import datetime
import re
import sys

rc_file = Path(sys.argv[1]).expanduser()
begin = sys.argv[2]
end = sys.argv[3]
upstream_dir = Path(sys.argv[4]).expanduser().resolve()
helper_dir = Path(sys.argv[5]).expanduser().resolve()
repo_root = helper_dir.parents[3]
venv_broker = (repo_root / ".venv" / "bin" / "codoxear-broker").resolve()

block = (
    f"{begin}\n"
    "codex() {\n"
    "  if [ -n \"${CODEX_HOME:-}\" ]; then\n"
    "    local _cxh=\"${CODEX_HOME//\\$\\{HOME\\}/${HOME}}\"\n"
    "    _cxh=\"${_cxh//\\$HOME/${HOME}}\"\n"
    "    case \"${_cxh}\" in\n"
    "      \"~\") _cxh=\"${HOME}\" ;;\n"
    "      \"~/\"*) _cxh=\"${HOME}/${_cxh#\\~/}\" ;;\n"
    "    esac\n"
    "    export CODEX_HOME=\"${_cxh}\"\n"
    "  fi\n"
    "  if command -v codoxear-broker >/dev/null 2>&1; then\n"
    "    codoxear-broker -- \"$@\"\n"
    "    return\n"
    "  fi\n"
    f"  if [ -x {str(venv_broker)!r} ]; then\n"
    f"    {str(venv_broker)!r} -- \"$@\"\n"
    "    return\n"
    "  fi\n"
    "  echo \"error: codoxear-broker 未找到。请先运行 ./coding-agent/shared/tools/codoxear/setup.sh\" >&2\n"
    "  return 127\n"
    "}\n"
    f"{end}\n"
)

if rc_file.exists():
    original = rc_file.read_text(encoding="utf-8")
else:
    original = ""

pat = re.compile(rf"(?ms)^{re.escape(begin)}\n.*?^{re.escape(end)}\n?")
m = pat.search(original)

if m:
    updated = original[: m.start()] + block + original[m.end() :]
else:
    updated = original
    if updated and not updated.endswith("\n"):
        updated += "\n"
    if updated and not updated.endswith("\n\n"):
        updated += "\n"
    updated += block

if updated == original:
    print(f"wrapper rc: unchanged ({rc_file})")
    raise SystemExit(0)

if original:
    backup = rc_file.with_name(rc_file.name + ".backup." + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.write_text(original, encoding="utf-8")

rc_file.parent.mkdir(parents=True, exist_ok=True)
rc_file.write_text(updated, encoding="utf-8")
print(f"wrapper rc: updated ({rc_file})")
PY
}

write_run_script() {
  cat >"${RUN_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

if [[ -z "${CODEX_HOME:-}" && -f "${SCRIPT_DIR}/.env" ]]; then
  _cxh_line="$(grep -E '^[[:space:]]*CODEX_HOME[[:space:]]*=' "${SCRIPT_DIR}/.env" | tail -n 1 || true)"
  if [[ -n "${_cxh_line}" ]]; then
    CODEX_HOME="${_cxh_line#*=}"
    CODEX_HOME="${CODEX_HOME#\"}"
    CODEX_HOME="${CODEX_HOME%\"}"
    CODEX_HOME="${CODEX_HOME#\'}"
    CODEX_HOME="${CODEX_HOME%\'}"
    export CODEX_HOME
  fi
fi

if [[ -n "${CODEX_HOME:-}" ]]; then
  CODEX_HOME="${CODEX_HOME//\$\{HOME\}/${HOME}}"
  CODEX_HOME="${CODEX_HOME//\$HOME/${HOME}}"
  case "${CODEX_HOME}" in
    "~") CODEX_HOME="${HOME}" ;;
    "~/"*) CODEX_HOME="${HOME}/${CODEX_HOME#\~/}" ;;
  esac
  export CODEX_HOME
fi

if command -v codoxear-server >/dev/null 2>&1; then
  exec codoxear-server
fi

if [[ -x "${REPO_ROOT}/.venv/bin/codoxear-server" ]]; then
  exec "${REPO_ROOT}/.venv/bin/codoxear-server"
fi

echo "error: codoxear-server 未找到。请先运行 ./coding-agent/shared/tools/codoxear/setup.sh"
exit 127
EOF
  chmod +x "${RUN_SCRIPT}"
}

main() {
  need_cmd git
  need_cmd python3

  sync_upstream
  install_codoxear
  ensure_env_file
  normalize_env_file
  validate_env

  local rc_file
  rc_file="$(target_rc_file)"
  install_wrapper "${rc_file}" "${UPSTREAM_DIR}"
  write_run_script

  local host
  local port
  host="$(read_env_value CODEX_WEB_HOST "${ENV_FILE}")"
  port="$(read_env_value CODEX_WEB_PORT "${ENV_FILE}")"
  if [[ -z "${host}" ]]; then
    host="::"
  fi
  if [[ -z "${port}" ]]; then
    port="8743"
  fi

  echo "configured helper dir: ${HELPER_DIR}"
  echo "upstream repo: ${UPSTREAM_DIR}"
  echo "server command: ${RUN_SCRIPT}"
  echo "shell reload: source ${rc_file}"
  echo "browser url: http://127.0.0.1:${port}"
}

main "$@"
