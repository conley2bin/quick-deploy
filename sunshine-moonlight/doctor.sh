#!/bin/bash
# quick-deploy/sunshine-moonlight/doctor.sh
# 只读诊断：不安装、不修改、不启动任何服务。
# 注意：绝不执行 `sunshine --version`（它会读配置并写 sunshine.log，不是只读）；
# 版本一律从 dpkg 包元数据读取。

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

FLOOR_VERSION='2026.516.143833'
FLOOR_CVE='CVE-2026-32253'
CANONICAL_UNIT='app-dev.lizardbyte.app.Sunshine.service'
ALIAS_UNIT='sunshine.service'
# sunshine.conf 的 port 是基准端口（默认 47989）；Web UI 监听 base+1（默认 47990）
DEFAULT_BASE_PORT='47989'
MOONLIGHT_PINNED_SHA='0e855ffd22d407e18ab5fdb575fed5f01ca119a3f91993c5f0213f15ac80b400'

# 测试专用钩子（tests/run.sh 使用；真实运行不要设置）
CONFIG_DIR="${QD_SUNSHINE_CONFIG_DIR:-$HOME/.config/sunshine}"
CONFIG_FILE="$CONFIG_DIR/sunshine.conf"
UINPUT_NODE="${QD_UINPUT_NODE:-/dev/uinput}"
UHID_NODE="${QD_UHID_NODE:-/dev/uhid}"
SYSTEMD_USER_DIR="${QD_SYSTEMD_USER_DIR:-/usr/lib/systemd/user}"
OPT_DIR="$HOME/.local/opt/moonlight"
WRAPPER="$HOME/.local/bin/moonlight"
DESKTOP_FILE="$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"

CHECK_HOST=false
CHECK_CLIENT=false
EXPLICIT_CLIENT=false   # 显式 --client 时未安装 Moonlight 记失败；自动检测时只警告
FAILURES=0
WARNINGS=0

ok()   { printf '  [通过] %s\n' "$*"; }
warn() { printf '  [警告] %s\n' "$*"; WARNINGS=$((WARNINGS+1)); }
bad()  { printf '  [失败] %s\n' "$*"; FAILURES=$((FAILURES+1)); }

usage() {
    cat <<USAGE
用法: ./doctor.sh [--host] [--client] [-h]

只读诊断。不带参数时自动检测角色（装了 Sunshine 查主机项，装了 Moonlight 查客户端项，
两者都没有则两类都查）。退出码: 0=无失败项, 1=存在失败项。
严重性约定：显式 --client 时「未安装 Moonlight」记失败（退出码 1）；
自动检测模式下同一状态只记警告（可能只是不想在这台机器装客户端）。
USAGE
}

# ---- 通用 --------------------------------------------------------------------------

check_os() {
    qd_section '系统'
    if [ -r "$QD_OS_RELEASE_FILE" ]; then
        # shellcheck disable=SC1090
        . "$QD_OS_RELEASE_FILE"
        if [ "${ID:-}" = ubuntu ] && dpkg --compare-versions "${VERSION_ID:-0}" ge 24.04; then
            ok "系统: ${PRETTY_NAME:-Ubuntu}"
        else
            bad "仅支持 Ubuntu 24.04+: ${PRETTY_NAME:-未知}"
        fi
    else
        bad "无法读取 $QD_OS_RELEASE_FILE"
    fi
    printf '  [信息] 架构: %s\n' "$(dpkg --print-architecture 2>/dev/null || uname -m)"
    printf '  [信息] 会话类型: %s（显示服务器: %s）\n' \
        "${XDG_SESSION_TYPE:-未知}" "${XDG_CURRENT_DESKTOP:-未知}"
}

# ---- 主机 ----------------------------------------------------------------------------

check_host_session() {
    qd_section '主机: 图形会话'
    if [ -n "${XDG_SESSION_TYPE:-}" ]; then
        ok "当前处于已登录会话（$XDG_SESSION_TYPE）"
    else
        warn '检测不到图形会话环境变量（可能正通过 SSH 诊断）。Sunshine 需要本机有已登录的图形用户会话；enable-linger 不是替代方案。'
    fi
}

