#!/bin/bash
# Install/configure the native Sunshine package for an existing Ubuntu desktop over Tailscale.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

GITHUB_REPO='LizardByte/Sunshine'
VERSION_TAG="v$QD_SUNSHINE_VERSION"
CONFIG_DIR=''
CONFIG_FILE=''
STATE_DIR="${QD_HOST_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/quick-deploy/sunshine-moonlight}"
STATE_FILE="$STATE_DIR/host.state"
UINPUT_NODE="${QD_UINPUT_NODE:-/dev/uinput}"
UHID_NODE="${QD_UHID_NODE:-/dev/uhid}"
CAPTURE=''
BIND_ADDRESS=''
BASE_PORT=''
WEB_UI_PORT=''
CONFIG_CHANGED=false
PACKAGE_CHANGED=false
CAPS_CHANGED=false

usage() {
    cat <<USAGE
用法: ./install-host.sh [--version v版本] [--capture auto|kms|portal|x11|nvfbc|wlr|kwin]
                      [--bind-address 本机TailnetIPv4]
默认版本 v$QD_SUNSHINE_VERSION；最低版本 v$QD_SUNSHINE_FLOOR。
不传 --capture 保留已有设置；auto 明确删除 capture 键。
--bind-address 只能确认本机已分配的 Tailscale IPv4，不能用来绑定 LAN/公网/通配地址。
请在已登录图形桌面的普通用户下运行。脚本会调用 sudo 安装原生包，不修改睡眠/锁屏。
USAGE
}
parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                [ "$#" -ge 2 ] && [[ "$2" =~ ^v[0-9]+(\.[0-9]+)*$ ]] || qd_die '--version 需要 v+数字点分版本'
                VERSION_TAG="$2"; shift;;
            --capture)
                [ "$#" -ge 2 ] || qd_die '--capture 需要参数'
                if [ "$2" != auto ] && { [ -z "$2" ] || ! qd_valid_capture "$2"; }; then
                    qd_die 'capture 只接受 auto|kms|portal|x11|nvfbc|wlr|kwin'
                fi
                CAPTURE="$2"; shift;;
            --bind-address)
                [ "$#" -ge 2 ] && qd_valid_ipv4 "$2" || qd_die '--bind-address 需要标准 IPv4 地址'
                BIND_ADDRESS="$2"; shift;;
            -h|--help) usage; exit 0;;
            *) qd_die "未知参数: $1";;
        esac
        shift
    done
    qd_version_ge "$VERSION_TAG" "$QD_SUNSHINE_FLOOR" \
        || qd_die "拒绝低于维护基线 v$QD_SUNSHINE_FLOOR 的版本；请使用包含 September 2026 上游安全修复的版本"
}

