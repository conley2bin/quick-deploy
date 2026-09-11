#!/bin/bash
# quick-deploy/sunshine-moonlight/uninstall.sh
# 只移除本目录脚本创建/拥有的内容；外来文件一律保留并说明。
#
# 默认保护：
#   - 有效 Sunshine 配置目录（默认 ~/.config/sunshine）默认保留；
#     只有显式 --destroy-host-state 才删除。
#   - sunshine 包：仅当 install-host.sh 的归属记录证明它由本脚本首次引入时才允许移除；
#     预先存在的 Sunshine 一律拒绝移除，除非再加 --force-remove-preexisting-package。

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

OWNERSHIP_MARK='quick-deploy/sunshine-moonlight'
OPT_DIR="$HOME/.local/opt/moonlight"
WRAPPER="$HOME/.local/bin/moonlight"
DESKTOP_FILE="$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"

# 测试专用钩子（tests/run.sh 使用；真实运行不要设置）
CONFIG_DIR=''
STATE_DIR="${QD_HOST_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/quick-deploy/sunshine-moonlight}"
STATE_FILE="$STATE_DIR/host.state"

DO_CLIENT=false
DO_HOST_PACKAGE=false
DESTROY_HOST_STATE=false
FORCE_PREEXISTING=false

usage() {
    cat <<USAGE
用法: ./uninstall.sh <动作> [选项]

不带参数不执行任何操作。

动作:
  --client                           移除 quick-deploy 安装的 Moonlight 客户端文件
  --host-package                     apt remove sunshine（仅限本脚本引入的安装）
  --destroy-host-state               停止并禁用服务，删除有效 Sunshine 配置目录（不可恢复）

选项:
  --force-remove-preexisting-package  确认移除「并非本脚本引入」的 sunshine 包
  -h, --help                          显示帮助
USAGE
}

parse_args() {
    [ "$#" -gt 0 ] || { usage; exit 2; }
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --client) DO_CLIENT=true ;;
            --host-package) DO_HOST_PACKAGE=true ;;
            --destroy-host-state) DESTROY_HOST_STATE=true ;;
            --force-remove-preexisting-package) FORCE_PREEXISTING=true ;;
            -h|--help) usage; exit 0 ;;
            *) qd_die "未知参数: $1" ;;
        esac
        shift
    done
}

remove_client() {
    qd_section '移除 Moonlight 客户端（仅限 quick-deploy 拥有的文件）'
    local removed_any=false

    if [ -e "$WRAPPER" ]; then
        if grep -q "$OWNERSHIP_MARK" "$WRAPPER" 2>/dev/null; then
            rm -f "$WRAPPER"; removed_any=true
            qd_info "已移除启动包装: $WRAPPER"
        else
            qd_warn "保留外来文件（无本脚本标记）: $WRAPPER"
        fi
    fi

    if [ -e "$DESKTOP_FILE" ]; then
        if grep -q "$OWNERSHIP_MARK" "$DESKTOP_FILE" 2>/dev/null; then
            rm -f "$DESKTOP_FILE"; removed_any=true
            qd_info "已移除桌面项: $DESKTOP_FILE"
        else
            qd_warn "保留外来文件（无本脚本标记）: $DESKTOP_FILE"
        fi
    fi

    if [ -d "$OPT_DIR" ]; then
        local dir foreign=false
        for dir in "$OPT_DIR"/*/; do
            [ -d "$dir" ] || continue
            if [ -f "$dir/.quick-deploy-sha256" ]; then
                rm -rf "$dir"; removed_any=true
                qd_info "已移除安装目录: $dir"
            else
                foreign=true
                qd_warn "保留外来目录（缺少 quick-deploy 标记）: $dir"
            fi
        done
        # 隐藏目录：本脚本的暂存/回滚残留是 .staging-*/.backup-*（带标记），普通 glob 看不到，
        # 需要显式处理；不带标记的隐藏目录一律视为外来并保留。
        for dir in "$OPT_DIR"/.[!.]*/; do
            [ -d "$dir" ] || continue
            if [ -f "$dir/.quick-deploy-sha256" ]; then
                rm -rf "$dir"; removed_any=true
                qd_info "已移除受管的隐藏暂存/备份目录: $dir"
            else
                foreign=true
                qd_warn "保留外来隐藏目录（缺少 quick-deploy 标记）: $dir"
            fi
        done
        if [ "$foreign" = false ]; then
            if rmdir "$OPT_DIR" 2>/dev/null; then
                qd_info "已移除空目录: $OPT_DIR"
            fi
        else
            qd_warn "$OPT_DIR 内含外来内容，目录保留"
        fi
    fi

    [ "$removed_any" = true ] || qd_info '没有可移除的 quick-deploy 客户端文件'
}

