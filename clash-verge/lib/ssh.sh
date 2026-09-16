#!/bin/bash
# GitHub SSH configuration helpers for tun-fix.sh.

remove_managed_ssh_block() {
    local ssh_config="$1"
    local remove_marked=0 remove_legacy=0 output
    [ -f "$ssh_config" ] || return 0

    if grep -q '^# >>> tun-fix\.sh github ssh >>>$' "$ssh_config"; then
        if grep -q '^# <<< tun-fix\.sh github ssh <<<$' "$ssh_config"; then
            remove_marked=1
        else
            echo "warning 发现未配对的 tun-fix SSH 块起始标记，为避免误删未做清理"
        fi
    fi
    if grep -q '^# GitHub SSH over HTTPS port' "$ssh_config"; then
        if grep -qE '^[[:space:]]*ControlPersist no[[:space:]]*$' "$ssh_config"; then
            remove_legacy=1
        else
            echo "warning 旧格式 SSH 块缺少结束标记，为避免误删未做清理"
        fi
    fi

    output=$(mktemp "${ssh_config}.tmp.XXXXXX") || return 1
    if ! awk -v marked="$remove_marked" -v legacy="$remove_legacy" '
        marked && /^# >>> tun-fix\.sh github ssh >>>$/ {skip_marked=1; next}
        skip_marked && /^# <<< tun-fix\.sh github ssh <<<$/{skip_marked=0; next}
        skip_marked {next}
        legacy && /^# GitHub SSH over HTTPS port/ {skip_legacy=1; next}
        skip_legacy && /^[[:space:]]*ControlPersist no[[:space:]]*$/ {skip_legacy=0; next}
        skip_legacy {next}
        /^[[:space:]]*$/ {pending=1; next}
        {if (pending && started) print ""; pending=0; started=1; print}
    ' "$ssh_config" > "$output"; then
        rm -f -- "$output"
        return 1
    fi
    commit_same_dir_temp "$output" "$ssh_config"
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
    local has_github=0 backup_file="" tmp shadowed

    echo ""
    echo "==========================================="
    echo "  配置 GitHub SSH（ssh.github.com:443）"
    echo "==========================================="
    echo "此项设置 IPQoS none，不设置跳板；Clash 出站仍由路由规则决定。"
    echo "DST-PORT,22 不覆盖 443，写入 SSH 配置也不证明 DIRECT 已命中。"
    echo ""

    if [ -f "$ssh_config" ] && grep -qE '^# >>> tun-fix\.sh github ssh >>>|^[[:space:]]*Host[[:space:]].*github\.com' "$ssh_config"; then
        has_github=1
        echo "warning 检测到已有 GitHub SSH 配置"
        grep -A 10 '^Host github.com' "$ssh_config" || true
        echo -n "是否覆盖本工具管理的块并将新块置顶？[y/N]: "
        local overwrite
        read -r overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            echo "已取消 SSH 配置"
            return
        fi
    fi

    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    if [ -f "$ssh_config" ]; then
        backup_file=$(unique_backup "$ssh_config") || return 1
        echo "pass 已备份原配置到: $backup_file"
    fi
    if [ "$has_github" -eq 1 ]; then
        remove_managed_ssh_block "$ssh_config"
    fi

    tmp=$(mktemp "${ssh_config}.tmp.XXXXXX") || return 1
    {
        github_ssh_block
        if [ -s "$ssh_config" ]; then
            echo ""
            cat "$ssh_config"
        fi
    } > "$tmp"
    chmod 600 "$tmp"
    commit_same_dir_temp "$tmp" "$ssh_config"
    chmod 600 "$ssh_config"
    echo "pass SSH 配置已原子写入 ~/.ssh/config 顶部"

    shadowed=$(awk '
        /^# >>> tun-fix\.sh github ssh >>>$/ {inblk=1}
        /^# <<< tun-fix\.sh github ssh <<<$/{inblk=0; next}
        !inblk && /^[[:space:]]*Host[[:space:]].*github\.com/ {print "       " NR ": " $0}
    ' "$ssh_config")
    if [ -n "$shadowed" ]; then
        echo "note 仍保留本工具块之外的 github.com 配置（现在位于其后）："
        printf '%s\n' "$shadowed"
    fi

    echo ""
    if verify_github_ssh_config; then
        echo "测试具体仓库权限与传输: git ls-remote origin"
    fi
    echo ""
}
