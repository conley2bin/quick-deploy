#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.internal/common.sh"

WRAPPER_MARKER_BEGIN="# >>> codoxear codex wrapper >>>"
WRAPPER_MARKER_END="# <<< codoxear codex wrapper <<<"

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
  python3 - "${rc_file}" "${WRAPPER_MARKER_BEGIN}" "${WRAPPER_MARKER_END}" "${HELPER_DIR}" <<'PY'
from pathlib import Path
from datetime import datetime
import re
import sys

rc_file = Path(sys.argv[1]).expanduser()
begin = sys.argv[2]
end = sys.argv[3]
helper_dir = Path(sys.argv[4]).expanduser().resolve()
sys.path.insert(0, str(helper_dir / ".internal"))

from render_wrapper import render_wrapper_block

repo_root = helper_dir.parents[3]
venv_broker = (repo_root / ".venv" / "bin" / "codoxear-broker").resolve()

block = render_wrapper_block(
    begin=begin,
    end=end,
    venv_broker=str(venv_broker),
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
  install_wrapper "${rc_file}"

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
