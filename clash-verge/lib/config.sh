#!/bin/bash
# Shared routing helpers for tun-fix.sh: registry lookup, Script candidates,
# atomic writes, exact-target backups, and restore-last.

require_profiles() {
    [ -f "$PROFILES_YAML" ] || {
        echo "未找到 $PROFILES_YAML；请先启动 Clash Verge 生成配置" >&2
        return 1
    }
}

# `rules check` / `rules render` operate solely on repository sources, so they
# deliberately do not require a live Verge profile registry.
check_route_sources() {
    python3 "$RULES_READER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --check
}

render_route_script() {
    python3 "$RULES_READER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --render
}

# Registry lookups. Every read of profiles.yaml goes through the Python YAML
# reader; file-producing queries validate type, uniqueness, and basename safety
# before Bash joins paths.
registry_query() {
    local query="$1"
    shift
    python3 "$RULES_READER" --registry "$PROFILES_YAML" --registry-query "$query" "$@"
}

registered_profile_path() {
    local query="$1"
    local file
    file=$(registry_query "$query") || return 1
    printf '%s/profiles/%s\n' "$CLASH_DIR" "$file"
}

# Read-only presentation helpers. A missing or malformed registry is normal
# menu state: it must not block discovery or SSH, only routing writes.
get_script_config() {
    registered_profile_path script-target
}

get_profile_name() {
    registry_query current-name 2>/dev/null
}

get_current_profile_path() {
    local file
    file=$(registry_query current-file 2>/dev/null) || return 1
    if [ -n "$file" ]; then
        printf '%s/profiles/%s\n' "$CLASH_DIR" "$file"
    fi
}

policy_path_line() {
    local label="$1"
    local display_path="$2"
    shift 2
    if [ -f "$display_path" ]; then
        printf '  %s: %s\n' "$label" "$display_path"
        return 0
    fi
    if [ "$#" -gt 0 ]; then
        printf '  %s: %s（%s）\n' "$label" "$display_path" "$1"
        return 0
    fi
    printf '  %s: %s（文件不存在）\n' "$label" "$display_path"
}

# Canonical paths shown before every menu. Relative RULES_DIR/CLASH_DIR
# overrides were already resolved against the invocation directory.
show_route_paths() {
    local script_binding profile_name profile_path
    echo "规则来源:"
    policy_path_line "直连 direct.yaml" "$RULES_DIR/direct.yaml"
    policy_path_line "代理 proxy.yaml" "$RULES_DIR/proxy.yaml"
    echo "写入目标:"
    if [ ! -f "$PROFILES_YAML" ]; then
        echo "  全局 Script: 不可用（未找到 $PROFILES_YAML）"
    elif script_binding=$(get_script_config); then
        policy_path_line "全局 Script" "$script_binding" "已登记，文件尚不存在，首次更新会创建"
    else
        echo "  全局 Script: 不可用（profiles.yaml 未登记可用的 Script 目标）"
        printf '      原因: %s\n' "$(registry_query script-target 2>&1 | head -1)"
    fi
    echo "  订阅注册表: $PROFILES_YAML"
    if profile_name=$(get_profile_name) && profile_path=$(get_current_profile_path); then
        printf '  当前订阅: %s\n' "$profile_name"
        policy_path_line "订阅文件（只读）" "$profile_path" "文件不存在"
    else
        echo "  当前订阅: 不可读（不影响规则更新与应用识别）"
    fi
}

# Collision-free, timestamp-prefixed backup next to the target. The
# YYYYMMDD_HHMMSS prefix plus a unique suffix is the pattern restore-last and the
# documented backup layout both rely on; mktemp reserves the name exclusively so
# two runs in the same second cannot collide or overwrite.
unique_backup() {
    local file="$1"
    local directory base stamp backup
    directory=$(dirname -- "$file")
    base=$(basename -- "$file")
    stamp=$(date +%Y%m%d_%H%M%S)
    backup=$(mktemp "$directory/$base.backup.$stamp.XXXXXX") || return 1
    if ! cp -- "$file" "$backup"; then
        rm -f -- "$backup"
        return 1
    fi
    printf '%s\n' "$backup"
}

# Replace `target` with a fully rendered same-directory candidate. An optional
# mode argument sets the candidate's mode explicitly instead of inheriting the
# target's, so one rename carries both content and permissions.
#
# Callers must check the status: the menu invokes these actions from `||` lists,
# and bash disables errexit for the entire body of a function called that way.
commit_same_dir_temp() {
    local temp="$1"
    local target="$2"
    local mode="${3:-}"
    if [ -n "$mode" ]; then
        if ! chmod -- "$mode" "$temp"; then
            rm -f -- "$temp"
            return 1
        fi
    elif [ -e "$target" ] && ! chmod --reference="$target" "$temp"; then
        rm -f -- "$temp"
        return 1
    fi
    if ! mv -f -- "$temp" "$target"; then
        rm -f -- "$temp"
        return 1
    fi
}

