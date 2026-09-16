#!/bin/bash
# Shared configuration and mutation helpers for tun-fix.sh.

require_profiles() {
    [ -f "$PROFILES_YAML" ] || {
        echo "未找到 $PROFILES_YAML，请先启动 Clash Verge 生成配置" >&2
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

check_route_migration() {
    python3 "$RULES_READER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --registry "$PROFILES_YAML" \
        --migration-check
}

# TUN 排除的本地网段 —— 唯一来源：tun_block 写入的和 verify_tun_routes 校验的
# 是同一份数组，两者不会各自漂移（否则验证会去查一组从未写入的网段）。
TUN_EXCLUDED_PREFIXES=(192.168.0.0/16 10.0.0.0/8 172.16.0.0/12 127.0.0.0/8)

# 规范 TUN 配置块 —— 全脚本唯一副本，输出到 stdout
#
# 键名说明：排除网段的键是 route-exclude-address（mihomo RawTun 字段，需要
# auto-route: true 才有意义）。此前使用的 exclude-routes 是 sing-box 的
# route_exclude_address，mihomo 的配置结构里根本没有这个字段，解析时被静默丢弃：
# 不报错、不告警，/configs 返回的 tun 对象里没有任何排除字段，内核 ip rule /
# table 2022 里也没有对应例外。因此"没有解析错误"不能当作键生效的证据。
tun_block() {
    cat << 'EOF'
tun:
  enable: true
  stack: system
  auto-route: true
  auto-detect-interface: true
  route-exclude-address:
EOF
    printf '    - %s\n' "${TUN_EXCLUDED_PREFIXES[@]}"
}

# 规范 sniffer 块 —— 全脚本唯一副本，输出到 stdout
#
# 为什么需要它：DOMAIN-SUFFIX,dex-gem.ai,DIRECT 只对「带域名」的连接生效。
# fake-ip 路径域名在（应用连的是 fake IP，mihomo 查表还原域名再匹配规则）。
# 但任何绕过 mihomo DNS 的解析都会让连接以真实 IP 裸进 TUN，域名上下文丢失：
#   - 应用自带 DoH / DoT 解析
#   - 系统 DNS 指向 10/8、192.168/16、172.16/12 段内的解析器（这些段被 TUN 排除，
#     DNS 查询不进 TUN、不被 hijack，拿到的是真实 IP）
#   - 应用硬编码 IP
# 这类连接不匹配任何域名规则，实测一路掉到 MATCH 走代理。
# 2026-08-19 conley-company 实测：openssl s_client 连 litellm.dex-gem.ai 的
# 真实 Cloudflare IP 104.21.42.134:443，连接表里 rule=Match、chains 指向机场节点。
# 开启 sniffer 后 mihomo 从 TLS ClientHello 的 SNI 恢复 litellm.dex-gem.ai，
# 直连规则重新命中 —— 直连不再依赖 DNS 恰好走了 fake-ip。
# 订阅与本 Merge 此前都没有 sniffer 段，由本块补齐（顶层键，mihomo 原生支持）。
sniffer_block() {
    cat << 'EOF'
sniffer:
  enable: true
  sniff:
    TLS:
      ports: [443, 8443]
    HTTP:
      ports: [80, 8080-8880]
    QUIC:
      ports: [443]
EOF
}

# All profiles.yaml reads go through the Python YAML reader. File-producing
# queries validate type, uniqueness, and basename safety before Bash joins paths.
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

get_merge_config() {
    registered_profile_path merge-target
}

get_script_config() {
    registered_profile_path script-target
}

get_profile_name() {
    registry_query current-name
}

get_current_profile_path() {
    local file
    file=$(registry_query current-file) || return 1
    if [ -n "$file" ]; then
        printf '%s/profiles/%s\n' "$CLASH_DIR" "$file"
    fi
}

get_current_profile_option_path() {
    local file
    file=$(registry_query current-option --option "$1") || return 1
    if [ -n "$file" ]; then
        printf '%s/profiles/%s\n' "$CLASH_DIR" "$file"
    fi
}

# 规范 fake-ip-filter 块 —— 全脚本唯一副本（2 空格键、4 空格条目）
# 三条写入路径（新建文件 / 插入现有 dns 块 / 追加整个 dns 块）都从这里取，
# 增删域名只改这一处。旧版三条路径各持一份拷贝，漂移后不同历史的机器
# 会拿到不同过滤列表。
fake_ip_filter_block() {
    cat << 'EOF'
  fake-ip-filter:
    # 本地网络
    - '*.local'
    - '*.lan'
    # 企业应用（主域名 + 通配符）
    #
    # 飞书 / Lark（feishu.cn、larkoffice.com）有意不在此列表，理由同下方 dex-gem.ai：
    # 进了 fake-ip-filter 就等于放弃域名上下文，DOMAIN-SUFFIX,feishu.cn,DIRECT 永远
    # 不可能命中。本订阅（Rbyu8mvt8Jk7.yaml）实测 enhanced-mode: fake-ip，且订阅与
    # 本文件都没有 sniffer 段、没有 dns.respect-rules —— 域名丢了就没有任何恢复途径。
    # 改动前的活跃连接可以直接看到这一点：
    #   host='' dst=223.111.26.66 -> rule: GeoIP cn -> DIRECT
    # host 为空，飞书直连完全靠 GEOIP,CN 接住真实 IP，是运气不是保证。
    # 移出后飞书拿到 198.18.0.0/16 的 fake IP，因规则判定 DIRECT，由 mihomo 自行解析
    # 真实 IP 并直连，fake IP 不会离开本机。局域网发现不受影响（*.local / *.lan 仍在）。
    - 'bytedance.com'
    - '*.bytedance.com'
    - 'dingtalk.com'
    - '*.dingtalk.com'
    # GitHub 主站保持原有解析；不再过滤 *.github.com。
    # ssh.github.com 需要 Fake-IP 映射保留域名，才能命中专用 DIRECT 规则。
    # 原始 SSH 没有 TLS SNI，不能依靠 TLS sniffer 恢复这个域名。
    - 'github.com'
    - 'githubusercontent.com'
    - '*.githubusercontent.com'
    - 'githubassets.com'
    - '*.githubassets.com'
    - 'github.io'
    - '*.github.io'
    # 中国镜像源（教育网）
    - '*.tsinghua.edu.cn'
    - '*.tuna.tsinghua.edu.cn'
    - '*.ustc.edu.cn'
    - '*.zju.edu.cn'
    - '*.bit.edu.cn'
    - '*.bjtu.edu.cn'
    - '*.hust.edu.cn'
    - '*.sjtu.edu.cn'
    - '*.lzu.edu.cn'
    - '*.neusoft.edu.cn'
    - '*.cqu.edu.cn'
    - '*.nju.edu.cn'
    - '*.hit.edu.cn'
    - '*.iscas.ac.cn'
    - '*.njupt.edu.cn'
    - '*.xjtu.edu.cn'
    # 中国镜像源（企业）
    - '*.aliyun.com'
    - '*.aliyuncs.com'
    - '*.huaweicloud.com'
    - '*.cloud.tencent.com'
    - '*.163.com'
    - '*.sohu.com'
    - '*.yun-idc.com'
    # 协议过滤
    - '+._tcp'
    - '+._udp'
    # 注意：dex-gem.ai 有意不在此列表。保留 fake-ip 才能保住域名上下文，
    # 让全局脚本的 DOMAIN-SUFFIX,dex-gem.ai,DIRECT 规则确定性命中；
    # 若在此过滤，应用拿到真实 IP 后连接没有域名信息，会绕过域名规则、
    # 一路掉到最后的 MATCH（通常是代理）。
    # 本订阅实测既无 sniffer 段也无 dns.respect-rules —— 这是 2026-08-19 之前
    # 的状态；此后本脚本会写入 sniffer 块，为「真实 IP 裸进 TUN」的连接从
    # TLS SNI 恢复域名（见 sniffer_block），域名丢失不再是不可恢复的。
    # 但这只是兑底：主路径仍是 fake-ip，本条目继续排除。
    #
    # 同样的陷阱目前仍存在于 bytedance.com：它同时在本列表和全局脚本的
    # DOMAIN-SUFFIX,bytedance.com,DIRECT 里，后者因此永远不会命中（实测活跃位次 22，
    # 但连接的 host 为空），bytedance 直连实际靠 GEOIP,CN 接住。sniffer 块写入后
    # 这类连接会从 SNI 恢复出 bytedance.com，域名规则重新有机会命中。本次不改动
    # 该条目的归属，但别把「规则列表里有这条」当作「这条在生效」。
EOF
}

# Create collision-free, timestamp-prefixed backups in the target directory.
# The prefix remains compatible with the existing backup menu.
unique_backup() {
    local file="$1"
    local timestamp backup
    timestamp=$(date +%Y%m%d_%H%M%S)
    backup=$(mktemp "$file.backup.${timestamp}.XXXXXX") || return 1
    if ! cp -- "$file" "$backup"; then
        rm -f -- "$backup"
        return 1
    fi
    printf '%s\n' "$backup"
}

commit_same_dir_temp() {
    local temp="$1"
    local target="$2"
    if [ -e "$target" ] && ! chmod --reference="$target" "$temp"; then
        rm -f -- "$temp"
        return 1
    fi
    if ! mv -f -- "$temp" "$target"; then
        rm -f -- "$temp"
        return 1
    fi
}

# Remove one top-level YAML block. With trim_tail=1 trailing blank lines are
# normalized so repeated TUN/sniffer replacement is byte-idempotent.
strip_top_level_block() {
    local file="$1"
    local key="$2"
    local output="$3"
    local trim_tail="${4:-0}"
    awk -v key="$key" -v trim_tail="$trim_tail" '
        function is_key(line, name) {return index(line, name ":") == 1}
        BEGIN {skip=0; pending=0}
        is_key($0, key) {skip=1; next}
        skip && /^[^[:space:]]/ {skip=0}
        skip {next}
        trim_tail == 1 && /^[[:space:]]*$/ {pending++; next}
        {
            while (pending-- > 0) print ""
            pending=0
            print
        }
    ' "$file" > "$output"
}

# Replace a top-level block and optionally place it before another top-level key.
replace_top_level_block() (
    set -e
    local file="$1"
    local key="$2"
    local producer="$3"
    local before="${4:-}"
    local stripped output block content
    stripped=""
    output=$(mktemp "${file}.tmp.XXXXXX")
    block=$(mktemp "${file}.block.XXXXXX")
    trap 'rm -f -- "$stripped" "$output" "$block"' EXIT

    # Producer calls stay straight-line under this subshell's errexit. A helper
    # that runs `false; echo ...` cannot turn its trailing echo into success.
    "$producer" > "$block"
    if [ ! -f "$file" ]; then
        commit_same_dir_temp "$block" "$file"
        block=""
        return
    fi

    stripped=$(mktemp "${file}.strip.XXXXXX")
    strip_top_level_block "$file" "$key" "$stripped" 1
    content=$(cat "$block")
    if [ -n "$before" ]; then
        awk -v before="$before" -v block="$content" '
            function is_key(line, name) {return index(line, name ":") == 1}
            !placed && is_key($0, before) {print block; print ""; placed=1}
            {print}
            END {if (!placed) {print ""; print block}}
        ' "$stripped" > "$output"
    else
        {
            cat "$stripped"
            echo ""
            cat "$block"
        } > "$output"
    fi
    commit_same_dir_temp "$output" "$file"
    output=""
)

# Update the nested dns.fake-ip-filter block while preserving every unrelated
# byte outside that bounded block. Broad YAML serialization would discard user
# comments, so this remains an indentation-aware text edit.
update_fake_ip_filter() (
    set -e
    local file="$1"
    local stripped output block filter_content
    stripped=""
    output=$(mktemp "${file}.tmp.XXXXXX")
    block=$(mktemp "${file}.block.XXXXXX")
    trap 'rm -f -- "$stripped" "$output" "$block"' EXIT
    fake_ip_filter_block > "$block"

    if [ ! -f "$file" ]; then
        {
            printf '# Clash Verge Merge 配置\n# 此文件会与订阅配置合并，提供全局增强\n\n'
            printf 'dns:\n'
            cat "$block"
            echo ""
            tun_block
        } > "$output"
        commit_same_dir_temp "$output" "$file"
        output=""
        echo "pass Merge 配置已创建: $file"
        return
    fi

    stripped=$(mktemp "${file}.strip.XXXXXX")
    awk '
        /^[[:space:]]*fake-ip-filter:[[:space:]]*$/ {
            match($0, /^[[:space:]]*/); ind=RLENGTH; skip=1; next
        }
        skip {
            if ($0 ~ /^[[:space:]]*$/) next
            match($0, /^[[:space:]]*/)
            if (RLENGTH <= ind) skip=0
            else next
        }
        {print}
    ' "$file" > "$stripped"

    filter_content=$(cat "$block")
    if grep -q '^dns:' "$stripped"; then
        awk -v filter="$filter_content" '/^dns:/ {print; print filter; next} 1' "$stripped" > "$output"
    else
        {
            cat "$stripped"
            echo ""
            printf 'dns:\n'
            cat "$block"
        } > "$output"
    fi
    commit_same_dir_temp "$output" "$file"
    output=""
    echo "pass Fake-IP Filter 已更新（保留 ssh.github.com 的域名上下文）"
)

update_tun_config() {
    local file="$1"
    local had_block=0
    [ -f "$file" ] && grep -q '^tun:' "$file" && had_block=1
    replace_top_level_block "$file" tun tun_block
    if [ "$had_block" -eq 1 ]; then
        echo "已替换原有 tun: 块为规范块 (route-exclude-address)"
    else
        echo "已写入 TUN 配置块 (route-exclude-address)"
    fi
    echo "这只说明文本已写入；重载后再用 verify_tun_routes 检查内核路由"
}

update_sniffer_config() {
    local file="$1"
    replace_top_level_block "$file" sniffer sniffer_block tun
    echo "已写入 sniffer 配置块（有 tun 时位于其前）"
    echo "这只说明文本已写入；重载后再用 verify_sniffer_live 做专用实测"
}

remove_prepend_rules() {
    local file="$1"
    local output
    [ -f "$file" ] || return 0
    grep -q '^prepend-rules:' "$file" || return 0
    output=$(mktemp "${file}.tmp.XXXXXX") || return 1
    if ! strip_top_level_block "$file" prepend-rules "$output" 0; then
        rm -f -- "$output"
        return 1
    fi
    commit_same_dir_temp "$output" "$file"
    echo "已移除 prepend-rules"
}

# Route preparation is intentionally small shared state: ordinary apply and the
# full optimizer both settle every Script-specific failure before either writes.
PREPARED_ROUTE_SCRIPT=""
PREPARED_ROUTE_CANDIDATE=""

cleanup_prepared_route_rules() {
    if [ -n "$PREPARED_ROUTE_CANDIDATE" ]; then
        rm -f -- "$PREPARED_ROUTE_CANDIDATE"
    fi
    PREPARED_ROUTE_SCRIPT=""
    PREPARED_ROUTE_CANDIDATE=""
}

prepare_route_rules() {
    local script_file target_dir candidate

    require_profiles || return 1
    check_route_migration || return 1
    script_file=$(get_script_config) || return 1
    target_dir=$(dirname "$script_file")
    if [ ! -d "$target_dir" ]; then
        echo "已登记的全局 Script 目录不存在: $target_dir" >&2
        return 1
    fi
    candidate=$(mktemp "$target_dir/.${script_file##*/}.candidate.XXXXXX") || return 1
    if ! render_route_script > "$candidate"; then
        rm -f -- "$candidate"
        return 1
    fi
    if [ -f "$script_file" ] && ! head -n 1 "$script_file" | grep -qF "// Generated by tun-fix.sh"; then
        echo "warning: $script_file 不是本脚本的路由生成文件（可能含你的自定义规则）"
        echo -n "覆盖它？原文件会带唯一备份。[y/N]: "
        local overwrite_js
        read -r overwrite_js
        if [[ ! "$overwrite_js" =~ ^[Yy]$ ]]; then
            rm -f -- "$candidate"
            echo "已取消；全局脚本未修改。"
            return 1
        fi
    fi
    PREPARED_ROUTE_SCRIPT="$script_file"
    PREPARED_ROUTE_CANDIDATE="$candidate"
}

write_prepared_route_rules() {
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
    echo "路由规则已写入已登记的全局脚本: $PREPARED_ROUTE_SCRIPT"
    if [ -n "$backup_file" ]; then
        echo "脚本备份: $backup_file"
    fi
    PREPARED_ROUTE_SCRIPT=""
    PREPARED_ROUTE_CANDIDATE=""
}

# Rules-only path: prepare and consume immediately. It never changes Merge/DNS/TUN/SSH.
apply_route_rules() {
    prepare_route_rules || return 1
    write_prepared_route_rules || { cleanup_prepared_route_rules; return 1; }
}

# Kept as the full optimizer's existing integration seam. It consumes the
# candidate prepared before optimize_all made its first unrelated mutation.
update_direct_rules() {
    write_prepared_route_rules || { cleanup_prepared_route_rules; return 1; }
}

# 删除本脚本之前生成的 GitHub SSH 块（新标记块 + 旧版本的无标记块）

preflight_optimizer_registry() {
    local requested="$1"
    local registered
    require_profiles || return 1
    registered=$(get_merge_config) || return 1
    if [ "$registered" != "$requested" ]; then
        echo "全局 Merge 写入目标与 profiles.yaml 登记不一致: $requested" >&2
        echo "已登记目标: $registered" >&2
        return 1
    fi
    # Resolve every remote Merge binding now, before the optimizer backs up or
    # mutates any file. The output is consumed again only after writes begin.
    registry_query remote-merge-targets >/dev/null || return 1
}

optimize_all() (
    # This trap is scoped to the optimizer subshell: any failure after preparing
    # a candidate removes it without changing outer shell traps or state.
    trap 'cleanup_prepared_route_rules' EXIT
    local file="$1"

    echo ""
    echo "=========================================="
    echo "  开始优化 Merge 配置"
    echo "=========================================="
    echo ""
    echo "配置文件: $file"
    echo "作用范围: 全局 (所有订阅)"
    echo ""

    # Resolve all write targets and prepare the Script candidate before the
    # first backup or mutation.
    preflight_optimizer_registry "$file" || return 1
    prepare_route_rules || return 1

    # 前置备份：先于一切修改。旧流程唯一的备份在第三步 update_tun_config 里，
    # 前两步（清空订阅级 merge、改写 fake-ip-filter）的改动无备份可回
    if [ -f "$file" ]; then
        local pre_backup
        pre_backup=$(unique_backup "$file")
        echo "已备份原 Merge 配置: $pre_backup"
        echo ""
    fi

    clear_subscription_merge
    remove_prepend_rules "$file"
    update_fake_ip_filter "$file"
    update_tun_config "$file"
    update_sniffer_config "$file"
    update_direct_rules "$file"

    # 文本手术的成功输出 ≠ 文件结构正确，写后校验未通过就以非零退出
    verify_merge_yaml "$file"

    echo ""
    echo "配置文件已更新；以下活跃诊断只描述当前运行核心。"
    echo "若 Verge 尚未重载，结果会反映旧状态，而不是证明本次写入已激活。"

    # 写入 Script.js ≠ 规则真的在顶部，去问内核拿到的那份规则表
    verify_route_rules

    # 规则在顶部 ≠ 裸 IP 连接也直连，造一条真实 IP 连接看 sniffer 是否救回域名
    verify_sniffer_live

    echo ""
    echo "重载后重新运行诊断，并用具体连接确认命中与出站。"
    echo ""

    verify_tun_routes
)

# 显示菜单

show_config_paths() {
    local current_profile_path
    local global_merge
    local global_script
    local sub_merge
    local sub_script
    local sub_rules
    local sub_proxies
    local sub_groups

    current_profile_path=$(get_current_profile_path)
    global_merge=$(get_merge_config)
    global_script=$(get_script_config)
    sub_merge=$(get_current_profile_option_path "merge")
    sub_script=$(get_current_profile_option_path "script")
    sub_rules=$(get_current_profile_option_path "rules")
    sub_proxies=$(get_current_profile_option_path "proxies")
    sub_groups=$(get_current_profile_option_path "groups")

    echo ""
    echo "=========================================="
    echo "配置文件路径"
    echo "=========================================="
    echo ""
    echo "直接修改:"
    echo "  全局 Merge: $global_merge"
    echo "  全局 Script: $global_script"
    echo "  订阅级 Merge: ${sub_merge:-(未绑定)}"
    echo ""
    echo "读取定位:"
    echo "  profiles.yaml: $PROFILES_YAML"
    echo "  当前订阅: ${current_profile_path:-(路径缺失)}"
    echo "  订阅级 Script: ${sub_script:-(未绑定)}"
    echo "  订阅级 Rules: ${sub_rules:-(未绑定)}"
    echo "  订阅级 Proxies: ${sub_proxies:-(未绑定)}"
    echo "  订阅级 Groups: ${sub_groups:-(未绑定)}"
    echo ""
    echo "说明:"
    echo "  - 全局 Merge: Fake-IP Filter 与 TUN 配置"
    echo "  - 全局 Script: 具体域名/端口规则强制置顶，宽泛兜底规则订阅已有则不重复添加"
    echo "  - 订阅级 Merge: 一键优化时会清空，避免覆盖全局 Merge"
    echo "  - 其余订阅绑定文件当前只读取，不直接改写"
    echo ""
    echo "=========================================="
    echo ""
}

# Clear subscription-level Merge files after the Python reader has resolved and
# validated every remote binding. Each file is backed up uniquely and replaced
# atomically in its own directory.
clear_subscription_merge() {
    local listed file path backup temp
    listed=$(registry_query remote-merge-targets) || return 1
    if [ -z "$listed" ]; then
        echo "未找到订阅级 merge 记录"
        return
    fi

    while IFS= read -r file; do
        [ -n "$file" ] || continue
        path="$CLASH_DIR/profiles/$file"
        if [ ! -f "$path" ]; then
            echo "文件不存在: $path"
            continue
        fi
        backup=$(unique_backup "$path") || return 1
        temp=$(mktemp "${path}.tmp.XXXXXX") || return 1
        printf "# 订阅级 Merge 为空\n" > "$temp"
        commit_same_dir_temp "$temp" "$path"
        echo "已清空: $path (备份: $backup)"
    done <<< "$listed"
}

# 备份管理
get_backup_files() {
    local dir="$CLASH_DIR/profiles"
    find "$dir" -maxdepth 1 -type f -name "*.backup.*" -printf "%f\n" \
        | awk -F'.backup.' 'NF>1{print $2 "|" $0}' \
        | LC_ALL=C sort \
        | awk -F'|' '{print $2}'
}

list_backups() {
    local dir="$CLASH_DIR/profiles"
    mapfile -t files < <(get_backup_files)

    if [ ${#files[@]} -eq 0 ]; then
        echo "未找到备份文件"
        return
    fi

    printf "%-4s %-28s %-19s %s\n" "序号" "原文件" "备份时间" "大小(bytes)"
    local i=1
    local f
    for f in "${files[@]}"; do
        local orig="${f%%.backup.*}"
        local ts="${f##*.backup.}"
        local ts_date="${ts%%_*}"
        local ts_time="${ts##*_}"
        local ts_fmt="${ts_date:0:4}-${ts_date:4:2}-${ts_date:6:2} ${ts_time:0:2}:${ts_time:2:2}:${ts_time:4:2}"
        local size
        size=$(stat -c %s "$dir/$f" 2>/dev/null || echo "0")
        printf "%-4s %-28s %-19s %s\n" "$i" "$orig" "$ts_fmt" "$size"
        i=$((i+1))
    done
}

restore_backup() {
    local dir="$CLASH_DIR/profiles"
    mapfile -t files < <(get_backup_files)

    if [ ${#files[@]} -eq 0 ]; then
        echo "未找到备份文件"
        return
    fi

    list_backups
    echo -n "选择序号以恢复: "
    read -r idx

    if ! [[ "$idx" =~ ^[0-9]+$ ]] || [ "$idx" -lt 1 ] || [ "$idx" -gt ${#files[@]} ]; then
        echo "无效选择"
        return
    fi

    local file="${files[$((idx-1))]}"
    local orig="${file%%.backup.*}"
    local temp
    temp=$(mktemp "$dir/.${orig}.restore.XXXXXX") || return 1
    if ! cp -- "$dir/$file" "$temp"; then
        rm -f -- "$temp"
        return 1
    fi
    commit_same_dir_temp "$temp" "$dir/$orig"
    echo "已恢复: $orig"
}

cleanup_backups() {
    local dir="$CLASH_DIR/profiles"
    mapfile -t files < <(get_backup_files)

    if [ ${#files[@]} -eq 0 ]; then
        echo "没有符合条件的备份"
        return
    fi

    echo -n "输入序号(空格分隔)或 all: "
    read -r selection

    if [ -z "$selection" ]; then
        echo "无效选择"
        return
    fi

    local to_delete=()
    if [ "$selection" = "all" ]; then
        to_delete=("${files[@]}")
    else
        local idx
        for idx in $selection; do
            if ! [[ "$idx" =~ ^[0-9]+$ ]] || [ "$idx" -lt 1 ] || [ "$idx" -gt ${#files[@]} ]; then
                echo "无效序号: $idx"
                return
            fi
            to_delete+=("${files[$((idx-1))]}")
        done
    fi

    echo "以下备份将被删除:"
    local f
    for f in "${to_delete[@]}"; do
        echo "  $f"
    done
    echo -n "确认删除? [y/N]: "
    read -r confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "已取消"
        return
    fi

    local count=0
    for f in "${to_delete[@]}"; do
        rm -f "$dir/$f"
        count=$((count+1))
    done
    echo "已删除 $count 个备份"
}

backup_menu() {
    while true; do
        echo ""
        echo "=========================================="
        echo "  备份管理"
        echo "=========================================="
        echo ""
        list_backups
        echo ""
        echo "  1. 恢复备份"
        echo "  2. 清理备份"
        echo "  0. 返回"
        echo ""
        echo "=========================================="
        echo -n "请选择 [0-2]: "
        read -r choice

        case $choice in
            1)
                restore_backup
                ;;
            2)
                cleanup_backups
                ;;
            0)
                return
                ;;
            *)
                echo "无效选择"
                ;;
        esac
    done
}
