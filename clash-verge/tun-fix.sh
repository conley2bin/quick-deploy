#!/bin/bash
# Clash Verge 优化脚本
# 修改全局 Merge 配置，自动应用到所有订阅

set -e

# 配置
# RULES_DIR is overridable only for isolated tests; normal use is repository-local.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RULES_DIR="${RULES_DIR:-$SCRIPT_DIR/rules}"
RULES_RENDERER="$RULES_DIR/render-rules.py"
CLASH_DIR="${CLASH_DIR:-$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev}"
PROFILES_YAML="$CLASH_DIR/profiles.yaml"

require_profiles() {
    [ -f "$PROFILES_YAML" ] || {
        echo "未找到 $PROFILES_YAML，请先启动 Clash Verge 生成配置" >&2
        return 1
    }
}

# `rules check` / `rules render` operate solely on repository sources, so they
# deliberately do not require a live Verge profile registry.
check_route_sources() {
    python3 "$RULES_RENDERER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --check
}

render_route_script() {
    python3 "$RULES_RENDERER" \
        --direct "$RULES_DIR/direct.yaml" \
        --proxy "$RULES_DIR/proxy.yaml" \
        --render
}

check_route_migration() {
    python3 "$RULES_RENDERER" \
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

# 获取全局 Merge 配置文件
get_merge_config() {
    local merge_file=$(awk '
        $0 ~ /^- uid: Merge$/ {found=1; next}
        found && /file:/ {print $2; exit}
    ' "$PROFILES_YAML")

    if [ -n "$merge_file" ]; then
        echo "$CLASH_DIR/profiles/$merge_file"
    else
        # 未登记就写出 Merge.yaml 是一个 Verge 永远不加载的孤儿文件——必须让人知道
        echo "warning: profiles.yaml 未登记全局 Merge；写出的 Merge.yaml 可能不会被加载。" >&2
        echo "         请先在 Clash Verge 的「全局扩展配置」中启用 Merge。" >&2
        echo "$CLASH_DIR/profiles/Merge.yaml"
    fi
}

# 获取全局 Script 配置文件
get_script_config() {
    local script_file=$(awk '
        $0 ~ /^- uid: Script$/ {found=1; next}
        found && /file:/ {print $2; exit}
    ' "$PROFILES_YAML")

    if [ -n "$script_file" ]; then
        echo "$CLASH_DIR/profiles/$script_file"
    else
        echo "warning: profiles.yaml 未登记全局 Script；写出的 Script.js 可能不会被加载。" >&2
        echo "         请先在 Clash Verge 的「全局扩展配置」中启用 Script。" >&2
        echo "$CLASH_DIR/profiles/Script.js"
    fi
}

# Rules-only apply must never create a Script.js that Verge cannot load. Unlike
# the legacy informational helper above, this requires the registered global
# target and rejects path-like registry values.
get_registered_script_config() {
    local script_file
    script_file=$(python3 "$RULES_RENDERER" --registry "$PROFILES_YAML" --script-target) || return 1
    echo "$CLASH_DIR/profiles/$script_file"
}

# 获取订阅名称
get_profile_name() {
    local uid
    uid=$(get_current_profile_uid)
    [ -n "$uid" ] || { echo "(profiles.yaml 中没有 current)"; return; }
    # 整行精确匹配：grep "uid: $uid" 是子串匹配，uid 前缀碰撞（R1 撞 R1abc）
    # 或空 uid（匹配一切）时会返回多行垃圾名字
    awk -v uid="$uid" '
        $0 == "- uid: " uid {found=1; next}
        found && /^  name: / {sub(/^  name: /, ""); print; exit}
        found && /^- uid: / {exit}
    ' "$PROFILES_YAML"
}

# 获取当前订阅 UID
get_current_profile_uid() {
    awk '/^current:/ {print $2; exit}' "$PROFILES_YAML"
}

# 根据 UID 获取配置文件名
get_profile_item_file() {
    local target_uid="$1"

    awk -v uid="$target_uid" '
        $0 ~ /^- uid: / {found=($3==uid); next}
        found && /^  file: / {print $2; exit}
    ' "$PROFILES_YAML"
}

# 根据 UID 获取配置文件路径
get_profile_item_path() {
    local target_uid="$1"
    local file

    file=$(get_profile_item_file "$target_uid")
    [ -n "$file" ] || return 0
    echo "$CLASH_DIR/profiles/$file"
}

# 获取当前订阅 option 绑定的配置 UID
get_current_profile_option_uid() {
    local key="$1"
    local current_uid

    current_uid=$(get_current_profile_uid)
    [ -z "$current_uid" ] && return

    awk -v uid="$current_uid" -v key="$key" '
        $0 ~ /^- uid: / {in_item=($3==uid); in_option=0; next}
        in_item && /^  option:$/ {in_option=1; next}
        in_item && in_option && $1==(key ":") {print $2; exit}
        in_item && in_option && /^  [^[:space:]]/ {in_option=0}
    ' "$PROFILES_YAML"
}

# 获取当前订阅 option 绑定的配置路径
get_current_profile_option_path() {
    local key="$1"
    local uid

    uid=$(get_current_profile_option_uid "$key")
    [ -n "$uid" ] || return 0
    get_profile_item_path "$uid"
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

# 更新或创建 Merge 配置中的 Fake-IP Filter
update_fake_ip_filter() {
    local file="$1"

    # 检查文件是否存在
    if [ ! -f "$file" ]; then
        echo "创建新的 Merge 配置文件: $file"
        {
            printf '# Clash Verge Merge 配置\n# 此文件会与订阅配置合并，提供全局增强\n\n'
            printf 'dns:\n'
            fake_ip_filter_block
            echo ""
            # TUN 块只有一处定义，见 tun_block()
            tun_block
        } > "$file"
        echo "pass Merge 配置已创建"
        return
    fi

    # 删除旧 fake-ip-filter 块：按缩进整块删除（从键行起，删到下一个缩进
    # 不超过键缩进的行）。不能用 sed 范围正则——列表项含冒号（rule-set:、
    # geosite: 是 mihomo 真实语法）会提前终结范围，残留条目被 YAML 吸收成
    # 前一个键的标量续行，布尔键类型静默损坏。
    if grep -qE '^[[:space:]]*fake-ip-filter:' "$file"; then
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
        ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
    fi

    # 查找 dns: 区块的位置
    if grep -q "^dns:" "$file"; then
        # 在 dns: 后面插入规范块（awk -v 传值，避免 sed 命令过长）
        local filter_content
        filter_content=$(fake_ip_filter_block)
        awk -v filter="$filter_content" '/^dns:/ {print; print filter; next} 1' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
    else
        # 文件中没有 dns: 区块，添加整个区块
        {
            echo ""
            printf 'dns:\n'
            fake_ip_filter_block
        } >> "$file"
    fi

    echo "pass Fake-IP Filter 已更新（保留 ssh.github.com 的域名上下文）"
}

# 写入规范 TUN 配置（可重复执行）
#
# 旧实现在 grep -q '^tun:' 时直接 return，导致任何跑过旧版本的机器会永久保留
# 带 exclude-routes 的无效块。现在改为：有旧块就整块删除，然后追加规范块。
update_tun_config() {
    local file="$1"

    if [ ! -f "$file" ]; then
        tun_block > "$file"
        echo "已创建并写入 TUN 配置块: $file"
        return
    fi

    # 备份由 optimize_all 的前置备份统一负责（它先于一切修改）
    local had_block=0
    grep -q '^tun:' "$file" && had_block=1

    # 删除已有 tun: 块（从 ^tun: 到下一个顶层键），并去掉尾部空行，
    # 使重复执行产生字节级相同的结果。
    awk '
        BEGIN {skip=0; pending=0}
        /^tun:/ {skip=1; next}
        skip && /^[^[:space:]]/ {skip=0}
        skip {next}
        /^[[:space:]]*$/ {pending++; next}
        {while (pending-- > 0) print ""; pending=0; print}
    ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"

    echo "" >> "$file"
    tun_block >> "$file"

    if [ "$had_block" -eq 1 ]; then
        echo "已替换原有 tun: 块为规范块 (route-exclude-address)"
    else
        echo "已追加 TUN 配置块 (route-exclude-address)"
    fi
    echo "这只说明文本已写入；是否真的生效需 Clash Verge 重载后用 verify_tun_routes 看内核"
}

# 写入规范 sniffer 配置（可重复执行）
#
# 删除旧 sniffer: 块（从 ^sniffer: 到下一个顶层键）后，插到 tun: 块之前；
# 没有 tun: 块就追加到文件末尾。插到 tun 之前是为了让本函数与 update_tun_config
# 各自重复执行时文件内容字节级一致（update_tun_config 会把 tun 块挪到文件尾，
# 两个函数都往末尾追加会让两块来回换位置）。
update_sniffer_config() {
    local file="$1"

    if [ ! -f "$file" ]; then
        sniffer_block > "$file"
        echo "已创建并写入 sniffer 配置块: $file"
        return
    fi

    # 与 update_tun_config 同一套删除 + 尾部空行规范，保证幂等
    awk '
        BEGIN {skip=0; pending=0}
        /^sniffer:/ {skip=1; next}
        skip && /^[^[:space:]]/ {skip=0}
        skip {next}
        /^[[:space:]]*$/ {pending++; next}
        {while (pending-- > 0) print ""; pending=0; print}
    ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"

    local content
    content=$(sniffer_block)
    if grep -q '^tun:' "$file"; then
        awk -v block="$content" '/^tun:/ {print block; print ""; print; next} 1' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
        echo "已写入 sniffer 配置块（位于 tun 块之前）"
    else
        echo "" >> "$file"
        printf '%s\n' "$content" >> "$file"
        echo "已追加 sniffer 配置块（文件中没有 tun 块）"
    fi
    echo "这只说明文本已写入；是否真的生效需 Clash Verge 重载后用 verify_sniffer_live 实测"
}

# 验证直连规则在活跃配置里的真实位次
#
# 为什么不能只查 Script.js 里有没有这行字：旧版生成的 main() 用
# new Set(config.rules) 去重，订阅里已存在的同串规则会被跳过，于是它保留在
# 订阅的原始深位而不是被提到顶部 —— 文件里白纸黑字写着，实际排在 537/541。
# 只有问内核拿到的那份规则表才能分辨这两种情况。
verify_direct_rules() {
    echo ""
    echo "=========================================="
    echo "  直连规则位次检查（活跃配置）"
    echo "=========================================="
    echo ""

    local sock="/tmp/verge/verge-mihomo.sock"

    if ! command -v curl >/dev/null 2>&1; then
        echo "未找到 curl，无法查询内核规则表"
        return
    fi
    if ! python3 -c '' 2>/dev/null; then
        echo "未找到 python3，跳过规则位次检查（这是跳过，不是通过）"
        return
    fi

    local rules_json
    rules_json=$(curl -s --max-time 5 --unix-socket "$sock" http://localhost/rules 2>/dev/null || true)

    if [ -z "$rules_json" ]; then
        echo "Clash Verge 未运行或尚未重载，无法判断规则是否生效。"
        echo "（查不到不等于通过。重载 Clash Verge 后重跑本检查。）"
        return
    fi

    printf '%s' "$rules_json" | python3 -c '
import json, sys

try:
    rules = json.load(sys.stdin)["rules"]
except Exception as e:
    print("fail /rules 返回不是预期的 JSON：%s" % e)
    sys.exit(1)

print("规则总数: %d" % len(rules))
print("")

# 第一条会把流量引向代理的规则。REJECT 不算 —— 它不会把目标弄到境外，
# 广告拦截排在前面是正常的。
barrier = None
for i, r in enumerate(rules):
    p = r.get("proxy", "")
    if p not in ("DIRECT", "REJECT", "REJECT-DROP", "PASS"):
        barrier = i
        break

if barrier is None:
    print("未找到任何代理规则（全直连配置？），位次断言无意义")
    barrier = len(rules)
else:
    b = rules[barrier]
    print("第一条代理规则在下标 %d: %s | %s -> %s"
          % (barrier, b.get("type"), b.get("payload"), b.get("proxy")))
print("")

# 与 Script.js 的 forceTop 一一对应。mihomo /rules 的 type 是驼峰形式。
force_top = [
    ("Domain",       "ssh.github.com"),
    ("DstPort",      "22"),
    ("DomainSuffix", "dex-gem.ai"),
    ("DomainSuffix", "feishu.cn"),
    ("DomainSuffix", "feishucdn.com"),
    ("DomainSuffix", "larkoffice.com"),
]

fail = 0
for typ, payload in force_top:
    hits = [i for i, r in enumerate(rules)
            if r.get("type") == typ and r.get("payload") == payload]
    label = "%s,%s" % (typ, payload)
    if not hits:
        print("fail %-32s 不在规则表里" % label)
        fail = 1
        continue
    idx = hits[0]
    proxy = rules[idx].get("proxy")
    if proxy != "DIRECT":
        print("fail %-32s 下标 %d 但目标是 %s（应为 DIRECT）" % (label, idx, proxy))
        fail = 1
    elif idx < barrier:
        print("pass %-32s 下标 %d，在首条代理规则之前" % (label, idx))
    else:
        print("fail %-32s 下标 %d，前面挡着 %d 条代理规则——未被提到顶部"
              % (label, idx, barrier))
        fail = 1
    if len(hits) > 1:
        print("     note 该规则出现 %d 次（下标 %s），原位副本应已被移除"
              % (len(hits), hits))
        fail = 1

print("")
if fail:
    print("位次检查未通过。若刚改完配置，先重载 Clash Verge 再重跑；")
    print("重载后仍 fail 说明全局 Script.js 没被加载，或订阅级 script 覆盖了它。")
else:
    print("forceTop 规则均位于首条代理规则之前，前置真实生效。")
' || true

    echo ""
    echo "注：位次正确只说明规则会被优先匹配。飞书要真命中 DOMAIN-SUFFIX，"
    echo "还需要它不在 fake-ip-filter 里（由 verify_merge_yaml 单独断言）。"
    echo "想看实际命中，浏览器打开飞书后跑："
    echo "  curl -s --unix-socket $sock http://localhost/connections \\"
    echo "    | python3 -c \"import json,sys;[print(c['metadata'].get('host'),c.get('rule'),c.get('chains')) for c in json.load(sys.stdin)['connections'] if 'feishu' in (c['metadata'].get('host') or '')]\""
    echo "host 字段非空、rule 为 DomainSuffix 才是域名规则真的接管了。"
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
        echo "未找到 ip 命令，无法检查"
        return
    fi

    local routes tun_dev route_count has_default
    routes=$(ip route show table 2022 2>/dev/null || true)

    if [ -z "$routes" ]; then
        echo "table 2022 为空 —— TUN 未启用，或 auto-route 没有装路由。无法判断排除是否生效。"
        return
    fi

    tun_dev=$(printf '%s\n' "$routes" | awk '{for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')
    route_count=$(printf '%s\n' "$routes" | grep -c .)
    has_default=$(printf '%s\n' "$routes" | grep -c '^default ' || true)

    echo "TUN 网卡: ${tun_dev:-未知}    table 2022 路由数: $route_count"
    if [ "$has_default" -gt 0 ]; then
        echo "table 2022 里是一条 default —— 全部流量无差别地进 TUN，排除没有生效。"
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
        echo "  curl -s --unix-socket /tmp/verge/verge-mihomo.sock http://localhost/configs | grep -o 'route-exclude-address'"
        echo "没有该字段就是键名没被采纳，不要再把“配置无解析错误”当作生效证据。"
    fi
    echo ""
}

# 裸 IP 直连实测：sniffer 是否把 TLS SNI 恢复成域名，让直连规则命中。
#
# 静态检查只能确认 sniffer.enable 写进了配置；真正的判据是造一条「域名上下文
# 丢失」的连接（直连真实 Cloudflare IP，不走 fake-ip），看它命中
# DomainSuffix,dex-gem.ai 还是掉到 MATCH 走代理。
# 2026-08-19 实测：无 sniffer 时这条裸连命中 Match -> chains 指向机场节点。
verify_sniffer_live() {
    echo ""
    echo "=========================================="
    echo "  裸 IP 直连实测（sniffer 生效性）"
    echo "=========================================="
    echo ""

    local sock="/tmp/verge/verge-mihomo.sock"

    if ! command -v curl >/dev/null 2>&1; then
        echo "未找到 curl，无法实测"
        return
    fi
    if ! command -v openssl >/dev/null 2>&1; then
        echo "note 未找到 openssl，无法造 TLS ClientHello，跳过实测（这是跳过，不是通过）"
        return
    fi

    local rules_json
    rules_json=$(curl -s --max-time 5 --unix-socket "$sock" http://localhost/rules 2>/dev/null || true)
    if [ -z "$rules_json" ]; then
        echo "Clash Verge 未运行，无法实测。"
        return
    fi

    # 先看静态配置是否进了内核。mihomo 1.19 的 /configs 把嗅探段序列化为
    # "sniffing" 键（值为 true 或对象），不是配置文件里的 sniffer 键。
    local sniffer_on
    sniffer_on=$(curl -s --max-time 5 --unix-socket "$sock" http://localhost/configs 2>/dev/null \
        | python3 -c 'import json,sys
c=json.load(sys.stdin)
s=c.get("sniffing")
s2=c.get("sniffer") or {}
ok=bool(s) or bool(s2.get("enable"))
print("True" if ok else "False")' 2>/dev/null || true)
    if [ "$sniffer_on" = "True" ]; then
        echo "pass /configs 中 sniffing/sniffer.enable = true"
    else
        echo "fail /configs 中 sniffing/sniffer.enable = ${sniffer_on:-缺字段} —— 配置没进内核"
        echo "      刚改完 Merge 配置还没重载时，这里看到的就是旧内核状态；重载 Clash Verge 后重跑。"
        return
    fi

    # 拿真实 IP：DoH 查询走 https，绕开 mihomo 的 :53 hijack，拿到的不是 fake IP。
    # 这条 DoH 连接本身可能走代理，但它返回的 JSON 里是真实解析结果。
    local realip
    realip=$(curl -s --max-time 8 "https://1.1.1.1/dns-query?name=litellm.dex-gem.ai&type=A" \
        -H "accept: application/dns-json" 2>/dev/null \
        | python3 -c 'import json,sys
try:
    print([a["data"] for a in json.load(sys.stdin)["Answer"] if a.get("type")==1][0])
except Exception:
    pass' 2>/dev/null || true)

    if [ -z "$realip" ]; then
        echo "fail DoH 拿不到 litellm.dex-gem.ai 的真实 IP，无法实测"
        return
    fi
    echo "litellm.dex-gem.ai 真实 IP: $realip"

    # 保持一条带 SNI 的裸连接开几秒，让它在连接表里可查。
    # openssl 发 TLS ClientHello（SNI=litellm.dex-gem.ai）后挂着不动，
    # 模拟「域名上下文丢失、只有真实 IP」的连接。
    (sleep 4 | timeout 6 openssl s_client -quiet -connect "$realip:443" -servername litellm.dex-gem.ai >/dev/null 2>&1) &
    local hold=$!
    sleep 1

    local result
    result=$(curl -s --max-time 5 --unix-socket "$sock" http://localhost/connections 2>/dev/null \
        | python3 -c '
import json,sys
try:
    conns=json.load(sys.stdin)["connections"]
except Exception:
    sys.exit(0)
# 第一轮：sniffHost 非空的连接才是嗅探恢复出的裸连，优先取它。
# 不能先按 remoteDestination 匹配 —— pi 的 fake-ip 直连（嗅探无关）也指向
# 同一个真实 IP，先命中它会把「没嗅探也直连」误报成「嗅探生效」。
found=None
for c in conns:
    if c.get("metadata",{}).get("sniffHost")=="litellm.dex-gem.ai":
        found=c; break
# 第二轮：嗅探失败时裸连的 destinationIP 就是真实 IP，且 host 为空 ——
# 靠 host=="" 把它与 pi 的 fake-ip 直连（host 非空）区分开，避免误报。
if found is None:
    for c in conns:
        m=c.get("metadata",{})
        if m.get("host")=="" and (m.get("destinationIP")==sys.argv[1] or m.get("remoteDestination")==sys.argv[1]):
            found=c; break
if found:
    print(found.get("rule",""), "|", found.get("rulePayload",""), "|", "|".join(found.get("chains") or []))
' "$realip" 2>/dev/null || true)
    # wait 会透传后台任务的退出码：timeout 掐掉 openssl 时是 124，
    # 必须 || true，否则 set -e 会把脚本从函数中途静默终止。
    wait "$hold" 2>/dev/null || true

    if [ -z "$result" ]; then
        echo "fail 连接表里没找到 dst=$realip 的连接（采样窗口太短），重跑本检查即可"
        return
    fi

    case "$result" in
        DomainSuffix*dex-gem.ai*DIRECT*)
            echo "pass 裸 IP 连接命中: $result"
            echo "      sniffer 从 SNI 恢复了域名，真实 IP 路径也直连，不再依赖 fake-ip"
            ;;
        *)
            echo "fail 裸 IP 连接命中: $result"
            echo "      sniffer 没有恢复域名，真实 IP 路径仍在走代理"
            ;;
    esac
    echo ""
}

# 移除 prepend-rules（已改用全局脚本写入 rules）
remove_prepend_rules() {
    local file="$1"

    if ! grep -q "^prepend-rules:" "$file"; then
        return
    fi

    awk '
        BEGIN {skip=0}
        /^prepend-rules:/ {skip=1; next}
        skip && /^[^[:space:]]/ {skip=0}
        !skip {print}
    ' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"

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
    script_file=$(get_registered_script_config) || return 1
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
    local backup_file timestamp

    if [ -z "$PREPARED_ROUTE_SCRIPT" ] || [ -z "$PREPARED_ROUTE_CANDIDATE" ]; then
        echo "没有已准备的路由脚本候选；未写入。" >&2
        return 1
    fi
    if [ -f "$PREPARED_ROUTE_SCRIPT" ]; then
        # Keep the historical YYYYMMDD_HHMMSS prefix used by backup-menu parsing,
        # then add mktemp entropy so two applies in one second cannot collide.
        timestamp=$(date +%Y%m%d_%H%M%S)
        backup_file=$(mktemp "$PREPARED_ROUTE_SCRIPT.backup.${timestamp}.XXXXXX") || {
            cleanup_prepared_route_rules
            return 1
        }
        if ! cp -- "$PREPARED_ROUTE_SCRIPT" "$backup_file"; then
            rm -f -- "$backup_file"
            cleanup_prepared_route_rules
            return 1
        fi
    fi
    # Candidate and target share a directory, so replacement is atomic.
    if ! mv -f -- "$PREPARED_ROUTE_CANDIDATE" "$PREPARED_ROUTE_SCRIPT"; then
        cleanup_prepared_route_rules
        return 1
    fi
    echo "路由规则已写入已登记的全局脚本: $PREPARED_ROUTE_SCRIPT"
    if [ -n "${backup_file:-}" ]; then
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
remove_managed_ssh_block() {
    local ssh_config="$1"

    [ -f "$ssh_config" ] || return 0

    # 范围删除前先确认终点标记在场：sed 范围终点缺失时会一路删到 EOF，
    # 无关的 Host 块会被整文件清空（备份可回，但 verify 只查 github 解析，照样 pass）
    if grep -q '^# >>> tun-fix\.sh github ssh >>>$' "$ssh_config"; then
        if grep -q '^# <<< tun-fix\.sh github ssh <<<$' "$ssh_config"; then
            sed -i '/^# >>> tun-fix\.sh github ssh >>>$/,/^# <<< tun-fix\.sh github ssh <<<$/d' "$ssh_config"
        else
            echo "warning 发现未配对的 tun-fix SSH 块起始标记，为避免误删未做清理（请手动检查 ~/.ssh/config）"
        fi
    fi
    # 旧格式：旧版本写的块以注释头开始、以 ControlPersist no 结尾
    if grep -q '^# GitHub SSH over HTTPS port' "$ssh_config"; then
        if grep -qE '^[[:space:]]*ControlPersist no[[:space:]]*$' "$ssh_config"; then
            sed -i '/^# GitHub SSH over HTTPS port/,/^[[:space:]]*ControlPersist no[[:space:]]*$/d' "$ssh_config"
        else
            echo "warning 旧格式 SSH 块缺少结束标记（ControlPersist no），为避免误删未做清理"
        fi
    fi
    # 规范空行：连续空行压成一行，并去掉首尾空行。
    # 不能用 sed '/^$/N;/^\n$/d'（旧实现）：那是成对删除，删完块后剩下的两个
    # 空行会被整体删掉，用户原有条目被粘到上一段末尾。
    # 去尾空行是幂等性的关键：追加的块自带一个前置空行，不去尾就会每跑一次多一行。
    local squeezed
    squeezed=$(mktemp "${ssh_config}.tmp.XXXXXX")
    awk '
        BEGIN {pending=0; started=0}
        /^[[:space:]]*$/ {pending=1; next}
        {if (pending && started) print ""; pending=0; started=1; print}
    ' "$ssh_config" > "$squeezed" && cat "$squeezed" > "$ssh_config"
    rm -f "$squeezed"
}

# ~/.ssh/config 中本脚本管理的段落 —— 全脚本唯一副本，输出到 stdout
github_ssh_block() {
    cat << 'EOF'
# >>> tun-fix.sh github ssh >>>
# GitHub SSH uses ssh.github.com:443; the port alone does not select a route.
# Clash rule DOMAIN,ssh.github.com,DIRECT selects the local outbound.
# IPQoS none avoids the reproduced post-auth stall on this machine's TUN path.
Host github.com ssh.github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/conley
    IdentitiesOnly yes
    IPQoS none
    ConnectTimeout 8
# <<< tun-fix.sh github ssh <<<
EOF
}

# 用 ssh 自己的解析结果确认块真的生效，而不是被更靠前的块遮蔽了。
# 写入成功 ≠ 生效：SSH 取首个匹配到的值，且会需要考虑 Include 与 /etc/ssh/ssh_config。
verify_github_ssh_config() {
    local out h p qos proxy

    if ! command -v ssh >/dev/null 2>&1; then
        echo "未找到 ssh 命令，跳过生效检查（不是通过）"
        return 1
    fi

    out=$(ssh -G github.com 2>/dev/null || true)
    h=$(printf '%s\n' "$out" | awk '$1=="hostname"{print $2; exit}')
    p=$(printf '%s\n' "$out" | awk '$1=="port"{print $2; exit}')
    qos=$(printf '%s\n' "$out" | awk '$1=="ipqos"{print $2 " " $3; exit}')
    proxy=$(printf '%s\n' "$out" | awk '($1=="proxycommand" || $1=="proxyjump") && $2!="none"{print $1}')

    if [ "$h" = "ssh.github.com" ] && [ "$p" = "443" ] && \
       [ "$qos" = "none none" ] && [ -z "$proxy" ]; then
        echo "pass SSH 解析为 $h:$p，IPQoS none，无跳板"
        echo "     这是静态配置检查；还需用 git ls-remote 和活跃连接确认 DIRECT 与仓库权限。"
        return 0
    fi

    echo "fail SSH 解析为 ${h:-?}:${p:-?}，IPQoS=${qos:-?}，跳板设置=${proxy:-无}"
    echo "     检查 Host / Match / Include 的首值优先规则，以及旧的 ProxyCommand/ProxyJump："
    grep -nE '^[[:space:]]*(Host|Match|Include|ProxyCommand|ProxyJump|IPQoS)[[:space:]]' "$HOME/.ssh/config" 2>/dev/null | head -20
    return 1
}

# 配置 SSH (可选)
configure_ssh() {
    local ssh_config="$HOME/.ssh/config"
    local timestamp
    timestamp=$(date +%Y%m%d_%H%M%S)

    echo ""
    echo "==========================================="
    echo "  配置 SSH for GitHub (可选)"
    echo "==========================================="
    echo ""
    echo "此配置使用 ssh.github.com:443，并设置 IPQoS none，不增加跳板。"
    echo "路由由 Clash 决定：选项 1 生成 DOMAIN,ssh.github.com,DIRECT。"
    echo "DST-PORT,22,DIRECT 不覆盖 443；只改端口不等于直连。"
    echo ""
    echo "认证后卡住时，需区分路由与 QoS：同一 DIRECT 路径的 A/B/A 实测中，"
    echo "默认 IPQoS 两次超时，IPQoS none 成功；不能仅凭换节点失败认定机场封 SSH。"
    echo "ConnectTimeout 只限制连接/初始握手，不是整个 git 命令的超时。"
    echo ""
    echo "选项 2 只改 SSH；选项 1 会改更多路由和 DNS 设置。"
    echo "若只修 GitHub，可在订阅 Rules 扩展 prepend 中加入专用 DIRECT 规则后重载。"
    echo ""

    # 检查是否已配置
    if [ -f "$ssh_config" ] && grep -qE '^# >>> tun-fix\.sh github ssh >>>|^Host github\.com' "$ssh_config"; then
        echo "warning  检测到 ~/.ssh/config 中已存在 GitHub 配置"
        echo ""
        grep -A 10 "^Host github.com" "$ssh_config" || true
        echo ""
        echo -n "是否覆盖现有配置？[y/N]: "
        read -r overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            echo "已取消 SSH 配置"
            return
        fi

        cp "$ssh_config" "$ssh_config.backup.$timestamp"
        echo "pass 已备份原配置到: $ssh_config.backup.$timestamp"

        remove_managed_ssh_block "$ssh_config"
    fi

    echo ""
    echo "正在添加 SSH 配置..."

    # 确保 .ssh 目录存在
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"

    # 备份现有配置（如果存在且未备份）
    if [ -f "$ssh_config" ] && [ ! -f "$ssh_config.backup.$timestamp" ]; then
        cp "$ssh_config" "$ssh_config.backup.$timestamp"
        echo "pass 已备份原配置到: $ssh_config.backup.$timestamp"
    fi

    # 写到文件最前面，不是追加。
    # SSH 配置是「首个匹配到的值生效」：追加到末尾时，任何更靠前的
    # Host github.com / Host * 块都会盖掉本块，而旧实现仍然打印成功。
    # 实测：手写 Host github.com 在前时，ssh -G 解析出 github.com:22，本块彻底无效。
    # OpenSSH 的惯例本来就是特例在前、Host * 在末尾。
    local tmp
    tmp=$(mktemp "${ssh_config}.tmp.XXXXXX")
    {
        github_ssh_block
        echo ""
        if [ -f "$ssh_config" ]; then
            cat "$ssh_config"
        fi
    } > "$tmp"
    cat "$tmp" > "$ssh_config"
    rm -f "$tmp"

    chmod 600 "$ssh_config"

    echo ""
    echo "pass SSH 配置已写入 ~/.ssh/config 顶部"

    # 本脚本之外的 github.com 块不删（那是用户的东西），但必须告知已被遮蔽
    local shadowed
    shadowed=$(awk '
        /^# >>> tun-fix\.sh github ssh >>>$/ {inblk=1}
        /^# <<< tun-fix\.sh github ssh <<<$/ {inblk=0; next}
        !inblk && /^[[:space:]]*Host[[:space:]].*github\.com/ {print "       " NR ": " $0}
    ' "$ssh_config")
    if [ -n "$shadowed" ]; then
        echo ""
        echo "note 文件里还有本脚本之外的 github.com 块，已被前置块遮蔽（未删除）："
        printf '%s\n' "$shadowed"
    fi

    echo ""
    if verify_github_ssh_config; then
        echo ""
        echo "测试连接:"
        echo "  ssh -T git@github.com"
    fi
    echo ""
}

# 写后结构校验：sed/awk 文本手术的成功输出不等于文件结构正确。
# 只读目录、含冒号的列表项、重复插入……这一类失败的共同特征是
# "写入零字节或结构损坏，照样打印 pass"，必须有一个验证器兜底。
verify_merge_yaml() {
    local file="$1"
    local fail=0

    echo ""
    echo "=========================================="
    echo "  Merge 配置结构校验"
    echo "=========================================="

    if [ ! -f "$file" ]; then
        echo "fail 文件不存在: $file"
        return 1
    fi

    # grep -c 计数为 0 时退出码是 1，赋值必须带 || true 防 set -e 误杀
    local n_filter n_tun n_sniffer
    n_filter=$(grep -cE '^[[:space:]]*fake-ip-filter:' "$file" || true)
    n_tun=$(grep -c '^tun:' "$file" || true)
    n_sniffer=$(grep -c '^sniffer:' "$file" || true)

    if [ "$n_filter" -eq 1 ]; then
        echo "pass fake-ip-filter 恰好 1 处"
    else
        echo "fail fake-ip-filter 出现 $n_filter 处（应为 1）"
        fail=1
    fi

    if [ "$n_tun" -eq 1 ]; then
        echo "pass tun 块恰好 1 处"
    else
        echo "fail tun: 出现 $n_tun 处（应为 1）"
        fail=1
    fi

    if [ "$n_sniffer" -eq 1 ]; then
        echo "pass sniffer 块恰好 1 处"
    else
        echo "fail sniffer: 出现 $n_sniffer 处（应为 1）"
        fail=1
    fi
    if grep -A 3 '^sniffer:' "$file" | grep -q 'enable: true'; then
        echo "pass sniffer 块含 enable: true"
    else
        echo "fail sniffer 块缺 enable: true"
        fail=1
    fi

    # 规范条目抽查（与 fake_ip_filter_block / tun_block 单源内容对应的代表项）
    local entry
    for entry in "- 'github.com'" "- '*.tsinghua.edu.cn'" "- '+._tcp'"; do
        if grep -qF -- "$entry" "$file"; then
            echo "pass 条目在场: $entry"
        else
            echo "fail 条目缺失: $entry"
            fail=1
        fi
    done

    # 不允许重新引入匹配 ssh.github.com 的通配符过滤，否则专用域名规则失去上下文。
    if grep -qE "^[[:space:]]*-[[:space:]]*['\"]?(\*\.github\.com|ssh\.github\.com)['\"]?[[:space:]]*$" "$file"; then
        echo "fail fake-ip-filter 过滤了 ssh.github.com，SSH 的 DIRECT 域名规则无法可靠匹配"
        fail=1
    else
        echo "pass fake-ip-filter 保留 ssh.github.com 的域名上下文"
    fi

    if grep -q 'route-exclude-address' "$file"; then
        echo "pass route-exclude-address 在场"
    else
        echo "fail route-exclude-address 缺失"
        fail=1
    fi

    # 飞书/Lark 必须不在 fake-ip-filter 里，否则 DOMAIN-SUFFIX,feishu.cn,DIRECT 不可能命中。
    # 旧版本写过这四条；update_fake_ip_filter 对已有文件是"整块删除后重写"，
    # 残留会被自动清掉；这条断言是防回退的守卫。
    if grep -qE "^[[:space:]]*- '\*?\.?(feishu\.cn|larkoffice\.com)'" "$file"; then
        echo "fail fake-ip-filter 中残留飞书/Lark 条目，域名规则将无法命中："
        grep -nE "^[[:space:]]*- '\*?\.?(feishu\.cn|larkoffice\.com)'" "$file"
        fail=1
    else
        echo "pass fake-ip-filter 中无飞书/Lark 条目（域名上下文得以保留）"
    fi

    # 有 PyYAML 就做真解析（Ubuntu 默认不带 python3-yaml，没有就跳过而不是安装）
    if python3 -c 'import yaml' 2>/dev/null; then
        if python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' "$file" 2>/dev/null; then
            echo "pass YAML 可解析"
        else
            echo "fail YAML 解析失败——文件结构已损坏，请从备份恢复"
            fail=1
        fi
    else
        echo "note 未安装 PyYAML，跳过解析级检查（以上均为文本级断言）"
    fi

    if [ "$fail" -eq 1 ]; then
        echo ""
        echo "结构校验未通过。备份在同目录 *.backup.*，可用菜单 4 恢复。"
        return 1
    fi
}

# 一键优化
optimize_all() {
    local file="$1"

    echo ""
    echo "=========================================="
    echo "  开始优化 Merge 配置"
    echo "=========================================="
    echo ""
    echo "配置文件: $file"
    echo "作用范围: 全局 (所有订阅)"
    echo ""

    # Complete all Script-specific validation, candidate rendering, and foreign
    # confirmation before backing up or touching Merge/subscription files.
    prepare_route_rules || return 1

    # 前置备份：先于一切修改。旧流程唯一的备份在第三步 update_tun_config 里，
    # 前两步（清空订阅级 merge、改写 fake-ip-filter）的改动无备份可回
    if [ -f "$file" ]; then
        local pre_backup="$file.backup.$(date +%Y%m%d_%H%M%S)"
        if ! cp "$file" "$pre_backup"; then
            cleanup_prepared_route_rules
            return 1
        fi
        echo "已备份原 Merge 配置: $pre_backup"
        echo ""
    fi

    if ! clear_subscription_merge || ! remove_prepend_rules "$file" || \
       ! update_fake_ip_filter "$file" || ! update_tun_config "$file" || \
       ! update_sniffer_config "$file"; then
        cleanup_prepared_route_rules
        return 1
    fi
    update_direct_rules "$file"

    # 文本手术的成功输出 ≠ 文件结构正确，写后校验未通过就以非零退出
    verify_merge_yaml "$file"

    # 写入 Script.js ≠ 规则真的在顶部，去问内核拿到的那份规则表
    verify_direct_rules

    # 规则在顶部 ≠ 裸 IP 连接也直连，造一条真实 IP 连接看 sniffer 是否救回域名
    verify_sniffer_live

    echo ""
    echo "=========================================="
    echo "  配置完成！"
    echo "=========================================="
    echo ""
    echo "pass Fake-IP Filter: 已配置 (主域名 + 通配符)"
    echo "  - 本地网络 (*.local, *.lan)"
    echo "  - 企业应用 (飞书、钉钉、字节跳动)"
    echo "  - GitHub 主站及资源域名；ssh.github.com 保留 Fake-IP 域名映射"
    echo ""
    echo "TUN 模式: 已写入 tun: 块（route-exclude-address 排除 192.168.0.0/16、10.0.0.0/8、172.16.0.0/12、127.0.0.0/8）"
    echo "  尚未验证生效：需重载 Clash Verge 后，由 verify_tun_routes 看内核路由确认"
    echo ""
    echo "pass 直连规则: 已写入全局脚本"
    echo "  - 飞书 (feishu.cn / feishucdn.com / larkoffice.com) 直连 —— 实测全部解析到"
    echo "    中国大陆 IP；配套将飞书从 fake-ip-filter 移出，否则连接不带域名，"
    echo "    域名规则永远不会命中（改动前飞书直连完全靠 GEOIP,CN 碰巧接住）"
    echo "  - LiteLLM 网关 (dex-gem.ai) 直连 —— pi CLI 的模型入口；走代理时"
    echo "    节点失联曾导致 pi 全程 Connection error（2026-08-09 实测），直连后免疫"
    echo "  - sniffer: TLS/HTTP/QUIC 嗅探 —— 域名规则只对带域名的连接生效；绕过 mihomo"
    echo "    DNS 的路径（DoH、排除段内 DNS、硬编码 IP）连接以真实 IP 裸进 TUN，此前"
    echo "    一路掉到 MATCH 走代理（2026-08-19 实测）。sniffer 从 TLS SNI 恢复域名，"
    echo "    litellm.dex-gem.ai 的直连不再依赖 fake-ip。由 verify_sniffer_live 实测"
    echo "  - GitHub SSH 专用直连 (DOMAIN,ssh.github.com)，覆盖 443 端口"
    echo "  - 保持出站 22 端口直连策略 (DST-PORT,22)，远端可达性仍需实测"
    echo "  - 中国大陆 IP (GEOIP,CN)"
    echo "  - 中国域名 (.cn, .com.cn)"
    echo "  - 常见中国网站 (B站、知乎、抖音、淘宝等)"
    echo ""
    echo "下一步:"
    echo "  1. 重启或重新启用 Clash Verge 使配置生效"
    echo "  2. 测试 Git: git fetch 或 git push"
    echo ""
    echo "注意:"
    echo "  - 配置对所有订阅有效"
    echo "  - 中国大陆网站已配置直连 (不走代理，节省流量)"
    echo "  - DNS 层: 主域名和通配符都已添加"
    echo "  - 规则层: 直连规则已写入全局脚本"
    echo ""

    # 写文本 ≠ 内核生效。下面的结果反映的是重载前的内核状态，
    # 只有在 Clash Verge 重载配置、重建 TUN 之后重跑才能当作结论。
    verify_tun_routes
}

# 显示菜单
show_menu() {
    echo ""
    echo "=========================================="
    echo "  Clash Verge 优化工具 - 主菜单"
    echo "=========================================="
    echo ""
    echo "  1. 一键优化 Clash 配置 (推荐)"
    echo "  2. 配置 SSH for GitHub (可选)"
    echo "  3. 查看会读取/修改的配置文件"
    echo "  4. 备份管理"
    echo "  0. 退出"
    echo ""
    echo "=========================================="
    echo -n "请选择 [0-4]: "
}

show_config_paths() {
    local current_uid
    local current_profile_path
    local global_merge
    local global_script
    local sub_merge
    local sub_script
    local sub_rules
    local sub_proxies
    local sub_groups

    current_uid=$(get_current_profile_uid)
    current_profile_path=$(get_profile_item_path "$current_uid")
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

# 清空订阅级 Merge（保留备份）
clear_subscription_merge() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local sub_merge_uids
    local uid
    local file
    local path

    sub_merge_uids=$(awk '
        $0 ~ /^- uid: / {type=""; in_option=0}
        $0 ~ /^  type: / {type=$2}
        $0 ~ /^  option:$/ {in_option=1; next}
        in_option && /^  [^[:space:]]/ {in_option=0}
        in_option && /merge:/ {
            if (type=="remote") print $2
        }
    ' "$PROFILES_YAML" | LC_ALL=C sort -u)

    if [ -z "$sub_merge_uids" ]; then
        echo "未找到订阅级 merge 记录"
        return
    fi

    for uid in $sub_merge_uids; do
        if [ "$uid" = "Merge" ]; then
            continue
        fi
        file=$(get_profile_item_file "$uid")
        if [ -z "$file" ]; then
            echo "未找到 merge 文件: $uid"
            continue
        fi
        path="$CLASH_DIR/profiles/$file"
        if [ ! -f "$path" ]; then
            echo "文件不存在: $path"
            continue
        fi
        cp "$path" "$path.backup.$timestamp"
        printf "# 订阅级 Merge 为空\n" > "$path"
        echo "已清空: $path (备份: ${path}.backup.${timestamp})"
    done
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
    cp -f "$dir/$file" "$dir/$orig"
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

usage() {
    cat <<'EOF'
用法:
  ./tun-fix.sh                 打开完整维护菜单（会修改 Merge/DNS/TUN/SSH）
  ./tun-fix.sh rules check     校验 rules/direct.yaml 和 rules/proxy.yaml
  ./tun-fix.sh rules render    将自包含全局 Script.js 输出到 stdout，不读取 Verge 配置
  ./tun-fix.sh rules apply     只生成并替换已登记的全局 Script.js；随后在 Verge 中重载
  ./tun-fix.sh --help          显示本帮助
EOF
}

# 主程序
main() {
    require_profiles
    MERGE_CONFIG=$(get_merge_config)
    PROFILE_NAME=$(get_profile_name)

    echo ""
    echo "当前订阅: $PROFILE_NAME"
    echo "Merge 配置: $(basename "$MERGE_CONFIG")"

    while true; do
        show_menu
        read -r choice

        case $choice in
            1)
                optimize_all "$MERGE_CONFIG"
                ;;
            2)
                configure_ssh
                ;;
            3)
                show_config_paths
                ;;
            4)
                backup_menu
                ;;
            0)
                exit 0
                ;;
            *)
                echo "无效选择"
                ;;
        esac
    done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        rules)
            case "${2:-}" in
                check) check_route_sources ;;
                render) render_route_script ;;
                apply) apply_route_rules ;;
                *) usage; exit 2 ;;
            esac
            ;;
        --help|-h) usage ;;
        "") main ;;
        *) usage; exit 2 ;;
    esac
fi
