#!/bin/bash
# Runtime and generated-config diagnostics for tun-fix.sh.

mihomo_api() {
    local endpoint="$1"
    command -v curl >/dev/null 2>&1 || {
        echo "未找到 curl，无法查询 Mihomo 控制器" >&2
        return 1
    }
    curl -fsS --max-time 5 --unix-socket "$MIHOMO_SOCKET" "http://localhost$endpoint"
}

# Generic policy verification. Expectations come from both editable sources:
# exact pre prefix order/targets, then every post rule somewhere before MATCH.
verify_route_rules() {
    echo ""
    echo "=========================================="
    echo "  本地路由来源与活跃 /rules 对照"
    echo "=========================================="
    echo ""

    local rules_json
    if ! rules_json=$(mihomo_api /rules 2>/dev/null); then
        echo "fail Clash Verge 未运行、未重载，或 /rules 无法读取。"
        echo "     查不到活跃数据不是通过；重载后重跑。"
        return 1
    fi
    if [ -z "$rules_json" ]; then
        echo "fail /rules 返回空响应；无法证明本地规则已加载。"
        return 1
    fi

    if ! printf '%s' "$rules_json" | python3 "$RULES_READER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --verify-runtime; then
        echo "fail 活跃规则与来源定义的 pre/post 语义不一致。"
        return 1
    fi
    echo "pass 活跃规则满足来源定义的 pre/post 元数据语义。"
    echo "     这是 /rules 静态位次检查，不证明任何真实连接已命中。"
    echo ""
}

