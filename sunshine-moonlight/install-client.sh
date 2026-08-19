#!/bin/bash
# quick-deploy/sunshine-moonlight/install-client.sh
# 在 Ubuntu 24.04+ x86_64 上安装 Moonlight 客户端（官方 AppImage，解包模式）。
#
# 为什么解包而不是直接运行 AppImage：Ubuntu 24.04 默认只有 fuse3，
# 直接运行 AppImage 会报 dlopen(): error loading libfuse.so.2。
# --appimage-extract 不依赖 FUSE；解包后的 squashfs-root 直接可运行。
# 因此本脚本不引入、也不要求安装 libfuse2。
#
# 不使用 Snap、不使用 Flatpak。
# 只支持固定版本 v6.1.0：用脚本内置的 SHA-256 与精确字节数双重校验，全部通过才落盘。
# 为什么不接受其它 --version：GitHub release v6.1 的 API asset digest 字段为 null，
# “动态查 API 拿 digest”这条路既不可用也不如固定值安全——因此只有固定值一条信任路径。
# 升级 = 手工把新版本的 sha256/大小固化进下方常量（见 README“升级”节）。
#
# 归属纪律：拒绝覆盖任何“外来”资产（无 quick-deploy 标记的启动包装、桌面项、
# 目标版本目录、暂存目录），而不是先静默备份再让 uninstall 误删。

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

GITHUB_REPO='moonlight-stream/moonlight-qt'
DEFAULT_VERSION='v6.1.0'

# 固定校验值（v6.1.0 官方 AppImage）。升级版本时必须同步更新这两个常量。
# 测试专用钩子（tests/run.sh 使用；真实运行不要设置）：
#   QD_TEST_MOONLIGHT_SHA256 / QD_TEST_MOONLIGHT_SIZE —— 覆盖固定摘要与大小
PINNED_SHA256="${QD_TEST_MOONLIGHT_SHA256:-0e855ffd22d407e18ab5fdb575fed5f01ca119a3f91993c5f0213f15ac80b400}"
PINNED_SIZE="${QD_TEST_MOONLIGHT_SIZE:-55325888}"

MOONLIGHT_VERSION="$DEFAULT_VERSION"

OPT_DIR="$HOME/.local/opt/moonlight"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
MARKER_NAME='.quick-deploy-sha256'
OWNERSHIP_MARK='# Managed by quick-deploy/sunshine-moonlight/install-client.sh'
OWNERSHIP_GREP='quick-deploy/sunshine-moonlight'

usage() {
    cat <<USAGE
用法: ./install-client.sh [选项]

安装 Moonlight 客户端（官方 AppImage 解包到 ~/.local/opt/moonlight/<版本>）。
不要用 root/sudo 运行本脚本。不使用 Snap/Flatpak，不依赖 libfuse2。

选项:
  --version TAG   只接受固定版本 $DEFAULT_VERSION（内置 SHA-256+大小双重校验）。
                  其它版本一律拒绝：API digest 不可用且强度不够；
                  升级请手工核对后更新脚本内的 PINNED_SHA256/PINNED_SIZE。
  -h, --help      显示帮助
USAGE
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                [ "$#" -ge 2 ] || qd_die '--version 需要一个参数'
                [[ "$2" =~ ^v[0-9]+(\.[0-9]+)*$ ]] \
                    || qd_die "--version 标签格式非法: $2（期望形如 v6.1.0，仅 v+数字+点）"
                MOONLIGHT_VERSION="$2"; shift ;;
            -h|--help) usage; exit 0 ;;
            *) qd_die "未知参数: $1（-h 查看用法）" ;;
        esac
        shift
    done
    [ "$MOONLIGHT_VERSION" = "$DEFAULT_VERSION" ] \
        || qd_die "拒绝安装非固定版本 $MOONLIGHT_VERSION：本脚本只信任内置固定校验值（$DEFAULT_VERSION）。
GitHub API 对该 release 的 asset digest 为 null，动态校验既不可用也不如固定值安全。
升级流程：手工下载核对新版本 sha256/大小后，更新脚本顶部 PINNED_SHA256/PINNED_SIZE 常量。"
}

# ---- 下载与校验 -------------------------------------------------------------------

download_appimage() {
    local out="$1" ver_num="${MOONLIGHT_VERSION#v}"
    qd_require_cmd curl curl
    qd_require_cmd sha256sum coreutils

    qd_curl -o "$out" \
        "https://github.com/$GITHUB_REPO/releases/download/$MOONLIGHT_VERSION/Moonlight-$ver_num-x86_64.AppImage" \
        || qd_die '下载失败；未对系统做任何修改'
    local size
    size="$(stat -c %s "$out")"
    [ "$size" = "$PINNED_SIZE" ] \
        || qd_die "文件大小不符（期望 $PINNED_SIZE，实际 $size）；已放弃，未做任何修改"
    qd_info "大小校验通过: $size 字节"

    qd_verify_sha256 "$out" "$PINNED_SHA256"
    EXPECTED_SHA="$PINNED_SHA256"
}