check_host_tailscale() {
    qd_section '主机: Tailscale'
    if ! command -v tailscale >/dev/null 2>&1; then
        warn '未安装 tailscale（若仅局域网使用可忽略；远程串流建议先运行 ../tailscale/install.sh）'
        return 0
    fi
    local ip
    ip="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
    if [ -n "$ip" ]; then
        ok "Tailscale IPv4: $ip"
    else
        warn 'tailscale 已安装但无 IPv4 地址（未登录或 tailscaled 未运行）'
    fi
    # bind_address 绑定 tailnet IP 的可用性权衡：tailscaled 不在线时 Sunshine 可能绑定失败
    local bind
    bind="$(qd_conf_get "$CONFIG_FILE" bind_address 2>/dev/null || true)"
    if [ -n "$bind" ] && [[ "$bind" =~ ^100\. ]]; then
        if [ -n "$ip" ] && [ "$ip" = "$bind" ]; then
            ok "bind_address ($bind) 与当前 tailscale IPv4 一致"
        else
            warn "bind_address=$bind 是 Tailnet 地址，但当前 tailscale IP 为 '${ip:-无}'。tailscaled 不在线或 IP 变化时 Sunshine 监听会失败——这是绑定 tailnet 的固有取舍（安全换可用性）。"
        fi
    fi
}

check_host_package() {
    qd_section '主机: Sunshine 包与安全基线'
    local status version
    status="$(dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null || true)"
    if [ "$status" != installed ]; then
        bad '未安装 sunshine 包（运行 ./install-host.sh）'
        return 0
    fi
    version="$(dpkg-query -W -f='${Version}' sunshine 2>/dev/null)"
    ok "已安装 Sunshine: $version"
    if qd_version_ge "$version" "$FLOOR_VERSION"; then
        ok "版本 >= v$FLOOR_VERSION（$FLOOR_CVE 已修复）"
    else
        bad "版本 $version 低于安全基线 v$FLOOR_VERSION（$FLOOR_CVE）！请立即升级: ./install-host.sh"
    fi
}

check_host_service() {
    qd_section '主机: systemd 用户服务'
    local unit=''
    if systemctl --user cat "$CANONICAL_UNIT" >/dev/null 2>&1 \
        || [ -f "$SYSTEMD_USER_DIR/$CANONICAL_UNIT" ]; then
        unit="$CANONICAL_UNIT"
    elif systemctl --user cat "$ALIAS_UNIT" >/dev/null 2>&1 \
        || [ -f "$SYSTEMD_USER_DIR/$ALIAS_UNIT" ]; then
        unit="$ALIAS_UNIT"
    fi
    if [ -z "$unit" ]; then
        bad "未找到 Sunshine 用户服务（$CANONICAL_UNIT / $ALIAS_UNIT）"
        return 0
    fi
    ok "服务单元: $unit"
    if systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
        ok '已 enabled'
    else
        bad "未 enabled（运行: systemctl --user enable --now $unit）"
    fi
    if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
        ok '运行中 (active)'
    else
        bad "未在运行。查看: journalctl --user -u $unit -e（注意需要已登录图形会话）"
    fi
}

check_host_caps() {
    qd_section '主机: 捕获 capability'
    local bin real cur
    bin="$(dpkg -L sunshine 2>/dev/null | grep -E '/bin/sunshine$' | head -n1 || true)"
    if [ -z "$bin" ]; then
        bad '无法在 sunshine 包内定位 bin/sunshine'
        return 0
    fi
    real="$(readlink -f "$bin")"
    if ! command -v getcap >/dev/null 2>&1; then
        warn '缺少 getcap（libcap2-bin），无法验证 capability'
        return 0
    fi
    cur="$(getcap "$real" 2>/dev/null || true)"
    if grep -q 'cap_sys_admin' <<<"$cur" && grep -q 'cap_sys_nice' <<<"$cur" && grep -q '=.*p' <<<"$cur"; then
        ok "$cur"
    else
        warn "capability 缺失（当前: ${cur:-无}）。KMS 捕获需要 cap_sys_admin+p；portal 捕获可不需要。修复: ./install-host.sh 会自动收敛"
    fi
}

