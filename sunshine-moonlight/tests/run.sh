#!/bin/bash
# quick-deploy/sunshine-moonlight/tests/run.sh
# 隔离测试：临时 HOME + PATH 命令 mock。不需要 root、不需要网络、
# 绝不触碰真实 Sunshine/Moonlight 安装与用户状态。
#
# 脚本中的测试专用钩子（真实运行不要设置）：
#   QD_OS_RELEASE_FILE / QD_SUNSHINE_CONFIG_DIR / QD_UINPUT_NODE / QD_UHID_NODE
#   QD_HOST_STATE_DIR / QD_SYSTEMD_USER_DIR / QD_TEST_MOONLIGHT_SHA256 / QD_TEST_MOONLIGHT_SIZE

set -uo pipefail

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(dirname "$TESTS_DIR")"
BASE_PATH="$PATH"

PASSED=0
FAILED=0
CURRENT_CASE=''
CASE=''

# F9：中断/异常退出也要清掉用例目录，不留 /tmp/qd-sm-test-* 残留
cleanup_on_exit() {
    if [ -n "${CASE:-}" ] && [ -d "${CASE:-}" ]; then
        (cd / && rm -rf "$CASE")
    fi
}
trap cleanup_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

say()  { printf '%s\n' "$*"; }
pass() { PASSED=$((PASSED+1)); printf '  [PASS] %s\n' "$1"; }
fail() { FAILED=$((FAILED+1)); printf '  [FAIL] %s\n' "$1"; }

assert_eq() { # name expected actual
    if [ "$2" = "$3" ]; then pass "$1"; else fail "$1（期望 [$2] 实际 [$3]）"; fi
}
assert_contains() { # name haystack needle
    if grep -qF -- "$3" <<<"$2"; then pass "$1"; else fail "$1（未找到: $3）"; fi
}
assert_not_contains() { # name haystack needle
    if grep -qF -- "$3" <<<"$2"; then fail "$1（不应出现: $3）"; else pass "$1"; fi
}
assert_file_exists() {
    if [ -e "$2" ]; then pass "$1"; else fail "$1（文件不存在: $2）"; fi
}
assert_file_absent() {
    if [ ! -e "$2" ]; then pass "$1"; else fail "$1（文件不应存在: $2）"; fi
}

# ---- 沙箱与 mock ------------------------------------------------------------------

new_case() {
    CURRENT_CASE="$1"
    CASE="$(mktemp -d "/tmp/qd-sm-test-$1.XXXXXX")"
    export HOME="$CASE/home"
    export XDG_STATE_HOME="$HOME/.local/state"
    export XDG_DATA_HOME="$HOME/.local/share"
    mkdir -p "$HOME" "$CASE/bin" "$CASE/fixtures"
    export MOCKBIN="$CASE/bin"
    export FIXTURES="$CASE/fixtures"
    export MOCK_LOG="$CASE/mock.log"
    : >"$MOCK_LOG"
    export PATH="$MOCKBIN:$BASE_PATH"
    # 通用测试钩子：隔离配置/状态/设备节点
    export QD_SUNSHINE_CONFIG_DIR="$HOME/.config/sunshine"
    export QD_HOST_STATE_DIR="$CASE/host-state"
    export QD_UINPUT_NODE="$CASE/uinput"
    export QD_UHID_NODE="$CASE/uhid"
    # F5：单元文件探测目录也隔进沙箱，绝不回退到真实 /usr/lib/systemd/user
    export QD_SYSTEMD_USER_DIR="$CASE/systemd-user"
    mkdir -p "$QD_SYSTEMD_USER_DIR"
    export QD_OS_RELEASE_FILE="$FIXTURES/os-release"
    printf 'ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\nVERSION_CODENAME=noble\n' >"$QD_OS_RELEASE_FILE"
    write_standard_mocks
    say "用例: $CURRENT_CASE"
}

end_case() {
    cd /
    rm -rf "$CASE"
    CASE=''
    export PATH="$BASE_PATH"
    unset QD_TEST_MOONLIGHT_SHA256 QD_TEST_MOONLIGHT_SIZE MOCK_UID MOCK_ARCH \
        MOCK_SYSTEMCTL_FAIL MOCK_SYSTEMD_UNITS MOCK_GETCAP MOCK_TS_IP MOCK_GROUPS \
        MOCK_SUNSHINE_INSTALLED MOCK_SUNSHINE_VERSION MOCK_DPKG_ARCH \
        MOCK_SS_OUTPUT MOCK_SUNSHINE_BIN MOCK_APT_GET_FAIL \
        MOCK_SYSTEMCTL_ACTIVE MOCK_SYSTEMCTL_ENABLED TMPDIR
}

write_standard_mocks() {
    cat >"$MOCKBIN/curl" <<'MOCK'
#!/usr/bin/env bash
echo "curl $*" >> "$MOCK_LOG"
out='' url='' prev=''
for a in "$@"; do
    [ "$prev" = "-o" ] && out="$a"
    prev="$a"
    case "$a" in http*) url="$a";; esac
done
src=''
case "$url" in
    *api.github.com/repos/LizardByte/Sunshine*) src="$FIXTURES/sunshine-api.json";;
    *api.github.com/repos/moonlight-stream*)  src="$FIXTURES/moonlight-api.json";;
    *.deb)      src="$FIXTURES/sunshine.deb";;
    *.AppImage) src="$FIXTURES/moonlight.AppImage";;
    *) echo "mock curl: 未映射 URL $url" >&2; exit 22;;
esac
[ -f "$src" ] || { echo "mock curl: 缺少 fixture $src" >&2; exit 22; }
if [ -n "$out" ]; then cp "$src" "$out"; else cat "$src"; fi
MOCK

    cat >"$MOCKBIN/sudo" <<'MOCK'
#!/usr/bin/env bash
echo "sudo $*" >> "$MOCK_LOG"
exec "$@"
MOCK

    cat >"$MOCKBIN/apt-get" <<'MOCK'
#!/usr/bin/env bash
echo "apt-get $*" >> "$MOCK_LOG"
if [ "${MOCK_APT_GET_FAIL:-0}" = 1 ]; then
    echo "mock apt-get: 模拟失败" >&2; exit 1
fi
case " $* " in
    *" install "*)
        rm -f "$MOCK_LOG.pkg-removed"
        : >"$MOCK_LOG.pkg-installed"
        printf '%s\n' "${MOCK_APT_INSTALL_VERSION:-2026.516.143833}" >"$MOCK_LOG.pkg-version" ;;
    *" remove "*)
        rm -f "$MOCK_LOG.pkg-installed" "$MOCK_LOG.pkg-version"
        : >"$MOCK_LOG.pkg-removed" ;;
esac
exit 0
MOCK

    cat >"$MOCKBIN/dpkg" <<'MOCK'
