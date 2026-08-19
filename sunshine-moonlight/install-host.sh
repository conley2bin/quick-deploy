#!/bin/bash
# quick-deploy/sunshine-moonlight/install-host.sh
# 在 Ubuntu 24.04+ 上安装/收敛 Sunshine 串流主机（官方 GitHub release .deb）。
#
# 安全设计：
#   - 版本下限 v2026.516.143833（CVE-2026-32253，认证绕过，CVSS 9.8），
#     低于下限一律无条件拒绝，不提供任何绕过开关。
#   - .deb 的 SHA-256 与 GitHub API 返回的 asset digest 比对通过后才允许 apt 安装；
#     摘要不匹配时系统在逻辑上未被触碰。
#   - 不动锁屏/睡眠/DPMS 设置，不启用 enable-linger，不把端口暴露到公网。
#
# 幂等：重跑会重新校验并重放到同一状态；已有 ~/.config/sunshine 的未知键全部保留。

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

# ---- 常量 --------------------------------------------------------------------

GITHUB_REPO='LizardByte/Sunshine'
DEFAULT_VERSION='v2026.516.143833'
FLOOR_VERSION='2026.516.143833'           # CVE-2026-32253 修复下限（不含前导 v）
FLOOR_CVE='CVE-2026-32253'
CANONICAL_UNIT='app-dev.lizardbyte.app.Sunshine.service'
ALIAS_UNIT='sunshine.service'
# sunshine.conf 里的 port 是“基准端口”（默认 47989）；Web UI 实际监听 base+1（默认 47990）。
DEFAULT_BASE_PORT='47989'

# 测试专用钩子（tests/run.sh 使用；真实运行不要设置）：
#   QD_OS_RELEASE_FILE      替代 /etc/os-release（common.sh）
#   QD_SUNSHINE_CONFIG_DIR   替代 ~/.config/sunshine
#   QD_UINPUT_NODE/QD_UHID_NODE  替代 /dev/uinput、/dev/uhid 设备节点路径
#   QD_SYSTEMD_USER_DIR      替代 /usr/lib/systemd/user（单元文件探测目录）
CONFIG_DIR="${QD_SUNSHINE_CONFIG_DIR:-$HOME/.config/sunshine}"
CONFIG_FILE="$CONFIG_DIR/sunshine.conf"
UINPUT_NODE="${QD_UINPUT_NODE:-/dev/uinput}"
UHID_NODE="${QD_UHID_NODE:-/dev/uhid}"
SYSTEMD_USER_DIR="${QD_SYSTEMD_USER_DIR:-/usr/lib/systemd/user}"

# 安装归属记录：uninstall.sh 据此判断 sunshine 包是否由本脚本引入。
STATE_DIR="${QD_HOST_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/quick-deploy/sunshine-moonlight}"
STATE_FILE="$STATE_DIR/host.state"

VERSION_TAG="$DEFAULT_VERSION"
CAPTURE=''                 # 空 = 不写 capture 键（Sunshine 自行选择）；auto = 显式删除 capture 键
BIND_ADDRESS=''            # 空 = 取当前 tailscale IPv4
WEB_UI_PORT=''             # configure_sunshine 收敛后 = 配置基准端口 + 1
CONFIG_CHANGED=false       # configure_sunshine 检测到内容变化时置 true

