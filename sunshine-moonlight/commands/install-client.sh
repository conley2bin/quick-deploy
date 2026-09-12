#!/bin/bash
# Install a verified Moonlight AppImage as a user-owned extracted application.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

GITHUB_REPO='moonlight-stream/moonlight-qt'
OPT_DIR="$HOME/.local/opt/moonlight"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
MARKER_NAME='.quick-deploy-sha256'
OWNERSHIP_MARK='# Managed by quick-deploy/sunshine-moonlight/install-client.sh'
RELEASE_MODE=latest
REQUESTED_TAG=''
SELECTED_TAG=''
SELECTED_NAME=''
SELECTED_URL=''
SELECTED_SHA=''
SELECTED_SIZE=''
TARGET_DIR=''
DESKTOP_SRC=''
ICON_SRC=''

usage() {
    cat <<USAGE
用法: ./commands/install-client.sh [--version v版本]

默认查询 Moonlight 最新稳定 release；缺失或较旧时安装，版本相同跳过下载，
本机较新时保留且不降级。--version 选择一个明确的稳定 tag，但同样不降级。
所有动态资产必须有 GitHub API sha256 摘要和精确大小。唯一例外是经审计的
v$QD_MOONLIGHT_VERSION AppImage；其它缺少有效摘要的 release 会在修改前停止。
USAGE
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version)
                [ "$#" -ge 2 ] && qd_valid_release_tag "$2" || qd_die '--version 需要 lowercase v+至少两个数字组件'
                REQUESTED_TAG="$2"; RELEASE_MODE=explicit; shift;;
            -h|--help) usage; exit 0;;
            *) qd_die "未知参数: $1（-h 查看用法）";;
        esac
        shift
    done
    [ "$RELEASE_MODE" != explicit ] || qd_require_release_floor "$REQUESTED_TAG" "$QD_MOONLIGHT_FLOOR" Moonlight
}

resolve_moonlight_release() {
    local json selector fields
    selector=latest
    [ "$RELEASE_MODE" != explicit ] || selector="tags/$REQUESTED_TAG"
    qd_info "查询 GitHub release: $GITHUB_REPO $selector"
    qd_github_release_fetch "$GITHUB_REPO" "$selector" json || qd_die '无法获取可信 Moonlight release 元数据；未修改客户端'
    mapfile -t fields < <(python3 - "$json" "$RELEASE_MODE" "$REQUESTED_TAG" "v$QD_MOONLIGHT_VERSION" "$QD_MOONLIGHT_SHA256" "$QD_MOONLIGHT_SIZE" <<'PY'
import json, re, sys
path, mode, requested, audited_tag, audited_sha, audited_size = sys.argv[1:]
try:
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    print(f'错误: 无法解析 GitHub release JSON: {exc}', file=sys.stderr); raise SystemExit(1)

def fail(message):
    print('错误: ' + message, file=sys.stderr); raise SystemExit(1)

tag = data.get('tag_name')
if data.get('draft') is not False or data.get('prerelease') is not False:
    fail('release 必须明确为非 draft、非 prerelease')
if not isinstance(tag, str) or not re.fullmatch(r'v[0-9]+(?:\.[0-9]+)+', tag):
    fail(f'release tag 非严格数字稳定版本: {tag!r}')
if mode == 'explicit' and tag != requested:
    fail(f'release tag 不匹配（期望 {requested}，实际 {tag}）')
assets = data.get('assets')
if not isinstance(assets, list): fail('release assets 必须是数组')
name = f'Moonlight-{tag[1:]}-x86_64.AppImage'
items = [a for a in assets if isinstance(a, dict) and a.get('name') == name]
if len(items) != 1: fail(f'找不到唯一的支持资产 {name}')
asset = items[0]
url = asset.get('browser_download_url')
expected_url = f'https://github.com/moonlight-stream/moonlight-qt/releases/download/{tag}/{name}'
if not isinstance(url, str) or url != expected_url: fail('资产 URL 不是对应 release/name 的规范 GitHub 下载 URL')
if any(ord(c) < 32 or ord(c) == 127 for c in name + url): fail('资产名称或 URL 含控制字符')
size = asset.get('size')
if not isinstance(size, int) or isinstance(size, bool) or size <= 0: fail('资产 size 必须为正整数')
digest = asset.get('digest')
valid_digest = isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest)
release_id = data.get('id'); asset_id = asset.get('id')
if valid_digest:
    sha = digest[7:].lower()
    if tag == audited_tag and name == 'Moonlight-6.1.0-x86_64.AppImage' and size == int(audited_size) and release_id == 175337682 and asset_id == 193059073 and sha != audited_sha:
        fail('审计 v6.1.0 资产的 API digest 与内置审计 SHA-256 不一致')
elif digest is None and tag == audited_tag and name == 'Moonlight-6.1.0-x86_64.AppImage' and size == int(audited_size) and release_id == 175337682 and asset_id == 193059073:
    sha = audited_sha
else:
    fail(f'资产 {name} 没有可信 sha256 digest；需要经审阅的 tag 专用校验值')
print(tag); print(name); print(url); print(sha); print(size)
PY
) || exit 1
    [ "${#fields[@]}" -eq 5 ] || qd_die 'Moonlight release 解析未返回完整资产信息'
    SELECTED_TAG="${fields[0]}"; SELECTED_NAME="${fields[1]}"; SELECTED_URL="${fields[2]}"
    SELECTED_SHA="${fields[3]}"; SELECTED_SIZE="${fields[4]}"
    qd_require_release_floor "$SELECTED_TAG" "$QD_MOONLIGHT_FLOOR" Moonlight
    qd_info "选定资产: $SELECTED_NAME"
}