preflight() {
    local cmd ip session capture origin
    for cmd in python3 curl sha256sum dpkg-deb systemctl loginctl tailscale ip ss; do qd_require_cmd "$cmd"; done
    CONFIG_DIR="$(qd_host_config_dir)" || exit 1
    CONFIG_FILE="$CONFIG_DIR/sunshine.conf"
    qd_check_service_config "$CONFIG_DIR" || qd_die '用户服务配置不兼容；尚未安装或改写配置'
    session="$(qd_graphical_session)" || qd_die '没有当前用户的活动本地图形会话；请先登录主机桌面（SSH/linger 不能创建桌面）'
    qd_check_display_environment "$session" || exit 1
    qd_info "图形会话: $session"
    ip="$(qd_tailnet_ip)" || exit 1
    [ -z "$BIND_ADDRESS" ] || [ "$BIND_ADDRESS" = "$ip" ] || qd_die "只能绑定本机 Tailnet IPv4: $ip"
    BIND_ADDRESS="$ip"
    [ ! -L "$CONFIG_FILE" ] || qd_die "配置文件是符号链接，请先人工核对目标: $CONFIG_FILE"
    if [ -e "$CONFIG_FILE" ]; then
        [ -f "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ] || qd_die "配置不是可读普通文件: $CONFIG_FILE"
    fi
    BASE_PORT="$(qd_base_port "$CONFIG_FILE")" || exit 1
    WEB_UI_PORT=$((BASE_PORT + 1))
    origin="$(qd_conf_get "$CONFIG_FILE" origin_web_ui_allowed || true)"
    case "$origin" in
        ''|lan|wan) ;;
        pc) qd_die 'origin_web_ui_allowed=pc 拒绝 Tailnet Web UI；若同意 Tailnet 访问，请手动改为 lan 后重跑（无需 wan）';;
        *) qd_die "无法识别 origin_web_ui_allowed=$origin；请先修正为 lan";;
    esac
    capture="$(qd_conf_get "$CONFIG_FILE" capture || true)"
    if [ -z "$CAPTURE" ] && ! qd_valid_capture "$capture"; then
        qd_die "已有 capture=$capture 无效；显式 --capture auto 删除，或选择有效后端（X11 用 x11，不是 xcb）"
    fi
    case "${CAPTURE:-$capture}:$session" in
        x11:*'(wayland)') qd_die 'capture=x11 只用于 Xorg 会话；Wayland 桌面请明确选择 kms、portal 或自动选择';;
        wlr:*'(x11)'|kwin:*'(x11)') qd_die 'capture=wlr/kwin 需要 Wayland 会话；Xorg 桌面请选择 x11、kms 或自动选择';;
    esac
}

pick_deb_asset() {
    local json_file="$1" arch="$2" ubuntu_version="$3"
    python3 - "$json_file" "$arch" "$ubuntu_version" "$VERSION_TAG" <<'PY'
import json, re, sys

path, arch, ubuntu_version, expected_tag = sys.argv[1:5]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)

if data.get("tag_name") != expected_tag:
    print(f"错误: release tag 不匹配（期望 {expected_tag}，实际 {data.get('tag_name')}）", file=sys.stderr)
    sys.exit(1)

assets = data.get("assets") or []
preferred = f"sunshine-ubuntu-{ubuntu_version}-{arch}.deb"

def matches(name):
    n = name.lower()
    return (
        n.endswith(".deb")
        and "ubuntu" in n
        and ubuntu_version in n
        and re.search(rf"(^|[^a-z0-9]){re.escape(arch)}([^a-z0-9]|$)", n)
    )

candidates = [a for a in assets if matches(a.get("name") or "")]
chosen = None
for a in candidates:
    if a["name"] == preferred:
        chosen = a
        break
if chosen is None and len(candidates) == 1:
    chosen = candidates[0]
if chosen is None:
    names = ", ".join(a.get("name", "?") for a in assets) or "(无资产)"
    print(f"错误: 无法唯一确定 Ubuntu {ubuntu_version}/{arch} 的 .deb 资产。release 资产列表: {names}", file=sys.stderr)
    sys.exit(1)

digest = chosen.get("digest") or ""
if not digest.startswith("sha256:"):
    print(f"错误: GitHub API 未提供资产 {chosen['name']} 的 sha256 digest，无法验证，拒绝继续。", file=sys.stderr)
    sys.exit(1)

print(f"{chosen['browser_download_url']}\t{digest[len('sha256:'):]}\t{chosen['name']}")
PY
}

download_and_verify_deb() {
    local arch="$1" json_file json_tmp url digest name deb
    qd_require_cmd curl curl
    qd_require_cmd python3 python3
    qd_require_cmd sha256sum coreutils

    qd_mktemp_file json_tmp
    qd_info "查询 GitHub release: $GITHUB_REPO $VERSION_TAG"
    qd_curl -o "$json_tmp" \
        "https://api.github.com/repos/$GITHUB_REPO/releases/tags/$VERSION_TAG" \
        || qd_die "无法获取 release 元数据（检查网络/代理；api.github.com 需要可达）"

    json_file="$json_tmp"
    local picked
    picked="$(pick_deb_asset "$json_file" "$arch" "$VERSION_ID")" || exit 1
    url="$(cut -f1 <<<"$picked")"
    digest="$(cut -f2 <<<"$picked")"
    name="$(cut -f3 <<<"$picked")"
    qd_info "选定资产: $name"

    qd_mktemp_file deb --suffix=.deb
    qd_info "下载: $url"
    qd_curl -o "$deb" "$url" || qd_die '下载失败；未安装下载文件'
    qd_verify_sha256 "$deb" "$digest"
    DEB_FILE="$deb"
}