# Route preparation is intentionally small shared state: every Script-specific
# failure settles here before anything is backed up or replaced.
PREPARED_ROUTE_SCRIPT=""
PREPARED_ROUTE_CANDIDATE=""

cleanup_prepared_route_rules() {
    if [ -n "$PREPARED_ROUTE_CANDIDATE" ]; then
        rm -f -- "$PREPARED_ROUTE_CANDIDATE"
    fi
    PREPARED_ROUTE_SCRIPT=""
    PREPARED_ROUTE_CANDIDATE=""
}

# One call owns the whole pre-write decision point: resolve the registered
# target, render one discovery-backed candidate, and confirm overwriting a
# foreign Script. Return 2 means the operator declined; any other non-zero means
# a real failure. Callers consume the candidate via write_prepared_route_script.
prepare_route_target() {
    local script_file target_dir candidate overwrite
    cleanup_prepared_route_rules
    require_profiles || return 1
    script_file=$(get_script_config) || return 1
    target_dir=$(dirname -- "$script_file")
    if [ ! -d "$target_dir" ]; then
        echo "已登记的全局 Script 目录不存在: $target_dir" >&2
        return 1
    fi
    candidate=$(mktemp "$target_dir/.${script_file##*/}.candidate.XXXXXX") || return 1
    if ! python3 "$RULES_READER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --registry "$PROFILES_YAML" \
        --prepared-script "$candidate" \
        --prepare; then
        rm -f -- "$candidate"
        return 1
    fi
    if [ -e "$script_file" ] && ! head -n 1 -- "$script_file" | grep -qF "// Generated by tun-fix.sh"; then
        echo "warning: $script_file 不是本工具生成的路由文件（可能含你的自定义规则）"
        echo -n "覆盖它？原文件会先备份到同目录。[y/N]: "
        read -r overwrite || true
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            rm -f -- "$candidate"
            echo "已取消；全局 Script 未修改。"
            return 2
        fi
    fi
    PREPARED_ROUTE_SCRIPT="$script_file"
    PREPARED_ROUTE_CANDIDATE="$candidate"
}

write_prepared_route_script() {
    local backup_file=""

    if [ -z "$PREPARED_ROUTE_SCRIPT" ] || [ -z "$PREPARED_ROUTE_CANDIDATE" ]; then
        echo "没有已准备的路由脚本候选；未写入。" >&2
        return 1
    fi
    if [ -f "$PREPARED_ROUTE_SCRIPT" ]; then
        backup_file=$(unique_backup "$PREPARED_ROUTE_SCRIPT") || {
            cleanup_prepared_route_rules
            return 1
        }
    fi
    # Candidate and target share a directory, so replacement is atomic.
    if ! commit_same_dir_temp "$PREPARED_ROUTE_CANDIDATE" "$PREPARED_ROUTE_SCRIPT"; then
        cleanup_prepared_route_rules
        return 1
    fi
    echo "路由规则已写入已登记的全局 Script: $PREPARED_ROUTE_SCRIPT"
    if [ -n "$backup_file" ]; then
        echo "脚本备份: $backup_file"
    fi
    PREPARED_ROUTE_SCRIPT=""
    PREPARED_ROUTE_CANDIDATE=""
}

# Menu option 1: validate sources, discover declared apps once, compare, and only
# then back up and atomically replace the registered Script. Identical output is
# a no-op that keeps the target mtime and creates no backup.
update_route_rules() {
    local prepared=0
    prepare_route_target || prepared=$?
    if [ "$prepared" -ne 0 ]; then
        return "$prepared"
    fi
    if cmp -s -- "$PREPARED_ROUTE_CANDIDATE" "$PREPARED_ROUTE_SCRIPT"; then
        cleanup_prepared_route_rules
        echo "生成结果与现有全局 Script 完全一致，无需更新（未改写文件，未新建备份）。"
        return 0
    fi
    write_prepared_route_script || {
        cleanup_prepared_route_rules
        return 1
    }
    cleanup_prepared_route_rules
    echo "本次只写了全局 Script 及其同目录备份；订阅、Merge、DNS、TUN、运行 YAML 和 ~/.ssh 未改动。"
    echo "请在 Verge 中重载/重新生成配置后，再核对运行规则和实际连接。"
}

