#!/usr/bin/env bash
set -euo pipefail

API_KEY_MARKER_BEGIN="# >>> pi skill api keys >>>"
API_KEY_MARKER_END="# <<< pi skill api keys <<<"

usage() {
  cat <<'EOF'
Install this repository's Pi skills into the local Pi agent skill directory.

Usage:
  pi-agent/skills/install-skills.sh [--dry-run] [--target DIR]

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
  pi-agent/skills/install-skills.sh --target ~/.pi/agent/skills

Behavior:
  - Discovers subdirectories that contain SKILL.md (one or two levels deep).
  - Every invocation displays a numbered logical-skill menu and accepts sequence
    numbers separated by spaces and/or commas (for example, `1, 3 5`). Empty
    input cancels without installing skills.
  - The `grill` menu item expands to its discovered physical skills:
    domain-modeling, grilling, grill-me, and grill-with-docs. Pi continues to
    invoke their separate physical directories.
  - Menu entries include any API-key acquisition guidance declared in skill
    metadata.
  - Reads selected skills' metadata.api-key-env declarations.
  - Reuses nonempty environment variables and securely prompts for missing keys.
  - Persists interactively entered keys in a managed block for later Pi sessions.
  - Copies files into target/<skill-name>/, overwriting same-named files.
  - Does not delete extra files already present in the target directory.
  - Does not copy this installer script.
  - In --dry-run mode, prompts for the same menu choices but neither requests
    secret values nor writes files.
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$script_dir"
target_dir="${PI_AGENT_SKILLS_DIR:-$HOME/.pi/agent/skills}"
zshrc_path="${PI_AGENT_SKILLS_ZSHRC:-$HOME/.zshrc}"
dry_run=0

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
    -*)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      printf 'error: unexpected positional operand: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

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

read_skill_metadata() {
  local skill_file="$1"
  awk '
    NR == 1 && $0 == "---" { frontmatter = 1; next }
    frontmatter && $0 == "---" { exit }
    frontmatter && /^metadata:[[:space:]]*$/ { metadata = 1; next }
    frontmatter && metadata && /^[^[:space:]]/ { metadata = 0 }
    frontmatter && metadata && /^[[:space:]]+(api-key-env|api-key-url):[[:space:]]*/ {
      line = $0
      sub(/^[[:space:]]+/, "", line)
      field = line
      sub(/:.*/, "", field)
      sub(/^[^:]+:[[:space:]]*/, "", line)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
      if ((substr(line, 1, 1) == "\"" && substr(line, length(line), 1) == "\"") ||
          (substr(line, 1, 1) == "\047" && substr(line, length(line), 1) == "\047")) {
        line = substr(line, 2, length(line) - 2)
      }
      print field "\t" line
    }
  ' "$skill_file"
}

read_api_key_envs() {
  local skill_file="$1" field value
  while IFS=$'\t' read -r field value; do
    [[ "$field" == "api-key-env" ]] || continue
    printf '%s\n' "$value"
  done < <(read_skill_metadata "$skill_file")
}