usage() {
    cat <<USAGE
用法: ./install-host.sh [选项]

安装或收敛 Sunshine 串流主机（Ubuntu 24.04+，官方 GitHub release .deb）。
不要用 root/sudo 运行本脚本；需要管理员权限的步骤会自行调用 sudo。

选项:
  --version TAG                  指定 release 标签（默认 $DEFAULT_VERSION）。低于 v$FLOOR_VERSION
                                 （$FLOOR_CVE 修复版本）一律无条件拒绝，没有绕过开关。
  --capture auto|kms|portal|x11  写入 capture 捕获后端；auto = 删除 capture 键，由 Sunshine
                                 自动侦测（默认不写；发现旧的 xcb/auto 等无效值会警告并移除）
  --bind-address IPV4            Web UI/服务绑定地址（默认取当前 tailscale IPv4）
  -h, --help                     显示帮助
USAGE
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                [ "$#" -ge 2 ] || qd_die '--version 需要一个参数'
                [[ "$2" =~ ^v[0-9]+(\.[0-9]+)*$ ]] \
                    || qd_die "--version 标签格式非法: $2（期望形如 v2026.516.143833，仅 v+数字+点）"
                VERSION_TAG="$2"; shift ;;
            --capture)
                [ "$#" -ge 2 ] || qd_die '--capture 需要一个参数'
                case "$2" in
                    auto|kms|portal|x11) CAPTURE="$2" ;;
                    *) qd_die "--capture 只接受 auto|kms|portal|x11（收到: $2）。注意 xcb 是旧名，现行值为 x11。" ;;
                esac
                shift ;;
            --bind-address)
                [ "$#" -ge 2 ] || qd_die '--bind-address 需要一个参数'
                qd_valid_ipv4 "$2" \
                    || qd_die "--bind-address 不是合法 IPv4 点分地址: $2"
                BIND_ADDRESS="$2"; shift ;;
            -h|--help) usage; exit 0 ;;
            *) qd_die "未知参数: $1（-h 查看用法）" ;;
        esac
        shift
    done
}

# ---- 版本下限 ------------------------------------------------------------------

# 低于下限一律拒绝：远程桌面的认证绕过（CVSS 9.8）没有“可接受风险”的合理场景，
# 因此不提供任何绕过开关（旧的 --i-accept-cve-2026-32253-risk 已移除）。
enforce_version_floor() {
    local num="${VERSION_TAG#v}"
    if qd_version_ge "$num" "$FLOOR_VERSION"; then
        qd_info "版本基线: $VERSION_TAG >= v$FLOOR_VERSION（$FLOOR_CVE 已修复）"
        return 0
    fi
    qd_die "拒绝安装 $VERSION_TAG：低于安全基线 v$FLOOR_VERSION（$FLOOR_CVE，认证绕过，CVSS 9.8）。
该漏洞没有可接受的例外场景，本脚本不提供绕过开关。请使用 >= v$FLOOR_VERSION 的版本。"
}

# ---- 资产选择与摘要验证 -----------------------------------------------------------

# 从 GitHub API release JSON 中选出当前 Ubuntu VERSION_ID/架构的 .deb，输出: 下载URL<TAB>sha256。
# 例如 Ubuntu 24.04 只接受 sunshine-ubuntu-24.04-amd64.deb；未来系统不会静默安装旧发行版资产。
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

    qd_mktemp_file deb
    qd_info "下载: $url"
    qd_curl -o "$deb" "$url" || qd_die '下载失败；未对系统做任何修改'
    qd_verify_sha256 "$deb" "$digest"
    DEB_FILE="$deb"
}

# ---- 包安装与状态记录 -------------------------------------------------------------

sunshine_pkg_installed() {
    dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null | grep -qx 'installed'
}

sunshine_pkg_version() {
    dpkg-query -W -f='${Version}' sunshine 2>/dev/null
}

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

install_package() {
    local arch="$1"
    local preexisting=false installed_version='' requested_version="${VERSION_TAG#v}"
    if sunshine_pkg_installed; then
        preexisting=true
        installed_version="$(sunshine_pkg_version)"
        [ -n "$installed_version" ] || qd_die '已安装 Sunshine，但 dpkg 返回的版本为空'
        qd_info "检测到已安装的 Sunshine: $installed_version（保留现有状态与配置）"
        if qd_version_ge "$installed_version" "$requested_version"; then
            qd_version_ge "$installed_version" "$FLOOR_VERSION" \
                || qd_die "现有版本 $installed_version 低于安全基线 v$FLOOR_VERSION（$FLOOR_CVE）"
            if [ "$installed_version" = "$requested_version" ]; then
                qd_info '已是请求版本，跳过重复下载与 apt 重装'
            else
                qd_warn "现有版本 $installed_version 高于请求版本 $requested_version；拒绝降级，保留现有版本"
            fi
            record_ownership preexisting "$installed_version"
            return 0
        fi
    fi

    download_and_verify_deb "$arch"

    qd_info '安装 .deb（apt 会自动补齐依赖）...'
    if ! qd_sudo apt-get install -y "$DEB_FILE"; then
        qd_die 'apt 安装失败；未记录任何归属状态，系统包状态由 apt 自身保证一致'
    fi

    sunshine_pkg_installed || qd_die 'apt 返回后仍查询不到 sunshine 包，安装失败'
    local now_version
    now_version="$(sunshine_pkg_version)"
    [ -n "$now_version" ] || qd_die 'apt 返回后 Sunshine 版本为空'
    if ! qd_version_ge "$now_version" "$FLOOR_VERSION"; then
        qd_die "安装后版本 $now_version 低于基线 v$FLOOR_VERSION（$FLOOR_CVE）；请人工检查"
    fi
    qd_info "Sunshine 包已就绪: $now_version"

    if [ "$preexisting" = true ]; then
        record_ownership preexisting "$now_version"
    else
        record_ownership fresh "$now_version"
    fi
}