sunshine_pkg_installed() {
    dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null | grep -qx installed
}
sunshine_pkg_version() { dpkg-query -W -f='${Version}' sunshine; }

record_ownership() {
    # 只在首次运行时记录：sunshine 包是否在本脚本介入之前就已存在。
    # uninstall.sh 据此默认保留“外来”的包。第二个参数是实际在机版本；
    # 已安装版本比请求版本新时不能把请求标签写成实际状态。
    local ownership="$1" actual_version="${2:-${VERSION_TAG#v}}"
    mkdir -p "$STATE_DIR"
    if [ ! -f "$STATE_FILE" ]; then
        local pre=false
        [ "$ownership" = preexisting ] && pre=true
        cat >"$STATE_FILE" <<EOF_STATE
# quick-deploy/sunshine-moonlight 主机归属记录（install-host.sh 首次运行时生成）
package_preexisting=$pre
first_run_version=$actual_version
EOF_STATE
    fi
    # 每次运行都刷新最近检查到的实际版本（不含任何凭据）
    cat >"$STATE_DIR/last-install" <<EOF_LAST
version=$actual_version
time=$(date -Iseconds)
EOF_LAST
}

verify_deb_metadata() {
    local arch="$1" package upstream actual_arch
    package="$(dpkg-deb -f "$DEB_FILE" Package)" || qd_die '无法读取 deb Package'
    actual_arch="$(dpkg-deb -f "$DEB_FILE" Architecture)" || qd_die '无法读取 deb Architecture'
    DEB_VERSION="$(dpkg-deb -f "$DEB_FILE" Version)" || qd_die '无法读取 deb Version'
    [ "$package" = sunshine ] || qd_die "deb Package=$package，不是 sunshine"
    [ "$actual_arch" = "$arch" ] || qd_die "deb Architecture=$actual_arch，期望 $arch"
    upstream="$(qd_upstream_version "$DEB_VERSION")" || qd_die "无法识别 deb 上游版本: $DEB_VERSION"
    [ "$upstream" = "${VERSION_TAG#v}" ] || qd_die "deb 上游版本 $upstream 与请求 $VERSION_TAG 不匹配"
    qd_version_ge "$upstream" "$QD_SUNSHINE_FLOOR" || qd_die 'deb 上游版本低于维护基线'
}
install_package() {
    local arch="$1" preexisting=false installed='' upstream now_version
    if sunshine_pkg_installed; then
        preexisting=true
        installed="$(sunshine_pkg_version)"
        upstream="$(qd_upstream_version "$installed")" || qd_die "无法识别已安装版本: $installed"
        if qd_version_ge "$upstream" "${VERSION_TAG#v}"; then
            if [ "$upstream" = "${VERSION_TAG#v}" ]; then
                qd_info "已有请求的上游版本 ($installed)，跳过下载/重装"
            else
                qd_warn "已有更高上游版本 $upstream，保留现有包，不降级"
            fi
            record_ownership preexisting "$installed"
            return 0
        fi
    fi
    download_and_verify_deb "$arch"
    verify_deb_metadata "$arch"
    local -a apt_args=(install -y)
    if [ "$preexisting" = true ] && dpkg --compare-versions "$installed" gt "$DEB_VERSION"; then
        # An old downstream epoch can sort above a newer official upstream release.
        qd_info '已验证上游版本更新；允许切换到 Debian 排序较低的官方包版本'
        apt_args+=(--allow-downgrades)
    fi
    qd_sudo apt-get "${apt_args[@]}" "$DEB_FILE" || qd_die 'apt 安装失败；请检查 apt/dpkg 状态，本次未记录包归属'
    sunshine_pkg_installed || qd_die 'apt 返回后 sunshine 不是 installed；请检查 dpkg 状态'
    now_version="$(sunshine_pkg_version)"
    [ "$now_version" = "$DEB_VERSION" ] || qd_die "安装后版本 $now_version 与已验证 deb $DEB_VERSION 不符；请检查包状态"
    PACKAGE_CHANGED=true
    if [ "$preexisting" = true ]; then record_ownership preexisting "$now_version"; else record_ownership fresh "$now_version"; fi
    qd_info "包已安装: $now_version"
}