#!/usr/bin/env bash
echo "dpkg $*" >> "$MOCK_LOG"
if [ "$1" = "--compare-versions" ]; then
    a="$2"; op="$3"; b="$4"
    first="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"
    eq=0; [ "$a" = "$b" ] && eq=1
    case "$op" in
        ge) { [ "$eq" = 1 ] || [ "$first" = "$b" ]; };;
        gt) [ "$eq" = 0 ] && [ "$first" = "$b" ];;
        le) { [ "$eq" = 1 ] || [ "$first" = "$a" ]; };;
        lt) [ "$eq" = 0 ] && [ "$first" = "$a" ];;
        eq) [ "$eq" = 1 ];;
        *) exit 2;;
    esac
    exit $?
fi
if [ "$1" = "--print-architecture" ]; then echo "${MOCK_DPKG_ARCH:-amd64}"; exit 0; fi
if [ "$1" = "-L" ]; then echo "${MOCK_SUNSHINE_BIN:-/usr/bin/sunshine}"; exit 0; fi
echo "mock dpkg: 未处理 $*" >&2; exit 2
MOCK

    cat >"$MOCKBIN/dpkg-query" <<'MOCK'
#!/usr/bin/env bash
echo "dpkg-query $*" >> "$MOCK_LOG"
[ ! -f "$MOCK_LOG.pkg-removed" ] || exit 1
if [ -f "$MOCK_LOG.pkg-installed" ]; then
    installed=1
    version="$(cat "$MOCK_LOG.pkg-version")"
else
    installed="${MOCK_SUNSHINE_INSTALLED:-1}"
    version="${MOCK_SUNSHINE_VERSION:-2026.516.143833}"
fi
[ "$installed" = 1 ] || exit 1
case "$*" in
    *db:Status-Status*) echo 'installed';;
    *Version*) echo "$version";;
    *) echo 'installed';;
esac
exit 0
MOCK

    cat >"$MOCKBIN/systemctl" <<'MOCK'
#!/usr/bin/env bash
echo "systemctl $*" >> "$MOCK_LOG"
# Use ${var-default}, not ${var:-default}: tests deliberately set an empty
# unit list to model a missing package and that must remain distinguishable
# from an unset mock variable.
units="${MOCK_SYSTEMD_UNITS-app-dev.lizardbyte.app.Sunshine.service}"
active_state="$MOCK_LOG.systemctl-active"
enabled_state="$MOCK_LOG.systemctl-enabled"
case "$*" in
    *list-unit-files*)
        printf '%s\n' "$units"
        exit 0;;
esac
if [ "${MOCK_SYSTEMCTL_FAIL:-0}" = 1 ]; then
    case "$*" in *enable*|*is-active*|*is-enabled*|*try-restart*)
        echo "mock systemctl: 模拟失败" >&2; exit 1;;
    esac
fi
case "$*" in
    *cat*)
        [ -n "$units" ]; exit $?;;
    *enable\ --now*)
        : >"$active_state"
        : >"$enabled_state"
        exit 0;;
    *enable*)
        : >"$enabled_state"
        exit 0;;
    *try-restart*)
        if [ "${MOCK_SYSTEMCTL_ACTIVE:-0}" = 1 ] || [ -f "$active_state" ]; then
            : >"$active_state"
            exit 0
        fi
        exit 1;;
    *is-active*)
        { [ "${MOCK_SYSTEMCTL_ACTIVE:-0}" = 1 ] || [ -f "$active_state" ]; }; exit $?;;
    *is-enabled*)
        { [ "${MOCK_SYSTEMCTL_ENABLED:-1}" = 1 ] || [ -f "$enabled_state" ]; }; exit $?;;
esac
exit 0
MOCK

    cat >"$MOCKBIN/tailscale" <<'MOCK'
#!/usr/bin/env bash
echo "tailscale $*" >> "$MOCK_LOG"
if [ "$1" = "ip" ] && [ "$2" = "-4" ]; then echo "${MOCK_TS_IP:-100.123.34.64}"; exit 0; fi
exit 0
MOCK

    cat >"$MOCKBIN/id" <<'MOCK'
#!/usr/bin/env bash
if [ "$1" = "-u" ]; then echo "${MOCK_UID:-$(/usr/bin/id -u)}"; exit 0; fi
if [ "$1" = "-nG" ]; then echo "${MOCK_GROUPS:-$(/usr/bin/id -nG)}"; exit 0; fi
if [ "$1" = "-un" ]; then /usr/bin/id -un; exit 0; fi
exec /usr/bin/id "$@"
MOCK

    cat >"$MOCKBIN/uname" <<'MOCK'
#!/usr/bin/env bash
if [ "$1" = "-m" ]; then echo "${MOCK_ARCH:-x86_64}"; exit 0; fi
exec /usr/bin/uname "$@"
MOCK

    cat >"$MOCKBIN/getcap" <<'MOCK'
#!/usr/bin/env bash
echo "getcap $*" >> "$MOCK_LOG"
if [ -n "${MOCK_GETCAP:-}" ]; then printf '%s\n' "$MOCK_GETCAP"; exit 0; fi
# setcap mock 被调用后，报告已具备完整 capability（模拟真实修复效果）
if [ -f "$MOCK_LOG.setcap-done" ]; then
    printf '%s\n' '/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
fi
exit 0
MOCK

    cat >"$MOCKBIN/setcap" <<'MOCK'
#!/usr/bin/env bash
echo "setcap $*" >> "$MOCK_LOG"
: >"$MOCK_LOG.setcap-done"
exit 0
MOCK

    cat >"$MOCKBIN/usermod" <<'MOCK'
#!/usr/bin/env bash
echo "usermod $*" >> "$MOCK_LOG"
exit 0
MOCK

    cat >"$MOCKBIN/ss" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "${MOCK_SS_OUTPUT:-}"