check_host_input() {
    qd_section '主机: 输入注入'
    local node
    for node in "$UINPUT_NODE" "$UHID_NODE"; do
        if [ ! -e "$node" ]; then
            warn "$node 不存在（官方包通常通过 udev/uhid 提供；若刚安装请重新登录）"
        elif [ -r "$node" ] && [ -w "$node" ]; then
            ok "$node 可读写（uaccess ACL 生效）"
        else
            warn "$node 存在但无有效读写权限；install-host.sh 会在此时把你加入 input 组（需重新登录）"
        fi
    done
    if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx 'input'; then
        ok '当前用户在 input 组'
    else
        printf '  [信息] 当前用户不在 input 组（uaccess 生效时不需要）\n'
    fi
}

check_host_config() {
    qd_section '主机: 配置（仅显示受管键，不输出任何凭据）'
    if [ ! -f "$CONFIG_FILE" ]; then
        warn "配置文件不存在: $CONFIG_FILE（install-host.sh 会创建）"
        return 0
    fi
    local key value
    for key in upnp capture bind_address csrf_allowed_origins; do
        value="$(qd_conf_get "$CONFIG_FILE" "$key" 2>/dev/null || true)"
        if [ -z "$value" ]; then
            case "$key" in
                capture) ok 'capture 未设置（Sunshine 自动侦测，这是健康的默认状态）' ;;
                *) warn "$key 未设置" ;;
            esac
            continue
        fi
        case "$key:$value" in
            upnp:disabled) ok 'upnp = disabled' ;;
            upnp:*) warn "upnp = $value（建议 disabled，避免路由器自动开端口）" ;;
            capture:xcb) bad 'capture = xcb 是旧名，现行值为 x11；请修正（install-host.sh 重跑会自动移除）' ;;
            capture:auto) bad 'capture = auto 是无效写法：自动侦测应为“配置里没有 capture 键”。请删除该行（./install-host.sh --capture auto 会自动移除）' ;;
            capture:kms|capture:portal|capture:x11) ok "capture = $value" ;;
            capture:*) bad "capture = $value 无法识别（合法: kms|portal|x11，自动侦测 = 删除该键）" ;;
            *) printf '  [信息] %s = %s\n' "$key" "$value" ;;
        esac
    done
}

check_host_listeners() {
    qd_section '主机: 监听端口'
    command -v ss >/dev/null 2>&1 || { warn '缺少 ss（iproute2），跳过端口检查'; return 0; }
    local base web out
    base="$(qd_conf_get "$CONFIG_FILE" port 2>/dev/null || true)"
    if [ -n "$base" ]; then
        if [[ "$base" =~ ^[0-9]+$ ]] && [ "$base" -ge 1029 ] && [ "$base" -le 65514 ]; then
            web=$((base + 1))
        else
            bad "配置里的 port 不是 Sunshine 合法基准端口（1029-65514）: '$base'"
            return 0
        fi
    else
        base="$DEFAULT_BASE_PORT"
        web=$((base + 1))
    fi
    out="$(ss -tln 2>/dev/null || true)"
    # Parse the first field ending in :<web-port>. This is the local listener;
    # the peer column ends in :* and must not be mistaken for a wildcard bind.
    local local_listener
    local_listener="$(awk -v suffix=":$web" '
        $1 == "LISTEN" || $2 == "LISTEN" {
            for (i = 1; i <= NF; i++) {
                if (length($i) >= length(suffix) && substr($i, length($i) - length(suffix) + 1) == suffix) {
                    print $i
                    exit
                }
            }
        }
    ' <<<"$out")"
    if [ -n "$local_listener" ]; then
        ok "TCP $web (Web UI，基准端口 $base + 1) 监听中: $local_listener"
    else
        warn "TCP $web 未监听（Sunshine 未运行或绑定失败——若绑定 tailnet 地址，确认 tailscaled 在线）"
    fi
    case "$local_listener" in
        "0.0.0.0:$web"|"*:$web"|"[::]:$web"|":::$web")
            warn "$web 绑定在通配地址。默认应只绑定 tailscale IP；确认防火墙未对公网放行 47984-48010。" ;;
    esac
}

