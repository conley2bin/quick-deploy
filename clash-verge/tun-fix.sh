#!/bin/bash
# Clash Verge 优化脚本
# 修改全局 Merge 配置，自动应用到所有订阅

set -e

# 配置
CLASH_DIR="$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev"
PROFILES_YAML="$CLASH_DIR/profiles.yaml"

# 获取当前订阅使用的 Merge 配置文件
get_merge_config() {
    local uid=$(grep "^current:" "$PROFILES_YAML" | awk '{print $2}')

    # 从 profiles.yaml 中提取当前订阅关联的 merge 配置 UID
    local merge_uid=$(awk -v uid="$uid" '
        $0 ~ "uid: " uid {found=1}
        found && /merge:/ {print $2; exit}
    ' "$PROFILES_YAML")

    if [ -n "$merge_uid" ]; then
        echo "$CLASH_DIR/profiles/${merge_uid}.yaml"
    else
        # 如果没有关联 merge 配置，使用全局 Merge.yaml
        echo "$CLASH_DIR/profiles/Merge.yaml"
    fi
}

# 获取订阅名称
get_profile_name() {
    local uid=$(grep "^current:" "$PROFILES_YAML" | awk '{print $2}')
    grep -A 3 "uid: $uid" "$PROFILES_YAML" | grep "name:" | sed 's/.*name: *//' | sed 's/^[[:space:]]*//'
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
    # GitHub SSH 支持（主域名 + 通配符）
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

tun:
  enable: true
  stack: system
  auto-route: true
  auto-detect-interface: true
  exclude-routes:
    - 192.168.0.0/16
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 127.0.0.0/8
EOF
        echo "✓ Merge 配置已创建"
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
    # GitHub SSH 支持（主域名 + 通配符）
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
    # GitHub SSH 支持（主域名 + 通配符）
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

    echo "✓ Fake-IP Filter 已更新 (GitHub SSH 支持主域名和通配符)"
}

# 更新或创建 TUN 配置
update_tun_config() {
    local file="$1"

    if grep -q "^tun:" "$file"; then
        echo "✓ TUN 配置已存在"
        return
    fi

    # 添加 TUN 配置
    cat >> "$file" << 'EOF'

tun:
  enable: true
  stack: system
  auto-route: true
  auto-detect-interface: true
  exclude-routes:
    - 192.168.0.0/16
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 127.0.0.0/8
EOF

    echo "✓ TUN 配置已添加"
}

# 更新或创建直连规则
update_direct_rules() {
    local file="$1"

    # 检查是否已存在 GitHub 直连规则
    if grep -q "DOMAIN-SUFFIX,github.com,DIRECT" "$file"; then
        echo "✓ GitHub 直连规则已存在"
        return
    fi

    # 添加直连规则
    cat >> "$file" << 'EOF'

prepend-rules:
  # GitHub SSH 直连（不走代理）
  - DOMAIN-SUFFIX,github.com,DIRECT
  - DOMAIN-SUFFIX,githubusercontent.com,DIRECT
  - DOMAIN-SUFFIX,githubassets.com,DIRECT
  - DOMAIN-SUFFIX,github.io,DIRECT
  # 本地网络直连
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
EOF

    echo "✓ GitHub 直连规则已添加"
}

# 配置 SSH (可选但推荐)
configure_ssh() {
    local ssh_config="$HOME/.ssh/config"
    local timestamp=$(date +%Y%m%d_%H%M%S)

    echo ""
    echo "==========================================="
    echo "  配置 SSH for GitHub (可选但推荐)"
    echo "==========================================="
    echo ""
    echo "此配置将优化 GitHub SSH 连接的稳定性："
    echo "  1. 使用端口 443 (HTTPS端口) 替代默认的 22"
    echo "     - 更稳定，避免防火墙限制"
    echo "     - GitHub 官方推荐方案"
    echo "  2. 禁用连接复用 (ControlMaster)"
    echo "     - 避免间歇性连接失败"
    echo "     - 解决 GitHub 2% SSH 失败率问题"
    echo ""

    # 检查是否已配置
    if [ -f "$ssh_config" ] && grep -q "^Host github.com" "$ssh_config"; then
        echo "⚠️  检测到 ~/.ssh/config 中已存在 GitHub 配置"
        echo ""
        grep -A 10 "^Host github.com" "$ssh_config"
        echo ""
        echo -n "是否覆盖现有配置？[y/N]: "
        read overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            echo "已取消 SSH 配置"
            return
        fi

        # 备份并移除旧配置
        cp "$ssh_config" "$ssh_config.backup.$timestamp"
        echo "✓ 已备份原配置到: $ssh_config.backup.$timestamp"

        # 移除旧的 GitHub 配置（包括注释）
        # 删除从 GitHub 注释开始到 Host github.com 配置块结束的所有内容
        sed -i '/# GitHub SSH over HTTPS port/,/ControlPersist no/d' "$ssh_config"
        # 清理可能残留的空行
        sed -i '/^$/N;/^\n$/d' "$ssh_config"
    fi

    echo ""
    echo "正在添加 SSH 配置..."

    # 确保 .ssh 目录存在
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"

    # 备份现有配置（如果存在且未备份）
    if [ -f "$ssh_config" ] && [ ! -f "$ssh_config.backup.$timestamp" ]; then
        cp "$ssh_config" "$ssh_config.backup.$timestamp"
        echo "✓ 已备份原配置到: $ssh_config.backup.$timestamp"
    fi

    # 添加配置
    cat >> "$ssh_config" << 'EOF'

# GitHub SSH over HTTPS port (443) - 最可靠的解决方案
# 原因：
# 1. 端口 443 比端口 22 更稳定（GitHub 官方推荐用于防火墙受限环境）
# 2. 禁用 ControlMaster 避免连接复用导致的间歇性失败
Host github.com
    Hostname ssh.github.com
    Port 443
    User git
    # 禁用连接复用避免间歇性问题
    ControlMaster no
    ControlPath none
    ControlPersist no
EOF

    # 设置权限
    chmod 600 "$ssh_config"

    echo ""
    echo "✓ SSH 配置已添加到 ~/.ssh/config"
    echo ""
    echo "测试连接:"
    echo "  ssh -T git@github.com"
    echo ""
    echo "预期输出:"
    echo "  Hi <your-username>! You've successfully authenticated, but GitHub does not provide shell access."
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
    echo "作用范围: 当前订阅 ($(get_profile_name))"
    echo ""

    update_fake_ip_filter "$file"
    update_tun_config "$file"
    update_direct_rules "$file"

    echo ""
    echo "=========================================="
    echo "  配置完成！"
    echo "=========================================="
    echo ""
    echo "✓ Fake-IP Filter: 已配置 (主域名 + 通配符)"
    echo "  - 本地网络 (*.local, *.lan)"
    echo "  - 企业应用 (飞书、钉钉、字节跳动)"
    echo "  - GitHub (github.com, *.github.com, 等)"
    echo ""
    echo "✓ TUN 模式: 已配置 (排除本地网络)"
    echo ""
    echo "✓ 直连规则: 已配置 (GitHub 不走代理)"
    echo ""
    echo "下一步:"
    echo "  1. 重启 Clash Verge 使配置生效"
    echo "  2. [推荐] 配置 SSH (选择菜单选项 2)"
    echo "     - 使用端口 443 提高稳定性"
    echo "     - 避免间歇性连接失败"
    echo "  3. 测试 SSH: ssh -T git@github.com"
    echo "  4. 测试 Git: git fetch 或 git push"
    echo ""
    echo "注意:"
    echo "  - 配置针对当前订阅有效"
    echo "  - GitHub SSH 已配置直连 (不走代理)"
    echo "  - DNS 层: 主域名和通配符都已添加"
    echo "  - 路由层: GitHub 流量直连规则已添加"
    echo "  - 切换订阅需要重新运行脚本"
    echo ""
}

# 显示菜单
show_menu() {
    echo ""
    echo "=========================================="
    echo "  Clash Verge 优化工具 - 主菜单"
    echo "=========================================="
    echo ""
    echo "  1. 一键优化 Clash 配置 (推荐)"
    echo "  2. 配置 SSH for GitHub (可选但推荐)"
    echo "  3. 查看 Merge 配置文件路径"
    echo "  0. 退出"
    echo ""
    echo "=========================================="
    echo -n "请选择 [0-3]: "
}

# 主程序
main() {
    MERGE_CONFIG=$(get_merge_config)
    PROFILE_NAME=$(get_profile_name)

    echo ""
    echo "当前订阅: $PROFILE_NAME"
    echo "Merge 配置: $(basename $MERGE_CONFIG)"

    while true; do
        show_menu
        read choice

        case $choice in
            1)
                optimize_all "$MERGE_CONFIG"
                ;;
            2)
                configure_ssh
                ;;
            3)
                echo ""
                echo "=========================================="
                echo "Merge 配置文件"
                echo "=========================================="
                echo ""
                echo "文件路径:"
                echo "  $MERGE_CONFIG"
                echo ""
                echo "核心作用:"
                echo "  这是当前订阅的 Merge 增强配置"
                echo "  与订阅配置合并后生效"
                echo ""
                echo "包含内容:"
                echo "  - dns.fake-ip-filter: DNS 过滤规则"
                echo "  - tun: TUN 模式配置"
                echo "  - prepend-rules: 分流规则（GitHub 直连）"
                echo ""
                echo "脚本修改内容:"
                echo "  ✓ DNS 层: 添加 fake-ip-filter (包含主域名和通配符)"
                echo "  ✓ 路由层: 添加 TUN + exclude-routes"
                echo "  ✓ 规则层: 添加 GitHub 直连规则（不走代理）"
                echo ""
                echo "优势:"
                echo "  - 订阅更新不会影响此配置"
                echo "  - 配置持久化，无需重复设置"
                echo ""
                echo "注意:"
                echo "  - 此配置仅对当前订阅有效"
                echo "  - 切换到其他订阅需要重新运行脚本"
                echo ""
                echo "=========================================="
                echo ""
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
