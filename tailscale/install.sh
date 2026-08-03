#!/bin/bash
# Install or update Tailscale and converge tailscaled's HTTP proxy with the
# current Clash Verge TUN state.
set -euo pipefail

OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
KEYRING_FILE="${TAILSCALE_KEYRING_FILE:-/usr/share/keyrings/tailscale-archive-keyring.gpg}"
SOURCE_LIST_FILE="${TAILSCALE_SOURCE_LIST_FILE:-/etc/apt/sources.list.d/tailscale.list}"
SERVICE='tailscaled.service'
MARKER='# Managed by quick-deploy/tailscale/install.sh'
LEGACY_MARKER='# Managed by quick-deploy/tailscale/configure-clash-proxy.sh'
DROPIN_DIR="${TAILSCALE_DROPIN_DIR:-/etc/systemd/system/tailscaled.service.d}"
DROPIN_FILE="$DROPIN_DIR/quick-deploy-clash-proxy.conf"
CLASH_CONFIG="${CLASH_CONFIG:-$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml}"
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
用法: ./install.sh [--no-clash-check]

安装或更新 Ubuntu 24.04+ 的 Tailscale stable 包，只请求 tailscale 目标包。
包和 tailscaled.service 就绪后，先让 Clash Verge TUN 代理配置随当前网络状态收敛，
再判断首次登录。已登录节点不会重新认证；未登录时交互终端会询问是否前台执行一次
sudo tailscale up。

--no-clash-check  跳过登录前的 Clash Verge TUN 检测与代理收敛
-h, --help        显示帮助
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

check_ubuntu() {
    [ -r "$OS_RELEASE_FILE" ] || die "无法读取 $OS_RELEASE_FILE"
    # shellcheck disable=SC1090
    . "$OS_RELEASE_FILE"
    [ "${ID:-}" = ubuntu ] || die "当前系统不是 Ubuntu: ${ID:-未知}"
    [ -n "${VERSION_ID:-}" ] || die '系统版本信息缺失'
    dpkg --compare-versions "$VERSION_ID" ge 24.04 \
        || die "仅支持 Ubuntu 24.04 及更高版本；当前为 ${PRETTY_NAME:-$VERSION_ID}"
    [ -n "${VERSION_CODENAME:-}" ] || die '系统版本代号缺失'
    CODENAME="$VERSION_CODENAME"
    echo "系统: ${PRETTY_NAME:-Ubuntu $VERSION_ID} (${CODENAME})"
}

install_repository() {
    local key_url list_url key_tmp list_tmp
    command -v curl >/dev/null 2>&1 || die '缺少 curl。请先执行: sudo apt install curl'
    key_url="https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.noarmor.gpg"
    list_url="https://pkgs.tailscale.com/stable/ubuntu/${CODENAME}.tailscale-keyring.list"
    new_temp key_tmp
    new_temp list_tmp

    echo "下载 Tailscale stable APT key: $key_url"
    curl -fL --retry 3 --connect-timeout 20 -o "$key_tmp" "$key_url"
    echo "下载 Tailscale stable APT source: $list_url"
    curl -fL --retry 3 --connect-timeout 20 -o "$list_tmp" "$list_url"
    grep -Fq "https://pkgs.tailscale.com/stable/ubuntu ${CODENAME} main" "$list_tmp" \
        || die '官方下载的 APT source 内容不符合预期，未写入系统'

    run_root install -d -m 0755 "$(dirname "$KEYRING_FILE")" "$(dirname "$SOURCE_LIST_FILE")"
    run_root install -m 0644 "$key_tmp" "$KEYRING_FILE"
    run_root install -m 0644 "$list_tmp" "$SOURCE_LIST_FILE"
}

apt_failure_hint() {
    cat >&2 <<'HINT'
APT 索引刷新失败。若输出包含 MergeList / Package lists 或 Hash Sum mismatch，
请人工检查并修复 APT 索引后重试；例如确认没有并发 apt 进程，再按发行版文档
处理 /var/lib/apt/lists。脚本不会自动删除 lists，以免掩盖或扩大本机问题。
HINT
}