check_wrapper_and_desktop_ownership() {
    local version target rc=0 wrapper="$BIN_DIR/moonlight" desktop="$APP_DIR/com.moonlight_stream.Moonlight.desktop"
    qd_client_active_version "$OPT_DIR" "$wrapper" version target || rc=$?
    case "$rc" in
        0) ACTIVE_VERSION="$version"; ACTIVE_TARGET="$target";;
        2) qd_die "拒绝覆盖外来 Moonlight 启动包装: $wrapper";;
        *) qd_die "受管 Moonlight 启动包装结构损坏: $wrapper";;
    esac
    if [ -e "$desktop" ] && ! grep -Fqx "$OWNERSHIP_MARK" "$desktop" 2>/dev/null; then
        qd_die "拒绝覆盖外来 Moonlight 桌面项: $desktop"
    fi
}

target_converged() {
    local target="$1" expected="$2" recorded
    [ -d "$target" ] && [ -x "$target/AppRun" ] && [ -f "$target/com.moonlight_stream.Moonlight.desktop" ] &&
        [ -r "$target/moonlight.svg" ] && [ -f "$target/$MARKER_NAME" ] || return 1
    recorded="$(cat "$target/$MARKER_NAME")"
    [ "$recorded" = "$expected" ]
}

require_managed_target_or_absent() {
    local target="$1"
    [ ! -e "$target" ] && return 0
    [ -f "$target/$MARKER_NAME" ] || qd_die "拒绝覆盖外来目标目录: $target（缺少 $MARKER_NAME）"
}

# Interrupted same-version promotion residue is ambiguous activation material.
# Preserve it byte-for-byte and require an explicit cleanup instead of guessing.
refuse_selected_residue() {
    local version="${SELECTED_TAG#v}" residue
    for residue in "$OPT_DIR"/.staging-"$version".* "$OPT_DIR"/.backup-"$version".*; do
        [ -e "$residue" ] || continue
        qd_die "发现未处理的 Moonlight 安装残留: $residue；为避免覆盖，请先人工核对或运行 commands/uninstall.sh --client"
    done
}

download_appimage() {
    local out="$1" size
    qd_info "下载: $SELECTED_URL"
    qd_curl -o "$out" "$SELECTED_URL" || qd_die '下载失败；未修改客户端'
    size="$(stat -c %s "$out")"
    [ "$size" = "$SELECTED_SIZE" ] || qd_die "文件大小不符（API 期望 $SELECTED_SIZE，实际 $size）；未修改客户端"
    qd_verify_sha256 "$out" "$SELECTED_SHA"
}

set_target_sources() {
    DESKTOP_SRC="$TARGET_DIR/com.moonlight_stream.Moonlight.desktop"
    ICON_SRC="$TARGET_DIR/moonlight.svg"
    [ -f "$DESKTOP_SRC" ] && [ -r "$ICON_SRC" ] || qd_die "选定 Moonlight 目标结构不完整: $TARGET_DIR"
}

