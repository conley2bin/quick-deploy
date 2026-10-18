#!/bin/bash
# Qualify an ALREADY installed Sunshine and publish a separately owned companion.
# Ordinary publication never mutates vendor/service. Explicit local RETIRE is
# separately authorized and dispatches before all download/install logic.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
PUBLISH=false
RECONCILE=false
RELEASE=false
RETIRE=false
usage() {
    cat <<'USAGE'
用法: ./commands/install-display-control.sh [--publish-shim] [--reconcile-host-identity | --reconcile-owned-release]
      ./commands/install-display-control.sh --reconcile-attempted-launch
只读资格检查已安装的 Sunshine v2026.914.233613/source63d35f7 的官方 Ubuntu deb、
实际用户服务/配置/apps/assets 和公开 serverinfo UUID；独立发布私人控制器与持久状态。
不安装/更新 Sunshine、不修改 unit/config、不 reload/restart/cancel、不收集管理员/TLS 凭据。
  --publish-shim 明确授权 sudo 原子新建 /usr/bin/quick-deploy-display-control；
                 不覆盖既有外来/不同内容路径，不在 RPC 内 sudo。
  --reconcile-host-identity 仅在主机本地 /dev/tty 输入 REBIND，明确独占、没有非受管
                 串流且已与当前 Sunshine 完成官方 PIN 配对。只接受本次预约未启动过串流的
                 身份变化受控重启，资格/旧进程退休/config/新 UUID 等完整证据均须一致；
                 保留旧记录原字节与 inode 的 .bak、锁 inode 和永久事务证据，重新发布
                 UNENROLLED。不修改 Sunshine 配置，不新增 shim 参数，不自动登记。
  --reconcile-owned-release 仅在主机本地 /dev/tty 输入 RELEASE，确认已发出唯一一次官方 quit、
                 控制器独占、无非受管/进行中的串流和并发管理编辑。只接受同 UUID/进程/配置/有效
                 收据的 releasing→owner_lost、既有正数 Desktop 与取消意图、fresh FREE/currentgame0；
                 保留原记录 .bak 与锁 inode，持久证据可精确续接，登记、收据及有效backup保留，预约revision单调递增。
                 不执行 quit、重启、配置更新或 RPC force；不能与身份重绑定同时使用。
  --reconcile-attempted-launch 独立本地 RETIRE 仪式：先正常发布完全一致的源码，
                 在主机用户终端确认已停止旧客户端/协调器/helpers、无重放发送者和并发管理，
                 明确授权唯一一次 Sunshine 用户服务 STOP→持久 STOPPED 证据→START。
                 仅接受 attested lease 的 owner_lost/已尝试/未正面观察活动 Desktop；
                 递归旧 cgroup 空、旧 birth 退休、无 jobs/activation/FDstore 后才可启动。
                 保留四原记录、历史、锁和登记；不 quit/配对/改配置、不增加 shim/RPC 动作。
                 不自动重复不确定的服务命令；无法证明下一阶段时保留阻塞。
                 不可与其它选项混用，不下载或重新资格安装 vendor。
没有该固定 shim 时仍可完成私人发布，但返回失败并明确报告 SSH RPC 尚未就绪。
用运行 Sunshine 的桌面用户操作；root 不创建其它用户的私人控制器。
安装绝不自动 enrollment。受控准备重启前必须完成官方 PIN 配对（配对才持久化实例 UUID）；
连接器会在取得预约后、准备前确认配对。空闲/独占时在主机交互终端运行：
  quick-deploy-display-control --enroll
Windows/macOS 原生 lifecycle 发布仍待实现；不会伪造 Linux schema2 设置。
USAGE
}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --publish-shim) PUBLISH=true;;
        --reconcile-host-identity) RECONCILE=true;;
        --reconcile-owned-release) RELEASE=true;;
        --reconcile-attempted-launch) RETIRE=true;;
        -h|--help) usage; exit 0;;
        *) qd_die "未知参数: $1";;
    esac
    shift