converge_caps() {
    local capture bin cur
    capture="${CAPTURE:-$(qd_conf_get "$CONFIG_FILE" capture || true)}"
    # KMS needs these file capabilities; portal/X11 have their own privilege handling.
    case "$capture" in ''|auto|kms) ;; *) return 0;; esac
    if ! command -v getcap >/dev/null 2>&1; then qd_sudo apt-get install -y libcap2-bin; fi
    bin="$(qd_sunshine_binary)"
    [ -n "$bin" ] || qd_die '包内缺少 sunshine executable'
    bin="$(readlink -f "$bin")"
    cur="$(getcap "$bin")"
    if grep -qE 'cap_sys_(admin,cap_sys_nice|nice,cap_sys_admin)=e?p($| )' <<<"$cur"; then return 0; fi
    qd_sudo setcap 'cap_sys_admin,cap_sys_nice+p' "$bin"
    cur="$(getcap "$bin")"
    grep -qE 'cap_sys_(admin,cap_sys_nice|nice,cap_sys_admin)=e?p($| )' <<<"$cur" || qd_die 'setcap 后 capability 仍不完整'
    CAPS_CHANGED=true
}
converge_input_access() {
    # Do not confuse absent devices with group membership. Keep repairs explicit.
    [ -e "$UINPUT_NODE" ] || qd_die "$UINPUT_NODE 不存在，键鼠注入未就绪；请检查 uinput 模块与包内 60-sunshine.rules，再重跑"
    if [ ! -r "$UINPUT_NODE" ] || [ ! -w "$UINPUT_NODE" ]; then
        qd_die "$UINPUT_NODE 无读写权限；请检查活动会话 uaccess ACL/udev。只有节点属 input 组且 ACL 不适用时才考虑加入 input 组；本次未修改组"
    fi
    if [ ! -e "$UHID_NODE" ] || [ ! -r "$UHID_NODE" ] || [ ! -w "$UHID_NODE" ]; then
        qd_warn "$UHID_NODE 不存在或不可读写，部分手柄注入不可用；请检查 uhid 模块/udev，键鼠不依赖此节点"
    fi
}
configure_sunshine() {
    local staged mode origin="https://$BIND_ADDRESS:$WEB_UI_PORT"
    mkdir -p "$CONFIG_DIR"
    qd_mktemp_file staged "$CONFIG_FILE.qdtmp.XXXXXX"
    if [ -f "$CONFIG_FILE" ]; then cp -p "$CONFIG_FILE" "$staged"; fi
    qd_conf_set "$staged" upnp disabled
    qd_conf_set "$staged" address_family ipv4
    qd_conf_set "$staged" bind_address "$BIND_ADDRESS"
    qd_conf_ensure_token "$staged" csrf_allowed_origins "$origin"
    case "$CAPTURE" in
        auto) qd_conf_unset "$staged" capture;;
        '') ;; # Preserve the user's selected backend, including its comments.
        *) qd_conf_set "$staged" capture "$CAPTURE";;
    esac
    [ "$(qd_conf_get "$staged" upnp)" = disabled ] &&
        [ "$(qd_conf_get "$staged" address_family)" = ipv4 ] &&
        [ "$(qd_conf_get "$staged" bind_address)" = "$BIND_ADDRESS" ] || qd_die '候选配置回读失败'
    qd_conf_get "$staged" csrf_allowed_origins | tr ',' '\n' | sed 's/^[ \t]*//;s/[ \t]*$//' | grep -Fxq "$origin" \
        || qd_die '候选配置 csrf_allowed_origins 回读失败'
    mode="$(stat -c %a "$staged")"
    if [ $((8#$mode & ~8#600)) -ne 0 ]; then chmod 600 "$staged"; fi
    if [ -f "$CONFIG_FILE" ] && cmp -s "$staged" "$CONFIG_FILE"; then
        # Content-identical runs do not touch backups or restart an active stream.
        chmod --reference="$staged" "$CONFIG_FILE"
        qd_info '配置无变化'
    else
        if [ -f "$CONFIG_FILE" ]; then
            local backup
            qd_mktemp_file backup "$CONFIG_FILE.bak.qdtmp.XXXXXX"
            cp "$CONFIG_FILE" "$backup"
            chmod 600 "$backup"
            mv -f "$backup" "$CONFIG_FILE.bak"
        fi
        mv -f "$staged" "$CONFIG_FILE"
        CONFIG_CHANGED=true
        qd_info "配置已更新: $CONFIG_FILE（其它键与凭据保留）"
    fi
}

enable_service() {
    local unit attempt detail='' binary_status=0
    systemctl --user daemon-reload || qd_die '用户管理器 daemon-reload 失败'
    qd_check_service_config "$CONFIG_DIR" || qd_die '已安装包的用户服务配置不兼容；尚未启动'
    unit="$(qd_find_unit)" || qd_die '未找到 Sunshine 用户服务'
    systemctl --user enable "$unit" || qd_die "无法 enable $unit"
    if systemctl --user is-active --quiet "$unit"; then
        qd_running_binary_current "$unit" || binary_status=$?
        if [ "$PACKAGE_CHANGED" = true ] || [ "$CAPS_CHANGED" = true ] || [ "$CONFIG_CHANGED" = true ] || [ "$binary_status" -eq 1 ]; then
            qd_info '包、capability、配置或运行 executable 有变化，重启用户服务'
            systemctl --user restart "$unit" || qd_die "无法 restart $unit"
        else
            qd_info '包与配置无变化，不重启活动服务'
        fi
    else
        systemctl --user start "$unit" || qd_die "无法 start $unit；查看 journalctl --user -u $unit -e"
    fi
    systemctl --user is-enabled --quiet "$unit" || qd_die "$unit 未 enabled"
    # Type=simple does not signal application readiness. Wait for its actual control listeners.
    for attempt in {1..30}; do
        if systemctl --user is-active --quiet "$unit" && detail="$(qd_check_listeners "$BIND_ADDRESS" "$BASE_PORT" "$unit" 2>&1)"; then
            qd_info "$detail"
            return 0
        fi
        sleep 1
    done
    qd_die "服务在 30 秒内未完成 TCP 监听。$detail
查看 journalctl --user -u $unit -e；portal 首次授权需在主机桌面确认。软件可能已安装，请修正后重跑。"
}

main() {
    parse_args "$@"
    qd_require_not_root
    qd_require_ubuntu
    local arch
    arch="$(dpkg --print-architecture)"
    case "$arch" in amd64|arm64) ;; *) qd_die "不支持架构 $arch";; esac
    preflight
    install_package "$arch"
    converge_caps
    converge_input_access
    configure_sunshine
    enable_service
    cat <<EOF_DONE
Sunshine 已部署，控制端口已监听。
  Web UI: https://$BIND_ADDRESS:$WEB_UI_PORT
  Moonlight 手动添加: $BIND_ADDRESS:$BASE_PORT
首次在 Web UI 设置管理员凭据；核对待配对客户端/来源后输入 Moonlight 显示的 PIN。
尚未验证实际 Desktop 串流：请从客户端检查画面、键鼠和断开重连。
EOF_DONE
}
main "$@"