extract_promote_target() {
    local appimage="$1" version target extract_tmp staged backup=''
    version="${SELECTED_TAG#v}"
    target="$OPT_DIR/$version"
    qd_mktemp_dir extract_tmp
    chmod +x "$appimage"
    qd_info '解包 AppImage（--appimage-extract，不需要 FUSE/libfuse2）...'
    (cd "$extract_tmp" && "$appimage" --appimage-extract >/dev/null) || qd_die 'AppImage 解包失败；未激活新客户端'
    [ -d "$extract_tmp/squashfs-root" ] && [ -x "$extract_tmp/squashfs-root/AppRun" ] || qd_die '解包结果缺少可执行 AppRun；未激活新客户端'
    mkdir -p "$OPT_DIR"
    staged="$OPT_DIR/.staging-$version.$$"
    mv "$extract_tmp/squashfs-root" "$staged"
    [ -f "$staged/com.moonlight_stream.Moonlight.desktop" ] && [ -r "$staged/moonlight.svg" ] || qd_die '解包结果缺少需要的桌面元数据；未激活新客户端'
    printf '%s\n' "$SELECTED_SHA" >"$staged/$MARKER_NAME"
    if [ -e "$target" ]; then
        [ -f "$target/$MARKER_NAME" ] || qd_die "拒绝替换外来目标目录: $target"
        backup="$OPT_DIR/.backup-$version.$$"
        mv "$target" "$backup"
    fi
    if ! mv "$staged" "$target"; then
        [ -z "$backup" ] || mv "$backup" "$target"
        qd_die '替换 Moonlight 目标目录失败，已恢复原目录'
    fi
    [ -z "$backup" ] || rm -rf "$backup"
    TARGET_DIR="$target"
    set_target_sources
}

install_wrapper() {
    local wrapper="$BIN_DIR/moonlight" tmp
    mkdir -p "$BIN_DIR"
    qd_mktemp_file tmp "$wrapper.qdtmp.XXXXXX"
    cat >"$tmp" <<EOF_WRAP
#!/bin/sh
$OWNERSHIP_MARK
exec "$TARGET_DIR/AppRun" "\$@"
EOF_WRAP
    chmod 755 "$tmp"
    mv -f "$tmp" "$wrapper"
}

install_desktop_entry() {
    local dst="$APP_DIR/com.moonlight_stream.Moonlight.desktop" tmp
    mkdir -p "$APP_DIR"
    qd_mktemp_file tmp "$dst.qdtmp.XXXXXX"
    sed -e "s|^Exec=.*|Exec=$BIN_DIR/moonlight|" -e "s|^Icon=.*|Icon=$TARGET_DIR/moonlight.svg|" "$DESKTOP_SRC" >"$tmp"
    grep -q '^Exec=' "$tmp" || printf 'Exec=%s/moonlight\n' "$BIN_DIR" >>"$tmp"
    printf '%s\n' "$OWNERSHIP_MARK" >>"$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$dst"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
}

activate_target() {
    # The desktop entry always targets the stable wrapper path. Repair it first:
    # if this write fails, the old wrapper and therefore the old active payload
    # remain untouched. Wrapper replacement is the sole activation commit point.
    install_desktop_entry
    install_wrapper
    qd_info "Moonlight 已激活: $SELECTED_TAG"
}

main() {
    parse_args "$@"
    qd_require_not_root
    qd_require_ubuntu
    [ "$(uname -m)" = x86_64 ] || qd_die 'Moonlight AppImage 仅支持 x86_64'
    check_wrapper_and_desktop_ownership  # foreign/broken active state must not consume API quota
    resolve_moonlight_release

    local selected_target="$OPT_DIR/${SELECTED_TAG#v}" action=install
    if [ -n "$ACTIVE_VERSION" ]; then
        if qd_version_ge "v$ACTIVE_VERSION" "$SELECTED_TAG"; then
            if [ "$ACTIVE_VERSION" = "${SELECTED_TAG#v}" ]; then action=equal; else action=keep-newer; fi
        else action=install
        fi
    fi
    if [ "$action" = keep-newer ]; then
        local active_digest
        active_digest="$(cat "$ACTIVE_TARGET/$MARKER_NAME")"
        [[ "$active_digest" =~ ^[0-9a-fA-F]{64}$ ]] && target_converged "$ACTIVE_TARGET" "$active_digest" \
            || qd_die '较新的活动 Moonlight 结构或来源记录无效；拒绝降级'
        TARGET_DIR="$ACTIVE_TARGET"; set_target_sources
        # Both owned front doors point at the preserved active target. Repair the
        # desktop first; wrapper replacement (including mode 0755) is the commit.
        install_desktop_entry
        install_wrapper
        qd_info "已有更高活动版本 v$ACTIVE_VERSION，保留且不降级"
        exit 0
    fi

    refuse_selected_residue
    require_managed_target_or_absent "$selected_target"
    if target_converged "$selected_target" "$SELECTED_SHA"; then
        TARGET_DIR="$selected_target"; set_target_sources
        qd_info "选定版本 $SELECTED_TAG 已验证；跳过下载；未重新校验已解包内容"
    else
        local appimage
        qd_mktemp_file appimage
        download_appimage "$appimage"
        extract_promote_target "$appimage"
    fi
    activate_target
}
main "$@"