# 验证排除网段是否真的生效
#
# 旧实现是错的：它去 ip rule / table 2022 里 grep 那四个前缀，但 mihomo 不是
# “先全部纳入、再加四条例外”，而是把「全部减去这四段」算成 36 条互不重叠的
# CIDR 写进 table 2022。排除段是以「不存在」的形式体现的，grep 一个洞永远 grep 不到，
# 所以旧版在配置完全正常的机器上也会报 0/4 fail。
#
# 实测对照（同一台机器，只改 tun 键名后重载）：
#   exclude-routes（无效）      table 2022: default via 198.18.0.2 dev Meta
#   route-exclude-address（有效） table 2022: 0.0.0.0/5, 8.0.0.0/7, 11.0.0.0/8, ...
#                                  —— 36 条，恰好跳过 10/8、127/8、172.16/12、192.168/16
#
# 正确的判据：拿每段里的一个代表地址问内核“这包从哪个网卡出去”，
# 不是 TUN 网卡就说明该段确实没被吸进去。
verify_tun_routes() {
    echo ""
    echo "=========================================="
    echo "  TUN 排除网段内核态检查"
    echo "=========================================="
    echo ""

    if ! command -v ip >/dev/null 2>&1; then
        echo "skip 未找到 ip 命令，无法进行 TUN 内核态检查（不是通过）"
        return
    fi

    local routes tun_dev route_count has_default failed=0
    routes=$(ip route show table 2022 2>/dev/null || true)

    if [ -z "$routes" ]; then
        echo "skip table 2022 为空：TUN 未启用或 auto-route 尚未安装路由，无法判断。"
        return
    fi

    tun_dev=$(printf '%s\n' "$routes" | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
    route_count=$(printf '%s\n' "$routes" | grep -c .)
    has_default=$(printf '%s\n' "$routes" | grep -c '^default ' || true)

    echo "TUN 网卡: ${tun_dev:-未知}    table 2022 路由数: $route_count"
    if [ "$has_default" -gt 0 ]; then
        echo "fail table 2022 是 default 路由：排除没有形成路由空洞。"
        failed=1
    else
        echo "table 2022 是拆分后的网段列表（不是 default）—— 符合排除已生效的形状。"
    fi
    echo ""

    # 与 tun_block 共用顶部 TUN_EXCLUDED_PREFIXES 同一份数组（顶部注释的承诺：
    # 写入的和校验的是同一份，不各自漂移——旧版这里硬编码了第二份，注释在撒谎）。
    # 代表地址从基地址推导（最后一段 0 换成 1），无需第二个按下标耦合的数组。
    # 127.0.0.1 总是被优先级 0 的 local 表接走，对它的检查恒为真，保留只为列表完整。
    local i dev prefix base probe hit=0 total=${#TUN_EXCLUDED_PREFIXES[@]}

    for i in "${!TUN_EXCLUDED_PREFIXES[@]}"; do
        prefix="${TUN_EXCLUDED_PREFIXES[$i]}"
        base="${prefix%%/*}"
        probe="${base%.*}.1"
        dev=$(ip route get "$probe" 2>/dev/null \
              | awk '{for(j=1;j<=NF;j++) if($j=="dev"){print $(j+1); exit}}')
        if [ -z "$dev" ]; then
            echo "fail $prefix  (ip route get $probe 无结果)"
        elif [ "$dev" = "$tun_dev" ]; then
            echo "fail $prefix  → dev $dev（这是 TUN 网卡，该段仍被代理接管）"
        else
            echo "pass $prefix  → dev $dev（绕过 TUN）"
            hit=$((hit+1))
        fi
    done

    echo ""
    if [ "$hit" -eq "$total" ]; then
        echo "$hit/$total 段均绕过 TUN，排除真实生效。"
    else
        echo "已生效 $hit/$total。本结果只在 Clash Verge 重载配置、重建 TUN 之后才有意义。"
        echo "重载后仍有 fail，看一下 /configs 返回的 tun 对象里有没有 route-exclude-address 字段："
        echo "  curl -s --unix-socket $MIHOMO_SOCKET http://localhost/configs | grep -o 'route-exclude-address'"
        echo "没有该字段就是键名没被采纳，不要把“配置无解析错误”当作生效证据。"
        failed=1
    fi
    echo ""
    return "$failed"
}

# 裸 IP 直连实测：sniffer 是否把 TLS SNI 恢复成域名，让直连规则命中。
#
# 静态检查只能确认 sniffer.enable 写进了配置；真正的判据是造一条「域名上下文
# 丢失」的连接（直连真实 Cloudflare IP，不走 fake-ip），看它命中
# DomainSuffix,dex-gem.ai 还是掉到 MATCH 走代理。
# 2026-08-19 实测：无 sniffer 时这条裸连命中 Match -> chains 指向机场节点。
verify_sniffer_live() {
    local host="litellm.dex-gem.ai"
    local coverage status phase matcher payload runtime_matcher
    echo ""
    echo "=========================================="
    echo "  LiteLLM TLS-SNI 嗅探专用实测"
    echo "=========================================="
    echo ""

    if coverage=$(python3 "$RULES_READER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --policy-host "$host" --policy-target DIRECT); then
        IFS=$'\t' read -r phase matcher payload <<< "$coverage"
    else
        status=$?
        if [ "$status" -eq 2 ]; then
            echo "skip 本地来源没有覆盖 $host 的 DIRECT 域名规则；该专用探针不适用。"
            return 0
        fi
        echo "fail 无法读取本地策略；未运行 LiteLLM 专用探针。"
        return 1
    fi
    case "$matcher" in
        DOMAIN) runtime_matcher="Domain" ;;
        DOMAIN-SUFFIX) runtime_matcher="DomainSuffix" ;;
        *)
            echo "skip 覆盖 $host 的规则不是 DOMAIN/DOMAIN-SUFFIX；该 TLS 探针不适用。"
            return 0
            ;;
    esac
    echo "scope 来源 $phase: $matcher,$payload,DIRECT；只验证该主机的 TLS-SNI 恢复。"

    command -v curl >/dev/null 2>&1 || { echo "fail 未找到 curl，无法实测"; return 1; }
    command -v openssl >/dev/null 2>&1 || {
        echo "skip 未找到 openssl，无法生成 TLS ClientHello（不是通过）"
        return 0
    }

    local configs_json sniffer_on
    if ! configs_json=$(mihomo_api /configs 2>/dev/null); then
        echo "fail Clash Verge 未运行或 /configs 无法读取。"
        return 1
    fi
    if ! sniffer_on=$(printf '%s' "$configs_json" | python3 "$RULES_READER" --sniffer-enabled); then
        echo "fail /configs 不是预期的 JSON。"
        return 1
    fi
    if [ "$sniffer_on" != "true" ]; then
        echo "fail /configs 中 sniffing/sniffer.enable 未启用；重载后再测。"
        return 1
    fi
    echo "pass /configs 报告 sniffer 已启用"

    local realip
    if ! realip=$(curl -fsS --max-time 8 \
        "https://1.1.1.1/dns-query?name=$host&type=A" \
        -H "accept: application/dns-json" \
        | python3 "$RULES_READER" --doh-address); then
        echo "fail DoH 无法取得 $host 的真实 IPv4 地址；未建立探针连接。"
        return 1
    fi
    echo "$host 真实 IP: $realip"

    (sleep 4 | timeout 6 openssl s_client -quiet -connect "$realip:443" -servername "$host" >/dev/null 2>&1) &
    local hold=$!
    sleep 1

    local connections_json result
    connections_json=$(mihomo_api /connections 2>/dev/null || true)
    if [ -n "$connections_json" ]; then
        result=$(printf '%s' "$connections_json" | python3 "$RULES_READER" \
            --connection-result --connection-host "$host" --connection-address "$realip" 2>/dev/null || true)
    fi
    wait "$hold" 2>/dev/null || true

    if [ -z "${result:-}" ]; then
        echo "fail 连接表里没找到 dst=$realip 的专用探针连接；重跑即可。"
        return 1
    fi

    local expected_prefix="$runtime_matcher | $payload | "
    local chains="${result##* | }"
    if [ "${result#"$expected_prefix"}" != "$result" ] && [[ "|$chains|" == *"|DIRECT|"* ]]; then
        echo "pass 专用探针命中: $result"
        echo "     SNI 恢复了 $host，并按对应 DIRECT 域名规则出站。"
    else
        echo "fail 专用探针命中: $result"
        echo "     结果未匹配来源中的 $matcher,$payload,DIRECT。"
        return 1
    fi
    echo ""
}

