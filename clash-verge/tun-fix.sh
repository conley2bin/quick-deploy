#!/bin/bash
# Clash Verge 优化脚本
# 修改全局 Merge 配置，自动应用到所有订阅

set -e

# 配置
CLASH_DIR="$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev"
PROFILES_YAML="$CLASH_DIR/profiles.yaml"

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

    echo "pass Fake-IP Filter 已更新 (GitHub SSH 支持主域名和通配符)"
}

# 更新或创建 TUN 配置
update_tun_config() {
    local file="$1"

    if grep -q "^tun:" "$file"; then
        echo "pass TUN 配置已存在"
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

    echo "pass TUN 配置已添加"
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
    "GEOIP,CN,DIRECT,no-resolve",
    "DOMAIN-SUFFIX,cn,DIRECT",
    "DOMAIN-SUFFIX,com.cn,DIRECT",
    "DOMAIN-SUFFIX,gov.cn,DIRECT",
    "DOMAIN-SUFFIX,edu.cn,DIRECT",
    "DOMAIN-SUFFIX,bilibili.com,DIRECT",
    "DOMAIN-SUFFIX,zhihu.com,DIRECT",
    "DOMAIN-SUFFIX,huya.com,DIRECT",
    "DOMAIN-SUFFIX,douyin.com,DIRECT",
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
    "DOMAIN-SUFFIX,cn.bing.com,DIRECT",
    "DOMAIN-SUFFIX,bing.com,DIRECT",
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
        echo "warning  检测到 ~/.ssh/config 中已存在 GitHub 配置"
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
        echo "pass 已备份原配置到: $ssh_config.backup.$timestamp"

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
        echo "pass 已备份原配置到: $ssh_config.backup.$timestamp"
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
    echo "pass SSH 配置已添加到 ~/.ssh/config"
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
    echo "pass TUN 模式: 已配置 (排除本地网络)"
    echo ""
    echo "pass 直连规则: 已写入全局脚本"
    echo "  - 中国大陆 IP (GEOIP,CN)"
    echo "  - 中国域名 (.cn, .com.cn)"
    echo "  - 常见中国网站 (B站、知乎、抖音、淘宝等)"
    echo ""
    echo "下一步:"
    echo "  1. 重启或重新启用 Clash Verge 使配置生效"
    echo "  2. [推荐] 配置 SSH (选择菜单选项 2)"
    echo "     - 使用端口 443 提高稳定性"
    echo "     - 避免间歇性连接失败"
    echo "  3. 测试 SSH: ssh -T git@github.com"
    echo "  4. 测试 Git: git fetch 或 git push"
    echo ""
    echo "注意:"
    echo "  - 配置对所有订阅有效"
    echo "  - 中国大陆网站已配置直连 (不走代理，节省流量)"
    echo "  - DNS 层: 主域名和通配符都已添加"
    echo "  - 规则层: 直连规则已写入全局脚本"
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
    echo "  4. 备份管理"
    echo "  0. 退出"
    echo ""
    echo "=========================================="
    echo -n "请选择 [0-4]: "
}

# 清空订阅级 Merge（保留备份）
clear_subscription_merge() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    local merge_list
    local sub_merge_uids
    local uid
    local file
    local path

    merge_list=$(awk '
        $0 ~ /^- uid: / {uid=$3; type=""; file=""}
        $0 ~ /^  type: / {type=$2}
        $0 ~ /^  file: / {file=$2; if (type=="merge" && file!="") print uid, file}
    ' "$PROFILES_YAML")

    declare -A merge_files
    while IFS= read -r uid file; do
        [ -z "$uid" ] && continue
        merge_files["$uid"]="$file"
    done <<< "$merge_list"

    sub_merge_uids=$(awk '
        $0 ~ /^- uid: / {type=""; in_option=0}
        $0 ~ /^  type: / {type=$2}
        $0 ~ /^  option:$/ {in_option=1; next}
        in_option && /^  [^[:space:]]/ {in_option=0}
        in_option && /merge:/ {
            if (type=="remote") print $2
        }
    ' "$PROFILES_YAML" | sort -u)

    if [ -z "$sub_merge_uids" ]; then
        echo "未找到订阅级 merge 记录"
        return
    fi

    for uid in $sub_merge_uids; do
        if [ "$uid" = "Merge" ]; then
            continue
        fi
        file="${merge_files[$uid]}"
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
        | sort \
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
    read idx

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
    read selection

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
    read confirm
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
        read choice

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
                echo "  这是全局 Merge 增强配置"
                echo "  与订阅配置合并后生效"
                echo ""
                echo "包含内容:"
                echo "  - dns.fake-ip-filter: DNS 过滤规则"
                echo "  - tun: TUN 模式配置"
                echo ""
                echo "脚本修改内容:"
                echo "  pass DNS 层: 添加 fake-ip-filter (包含主域名和通配符)"
                echo "  pass 路由层: 添加 TUN + exclude-routes"
                echo "  pass 规则层: 写入全局脚本插入直连规则"
                echo ""
                echo "优势:"
                echo "  - 订阅更新不会影响此配置"
                echo "  - 配置持久化，无需重复设置"
                echo ""
                echo "注意:"
                echo "  - 此配置对所有订阅有效"
                echo ""
                echo "=========================================="
                echo ""
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