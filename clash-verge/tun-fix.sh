#!/bin/bash
# Clash Verge local routing maintenance dispatcher.
#
# Bare invocation opens one flat menu. `rules check/render/apply` and
# `apps discover` stay available as automation entrypoints.

set -e
set -o pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RULES_DIR="${RULES_DIR:-$SCRIPT_DIR/rules}"
CLASH_DIR="${CLASH_DIR:-$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev}"
RULES_READER="$SCRIPT_DIR/lib/rules.py"
APP_DISCOVERER="$SCRIPT_DIR/lib/discover_apps.py"
APP_REPORTER="$SCRIPT_DIR/lib/report_apps.py"

# Relative overrides resolve against the invocation directory, so `tun-fix.sh`
# stays usable from any cwd without hardcoded user paths.
RULES_DIR="$(CDPATH= cd -- "$RULES_DIR" 2>/dev/null && pwd || printf '%s' "$RULES_DIR")"
CLASH_DIR="$(CDPATH= cd -- "$CLASH_DIR" 2>/dev/null && pwd || printf '%s' "$CLASH_DIR")"
PROFILES_YAML="$CLASH_DIR/profiles.yaml"

# shellcheck source=lib/config.sh
source "$SCRIPT_DIR/lib/config.sh"
# shellcheck source=lib/ssh.sh
source "$SCRIPT_DIR/lib/ssh.sh"

show_menu() {
    echo ""
    echo "=========================================="
    echo "  Clash Verge 本地路由维护"
    echo "=========================================="
    echo ""
    show_route_paths
    echo ""
    echo "  1. 更新直连/代理规则（默认，回车执行）"
    echo "  2. 配置 GitHub SSH"
    echo "  3. 查看本机应用识别结果"
    echo "  4. 恢复上次规则（已登记全局 Script）"
    echo "  0. 退出"
    echo ""
    echo "=========================================="
    echo -n "请选择 [0-4，回车=1]: "
}

usage() {
    cat <<'EOF'
用法:
  ./tun-fix.sh                 打开菜单：更新规则 / GitHub SSH / 应用识别 / 恢复上次规则
  ./tun-fix.sh rules check     校验 rules/direct.yaml 和 rules/proxy.yaml 语法
  ./tun-fix.sh rules render    将解析后的全局 Script.js 输出到 stdout，不写任何文件
  ./tun-fix.sh rules apply     更新已登记的全局 Script.js（含应用发现；不改 Merge/SSH）
  ./tun-fix.sh apps discover [ID ...]
                                    只读盘点已支持应用；不启动应用或改动 Clash
  ./tun-fix.sh --help          显示本帮助

菜单是日常入口：回车等价于选项 1。更新只写已登记的全局 Script 及其同目录备份；
订阅、Merge、DNS、TUN、运行 YAML 和 ~/.ssh 都不会被这条路径改动。
生成成功后需在 Verge 中手动重载/重新生成配置。
EOF
}

# Option 3: read-only host discovery formatted for humans. Unresolved apps are
# reported as text, not as a failed action: display never counts as a failure and
# never writes or activates anything.
show_app_discovery() {
    local report
    report=$(python3 "$APP_DISCOVERER" 2>/dev/null || true)
    if [ -z "$report" ]; then
        echo "应用识别失败：无法运行 $APP_DISCOVERER" >&2
        return 1
    fi
    printf '%s\n' "$report" | python3 "$APP_REPORTER"
}

main() {
    echo ""
    echo "Clash Verge 本地路由维护"
    echo "读取来源: $RULES_DIR/direct.yaml, $RULES_DIR/proxy.yaml"
    echo ""
    local choice status=0 action=0 action_status=0
    while true; do
        show_menu
        if ! read -r choice; then
            # EOF is a clean exit, not a blank/default update request.
            echo ""
            echo "输入结束，退出。"
            return "$status"
        fi
        case $choice in
            "")
                action=1
                ;;
            1)
                action=1
                ;;
            2)
                action=2
                ;;
            3)
                action=3
                ;;
            4)
                action=4
                ;;
            0)
                exit "$status"
                ;;
            *)
                echo "无效选择: $choice（可用: 1 2 3 4 0）"
                continue
                ;;
        esac
        action_status=0
        case $action in
            1)
                # Straight-line call: neither update path disables errexit inside
                # a helper body, and a non-zero return maps to an explicit error.
                update_route_rules || action_status=$?
                report_action_status "更新失败" "$action_status"
                ;;
            2)
                configure_ssh || action_status=$?
                report_action_status "GitHub SSH 配置未完成" "$action_status"
                ;;
            3)
                show_app_discovery || action_status=$?
                report_action_status "应用识别失败" "$action_status"
                ;;
            4)
                restore_last_script || action_status=$?
                report_action_status "恢复未完成" "$action_status"
                ;;
        esac
        if [ "$action_status" -ne 0 ] && [ "$action_status" -ne 2 ]; then
            status=1
        fi
    done
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        rules)
            case "${2:-}" in
                check) check_route_sources ;;
                render) render_route_script ;;
                apply) apply_route_rules ;;
                *) usage; exit 2 ;;
            esac
            ;;
        apps)
            case "${2:-}" in
                discover) shift 2; python3 "$APP_DISCOVERER" "$@" ;;
                *) usage; exit 2 ;;
            esac
            ;;
        --help|-h) usage ;;
        "") main ;;
        *) usage; exit 2 ;;
    esac
fi