tailscale_package_record() {
    local records
    # Query the database as a whole: dpkg-query returning nonzero is a database
    # failure, not evidence that the tailscale package is absent. Keep its stderr
    # visible and let the caller stop before apt touches the system.
    if ! records="$(dpkg-query -W -f='${binary:Package}\t${db:Status-Status}\t${Version}\n')"; then
        echo '错误: dpkg-query 无法读取软件包数据库。' >&2
        return 2
    fi
    awk -F '\t' '
        $1 == "tailscale" || $1 ~ /^tailscale:/ { print $2 "\t" $3; found=1; exit }
        END { exit(found ? 0 : 1) }
    ' <<<"$records"
}

install_or_update_package() {
    local record='' state='' version='' rc=0
    echo '刷新 APT 索引...'
    if ! run_root apt-get update --error-on=any; then
        apt_failure_hint
        exit 1
    fi

    if record="$(tailscale_package_record)"; then
        IFS=$'\t' read -r state version <<<"$record"
        if [ "$state" = installed ]; then
            echo "已安装 Tailscale: $version；检查 stable 更新..."
        else
            echo "Tailscale 当前状态为 $state；开始安装..."
        fi
    else
        rc=$?
        if [ "$rc" -eq 1 ]; then
            echo '未安装 Tailscale；开始安装...'
        else
            die '无法查询 Tailscale 软件包状态；不会继续运行 apt 安装。'
        fi
    fi

    # Request only tailscale: no broad upgrade/autoremove is performed.
    run_root apt-get install -y tailscale

    if ! record="$(tailscale_package_record)"; then
        die 'apt 已返回，但无法重新读取 Tailscale 软件包状态。'
    fi
    IFS=$'\t' read -r state version <<<"$record"
    [ "$state" = installed ] || die "apt 已返回，但 Tailscale 状态仍为 ${state:-未记录}"
    [ -n "$version" ] || die 'apt 已返回，但 Tailscale 版本为空'
    echo "Tailscale 已安装: $version"
}

ensure_service() {
    local unit_state
    unit_state="$(systemctl is-enabled "$SERVICE" 2>&1 || true)"
    if grep -Eq '^(masked|masked-runtime)$' <<<"$unit_state"; then
        die "$SERVICE 被 $unit_state（管理员已显式禁用）；不会自动 unmask"
    fi
    run_root systemctl enable "$SERVICE"
    run_root systemctl start "$SERVICE"
    systemctl is-enabled --quiet "$SERVICE" || die "$SERVICE 未处于 enabled 状态"
    systemctl is-active --quiet "$SERVICE" || die "$SERVICE 未处于 active 状态"
    echo "$SERVICE 已 enabled 且 active"
}

get_status_json() {
    local json
    if ! json="$(tailscale status --json)"; then
        [ -z "$json" ] || printf '%s\n' "$json" >&2
        echo '错误: 无法读取 Tailscale 状态。' >&2
        return 2
    fi
    if ! python3 -c 'import json, sys; json.load(sys.stdin)' <<<"$json"; then
        echo '错误: Tailscale 状态不是有效 JSON。' >&2
        return 2
    fi
    printf '%s\n' "$json"
}

login_state() {
    python3 -c '
import json, sys
state = json.load(sys.stdin)
self_ = state.get("Self") or {}
print("logged" if state.get("BackendState") == "Running" and self_.get("TailscaleIPs") else "not-logged")
'
}

report_login() {
    python3 -c '
import json, sys
state = json.load(sys.stdin)
self_ = state.get("Self") or {}
tailnet = (state.get("CurrentTailnet") or {}).get("Name", "未知 tailnet")
ips = ", ".join(self_.get("TailscaleIPs") or [])
print("已登录 tailnet: " + tailnet)
print("本机 Tailscale IP: " + (ips or "未知"))
'
}