read_skill_api_key_pairs() {
  local skill_file="$1" field value env url index existing_url
  local -a env_values url_values metadata_values
  declare -A url_by_env=()

  while IFS=$'\t' read -r field value; do
    metadata_values=()
    value="${value//,/ }"
    read -r -a metadata_values <<< "$value"
    case "$field" in
      api-key-env)
        if [[ ${#metadata_values[@]} -eq 0 ]]; then
          printf 'error: metadata.api-key-env in %s must not be empty\n' "$skill_file" >&2
          return 1
        fi
        env_values+=("${metadata_values[@]}")
        ;;
      api-key-url)
        if [[ ${#metadata_values[@]} -eq 0 ]]; then
          printf 'error: metadata.api-key-url in %s must not be empty\n' "$skill_file" >&2
          return 1
        fi
        url_values+=("${metadata_values[@]}")
        ;;
    esac
  done < <(read_skill_metadata "$skill_file")

  if [[ ${#url_values[@]} -gt 0 && ${#env_values[@]} -eq 0 ]]; then
    printf 'error: metadata.api-key-url in %s requires metadata.api-key-env\n' "$skill_file" >&2
    return 1
  fi
  if [[ ${#url_values[@]} -gt 0 && ${#url_values[@]} -ne ${#env_values[@]} ]]; then
    printf 'error: metadata.api-key-env and metadata.api-key-url in %s must declare matching counts\n' "$skill_file" >&2
    return 1
  fi

  for index in "${!env_values[@]}"; do
    env="${env_values[$index]}"
    if [[ ! "$env" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
      printf 'error: invalid metadata.api-key-env value in %s: %s\n' "$skill_file" "$env" >&2
      return 1
    fi
    [[ ${#url_values[@]} -gt 0 ]] || continue
    url="${url_values[$index]}"
    if [[ ! "$url" =~ ^https?://[^[:space:],]+$ ]]; then
      printf 'error: invalid metadata.api-key-url value in %s: %s\n' "$skill_file" "$url" >&2
      return 1
    fi
    existing_url="${url_by_env[$env]:-}"
    if [[ -n "$existing_url" && "$existing_url" != "$url" ]]; then
      printf 'error: conflicting metadata.api-key-url values for %s in %s\n' "$env" "$skill_file" >&2
      return 1
    fi
    url_by_env["$env"]="$url"
  done

  while IFS= read -r env; do
    [[ -n "$env" ]] || continue
    printf '%s\t%s\n' "$env" "${url_by_env[$env]}"
  done < <(printf '%s\n' "${!url_by_env[@]}" | LC_ALL=C sort)
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
all_skill_names=()

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
  all_skill_names+=("$name")
done

if [[ ${#all_skill_names[@]} -eq 0 ]]; then
  echo "no skills installed from $source_dir" >&2
  exit 1
fi

physical_skill_names=()
while IFS= read -r name; do
  physical_skill_names+=("$name")
done < <(printf '%s\n' "${all_skill_names[@]}" | LC_ALL=C sort)

logical_name_for_skill() {
  case "$1" in
    domain-modeling|grilling|grill-me|grill-with-docs)
      printf '%s\n' "grill"
      ;;
    *)
      printf '%s\n' "$1"
      ;;
  esac
}

declare -A logical_members=()
declare -A api_key_url_by_env=()
declare -A logical_unit_key_urls=()
declare -A logical_unit_key_envs=()
declare -A logical_unit_guidance=()
logical_unit_names_unsorted=()
for name in "${physical_skill_names[@]}"; do
  logical_name="$(logical_name_for_skill "$name")"
  if [[ -n "${logical_members[$logical_name]:-}" ]]; then
    logical_members["$logical_name"]+=",$name"
  else
    logical_members["$logical_name"]="$name"
    logical_unit_names_unsorted+=("$logical_name")
  fi

  if ! api_key_pairs="$(read_skill_api_key_pairs "${skill_location[$name]}/SKILL.md")"; then
    exit 1
  fi
  [[ -n "$api_key_pairs" ]] || continue
  while IFS=$'\t' read -r key url; do
    known_url="${api_key_url_by_env[$key]:-}"
    if [[ -n "$known_url" && "$known_url" != "$url" ]]; then
      printf 'error: conflicting metadata.api-key-url values for %s across discovered skills\n' "$key" >&2
      exit 1
    fi
    api_key_url_by_env["$key"]="$url"

    aggregate_key="$logical_name:$key"
    existing_url="${logical_unit_key_urls[$aggregate_key]:-}"
    if [[ -n "$existing_url" && "$existing_url" != "$url" ]]; then
      printf 'error: conflicting metadata.api-key-url values for %s in logical skill %s\n' "$key" "$logical_name" >&2
      exit 1
    fi
    logical_unit_key_urls["$aggregate_key"]="$url"
    case ",${logical_unit_key_envs[$logical_name]:-}," in
      *",$key,"*) ;;
      *)
        if [[ -n "${logical_unit_key_envs[$logical_name]:-}" ]]; then
          logical_unit_key_envs["$logical_name"]+=",$key"
        else
          logical_unit_key_envs["$logical_name"]="$key"
        fi
        ;;
    esac
  done <<< "$api_key_pairs"
done

logical_unit_names=()
while IFS= read -r logical_name; do
  logical_unit_names+=("$logical_name")
done < <(printf '%s\n' "${logical_unit_names_unsorted[@]}" | LC_ALL=C sort)

for logical_name in "${logical_unit_names[@]}"; do
  guidance=""
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    if [[ -n "$guidance" ]]; then
      guidance+="; "
    fi
    guidance+="$key: ${logical_unit_key_urls[$logical_name:$key]}"
  done < <(printf '%s\n' "${logical_unit_key_envs[$logical_name]:-}" | tr ',' '\n' | LC_ALL=C sort)
  logical_unit_guidance["$logical_name"]="$guidance"
done

echo "Available skills:"
for logical_index in "${!logical_unit_names[@]}"; do
  logical_name="${logical_unit_names[$logical_index]}"
  if [[ -n "${logical_unit_guidance[$logical_name]:-}" ]]; then
    printf '  %d. %s — %s\n' "$((logical_index + 1))" "$logical_name" "${logical_unit_guidance[$logical_name]}"
  else
    printf '  %d. %s\n' "$((logical_index + 1))" "$logical_name"
  fi
done

declare -A selected_seen=()
skill_dirs=()
skill_names=()

add_selected_physical_skill() {
  local physical_name="$1"
  [[ -n "${selected_seen[$physical_name]:-}" ]] && return
  selected_seen["$physical_name"]=1
  skill_dirs+=("${skill_location[$physical_name]}")
  skill_names+=("$physical_name")
}

add_selected_logical_unit() {
  local logical_name="$1" physical_name
  local -a physical_names
  IFS=, read -r -a physical_names <<< "${logical_members[$logical_name]}"
  for physical_name in "${physical_names[@]}"; do
    add_selected_physical_skill "$physical_name"
  done
}

choose_logical_units() {
  local selection_input normalized_input token logical_name choice_index valid_choice
  local -a selection_tokens chosen_logical_units

  while true; do
    printf 'Select skill numbers (spaces or commas; Enter cancels): '
    if ! IFS= read -r selection_input; then
      echo "error: unable to read skill selection" >&2
      exit 1
    fi
    if [[ -z "${selection_input//[[:space:]]/}" ]]; then
      report_synced_updates
      echo "No skills selected; installation cancelled."
      exit 0
    fi

    normalized_input="${selection_input//,/ }"
    read -r -a selection_tokens <<< "$normalized_input"
    if [[ ${#selection_tokens[@]} -eq 0 ]]; then
      echo "error: enter one or more sequence numbers" >&2
      continue
    fi

    valid_choice=1
    chosen_logical_units=()
    for token in "${selection_tokens[@]}"; do
      if [[ ! "$token" =~ ^[0-9]+$ ]]; then
        printf 'error: "%s" is not a sequence number; enter numbers from 1 to %d\n' \
          "$token" "${#logical_unit_names[@]}" >&2
        valid_choice=0
        continue
      fi
      choice_index=$((10#$token))
      if ((choice_index < 1 || choice_index > ${#logical_unit_names[@]})); then
        printf 'error: selection %s is out of range; enter numbers from 1 to %d\n' \
          "$token" "${#logical_unit_names[@]}" >&2
        valid_choice=0
        continue
      fi
      chosen_logical_units+=("${logical_unit_names[$((choice_index - 1))]}")
    done
    [[ $valid_choice -eq 1 ]] || continue

    for logical_name in "${chosen_logical_units[@]}"; do
      add_selected_logical_unit "$logical_name"
    done
    return
  done
}

choose_logical_units

if [[ ${#skill_dirs[@]} -eq 0 ]]; then
  echo "no skills installed from $source_dir" >&2
  exit 1
fi

declare -A required_by=()
declare -A skipped_skills=()
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

key_has_unskipped_dependents() {
  local key="$1" dependent
  local -a dependents
  IFS=, read -r -a dependents <<< "${required_by[$key]}"
  for dependent in "${dependents[@]}"; do
    [[ -z "${skipped_skills[$dependent]:-}" ]] && return 0
  done
  return 1
}

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
      key_has_unskipped_dependents "$key" || continue
      value=""
      if ! IFS= read -r -s -p "$key: " value; then
        printf '\nerror: unable to read required API key %s\n' "$key" >&2
        exit 1
      fi
      printf '\n' >&2
      if [[ -z "$value" ]]; then
        printf 'skipping selected skills because %s was declined:\n' "$key"
        while IFS= read -r name; do
          skipped_skills["$name"]="$key"
          printf '  - %s\n' "$name"
        done < <(printf '%s\n' "${required_by[$key]//,/$'\n'}" | LC_ALL=C sort)
        continue
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
    elif [[ ${#skipped_skills[@]} -gt 0 ]]; then
      printf 'API key prompts complete; skills needing declined keys will be skipped\n'
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
  if [[ -n "${skipped_skills[$name]:-}" ]]; then
    printf 'skipped %s (requires declined API key %s)\n' "$name" "${skipped_skills[$name]}"
    continue
  fi
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
  if [[ $dry_run -eq 0 && ${#skipped_skills[@]} -gt 0 ]]; then
    report_synced_updates
    echo "done. No selected skills were installed; every selected skill was skipped after its required API key was declined."
    exit 0
  fi
  echo "no skills installed from $source_dir" >&2
  exit 1
fi

report_synced_updates

if [[ $dry_run -eq 0 ]]; then
  echo "done. Start a new shell (or source $zshrc_path), then run /reload in Pi."
fi
