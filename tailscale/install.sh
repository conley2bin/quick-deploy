#!/bin/bash
# Install or update Tailscale from its official stable Ubuntu APT repository.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
KEYRING_FILE="${TAILSCALE_KEYRING_FILE:-/usr/share/keyrings/tailscale-archive-keyring.gpg}"
SOURCE_LIST_FILE="${TAILSCALE_SOURCE_LIST_FILE:-/etc/apt/sources.list.d/tailscale.list}"
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
用法: ./install.sh [--no-clash-check]

安装或更新 Ubuntu 24.04+ 的 Tailscale stable 包，只请求 tailscale 目标包。
包和 tailscaled.service 就绪后，先检测/处理 Clash Verge TUN 代理，再判断首次登录。
已登录节点不会重新认证；未登录时交互终端会询问是否前台执行一次 sudo tailscale up。

--no-clash-check  跳过登录前的 Clash Verge TUN 自动检测
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
# A first login needs the control plane, so apply/prove the Clash proxy first.
if [ "$run_clash_check" = true ]; then
    "$SCRIPT_DIR/configure-clash-proxy.sh" --auto
fi
handle_login