exit 0
MOCK

    chmod +x "$MOCKBIN"/*
}

# ---- fixtures ----------------------------------------------------------------------

make_fixture_deb() { # PATH —— 内容是任意字节；测试关心的是 sha256
    printf 'fake-sunshine-deb-payload\n' >"$1"
}

write_sunshine_api() { # TAG DEB_PATH  —— digest 取 DEB_PATH 的真实 sha256
    local tag="$1" deb="$2" sha
    sha="$(sha256sum "$deb" | awk '{print $1}')"
    cat >"$FIXTURES/sunshine-api.json" <<EOF_JSON
{
  "tag_name": "$tag",
  "assets": [
    {
      "name": "sunshine-ubuntu-24.04-amd64.deb",
      "browser_download_url": "https://example.invalid/sunshine-ubuntu-24.04-amd64.deb",
      "digest": "sha256:$sha"
    },
    {
      "name": "sunshine-ubuntu-24.04-arm64.deb",
      "browser_download_url": "https://example.invalid/sunshine-ubuntu-24.04-arm64.deb",
      "digest": "sha256:$sha"
    }
  ]
}
EOF_JSON
}

make_fixture_appimage() { # PATH —— 可执行脚本，响应 --appimage-extract
    cat >"$1" <<'MOCK_AI'
#!/usr/bin/env bash
if [ "$1" = "--appimage-extract" ]; then
    mkdir -p squashfs-root
    cat > squashfs-root/com.moonlight_stream.Moonlight.desktop <<'D'
[Desktop Entry]
Name=Moonlight
Exec=moonlight
Icon=moonlight
Type=Application
Categories=Game;
D
    printf '<svg xmlns="http://www.w3.org/2000/svg"/>\n' > squashfs-root/moonlight.svg
    printf '#!/bin/sh\necho moonlight-stub\n' > squashfs-root/AppRun
    chmod +x squashfs-root/AppRun
    exit 0
fi
echo "stub appimage: 直接运行不受支持" >&2
exit 1
MOCK_AI
    chmod +x "$1"
}

# ---- 用例 ----------------------------------------------------------------------------

t_syntax() {
    say '用例: bash 语法检查'
    local f bad=0
    for f in "$MODULE_DIR"/lib/common.sh "$MODULE_DIR"/install-host.sh \
             "$MODULE_DIR"/install-client.sh "$MODULE_DIR"/doctor.sh \
             "$MODULE_DIR"/uninstall.sh "$TESTS_DIR"/run.sh; do
        if bash -n "$f" 2>"$CASE/syntax-err"; then
            pass "语法: $(basename "$f")"
        else
            fail "语法: $(basename "$f") — $(cat "$CASE/syntax-err")"
            bad=1
        fi
    done
    return $bad
}

t_host_digest_mismatch_blocks() {
    new_case digest-mismatch
    export MOCK_SUNSHINE_INSTALLED=0
    make_fixture_deb "$FIXTURES/sunshine.deb"
    write_sunshine_api v2026.516.143833 "$FIXTURES/sunshine.deb"
    # 篡改 API 里的 digest
    sed -i 's/sha256:[0-9a-f]\{64\}/sha256:0000000000000000000000000000000000000000000000000000000000000000/g' \
        "$FIXTURES/sunshine-api.json"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    local rc=$?
    assert_eq '摘要不匹配应非零退出' 1 "$rc"
    assert_contains '报错说明摘要不匹配' "$(cat "$CASE/err")" 'SHA-256 摘要不匹配'
    assert_not_contains '未调用 apt-get' "$(cat "$MOCK_LOG")" 'apt-get install'
    assert_file_absent '未创建配置文件' "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    end_case
}

t_host_version_floor() {
    new_case version-floor
    bash "$MODULE_DIR/install-host.sh" --version v2025.100.1 >"$CASE/out" 2>"$CASE/err"
    local rc=$?
    assert_eq '低于基线应拒绝' 1 "$rc"
    assert_contains '报错点名 CVE' "$(cat "$CASE/err")" 'CVE-2026-32253'
    assert_not_contains '未访问网络' "$(cat "$MOCK_LOG")" 'curl'
    end_case
}

t_host_cve_floor_no_bypass() {
    new_case cve-floor-no-bypass
    # 低于基线一律拒绝（F11：不再有绕过开关）
    bash "$MODULE_DIR/install-host.sh" --version v2025.100.1 >"$CASE/out" 2>"$CASE/err"
    assert_eq '低于基线拒绝' 1 "$?"
    assert_contains '报错点名 CVSS 9.8' "$(cat "$CASE/err")" 'CVSS 9.8'
    assert_not_contains '未访问网络' "$(cat "$MOCK_LOG")" 'curl'
    # 旧的绕过旗标已移除：即使显式传入也不再放行
    bash "$MODULE_DIR/install-host.sh" --version v2025.100.1 \
        --i-accept-cve-2026-32253-risk >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '旧绕过旗标不再放行' 1 "$?"
    assert_not_contains '绕过路径未触网' "$(cat "$MOCK_LOG")" 'curl'
    bash "$MODULE_DIR/install-host.sh" --i-accept-cve-2026-32253-risk >"$CASE/out3" 2>"$CASE/err3"
    assert_eq '绕过旗标本身报未知参数' 1 "$?"
    assert_contains '报错为未知参数' "$(cat "$CASE/err3")" '未知参数'
    end_case
}

t_host_success_idempotent_config() {
    new_case host-success
    make_fixture_deb "$FIXTURES/sunshine.deb"
    write_sunshine_api v2026.516.143833 "$FIXTURES/sunshine.deb"
    # 预置配置：未知键、注释、已有 csrf 列表
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    cat >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" <<'EOF_CONF'
# 我的手写注释
my_custom_key = keep-me
min_threads = 4
csrf_allowed_origins = https://other-host:47990
EOF_CONF
    # uinput 存在但无权限、uhid 不存在、用户不在 input 组 → 触发 usermod
    : >"$QD_UINPUT_NODE"; chmod 000 "$QD_UINPUT_NODE"
    export MOCK_GROUPS='conley adm cdrom'
    # getcap 无输出 → 触发 setcap 修复
    export MOCK_GETCAP=''

    bash "$MODULE_DIR/install-host.sh" --capture kms >"$CASE/out" 2>"$CASE/err"
    local rc=$?
    assert_eq '主机安装成功' 0 "$rc"
    local conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    assert_contains '保留未知键 my_custom_key' "$(cat "$conf")" 'my_custom_key = keep-me'
    assert_contains '保留未知键 min_threads' "$(cat "$conf")" 'min_threads = 4'
    assert_contains '保留注释' "$(cat "$conf")" '# 我的手写注释'
    assert_contains 'upnp 已禁用' "$(cat "$conf")" 'upnp = disabled'
    assert_contains 'capture=kms' "$(cat "$conf")" 'capture = kms'
    assert_contains '绑定 tailscale IP' "$(cat "$conf")" 'bind_address = 100.123.34.64'
    assert_contains 'csrf 含新来源' "$(cat "$conf")" 'https://100.123.34.64:47990'
    assert_contains 'csrf 保留旧来源' "$(cat "$conf")" 'https://other-host:47990'
    assert_contains '已加入 input 组' "$(cat "$MOCK_LOG")" 'usermod -aG input'
    assert_contains '修复了 capability' "$(cat "$MOCK_LOG")" 'setcap cap_sys_admin,cap_sys_nice+p'
    assert_contains '启用 canonical 用户服务' "$(cat "$MOCK_LOG")" 'systemctl --user enable --now app-dev.lizardbyte.app.Sunshine.service'
    assert_not_contains '未用 sudo 操作用户服务' "$(cat "$MOCK_LOG")" 'sudo systemctl'
    assert_contains '提示重新登录' "$(cat "$CASE/err")" '重新登录'

    cp "$conf" "$CASE/conf-first"
    bash "$MODULE_DIR/install-host.sh" --capture kms >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '二次运行成功' 0 "$?"
    if diff -q "$CASE/conf-first" "$conf" >/dev/null; then
        pass '配置幂等（二次运行文件不变）'
    else
        fail '配置幂等（二次运行文件变化）'
        diff "$CASE/conf-first" "$conf" | head -20
    fi
    end_case
}

t_host_service_failure_visible() {
    new_case service-failure
    make_fixture_deb "$FIXTURES/sunshine.deb"
    write_sunshine_api v2026.516.143833 "$FIXTURES/sunshine.deb"
    : >"$QD_UINPUT_NODE"; chmod 666 "$QD_UINPUT_NODE"
    : >"$QD_UHID_NODE"; chmod 666 "$QD_UHID_NODE"
    export MOCK_GETCAP='/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
    export MOCK_SYSTEMCTL_FAIL=1
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    local rc=$?
    assert_eq '服务失败应非零退出' 1 "$rc"
    assert_contains '报错点名用户服务' "$(cat "$CASE/err")" 'app-dev.lizardbyte.app.Sunshine.service'
    assert_contains '说明需要图形会话' "$(cat "$CASE/err")" '图形'
    assert_contains '不推荐 enable-linger' "$(cat "$CASE/err")" 'enable-linger'
    end_case
}

t_host_os_reject() {
    new_case os-reject
    printf 'ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian 12"\n' >"$QD_OS_RELEASE_FILE"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '非 Ubuntu 拒绝' 1 "$?"
    printf 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04"\n' >"$QD_OS_RELEASE_FILE"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq 'Ubuntu 22.04 拒绝' 1 "$?"
    assert_contains '报错提示版本要求' "$(cat "$CASE/err")" '24.04'
    end_case
}

t_root_reject() {
    new_case root-reject
    export MOCK_UID=0
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq 'install-host 拒绝 root' 1 "$?"
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq 'install-client 拒绝 root' 1 "$?"
    bash "$MODULE_DIR/uninstall.sh" --client >"$CASE/out" 2>"$CASE/err"
    assert_eq 'uninstall 拒绝 root' 1 "$?"
    assert_contains '报错说明不要用 root' "$(cat "$CASE/err")" 'root'
    end_case
}

t_client_arch_reject() {
    new_case client-arch
    export MOCK_ARCH=aarch64
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq 'ARM 拒绝安装' 1 "$?"
    assert_contains '报错说明架构' "$(cat "$CASE/err")" 'x86_64'
    end_case
}

client_case_setup() { # 生成 AppImage fixture 并设置固定值覆盖钩子
    make_fixture_appimage "$FIXTURES/moonlight.AppImage"
    export QD_TEST_MOONLIGHT_SHA256
    QD_TEST_MOONLIGHT_SHA256="$(sha256sum "$FIXTURES/moonlight.AppImage" | awk '{print $1}')"
    export QD_TEST_MOONLIGHT_SIZE
    QD_TEST_MOONLIGHT_SIZE="$(stat -c %s "$FIXTURES/moonlight.AppImage")"
}

t_client_digest_mismatch() {
    new_case client-digest
    client_case_setup
    export QD_TEST_MOONLIGHT_SHA256='1111111111111111111111111111111111111111111111111111111111111111'
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '客户端摘要不匹配拒绝' 1 "$?"
    assert_contains '报错说明摘要' "$(cat "$CASE/err")" 'SHA-256'
    assert_file_absent '未创建安装目录' "$HOME/.local/opt/moonlight"
    assert_file_absent '未创建启动包装' "$HOME/.local/bin/moonlight"
    end_case
}

t_client_size_mismatch() {
    new_case client-size
    client_case_setup
    export QD_TEST_MOONLIGHT_SIZE='12345'
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '客户端大小不符拒绝' 1 "$?"
    assert_contains '报错说明大小' "$(cat "$CASE/err")" '大小'
    assert_file_absent '未创建安装目录' "$HOME/.local/opt/moonlight"
    end_case
}

t_client_install_and_converge() {
    new_case client-install
    client_case_setup
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '客户端安装成功' 0 "$?"
    local target="$HOME/.local/opt/moonlight/6.1.0"
    local wrapper="$HOME/.local/bin/moonlight"
    local desktop="$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"
    assert_file_exists 'AppRun 就位' "$target/AppRun"
    assert_file_exists '摘要标记就位' "$target/.quick-deploy-sha256"
    assert_file_exists '启动包装就位' "$wrapper"
    assert_file_exists '桌面项就位' "$desktop"
    assert_file_exists '图标就位' "$target/moonlight.svg"
    assert_contains '包装指向 AppRun' "$(cat "$wrapper")" "$target/AppRun"
    assert_contains '桌面项 Exec 绝对路径' "$(cat "$desktop")" "Exec=$HOME/.local/bin/moonlight"
    assert_contains '桌面项 Icon 绝对路径' "$(cat "$desktop")" "Icon=$target/moonlight.svg"
    assert_contains '桌面项带归属标记' "$(cat "$desktop")" 'quick-deploy/sunshine-moonlight'

    local curl_calls
    curl_calls="$(grep -c '^curl ' "$MOCK_LOG")"
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '二次运行成功（收敛）' 0 "$?"
    assert_eq '二次运行不再下载' "$curl_calls" "$(grep -c '^curl ' "$MOCK_LOG")"
    assert_contains '二次运行报告收敛' "$(cat "$CASE/out2")" '跳过下载'
    end_case
}

t_uninstall_client() {
    new_case uninstall-client
    client_case_setup
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '前置安装成功' 0 "$?"
    # 放入一个外来文件验证保留逻辑
    mkdir -p "$HOME/.local/opt/moonlight/foreign-build"
    printf 'not ours\n' >"$HOME/.local/opt/moonlight/foreign-build/keep.txt"
    bash "$MODULE_DIR/uninstall.sh" --client >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '客户端卸载成功' 0 "$?"
    assert_file_absent '包装已移除' "$HOME/.local/bin/moonlight"
    assert_file_absent '桌面项已移除' "$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"
    assert_file_absent '受管版本目录已移除' "$HOME/.local/opt/moonlight/6.1.0"
    assert_file_exists '外来目录被保留' "$HOME/.local/opt/moonlight/foreign-build/keep.txt"
    assert_contains '报告保留了外来目录' "$(cat "$CASE/err2")" '外来'
    end_case
}

t_uninstall_host_package_ownership() {
    new_case uninstall-host
    # 无归属记录 → 拒绝
    bash "$MODULE_DIR/uninstall.sh" --host-package >"$CASE/out" 2>"$CASE/err"
    assert_eq '无归属记录拒绝移除包' 1 "$?"
    assert_not_contains '未执行 apt remove' "$(cat "$MOCK_LOG")" 'apt-get remove'
    # 记录为“预先存在” → 仍拒绝
    mkdir -p "$QD_HOST_STATE_DIR"
    printf 'package_preexisting=true\nfirst_run_version=v2026.516.143833\n' >"$QD_HOST_STATE_DIR/host.state"
    bash "$MODULE_DIR/uninstall.sh" --host-package >"$CASE/out" 2>"$CASE/err"
    assert_eq '预先存在的包拒绝移除' 1 "$?"
    assert_contains '报错说明预先存在' "$(cat "$CASE/err")" '介入前就已存在'
    # 显式 force → 放行
    bash "$MODULE_DIR/uninstall.sh" --host-package --force-remove-preexisting-package >"$CASE/out" 2>"$CASE/err"
    assert_eq 'force 旗标放行移除' 0 "$?"
    assert_contains '执行了 apt remove' "$(cat "$MOCK_LOG")" 'apt-get remove -y sunshine'
    end_case
}

t_uninstall_host_state_destructive() {
    new_case uninstall-state
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'credential-ish\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine_state.json"
    # 默认（只做 client）不动主机配置
    bash "$MODULE_DIR/uninstall.sh" --client >"$CASE/out" 2>"$CASE/err"
    assert_eq 'client 卸载成功' 0 "$?"
    assert_file_exists '默认保留主机配置' "$QD_SUNSHINE_CONFIG_DIR/sunshine_state.json"
    # 显式破坏性旗标才删除
    bash "$MODULE_DIR/uninstall.sh" --destroy-host-state >"$CASE/out" 2>"$CASE/err"
    assert_eq '显式删除状态成功' 0 "$?"
    assert_file_absent '状态已删除' "$QD_SUNSHINE_CONFIG_DIR/sunshine_state.json"
    end_case
}

t_doctor_host_outdated_fails() {
    new_case doctor-outdated
    export MOCK_SUNSHINE_VERSION='2025.100.1'
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out" 2>"$CASE/err"
    assert_eq '过期版本 doctor 退出码 1' 1 "$?"
    assert_contains '报告 CVE' "$(cat "$CASE/out")" 'CVE-2026-32253'
    end_case
}

t_doctor_host_current_passes() {
    new_case doctor-ok
    : >"$QD_UINPUT_NODE"; chmod 666 "$QD_UINPUT_NODE"
    : >"$QD_UHID_NODE"; chmod 666 "$QD_UHID_NODE"
    export MOCK_GETCAP='/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
    export MOCK_GROUPS='conley input'
    export MOCK_SYSTEMCTL_ACTIVE=1
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'upnp = disabled\ncapture = kms\nbind_address = 100.123.34.64\ncsrf_allowed_origins = https://100.123.34.64:47990\n' \
        >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out" 2>"$CASE/err"
    assert_eq '健康主机 doctor 退出码 0' 0 "$?"
    assert_contains '确认安全基线' "$(cat "$CASE/out")" '已修复'
    end_case
}

t_doctor_capture_xcb_flagged() {
    new_case doctor-xcb
    : >"$QD_UINPUT_NODE"; chmod 666 "$QD_UINPUT_NODE"
    : >"$QD_UHID_NODE"; chmod 666 "$QD_UHID_NODE"
    export MOCK_GETCAP='/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'capture = xcb\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out" 2>"$CASE/err"
    assert_eq 'xcb 旧值导致失败' 1 "$?"
    assert_contains '报告 xcb 是旧名' "$(cat "$CASE/out")" 'xcb'
    end_case
}

# ---- F1: 临时文件清理（父 shell 登记，EXIT trap 清空私有 TMPDIR） ------------------

host_standard_setup() { # 常用主机 fixture + 无权限设备，保证走通全流程
    make_fixture_deb "$FIXTURES/sunshine.deb"
    write_sunshine_api v2026.516.143833 "$FIXTURES/sunshine.deb"
    : >"$QD_UINPUT_NODE"; chmod 666 "$QD_UINPUT_NODE"
    : >"$QD_UHID_NODE"; chmod 666 "$QD_UHID_NODE"
    export MOCK_GETCAP='/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
}

assert_tmpdir_empty() { # NAME
    local n
    n="$(find "$TMPDIR" -mindepth 1 2>/dev/null | wc -l)"
    assert_eq "$1" 0 "$n"
}

t_temp_cleanup_host() {
    new_case temp-host
    export TMPDIR="$CASE/tmp"; mkdir -p "$TMPDIR"
    host_standard_setup
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '主机安装成功（TMPDIR 用例）' 0 "$?"
    assert_tmpdir_empty '成功后私有 TMPDIR 零残留'
    end_case

    new_case temp-host-fail
    export TMPDIR="$CASE/tmp"; mkdir -p "$TMPDIR"
    export MOCK_SUNSHINE_INSTALLED=0
    host_standard_setup
    sed -i 's/sha256:[0-9a-f]\{64\}/sha256:0000000000000000000000000000000000000000000000000000000000000000/g' \
        "$FIXTURES/sunshine-api.json"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '摘要失败退出码 1' 1 "$?"
    assert_tmpdir_empty '失败路径私有 TMPDIR 零残留'
    end_case
}

t_temp_cleanup_client() {
    new_case temp-client
    export TMPDIR="$CASE/tmp"; mkdir -p "$TMPDIR"
    client_case_setup
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '客户端安装成功（TMPDIR 用例）' 0 "$?"
    assert_tmpdir_empty '客户端成功路径 TMPDIR 零残留'
    end_case

    new_case temp-client-fail
    export TMPDIR="$CASE/tmp"; mkdir -p "$TMPDIR"
    client_case_setup
    export QD_TEST_MOONLIGHT_SIZE='12345'
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '客户端失败退出码 1' 1 "$?"
    assert_tmpdir_empty '客户端失败路径 TMPDIR 零残留'
    end_case
}

# ---- F2/F4: capture 语义 -----------------------------------------------------------

t_capture_auto_removes_key() {
    new_case capture-auto
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'capture = kms\nmy_key = keep\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" --capture auto >"$CASE/out" 2>"$CASE/err"
    assert_eq '--capture auto 安装成功' 0 "$?"
    local conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    assert_not_contains 'capture 键已移除' "$(cat "$conf")" 'capture'
    assert_contains '未知键保留' "$(cat "$conf")" 'my_key = keep'
    assert_contains '报告自动侦测' "$(cat "$CASE/out")" '自动侦测'
    end_case
}

t_capture_default_normalizes_stale() {
    new_case capture-stale
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'capture = xcb\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '默认运行成功（xcb 被纠正）' 0 "$?"
    local conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    assert_not_contains 'xcb 不再保留' "$(cat "$conf")" 'capture'
    assert_contains 'stderr 有可见告警' "$(cat "$CASE/err")" '无效'
    # 纠正后的状态对 doctor 是健康的（不再报 xcb 失败）
    export MOCK_SYSTEMCTL_ACTIVE=1
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/dout" 2>"$CASE/derr"
    assert_not_contains 'doctor 不再报 xcb' "$(cat "$CASE/dout")" '旧名'
    assert_contains 'doctor 认为 capture 空键健康' "$(cat "$CASE/dout")" '自动侦测'
    end_case

    new_case capture-auto-value
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'capture = auto\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '默认运行成功（auto 值被纠正）' 0 "$?"
    assert_not_contains 'capture=auto 行已移除' "$(cat "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")" 'capture'
    assert_contains 'stderr 有可见告警（auto 值）' "$(cat "$CASE/err")" '无效'
    end_case
}

t_capture_default_preserves_valid() {
    new_case capture-keep
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'capture = portal\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '默认运行成功（portal 保留）' 0 "$?"
    assert_contains 'portal 保留（v2026.516 源码中仍合法）' \
        "$(cat "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")" 'capture = portal'
    end_case
}

t_doctor_capture_auto_flagged() {
    new_case doctor-capture-auto
    : >"$QD_UINPUT_NODE"; chmod 666 "$QD_UINPUT_NODE"
    : >"$QD_UHID_NODE"; chmod 666 "$QD_UHID_NODE"
    export MOCK_GETCAP='/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
    export MOCK_SYSTEMCTL_ACTIVE=1
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'capture = auto\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out" 2>"$CASE/err"
    assert_eq 'capture=auto 显式值导致 doctor 失败' 1 "$?"
    assert_contains '报告 auto 是无效写法' "$(cat "$CASE/out")" '无效'
    end_case
}

# ---- F3: 配置变更 → 已运行服务 try-restart；无变更不重启 ----------------------------

t_restart_on_changed_active_config() {
    new_case restart-on-change
    host_standard_setup
    export MOCK_SYSTEMCTL_ACTIVE=1
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf '# 旧配置\nbind_address = 100.123.34.1\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '活跃服务+配置变更运行成功' 0 "$?"
    assert_eq '执行了一次 try-restart' 1 "$(grep -c 'try-restart' "$MOCK_LOG")"
    assert_not_contains '活跃路径不再 enable --now' "$(cat "$MOCK_LOG")" 'enable --now'
    assert_contains '输出说明重启生效' "$(cat "$CASE/out")" '新配置已生效'
    assert_file_exists '变更生成了滚动备份' "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
    local bak_sum
    bak_sum="$(sha256sum "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak" | awk '{print $1}')"
    # 幂等重跑：配置无变化 → 不重启、不更新备份
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '幂等重跑成功' 0 "$?"
    assert_eq '无变更不重启' 1 "$(grep -c 'try-restart' "$MOCK_LOG")"
    assert_eq '无变更不刷新备份' "$bak_sum" \
        "$(sha256sum "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak" | awk '{print $1}')"
    assert_contains '报告配置无变化' "$(cat "$CASE/out2")" '配置无变化'
    end_case
}

t_enable_now_when_inactive() {
    new_case enable-when-inactive
    host_standard_setup
    # MOCK_SYSTEMCTL_ACTIVE 默认 0（服务未运行）
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '未运行服务安装成功' 0 "$?"
    assert_contains '走了 enable --now' "$(cat "$MOCK_LOG")" 'enable --now'
    assert_not_contains '未运行路径不 try-restart' "$(cat "$MOCK_LOG")" 'try-restart'
    end_case
}

# ---- F6/F7 + 需求4: 端口语义（配置 port 是基准端口，Web UI = base+1） ------------------

t_custom_base_port() {
    new_case custom-port
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'port = 48000\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '自定义基准端口安装成功' 0 "$?"
    local conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    assert_contains '配置 port 原样保留' "$(cat "$conf")" 'port = 48000'
    assert_contains 'CSRF 来源用 base+1' "$(cat "$conf")" 'https://100.123.34.64:48001'
    assert_contains '完成信息用 Web UI 端口' "$(cat "$CASE/out")" 'https://100.123.34.64:48001'
    assert_not_contains '完成信息不含硬编码 47990' "$(cat "$CASE/out")" '47990'
    end_case
}

t_invalid_base_port_rejected() {
    new_case bad-port
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'port = abc\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '非数字 port 拒绝' 1 "$?"
    assert_contains '报错点名 port' "$(cat "$CASE/err")" 'port'
    printf 'port = 1028\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '低于 Sunshine 下限的 port 拒绝' 1 "$?"
    printf 'port = 65515\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '高于 Sunshine 上限的 port 拒绝' 1 "$?"
    end_case
}

t_doctor_listeners_custom_port() {
    new_case doctor-port
    : >"$QD_UINPUT_NODE"; chmod 666 "$QD_UINPUT_NODE"
    : >"$QD_UHID_NODE"; chmod 666 "$QD_UHID_NODE"
    export MOCK_GETCAP='/usr/bin/sunshine cap_sys_admin,cap_sys_nice=p'
    export MOCK_SYSTEMCTL_ACTIVE=1
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'port = 48000\nupnp = disabled\nbind_address = 100.123.34.64\n' \
        >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    export MOCK_SS_OUTPUT='tcp LISTEN 0 4096 100.123.34.64:48001 0.0.0.0:*'
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out" 2>"$CASE/err"
    assert_eq '自定义端口健康 doctor 退出码 0' 0 "$?"
    assert_contains '识别 Web UI 端口 48001' "$(cat "$CASE/out")" 'TCP 48001'
    assert_not_contains '不再误报 47990' "$(cat "$CASE/out")" '47990'
    assert_not_contains 'Tailnet 绑定不被对端列误报成通配监听' "$(cat "$CASE/out")" '绑定在通配地址'
    export MOCK_SS_OUTPUT='tcp LISTEN 0 4096 0.0.0.0:48001 0.0.0.0:*'
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out2" 2>"$CASE/err2"
    assert_contains '真正的 IPv4 通配监听会告警' "$(cat "$CASE/out2")" '绑定在通配地址'
    end_case
}

# ---- F5 + 需求8: systemd 单元目录完全可 mock（缺失单元路径） --------------------------

t_missing_unit_detected() {
    new_case missing-unit
    host_standard_setup
    export MOCK_SYSTEMD_UNITS=''   # list-unit-files 为空；QD_SYSTEMD_USER_DIR 也是空目录
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '缺失单元拒绝并退出 1' 1 "$?"
    assert_contains '报错未找到服务单元' "$(cat "$CASE/err")" '未找到 Sunshine 用户服务单元'
    assert_not_contains '未对 mock systemd 发出 enable' "$(cat "$MOCK_LOG")" 'enable --now'
    # 单元文件回退路径仍然有效（在沙箱目录里放一个单元文件）
    : >"$QD_SYSTEMD_USER_DIR/app-dev.lizardbyte.app.Sunshine.service"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '沙箱单元文件可被探测到' 0 "$?"
    end_case
}

t_doctor_missing_unit() {
    new_case doctor-missing-unit
    export MOCK_SYSTEMD_UNITS=''
    bash "$MODULE_DIR/doctor.sh" --host >"$CASE/out" 2>"$CASE/err"
    assert_eq 'doctor 缺失单元退出码 1' 1 "$?"
    assert_contains '报告未找到用户服务' "$(cat "$CASE/out")" '未找到 Sunshine 用户服务'
    end_case
}

# ---- 需求7: 主机包归属状态 -----------------------------------------------------------

t_ownership_state_apt_failure() {
    new_case ownership-apt-fail
    host_standard_setup
    export MOCK_SUNSHINE_INSTALLED=0
    export MOCK_APT_GET_FAIL=1
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq 'apt 失败退出码 1' 1 "$?"
    assert_contains '报错点名 apt' "$(cat "$CASE/err")" 'apt'
    assert_file_absent 'apt 失败不创建归属状态' "$QD_HOST_STATE_DIR/host.state"
    assert_file_absent 'apt 失败不写 last-install' "$QD_HOST_STATE_DIR/last-install"
    end_case
}

t_ownership_state_preserved() {
    new_case ownership-preserved
    host_standard_setup
    mkdir -p "$QD_HOST_STATE_DIR"
    printf 'package_preexisting=true\nfirst_run_version=v2099.1.1\n' >"$QD_HOST_STATE_DIR/host.state"
    local before
    before="$(cat "$QD_HOST_STATE_DIR/host.state")"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '重跑成功' 0 "$?"
    assert_eq '已有归属状态不被覆写' "$before" "$(cat "$QD_HOST_STATE_DIR/host.state")"
    end_case
}

t_installed_version_convergence() {
    new_case installed-version
    host_standard_setup
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '已安装同版收敛成功' 0 "$?"
    assert_not_contains '同版不重复下载' "$(cat "$MOCK_LOG")" 'curl '
    assert_not_contains '同版不重复 apt 安装' "$(cat "$MOCK_LOG")" 'apt-get install'
    assert_contains '同版输出说明跳过' "$(cat "$CASE/out")" '跳过重复下载'
    end_case

    new_case installed-newer
    host_standard_setup
    export MOCK_SUNSHINE_VERSION=2099.1.1
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '已安装高版收敛成功' 0 "$?"
    assert_not_contains '高版不触发下载降级' "$(cat "$MOCK_LOG")" 'curl '
    assert_not_contains '高版不触发 apt 降级' "$(cat "$MOCK_LOG")" 'apt-get install'
    assert_contains '高版显式拒绝降级' "$(cat "$CASE/err")" '拒绝降级'
    assert_contains 'last-install 记录实际高版' "$(cat "$QD_HOST_STATE_DIR/last-install")" 'version=2099.1.1'
    end_case
}

t_uninstall_owned_clears_state() {
    new_case uninstall-owned-state
    export MOCK_SUNSHINE_INSTALLED=0
    : >"$MOCK_LOG.pkg-installed"
    printf '2026.516.143833\n' >"$MOCK_LOG.pkg-version"
    mkdir -p "$QD_HOST_STATE_DIR"
    printf 'package_preexisting=false\nfirst_run_version=2026.516.143833\n' >"$QD_HOST_STATE_DIR/host.state"
    printf 'version=2026.516.143833\n' >"$QD_HOST_STATE_DIR/last-install"
    bash "$MODULE_DIR/uninstall.sh" --host-package >"$CASE/out" 2>"$CASE/err"
    assert_eq '受管包卸载成功' 0 "$?"
    assert_contains '执行 apt remove' "$(cat "$MOCK_LOG")" 'apt-get remove -y sunshine'
    assert_file_absent '卸载后清除 host.state' "$QD_HOST_STATE_DIR/host.state"
    assert_file_absent '卸载后清除 last-install' "$QD_HOST_STATE_DIR/last-install"
    end_case
}

# ---- 需求13: 输入校验 -----------------------------------------------------------------

t_bind_address_validation() {
    new_case bind-validate
    bash "$MODULE_DIR/install-host.sh" --bind-address 999.1.1.1 >"$CASE/out" 2>"$CASE/err"
    assert_eq '超范围段拒绝' 1 "$?"
    bash "$MODULE_DIR/install-host.sh" --bind-address 1.2.3 >"$CASE/out" 2>"$CASE/err"
    assert_eq '三段地址拒绝' 1 "$?"
    bash "$MODULE_DIR/install-host.sh" --bind-address '1.2.3.4.5' >"$CASE/out" 2>"$CASE/err"
    assert_eq '五段地址拒绝' 1 "$?"
    bash "$MODULE_DIR/install-host.sh" --bind-address "$(printf '10.0.0.1\nupnp = enabled')" >"$CASE/out" 2>"$CASE/err"
    assert_eq '换行注入拒绝' 1 "$?"
    assert_contains '报错说明 IPv4' "$(cat "$CASE/err")" 'IPv4'
    assert_not_contains '校验在联网之前' "$(cat "$MOCK_LOG")" 'curl'
    end_case
}

t_version_tag_validation() {
    new_case version-validate
    bash "$MODULE_DIR/install-host.sh" --version 'abc' >"$CASE/out" 2>"$CASE/err"
    assert_eq '非版本标签拒绝' 1 "$?"
    assert_contains '报错说明格式' "$(cat "$CASE/err")" '格式非法'
    bash "$MODULE_DIR/install-host.sh" --version 'v2026.516.143833;id' >"$CASE/out" 2>"$CASE/err"
    assert_eq '注入字符拒绝' 1 "$?"
    bash "$MODULE_DIR/install-host.sh" --version '2026.516.143833' >"$CASE/out" 2>"$CASE/err"
    assert_eq '缺前导 v 拒绝' 1 "$?"
    assert_not_contains '版本校验在联网之前' "$(cat "$MOCK_LOG")" 'curl'
    bash "$MODULE_DIR/install-client.sh" --version 'v6.1.0;id' >"$CASE/out" 2>"$CASE/err"
    assert_eq '客户端标签注入拒绝' 1 "$?"
    end_case
}

# ---- 需求12: Moonlight 只允许固定版本 -------------------------------------------------

t_client_pinned_version_only() {
    new_case client-pinned
    bash "$MODULE_DIR/install-client.sh" --version v6.1.1 >"$CASE/out" 2>"$CASE/err"
    assert_eq '非固定版本拒绝' 1 "$?"
    assert_contains '报错说明只信任固定校验值' "$(cat "$CASE/err")" '固定'
    assert_not_contains '拒绝路径不触网' "$(cat "$MOCK_LOG")" 'curl'
    end_case
}

# ---- 需求6: 客户端外来资产拒止 --------------------------------------------------------

t_client_foreign_assets_refused() {
    new_case client-foreign
    client_case_setup
    # 外来启动包装
    mkdir -p "$HOME/.local/bin"
    printf 'exec /opt/my-own-moonlight/run\n' >"$HOME/.local/bin/moonlight"
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '外来包装拒绝安装' 1 "$?"
    assert_contains '报错拒绝覆盖外来' "$(cat "$CASE/err")" '拒绝覆盖外来'
    assert_eq '外来包装字节不变' 'exec /opt/my-own-moonlight/run' "$(cat "$HOME/.local/bin/moonlight")"
    assert_not_contains '拒止发生在下载之前' "$(cat "$MOCK_LOG")" 'curl'
    rm -f "$HOME/.local/bin/moonlight"
    # 外来桌面项
    mkdir -p "$HOME/.local/share/applications"
    printf '[Desktop Entry]\nName=MyMoonlight\n' \
        >"$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '外来桌面项拒绝安装' 1 "$?"
    assert_contains '外来桌面项字节不变' \
        "$(cat "$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop")" 'Name=MyMoonlight'
    rm -f "$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"
    # 外来目标版本目录（无标记）
    mkdir -p "$HOME/.local/opt/moonlight/6.1.0"
    printf 'foreign build\n' >"$HOME/.local/opt/moonlight/6.1.0/AppRun"
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '外来目标目录拒绝安装' 1 "$?"
    assert_eq '外来目标目录字节不变' 'foreign build' "$(cat "$HOME/.local/opt/moonlight/6.1.0/AppRun")"
    rm -rf "$HOME/.local/opt/moonlight"
    # 中断残留的暂存目录：拒绝并提示人工处理
    mkdir -p "$HOME/.local/opt/moonlight/.staging-6.1.0.999"
    printf 'residue\n' >"$HOME/.local/opt/moonlight/.staging-6.1.0.999/.quick-deploy-sha256"
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '暂存残留拒绝安装' 1 "$?"
    assert_contains '报错点名残留目录' "$(cat "$CASE/err")" '残留'
    assert_file_exists '残留目录未被擅动' "$HOME/.local/opt/moonlight/.staging-6.1.0.999/.quick-deploy-sha256"
    end_case
}

t_uninstall_hidden_staging() {
    new_case uninstall-staging
    client_case_setup
    bash "$MODULE_DIR/install-client.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '前置安装成功' 0 "$?"
    # 受管隐藏残留（带标记）应被清理；外来隐藏目录必须保留
    mkdir -p "$HOME/.local/opt/moonlight/.staging-6.1.0.123"
    printf 'residue\n' >"$HOME/.local/opt/moonlight/.staging-6.1.0.123/.quick-deploy-sha256"
    mkdir -p "$HOME/.local/opt/moonlight/.foreign-hidden"
    printf 'keep me\n' >"$HOME/.local/opt/moonlight/.foreign-hidden/file"
    bash "$MODULE_DIR/uninstall.sh" --client >"$CASE/out2" 2>"$CASE/err2"
    assert_eq '卸载成功' 0 "$?"
    assert_file_absent '受管隐藏暂存目录已移除' "$HOME/.local/opt/moonlight/.staging-6.1.0.123"
    assert_file_exists '外来隐藏目录被保留' "$HOME/.local/opt/moonlight/.foreign-hidden/file"
    assert_contains '报告保留外来隐藏目录' "$(cat "$CASE/err2")" '外来'
    end_case
}

# ---- F8/需求9: doctor --client 显式缺失即失败 -----------------------------------------

t_doctor_client_missing_severity() {
    new_case doctor-client-missing
    # 显式 --client：未安装 = 失败，退出码 1
    bash "$MODULE_DIR/doctor.sh" --client >"$CASE/out" 2>"$CASE/err"
    assert_eq '显式 --client 未安装退出码 1' 1 "$?"
    assert_contains '显式模式记失败' "$(cat "$CASE/out")" '[失败] 未安装 Moonlight'
    # 自动模式：同一状态只警告（主机侧缺失会导致整体退出 1，这里只验证严重性文案）
    export MOCK_SUNSHINE_INSTALLED=0
    bash "$MODULE_DIR/doctor.sh" >"$CASE/out2" 2>"$CASE/err2"
    assert_contains '自动模式记警告' "$(cat "$CASE/out2")" '[警告] 未安装 Moonlight'
    assert_not_contains '自动模式不记客户端失败' "$(cat "$CASE/out2")" '[失败] 未安装 Moonlight'
    end_case
}

# ---- 需求10: 原子写入 + 权限 + 备份策略 ------------------------------------------------

t_config_mode_and_backup_policy() {
    new_case config-mode
    host_standard_setup
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '首次安装成功' 0 "$?"
    local conf="$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    assert_eq '新建配置权限 0600' '600' "$(stat -c %a "$conf")"
    assert_file_absent '首次创建无备份' "$conf.bak"
    end_case

    new_case config-mode-strict
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf '# strict\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    chmod 400 "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '严格权限配置安装成功' 0 "$?"
    assert_eq '更严的 0400 被保留' '400' "$(stat -c %a "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")"
    end_case

    new_case config-mode-loose
    host_standard_setup
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    printf 'my_key = keep\n' >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    chmod 644 "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
    bash "$MODULE_DIR/install-host.sh" >"$CASE/out" 2>"$CASE/err"
    assert_eq '宽松权限配置安装成功' 0 "$?"
    assert_eq '0644 被收紧为 0600' '600' "$(stat -c %a "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")"
    assert_eq '备份权限同为 0600' '600' "$(stat -c %a "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak")"
    assert_contains '未知键在原子重写后保留' "$(cat "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")" 'my_key = keep'
    end_case
}

# ---- 需求14: git 卫生 ------------------------------------------------------------------

t_git_hygiene() {
    new_case git-hygiene
    if git -C "$MODULE_DIR" diff --check 2>"$CASE/giterr"; then
        pass 'git diff --check 干净'
    else
        fail "git diff --check 有空白问题 — $(cat "$CASE/giterr")"
    fi
    if git -C "$MODULE_DIR" diff --cached --check 2>"$CASE/giterr2"; then
        pass 'git diff --cached --check 干净'
    else
        fail "git diff --cached --check 有空白问题 — $(cat "$CASE/giterr2")"
    fi
    local f bad=0
    for f in "$MODULE_DIR"/lib/common.sh "$MODULE_DIR"/install-host.sh \
             "$MODULE_DIR"/install-client.sh "$MODULE_DIR"/doctor.sh \
             "$MODULE_DIR"/uninstall.sh "$TESTS_DIR"/run.sh; do
        if git diff --no-index --check /dev/null "$f" >"$CASE/noidx" 2>&1; then
            pass "git diff --no-index --check: $(basename "$f")"
        elif grep -q 'trailing whitespace\|space before tab' "$CASE/noidx"; then
            fail "git diff --no-index --check: $(basename "$f") — $(head -3 "$CASE/noidx")"
            bad=1
        else
            pass "git diff --no-index --check: $(basename "$f")"
        fi
    done
    return $bad
}

# ---- 驱动 ----------------------------------------------------------------------------

main() {
    say '== quick-deploy/sunshine-moonlight 测试 =='
    SYNTAX_CASE="$(mktemp -d /tmp/qd-sm-syntax.XXXXXX)"
    CASE="$SYNTAX_CASE"
    t_syntax
    rm -rf "$SYNTAX_CASE"
    CASE=''

    t_host_digest_mismatch_blocks
    t_host_version_floor
    t_host_cve_floor_no_bypass
    t_host_success_idempotent_config
    t_host_service_failure_visible
    t_host_os_reject
    t_root_reject
    t_client_arch_reject
    t_client_digest_mismatch
    t_client_size_mismatch
    t_client_install_and_converge
    t_uninstall_client
    t_uninstall_host_package_ownership
    t_uninstall_host_state_destructive
    t_doctor_host_outdated_fails
    t_doctor_host_current_passes
    t_doctor_capture_xcb_flagged
    t_temp_cleanup_host
    t_temp_cleanup_client
    t_capture_auto_removes_key
    t_capture_default_normalizes_stale
    t_capture_default_preserves_valid
    t_doctor_capture_auto_flagged
    t_restart_on_changed_active_config
    t_enable_now_when_inactive
    t_custom_base_port
    t_invalid_base_port_rejected
    t_doctor_listeners_custom_port
    t_missing_unit_detected
    t_doctor_missing_unit
    t_ownership_state_apt_failure
    t_ownership_state_preserved
    t_installed_version_convergence
    t_uninstall_owned_clears_state
    t_bind_address_validation
    t_version_tag_validation
    t_client_pinned_version_only
    t_client_foreign_assets_refused
    t_uninstall_hidden_staging
    t_doctor_client_missing_severity
    t_config_mode_and_backup_policy
    t_git_hygiene

    say ''
    say "结果: 断言总数 $((PASSED+FAILED))，通过 $PASSED，失败 $FAILED"
    [ "$FAILED" -eq 0 ]
}

main "$@"
