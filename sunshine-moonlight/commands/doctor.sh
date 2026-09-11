#!/bin/bash
# Read-only checks. Never execute Sunshine: even --version can write configuration/logs.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

CHECK_HOST=false
CHECK_CLIENT=false
EXPLICIT_CLIENT=false
FAILURES=0
WARNINGS=0
ok() { printf '  [通过] %s\n' "$*"; }
warn() { printf '  [警告] %s\n' "$*"; WARNINGS=$((WARNINGS+1)); }
bad() { printf '  [失败] %s\n' "$*"; FAILURES=$((FAILURES+1)); }
usage() {
    printf '%s\n' '用法: ./commands/doctor.sh [--host] [--client]' \
        '不带参数自动检测角色；退出码 1 表示必需条件不满足，0 不代表已完成双机串流测试。'
}

run_host_checks() {
    qd_section '主机'
    local cmd dir conf unit session detail ip bind base version upstream capture family origin rc
    for cmd in python3 systemctl loginctl tailscale ip ss; do
        if ! command -v "$cmd" >/dev/null 2>&1; then bad "缺少 $cmd，无法检查主机"; return 0; fi
    done
    dir="$(qd_host_config_dir)" || { bad '无法确定配置目录'; return 0; }
    conf="$dir/sunshine.conf"
    if detail="$(qd_check_service_config "$dir" 2>&1)"; then
        ok "有效配置目录: $dir"
    else
        bad "$detail"
        # Do not draw config/listener conclusions from a possibly unused file.
        return 0
    fi
    if session="$(qd_graphical_session)"; then ok "活动本地图形会话: $session"; else bad '没有当前用户的活动本地图形会话；SSH/tty 不是桌面'; fi
    if ! detail="$(qd_check_display_environment "${session:-}" 2>&1)"; then bad "$detail"; fi

    if [ "$(dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null || true)" = installed ]; then
        version="$(dpkg-query -W -f='${Version}' sunshine)"
        if upstream="$(qd_upstream_version "$version")" && qd_version_ge "$upstream" "$QD_SUNSHINE_FLOOR"; then
            ok "已安装包 $version；上游版本达到维护基线 v$QD_SUNSHINE_FLOOR"
        else
            bad "已安装包 $version 未达到/无法验证维护基线 v$QD_SUNSHINE_FLOOR"
        fi
    else
        bad 'sunshine 包未安装'
    fi
    unit="$(qd_find_unit || true)"
    if [ -z "$unit" ]; then
        bad '未找到 Sunshine 用户服务'
    else
        if systemctl --user is-enabled --quiet "$unit"; then ok '用户服务已 enabled'; else bad '用户服务未 enabled'; fi
        if systemctl --user is-active --quiet "$unit"; then
            rc=0
            detail="$(qd_running_binary_current "$unit" 2>&1)" || rc=$?
            case "$rc" in
                0) ok '用户服务运行的 executable 与已安装包一致';;
                2) warn "$detail";;
                *) bad "$detail";;
            esac
        else
            bad "用户服务未运行；journalctl --user -u $unit -e"
        fi
    fi
    if [ ! -f "$conf" ] || [ ! -r "$conf" ]; then bad "配置文件缺失/不可读: $conf"; return 0; fi
    capture="$(qd_conf_get "$conf" capture || true)"
    if qd_valid_capture "$capture"; then
        ok "capture = ${capture:-自动选择}（可用性仍取决于桌面与驱动）"
    else
        bad "capture=$capture 无效；显式 --capture auto 删除，或选择有效后端；X11 使用 x11，不是 xcb"
    fi
    case "$capture:${session:-}" in
        x11:*'(wayland)') bad 'capture=x11 与当前 Wayland 会话不符';;
        wlr:*'(x11)'|kwin:*'(x11)') bad "capture=$capture 需要 Wayland，与当前 Xorg 会话不符";;
    esac
    if [ "$capture" = portal ]; then printf '  [信息] GNOME 46 锁屏会终止 portal 捕获，可能需要重新授权。\n'; fi
    family="$(qd_conf_get "$conf" address_family || true)"
    [ "${family:-ipv4}" = ipv4 ] || bad "address_family=$family 与本流程的 IPv4 绑定冲突"
    origin="$(qd_conf_get "$conf" origin_web_ui_allowed || true)"
    case "$origin" in
        ''|lan|wan) ;;
        pc) bad 'origin_web_ui_allowed=pc 阻止 Tailnet Web UI；同意 Tailnet 访问后改为 lan';;
        *) bad "origin_web_ui_allowed=$origin 无法识别";;
    esac
    [ "$(qd_conf_get "$conf" upnp || true)" = disabled ] || bad '本流程要求 upnp=disabled'
    ip=''
    if detail="$(qd_tailnet_ip 2>&1)"; then ip="$detail"; ok "Tailscale 在线: $ip"; else bad "$detail"; fi
    bind="$(qd_conf_get "$conf" bind_address || true)"
    if [ -z "$ip" ] || [ "$bind" != "$ip" ]; then bad "bind_address=${bind:-未设置} 不等于本机在线 Tailnet IPv4"; fi
    if base="$(qd_base_port "$conf")"; then
        origin="https://$bind:$((base+1))"
        if ! qd_conf_get "$conf" csrf_allowed_origins | tr ',' '\n' | sed 's/^[ \t]*//;s/[ \t]*$//' | grep -Fxq "$origin"; then
            bad "csrf_allowed_origins 缺少 $origin，直接访问时的配对操作可能被拒绝"
        fi
        if [ -n "$unit" ]; then
            if detail="$(qd_check_listeners "$bind" "$base" "$unit" 2>&1)"; then
                ok "$detail"
                if [[ "$detail" == *'未验证所有者'* ]]; then warn 'ss 未提供部分端口 PID；端口所有者尚未验证'; fi
            else
                bad "$detail"
            fi
        fi
    else
        bad '基准 port 无效'
    fi
    local bin caps uinput="${QD_UINPUT_NODE:-/dev/uinput}" uhid="${QD_UHID_NODE:-/dev/uhid}"
    if [ -e "$uinput" ] && [ -r "$uinput" ] && [ -w "$uinput" ]; then
        ok 'uinput 可读写（键鼠注入前提）'
    else
        bad "$uinput 缺失或无读写权限；检查 uinput 模块、包内 udev 规则、活动会话 ACL；组成员身份不能创建节点"
    fi
    if [ ! -e "$uhid" ] || [ ! -r "$uhid" ] || [ ! -w "$uhid" ]; then warn "$uhid 不可用，部分手柄功能受限"; fi
    bin="$(qd_sunshine_binary 2>/dev/null || true)"
    if [ -n "$bin" ] && command -v getcap >/dev/null 2>&1; then
        caps="$(getcap "$bin" 2>/dev/null || true)"
        if ! grep -qE 'cap_sys_(admin,cap_sys_nice|nice,cap_sys_admin)=e?p($| )' <<<"$caps"; then
            if [ "$capture" = kms ]; then bad 'KMS capability 不完整；重跑主机安装器修复'; else warn '未确认 KMS capability，自动捕获可能选择其他后端'; fi
        fi
    elif [ "$capture" = kms ]; then
        bad '无法检查 KMS capability（缺少 getcap 或 executable）'
    fi
    printf '  [信息] 实际画面、硬件编码与输入需从 Moonlight 的 Desktop 串流检查；此处不启动捕获。\n'
}