# ---- 外来资产拒止（在任何下载/修改之前执行） -----------------------------------------

# 外来 = 已存在但无 quick-deploy 归属标记。对这类资产一律拒绝并退出，
# 而不是覆盖（覆盖会毁掉用户原有文件，还会让 uninstall 把它误当受管文件删掉）。
refuse_foreign_file() { # PATH 描述
    local path="$1" desc="$2"
    [ -e "$path" ] || return 0
    if grep -q "$OWNERSHIP_GREP" "$path" 2>/dev/null; then
        return 0   # 受管文件：允许收敛覆盖
    fi
    qd_die "拒绝覆盖外来$desc: $path（无 quick-deploy 归属标记）。
请先自行备份并移除该文件后重跑；本脚本尚未做任何修改。"
}

check_no_foreign_assets() {
    local ver_num="${MOONLIGHT_VERSION#v}"
    refuse_foreign_file "$BIN_DIR/moonlight" '启动包装'
    refuse_foreign_file "$APP_DIR/com.moonlight_stream.Moonlight.desktop" '桌面项'
    if [ -e "$OPT_DIR/$ver_num" ] && [ ! -f "$OPT_DIR/$ver_num/$MARKER_NAME" ]; then
        qd_die "拒绝覆盖外来目标目录: $OPT_DIR/$ver_num（缺少 $MARKER_NAME 归属标记）。
请确认它不是你自己安装/构建的 Moonlight；如确认无用请手工移除后重跑。本脚本尚未做任何修改。"
    fi
    local stale
    for stale in "$OPT_DIR"/.staging-"$ver_num".* "$OPT_DIR"/.backup-"$ver_num".*; do
        [ -e "$stale" ] || continue
        qd_die "发现上次安装中断/回滚残留的目录: $stale。
为避免误删，请人工检查其内容后自行移除（或运行 ./uninstall.sh --client，带标记的残留会被清掉），再重跑。"
    done
}

# ---- 解包安装（原子替换 + 受管目录回滚） ----------------------------------------------

extract_and_install() {
    local appimage="$1" ver_num="${MOONLIGHT_VERSION#v}"
    local target="$OPT_DIR/$ver_num"
    local extract_tmp staged backup
    qd_mktemp_dir extract_tmp
    chmod +x "$appimage"

    qd_info '解包 AppImage（--appimage-extract，不需要 FUSE/libfuse2）...'
    if ! (cd "$extract_tmp" && "$appimage" --appimage-extract >/dev/null); then
        qd_die 'AppImage 解包失败；未安装任何内容'
    fi
    [ -d "$extract_tmp/squashfs-root" ] || qd_die '解包结果缺少 squashfs-root；未安装任何内容'
    [ -e "$extract_tmp/squashfs-root/AppRun" ] || qd_die '解包结果缺少 AppRun；未安装任何内容'

    mkdir -p "$OPT_DIR"
    staged="$OPT_DIR/.staging-$ver_num.$$"
    mv "$extract_tmp/squashfs-root" "$staged"
    # 归属标记在晋级为正式目录之前就写入：中断残留的 .staging-* 因此可被 uninstall 安全识别
    printf '%s\n' "$EXPECTED_SHA" >"$staged/$MARKER_NAME"

    # 原子替换 + 回滚：受管的旧版本先换名为备份，新版本晋级失败则恢复原版本
    backup=''
    if [ -e "$target" ]; then
        backup="$OPT_DIR/.backup-$ver_num.$$"
        mv "$target" "$backup"
    fi
    if mv "$staged" "$target"; then
        [ -z "$backup" ] || rm -rf "$backup"
    else
        [ -z "$backup" ] || mv "$backup" "$target"
        qd_die '替换安装目录失败，已回滚到原有版本'
    fi

    # 记录上游桌面文件与图标（必须在 mv 之后、以最终路径查找）
    DESKTOP_SRC="$(find "$target" -maxdepth 1 -name 'com.moonlight_stream.Moonlight.desktop' -print -quit)"
    [ -n "$DESKTOP_SRC" ] || DESKTOP_SRC="$(find "$target" -maxdepth 1 -name '*.desktop' -print -quit)"
    ICON_SRC="$(find "$target" -maxdepth 2 -name 'moonlight.svg' -print -quit)"
    [ -n "$ICON_SRC" ] || ICON_SRC="$(find "$target" -maxdepth 1 -name '.DirIcon' -print -quit)"

    qd_info "已安装到 $target"
    TARGET_DIR="$target"
}