check_host_gpu() {
    qd_section '主机: GPU/编码器信号（信息性）'
    local -a render_nodes=()
    mapfile -t render_nodes < <(find /dev/dri -maxdepth 1 -type c -name 'renderD*' -print 2>/dev/null | sort)
    if [ "${#render_nodes[@]}" -gt 0 ]; then
        ok "DRM 渲染节点: ${render_nodes[*]}"
    else
        printf '  [信息] 无 /dev/dri 渲染节点\n'
    fi
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        ok "NVIDIA: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1)"
    fi
    if command -v vainfo >/dev/null 2>&1 && vainfo >/dev/null 2>&1; then
        printf '  [信息] VA-API 可用\n'
    fi
}

run_host_checks() {
    check_host_session
    check_host_tailscale
    check_host_package
    check_host_service
    check_host_caps
    check_host_input
    check_host_config
    check_host_listeners
    check_host_gpu
}

# ---- 客户端 ----------------------------------------------------------------------------

run_client_checks() {
    qd_section '客户端: Moonlight'
    local machine
    machine="$(uname -m)"
    if [ "$machine" = x86_64 ]; then
        ok "架构 x86_64 受支持"
    else
        bad "架构 $machine 不受官方 AppImage 支持"
    fi

    local ver_dir="$OPT_DIR/6.1.0"
    if [ -x "$ver_dir/AppRun" ] && [ -f "$ver_dir/.quick-deploy-sha256" ]; then
        local recorded
        recorded="$(cat "$ver_dir/.quick-deploy-sha256")"
        if [ "$recorded" = "$MOONLIGHT_PINNED_SHA" ]; then
            ok "安装目录与固定 SHA-256 一致: $ver_dir"
        else
            warn "安装记录摘要与 v6.1.0 固定值不一致（可能装的是其它版本）: $recorded"
        fi
    elif [ -d "$OPT_DIR" ]; then
        warn "$OPT_DIR 存在但缺少完整的 6.1.0 安装标记"
    elif [ "$EXPLICIT_CLIENT" = true ]; then
        bad '未安装 Moonlight（运行 ./install-client.sh）'
    else
        warn '未安装 Moonlight（运行 ./install-client.sh；自动检测模式下仅作提醒）'
    fi

    if [ -x "$WRAPPER" ] && grep -q 'quick-deploy/sunshine-moonlight' "$WRAPPER" 2>/dev/null; then
        ok "启动包装: $WRAPPER"
    else
        warn "启动包装缺失或外来: $WRAPPER"
    fi
    if [ -f "$DESKTOP_FILE" ] && grep -q 'quick-deploy/sunshine-moonlight' "$DESKTOP_FILE" 2>/dev/null; then
        ok "桌面项: $DESKTOP_FILE"
    else
        warn "桌面项缺失或外来: $DESKTOP_FILE"
    fi
}

# ---- 主流程 ------------------------------------------------------------------------------

main() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --host) CHECK_HOST=true ;;
            --client) CHECK_CLIENT=true; EXPLICIT_CLIENT=true ;;
            -h|--help) usage; exit 0 ;;
            *) qd_die "未知参数: $1" ;;
        esac
        shift
    done

    if [ "$CHECK_HOST" = false ] && [ "$CHECK_CLIENT" = false ]; then
        # 自动角色检测
        if dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null | grep -qx installed \
            || [ -f "$SYSTEMD_USER_DIR/$CANONICAL_UNIT" ]; then
            CHECK_HOST=true
        fi
        if [ -d "$OPT_DIR" ] || [ -e "$WRAPPER" ]; then
            CHECK_CLIENT=true
        fi
        if [ "$CHECK_HOST" = false ] && [ "$CHECK_CLIENT" = false ]; then
            CHECK_HOST=true
            CHECK_CLIENT=true
        fi
    fi

    check_os
    [ "$CHECK_HOST" = true ] && run_host_checks
    [ "$CHECK_CLIENT" = true ] && run_client_checks

    qd_section '结论'
    printf '失败 %d 项，警告 %d 项。\n' "$FAILURES" "$WARNINGS"
    [ "$FAILURES" -eq 0 ]
}

main "$@"