remove_host_package() {
    qd_section '移除 sunshine 包'
    if ! dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null | grep -qx installed; then
        stop_host_service
        qd_remove_retry "$CONFIG_DIR"
        rm -f "$STATE_FILE" "$STATE_DIR/last-install"
        local remaining
        remaining="$(qd_sunshine_processes)" || qd_die '无法查询残留 Sunshine 实例'
        [ -z "$remaining" ] || qd_die "包已不在 installed 状态，但仍有 Sunshine 实例（未终止）：$remaining"
        qd_info 'sunshine 包未安装；已清理过期归属记录'
        return 0
    fi

    local preexisting='unknown'
    if [ -f "$STATE_FILE" ]; then
        preexisting="$(qd_conf_get "$STATE_FILE" package_preexisting 2>/dev/null || echo unknown)"
    fi

    case "$preexisting" in
        false)
            qd_info '归属记录显示 sunshine 由本脚本首次引入，执行移除。' ;;
        true)
            if [ "$FORCE_PREEXISTING" = true ]; then
                qd_warn '归属记录显示 sunshine 在本脚本介入前已存在；你使用了 --force-remove-preexisting-package，按你的要求移除。'
            else
                qd_die '拒绝移除: sunshine 包在本脚本介入前就已存在（见归属记录 '"$STATE_FILE"'）。
如确认要移除，请追加 --force-remove-preexisting-package。配置目录不受影响。'
            fi ;;
        *)
            if [ "$FORCE_PREEXISTING" = true ]; then
                qd_warn '无归属记录，无法证明 sunshine 由本脚本引入；你使用了 --force-remove-preexisting-package，按你的要求移除。'
            else
                qd_die "拒绝移除: 无归属记录（$STATE_FILE 不存在），无法证明 sunshine 由本脚本引入。
如确认要移除，请追加 --force-remove-preexisting-package。配置目录不受影响。"
            fi ;;
    esac

    stop_host_service
    qd_remove_retry "$CONFIG_DIR"
    qd_sudo apt-get remove -y sunshine
    if dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null | grep -qx installed; then
        qd_die 'apt remove 返回后 sunshine 仍处于 installed 状态；保留归属记录以便重试'
    fi
    # Clear ownership as soon as package absence is verified, even if reload later fails.
    rm -f "$STATE_FILE" "$STATE_DIR/last-install"
    rmdir "$STATE_DIR" 2>/dev/null || true
    systemctl --user daemon-reload || qd_die '移除包后用户管理器 reload 失败'
    qd_info "sunshine 包已移除，归属记录已清理；配置目录 $CONFIG_DIR 保留。"
}

stop_host_service() {
    local unit remaining
    unit="$(qd_find_unit || true)"
    if [ -n "$unit" ]; then
        systemctl --user disable --now "$unit" || qd_die "无法停止/禁用 $unit；未删除包或状态"
        if systemctl --user is-active --quiet "$unit"; then qd_die "$unit 仍 active；拒绝删除"; fi
        if systemctl --user is-enabled --quiet "$unit"; then qd_die "$unit 仍 enabled；拒绝删除"; fi
    fi
    remaining="$(qd_sunshine_processes)" || qd_die '无法查询残留 Sunshine 进程；拒绝删除'
    [ -z "$remaining" ] || qd_die "仍有 Sunshine 实例运行（不会终止其他用户/手动进程）：
$remaining
请由进程所有者停止后重跑。"
}

destroy_host_state() {
    qd_section '删除 Sunshine 主机状态'
    stop_host_service
    qd_remove_retry "$CONFIG_DIR"
    qd_warn "即将删除 $CONFIG_DIR（不可恢复）；目录外的凭据不在删除范围。服务已停止并禁用，再次使用前请重跑 install-host.sh"
    if [ ! -d "$CONFIG_DIR" ]; then
        qd_info '配置目录不存在，无需删除'
        return 0
    fi
    rm -rf "$CONFIG_DIR"
    [ ! -e "$CONFIG_DIR" ] || qd_die "删除失败: $CONFIG_DIR"
    qd_warn "已删除: $CONFIG_DIR"
}

main() {
    parse_args "$@"
    qd_require_not_root
    if [ "$DO_HOST_PACKAGE" = true ] || [ "$DESTROY_HOST_STATE" = true ]; then
        local selected_config
        selected_config="$(qd_host_config_path)" || exit 1
        CONFIG_DIR="$(qd_host_config_dir)" || exit 1
        if [ "$DESTROY_HOST_STATE" = true ] && [ -L "${selected_config%/}" ]; then
            qd_die "拒绝删除符号链接配置目录 $selected_config：它指向 $CONFIG_DIR，递归删除会移除目标内容。请先人工核对；服务未停止"
        fi
        qd_check_service_config "$CONFIG_DIR" || qd_die '有效服务配置路径不明确；未执行卸载'
        [ "$CONFIG_DIR" != / ] && [ "$CONFIG_DIR" != "$HOME" ] || qd_die '拒绝删除根目录或 HOME'
    fi
    [ "$DO_CLIENT" = true ] && remove_client
    [ "$DO_HOST_PACKAGE" = true ] && remove_host_package
    [ "$DESTROY_HOST_STATE" = true ] && destroy_host_state
    qd_info '卸载流程结束。'
}

main "$@"
