#!/bin/bash
# Clash Verge local configuration maintenance dispatcher.

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RULES_DIR="${RULES_DIR:-$SCRIPT_DIR/rules}"
RULES_READER="$SCRIPT_DIR/lib/rules.py"
APP_DISCOVERER="$SCRIPT_DIR/lib/discover_apps.py"
CLASH_DIR="${CLASH_DIR:-$HOME/.local/share/io.github.clash-verge-rev.clash-verge-rev}"
PROFILES_YAML="$CLASH_DIR/profiles.yaml"
MIHOMO_SOCKET="${MIHOMO_SOCKET:-/tmp/verge/verge-mihomo.sock}"

# shellcheck source=lib/config.sh
source "$SCRIPT_DIR/lib/config.sh"
# shellcheck source=lib/diagnose.sh
source "$SCRIPT_DIR/lib/diagnose.sh"
# shellcheck source=lib/ssh.sh
source "$SCRIPT_DIR/lib/ssh.sh"

show_menu() {
    echo ""
    echo "=========================================="
    echo "  Clash Verge 优化工具 - 主菜单"
    echo "=========================================="
    echo ""
    echo "  1. 一键优化 Clash 配置 (推荐)"
    echo "  2. 配置 SSH for GitHub (可选)"
    echo "  3. 查看会读取/修改的配置文件"
    echo "  4. 备份管理"
    echo "  0. 退出"
    echo ""
    echo "=========================================="
    echo -n "请选择 [0-4]: "
}


usage() {
    cat <<'EOF'
用法:
  ./tun-fix.sh                 打开完整维护菜单（会修改 Merge/DNS/TUN/SSH）
  ./tun-fix.sh rules check     校验 rules/direct.yaml 和 rules/proxy.yaml
  ./tun-fix.sh rules render    将自包含全局 Script.js 输出到 stdout，不读取 Verge 配置
  ./tun-fix.sh rules apply     只生成并替换已登记的全局 Script.js；随后在 Verge 中重载
  ./tun-fix.sh apps discover [ID ...]
                                    只读盘点已支持应用；不启动应用或改动 Clash
  ./tun-fix.sh --help          显示本帮助
EOF
}

# 主程序
main() {
    require_profiles
    MERGE_CONFIG=$(get_merge_config)
    PROFILE_NAME=$(get_profile_name)

    echo ""
    echo "当前订阅: $PROFILE_NAME"
    echo "Merge 配置: $(basename "$MERGE_CONFIG")"

    while true; do
        show_menu
        read -r choice

        case $choice in
            1)
                optimize_all "$MERGE_CONFIG"
                ;;
            2)
                configure_ssh
                ;;
            3)
                show_config_paths
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
