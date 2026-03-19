#!/usr/bin/env bash

COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_DIR="$(cd "${COMMON_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${HELPER_DIR}/../../../.." && pwd)"
UPSTREAM_DIR="${HELPER_DIR}/codoxear"
LEGACY_UPSTREAM_DIR="${HELPER_DIR}/upstream/codoxear"
PATCH_DIR="${HELPER_DIR}/patches"
UPSTREAM_REPO_URL="${CODOXEAR_REPO_URL:-https://github.com/yiwenlu66/codoxear}"
ENV_FILE="${HELPER_DIR}/.env"
RUN_SCRIPT="${HELPER_DIR}/run_server.sh"
INSTALL_STATE_FILE="${COMMON_DIR}/install-state"

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

export_env_if_missing() {
  local key="$1"
  local value="${!key:-}"

  if [[ -n "${value}" ]]; then
    export "${key}=${value}"
    return
  fi

  if [[ ! -f "${ENV_FILE}" ]]; then
    return
  fi

  value="$(read_env_value "${key}" "${ENV_FILE}")"
  if [[ -n "${value}" ]]; then
    export "${key}=${value}"
  fi
}

default_branch() {
  local repo_dir="$1"
  local out
  out="$(git -C "${repo_dir}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [[ "${out}" == origin/* ]]; then
    echo "${out#origin/}"
    return
  fi
  if git -C "${repo_dir}" show-ref --verify --quiet refs/remotes/origin/main; then
    echo "main"
    return
  fi
  if git -C "${repo_dir}" show-ref --verify --quiet refs/remotes/origin/master; then
    echo "master"
    return
  fi
  out="$(
    git -C "${repo_dir}" for-each-ref --format='%(refname:short)' refs/remotes/origin |
      sed -n 's#^origin/##p' |
      grep -v '^HEAD$' |
      head -n 1
  )"
  if [[ -n "${out}" ]]; then
    echo "${out}"
    return
  fi
  echo "main"
}

current_repo_rev() {
  git -C "${UPSTREAM_DIR}" rev-parse HEAD
}

codoxear_python_cmd() {
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    printf '%s\n' "${REPO_ROOT}/.venv/bin/python"
    return
  fi
  printf '%s\n' "python3"
}

local_overlay_rev() {
  python3 - "${PATCH_DIR}" <<'PY'
from pathlib import Path
import hashlib
import sys

patch_dir = Path(sys.argv[1])
h = hashlib.sha256()

if patch_dir.exists():
    for path in sorted(patch_dir.glob("*.patch")):
        if not path.is_file():
            continue
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")

print(h.hexdigest())
PY
}

apply_local_patches() {
  if [[ ! -d "${PATCH_DIR}" ]]; then
    return
  fi

  local patch
  while IFS= read -r patch; do
    git -C "${UPSTREAM_DIR}" apply --whitespace=nowarn "${patch}"
  done < <(find "${PATCH_DIR}" -maxdepth 1 -type f -name '*.patch' | sort)
}

ensure_origin_remote() {
  if git -C "${UPSTREAM_DIR}" remote get-url origin >/dev/null 2>&1; then
    git -C "${UPSTREAM_DIR}" remote set-url origin "${UPSTREAM_REPO_URL}"
    return
  fi
  git -C "${UPSTREAM_DIR}" remote add origin "${UPSTREAM_REPO_URL}"
}

maybe_migrate_legacy_checkout() {
  if [[ -e "${UPSTREAM_DIR}" ]]; then
    return
  fi
  if [[ ! -e "${LEGACY_UPSTREAM_DIR}" ]]; then
    return
  fi
  if [[ ! -d "${LEGACY_UPSTREAM_DIR}/.git" ]]; then
    echo "error: 旧路径存在但不是 git 仓库: ${LEGACY_UPSTREAM_DIR}"
    exit 1
  fi
  mv "${LEGACY_UPSTREAM_DIR}" "${UPSTREAM_DIR}"
  rmdir "$(dirname "${LEGACY_UPSTREAM_DIR}")" 2>/dev/null || true
}

sync_upstream() {
  maybe_migrate_legacy_checkout

  if [[ -e "${UPSTREAM_DIR}" && ! -d "${UPSTREAM_DIR}/.git" ]]; then
    echo "error: 路径存在但不是 git 仓库: ${UPSTREAM_DIR}"
    exit 1
  fi

  if [[ ! -d "${UPSTREAM_DIR}" ]]; then
    mkdir -p "$(dirname "${UPSTREAM_DIR}")"
    git clone "${UPSTREAM_REPO_URL}" "${UPSTREAM_DIR}"
  fi

  ensure_origin_remote
  git -C "${UPSTREAM_DIR}" reset --hard HEAD >/dev/null
  git -C "${UPSTREAM_DIR}" clean -fd >/dev/null
  git -C "${UPSTREAM_DIR}" fetch --prune origin

  local branch
  branch="$(default_branch "${UPSTREAM_DIR}")"

  git -C "${UPSTREAM_DIR}" checkout --detach "origin/${branch}"
  git -C "${UPSTREAM_DIR}" reset --hard "origin/${branch}"
  git -C "${UPSTREAM_DIR}" clean -fd
  apply_local_patches

  local rev
  rev="$(current_repo_rev)"
  echo "codoxear repo: branch=${branch}, commit=${rev}"
}

write_install_state() {
  local rev
  rev="$(current_repo_rev)"
  mkdir -p "$(dirname "${INSTALL_STATE_FILE}")"
  cat > "${INSTALL_STATE_FILE}" <<EOF
REPO_DIR=${UPSTREAM_DIR}
REPO_REV=${rev}
OVERLAY_REV=$(local_overlay_rev)
EOF
}

has_codoxear_server() {
  if command -v codoxear-server >/dev/null 2>&1; then
    return 0
  fi
  [[ -x "${REPO_ROOT}/.venv/bin/codoxear-server" ]]
}

install_state_matches() {
  if [[ ! -f "${INSTALL_STATE_FILE}" ]]; then
    return 1
  fi

  local repo_dir
  local repo_rev
  local overlay_rev
  repo_dir="$(read_env_value REPO_DIR "${INSTALL_STATE_FILE}")"
  repo_rev="$(read_env_value REPO_REV "${INSTALL_STATE_FILE}")"
  overlay_rev="$(read_env_value OVERLAY_REV "${INSTALL_STATE_FILE}")"

  [[ "${repo_dir}" == "${UPSTREAM_DIR}" ]] || return 1
  [[ "${repo_rev}" == "$(current_repo_rev)" ]] || return 1
  [[ "${overlay_rev}" == "$(local_overlay_rev)" ]]
}

install_codoxear() {
  local py
  py="$(codoxear_python_cmd)"

  if "${py}" -m pip --version >/dev/null 2>&1; then
    if [[ "${py}" == "${REPO_ROOT}/.venv/bin/python" ]]; then
      "${py}" -m pip install -e "${UPSTREAM_DIR}"
      echo "installer: ${py} -m pip -e (.venv)"
    elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
      "${py}" -m pip install -e "${UPSTREAM_DIR}"
      echo "installer: ${py} -m pip -e (venv)"
    else
      "${py}" -m pip install --user -e "${UPSTREAM_DIR}"
      echo "installer: ${py} -m pip --user -e"
    fi
    write_install_state
    return
  fi

  echo "error: ${py} -m pip 不可用"
  echo "note: 请先配置主仓库 uv 环境并激活，再重试"
  exit 1
}

codoxear_imports_cleanly() {
  local py
  py="$(codoxear_python_cmd)"
  "${py}" - <<'PY' >/dev/null 2>&1
import codoxear.server
PY
}

ensure_codoxear_install() {
  if install_state_matches && has_codoxear_server && codoxear_imports_cleanly; then
    return
  fi
  install_codoxear
  if ! codoxear_imports_cleanly; then
    echo "error: codoxear Python import failed after install"
    exit 1
  fi
}

load_runtime_env_from_env() {
  export_env_if_missing CODEX_WEB_PASSWORD
  export_env_if_missing CODEX_WEB_HOST
  export_env_if_missing CODEX_WEB_PORT
  export_env_if_missing CODEX_HOME
  export_env_if_missing CODEX_BIN

  if [[ -n "${CODEX_HOME:-}" ]]; then
    CODEX_HOME="$(normalize_home_path "${CODEX_HOME}")"
    export CODEX_HOME
  fi
}

launch_codoxear_server() {
  if command -v codoxear-server >/dev/null 2>&1; then
    exec codoxear-server
  fi

  if [[ -x "${REPO_ROOT}/.venv/bin/codoxear-server" ]]; then
    exec "${REPO_ROOT}/.venv/bin/codoxear-server"
  fi

  echo "error: codoxear-server 未找到。请先运行 ./coding-agent/shared/tools/codoxear/setup.sh"
  exit 127
}
