#!/usr/bin/env bash
set -euo pipefail

API_KEY_MARKER_BEGIN="# >>> pi skill api keys >>>"
API_KEY_MARKER_END="# <<< pi skill api keys <<<"

usage() {
  cat <<'EOF'
Install this repository's Pi skills into the local Pi agent skill directory.

Usage:
  pi-agent/skills/install-skills.sh [--dry-run] [--target DIR] [skill-name ...]

Defaults:
  source: directory containing this script (project pi-agent/skills)
  target: $PI_AGENT_SKILLS_DIR, or ~/.pi/agent/skills when unset
  API key config: $PI_AGENT_SKILLS_ZSHRC, or ~/.zshrc when unset

Layout:
  - Own skills live directly under pi-agent/skills/<name>/SKILL.md.
  - Vendored skills live one level deeper, grouped by upstream source:
    pi-agent/skills/<source>/<name>/SKILL.md (e.g. mattpocock/grilling).
  - Skill names must be unique across both levels; duplicates are an error.

Upstream sync:
  - Any UPSTREAM.txt at pi-agent/skills/UPSTREAM.txt or
    pi-agent/skills/<source>/UPSTREAM.txt registers vendored files.
  - Each non-comment line: <upstream raw URL> <path relative to the manifest>.
  - Before installing, every registered file is fetched and overwritten when
    upstream differs; fetch failures warn and keep the local copy.
  - Sync covers all registered files even when only some skills are selected.
  - Synced changes are left uncommitted; they are listed at the end for
    manual review and commit.

Examples:
  pi-agent/skills/install-skills.sh
  pi-agent/skills/install-skills.sh --dry-run
  pi-agent/skills/install-skills.sh --target ~/.pi/agent/skills firecrawl tavily

Behavior:
  - Installs subdirectories that contain SKILL.md (one or two levels deep).
  - Reads selected skills' metadata.api-key-env declarations.
  - Reuses nonempty environment variables and securely prompts for missing keys.
  - Persists interactively entered keys in a managed block for later Pi sessions.
  - Copies files into target/<skill-name>/, overwriting same-named files.
  - Does not delete extra files already present in the target directory.
  - Does not copy this installer script.
  - In --dry-run mode, reports sync and key status but neither prompts nor writes files.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$script_dir"
target_dir="${PI_AGENT_SKILLS_DIR:-$HOME/.pi/agent/skills}"
zshrc_path="${PI_AGENT_SKILLS_ZSHRC:-$HOME/.zshrc}"
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

# --- Upstream sync ---------------------------------------------------------

synced_updates=()
sync_failures=()

sync_manifest() {
  local manifest="$1"
  local manifest_dir line url path dest tmp
  manifest_dir="$(dirname "$manifest")"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    # skip blank lines and comments (leading whitespace allowed)
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "${line#"${line%%[![:space:]]*}"}" == \#* ]] && continue
    url="${line%%[[:space:]]*}"
    path="${line#"$url"}"
    path="${path#"${path%%[![:space:]]*}"}"   # ltrim
    path="${path%"${path##*[![:space:]]}"}"   # rtrim
    if [[ -z "$url" || -z "$path" ]]; then
      echo "warning: malformed line in $manifest: $line" >&2
      continue
    fi
    dest="$manifest_dir/$path"
    tmp="$(mktemp)"
    if curl -fsSL --retry 2 "$url" -o "$tmp"; then
      if [[ -f "$dest" ]] && cmp -s "$tmp" "$dest"; then
        : # already up to date
      else
        if [[ $dry_run -eq 1 ]]; then
          echo "would sync from upstream: $dest"
        else
          mkdir -p "$(dirname "$dest")"
          cp "$tmp" "$dest"
          echo "synced from upstream: $dest"
        fi
        synced_updates+=("$dest")
      fi
    else
      echo "warning: failed to fetch $url; keeping local copy" >&2
      sync_failures+=("$url")
    fi
    rm -f "$tmp"
  done < "$manifest"
}

sync_upstream() {
  local manifest
  for manifest in "$source_dir"/UPSTREAM.txt "$source_dir"/*/UPSTREAM.txt; do
    [[ -f "$manifest" ]] || continue
    sync_manifest "$manifest"
  done
}

report_synced_updates() {
  [[ $dry_run -eq 0 && ${#synced_updates[@]} -gt 0 ]] || return 0
  local top="" rel
  local rel_updates=()
  if command -v git >/dev/null 2>&1; then
    top="$(git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || true)"
  fi
  echo
  echo "Upstream updates were written to the working tree and left uncommitted:"
  local f
  for f in "${synced_updates[@]}"; do
    rel="$f"
    if [[ -n "$top" ]]; then
      rel="$(realpath --relative-to="$top" "$f" 2>/dev/null || printf '%s' "$f")"
    fi
    rel_updates+=("$rel")
    printf '  %s\n' "$rel"
  done
  echo "Review and commit manually, e.g.:"
  printf '  git add'
  printf ' %q' "${rel_updates[@]}"
  printf ' && git commit -m "Sync vendored skills from upstream"\n'
  if [[ ${#sync_failures[@]} -gt 0 ]]; then
    echo "note: ${#sync_failures[@]} upstream file(s) could not be fetched; local copies were kept"
  fi
}

# --- API key handling ------------------------------------------------------

read_api_key_envs() {
  local skill_file="$1"
  awk '
    NR == 1 && $0 == "---" { frontmatter = 1; next }
    frontmatter && $0 == "---" { exit }
    frontmatter && /^metadata:[[:space:]]*$/ { metadata = 1; next }
    frontmatter && metadata && /^[^[:space:]]/ { metadata = 0 }
    frontmatter && metadata && /^[[:space:]]+api-key-env:[[:space:]]*/ {
      line = $0
      sub(/^[[:space:]]+api-key-env:[[:space:]]*/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
      if ((substr(line, 1, 1) == "\"" && substr(line, length(line), 1) == "\"") ||
          (substr(line, 1, 1) == "\047" && substr(line, length(line), 1) == "\047")) {
        line = substr(line, 2, length(line) - 2)
      }
      gsub(/,/, " ", line)
      print line
    }
  ' "$skill_file"
}

shell_single_quote() {
  local value="$1"
  value=${value//\'/\'\\\'\'}
  printf "'%s'" "$value"
}

# Inspect only our managed block. Never source the user's shell configuration.
declare -A managed_exports=()
declare -A managed_configured=()
load_managed_api_keys() {
  [[ -f "$zshrc_path" ]] || return 0

  local state="outside" begin_count=0 end_count=0 line key rhs
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$API_KEY_MARKER_BEGIN" ]]; then
      [[ "$state" == "outside" ]] || {
        echo "error: malformed API key block in $zshrc_path (nested begin marker)" >&2
        return 1
      }
      state="inside"
      begin_count=$((begin_count + 1))
      continue
    fi
    if [[ "$line" == "$API_KEY_MARKER_END" ]]; then
      [[ "$state" == "inside" ]] || {
        echo "error: malformed API key block in $zshrc_path (end marker without begin marker)" >&2
        return 1
      }
      state="outside"
      end_count=$((end_count + 1))
      continue
    fi
    [[ "$state" == "inside" ]] || continue
    if [[ "$line" =~ ^export[[:space:]]+([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      rhs="${BASH_REMATCH[2]}"
      managed_exports["$key"]="$line"
      if [[ -n "$rhs" && "$rhs" != "''" && "$rhs" != '""' ]]; then
        managed_configured["$key"]=1
      fi
    fi
  done < "$zshrc_path"

  if [[ "$state" != "outside" || $begin_count -ne $end_count || $begin_count -gt 1 ]]; then
    echo "error: malformed API key block in $zshrc_path" >&2
    return 1
  fi
}

write_managed_api_keys() {
  local config_dir tmp key
  config_dir="$(dirname "$zshrc_path")"
  mkdir -p "$config_dir"
  tmp="$(mktemp "$config_dir/.pi-skill-keys.XXXXXX")"

  if [[ -f "$zshrc_path" ]]; then
    awk -v begin="$API_KEY_MARKER_BEGIN" -v end="$API_KEY_MARKER_END" '
      $0 == begin { managed = 1; next }
      $0 == end { managed = 0; next }
      !managed { outside[++count] = $0 }
      END {
        while (count > 0 && outside[count] == "") {
          count--
        }
        for (i = 1; i <= count; i++) {
          print outside[i]
        }
      }
    ' "$zshrc_path" > "$tmp"
    chmod --reference="$zshrc_path" "$tmp"
  else
    chmod 600 "$tmp"
  fi

  if [[ -s "$tmp" ]]; then
    printf '\n' >> "$tmp"
  fi
  printf '%s\n' "$API_KEY_MARKER_BEGIN" >> "$tmp"
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    printf '%s\n' "${managed_exports[$key]}" >> "$tmp"
  done < <(printf '%s\n' "${!managed_exports[@]}" | LC_ALL=C sort)
  printf '%s\n' "$API_KEY_MARKER_END" >> "$tmp"
  if [[ -L "$zshrc_path" ]]; then
    cat "$tmp" > "$zshrc_path"
    rm -f "$tmp"
  else
    mv "$tmp" "$zshrc_path"
  fi
}

# --- Skill discovery (one or two levels deep) ------------------------------

sync_upstream

declare -A skill_location=()
skill_dirs=()
skill_names=()
missing=()

for skill_file in "$source_dir"/*/SKILL.md "$source_dir"/*/*/SKILL.md; do
  [[ -f "$skill_file" ]] || continue
  skill_dir="$(dirname "$skill_file")"
  name="$(basename "$skill_dir")"
  if [[ -n "${skill_location[$name]:-}" ]]; then
    printf 'error: duplicate skill name "%s" found at:\n  %s\n  %s\nskill names must be unique; rename one of them\n' \
      "$name" "${skill_location[$name]}" "$skill_dir" >&2
    exit 1
  fi
  skill_location[$name]="$skill_dir"
  is_selected "$name" || continue
  skill_dirs+=("$skill_dir")
  skill_names+=("$name")
done

if [[ ${#selected[@]} -gt 0 ]]; then
  for name in "${selected[@]}"; do
    [[ -n "${skill_location[$name]:-}" ]] || missing+=("$name")
  done
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  printf 'error: requested skill(s) not found under %s (searched one and two levels deep): %s\n' "$source_dir" "${missing[*]}" >&2
  exit 1
fi

if [[ ${#skill_dirs[@]} -eq 0 ]]; then
  echo "no skills installed from $source_dir" >&2
  exit 1
fi

declare -A required_by=()
for i in "${!skill_dirs[@]}"; do
  skill_dir="${skill_dirs[$i]}"
  name="${skill_names[$i]}"
  while IFS= read -r declaration; do
    declaration="${declaration//,/ }"
    read -r -a keys <<< "$declaration"
    for key in "${keys[@]}"; do
      [[ -n "$key" ]] || continue
      if [[ ! "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
        printf 'error: invalid metadata.api-key-env value in %s: %s\n' "$skill_dir/SKILL.md" "$key" >&2
        exit 1
      fi
      if [[ -n "${required_by[$key]:-}" ]]; then
        case ",${required_by[$key]}," in
          *",$name,"*) ;;
          *) required_by["$key"]+=",$name" ;;
        esac
      else
        required_by["$key"]="$name"
      fi
    done
  done < <(read_api_key_envs "$skill_dir/SKILL.md")
done

if [[ ${#required_by[@]} -gt 0 ]]; then
  load_managed_api_keys
  config_changed=0
  echo "API key status for selected skills:"
  missing_keys=()
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    if [[ -n "${!key:-}" ]]; then
      current_value="${!key}"
      if [[ "$current_value" == *$'\n'* || "$current_value" == *$'\r'* ]]; then
        printf 'error: API key %s from the current environment must be a single line\n' "$key" >&2
        exit 1
      fi
      printf '  - %s: configured (current environment; used by: %s)\n' "$key" "${required_by[$key]}"
      unset current_value
    elif [[ -n "${managed_configured[$key]:-}" ]]; then
      printf '  - %s: configured (managed %s; used by: %s)\n' "$key" "$zshrc_path" "${required_by[$key]}"
    else
      printf '  - %s: missing (used by: %s)\n' "$key" "${required_by[$key]}"
      missing_keys+=("$key")
    fi
  done < <(printf '%s\n' "${!required_by[@]}" | LC_ALL=C sort)

  if [[ $dry_run -eq 1 ]]; then
    for key in "${missing_keys[@]}"; do
      printf 'would securely prompt for %s\n' "$key"
    done
  else
    for key in "${missing_keys[@]}"; do
      value=""
      if ! IFS= read -r -s -p "$key: " value; then
        printf '\nerror: unable to read required API key %s\n' "$key" >&2
        exit 1
      fi
      printf '\n' >&2
      if [[ -z "$value" ]]; then
        printf 'error: required API key %s cannot be empty\n' "$key" >&2
        exit 1
      fi
      if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
        printf 'error: required API key %s must be a single line\n' "$key" >&2
        exit 1
      fi
      managed_exports["$key"]="export $key=$(shell_single_quote "$value")"
      managed_configured["$key"]=1
      config_changed=1
      printf -v "$key" '%s' "$value"
      export "$key"
      unset value
    done

    if [[ $config_changed -eq 1 ]]; then
      write_managed_api_keys
      printf 'configured selected skills API keys in %s\n' "$zshrc_path"
    else
      printf 'selected skills API keys are already configured\n'
    fi
  fi
fi

installed=0
if [[ $dry_run -eq 0 ]]; then
  mkdir -p "$target_dir"
fi

for i in "${!skill_dirs[@]}"; do
  skill_dir="${skill_dirs[$i]}"
  name="${skill_names[$i]}"
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

if [[ $installed -eq 0 ]]; then
  echo "no skills installed from $source_dir" >&2
  exit 1
fi

report_synced_updates

if [[ $dry_run -eq 0 ]]; then
  echo "done. Start a new shell (or source $zshrc_path), then run /reload in Pi."
fi