done
[ "$RECONCILE" = false ] || [ "$RELEASE" = false ] || qd_die '两种本地对账仪式不可同时选择'
[ "$RELEASE" = false ] || [ "$PUBLISH" = false ] || qd_die '受管释放对账不可发布系统 shim'
qd_require_not_root
if [ "$RETIRE" = true ]; then
    [ "$PUBLISH" = false ] && [ "$RECONCILE" = false ] && [ "$RELEASE" = false ] \
        || qd_die 'RETIRE 是独立本地仪式，不可与安装/shim/REBIND/RELEASE 混用'
    [ -x /usr/bin/python3 ] || qd_die '需要系统 /usr/bin/python3'
    exec /usr/bin/python3 -I -B "$SCRIPT_DIR/display-control/install_controller.py" reconcile-attempted-launch
fi
qd_require_ubuntu
for command in python3 curl sha256sum dpkg-deb dpkg-query systemctl busctl journalctl stat; do
    qd_require_cmd "$command"
done
[ -x /usr/bin/python3 ] && [ -x /usr/bin/dpkg-deb ] || qd_die '需要系统 /usr/bin/python3 和 /usr/bin/dpkg-deb'
arch="$(dpkg --print-architecture)"
case "$arch" in amd64|arm64) ;; *) qd_die '此伴随控制器只资格检查官方 Ubuntu amd64/arm64 deb';; esac
[ "$(dpkg-query -W -f='${db:Status-Status}' sunshine 2>/dev/null)" = installed ] \
    || qd_die 'Sunshine 尚未安装；此独立入口不会安装 vendor 包'
installed="$(dpkg-query -W -f='${Version}' sunshine)"
upstream="$(qd_upstream_version "$installed")" || qd_die '已安装 Sunshine 版本不可识别'
[ "$upstream" = 2026.914.233613 ] \
    || qd_die '独立伴随控制器仅支持 Sunshine 2026.914.233613/source63d35f7；其它/较新版本保留不降级，尚未发布资格设置'
qd_github_release_fetch LizardByte/Sunshine tags/v2026.914.233613 metadata \
    || qd_die '精确官方 release 元数据查询失败；未发布控制器资格设置'
qd_mktemp_file fields
/usr/bin/python3 -I -B "$SCRIPT_DIR/display-control/install_controller.py" asset \
    --metadata "$metadata" --ubuntu "$VERSION_ID" --architecture "$arch" >"$fields" \
    || qd_die '精确官方 package/source/URL/digest/size 不可资格确认'
mapfile -t selected <"$fields"
[ "${#selected[@]}" -eq 4 ] || qd_die '精确 release 资产解析失败'
qd_mktemp_file deb --suffix=.deb
# common.sh owns API credential hygiene and SHA verification. Its public-asset
# curl helper predates --disable-first; use the same bounded download policy
# with --disable FIRST, no ambient proxy/config or token environment, HTTPS-only
# redirects. No API authentication/header is reused on the asset/CDN request.
(
    set +x
    unset GITHUB_TOKEN GH_TOKEN QD_GITHUB_TOKEN
    curl --disable --proxy '' --noproxy '*' --proto '=https' --proto-redir '=https' \
        -fL --retry 3 --connect-timeout 20 --silent --show-error -o "$deb" "${selected[1]}"
) || qd_die '精确参考 deb 下载失败；未发布控制器设置'
[ "$(stat -c %s "$deb")" = "${selected[3]}" ] || qd_die '参考 deb 精确大小与 API 不符'
qd_verify_sha256 "$deb" "${selected[2]}"
args=(install --metadata "$metadata" --package "$deb" --ubuntu "$VERSION_ID"
      --architecture "$arch" --installed-version "$installed")
[ "$PUBLISH" = false ] || args+=(--publish-shim)
[ "$RECONCILE" = false ] || args+=(--reconcile-host-identity)
[ "$RELEASE" = false ] || args+=(--reconcile-owned-release)
# -I suppresses ambient Python path/user-site settings; -B explicitly prevents
# cache writes (-I ignores PYTHONDONTWRITEBYTECODE). No token is needed
# after metadata acquisition, including by an explicitly invoked sudo publisher.
(
    set +x
    unset GITHUB_TOKEN GH_TOKEN QD_GITHUB_TOKEN
    /usr/bin/python3 -I -B "$SCRIPT_DIR/display-control/install_controller.py" "${args[@]}"
)
