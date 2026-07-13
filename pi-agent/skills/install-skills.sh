#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install this repository's Pi skills into the local Pi agent skill directory.

Usage:
  pi-agent/skills/install-skills.sh [--dry-run] [--target DIR] [skill-name ...]

Defaults:
  source: directory containing this script (project pi-agent/skills)
  target: $PI_AGENT_SKILLS_DIR, or ~/.pi/agent/skills when unset

Examples:
  pi-agent/skills/install-skills.sh
  pi-agent/skills/install-skills.sh --dry-run
  pi-agent/skills/install-skills.sh --target ~/.pi/agent/skills firecrawl tavily

Behavior:
  - Installs only subdirectories that contain SKILL.md.
  - Copies files into target/<skill-name>/, overwriting same-named files.
  - Does not delete extra files already present in the target directory.
  - Does not copy this installer script.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$script_dir"
target_dir="${PI_AGENT_SKILLS_DIR:-$HOME/.pi/agent/skills}"
dry_run=0
selected=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run|-n)
      dry_run=1
      shift
      ;;
    --target)
      [[ $# -ge 2 ]] || { echo "error: --target requires a directory" >&2; exit 2; }
      target_dir="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      selected+=("$@")
      break
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      selected+=("$1")
      shift
      ;;
  esac
done

is_selected() {
  local name="$1"
  if [[ ${#selected[@]} -eq 0 ]]; then
    return 0
  fi
  local item
  for item in "${selected[@]}"; do
    [[ "$item" == "$name" ]] && return 0
  done
  return 1
}

installed=0
missing=()

if [[ $dry_run -eq 0 ]]; then
  mkdir -p "$target_dir"
fi

for skill_dir in "$source_dir"/*; do
  [[ -d "$skill_dir" ]] || continue
  [[ -f "$skill_dir/SKILL.md" ]] || continue
  name="$(basename "$skill_dir")"
  is_selected "$name" || continue

  dest="$target_dir/$name"
  if [[ $dry_run -eq 1 ]]; then
    echo "would install $name -> $dest"
  else
    mkdir -p "$dest"
    cp -a "$skill_dir/." "$dest/"
    echo "installed $name -> $dest"
  fi
  installed=$((installed + 1))
done

if [[ ${#selected[@]} -gt 0 ]]; then
  for name in "${selected[@]}"; do
    [[ -f "$source_dir/$name/SKILL.md" ]] || missing+=("$name")
  done
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  printf 'error: requested skill(s) not found under %s: %s\n' "$source_dir" "${missing[*]}" >&2
  exit 1
fi

if [[ $installed -eq 0 ]]; then
  echo "no skills installed from $source_dir" >&2
  exit 1
fi

if [[ $dry_run -eq 0 ]]; then
  echo "done. Run /reload in Pi to load updated skills."
fi
