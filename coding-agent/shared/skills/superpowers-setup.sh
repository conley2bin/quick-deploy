#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
AGENTS_HOME="${AGENTS_HOME:-${HOME}/.agents}"
SUPERPOWERS_DIR="${SUPERPOWERS_DIR:-${CODEX_HOME}/superpowers}"
SUPERPOWERS_REPO_URL="${SUPERPOWERS_REPO_URL:-https://github.com/obra/superpowers.git}"
SUPERPOWERS_BRANCH="main"
SKILLS_LINK="${SKILLS_LINK:-${AGENTS_HOME}/skills/superpowers}"
AGENTS_MD="${AGENTS_MD:-${CODEX_HOME}/AGENTS.md}"

die() {
  echo "error: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

usage() {
  cat <<'EOF'
usage: superpowers-setup.sh [--uninstall]
EOF
}

safe_rm_path() {
  local path="$1"
  [[ -n "${path}" ]] || die "empty path"
  [[ "${path}" != "/" ]] || die "refusing to remove /"
  [[ "${path}" != "." ]] || die "refusing to remove ."
  rm -rf -- "${path}"
}

ensure_repo() {
  if [[ -e "${SUPERPOWERS_DIR}" && ! -d "${SUPERPOWERS_DIR}/.git" ]]; then
    die "path exists but is not a git repo: ${SUPERPOWERS_DIR}"
  fi

  if [[ ! -d "${SUPERPOWERS_DIR}" ]]; then
    mkdir -p "$(dirname "${SUPERPOWERS_DIR}")"
    git clone --branch "${SUPERPOWERS_BRANCH}" --single-branch "${SUPERPOWERS_REPO_URL}" "${SUPERPOWERS_DIR}"
  fi

  local status
  status="$(git -C "${SUPERPOWERS_DIR}" status --porcelain)"
  if [[ -n "${status}" ]]; then
    die "repo has uncommitted changes: ${SUPERPOWERS_DIR}"
  fi

  git -C "${SUPERPOWERS_DIR}" fetch --prune origin "${SUPERPOWERS_BRANCH}"
  if git -C "${SUPERPOWERS_DIR}" show-ref --verify --quiet "refs/heads/${SUPERPOWERS_BRANCH}"; then
    git -C "${SUPERPOWERS_DIR}" checkout "${SUPERPOWERS_BRANCH}"
  else
    git -C "${SUPERPOWERS_DIR}" checkout -B "${SUPERPOWERS_BRANCH}" "origin/${SUPERPOWERS_BRANCH}"
  fi
  git -C "${SUPERPOWERS_DIR}" pull --ff-only origin "${SUPERPOWERS_BRANCH}"

  local rev
  rev="$(git -C "${SUPERPOWERS_DIR}" rev-parse --short HEAD)"
  echo "superpowers repo: branch=${SUPERPOWERS_BRANCH}, commit=${rev}"
}

ensure_symlink() {
  local target="${SUPERPOWERS_DIR}/skills"
  [[ -d "${target}" ]] || die "skills directory not found: ${target}"

  mkdir -p "$(dirname "${SKILLS_LINK}")"

  if [[ -L "${SKILLS_LINK}" ]]; then
    local current
    current="$(readlink "${SKILLS_LINK}")"
    if [[ "${current}" == "${target}" ]]; then
      echo "skills link: unchanged (${SKILLS_LINK})"
      return
    fi
    rm -f "${SKILLS_LINK}"
  elif [[ -e "${SKILLS_LINK}" ]]; then
    die "path exists and is not a symlink: ${SKILLS_LINK}"
  fi

  ln -s "${target}" "${SKILLS_LINK}"
  echo "skills link: ${SKILLS_LINK} -> ${target}"
}

warn_legacy_bootstrap() {
  if [[ ! -f "${AGENTS_MD}" ]]; then
    return
  fi
  if grep -q "superpowers-codex bootstrap" "${AGENTS_MD}"; then
    echo "warning: legacy bootstrap detected in ${AGENTS_MD}"
    echo "warning: remove the block referencing 'superpowers-codex bootstrap'"
  fi
}

uninstall() {
  if [[ -e "${SKILLS_LINK}" || -L "${SKILLS_LINK}" ]]; then
    safe_rm_path "${SKILLS_LINK}"
    echo "removed skills link: ${SKILLS_LINK}"
  else
    echo "skills link not found: ${SKILLS_LINK}"
  fi

  if [[ -e "${SUPERPOWERS_DIR}" || -L "${SUPERPOWERS_DIR}" ]]; then
    safe_rm_path "${SUPERPOWERS_DIR}"
    echo "removed repo path: ${SUPERPOWERS_DIR}"
  else
    echo "repo path not found: ${SUPERPOWERS_DIR}"
  fi
}

mode="install"
case "${1:-}" in
  "")
    ;;
  --uninstall)
    mode="uninstall"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage
    die "unknown argument: ${1}"
    ;;
esac

if [[ "${mode}" == "uninstall" ]]; then
  uninstall
  exit 0
fi

need_cmd git

ensure_repo
ensure_symlink
warn_legacy_bootstrap

echo "restart codex to load skills"
