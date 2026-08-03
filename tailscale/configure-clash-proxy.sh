#!/bin/bash
# Configure tailscaled to use a local HTTP proxy only when active Clash Verge TUN
# makes a direct control-plane connection unsuitable.
set -euo pipefail

MARKER='# Managed by quick-deploy/tailscale/configure-clash-proxy.sh'
DROPIN_DIR="${TAILSCALE_DROPIN_DIR:-/etc/systemd/system/tailscaled.service.d}"
DROPIN_FILE="$DROPIN_DIR/quick-deploy-clash-proxy.conf"
CLASH_CONFIG="${CLASH_CONFIG:-$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml}"
SERVICE='tailscaled.service'
TEMP_FILES=()

cleanup() {
    local file
    for file in "${TEMP_FILES[@]}"; do
        rm -f "$file"
    done
}
trap cleanup EXIT

new_temp() {
    local target="$1" file
    file="$(mktemp)" || die '无法创建临时文件'
    TEMP_FILES+=("$file")
    printf -v "$target" '%s' "$file"
}

usage() {
    cat <<'USAGE'
用法:
  ./configure-clash-proxy.sh --auto
  ./configure-clash-proxy.sh --apply [--proxy http://HOST:PORT]
  ./configure-clash-proxy.sh --status
  ./configure-clash-proxy.sh --remove

--auto      检测活动 Clash Verge TUN；需要时在交互终端询问是否应用
--apply     应用本脚本带 marker 的 systemd drop-in
--proxy     显式 HTTP proxy，格式只能是 http://HOST:PORT
--status    只读显示 Clash 检测与服务代理是否已设置（代理地址会隐藏）
--remove    只删除本脚本管理的 drop-in，不触碰其他 drop-in
USAGE
}

die() {
    echo "错误: $*" >&2
    exit 1
}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

port_is_valid() {
    [[ "$1" =~ ^[0-9]+$ ]] || return 1
    [ "$1" -ge 1 ] 2>/dev/null && [ "$1" -le 65535 ] 2>/dev/null
}

# Deliberately accept only an uncredentialed, pathless HTTP proxy. This keeps
# values safe for a systemd Environment= line and prevents URL surprises.
valid_proxy_url() {
    local url="$1" port
    case "$url" in
        *[[:space:]]*|*\"*|*\'*|*\\*) return 1 ;;
    esac
    [[ "$url" =~ ^http://(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?):([0-9]+)$ ]] || return 1
    port="${BASH_REMATCH[3]}"
    port_is_valid "$port"
}

read_mixed_port() {
    [ -r "$CLASH_CONFIG" ] || return 1
    awk '
        /^mixed-port:[[:space:]]*/ {
            value=$0
            sub(/^[^:]*:[[:space:]]*/, "", value)
            sub(/[[:space:]]*(#.*)?$/, "", value)
            gsub(/^['\''"]|['\''"]$/, "", value)
            print value
            exit
        }
    ' "$CLASH_CONFIG"
}

clash_tun_enabled() {
    [ -r "$CLASH_CONFIG" ] || return 1
    awk '
        /^tun:[[:space:]]*$/ { in_tun=1; next }
        in_tun && /^[^[:space:]]/ { exit }
        in_tun && /^[[:space:]]+enable:[[:space:]]*(true|True|TRUE)[[:space:]]*$/ { found=1 }
        END { exit(found ? 0 : 1) }
    ' "$CLASH_CONFIG"
}

clash_tun_is_active() {
    clash_tun_enabled || return 1
    command -v pgrep >/dev/null 2>&1 || return 1
    command -v ip >/dev/null 2>&1 || return 1
    pgrep -f '[v]erge-mihomo|[c]lash-verge' >/dev/null 2>&1 || return 1
    ip route show table 2022 2>/dev/null | grep -q .
}

detected_clash_proxy() {
    local port
    port="$(read_mixed_port)" || return 1
    port_is_valid "$port" || return 2
    printf 'http://127.0.0.1:%s\n' "$port"
}

effective_environment() {
    systemctl show "$SERVICE" -p Environment --value
}

effective_proxy_url() {
    local environment
    environment="$(effective_environment)" || return 1
    tr ' ' '\n' <<<"$environment" | sed -n 's/^HTTP_PROXY=//p'
}

effective_proxy_matches() {
    local proxy="$1" environment
    environment="$(effective_environment)" || return 1
    grep -Fqx "HTTP_PROXY=$proxy" <<<"${environment// /$'\n'}" \
        && grep -Fqx "HTTPS_PROXY=$proxy" <<<"${environment// /$'\n'}" \
        && grep -Fqx 'NO_PROXY=localhost,127.0.0.1,::1' <<<"${environment// /$'\n'}"
}

probe_proxy() {
    local proxy="$1"
    echo '通过候选 HTTP proxy 以 CONNECT 探测 Tailscale 控制面...'
    curl --proxy "$proxy" --connect-timeout 5 --max-time 15 -fLsS -o /dev/null \
        'https://controlplane.tailscale.com/key?v=138'
}

probe_external_proxy() {
    # An externally supplied Environment value can contain credentials. Probe it
    # without relaying curl diagnostics, which may echo the URL back to stdout.
    curl --proxy "$1" --connect-timeout 5 --max-time 15 -fLsS -o /dev/null \
        'https://controlplane.tailscale.com/key?v=138' >/dev/null 2>&1
}

managed_dropin_state() {
    if [ ! -e "$DROPIN_FILE" ]; then
        printf 'absent\n'
    elif grep -Fqx "$MARKER" "$DROPIN_FILE" 2>/dev/null; then
        printf 'managed\n'
    else
        printf 'foreign\n'
    fi
}

require_active_service() {
    if ! systemctl is-active --quiet "$SERVICE"; then
        die "$SERVICE 重启后未处于 active 状态"
    fi
}

show_status() {
    local port='未找到' proxy='无有效 mixed-port' active='no' state effective='未设置'
    clash_tun_is_active && active='yes'
    port="$(read_mixed_port 2>/dev/null || printf '未找到')"
    proxy="$(detected_clash_proxy 2>/dev/null || printf '无有效 mixed-port')"
    state="$(managed_dropin_state)"
    [ -n "$(effective_proxy_url 2>/dev/null || true)" ] && effective='已设置（地址已隐藏）'

    echo "Clash 最终配置: $CLASH_CONFIG"
    echo "Clash TUN 活动: $active"
    echo "mixed-port: $port"
    echo "检测到的 HTTP proxy: $proxy"
    echo "受管 drop-in: $state ($DROPIN_FILE)"
    echo "tailscaled 当前 HTTP_PROXY: $effective"
}

apply_proxy() {
    local proxy="$1" tmp state
    valid_proxy_url "$proxy" || die '--proxy 只能是无认证、无路径的 http://HOST:PORT，端口必须为 1..65535'
    state="$(managed_dropin_state)"

    # A managed file means a prior operation may have stopped after daemon-reload
    # updated the manager configuration but before the process consumed it. Never
    # treat that manager-side match as completion: explicit apply must converge.
    if [ "$state" != managed ] && effective_proxy_matches "$proxy"; then
        require_active_service
        echo "tailscaled 已由外部配置有效使用 $proxy；不接管、不重启。"
        return 0
    fi
    [ "$state" != foreign ] || die "拒绝覆盖不含本脚本 marker 的 $DROPIN_FILE"

    if ! probe_proxy "$proxy"; then
        die '候选 proxy 无法通过 CONNECT 访问 Tailscale 控制面；未修改 tailscaled'
    fi

    new_temp tmp
    cat > "$tmp" <<EOF_DROPIN
$MARKER
[Service]
Environment="HTTP_PROXY=$proxy"
Environment="HTTPS_PROXY=$proxy"
Environment="NO_PROXY=localhost,127.0.0.1,::1"
EOF_DROPIN

    run_root mkdir -p "$DROPIN_DIR"
    run_root install -m 0644 "$tmp" "$DROPIN_FILE"
    run_root systemctl daemon-reload
    run_root systemctl restart "$SERVICE"
    require_active_service
    if ! effective_proxy_matches "$proxy"; then
        die '服务已重启，但 tailscaled 的有效代理环境不符合预期；请运行 --status 检查'
    fi
    echo "已应用并验证 tailscaled 代理: $proxy"
}

remove_proxy() {
    local state effective
    state="$(managed_dropin_state)"
    [ "$state" != foreign ] || die "拒绝删除不含本脚本 marker 的 $DROPIN_FILE"
    if [ "$state" = managed ]; then
        run_root rm -f "$DROPIN_FILE"
        echo '已删除受管 drop-in；继续重新加载并重启以收敛 daemon 状态。'
    else
        echo '受管 drop-in 已不存在；仍重新加载/重启以收敛残留 manager/process 状态。'
    fi

    run_root systemctl daemon-reload
    run_root systemctl restart "$SERVICE"
    require_active_service
    [ ! -e "$DROPIN_FILE" ] || die "撤销后受管 drop-in 仍存在: $DROPIN_FILE"
    effective="$(effective_proxy_url 2>/dev/null || true)"
    echo '已收敛撤销状态：tailscaled 已重启且 active。'
    if [ -n "$effective" ]; then
        echo '已验证: 其他 systemd 配置提供的 HTTP_PROXY 仍然有效（地址已隐藏）。'
    else
        echo '已验证: 当前没有其他有效 HTTP_PROXY。'
    fi
}

auto_configure() {
    local proxy answer='' external state
    if ! clash_tun_is_active; then
        echo '未检测到活动的 Clash Verge TUN；不配置 daemon proxy。'
        echo '这不影响普通网络下的 Tailscale 安装。'
        return 0
    fi
    if ! proxy="$(detected_clash_proxy)"; then
        echo '检测到 Clash Verge TUN，但最终配置中的 mixed-port 缺失或无效；不修改 tailscaled。' >&2
        return 0
    fi

    state="$(managed_dropin_state)"
    if [ "$state" = managed ]; then
        echo '检测到受管 proxy 配置；执行收敛 apply，确保运行中的 daemon 已消费配置。'
        apply_proxy "$proxy"
        return 0
    fi
    if effective_proxy_matches "$proxy"; then
        require_active_service
        echo "检测到活动的 Clash Verge TUN，tailscaled 已由外部配置有效使用 $proxy；不覆盖。"
        return 0
    fi
    [ "$state" != foreign ] || die "拒绝覆盖不含本脚本 marker 的 $DROPIN_FILE"

    external="$(effective_proxy_url 2>/dev/null || true)"
    if [ -n "$external" ]; then
        if probe_external_proxy "$external"; then
            require_active_service
            echo 'tailscaled 已由其他 HTTP proxy 成功连接控制面；保留外部配置，不覆盖。'
            return 0
        fi
        echo 'tailscaled 已有外部 HTTP_PROXY，但它无法连接控制面；为避免首次登录失败，拒绝继续登录。' >&2
        return 1
    fi

    echo "检测到活动的 Clash Verge TUN（mixed-port: ${proxy##*:}）。"
    echo 'Fake-IP/TUN 可能让 tailscaled 不能直接访问控制面，需要 daemon 显式经本地 HTTP proxy。'
    if [ ! -t 0 ]; then
        echo "非交互 stdin：未应用。需要时执行: ./configure-clash-proxy.sh --apply --proxy $proxy"
        return 0
    fi
    read -r -p '现在为 tailscaled 应用该代理？[Y/n] ' answer || answer=''
    case "$answer" in
        ''|Y|y|yes|YES|Yes) apply_proxy "$proxy" ;;
        *) echo "未应用。需要时执行: ./configure-clash-proxy.sh --apply --proxy $proxy" ;;
    esac
}

mode=''
proxy=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --auto|--apply|--status|--remove) [ -z "$mode" ] || die '一次只能使用一个操作选项'; mode="$1" ;;
        --proxy) shift; [ "$#" -gt 0 ] || die '--proxy 需要 URL 参数'; proxy="$1" ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
    shift
done

[ -z "$proxy" ] || [ "$mode" = --apply ] || die '--proxy 只能与 --apply 一起使用'

case "${mode:---status}" in
    --status) show_status ;;
    --remove) remove_proxy ;;
    --auto) auto_configure ;;
    --apply)
        if [ -z "$proxy" ]; then
            proxy="$(detected_clash_proxy)" || die "无法从 $CLASH_CONFIG 读取有效 mixed-port；请使用 --proxy http://HOST:PORT"
        fi
        apply_proxy "$proxy"
        ;;
esac