extract_top_level_block() {
    local file="$1"
    local key="$2"
    awk -v key="$key" '
        $0 == key ":" {found=1}
        found && $0 != key ":" && /^[^[:space:]]/ {exit}
        found {print}
    ' "$file"
}

extract_fake_ip_filter_block() {
    local file="$1"
    awk '
        /^[[:space:]]*fake-ip-filter:[[:space:]]*$/ {
            match($0, /^[[:space:]]*/); ind=RLENGTH; found=1
        }
        found && $0 !~ /^[[:space:]]*$/ {
            match($0, /^[[:space:]]*/)
            if (seen && RLENGTH <= ind) exit
            seen=1
        }
        found {print}
    ' "$file"
}

verify_merge_yaml() {
    local file="$1"
    local fail=0
    local n_filter n_tun n_sniffer actual expected

    echo ""
    echo "=========================================="
    echo "  Merge 生成块与结构校验"
    echo "=========================================="

    if [ ! -f "$file" ]; then
        echo "fail 文件不存在: $file"
        return 1
    fi

    n_filter=$(grep -cE '^[[:space:]]*fake-ip-filter:' "$file" || true)
    n_tun=$(grep -c '^tun:' "$file" || true)
    n_sniffer=$(grep -c '^sniffer:' "$file" || true)
    for count_spec in "fake-ip-filter:$n_filter" "tun:$n_tun" "sniffer:$n_sniffer"; do
        local label="${count_spec%%:*}"
        local count="${count_spec##*:}"
        if [ "$count" -eq 1 ]; then
            echo "pass $label 恰好 1 处"
        else
            echo "fail $label 出现 $count 处（应为 1）"
            fail=1
        fi
    done

    expected=$(fake_ip_filter_block)
    actual=$(extract_fake_ip_filter_block "$file")
    if [ "$actual" = "$expected" ]; then
        echo "pass fake-ip-filter 与规范生成块逐字一致"
    else
        echo "fail fake-ip-filter 与规范生成块不一致"
        fail=1
    fi

    expected=$(sniffer_block)
    actual=$(extract_top_level_block "$file" sniffer)
    if [ "$actual" = "$expected" ]; then
        echo "pass sniffer 与规范生成块逐字一致"
    else
        echo "fail sniffer 与规范生成块不一致"
        fail=1
    fi

    expected=$(tun_block)
    actual=$(extract_top_level_block "$file" tun)
    if [ "$actual" = "$expected" ]; then
        echo "pass tun 与规范生成块逐字一致"
    else
        echo "fail tun 与规范生成块不一致"
        fail=1
    fi

    if python3 "$RULES_READER" --yaml-check "$file" >/dev/null; then
        echo "pass YAML 可解析"
    else
        echo "fail YAML 解析失败——文件结构已损坏，请从备份恢复"
        fail=1
    fi

    if [ "$fail" -eq 1 ]; then
        echo "结构校验未通过。备份在同目录 *.backup.*，可用菜单 4 恢复。"
        return 1
    fi
    echo "note 本检查验证生成块和 YAML 结构；不会把写入等同于核心已重载。"
}