# Automation entrypoint (`rules apply`). Exit 2 marks an operator cancel.
apply_route_rules() {
    prepare_route_target || return $?
    if cmp -s -- "$PREPARED_ROUTE_CANDIDATE" "$PREPARED_ROUTE_SCRIPT"; then
        cleanup_prepared_route_rules
        echo "生成结果与现有全局 Script 完全一致，无需更新。"
        return 0
    fi
    write_prepared_route_script || {
        cleanup_prepared_route_rules
        return 1
    }
    cleanup_prepared_route_rules
}

# Fixed-width nanosecond modification key. Whole-second stat is not enough to
# order two backups written in the same second, and a filename tie-break alone
# would rank `.10` below `.2`.
ns_mtime_key() {
    local file="$1"
    local seconds stamp fraction
    seconds=$(stat -c '%Y' -- "$file") || return 1
    stamp=$(stat -c '%y' -- "$file") || return 1
    fraction=${stamp#*.}
    fraction=${fraction%% *}
    [ -n "$fraction" ] || fraction=0
    printf '%s.%09d\n' "$seconds" "$(( 10#$fraction ))"
}

# Menu option 4: restore the registered Script from its newest exact-target
# backup. Only regular files with the tool's timestamped name shape qualify.
restore_last_script() {
    local script_file target_dir backup="" candidate newest_key="" key status
    local -a matches=()

    require_profiles || return 1
    script_file=$(get_script_config) || return 1
    target_dir=$(dirname -- "$script_file")

    shopt -s nullglob
    matches=("$target_dir/${script_file##*/}.backup."*)
    shopt -u nullglob

    local pattern_re='^[0-9]{8}_[0-9]{6}(\.[A-Za-z0-9]+)?$'
    for candidate in "${matches[@]}"; do
        # Symlinked names are not tool-written regular backups; never follow one
        # into an unrelated file just because its target content looks right.
        [ -f "$candidate" ] && [ ! -L "$candidate" ] || continue
        if ! [[ "${candidate##*.backup.}" =~ $pattern_re ]]; then
            continue
        fi
        key=$(ns_mtime_key "$candidate") || continue
        if [ -z "$newest_key" ] || [[ "$key" > "$newest_key" ]] \
            || { [ "$key" = "$newest_key" ] && [[ "$candidate" > "$backup" ]]; }; then
            newest_key="$key"
            backup="$candidate"
        fi
    done

    if [ -z "$backup" ]; then
        echo "没有找到已登记全局 Script 的可用备份（需要 <Script 文件名>.backup.YYYYMMDD_HHMMSS[.后缀] 形式的普通文件）。"
        echo "现有全局 Script 保持原样，未做任何恢复动作。"
        return 0
    fi

    echo "将恢复: $backup"
    echo "恢复到: $script_file"
    echo -n "确认恢复？[y/N]: "
    read -r status || true
    if [[ ! "$status" =~ ^[Yy]$ ]]; then
        echo "已取消；全局 Script 未修改。"
        return 0
    fi

    # Preserve whatever the target holds now before replacing it.
    local safety=""
    if [ -f "$script_file" ] || [ -L "$script_file" ]; then
        safety=$(unique_backup "$script_file") || return 1
    fi
    # Stage through an exclusively created same-directory name: a predictable
    # path (for example `.Script.js.restore.$$`) could be pre-planted as a
    # symlink and would be followed by cp before the rename.
    if ! candidate=$(mktemp "$target_dir/.${script_file##*/}.restore.XXXXXX"); then
        echo "无法在 $target_dir 建立恢复候选文件；未改动全局 Script。" >&2
        return 1
    fi
    if ! cp -- "$backup" "$candidate"; then
        rm -f -- "$candidate"
        return 1
    fi
    if ! commit_same_dir_temp "$candidate" "$script_file"; then
        return 1
    fi
    echo "已从备份恢复全局 Script: $backup"
    if [ -n "$safety" ]; then
        echo "恢复前的当前内容已另存: $safety"
    else
        echo "恢复前该目标不存在，因此没有新建当前内容备份。"
    fi
    echo "本次只改写了全局 Script；rules/direct.yaml 与 rules/proxy.yaml 未改动。"
    echo "之后再次执行“更新规则”会按当前 YAML 来源重新生成并覆盖此内容。"
    echo "请在 Verge 中重载/重新生成配置后，再核对运行规则和实际连接。"
    return 0
}

# Return code 2 means the operator declined. It is a clean, no-write outcome
# and must not turn the whole session into a failure.
report_action_status() {
    local label="$1"
    local code="$2"
    if [ "$code" -eq 0 ]; then
        return 0
    fi
    echo ""
    if [ "$code" -eq 2 ]; then
        echo "$label: 已按取消处理，未写入任何配置。"
    else
        echo "$label: 操作未成功（退出码 $code）；上面的诊断说明了原因，已回到菜单。"
    fi
}
