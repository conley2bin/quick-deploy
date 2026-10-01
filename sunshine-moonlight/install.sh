#!/bin/bash
# Install the local Sunshine host and/or Moonlight client through the moved entrypoints.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

# Isolated tests may substitute a system-Python shim; deployments always use /usr/bin/python3.
SYSTEM_PYTHON="${QD_TEST_SYSTEM_PYTHON:-/usr/bin/python3}"
INSTALL_HOST=true
INSTALL_CLIENT=true
ROLE_MODE=''
PUBLISH_DISPLAY_SHIM=false

usage() {
    cat <<USAGE
用法: ./install.sh [--host-only | --client-only] [--publish-display-control-shim]

默认依次检查最新稳定 Sunshine 与 Moonlight：缺失/较旧时更新，相同跳过载荷，本机较新不降级；主机成功后才查询客户端。
  --host-only    只安装本机 Sunshine 主机（--capture kms）
  --client-only  只安装本机 Moonlight 客户端
  -h, --help     显示帮助
  --publish-display-control-shim
                显式授权在主机伴随阶段 sudo 新建 /usr/bin/quick-deploy-display-control；不覆盖冲突

原 Ubuntu vendor 包安装/最新/不降级逻辑不变；所选 host/client 成功后，host 角色再独立资格检查并安装
显示控制伴随程序（当前仅支持官方 Sunshine 2026.914.233613/source63d35f7）。其它版本保留，伴随阶段
明确失败，不声称 vendor 阶段回滚。固定 shim 未发布时 SSH RPC 不就绪；默认不会自动提权发布它。
安装不自动 enrollment，不清除预约/receipt，不由伴随阶段 restart/reload。Windows/macOS lifecycle 发布仍待实现，
此根入口仍是 Ubuntu vendor 安装器，不是可移植的 Windows/macOS 包安装器。

所选安装阶段全部成功后，仅在模块根目录缺少 machines.yaml 时新建全字段中文占位清单；
已有普通清单原样保留（内容、权限、修改时间不变）。占位模板不含真实地址，请新建后编辑它。
旧文件名 machines.local.yaml 与其它遗留清单文件不会被读取、迁移或清理，请自行核对后手动处理。

高级主机/客户端选项请直接运行 commands/install-host.sh 或 commands/install-client.sh。
本入口不配置自动登录、电源策略或远程机器。
USAGE
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --host-only)
                [ -z "$ROLE_MODE" ] || [ "$ROLE_MODE" = host ] \
                    || qd_die '--host-only 与 --client-only 不能同时使用'
                ROLE_MODE=host
                INSTALL_CLIENT=false ;;
            --client-only)
                [ -z "$ROLE_MODE" ] || [ "$ROLE_MODE" = client ] \
                    || qd_die '--host-only 与 --client-only 不能同时使用'
                ROLE_MODE=client
                INSTALL_HOST=false ;;
            --publish-display-control-shim)
                PUBLISH_DISPLAY_SHIM=true ;;
            -h|--help) usage; exit 0 ;;
            *) qd_die "未知参数: $1（--help 查看用法）" ;;
        esac
        shift
    done
    [ "$INSTALL_HOST" = true ] || [ "$PUBLISH_DISPLAY_SHIM" = false ] \
        || qd_die '--publish-display-control-shim 只用于所选主机角色'
}

ensure_pyyaml() {
    if "$SYSTEM_PYTHON" -c 'import yaml' >/dev/null 2>&1; then
        qd_info '系统 Python 已可导入 PyYAML'
        return 0
    fi

    qd_info '系统 Python 缺少 PyYAML，安装 python3-yaml'
    qd_sudo apt-get install -y python3-yaml \
        || qd_die '无法安装 python3-yaml；未开始 Sunshine 或 Moonlight 安装'
    "$SYSTEM_PYTHON" -c 'import yaml' >/dev/null 2>&1 \
        || qd_die 'python3-yaml 安装后系统 Python 仍无法导入 yaml；未开始 Sunshine 或 Moonlight 安装'
}

run_host() {
    if "$SCRIPT_DIR/commands/install-host.sh" --capture kms; then
        return 0
    fi
    qd_die 'Sunshine 主机安装失败；Moonlight 客户端未开始。已成功完成的步骤不会自动回滚。'
}

run_client() {
    if "$SCRIPT_DIR/commands/install-client.sh"; then
        return 0
    fi
    qd_die 'Moonlight 客户端安装失败。此前成功的 Sunshine 主机安装不会自动回滚。'
}

run_display_control() {
    local -a args=()
    [ "$PUBLISH_DISPLAY_SHIM" = false ] || args+=(--publish-shim)
    if "$SCRIPT_DIR/commands/install-display-control.sh" "${args[@]}"; then
        return 0
    fi
    qd_die '所选 Sunshine/Moonlight vendor 阶段已成功，但显示控制伴随安装/资格或固定 SSH shim 未就绪；已有包和个人清单/预约状态保留。未执行清单新建，本次安装未全部完成。可单独核对 commands/install-display-control.sh --help 后重跑该阶段。'
}

# Create only after every selected stage succeeded, relative to the module
# (never the caller's cwd). The generator itself reports created versus retained;
# a refused path never receives a false install-complete message.
generate_inventory() {
    local completed
    case "$ROLE_MODE" in
        host)   completed='Sunshine 主机安装已完成' ;;
        client) completed='Moonlight 客户端安装已完成' ;;
        *)      completed='Sunshine 主机与 Moonlight 客户端安装均已完成' ;;
    esac
    "$SYSTEM_PYTHON" "$SCRIPT_DIR/lib/machines_inventory.py" "$SCRIPT_DIR/machines.yaml" \
        || qd_die "$completed，但未能新建或保留 machines.yaml（目标可能是符号链接、非普通文件、发布时出现的其它文件，或权限/写入失败）。生成器未覆盖既有目标；本次安装未全部完成，请核对后重跑 ./install.sh。"
}

main() {
    parse_args "$@"
    qd_require_not_root
    qd_require_ubuntu
    ensure_pyyaml
    [ "$INSTALL_HOST" = false ] || run_host
    [ "$INSTALL_CLIENT" = false ] || run_client
    [ "$INSTALL_HOST" = false ] || run_display_control
    generate_inventory
    qd_info '所选本机安装阶段已完成。配对和实际 Desktop 串流仍需分别验证。'
}

main "$@"