already_converged() {
    local ver_num target expected
    ver_num="${MOONLIGHT_VERSION#v}"
    target="$OPT_DIR/$ver_num"
    [ -x "$target/AppRun" ] || return 1
    [ -f "$target/$MARKER_NAME" ] || return 1
    expected="$(cat "$target/$MARKER_NAME")"
    [ "$expected" = "$PINNED_SHA256" ]
}

# ---- 启动包装与桌面项 -----------------------------------------------------------------

install_wrapper() {
    mkdir -p "$BIN_DIR"
    local wrapper="$BIN_DIR/moonlight" tmp
    refuse_foreign_file "$wrapper" '启动包装'
    qd_mktemp_file tmp "$wrapper.qdtmp.XXXXXX"
    cat >"$tmp" <<EOF_WRAP
#!/bin/sh
$OWNERSHIP_MARK
exec "$TARGET_DIR/AppRun" "\$@"
EOF_WRAP
    chmod 755 "$tmp"
    mv -f "$tmp" "$wrapper"
    qd_info "启动包装: $wrapper -> $TARGET_DIR/AppRun"
}

install_desktop_entry() {
    [ -n "${DESKTOP_SRC:-}" ] || { qd_warn '解包内容中没有 .desktop 文件，跳过桌面项'; return 0; }
    mkdir -p "$APP_DIR"
    local dst="$APP_DIR/com.moonlight_stream.Moonlight.desktop" tmp icon_path=''
    refuse_foreign_file "$dst" '桌面项'
    qd_mktemp_file tmp
    if [ -n "${ICON_SRC:-}" ]; then
        icon_path="$TARGET_DIR/moonlight.svg"
        [ "$ICON_SRC" = "$icon_path" ] || cp "$ICON_SRC" "$icon_path"
    fi
    # 以解包出的上游桌面项为底，改写 Exec/Icon 为安装后的绝对路径
    sed -e "s|^Exec=.*|Exec=$BIN_DIR/moonlight|" \
        ${icon_path:+-e "s|^Icon=.*|Icon=$icon_path|"} \
        "$DESKTOP_SRC" >"$tmp"
    grep -q '^Exec=' "$tmp" || printf 'Exec=%s/moonlight\n' "$BIN_DIR" >>"$tmp"
    printf '%s\n' "$OWNERSHIP_MARK" >>"$tmp"
    cp "$tmp" "$dst"
    qd_info "桌面项: $dst"
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
    fi
}

main() {
    parse_args "$@"
    qd_require_not_root
    qd_require_ubuntu

    local machine
    machine="$(uname -m)"
    [ "$machine" = x86_64 ] \
        || qd_die "Moonlight 官方 AppImage 仅提供 x86_64 构建；当前架构 $machine 不受支持。
ARM 设备请改用发行版源码构建或官方 Flatpak 以外的渠道评估（本脚本不支持）。"

    qd_section "Moonlight $MOONLIGHT_VERSION (x86_64)"

    check_no_foreign_assets   # 在任何下载/写入之前拒止外来资产

    if already_converged; then
        qd_info "已安装且摘要与固定值一致，跳过下载（幂等收敛）"
        TARGET_DIR="$OPT_DIR/${MOONLIGHT_VERSION#v}"
        # 修复可能被删掉的包装/桌面项
        DESKTOP_SRC="$(find "$TARGET_DIR" -maxdepth 1 -name 'com.moonlight_stream.Moonlight.desktop' -print -quit)"
        [ -n "$DESKTOP_SRC" ] || DESKTOP_SRC="$(find "$TARGET_DIR" -maxdepth 1 -name '*.desktop' -print -quit)"
        ICON_SRC="$(find "$TARGET_DIR" -maxdepth 2 -name 'moonlight.svg' -print -quit)"
        install_wrapper
        install_desktop_entry
    else
        local appimage
        qd_mktemp_file appimage
        download_appimage "$appimage"
        extract_and_install "$appimage"
        install_wrapper
        install_desktop_entry
    fi

    qd_section '完成'
    cat <<EOF_DONE
Moonlight 已就绪。启动方式:
  命令行:  ~/.local/bin/moonlight
  桌面:    应用列表中的 Moonlight
添加主机: 在 Moonlight 中手动添加 Tailscale 地址（如 100.123.34.64），
配对 PIN 到主机 Web UI（https://<主机>:47990）的 PIN 页面完成。
EOF_DONE
}

main "$@"