run_client_checks() {
    qd_section '客户端'
    local opt="$HOME/.local/opt/moonlight" target wrapper desktop recorded
    target="$opt/$QD_MOONLIGHT_VERSION"
    wrapper="$HOME/.local/bin/moonlight"
    desktop="$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"
    [ "$(uname -m)" = x86_64 ] || bad '固定的 AppImage 只支持 x86_64'
    if [ ! -d "$opt" ] && [ "$EXPLICIT_CLIENT" = false ]; then warn '未安装本流程的 Moonlight'; return 0; fi
    if [ ! -x "$target/AppRun" ]; then bad "缺少可执行 AppRun: $target（运行 commands/install-client.sh 修复）"; fi
    if [ -f "$target/.quick-deploy-sha256" ]; then
        recorded="$(cat "$target/.quick-deploy-sha256")"
        if [ "$recorded" = "$QD_MOONLIGHT_SHA256" ]; then
            ok "v$QD_MOONLIGHT_VERSION 的下载摘要记录符合固定值；未重新校验已解包内容"
        else
            bad '下载摘要记录不匹配；不能确认安装来源'
        fi
    else
        bad '缺少 Moonlight 下载来源记录'
    fi
    if [ -x "$wrapper" ] && grep -qF 'quick-deploy/sunshine-moonlight' "$wrapper" && grep -Fxq "exec \"$target/AppRun\" \"\$@\"" "$wrapper"; then
        ok 'CLI 包装指向固定版本'
    else
        bad 'CLI 包装缺失、外来或指向错误版本'
    fi
    if [ -f "$desktop" ] && grep -qF 'quick-deploy/sunshine-moonlight' "$desktop" && grep -Fxq "Exec=$wrapper" "$desktop"; then
        ok '桌面入口指向 CLI 包装'
    else
        bad '桌面入口缺失、外来或指向错误程序'
    fi
}

main() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --host) CHECK_HOST=true;;
            --client) CHECK_CLIENT=true; EXPLICIT_CLIENT=true;;
            -h|--help) usage; exit 0;;
            *) qd_die "未知参数: $1";;
        esac
        shift
    done
    qd_require_not_root
    qd_require_ubuntu
    if [ "$CHECK_HOST" = false ] && [ "$CHECK_CLIENT" = false ]; then
        if dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null | grep -qx installed; then CHECK_HOST=true; fi
        if [ -d "$HOME/.local/opt/moonlight" ] || [ -e "$HOME/.local/bin/moonlight" ]; then CHECK_CLIENT=true; fi
        if [ "$CHECK_HOST" = false ] && [ "$CHECK_CLIENT" = false ]; then CHECK_HOST=true; CHECK_CLIENT=true; fi
    fi
    [ "$CHECK_HOST" = false ] || run_host_checks
    [ "$CHECK_CLIENT" = false ] || run_client_checks
    printf '\n失败 %d 项，警告 %d 项。\n' "$FAILURES" "$WARNINGS"
    [ "$FAILURES" -eq 0 ]
}
main "$@"