# ---- 捕获后端能力（cap） -----------------------------------------------------------

# 官方 deb 的 postinst 已执行 setcap cap_sys_admin,cap_sys_nice+p、加载 uhid、
# 安装 udev 规则。这里只做“验证 + 缺失时修复”，不重复安装 udev 规则。
converge_caps() {
    if [ "$CAPTURE" = portal ]; then
        qd_info 'capture=portal：经桌面门户捕获，无需 cap_sys_admin，跳过 capability 收敛'
        return 0
    fi
    if ! command -v getcap >/dev/null 2>&1; then
        qd_info '安装 libcap2-bin（提供 getcap/setcap）...'
        qd_sudo apt-get install -y libcap2-bin
    fi
    local bin real cur
    bin="$(dpkg -L sunshine 2>/dev/null | grep -E '/bin/sunshine$' | head -n1)"
    [ -n "$bin" ] || qd_die '无法在 sunshine 包内定位 bin/sunshine'
    real="$(readlink -f "$bin")"
    cur="$(getcap "$real" 2>/dev/null || true)"
    if grep -q 'cap_sys_admin' <<<"$cur" && grep -q 'cap_sys_nice' <<<"$cur" && grep -q '=.*p' <<<"$cur"; then
        qd_info "capability 已正确（官方 postinst 已设置）: $cur"
        return 0
    fi
    qd_warn "capability 缺失或不完整（当前: ${cur:-无}），执行修复: setcap cap_sys_admin,cap_sys_nice+p $real"
    qd_sudo setcap 'cap_sys_admin,cap_sys_nice+p' "$real"
    cur="$(getcap "$real" 2>/dev/null || true)"
    if grep -q 'cap_sys_admin' <<<"$cur" && grep -q 'cap_sys_nice' <<<"$cur" && grep -q '=.*p' <<<"$cur"; then
        qd_info "capability 修复并验证通过: $cur"
    else
        qd_die "setcap 后仍读不到 cap_sys_admin,cap_sys_nice+p（当前: ${cur:-无}）"
    fi
}

# ---- 输入设备访问 -----------------------------------------------------------------

# 官方包的 udev 规则 + systemd-logind 的 uaccess 通常会让“已登录图形会话的用户”
# 直接获得 /dev/uinput（及 /dev/uhid）的读写 ACL。只有有效访问缺失时才退回 input 组。
converge_input_access() {
    local need_group=false node
    for node in "$UINPUT_NODE" "$UHID_NODE"; do
        if [ -e "$node" ]; then
            if [ -r "$node" ] && [ -w "$node" ]; then
                qd_info "输入设备可读写: $node（uaccess ACL 生效，无需 input 组）"
            else
                qd_warn "输入设备存在但当前用户无有效读写权限: $node"
                need_group=true
            fi
        else
            qd_warn "输入设备节点不存在: $node（官方包应已加载 uhid/配置 udev；请确认 Sunshine 包安装完整且已重新登录）"
            need_group=true
        fi
    done
    [ "$need_group" = true ] || return 0

    if id -nG | tr ' ' '\n' | grep -qx 'input'; then
        qd_warn '你已在 input 组，但权限尚未生效：请注销并重新登录图形会话后重试。'
        return 0
    fi
    qd_warn '将当前用户加入 input 组（需要 sudo）...'
    qd_sudo usermod -aG input "$(id -un)"
    qd_warn '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!'
    qd_warn '已加入 input 组。必须注销并重新登录图形会话后才会生效。'
    qd_warn '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!'
}

