#!/usr/bin/env bash

INTERNAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_DIR="$(cd "${INTERNAL_DIR}/.." && pwd)"
RUNTIME_DIR="${HELPER_DIR}/.runtime"
UPSTREAM_DIR="${RUNTIME_DIR}/upstream"
VENV_DIR="${RUNTIME_DIR}/venv"
INSTALL_STATE_FILE="${RUNTIME_DIR}/install-state"
ENV_FILE="${HELPER_DIR}/.env"
UPSTREAM_REPO_URL="${CODOXEAR_REPO_URL:-https://github.com/yiwenlu66/codoxear}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: 缺少命令 $1" >&2
    return 1
  }
}

base_python() {
  if [[ -n "${CODOXEAR_PYTHON:-}" ]]; then
    command -v "${CODOXEAR_PYTHON}" 2>/dev/null || {
      echo "error: CODOXEAR_PYTHON 不可用: ${CODOXEAR_PYTHON}" >&2
      return 1
    }
    return 0
  fi
  command -v python3
}

ensure_origin() {
  local current
  current="$(git -C "${UPSTREAM_DIR}" remote get-url origin 2>/dev/null || true)"
  if [[ -z "${current}" ]]; then
    git -C "${UPSTREAM_DIR}" remote add origin "${UPSTREAM_REPO_URL}"
    return 0
  fi
  [[ "${current}" == "${UPSTREAM_REPO_URL}" ]] || {
    echo "error: upstream origin 不匹配" >&2
    echo "  current: ${current}" >&2
    echo "  expected: ${UPSTREAM_REPO_URL}" >&2
    return 1
  }
}

checkout_dirty() {
  [[ -n "$(git -C "${UPSTREAM_DIR}" status --porcelain --untracked-files=all)" ]]
}

sync_remote_main() {
  need_cmd git || return 1
  mkdir -p "${RUNTIME_DIR}"

  if [[ ! -e "${UPSTREAM_DIR}" ]]; then
    git clone --branch main --single-branch "${UPSTREAM_REPO_URL}" "${UPSTREAM_DIR}"
  fi
  [[ -d "${UPSTREAM_DIR}/.git" ]] || {
    echo "error: ${UPSTREAM_DIR} 存在但不是 git checkout" >&2
    return 1
  }
  ensure_origin || return 1
  if checkout_dirty; then
    echo "error: Codoxear runtime checkout 有本地修改，无法确认远程 main 最新版本" >&2
    git -C "${UPSTREAM_DIR}" status --short >&2
    return 1
  fi

  git -C "${UPSTREAM_DIR}" fetch --prune origin \
    '+refs/heads/main:refs/remotes/origin/main'

  local target current
  target="$(git -C "${UPSTREAM_DIR}" rev-parse --verify 'refs/remotes/origin/main^{commit}')"
  current="$(git -C "${UPSTREAM_DIR}" rev-parse --verify 'HEAD^{commit}')"
  if [[ "${current}" != "${target}" ]]; then
    git -C "${UPSTREAM_DIR}" checkout --detach "${target}"
  fi
  printf '%s\n' "${target}"
}

ensure_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    return 0
  fi
  local template="${UPSTREAM_DIR}/.env.example"
  [[ -f "${template}" ]] || {
    echo "error: upstream main 缺少 .env.example: ${template}" >&2
    return 1
  }
  cp "${template}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  echo "created ${ENV_FILE} from upstream main .env.example" >&2
  echo "note: 启动 Web server 前请修改其中的 CODEX_WEB_PASSWORD" >&2
}

validate_env() {
  local py
  py="$(base_python)" || return 1
  "${py}" "${INTERNAL_DIR}/launch.py" validate
}

ensure_venv() {
  if [[ -x "${VENV_DIR}/bin/python" ]]; then
    return 0
  fi

  local py
  py="$(base_python)" || return 1
  mkdir -p "${RUNTIME_DIR}"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "${py}" --seed "${VENV_DIR}"
  else
    "${py}" -m venv "${VENV_DIR}"
  fi
  [[ -x "${VENV_DIR}/bin/python" ]] || {
    echo "error: 无法创建 Codoxear venv: ${VENV_DIR}" >&2
    return 1
  }
}

installed_revision() {
  [[ -f "${INSTALL_STATE_FILE}" ]] || return 0
  sed -n 's/^revision=//p' "${INSTALL_STATE_FILE}" | tail -n 1
}

install_remote_main_if_needed() {
  local revision="$1"
  ensure_venv || return 1
  if [[ "$(installed_revision)" == "${revision}" ]] \
    && [[ -x "${VENV_DIR}/bin/codoxear-server" ]] \
    && [[ -x "${VENV_DIR}/bin/codoxear-broker" ]]; then
    echo "codoxear install: unchanged (${revision})"
    return 0
  fi

  need_cmd tar || return 1
  local source_snapshot state_tmp
  source_snapshot="$(mktemp -d "${RUNTIME_DIR}/install-source.XXXXXX")"
  state_tmp="${INSTALL_STATE_FILE}.tmp.$$"
  if ! git -C "${UPSTREAM_DIR}" archive "${revision}" | tar -x -C "${source_snapshot}"; then
    rm -rf "${source_snapshot}"
    return 1
  fi
  if ! "${VENV_DIR}/bin/python" -m pip install "${source_snapshot}"; then
    rm -rf "${source_snapshot}"
    return 1
  fi
  printf 'revision=%s\n' "${revision}" > "${state_tmp}"
  mv "${state_tmp}" "${INSTALL_STATE_FILE}"
  rm -rf "${source_snapshot}"
  echo "codoxear install: updated (${revision})"
}

python_launcher() {
  base_python
}
