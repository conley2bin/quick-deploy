#!/bin/bash
# Clash Verge 优化脚本
# 修改全局 Merge 配置，自动应用到所有订阅

set -e

# 配置
CLASH_DIR="$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev"
PROFILES_YAML="$CLASH_DIR/profiles.yaml"

# 启动守卫: 本脚本所有配置解析都以 profiles.yaml 为唯一入口。缺失时 awk/grep 退出码
# 非 0，在 set -e 下会让脚本从函数中途静默终止，用户看不到任何原因。此处显式拦截。
[ -f "$PROFILES_YAML" ] || { echo "未找到 $PROFILES_YAML，请先启动 Clash Verge 生成配置" >&2; exit 1; }

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

# 获取全局 Merge 配置文件
get_merge_config() {
    local merge_file=$(awk '
        $0 ~ /^- uid: Merge$/ {found=1; next}
        found && /file:/ {print $2; exit}
    ' "$PROFILES_YAML")

    if [ -n "$merge_file" ]; then
        echo "$CLASH_DIR/profiles/$merge_file"
    else
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
        echo "$CLASH_DIR/profiles/Script.js"
    fi
}

# 获取订阅名称
get_profile_name() {
    local uid=$(grep "^current:" "$PROFILES_YAML" | awk '{print $2}')
    grep -A 3 "uid: $uid" "$PROFILES_YAML" | grep "name:" | sed 's/.*name: *//' | sed 's/^[[:space:]]*//'
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

# 更新或创建 Merge 配置中的 Fake-IP Filter
update_fake_ip_filter() {
    local file="$1"

    # 检查文件是否存在
    if [ ! -f "$file" ]; then
        echo "创建新的 Merge 配置文件: $file"
        cat > "$file" << 'EOF'
# Clash Verge Merge 配置
# 此文件会与订阅配置合并，提供全局增强

dns:
  fake-ip-filter:
    # 本地网络
    - '*.local'
    - '*.lan'
    # 企业应用（主域名 + 通配符）
    - 'feishu.cn'
    - '*.feishu.cn'
    - 'larkoffice.com'
    - '*.larkoffice.com'
    - 'bytedance.com'
    - '*.bytedance.com'
    - 'dingtalk.com'
    - '*.dingtalk.com'
    # GitHub 支持（主域名 + 通配符）
    - 'github.com'
    - '*.github.com'
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
EOF
        # TUN 块只有一处定义，见 tun_block()
        echo "" >> "$file"
        tun_block >> "$file"
        echo "pass Merge 配置已创建"
        return
    fi

    # 文件存在，更新 fake-ip-filter
    if grep -q "fake-ip-filter:" "$file"; then
        # 删除旧的 fake-ip-filter 配置
        sed -i '/fake-ip-filter:/,/^[[:space:]]*[a-z-].*:/{ /^[[:space:]]*[a-z-].*:/!d; }; /fake-ip-filter:/d' "$file" 2>/dev/null || true
    fi

    # 查找 dns: 区块的位置
    if grep -q "^dns:" "$file"; then
        # 在 dns: 后面插入 fake-ip-filter（使用临时文件方式，避免 sed 命令过长）
        local filter_content=$(cat << 'FILTER_EOF'
  fake-ip-filter:
    # 本地网络
    - '*.local'
    - '*.lan'
    # 企业应用（主域名 + 通配符）
    - 'feishu.cn'
    - '*.feishu.cn'
    - 'larkoffice.com'
    - '*.larkoffice.com'
    - 'bytedance.com'
    - '*.bytedance.com'
    - 'dingtalk.com'
    - '*.dingtalk.com'
    # GitHub 支持（主域名 + 通配符）
    - 'github.com'
    - '*.github.com'
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
FILTER_EOF
)
        # 使用 awk 插入配置
        awk -v filter="$filter_content" '/^dns:/ {print; print filter; next} 1' "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
    else
        # 文件中没有 dns: 区块，添加整个区块
        cat >> "$file" << 'EOF'

dns:
  fake-ip-filter:
    # 本地网络
    - '*.local'
    - '*.lan'
    # 企业应用（主域名 + 通配符）
    - 'feishu.cn'
    - '*.feishu.cn'
    - 'larkoffice.com'
    - '*.larkoffice.com'
    - 'bytedance.com'
    - '*.bytedance.com'
    - 'dingtalk.com'
    - '*.dingtalk.com'
    # GitHub 支持（主域名 + 通配符）
    - 'github.com'
    - '*.github.com'
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
EOF
    fi

    echo "pass Fake-IP Filter 已更新 (GitHub 主域名和通配符)"
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

    cp "$file" "$file.backup.$(date +%Y%m%d_%H%M%S)"

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

# 验证排除网段是否真的落到了内核路由
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

    local rules routes p
    local hit=0
    rules=$(ip rule show 2>/dev/null || true)
    routes=$(ip route show table 2022 2>/dev/null || true)

    echo "--- ip rule show ---"
    echo "${rules:-(空)}"
    echo ""
    echo "--- ip route show table 2022 ---"
    echo "${routes:-(空)}"
    echo ""

    for p in "${TUN_EXCLUDED_PREFIXES[@]}"; do
        if printf '%s\n%s\n' "$rules" "$routes" | grep -qF "$p"; then
            echo "pass 内核中已出现: $p"
            hit=$((hit+1))
        else
            echo "fail 内核中未出现: $p"
        fi
    done

    echo ""
    if [ "$hit" -eq "${#TUN_EXCLUDED_PREFIXES[@]}" ]; then
        echo "全部排除前缀已落到内核路由，配置真实生效"
    else
        echo "已生效 $hit/${#TUN_EXCLUDED_PREFIXES[@]}。本结果只在 Clash Verge 重载配置、重建 TUN 之后才有意义。"
        echo "重载后仍为 0，说明该键没被采纳；不要再把\"配置无解析错误\"当作生效证据。"
    fi
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

# 更新或创建直连规则
update_direct_rules() {
    local script_file
    local timestamp

    script_file=$(get_script_config)
    timestamp=$(date +%Y%m%d_%H%M%S)

    mkdir -p "$CLASH_DIR/profiles"

    local backed_up=0
    if [ -f "$script_file" ]; then
        cp "$script_file" "$script_file.backup.$timestamp"
        backed_up=1
    fi

    cat > "$script_file" << 'EOF'
// Generated by tun-fix.sh

function main(config) {
  const prependRules = [
    // 出站 TCP/22 必须直连。
    // 实测（mihomo /proxies/<node>/delay 打 http://portquiz.net:22/，443 作存活对照）：
    // 该订阅 84 个节点中 81 个 443 通、22 全部不通，另 3 个节点连 443 对照都不通。
    // 0/84 放行出站 22 —— 这是机场服务级策略，不是某个节点的问题，换节点无用。
    // 症状是 connect 成功后 0 字节 EOF，看起来像对端拒绝，实际是出口丢弃。
    // 因此把 22 端口交给代理是确定性失败；直连至少可用（本地实测 github /
    // gitlab / salsa.debian.org / sourceware.org 的 :22 直连均返回 SSH banner）。
    // GitHub 若已由 ~/.ssh/config 改走 ssh.github.com:443，则不受此规则影响，
    // 仍走代理 —— 那是更稳的路径，两者互补而非互斥。
    "DST-PORT,22,DIRECT",
    "GEOIP,CN,DIRECT,no-resolve",
    "DOMAIN-SUFFIX,cn,DIRECT",
    "DOMAIN-SUFFIX,com.cn,DIRECT",
    "DOMAIN-SUFFIX,gov.cn,DIRECT",
    "DOMAIN-SUFFIX,edu.cn,DIRECT",
    "DOMAIN-KEYWORD,bili,DIRECT",
    "DOMAIN-KEYWORD,zhihu,DIRECT",
    "DOMAIN-KEYWORD,huya,DIRECT",
    "DOMAIN-KEYWORD,douyin,DIRECT",
    "DOMAIN-KEYWORD,bing,DIRECT",
    "DOMAIN-SUFFIX,taobao.com,DIRECT",
    "DOMAIN-SUFFIX,tmall.com,DIRECT",
    "DOMAIN-SUFFIX,jd.com,DIRECT",
    "DOMAIN-SUFFIX,qq.com,DIRECT",
    "DOMAIN-SUFFIX,weixin.qq.com,DIRECT",
    "DOMAIN-SUFFIX,baidu.com,DIRECT",
    "DOMAIN-SUFFIX,so.com,DIRECT",
    "DOMAIN-SUFFIX,sogou.com,DIRECT",
    "DOMAIN-SUFFIX,360.cn,DIRECT",
    "DOMAIN-SUFFIX,hao123.com,DIRECT",
    "DOMAIN-SUFFIX,2345.com,DIRECT",
    "DOMAIN-SUFFIX,163.com,DIRECT",
    "DOMAIN-SUFFIX,sina.com.cn,DIRECT",
    "DOMAIN-SUFFIX,weibo.com,DIRECT",
    "DOMAIN-SUFFIX,ifeng.com,DIRECT",
    "DOMAIN-SUFFIX,eastday.com,DIRECT",
    "DOMAIN-SUFFIX,sohu.com,DIRECT",
    "DOMAIN-SUFFIX,youku.com,DIRECT",
    "DOMAIN-SUFFIX,iqiyi.com,DIRECT",
    "DOMAIN-SUFFIX,mgtv.com,DIRECT",
    "DOMAIN-SUFFIX,aliyun.com,DIRECT",
    "DOMAIN-SUFFIX,alipay.com,DIRECT",
    "DOMAIN-SUFFIX,tencent.com,DIRECT",
    "DOMAIN-SUFFIX,bytedance.com,DIRECT",
    "DOMAIN,cc.yiwen.lu,DIRECT",
    "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
    "DOMAIN-SUFFIX,local,DIRECT"
  ];

  if (!Array.isArray(config.rules)) {
    config.rules = [];
  }

  const existing = new Set(config.rules);
  const toAdd = prependRules.filter(r => !existing.has(r));
  if (toAdd.length > 0) {
    config.rules = [...toAdd, ...config.rules];
  }

  return config;
}
EOF

    echo "直连规则已写入全局脚本: $script_file"
    if [ "$backed_up" -eq 1 ]; then
        echo "脚本备份: $script_file.backup.$timestamp"
    fi
}

# 删除本脚本之前生成的 GitHub SSH 块（新标记块 + 旧版本的无标记块）
remove_managed_ssh_block() {
    local ssh_config="$1"

    [ -f "$ssh_config" ] || return 0

    # 当前格式：成对标记之间全删
    sed -i '/^# >>> tun-fix\.sh github ssh >>>$/,/^# <<< tun-fix\.sh github ssh <<<$/d' "$ssh_config"
    # 旧格式：旧版本写的块以注释头开始、以 ControlPersist no 结尾
    sed -i '/^# GitHub SSH over HTTPS port/,/^[[:space:]]*ControlPersist no[[:space:]]*$/d' "$ssh_config"
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
    echo "此配置让 GitHub SSH 走 ssh.github.com:443。"
    echo "实测原因：流量已正确路由到代理，是机场封禁出站 TCP/22（订阅内 84 个节点"
    echo "逐一探测，0 个放行 22；换节点无用）。443 不受影响，已验证可用。"
    echo ""
    echo "与选项 1 的分工：选项 1 写入的 DST-PORT,22,DIRECT 让所有 22 端口直连，"
    echo "覆盖你自己的境外主机；本选项让 GitHub 改用 443，继续走代理，路径更稳。"
    echo "两者不冲突：GitHub 从此不再使用 22，DIRECT 规则自然不会命中它。"
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

    # 添加配置
    cat >> "$ssh_config" << 'EOF'

# >>> tun-fix.sh github ssh >>>
# GitHub SSH 走 443 端口。
# 实测机制：流量已正确走代理，是机场封禁出站 TCP/22——不是某个节点的问题。
# 逐节点探测（portquiz.net:22 走 /proxies/<node>/delay，443 作存活对照）：
# 84 个节点里 0 个放行 22，81 个 443 正常。换节点无解。
# 症状：connect 成功后一个 RTT 内返回 0 字节即断开（github/gitlab/bitbucket/
# kernel.org 一致），同节点 :443 与 :9418 正常。443 端口不受影响。
Host github.com ssh.github.com
    Hostname ssh.github.com
    Port 443
    User git

# 机场对所有境外 :22 都封，如需 GitLab / Bitbucket 取消下方注释即可
#Host gitlab.com altssh.gitlab.com
#    Hostname altssh.gitlab.com
#    Port 443
#    User git
#
#Host bitbucket.org altssh.bitbucket.org
#    Hostname altssh.bitbucket.org
#    Port 443
#    User git
# <<< tun-fix.sh github ssh <<<
EOF

    chmod 600 "$ssh_config"

    echo ""
    echo "pass SSH 配置已添加到 ~/.ssh/config"
    echo ""
    echo "测试连接:"
    echo "  ssh -T git@github.com"
    echo ""
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

    clear_subscription_merge
    remove_prepend_rules "$file"
    update_fake_ip_filter "$file"
    update_tun_config "$file"
    update_direct_rules "$file"

    echo ""
    echo "=========================================="
    echo "  配置完成！"
    echo "=========================================="
    echo ""
    echo "pass Fake-IP Filter: 已配置 (主域名 + 通配符)"
    echo "  - 本地网络 (*.local, *.lan)"
    echo "  - 企业应用 (飞书、钉钉、字节跳动)"
    echo "  - GitHub (github.com, *.github.com, 等)"
    echo ""
    echo "TUN 模式: 已写入 tun: 块（route-exclude-address 排除 192.168.0.0/16、10.0.0.0/8、172.16.0.0/12、127.0.0.0/8）"
    echo "  尚未验证生效：需重载 Clash Verge 后，由 verify_tun_routes 看内核路由确认"
    echo ""
    echo "pass 直连规则: 已写入全局脚本"
    echo "  - 出站 TCP/22 直连 (DST-PORT,22) —— 机场 84 个节点全部封禁出站 22，"
    echo "    走代理必然是 connect 后 0 字节断开；直连是唯一可用路径"
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
    echo "  - 全局 Script: 直连规则 prepend 到生成配置顶部"
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

# 主程序
main() {
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

main