# ---- 配置合并 ---------------------------------------------------------------------

resolve_bind_address() {
    if [ -z "$BIND_ADDRESS" ]; then
        command -v tailscale >/dev/null 2>&1 \
            || qd_die '未找到 tailscale 命令。请先运行 ../tailscale/install.sh 加入 Tailnet，或用 --bind-address 显式指定绑定地址。'
        BIND_ADDRESS="$(tailscale ip -4 2>/dev/null | head -n1)"
        [ -n "$BIND_ADDRESS" ] \
            || qd_die 'tailscale 当前没有 IPv4 地址（未登录或 tailscaled 未运行）。请先完成 tailscale 登录，或用 --bind-address 显式指定。'
        qd_info "绑定地址（tailscale IPv4）: $BIND_ADDRESS"
    else
        qd_info "绑定地址（显式指定）: $BIND_ADDRESS"
    fi
    # 显式与自动两种来源都过同一道校验：防配置注入（换行/特殊字符）与非法地址
    qd_valid_ipv4 "$BIND_ADDRESS" \
        || qd_die "绑定地址不是合法 IPv4 点分地址: $BIND_ADDRESS"
}

# 读取配置里的基准端口（port 键，Sunshine 语义：默认 47989，Web UI = base+1）。
# 不存在 → 默认；存在但非数字/越界 → 拒绝继续（不能拿着猜出来的 URL 声称成功）。
resolve_web_ui_port() {
    local base
    base="$(qd_conf_get "$CONFIG_FILE" port 2>/dev/null || true)"
    if [ -z "$base" ]; then
        base="$DEFAULT_BASE_PORT"
    elif ! [[ "$base" =~ ^[0-9]+$ ]] \
        || [ "$base" -lt 1029 ] || [ "$base" -gt 65514 ]; then
        qd_die "配置 $CONFIG_FILE 里的 port 不是 Sunshine 合法基准端口（1029-65514）: '$base'。请手工修正后重跑。"
    fi
    WEB_UI_PORT=$((base + 1))
}