handle_login() {
    local json state answer=''
    json="$(get_status_json)" || die 'Tailscale 状态不可读；不会把该错误当成未登录。'
    state="$(login_state <<<"$json")" || die '无法解析 Tailscale 登录状态。'
    if [ "$state" = logged ]; then
        report_login <<<"$json"
        echo '保留现有登录状态，不执行 reauth。'
        return 0
    fi

    echo 'Tailscale 尚未登录。'
    if [ ! -t 0 ]; then
        echo '非交互 stdin：未启动登录。请在终端执行: sudo tailscale up'
        return 0
    fi
    read -r -p '现在前台执行一次 sudo tailscale up 登录？[Y/n] ' answer || answer=''
    case "$answer" in
        ''|Y|y|yes|YES|Yes)
            run_root tailscale up
            json="$(get_status_json)" || die 'tailscale up 后无法读取 Tailscale 状态。'
            state="$(login_state <<<"$json")" || die 'tailscale up 后无法解析 Tailscale 登录状态。'
            [ "$state" = logged ] || die 'tailscale up 已返回，但节点仍未显示为已登录'
            report_login <<<"$json"
            ;;
        *) echo '未登录。下一步: sudo tailscale up' ;;
    esac
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
    elif grep -Fqx "$MARKER" "$DROPIN_FILE" 2>/dev/null \
        || grep -Fqx "$LEGACY_MARKER" "$DROPIN_FILE" 2>/dev/null; then
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

clash_apply_proxy() {
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
        die '服务已重启，但 tailscaled 的有效代理环境不符合预期；请检查: systemctl show tailscaled.service -p Environment --value'
    fi
    echo "已应用并验证 tailscaled 代理: $proxy"
}

clash_remove_proxy() {
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

clash_auto_configure() {
    local proxy answer='' external state
    if ! clash_tun_is_active; then
        state="$(managed_dropin_state)"
        if [ "$state" != managed ]; then
            echo '未检测到活动的 Clash Verge TUN；不配置 daemon proxy。'
            echo '这不影响普通网络下的 Tailscale 安装。'
            return 0
        fi
        # A managed drop-in pins tailscaled to a local proxy. Without an active
        # TUN that proxy is unnecessary, and a dead one cuts tailscaled off the
        # control plane. Converge by evidence: keep it only while it still
        # reaches the control plane; remove it once the probe provably fails.
        external="$(effective_proxy_url 2>/dev/null || true)"
        if [ -n "$external" ] && probe_external_proxy "$external"; then
            echo '未检测到活动的 Clash Verge TUN；受管代理仍可连通控制面，保留配置。'
            return 0
        fi
        echo '未检测到活动的 Clash Verge TUN，且受管代理已无法连通控制面；自动移除受管 drop-in。'
        clash_remove_proxy
        return 0
    fi
    if ! proxy="$(detected_clash_proxy)"; then
        echo '检测到 Clash Verge TUN，但最终配置中的 mixed-port 缺失或无效；不修改 tailscaled。' >&2
        return 0
    fi

    state="$(managed_dropin_state)"
    if [ "$state" = managed ]; then
        echo '检测到受管 proxy 配置；执行收敛 apply，确保运行中的 daemon 已消费配置。'
        clash_apply_proxy "$proxy"
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
        echo '非交互 stdin：未应用。需要时请在交互终端重跑 ./install.sh。'
        return 0
    fi
    read -r -p '现在为 tailscaled 应用该代理？[Y/n] ' answer || answer=''
    case "$answer" in
        ''|Y|y|yes|YES|Yes) clash_apply_proxy "$proxy" ;;
        *) echo '未应用。需要时请重跑 ./install.sh。' ;;
    esac
}

run_clash_check=true
while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-clash-check) run_clash_check=false ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数: $1" ;;
    esac
    shift
done

check_ubuntu
install_repository
install_or_update_package
ensure_service
# A first login needs the control plane, so converge the Clash proxy first.
if [ "$run_clash_check" = true ]; then
    clash_auto_configure
fi
handle_login