configure_sunshine() {
    mkdir -p "$CONFIG_DIR"
    if [ -e "$CONFIG_FILE" ] && [ ! -f "$CONFIG_FILE" ]; then
        qd_die "配置路径存在但不是普通文件: $CONFIG_FILE"
    fi
    if [ -f "$CONFIG_FILE" ] && [ ! -r "$CONFIG_FILE" ]; then
        qd_die "现有配置不可读，拒绝覆盖: $CONFIG_FILE"
    fi
    resolve_web_ui_port
    local origin="https://$BIND_ADDRESS:$WEB_UI_PORT"
    local before_hash='' after_hash curmode existing_capture
    local backup="$CONFIG_FILE.bak"
    CONF_BEFORE=''
    if [ -f "$CONFIG_FILE" ]; then
        before_hash="$(sha256sum "$CONFIG_FILE" | awk '{print $1}')"
        qd_mktemp_file CONF_BEFORE
        cp -p "$CONFIG_FILE" "$CONF_BEFORE"
    fi

    qd_conf_set "$CONFIG_FILE" upnp disabled
    # capture 语义：自动侦测 = 配置里完全没有 capture 键。
    #   --capture auto   → 删除所有 capture 行，交给 Sunshine 自动侦测
    #   --capture kms/portal/x11 → 显式写入（portal 在 v2026.516 源码中仍是合法后端）
    #   默认（不传）      → 保留合法值；发现 xcb/auto/未知等无效值时警告并移除，
    #                        绝不留着无效值还声称收敛成功（doctor 会把这些标为失败）
    case "$CAPTURE" in
        auto)
            if qd_conf_get "$CONFIG_FILE" capture >/dev/null 2>&1; then
                qd_conf_unset "$CONFIG_FILE" capture
                qd_info 'capture=auto：已移除 capture 键，由 Sunshine 自动侦测'
            fi ;;
        '' )
            existing_capture="$(qd_conf_get "$CONFIG_FILE" capture 2>/dev/null || true)"
            case "$existing_capture" in
                '') : ;;
                kms|portal|x11)
                    qd_info "保留已有 capture = $existing_capture" ;;
                *)
                    qd_warn "现有 capture = $existing_capture 无效（xcb 是旧名；auto 应为空键而非显式值）"
                    qd_warn '已移除 capture 键，改由 Sunshine 自动侦测（等价于 --capture auto）'
                    qd_conf_unset "$CONFIG_FILE" capture ;;
            esac ;;
        *)
            qd_conf_set "$CONFIG_FILE" capture "$CAPTURE" ;;
    esac
    qd_conf_set "$CONFIG_FILE" bind_address "$BIND_ADDRESS"
    qd_conf_ensure_token "$CONFIG_FILE" csrf_allowed_origins "$origin"

    # sunshine.conf 可能含敏感路径/设置：权限收紧到 0600；已有更严权限（如 0400）则保留
    curmode="$(stat -c %a "$CONFIG_FILE")"
    if [ $((8#$curmode & ~8#600)) -ne 0 ]; then
        chmod 600 "$CONFIG_FILE"
        qd_info '已将 sunshine.conf 权限收紧为 0600'
    fi

    # 回读验证
    [ "$(qd_conf_get "$CONFIG_FILE" upnp)" = disabled ] || qd_die '配置回读失败: upnp'
    [ "$(qd_conf_get "$CONFIG_FILE" bind_address)" = "$BIND_ADDRESS" ] || qd_die '配置回读失败: bind_address'
    qd_conf_get "$CONFIG_FILE" csrf_allowed_origins | tr ',' '\n' | sed 's/^[ \t]*//;s/[ \t]*$//' \
        | grep -qx "$origin" || qd_die '配置回读失败: csrf_allowed_origins'
    case "$CAPTURE" in
        auto)
            ! qd_conf_get "$CONFIG_FILE" capture >/dev/null 2>&1 || qd_die '配置回读失败: capture 应已移除' ;;
        '')
            case "$existing_capture" in
                ''|kms|portal|x11) : ;;
                *) ! qd_conf_get "$CONFIG_FILE" capture >/dev/null 2>&1 || qd_die '配置回读失败: 无效 capture 应已移除' ;;
            esac ;;
        *)
            [ "$(qd_conf_get "$CONFIG_FILE" capture)" = "$CAPTURE" ] || qd_die '配置回读失败: capture' ;;
    esac

    # 变更检测：内容没变 → 不备份、不重启服务；变了 → 滚动单份备份（.bak，避免 .bak.时间戳 无限堆积）
    after_hash="$(sha256sum "$CONFIG_FILE" | awk '{print $1}')"
    if [ "$before_hash" != "$after_hash" ]; then
        CONFIG_CHANGED=true
        if [ -n "$before_hash" ]; then
            cp -p "$CONF_BEFORE" "$backup"
            chmod 600 "$backup"
            qd_info "配置有变更，修改前内容已滚动备份到: $backup"
        fi
        qd_info "配置已收敛（未知键原样保留）: $CONFIG_FILE"
    else
        qd_info "配置无变化（已收敛）: $CONFIG_FILE"
    fi
    qd_info "  upnp=disabled, bind_address=$BIND_ADDRESS, csrf_allowed_origins 含 $origin, Web UI 端口 $WEB_UI_PORT（基准端口 $((WEB_UI_PORT - 1))）"
}

# ---- 用户服务 ---------------------------------------------------------------------

detect_unit() {
    local units
    units="$(systemctl --user list-unit-files --no-legend --no-pager 2>/dev/null || true)"
    if grep -q "^$CANONICAL_UNIT" <<<"$units" || [ -f "$SYSTEMD_USER_DIR/$CANONICAL_UNIT" ]; then
        printf '%s\n' "$CANONICAL_UNIT"
    elif grep -q "^$ALIAS_UNIT" <<<"$units" || [ -f "$SYSTEMD_USER_DIR/$ALIAS_UNIT" ]; then
        printf '%s\n' "$ALIAS_UNIT"
    else
        return 1
    fi
}

enable_service() {
    local unit
    # The package installs a user unit after the user manager may already have
    # cached its search path. Reload before discovery/start so a first install
    # does not depend on logging out or restarting the user manager.
    systemctl --user daemon-reload \
        || qd_die 'systemctl --user daemon-reload 失败；无法加载新安装的 Sunshine 用户服务'
    unit="$(detect_unit)" || qd_die "未找到 Sunshine 用户服务单元（$CANONICAL_UNIT 或别名 $ALIAS_UNIT）；请确认 deb 安装完整"
    if systemctl --user is-active --quiet "$unit"; then
        # 已在运行：enable --now 是 no-op，配置若变了必须 try-restart 才会生效；
        # 配置没变则不动服务（幂等重跑不打扰正在进行的串流会话）。
        systemctl --user enable "$unit" >/dev/null \
            || qd_die "无法为当前用户启用 $unit"
        systemctl --user is-enabled --quiet "$unit" \
            || qd_die "$unit 执行 enable 后仍不是 enabled"
        if [ "$CONFIG_CHANGED" = true ]; then
            qd_info "$unit 已在运行且配置有变更，执行 try-restart 使新配置生效"
            systemctl --user try-restart "$unit" \
                || qd_die "systemctl --user try-restart $unit 失败。查看日志: journalctl --user -u $unit -e"
            systemctl --user is-active --quiet "$unit" \
                || qd_die "$unit 重启后不是 active。查看日志: journalctl --user -u $unit -e"
            qd_info "$unit 已重启并保持 active，新配置已生效"
        else
            qd_info "$unit 已在运行且配置无变化，不重启"
        fi
        return 0
    fi
    qd_info "启用并启动用户服务: $unit（不使用 sudo，写入当前用户的 systemd）"
    if ! systemctl --user enable --now "$unit"; then
        qd_die "systemctl --user enable --now $unit 失败。
常见原因: 当前没有活动的用户 systemd 会话（例如纯 SSH 且从未登录图形界面）。
注意: Sunshine 需要本机存在已登录的图形用户会话才能捕获桌面；
不要用 loginctl enable-linger 当作无人值守/预登录方案——Sunshine 不在那种模式下工作。"
    fi
    systemctl --user is-enabled --quiet "$unit" \
        || qd_die "$unit 启动后不是 enabled"
    if ! systemctl --user is-active --quiet "$unit"; then
        qd_die "$unit 启动后不是 active。查看日志: journalctl --user -u $unit -e
注意: Sunshine 需要已登录的图形用户会话；enable-linger 不是替代方案。"
    fi
    qd_info "$unit 已 enabled 且 active"
}

# ---- 主流程 ------------------------------------------------------------------------

main() {
    parse_args "$@"
    qd_require_not_root
    qd_require_ubuntu
    enforce_version_floor

    local arch
    arch="$(dpkg --print-architecture)"
    case "$arch" in
        amd64|arm64) ;;
        *) qd_die "官方 release 仅提供 amd64/arm64 的 Ubuntu 24.04 .deb；当前架构: $arch" ;;
    esac

    resolve_bind_address   # 先解析，避免下载后才失败
    install_package "$arch"
    converge_caps
    converge_input_access
    configure_sunshine
    enable_service

    qd_section '完成'
    cat <<EOF_DONE
Sunshine 主机已就绪。
  Web UI（绑定到所选地址）: https://$BIND_ADDRESS:${WEB_UI_PORT}
  首次使用请在 Web UI 设置用户名/密码（凭据只落本机，不进入本仓库）。
  Moonlight 客户端添加主机地址: $BIND_ADDRESS，配对 PIN 在 Web UI 的 PIN 页面输入。
提醒: Sunshine 需要本机保持已登录的图形会话；锁屏/睡眠/DPMS 设置本脚本未做任何改动。
EOF_DONE
}

main "$@"
