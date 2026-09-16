#!/bin/bash
# Isolated HOME/PATH fixtures. Only the dummy .deb test invokes real apt, with --simulate.
set -euo pipefail
TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(dirname "$TESTS_DIR")"
export QD_TEST_BINDING_HELPER="$MODULE_DIR/service/check-tailnet.py"
BASE_PATH="$PATH"
ORIGINAL_HOME="$HOME"
# shellcheck source=../lib/common.sh
. "$MODULE_DIR/lib/common.sh"
CASE=''
MODULE_COPY=''
PASSED=0
FAILED=0
cleanup() { if [ -n "$CASE" ]; then rm -rf -- "$CASE"; fi; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
check() {
    local label="$1"; shift
    if "$@"; then PASSED=$((PASSED+1)); printf 'PASS %s\n' "$label";
    else FAILED=$((FAILED+1)); printf 'FAIL %s\n' "$label"; fi
}
contains() { grep -Fq -- "$2" "$1"; }
absent() { ! grep -Fq -- "$2" "$1"; }
# The suite must leave the real checkout exactly as it found it. Record metadata
# (type/mode/size/mtime) for the example and both inventory paths before any case
# runs, then re-check after the last one. Personal inventories are lstat-ed only:
# the suite never reads, copies, creates, or deletes them.
checkout_path_state() {
    if [ ! -e "$1" ] && [ ! -L "$1" ]; then printf 'absent\n'; return 0; fi
    stat -c '%F|%a|%s|%y' -- "$1"
}
REAL_EXAMPLE_STATE="$(checkout_path_state "$MODULE_DIR/machines.example.yaml")"
REAL_ACTUAL_STATE="$(checkout_path_state "$MODULE_DIR/machines.yaml")"
REAL_LEGACY_STATE="$(checkout_path_state "$MODULE_DIR/machines.local.yaml")"
run() {
    RC=0
    bash "$MODULE_DIR/$1" "${@:2}" >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
run_xtrace() {
    RC=0
    bash -x "$MODULE_DIR/$1" "${@:2}" >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
run_resolver() {
    RC=0
    bash -c 'sed "$2" "$1/commands/install-client.sh" >"$3/client-resolver-lib.sh"; sed -i "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"$1\"|" "$3/client-resolver-lib.sh"; source "$3/client-resolver-lib.sh"; resolve_moonlight_release; printf "resolved=%s %s %s\\n" "$SELECTED_TAG" "$SELECTED_SHA" "$SELECTED_SIZE"' bash "$MODULE_DIR" '$d' "$CASE" >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
run_sunshine_resolver() {
    RC=0
    bash -c 'sed "$2" "$1/commands/install-host.sh" >"$3/host-resolver-lib.sh"; sed -i "s|^SCRIPT_DIR=.*|SCRIPT_DIR=\"$1\"|" "$3/host-resolver-lib.sh"; source "$3/host-resolver-lib.sh"; VERSION_ID=24.04; resolve_sunshine_release amd64; printf "resolved=%s %s %s %s %s\\n" "$VERSION_TAG" "$SELECTED_NAME" "$SELECTED_URL" "$SELECTED_SHA" "$SELECTED_SIZE"' bash "$MODULE_DIR" '$d' "$CASE" >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
run_real_curlrc_probe() {
    local curl_home="$CASE/curl-home" curl_config_home="$CASE/curl-config-home" generated="$CASE/generated-curl.c" token='QD_REAL_CURLRC_SENTINEL'
    mkdir -p "$curl_home" "$curl_config_home" "$CASE/real-curl-bin"
    printf 'libcurl = "%s"\n' "$generated" >"$curl_home/.curlrc"
    cp "$curl_home/.curlrc" "$curl_config_home/.curlrc"
    # Positive control: the isolated fixture really serializes stdin config unless
    # --disable is first. It uses only file:// and is removed before production probe.
    printf 'header = "Authorization: Bearer %s"\n' "$token" | HOME="$curl_home" CURL_HOME="$curl_config_home" /usr/bin/curl --proto =file --config - --silent file:///dev/null >"$CASE/curlrc-control.out" 2>"$CASE/curlrc-control.err"
    CURLRC_CONTROL_GENERATED=false
    if [ -f "$generated" ] && grep -Fq "$token" "$generated"; then CURLRC_CONTROL_GENERATED=true; fi
    rm -f "$generated"
    cat >"$CASE/real-curl-bin/curl" <<'CURL_WRAPPER'
#!/bin/bash
printf 'first=%s args=%s\n' "$1" "$(printf '%q ' "$@")" >>"$CASE/real-curl-wrapper.log"
[ "$1" = --disable ] || exit 88
shift
exec /usr/bin/curl --disable --proto =file "$@"
CURL_WRAPPER
    chmod +x "$CASE/real-curl-bin/curl"
    RC=0
    HOME="$curl_home" CURL_HOME="$curl_config_home" PATH="$CASE/real-curl-bin:/usr/bin:/bin" GITHUB_TOKEN="$token" \
        bash -c 'source "$1/lib/common.sh"; qd_github_release_fetch LizardByte/Sunshine latest metadata' bash "$MODULE_DIR" >"$CASE/curlrc.out" 2>"$CASE/curlrc.err" || RC=$?
    CURLRC_GENERATED=false
    [ ! -e "$generated" ] || CURLRC_GENERATED=true
}
run_from() {
    RC=0
    (cd -- "$1" && "$2" "${@:3}") >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
# Root-install tests copy only public module code into a case-local module, so example
# generation never writes into the real checkout and no private inventory is copied.
make_module_copy() {
    MODULE_COPY="$CASE/${1:-module copy}/sunshine-moonlight"
    mkdir -p "$MODULE_COPY"
    cp -a "$MODULE_DIR/install.sh" "$MODULE_DIR/run_server.sh" "$MODULE_DIR/commands" "$MODULE_DIR/lib" "$MODULE_DIR/service" "$MODULE_COPY/"
}
run_install() {
    RC=0
    bash "$MODULE_COPY/install.sh" "$@" >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
write_expected_example() {
    python3 "$MODULE_DIR/lib/machines_example.py" --stdout >"$CASE/expected-example"
}
end_case() {
    rm -rf -- "$CASE"; CASE=''; MODULE_COPY=''
    export PATH="$BASE_PATH" HOME="$ORIGINAL_HOME"
    unset XDG_CONFIG_HOME CONFIGURATION_DIRECTORY QD_TEST_SYSTEM_PYTHON QD_TEST_YAML_AVAILABLE
    # All mutable mock controls are case-local.
    while IFS= read -r key; do unset "$key"; done < <(compgen -v | grep '^MOCK_' || true)
    unset GITHUB_TOKEN GH_TOKEN TZ TMPDIR
}
new_case() {
    CASE="$(mktemp -d /tmp/qd-sm-test.XXXXXX)"
    export CASE HOME="$CASE/home" PATH="$CASE/bin:$BASE_PATH"
    export XDG_STATE_HOME="$HOME/.local/state"
    export QD_HOST_STATE_DIR="$CASE/host-state" QD_OS_RELEASE_FILE="$CASE/os-release"
    export QD_SUNSHINE_CONFIG_DIR="$HOME/.config/sunshine" QD_PROC_ROOT="$CASE/proc"
    export QD_UINPUT_NODE="$CASE/uinput" QD_UHID_NODE="$CASE/uhid"
    export TMPDIR="$CASE/tmp"
    mkdir -p "$HOME" "$CASE/bin" "$CASE/tmp" "$CASE/fixtures" "$QD_PROC_ROOT/4242" "$CASE/installed/bin"
    printf 'ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04"\n' >"$QD_OS_RELEASE_FILE"
    : >"$CASE/log"; : >"$CASE/version"
    printf 'old executable\n' >"$CASE/installed/bin/sunshine"
    touch "$QD_UINPUT_NODE" "$QD_UHID_NODE"
    export MOCK_MANAGER_ENV='DISPLAY=:1'
    write_mocks
    make_deb sunshine amd64 "$QD_SUNSHINE_VERSION-1+ubuntu24.04"
    write_api "v$QD_SUNSHINE_VERSION" "sunshine_$QD_SUNSHINE_VERSION-1+ubuntu24.04_amd64.deb"
}
make_deb() {
    mkdir -p "$CASE/pkg/DEBIAN"
    printf 'Package: %s\nVersion: %s\nArchitecture: %s\nMaintainer: Test <test@example.invalid>\nDescription: Isolated package fixture\n' "$1" "$3" "$2" >"$CASE/pkg/DEBIAN/control"
    /usr/bin/dpkg-deb --build "$CASE/pkg" "$CASE/fixtures/sunshine.deb" >/dev/null
}
write_api() {
    python3 - "$CASE" "$1" "$2" <<'PY_API'
import hashlib, json, pathlib, sys
from urllib.parse import quote
case=pathlib.Path(sys.argv[1]); tag=sys.argv[2]; name=sys.argv[3]
deb=case/'fixtures/sunshine.deb'
data={'id': 1, 'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [{'id': 2, 'name': name, 'browser_download_url': f'https://github.com/LizardByte/Sunshine/releases/download/{tag}/{quote(name, safe="")}', 'digest': 'sha256:'+hashlib.sha256(deb.read_bytes()).hexdigest(), 'size': deb.stat().st_size}]}
(case/'fixtures/api-sunshine.json').write_text(json.dumps(data))
PY_API
}
write_moonlight_api() {
    python3 - "$CASE" <<'PY_MOONLIGHT_API'
import hashlib, json, pathlib, sys
case=pathlib.Path(sys.argv[1]); asset=case/'fixtures/moonlight.AppImage'; tag='v6.1.0'; name='Moonlight-6.1.0-x86_64.AppImage'
data={'id': 999, 'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [{'id': 998, 'name': name, 'browser_download_url': f'https://github.com/moonlight-stream/moonlight-qt/releases/download/{tag}/{name}', 'digest': 'sha256:'+hashlib.sha256(asset.read_bytes()).hexdigest(), 'size': asset.stat().st_size}]}
(case/'fixtures/api-moonlight.json').write_text(json.dumps(data))
PY_MOONLIGHT_API
}
write_audited_moonlight_api() {
    python3 - "$CASE" "$1" <<'PY_AUDITED_MOONLIGHT'
import json, pathlib, sys
case=pathlib.Path(sys.argv[1]); digest=None if sys.argv[2] == 'null' else sys.argv[2]
tag='v6.1.0'; name='Moonlight-6.1.0-x86_64.AppImage'
data={'id': 175337682, 'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': [{'id': 193059073, 'name': name, 'browser_download_url': f'https://github.com/moonlight-stream/moonlight-qt/releases/download/{tag}/{name}', 'digest': digest, 'size': 55325888}]}
(case/'fixtures/api-moonlight.json').write_text(json.dumps(data))
PY_AUDITED_MOONLIGHT
}
write_conf() {
    mkdir -p "$QD_SUNSHINE_CONFIG_DIR"
    cat >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" <<'CONF'
upnp = disabled
address_family = ipv4
bind_address = 100.64.0.2
csrf_allowed_origins = https://100.64.0.2:47990
CONF
}
installed() { printf '%s\n' "${1:-$QD_SUNSHINE_VERSION-1+ubuntu24.04}" >"$CASE/version"; }
active() {
    touch "$CASE/active" "$CASE/enabled"
    ln -sf "$CASE/installed/bin/sunshine" "$QD_PROC_ROOT/4242/exe"
    cp "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "$CASE/running.conf"
}
system_python_fixture() {
    cat >"$CASE/system-python" <<'MOCK'
#!/bin/bash
if [ "${1:-}" = -c ] && [ "${2:-}" = 'import yaml' ]; then
    [ "${QD_TEST_YAML_AVAILABLE:-0}" = 1 ] || [ -f "$CASE/python3-yaml-installed" ]
    exit $?
fi
exec /usr/bin/python3 "$@"
MOCK
    chmod +x "$CASE/system-python"
    export QD_TEST_SYSTEM_PYTHON="$CASE/system-python"
}

client_fixture() {
    cat >"$CASE/fixtures/moonlight.AppImage" <<'AI'
#!/bin/bash
[ "${1:-}" = --appimage-extract ] || exit 1
[ "${MOCK_EXTRACT_FAIL:-0}" = 0 ] || exit 1
mkdir -p squashfs-root/usr/bin
printf '#!/bin/sh\nprintf "fixture only\\n"\n' >squashfs-root/usr/bin/moonlight
chmod +x squashfs-root/usr/bin/moonlight
ln -s usr/bin/moonlight squashfs-root/AppRun
printf '[Desktop Entry]\nType=Application\nName=Moonlight\nExec=moonlight\nIcon=moonlight\n' >squashfs-root/com.moonlight_stream.Moonlight.desktop
printf '<svg/>\n' >squashfs-root/moonlight.svg
AI
    write_moonlight_api
}
write_mocks() {
    cat >"$CASE/bin/curl" <<'MOCK'
#!/bin/bash
# Keep the original argv for security assertions. Only GitHub metadata invokes
# curl with --config -, so asset downloads must not consume interactive stdin.
args=("$@")
out=''; headers=''; url=''; status="${MOCK_API_STATUS:-200}"; config=''; read_config=false
for ((i=0; i<${#args[@]}; i++)); do
    case "${args[i]}" in
        --config)
            ((i+=1))
            [ "${args[i]:-}" = - ] && read_config=true;;
        -o) ((i+=1)); out="${args[i]:-}";;
        -D) ((i+=1)); headers="${args[i]:-}";;
        -w) ((i+=1));;
        https:*) url="${args[i]}";;
    esac
done
$read_config && config="$(cat)"
argv_has_auth=no
for arg in "${args[@]}"; do [[ "$arg" == *Authorization* ]] && argv_has_auth=yes; done
if [[ "$url" == *api.github.com* ]]; then
    case "$url" in
        *LizardByte/Sunshine*) src=api-sunshine.json;;
        *moonlight-stream/moonlight-qt*) src=api-moonlight.json;;
        *) src='';;
    esac
    [[ "$config" == *'Authorization: Bearer '* ]] && auth=present || auth=absent
    printf 'api url=%s auth=%s env_github=%s env_gh=%s argv_has_auth=%s\n' \
        "$url" "$auth" "${GITHUB_TOKEN:+present}" "${GH_TOKEN:+present}" "$argv_has_auth" >>"$CASE/log"
    printf 'HTTP/1.1 %s fixture\r\nX-RateLimit-Remaining: %s\r\nX-RateLimit-Reset: %s\r\nRetry-After: %s\r\n\r\n' \
        "$status" "${MOCK_RATE_REMAINING:-1}" "${MOCK_RATE_RESET:-1789145352}" "${MOCK_RETRY_AFTER:-30}" >"$headers"
    [ -n "$src" ] && cp "$CASE/fixtures/$src" "$out" || : >"$out"
    printf '%s' "$status"
    exit "${MOCK_CURL_EXIT:-0}"
fi
printf 'asset url=%s auth_in_argv=%s\n' "$url" "$argv_has_auth" >>"$CASE/log"
case "$url" in *.deb) src=sunshine.deb;; *.AppImage) src=moonlight.AppImage;; *) exit 22;; esac
cp "$CASE/fixtures/$src" "$out"
MOCK
    cat >"$CASE/bin/sudo" <<'MOCK'
#!/bin/bash
printf 'sudo %s\n' "$*" >>"$CASE/log"
case "$1" in apt-get|setcap) exec "$@";; *) echo 'Unexpected sudo mutation blocked' >&2; exit 99;; esac
MOCK
    cat >"$CASE/bin/apt-get" <<'MOCK'
#!/bin/bash
printf 'apt-get %s\n' "$*" >>"$CASE/log"
[ "${MOCK_APT_FAIL:-0}" = 0 ] || exit 1
case "$1" in
 install)
    file="${@: -1}"
    if [ "$file" = python3-yaml ]; then
        touch "$CASE/python3-yaml-installed"
        exit 0
    fi
    [[ "$file" = /*.deb ]] || { echo "Unsupported file $file" >&2; exit 100; }
    /usr/bin/dpkg-deb -f "$file" Package >/dev/null || exit 100
    version="$(/usr/bin/dpkg-deb -f "$file" Version)"
    printf '%s\n' "${MOCK_APT_VERSION:-$version}" >"$CASE/version"
    printf 'new executable\n' >"$CASE/new-exe"
    mv "$CASE/new-exe" "$CASE/installed/bin/sunshine";;
 remove) : >"$CASE/version";;
 *) exit 99;;
esac
MOCK
    cat >"$CASE/bin/dpkg" <<'MOCK'
#!/bin/bash
case "$1" in
 -L) printf '%s\n' "$CASE/installed/bin/sunshine";;
 --print-architecture) echo "${MOCK_ARCH:-amd64}";;
 *) exec /usr/bin/dpkg "$@";;
esac
MOCK
    cat >"$CASE/bin/dpkg-query" <<'MOCK'
#!/bin/bash
[ -s "$CASE/version" ] || exit 1
case "$*" in *'db:Status-Status'*) echo installed;; *Version*) cat "$CASE/version";; *) exit 99;; esac
MOCK
    cat >"$CASE/bin/systemctl" <<'MOCK'
#!/bin/bash
printf 'systemctl %s\n' "$*" >>"$CASE/log"
[ "$1" = --user ] || exit 99
shift
case "$1" in
 show-environment) printf '%s\n' "$MOCK_MANAGER_ENV"; exit 0;;
 show)
    unit="$2"; prop="$4"
    present=0; [ -s "$CASE/version" ] && present=1
    case "$prop" in
      LoadState) if [ "$unit" = sunshine.service ]; then echo not-found; elif [ "${MOCK_UNIT_PRESENT:-$present}" = 1 ]; then echo "${MOCK_LOAD_STATE:-loaded}"; else echo not-found; fi;;
      FragmentPath) echo "${MOCK_FRAGMENT:-/usr/lib/systemd/user/app-dev.lizardbyte.app.Sunshine.service}";;
      DropInPaths)
        if [ "${MOCK_DROPS+x}" ]; then printf '%s\n' "$MOCK_DROPS";
        elif [ -f "$CASE/loaded-retry" ]; then cat "$CASE/loaded-retry-path"; fi;;
      Restart) echo "${MOCK_RESTART:-on-failure}";;
      RestartUSec) echo "${MOCK_RESTART_USEC:-5s}";;
      StartLimitIntervalUSec) if [ -f "$CASE/loaded-retry" ]; then echo "${MOCK_LIMIT:-0}"; else echo 8min\ 20s; fi;;
      PartOf) if [ -f "$CASE/loaded-retry" ]; then echo "${MOCK_PARTOF:-graphical-session.target}"; fi;;
      NeedDaemonReload) echo "${MOCK_NEED_RELOAD:-no}";;
      Result) echo "${MOCK_RESULT:-success}";;
      ExecStartPre)
        if [ "${MOCK_PRE+x}" ]; then printf '%s\n' "$MOCK_PRE"; else
            printf '{ path=/bin/sleep ; argv[]=/bin/sleep 5 ; ignore_errors=no ; }'
            if [ -f "$CASE/loaded-retry" ]; then
                python3 - "$CASE/loaded-retry" <<'PY_MOCK'
import shlex, sys
for line in open(sys.argv[1]):
    if line.startswith('ExecStartPre='):
        args = shlex.split(line.partition('=')[2].strip().replace('%%', '%').lstrip(':'))
        print(' ; { path=' + args[0] + ' ; argv[]=' + ' '.join(args) + ' ; ignore_errors=no ; }')
PY_MOCK
            fi
        fi;;
      ExecStart) echo "${MOCK_EXECSTART:-{ path=/usr/bin/sunshine ; argv[]=/usr/bin/sunshine ; }}";;
      Environment) echo "${MOCK_UNIT_ENV:-}";;
      EnvironmentFiles|ConfigurationDirectory) echo '';;
      MainPID) if [ -f "$CASE/active" ]; then echo 4242; else echo 0; fi;;
      *) exit 99;;
    esac; exit 0;;
 is-active) [ -f "$CASE/active" ] || [ -f "$CASE/retrying" ]; exit $?;;
 is-enabled) [ -f "$CASE/enabled" ]; exit $?;;
 daemon-reload)
    drop="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/app-dev.lizardbyte.app.Sunshine.service.d/quick-deploy-retry.conf"
    if [ -f "$drop" ]; then
        cp "$drop" "$CASE/loaded-retry"
        # Native systemd 255 static loading records resolved parent paths.
        readlink -f "$drop" >"$CASE/loaded-retry-path"
    else rm -f "$CASE/loaded-retry" "$CASE/loaded-retry-path"; fi
    exit 0;;
 reset-failed) exit 0;;
 enable) touch "$CASE/enabled"; exit 0;;
 disable|stop)
    [ "${MOCK_STOP_FAIL:-0}" = 0 ] || exit 1
    rm -f "$CASE/active" "$CASE/retrying"
    if [ "$1" = disable ] && [ "${MOCK_STILL_ENABLED:-0}" = 0 ]; then rm -f "$CASE/enabled"; fi
    exit 0;;
 start|restart)
    [ "${MOCK_START_FAIL:-0}" = 0 ] || exit 1
    touch "$CASE/active"
    ln -sf "$CASE/installed/bin/sunshine" "$QD_PROC_ROOT/4242/exe"
    conf="${QD_SUNSHINE_CONFIG_DIR:-${CONFIGURATION_DIRECTORY:-${XDG_CONFIG_HOME:-$HOME/.config}}/sunshine}/sunshine.conf"
    cp "$conf" "$CASE/running.conf"; exit 0;;
 *) echo "Unexpected systemctl $*" >&2; exit 99;;
esac
MOCK
    cat >"$CASE/bin/loginctl" <<'MOCK'
#!/bin/bash
case "$1" in
 list-sessions) printf '2 %s test seat0 tty2\n' "$(/usr/bin/id -u)";;
 show-session) printf 'Type=%s\nActive=%s\nRemote=no\n' "${MOCK_SESSION_TYPE:-x11}" "${MOCK_SESSION_ACTIVE:-yes}";;
 *) exit 99;;
esac
MOCK
    cat >"$CASE/bin/tailscale" <<'MOCK'
#!/bin/bash
case "$1" in ip) echo "${MOCK_TS_IP:-100.64.0.2}";; status) printf '{"BackendState":"%s"}\n' "${MOCK_TS_STATE:-Running}";; *) exit 99;; esac
MOCK
    cat >"$CASE/bin/ip" <<'MOCK'
#!/bin/bash
printf '7: tailscale0 inet %s/32 scope global tailscale0\n' "${MOCK_ASSIGNED_IP:-100.64.0.2}"
MOCK
    cat >"$CASE/bin/ss" <<'MOCK'
#!/bin/bash
[ -f "$CASE/active" ] && [ "${MOCK_LISTEN:-yes}" != no ] || exit 0
conf="$CASE/running.conf"
base="$(sed -n 's/^port *= *\([0-9]*\).*/\1/p' "$conf")"; base="${base:-47989}"
if [ "${MOCK_LISTEN_BIND+x}" ]; then bind="$MOCK_LISTEN_BIND";
else bind="$(python3 "$QD_TEST_BINDING_HELPER" --binding-get "$conf" bind_address)" || exit 1; fi
for off in -5 0 1 21; do
 [ "${MOCK_MISSING_OFFSET:-none}" != "$off" ] || continue
 owner="users:((\"sunshine\",pid=${MOCK_OWNER_PID:-4242},fd=9))"
 [ "${MOCK_HIDE_OWNER:-0}" = 0 ] || owner=''
 printf 'LISTEN 0 4096 %s:%s 0.0.0.0:* %s\n' "$bind" "$((base+off))" "$owner"
done
MOCK
    cat >"$CASE/bin/getcap" <<'MOCK'
#!/bin/bash
if [ "${MOCK_CAPS_MISSING:-0}" = 1 ] && [ ! -f "$CASE/caps-repaired" ]; then exit 0; fi
printf '%s cap_sys_admin,cap_sys_nice=p\n' "$1"
MOCK
    cat >"$CASE/bin/setcap" <<'MOCK'
#!/bin/bash
printf 'setcap %s\n' "$*" >>"$CASE/log"
touch "$CASE/caps-repaired"
MOCK
    cat >"$CASE/bin/ps" <<'MOCK'
#!/bin/bash
[ ! -f "$CASE/active" ] || printf '4242 %s sunshine\n' "$(/usr/bin/id -u)"
printf '%s\n' "${MOCK_EXTRA_PROCS:-}"
MOCK
    cat >"$CASE/bin/id" <<'MOCK'
#!/bin/bash
if [ "$1" = -u ] && [ -n "${MOCK_UID:-}" ]; then echo "$MOCK_UID"; else exec /usr/bin/id "$@"; fi
MOCK
    cat >"$CASE/bin/uname" <<'MOCK'
#!/bin/bash
if [ "$1" = -m ]; then echo "${MOCK_MACHINE:-x86_64}"; else exec /usr/bin/uname "$@"; fi
MOCK
    cat >"$CASE/bin/mv" <<'MOCK'
#!/bin/bash
if [ "${MOCK_FAIL_PROMOTE:-0}" = 1 ] && [[ "$1" == */.staging-* ]]; then exit 1; fi
if [ "${MOCK_FAIL_CONFIG_COMMIT:-0}" = 1 ] && [ "${@: -1}" = "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" ]; then exit 1; fi
exec /usr/bin/mv "$@"
MOCK
    cat >"$CASE/bin/ln" <<'MOCK'
#!/bin/bash
if [ "${MOCK_RETRY_COLLISION:-0}" = 1 ] && [[ "${@: -1}" == */check-tailnet.py ]]; then
    mkdir -p "${@: -1}"
    printf 'foreign collision\n' >"${@: -1}/sentinel"
fi
exec /usr/bin/ln "$@"
MOCK
    cat >"$CASE/bin/rm" <<'MOCK'
#!/bin/bash
printf 'rm %s\n' "$*" >>"$CASE/log"
for path in "$@"; do
    case "$path" in
      */app-dev.lizardbyte.app.Sunshine.service.d/check-tailnet.py|*/app-dev.lizardbyte.app.Sunshine.service.d/quick-deploy-retry.conf)
        if [ -f "$CASE/active" ] || [ -f "$CASE/retrying" ]; then
            echo 'Refusing owned retry deletion before stop' >&2; exit 99
        fi;;
    esac
done
exec /usr/bin/rm "$@"
MOCK
    cat >"$CASE/bin/sleep" <<'MOCK'
#!/bin/bash
exit 0
MOCK
    cat >"$CASE/bin/update-desktop-database" <<'MOCK'
#!/bin/bash
exit 0
MOCK
    chmod +x "$CASE/bin/"*
}

# Packaging: real apt suffix classification, actual deb metadata and Debian ordering.
new_case
make_deb qd-sunshine-audit-fixture all 1
cp "$CASE/fixtures/sunshine.deb" "$CASE/extensionless"
real_rc=0
LC_ALL=C /usr/bin/apt-get --simulate install "$CASE/extensionless" >"$CASE/apt-out" 2>&1 || real_rc=$?
check 'real apt rejects extensionless valid deb' test "$real_rc" -eq 100
check 'real apt identifies filename failure' contains "$CASE/apt-out" 'Unsupported file'
real_rc=0
LC_ALL=C /usr/bin/apt-get --simulate install "$CASE/fixtures/sunshine.deb" >"$CASE/apt-out" 2>&1 || real_rc=$?
check 'real apt accepts same deb bytes with suffix' test "$real_rc" -eq 0
check 'real apt selected only dummy package' contains "$CASE/apt-out" 'Inst qd-sunshine-audit-fixture'
end_case

new_case
run commands/install-host.sh
check 'fresh native-name install reaches listening state' test "$RC" -eq 0
check 'apt receives .deb suffix' contains "$CASE/log" '.deb'
check 'fresh install owns package' contains "$QD_HOST_STATE_DIR/host.state" 'package_preexisting=false'
check 'host prints Moonlight base port' contains "$CASE/out" '100.64.0.2:47989'
check 'host distinguishes untested live stream' contains "$CASE/out" '尚未验证实际 Desktop'
check 'new config mode is 600' test "$(stat -c %a "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")" = 600
: >"$CASE/log"
run commands/install-host.sh
check 'same Debian-revision upstream is idempotent' test "$RC" -eq 0
check 'same upstream skips apt' absent "$CASE/log" 'apt-get install'
check 'same upstream skips restart' absent "$CASE/log" 'systemctl --user restart'
check 'temporary files cleaned' test -z "$(find "$TMPDIR" -mindepth 1 -print -quit)"
run commands/doctor.sh --host
check 'healthy modeled host doctor passes' test "$RC" -eq 0
end_case

for failure in package arch version digest; do
 new_case
 case "$failure" in
 package) make_deb unrelated amd64 "$QD_SUNSHINE_VERSION";;
 arch) make_deb sunshine arm64 "$QD_SUNSHINE_VERSION";;
 version) make_deb sunshine amd64 2099.1.1;;
 esac
 write_api "v$QD_SUNSHINE_VERSION" 'sunshine-ubuntu-24.04-amd64.deb'
 if [ "$failure" = digest ]; then printf 'corruption' >>"$CASE/fixtures/sunshine.deb"; fi
 run commands/install-host.sh
 check "reject $failure before apt" test "$RC" -ne 0
 check "no privileged changes on $failure" absent "$CASE/log" 'sudo '
 check "no config on $failure" test ! -e "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 end_case
done
new_case
export MOCK_APT_VERSION=2099.1.1
run commands/install-host.sh
check 'exact installed Debian version required' test "$RC" -ne 0
check 'wrong installed version cannot claim ownership' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case
new_case
export MOCK_APT_FAIL=1
run commands/install-host.sh
check 'apt failure is surfaced without false consistency claim' test "$RC" -ne 0
check 'failed install keeps no ownership proof' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case
new_case
export MOCK_ARCH=arm64
make_deb sunshine arm64 "$QD_SUNSHINE_VERSION-1+ubuntu24.04"
write_api "v$QD_SUNSHINE_VERSION" "sunshine_$QD_SUNSHINE_VERSION-1+ubuntu24.04_arm64.deb"
run commands/install-host.sh
check 'native arm64 asset accepted' test "$RC" -eq 0
end_case
new_case
write_api "v$QD_SUNSHINE_VERSION" 'sunshine-ubuntu-22.04-amd64.deb'
run commands/install-host.sh
check 'other Ubuntu asset refused' test "$RC" -ne 0
check 'asset rejection before apt' absent "$CASE/log" 'apt-get'
end_case

# Lifecycle changes are independent of content changes.
for change in package caps stale; do
 new_case; installed; write_conf; active
 case "$change" in
 package) installed '1:2026.516.143833-1';;
 caps) export MOCK_CAPS_MISSING=1;;
 stale) printf 'stale\n' >"$CASE/stale"; ln -sf "$CASE/stale" "$QD_PROC_ROOT/4242/exe";;
 esac
 run commands/install-host.sh
 check "$change change succeeds" test "$RC" -eq 0
 check "$change change restarts active daemon" contains "$CASE/log" 'systemctl --user restart'
 if [ "$change" = package ]; then check 'old Debian epoch explicitly handled' contains "$CASE/log" '--allow-downgrades'; fi
 check "$change retains preexisting package ownership" contains "$QD_HOST_STATE_DIR/host.state" 'package_preexisting=true'
 end_case
done
new_case; installed 2099.1.1; write_conf; active
run commands/install-host.sh
check 'newer upstream preserved' test "$RC" -eq 0
check 'newer upstream not downloaded' absent "$CASE/log" 'asset url='
end_case
new_case; installed '1:2026.516.143833-99'; write_conf; active
run commands/doctor.sh --host
check 'epoch cannot satisfy upstream floor' test "$RC" -ne 0
end_case

# Release identity and digest are validated even when the payload itself is well-formed.
for issue in wrong-tag missing-digest; do
 new_case
 if [ "$issue" = wrong-tag ]; then
     write_api v2099.1.1 'sunshine-ubuntu-24.04-amd64.deb'
 else
     python3 - "$CASE/fixtures/api-sunshine.json" <<'PY_TEST'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['assets'][0]['digest']=None
with open(p,'w') as f: json.dump(d,f)
PY_TEST
 fi
 run commands/install-host.sh
 check "$issue fails before mutation" test "$RC" -ne 0
 check "$issue performs no sudo" absent "$CASE/log" 'sudo '
 end_case
done

# Preflight rejects unsupported service config and unusable transport before mutations.
for scenario in dropin fragment execstart unit-env xdg-mismatch configdir-mismatch tty no-display offline unassigned foreign-address; do
 new_case; installed
 case "$scenario" in
 dropin) export MOCK_DROPS='/tmp/user-custom.conf';;
 fragment) export MOCK_FRAGMENT="$HOME/.config/systemd/user/custom.service";;
 execstart) export MOCK_EXECSTART='{ argv[]=/usr/bin/sunshine /other/config ; }';;
 unit-env) export MOCK_UNIT_ENV='XDG_CONFIG_HOME=/other';;
 xdg-mismatch) unset QD_SUNSHINE_CONFIG_DIR; export XDG_CONFIG_HOME="$HOME/config";;
 configdir-mismatch) unset QD_SUNSHINE_CONFIG_DIR; export CONFIGURATION_DIRECTORY="$HOME/service-config";;
 tty) export MOCK_SESSION_TYPE=tty;;
 no-display) export MOCK_MANAGER_ENV='';;
 offline) export MOCK_TS_STATE=Stopped;;
 unassigned) export MOCK_ASSIGNED_IP=100.64.0.3;;
 esac
 if [ "$scenario" = foreign-address ]; then run commands/install-host.sh --bind-address 192.168.1.2; else run commands/install-host.sh; fi
 check "$scenario refused before mutations" test "$RC" -ne 0
 check "$scenario performs no sudo" absent "$CASE/log" 'sudo '
 check "$scenario performs no service start" absent "$CASE/log" 'systemctl --user start '
 end_case
done
for ip in 0.0.0.0 1.2.3.4. 010.0.0.1 999.1.1.1; do
 new_case; run commands/install-host.sh --bind-address "$ip"
 check "invalid/wildcard $ip rejected" test "$RC" -ne 0
 check "$ip rejected before sudo" absent "$CASE/log" 'sudo '
 end_case
done
new_case
unset QD_SUNSHINE_CONFIG_DIR
export XDG_CONFIG_HOME="$HOME/xdg-config" CONFIGURATION_DIRECTORY="$HOME/service-config"
export MOCK_MANAGER_ENV="$(printf 'DISPLAY=:1\nXDG_CONFIG_HOME=%s\nCONFIGURATION_DIRECTORY=%s' "$XDG_CONFIG_HOME" "$CONFIGURATION_DIRECTORY")"
run commands/install-host.sh
check 'matching service CONFIGURATION_DIRECTORY wins over XDG' test "$RC" -eq 0
check 'actual service directory configured' test -f "$CONFIGURATION_DIRECTORY/sunshine/sunshine.conf"
check 'unused default config not created' test ! -e "$HOME/.config/sunshine"
run commands/doctor.sh --host
check 'doctor uses matching effective directory' test "$RC" -eq 0
run commands/uninstall.sh --destroy-host-state
check 'destroy uses matching effective directory' test ! -e "$CONFIGURATION_DIRECTORY/sunshine"
end_case

new_case
unset QD_SUNSHINE_CONFIG_DIR
export XDG_CONFIG_HOME="$HOME/xdg-only"
export MOCK_MANAGER_ENV="$(printf 'DISPLAY=:1\nXDG_CONFIG_HOME=%s' "$XDG_CONFIG_HOME")"
run commands/install-host.sh
check 'XDG_CONFIG_HOME alone is honored' test "$RC" -eq 0
check 'XDG config created at actual service location' test -f "$XDG_CONFIG_HOME/sunshine/sunshine.conf"
end_case
new_case
export MOCK_SESSION_TYPE=wayland MOCK_MANAGER_ENV='WAYLAND_DISPLAY=wayland-0'
run commands/install-host.sh --capture portal
check 'Wayland portal configuration reaches modeled control listener' test "$RC" -eq 0
run commands/doctor.sh --host
check 'Wayland doctor discloses portal lock limitation' contains "$CASE/out" '锁屏会终止'
: >"$CASE/log"
run commands/install-host.sh --capture x11
check 'x11 on actual Wayland is preflight error' test "$RC" -ne 0
check 'x11 mismatch does not mutate packages' absent "$CASE/log" 'sudo '
end_case

# Config: valid choices preserved, explicit auto, comments, conflict checks, atomic backups.
for capture in kms portal x11 nvfbc wlr kwin; do
 new_case; write_conf
 case "$capture" in wlr|kwin) export MOCK_SESSION_TYPE=wayland MOCK_MANAGER_ENV='WAYLAND_DISPLAY=wayland-0';; esac
 printf 'capture = %s # intentional\n' "$capture" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run commands/install-host.sh
 check "preserve compatible $capture selection" test "$RC" -eq 0
 check "preserve $capture bytes/comment" contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "capture = $capture # intentional"
 run commands/doctor.sh --host
 check "doctor accepts compatible $capture session" test "$RC" -eq 0
 end_case
done
for capture in wlr kwin; do
 new_case; installed; write_conf; active
 run commands/install-host.sh --capture "$capture"
 check "explicit $capture on X11 rejected" test "$RC" -ne 0
 check "explicit $capture conflict precedes mutation" absent "$CASE/log" 'sudo '
 check "explicit $capture conflict preserves config" absent "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'capture ='
 printf 'capture = %s # intentional\n' "$capture" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run commands/install-host.sh
 check "preserved $capture on X11 rejected" test "$RC" -ne 0
 check "$capture conflict does not erase selection" contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "capture = $capture # intentional"
 check "$capture conflict leaves active service untouched" absent "$CASE/log" 'systemctl --user restart'
 run commands/doctor.sh --host
 check "doctor rejects $capture on X11" test "$RC" -ne 0
 check "doctor names $capture Wayland requirement" contains "$CASE/out" "capture=$capture 需要 Wayland"
 end_case
done
for capture in xcb auto typo; do
 new_case; write_conf
 printf 'capture = %s\n' "$capture" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run commands/install-host.sh
 check "invalid existing $capture is preflight error" test "$RC" -ne 0
 check "$capture not silently deleted" contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "capture = $capture"
 check "$capture fails before apt" absent "$CASE/log" 'apt-get'
 run commands/install-host.sh --capture auto
 check "explicit auto clears $capture" test "$RC" -eq 0
 check 'auto leaves no selector' absent "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'capture ='
 end_case
done
new_case; installed; write_conf; active
cat >"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" <<'CONF'
# Keep unknown content
custom_key = custom value # untouched
port = 48000#chosen port
address_family = both # old choice
capture = portal # selected
csrf_allowed_origins = https://existing.example:47990#trusted
CONF
cp "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "$CASE/before"
run commands/install-host.sh
check 'commented config converges successfully' test "$RC" -eq 0
check 'unknown scalar/comment preserved' contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'custom_key = custom value # untouched'
check 'IPv4 setting converged as native scalar' grep -qx 'address_family = ipv4' "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
check 'IPv4 comment preserved standalone' grep -qx '# old choice' "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
check 'origin inserted before comment' contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'https://existing.example:47990, https://100.64.0.2:48001 #trusted'
check 'custom Moonlight address uses base' contains "$CASE/out" '100.64.0.2:48000'
check 'custom UI address uses base+1' contains "$CASE/out" 'https://100.64.0.2:48001'
check 'backup retains original bytes' cmp -s "$CASE/before" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
: >"$CASE/log"; run commands/install-host.sh
check 'unchanged rerun leaves backup intact' cmp -s "$CASE/before" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
check 'unchanged rerun does not restart' absent "$CASE/log" 'systemctl --user restart'
end_case
for port in 01029 1028 65515 999999999999999999999999999999 foo; do
 new_case; write_conf; printf 'port = %s\n' "$port" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run commands/install-host.sh
 check "invalid port $port rejected before sudo" absent "$CASE/log" 'sudo '
 check "invalid port $port fails" test "$RC" -ne 0
 end_case
done
new_case; write_conf; printf 'origin_web_ui_allowed = pc # local only\n' >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
run commands/install-host.sh
check 'local-only Web UI conflict rejected' test "$RC" -ne 0
check 'local-only authorization not broadened' contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'origin_web_ui_allowed = pc'
check 'local-only conflict before sudo' absent "$CASE/log" 'sudo '
end_case

new_case; installed; write_conf; active
printf 'custom = keep # comment\n' >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
printf 'upnp = enabled\n' >"$CASE/change"
cat "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" >>"$CASE/change"
cp "$CASE/change" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
export MOCK_FAIL_CONFIG_COMMIT=1
run commands/install-host.sh
check 'failed config promotion is surfaced' test "$RC" -ne 0
check 'failed config promotion preserves original' cmp -s "$CASE/change" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
check 'failed config promotion preserves original backup' cmp -s "$CASE/change" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
check 'failed config promotion does not restart daemon' absent "$CASE/log" 'systemctl --user restart'
end_case

# Required input and listener failures do not become readiness success.
new_case; rm "$QD_UINPUT_NODE"; run commands/install-host.sh
check 'missing uinput blocks ready result' test "$RC" -ne 0
check 'missing device never triggers usermod' absent "$CASE/log" 'usermod'
check 'missing device never starts service' absent "$CASE/log" 'systemctl --user start '
end_case
new_case; chmod 000 "$QD_UINPUT_NODE"; run commands/install-host.sh
check 'inaccessible uinput blocks readiness' test "$RC" -ne 0
check 'inaccessible uinput has no automatic group mutation' absent "$CASE/log" 'usermod'
end_case
new_case; rm "$QD_UHID_NODE"; run commands/install-host.sh
check 'missing uhid warns without blocking keyboard/mouse setup' test "$RC" -eq 0
check 'uhid warning scoped to controllers' contains "$CASE/out" '手柄'
end_case
for scenario in no-listener wrong-address wrong-pid tty stale incomplete-family; do
 new_case; installed; write_conf; active
 case "$scenario" in
 no-listener) export MOCK_MISSING_OFFSET=1;;
 wrong-address) export MOCK_LISTEN_BIND=0.0.0.0;;
 wrong-pid) export MOCK_OWNER_PID=9999;;
 tty) export MOCK_SESSION_TYPE=tty;;
 stale) printf 'stale' >"$CASE/stale"; ln -sf "$CASE/stale" "$QD_PROC_ROOT/4242/exe";;
 incomplete-family) sed -i 's/address_family = ipv4/address_family = both/' "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf";;
 esac
 run commands/doctor.sh --host
 check "doctor fails $scenario" test "$RC" -ne 0
 check "doctor never mutates on $scenario" absent "$CASE/log" 'sudo '
 end_case
done
new_case; export MOCK_LISTEN=no; run commands/install-host.sh
check 'installer fails bounded listener wait' test "$RC" -ne 0
check 'timeout does not claim ready' absent "$CASE/out" '控制端口已监听'
end_case
new_case; export MOCK_HIDE_OWNER=1; run commands/install-host.sh
check 'unobservable listener PID is disclosed' contains "$CASE/out" '未验证所有者'
end_case

# Uninstall protects ownership and stops the daemon before destructive actions.
new_case; installed; write_conf; active
run commands/uninstall.sh --host-package
check 'unowned package removal refused' test "$RC" -ne 0
check 'ownership checked before stopping service' test -f "$CASE/active"
run commands/uninstall.sh --host-package --force-remove-preexisting-package
check 'explicit preexisting package removal succeeds' test "$RC" -eq 0
check 'package removal stops service' test ! -e "$CASE/active"
check 'package removal disables service' test ! -e "$CASE/enabled"
check 'credentials/config retained' test -f "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
stopline="$(grep -n 'disable --now' "$CASE/log" | head -1 | cut -d: -f1)"
aptline="$(grep -n '^apt-get remove' "$CASE/log" | head -1 | cut -d: -f1)"
check 'stop precedes apt remove' test "$stopline" -lt "$aptline"
end_case
for mode in stop-failure other-process; do
 new_case; installed; write_conf; active
 if [ "$mode" = stop-failure ]; then export MOCK_STOP_FAIL=1; else export MOCK_EXTRA_PROCS='9999 2345 sunshine'; fi
 run commands/uninstall.sh --host-package --force-remove-preexisting-package
 check "$mode blocks package removal" test "$RC" -ne 0
 check "$mode leaves package installed" test -s "$CASE/version"
 check "$mode never calls apt remove" absent "$CASE/log" 'apt-get remove'
 end_case
done
new_case; installed; write_conf; active
run commands/uninstall.sh --destroy-host-state
check 'state deletion succeeds' test "$RC" -eq 0
check 'state deletion leaves process stopped' test ! -e "$CASE/active"
check 'state deletion disables next-login startup' test ! -e "$CASE/enabled"
check 'state deletion removes effective config' test ! -e "$QD_SUNSHINE_CONFIG_DIR"
check 'state deletion does not remove package' test -s "$CASE/version"
check 'state deletion requires configured reinstall before reuse' contains "$CASE/out" 'commands/install-host.sh'
end_case
new_case; installed; write_conf; active
export MOCK_STILL_ENABLED=1
run commands/uninstall.sh --destroy-host-state
check 'enabled unit after disable is reported as failure' test "$RC" -ne 0
check 'still-enabled unit prevents state deletion' test -f "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
end_case
for action in state-only combined; do
 new_case; installed; write_conf; active
 mv "$QD_SUNSHINE_CONFIG_DIR" "$CASE/sentinel-dir"
 printf 'preserve sentinel\n' >"$CASE/sentinel-dir/sentinel"
 cp -a "$CASE/sentinel-dir" "$CASE/sentinel-before"
 ln -s "$CASE/sentinel-dir" "$QD_SUNSHINE_CONFIG_DIR"
 if [ "$action" = combined ]; then
     run commands/uninstall.sh --host-package --force-remove-preexisting-package --destroy-host-state
 else
     run commands/uninstall.sh --destroy-host-state
 fi
 check "$action final config symlink refused" test "$RC" -ne 0
 check "$action refusal explains resolved target" contains "$CASE/out" "$CASE/sentinel-dir"
 check "$action symlink target unchanged" diff -qr "$CASE/sentinel-before" "$CASE/sentinel-dir"
 check "$action config symlink remains" test -L "$QD_SUNSHINE_CONFIG_DIR"
 check "$action symlink refusal leaves service active" test -f "$CASE/active"
 check "$action symlink refusal leaves service enabled" test -f "$CASE/enabled"
 check "$action symlink refusal precedes privileged changes" absent "$CASE/log" 'sudo '
 check "$action symlink refusal precedes service disable" absent "$CASE/log" 'disable --now'
 end_case
done
for parent in home xdg; do
 new_case; installed; write_conf; active
 if [ "$parent" = home ]; then
     mv "$HOME" "$CASE/home-real"; ln -s "$CASE/home-real" "$HOME"
 else
     mv "$HOME/.config" "$CASE/config-real"; ln -s "$CASE/config-real" "$HOME/.config"
 fi
 printf 'keep parent content\n' >"$HOME/.config/sibling"
 unset QD_SUNSHINE_CONFIG_DIR
 run commands/uninstall.sh --destroy-host-state
 check "$parent parent symlink allowed for state deletion" test "$RC" -eq 0
 check "$parent parent symlink keeps sibling contents" contains "$HOME/.config/sibling" 'keep parent content'
 check "$parent parent symlink service disabled" test ! -e "$CASE/enabled"
 check "$parent parent symlink state removed" test ! -e "$HOME/.config/sunshine"
 end_case
done
new_case; run commands/install-host.sh; run commands/uninstall.sh --host-package
check 'owned uninstall clears stale ownership proof' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case

new_case
mkdir -p "$QD_HOST_STATE_DIR"
printf 'package_preexisting=false\n' >"$QD_HOST_STATE_DIR/host.state"
run commands/uninstall.sh --host-package
check 'already absent package clears stale ownership' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case

# New recovery checks reuse only these isolated fixtures.
. "$TESTS_DIR/retry.sh"
check 'native binding semantics and byte-preserving rewrite' python3 "$TESTS_DIR/binding.py"
check 'real prestart guard and retry time model' python3 "$TESTS_DIR/retry.py"

# Client extraction, pins/provenance, ownership and interrupted replacement.
new_case; client_fixture; run commands/install-client.sh
check 'extracted AppImage client installs' test "$RC" -eq 0
check 'extracted AppRun resolves to executable' test -x "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/AppRun"
: >"$CASE/log"; run commands/install-client.sh
check 'client rerun skips download based on provenance' absent "$CASE/log" 'asset url='
check 'client provenance wording does not claim rehash' contains "$CASE/out" '未重新校验'
rm "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/com.moonlight_stream.Moonlight.desktop"
run commands/install-client.sh
check 'missing extracted desktop metadata triggers repair' test "$RC" -eq 0
check 'extracted desktop metadata restored' test -f "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/com.moonlight_stream.Moonlight.desktop"
# The fake payload is intentionally not the published payload; test both marker paths.
run commands/doctor.sh --client
check 'doctor rejects wrong pinned provenance' test "$RC" -ne 0
printf '%s\n' "$QD_MOONLIGHT_SHA256" >"$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/.quick-deploy-sha256"
run commands/doctor.sh --client
check 'modeled client with published marker passes structural checks' test "$RC" -eq 0
rm "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/usr/bin/moonlight"
run commands/doctor.sh --client
check 'broken AppRun symlink fails client doctor' test "$RC" -ne 0
mkdir -p "$HOME/.local/opt/moonlight/foreign-build"
run commands/uninstall.sh --client
check 'client removes owned payload only' test ! -d "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION"
check 'client preserves foreign directory' test -d "$HOME/.local/opt/moonlight/foreign-build"
end_case
new_case; mkdir -p "$HOME/.local/opt/moonlight"; run commands/doctor.sh --client
check 'empty client directory is a failure' test "$RC" -ne 0
end_case
for failure in digest size extract foreign; do
 new_case; client_fixture
 case "$failure" in
 digest) python3 - "$CASE/fixtures/api-moonlight.json" <<'PY_DIGEST'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['assets'][0]['digest']='sha256:'+'0'*64; json.dump(d,open(p,'w'))
PY_DIGEST
 ;;
 size) python3 - "$CASE/fixtures/api-moonlight.json" <<'PY_SIZE'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['assets'][0]['size']=1; json.dump(d,open(p,'w'))
PY_SIZE
 ;;
 extract) export MOCK_EXTRACT_FAIL=1;;
 foreign) mkdir -p "$HOME/.local/bin"; printf 'foreign\n' >"$HOME/.local/bin/moonlight";;
 esac
 run commands/install-client.sh
 check "client $failure fails" test "$RC" -ne 0
 check "client $failure does not install payload" test ! -e "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION"
 end_case
done
# Distinct ownership boundaries: refusals must preserve foreign/residual bytes.
for asset in desktop target staging backup; do
 new_case; client_fixture
 case "$asset" in
 desktop)
     foreign="$HOME/.local/share/applications/com.moonlight_stream.Moonlight.desktop"
     mkdir -p "$(dirname "$foreign")"
     printf '[Desktop Entry]\nName=Foreign Moonlight\nExec=/opt/foreign-moonlight\n' >"$foreign"
     cp "$foreign" "$CASE/before";;
 target|staging|backup)
     case "$asset" in
         target) foreign="$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION";;
         staging) foreign="$HOME/.local/opt/moonlight/.staging-$QD_MOONLIGHT_VERSION.123";;
         backup) foreign="$HOME/.local/opt/moonlight/.backup-$QD_MOONLIGHT_VERSION.123";;
     esac
     mkdir -p "$foreign/nested"
     printf 'foreign payload\n' >"$foreign/AppRun"
     printf 'preserve nested bytes\n' >"$foreign/nested/file"
     if [ "$asset" != target ]; then printf 'recorded residue\n' >"$foreign/.quick-deploy-sha256"; fi
     cp -a "$foreign" "$CASE/before";;
 esac
 run commands/install-client.sh
 check "client refuses $asset ownership boundary" test "$RC" -ne 0
 check "$asset refusal occurs before download" absent "$CASE/log" 'curl '
 if [ "$asset" = desktop ]; then
     check 'foreign desktop preserved byte-for-byte' cmp -s "$CASE/before" "$foreign"
 else
     check "$asset contents preserved byte-for-byte" diff -qr "$CASE/before" "$foreign"
 fi
 check "$asset refusal does not create wrapper" test ! -e "$HOME/.local/bin/moonlight"
 end_case
done
new_case
opt="$HOME/.local/opt/moonlight"
for kind in staging backup; do
 residue="$opt/.$kind-$QD_MOONLIGHT_VERSION.123"
 mkdir -p "$residue"
 printf 'owned residue\n' >"$residue/.quick-deploy-sha256"
 printf 'owned payload\n' >"$residue/file"
done
mkdir -p "$opt/.foreign-hidden/nested"
printf 'keep hidden bytes\n' >"$opt/.foreign-hidden/nested/file"
cp -a "$opt/.foreign-hidden" "$CASE/hidden-before"
run commands/uninstall.sh --client
check 'hidden residue uninstall succeeds' test "$RC" -eq 0
check 'marked hidden staging removed' test ! -e "$opt/.staging-$QD_MOONLIGHT_VERSION.123"
check 'marked hidden backup removed' test ! -e "$opt/.backup-$QD_MOONLIGHT_VERSION.123"
check 'unmarked hidden contents preserved' diff -qr "$CASE/hidden-before" "$opt/.foreign-hidden"
end_case
new_case; client_fixture; run commands/install-client.sh
printf 'old marker\n' >"$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/.quick-deploy-sha256"
export MOCK_FAIL_PROMOTE=1
run commands/install-client.sh
check 'failed client promotion is surfaced' test "$RC" -ne 0
check 'failed client promotion restores old target' contains "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/.quick-deploy-sha256" 'old marker'
end_case
# Combined installer executes the moved entrypoints, not compatibility wrappers.
new_case; make_module_copy; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
printf 'machines:\n  keep:\n    ssh: keep-alias\n    tailnet_ip: 100.64.0.9\n' >"$MODULE_COPY/machines.yaml"
printf 'machines:\n  legacy-keep:\n    ssh: legacy-alias\n    tailnet_ip: 100.64.0.8\n' >"$MODULE_COPY/machines.local.yaml"
chmod 600 "$MODULE_COPY/machines.yaml" "$MODULE_COPY/machines.local.yaml"
cp "$MODULE_COPY/machines.yaml" "$CASE/inventory-before"
cp "$MODULE_COPY/machines.local.yaml" "$CASE/legacy-before"
write_expected_example
run_install
check 'combined installer succeeds' test "$RC" -eq 0
check 'combined installer invokes host before client' test "$(grep -n 'api.github.com/repos/LizardByte/Sunshine' "$CASE/log" | head -1 | cut -d: -f1)" -lt "$(grep -n 'Moonlight-6.1.0-x86_64.AppImage' "$CASE/log" | head -1 | cut -d: -f1)"
check 'combined installer selects KMS capture' grep -Fxq 'capture = kms' "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
check 'combined installer creates client wrapper' test -x "$HOME/.local/bin/moonlight"
check 'combined installer skips apt when system Python has PyYAML' absent "$CASE/log" 'apt-get install -y python3-yaml'
check 'client ownership marker keeps historic bytes' grep -Fxq '# Managed by quick-deploy/sunshine-moonlight/install-client.sh' "$HOME/.local/bin/moonlight"
check 'combined installer generates module example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'generated example mode is 644' test "$(stat -c %a "$MODULE_COPY/machines.example.yaml")" = 644
check 'generated example carries no real inventory data' bash -c '! grep -Fq keep-alias "$1"' bash "$MODULE_COPY/machines.example.yaml"
check 'generated example is ignored by module gitignore' git --no-pager -C "$MODULE_DIR" check-ignore -q --no-index machines.example.yaml
check 'combined installer preserves actual inventory bytes' cmp -s "$CASE/inventory-before" "$MODULE_COPY/machines.yaml"
check 'combined installer preserves actual inventory mode' test "$(stat -c %a "$MODULE_COPY/machines.yaml")" = 600
check 'combined installer preserves legacy inventory bytes' cmp -s "$CASE/legacy-before" "$MODULE_COPY/machines.local.yaml"
check 'combined installer preserves legacy inventory mode' test "$(stat -c %a "$MODULE_COPY/machines.local.yaml")" = 600
printf 'stale placeholder\n' >"$MODULE_COPY/machines.example.yaml"
: >"$CASE/log"
run_install
check 'same-version repeat succeeds' test "$RC" -eq 0
check 'same-version repeat downloads no payload' absent "$CASE/log" 'asset url='
check 'same-version repeat refreshes generated example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'same-version repeat preserves actual inventory bytes' cmp -s "$CASE/inventory-before" "$MODULE_COPY/machines.yaml"
end_case

new_case; make_module_copy; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
write_expected_example
run_install --host-only
check 'host-only installer succeeds' test "$RC" -eq 0
check 'host-only installer skips client' test ! -e "$HOME/.local/bin/moonlight"
check 'host-only installer generates example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'host-only installer creates no actual inventory' test ! -e "$MODULE_COPY/machines.yaml"
check 'host-only installer creates no legacy inventory' test ! -e "$MODULE_COPY/machines.local.yaml"
end_case

new_case; make_module_copy; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
write_expected_example
run_install --client-only
check 'client-only installer succeeds' test "$RC" -eq 0
check 'client-only installer skips host' test ! -s "$CASE/version"
check 'client-only installer generates example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'client-only installer creates no actual inventory' test ! -e "$MODULE_COPY/machines.yaml"
end_case

new_case; make_module_copy; client_fixture; system_python_fixture
write_expected_example
run_install --client-only
check 'missing PyYAML is installed through privileged apt' test "$RC" -eq 0
check 'missing PyYAML apt request uses package name' contains "$CASE/log" 'apt-get install -y python3-yaml'
check 'PyYAML installation precedes client installation' test "$(grep -n 'python3-yaml' "$CASE/log" | head -1 | cut -d: -f1)" -lt "$(grep -n 'Moonlight-6.1.0-x86_64.AppImage' "$CASE/log" | head -1 | cut -d: -f1)"
check 'successful PyYAML install also generates example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
end_case

new_case; make_module_copy; system_python_fixture; export MOCK_APT_FAIL=1
run_install --client-only
check 'PyYAML apt failure is surfaced' test "$RC" -ne 0
check 'PyYAML apt failure starts no client install' test ! -e "$HOME/.local/bin/moonlight"
check 'PyYAML apt failure generates no example' test ! -e "$MODULE_COPY/machines.example.yaml"
end_case

new_case; make_module_copy; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1 MOCK_START_FAIL=1
run_install
check 'host failure stops combined installer' test "$RC" -ne 0
check 'host failure leaves client unstarted' test ! -e "$HOME/.local/bin/moonlight"
check 'host failure makes zero Moonlight metadata calls' test "$(grep -c 'moonlight-stream/moonlight-qt' "$CASE/log")" -eq 0
check 'host failure reports partial-completion boundary' contains "$CASE/out" 'Moonlight 客户端未开始'
check 'host failure generates no example' test ! -e "$MODULE_COPY/machines.example.yaml"
end_case

new_case; make_module_copy; system_python_fixture
run_install --host-only --client-only
check 'combined installer rejects conflicting modes before mutation' test "$RC" -ne 0
check 'conflicting modes make no apt request' test ! -s "$CASE/log"
run_install --unknown
check 'combined installer rejects unknown option before mutation' test "$RC" -ne 0
check 'unknown option makes no apt request' test ! -s "$CASE/log"
run_install --help
check 'combined installer help succeeds without mutation' test "$RC" -eq 0
check 'combined installer help names direct commands' contains "$CASE/out" 'commands/install-host.sh'
check 'invalid/help invocations generate no example' test ! -e "$MODULE_COPY/machines.example.yaml"
end_case

new_case; make_module_copy; export MOCK_UID=0; run_install
check 'combined installer refuses root' test "$RC" -ne 0
check 'root refusal precedes PyYAML apt' test ! -s "$CASE/log"
check 'root refusal generates no example' test ! -e "$MODULE_COPY/machines.example.yaml"
end_case

for entry in install-host.sh install-client.sh doctor.sh uninstall.sh; do
    check "moved entrypoint exists: $entry" test -x "$MODULE_DIR/commands/$entry"
    check "legacy top-level entry removed: $entry" test ! -e "$MODULE_DIR/$entry"
done

new_case; make_module_copy 'repo with spaces'
space_module="$MODULE_COPY"
mkdir -p "$CASE/unrelated cwd"
for entry in install.sh commands/install-host.sh commands/install-client.sh commands/doctor.sh commands/uninstall.sh; do
    run_from "$CASE/unrelated cwd" "$space_module/$entry" --help
    check "moved entry help works from spaced module path: $entry" test "$RC" -eq 0
done
check 'spaced module help generates no example' test ! -e "$space_module/machines.example.yaml"
# A successful install from an unrelated cwd still writes the module-relative example.
client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
write_expected_example
run_from "$CASE/unrelated cwd" "$space_module/install.sh" --client-only
check 'spaced module client-only install from unrelated cwd succeeds' test "$RC" -eq 0
check 'spaced module example follows module, not cwd' cmp -s "$CASE/expected-example" "$space_module/machines.example.yaml"
check 'unrelated cwd gains no example' test ! -e "$CASE/unrelated cwd/machines.example.yaml"
end_case

# Example publication refuses links/directories/non-writable modules and never
# touches the actual or legacy inventory next to it.
new_case; make_module_copy; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
printf 'machines:\n  keep:\n    ssh: keep-alias\n    tailnet_ip: 100.64.0.9\n' >"$MODULE_COPY/machines.yaml"
chmod 600 "$MODULE_COPY/machines.yaml"
cp "$MODULE_COPY/machines.yaml" "$CASE/inventory-before"
ln -s "$MODULE_COPY/machines.yaml" "$MODULE_COPY/machines.example.yaml"
run_install --client-only
check 'symlinked example path is refused' test "$RC" -ne 0
check 'symlink refusal names the link' contains "$CASE/out" '符号链接'
check 'symlink refusal never follows into actual inventory' cmp -s "$CASE/inventory-before" "$MODULE_COPY/machines.yaml"
check 'symlink refusal keeps actual inventory mode' test "$(stat -c %a "$MODULE_COPY/machines.yaml")" = 600
check 'symlink refusal leaves the link itself' test -L "$MODULE_COPY/machines.example.yaml"
check 'symlink refusal does not claim install completion' absent "$CASE/out" '所选本机安装阶段已完成'
end_case

new_case; make_module_copy; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
mkdir "$MODULE_COPY/machines.example.yaml"
printf 'preserve directory sentinel\n' >"$MODULE_COPY/machines.example.yaml/sentinel"
run_install --client-only
check 'directory example path is refused' test "$RC" -ne 0
check 'directory refusal names the non-regular file' contains "$CASE/out" '不是普通文件'
check 'directory refusal preserves sentinel contents' contains "$MODULE_COPY/machines.example.yaml/sentinel" 'preserve directory sentinel'
check 'directory refusal keeps the directory itself' test -d "$MODULE_COPY/machines.example.yaml"
end_case

new_case; make_module_copy; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
chmod 555 "$MODULE_COPY"
run_install --client-only
check 'unwritable module surfaces generation failure' test "$RC" -ne 0
check 'unwritable module reports the write failure' contains "$CASE/out" '无法写入示例'
check 'unwritable module creates no example' test ! -e "$MODULE_COPY/machines.example.yaml"
chmod 755 "$MODULE_COPY"
end_case

# Public-module fixture matrix across every supported module-root state. None of
# these copies contain a personal inventory: fixture inventories are written here,
# and nothing is ever read back from the real checkout.
new_case; make_module_copy 'absent state'; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
write_expected_example
run_install --client-only
check 'absent-state install succeeds' test "$RC" -eq 0
check 'absent-state install generates the example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'absent-state example mode is 644' test "$(stat -c %a "$MODULE_COPY/machines.example.yaml")" = 644
check 'absent-state install creates no actual inventory' test ! -e "$MODULE_COPY/machines.yaml"
check 'absent-state install creates no legacy inventory' test ! -e "$MODULE_COPY/machines.local.yaml"
# Documented flow: copy the freshly generated example to machines.yaml, then let the
# real connector read it — --list first, then the stream path through the wrapper.
cp "$MODULE_COPY/machines.example.yaml" "$MODULE_COPY/machines.yaml"
run_from "$CASE" "$MODULE_COPY/run_server.sh" --list
check 'generated example passes connector --list' test "$RC" -eq 0
check 'generated example lists its desktop entry' contains "$CASE/out" 'desktop'
run_from "$CASE" "$MODULE_COPY/run_server.sh" desktop
check 'generated example reaches the installed Moonlight wrapper' test "$RC" -eq 0
check 'installed wrapper received the stream request' contains "$CASE/out" 'fixture only'
end_case

new_case; make_module_copy 'example present'; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
printf '# hand-edited example placeholder\nmachines: {}\n' >"$MODULE_COPY/machines.example.yaml"
chmod 600 "$MODULE_COPY/machines.example.yaml"
write_expected_example
run_install --client-only
check 'example-present install succeeds' test "$RC" -eq 0
check 'example-present install refreshes example bytes' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'example-present refresh restores example mode 644' test "$(stat -c %a "$MODULE_COPY/machines.example.yaml")" = 644
check 'example-present install creates no actual inventory' test ! -e "$MODULE_COPY/machines.yaml"
check 'example-present install creates no legacy inventory' test ! -e "$MODULE_COPY/machines.local.yaml"
end_case

new_case; make_module_copy 'inventories present'; client_fixture; system_python_fixture; export QD_TEST_YAML_AVAILABLE=1
printf 'machines:\n  keep:\n    ssh: keep-alias\n    tailnet_ip: 100.64.0.9\n' >"$MODULE_COPY/machines.yaml"
printf 'machines:\n  legacy-keep:\n    ssh: legacy-alias\n    tailnet_ip: 100.64.0.8\n' >"$MODULE_COPY/machines.local.yaml"
chmod 600 "$MODULE_COPY/machines.yaml" "$MODULE_COPY/machines.local.yaml"
cp "$MODULE_COPY/machines.yaml" "$CASE/actual-before"
cp "$MODULE_COPY/machines.local.yaml" "$CASE/legacy-before"
write_expected_example
run_install --client-only
check 'inventory-present install succeeds' test "$RC" -eq 0
check 'inventory-present install generates the example' cmp -s "$CASE/expected-example" "$MODULE_COPY/machines.example.yaml"
check 'inventory-present preserves actual inventory bytes' cmp -s "$CASE/actual-before" "$MODULE_COPY/machines.yaml"
check 'inventory-present preserves actual inventory mode' test "$(stat -c %a "$MODULE_COPY/machines.yaml")" = 600
check 'inventory-present preserves legacy inventory bytes' cmp -s "$CASE/legacy-before" "$MODULE_COPY/machines.local.yaml"
check 'inventory-present preserves legacy inventory mode' test "$(stat -c %a "$MODULE_COPY/machines.local.yaml")" = 600
check 'inventory-present keeps inventory data out of the example' bash -c '! grep -Fq keep-alias "$1"' bash "$MODULE_COPY/machines.example.yaml"
end_case

new_case; export MOCK_UID=0; run commands/install-host.sh
check 'direct host installer refuses root' test "$RC" -ne 0
end_case
new_case; run commands/install-host.sh --version v2026.516.143833
check 'obsolete security baseline rejected' test "$RC" -ne 0
check 'obsolete version fails before network' absent "$CASE/log" 'api url='
run commands/install-client.sh --version v9999
check 'unpinned client version rejected' test "$RC" -ne 0
end_case

# The real curl probe blocks network with file:// protocol control while proving
# the isolated .curlrc fixture can serialize config absent production --disable.
new_case
run_real_curlrc_probe
check 'real curlrc fixture positive control detects persistent config' test "$CURLRC_CONTROL_GENERATED" = true
check 'GitHub helper curlrc probe is blocked before network' test "$RC" -ne 0
check 'GitHub helper passes --disable as first curl argument' contains "$CASE/real-curl-wrapper.log" 'first=--disable'
check 'GitHub helper ignores ambient curlrc generated output' test "$CURLRC_GENERATED" = false
check 'GitHub helper curlrc probe leaves no token in files/output/log' bash -c '! grep -R -Fq QD_REAL_CURLRC_SENTINEL "$1/curlrc.out" "$1/curlrc.err" "$1/real-curl-wrapper.log" "$1/home" "$1/curl-home" "$1/curl-config-home"' bash "$CASE"
end_case

# Sunshine release URLs are identity-bearing API input. The selected asset name
# is encoded once as a URL path component; alternate representations are refused.
new_case
run_sunshine_resolver
check 'encoded Sunshine API URL resolves' test "$RC" -eq 0
check 'encoded Sunshine API URL retains %2B path component' contains "$CASE/out" '%2Bubuntu24.04_amd64.deb'
check 'encoded Sunshine resolver makes no asset request' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

for url_mutation in raw alternate-host alternate-repo alternate-tag alternate-name query double-encoded; do
    new_case
    python3 - "$CASE/fixtures/api-sunshine.json" "$url_mutation" <<'PY_MUTATE_SUNSHINE_URL'
import json, sys
p, kind = sys.argv[1:]
d = json.load(open(p)); a = d['assets'][0]; url = a['browser_download_url']
if kind == 'raw': url = url.replace('%2B', '+')
elif kind == 'alternate-host': url = url.replace('https://github.com/', 'https://example.invalid/')
elif kind == 'alternate-repo': url = url.replace('/LizardByte/Sunshine/', '/Other/Sunshine/')
elif kind == 'alternate-tag': url = url.replace('/v2026.906.222525/', '/v2026.906.222526/')
elif kind == 'alternate-name': url = url.replace('_amd64.deb', '_arm64.deb')
elif kind == 'query': url += '?download=1'
elif kind == 'double-encoded': url = url.replace('%2B', '%252B')
a['browser_download_url'] = url
json.dump(d, open(p, 'w'))
PY_MUTATE_SUNSHINE_URL
    run_sunshine_resolver
    check "Sunshine $url_mutation URL is rejected" test "$RC" -ne 0
    check "Sunshine $url_mutation cannot reach asset request" test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
    end_case
done

# Resolver-boundary probes retain the real audited identifiers and avoid payload
# flow entirely, so failure cannot be misattributed to later size/hash/extraction.
new_case; client_fixture; write_audited_moonlight_api null
run_resolver
check 'audited null resolver accepts exact tuple' test "$RC" -eq 0
check 'audited null resolver selects built-in audited SHA' contains "$CASE/out" "resolved=v6.1.0 $QD_MOONLIGHT_SHA256 55325888"
check 'audited null resolver makes no payload request' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; client_fixture; write_audited_moonlight_api "sha256:$QD_MOONLIGHT_SHA256"
run_resolver
check 'audited matching API digest resolver accepts exact tuple' test "$RC" -eq 0
check 'audited matching API digest retains audited SHA' contains "$CASE/out" "resolved=v6.1.0 $QD_MOONLIGHT_SHA256 55325888"
check 'audited matching resolver makes no payload request' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; client_fixture; write_audited_moonlight_api "sha256:$(printf '%064d' 0)"
run_resolver
check 'audited conflicting API digest fails in resolver' test "$RC" -ne 0
check 'audited conflicting API digest reports disagreement' contains "$CASE/out" 'API digest 与内置审计 SHA-256 不一致'
check 'audited conflicting resolver makes zero payload requests' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

# xtrace must never serialize a credential. The mock records only safe auth
# facts and keeps its original argv so auth-in-argv checks are meaningful.
for auth_case in github gh dual-equal; do
    new_case; client_fixture
    sentinel="QD_XTRACE_${auth_case}_SENTINEL"
    case "$auth_case" in
        github) export GITHUB_TOKEN="$sentinel";;
        gh) export GH_TOKEN="$sentinel";;
        dual-equal) export GITHUB_TOKEN="$sentinel" GH_TOKEN="$sentinel";;
    esac
    run_xtrace commands/install-client.sh
    check "xtrace $auth_case succeeds" test "$RC" -eq 0
    check "xtrace $auth_case keeps sentinel out of output/log/files" bash -c '! grep -R -Fq "$1" "$2/out" "$2/err" "$2/log" "$2/fixtures"' bash "$sentinel" "$CASE"
    check "xtrace $auth_case uses config auth without argv token" contains "$CASE/log" 'auth=present'
    check "xtrace $auth_case preserves original argv evidence" contains "$CASE/log" 'argv_has_auth=no'
    end_case
done

new_case; client_fixture; export GITHUB_TOKEN='QD_XTRACE_CONFLICT_SENTINEL_A' GH_TOKEN='QD_XTRACE_CONFLICT_SENTINEL_B'
run_xtrace commands/install-client.sh
check 'xtrace conflicting tokens rejects before network' test "$RC" -ne 0
check 'xtrace conflicting tokens expose neither sentinel' bash -c '! grep -R -Fq QD_XTRACE_CONFLICT_SENTINEL "$1/out" "$1/err" "$1/log" "$1/fixtures"' bash "$CASE"
check 'xtrace conflicting tokens makes no API request' test ! -s "$CASE/log"
end_case

new_case; client_fixture; export GITHUB_TOKEN=$'QD_XTRACE_UNSAFE_SENTINEL\nvalue'
run_xtrace commands/install-client.sh
check 'xtrace unsafe token rejects before network' test "$RC" -ne 0
check 'xtrace unsafe token exposes no sentinel' bash -c '! grep -R -Fq QD_XTRACE_UNSAFE_SENTINEL "$1/out" "$1/err" "$1/log" "$1/fixtures"' bash "$CASE"
check 'xtrace unsafe token makes no API request' test ! -s "$CASE/log"
end_case

# Latest-release and strict-integrity regression matrix. These use the same actual
# scripts, fake HOME/PATH, and curl fixture; API and asset traffic are observable
# separately so a metadata lookup cannot be mistaken for an asset download.
new_case; client_fixture
run commands/install-client.sh
check 'client default queries exactly latest endpoint' grep -Fq 'releases/latest' "$CASE/log"
check 'client default has one metadata request' test "$(grep -c '^api url=' "$CASE/log")" -eq 1
check 'client default has one asset request' test "$(grep -c '^asset url=' "$CASE/log")" -eq 1
end_case

new_case; client_fixture
run commands/install-client.sh --version v6.1.0
check 'client explicit tag queries tags endpoint' grep -Fq 'releases/tags/v6.1.0' "$CASE/log"
check 'client explicit tag makes one metadata request' test "$(grep -c '^api url=' "$CASE/log")" -eq 1
end_case

new_case; installed; write_conf; active
run commands/install-host.sh
check 'host equal still queries latest once' test "$(grep -c '^api url=' "$CASE/log")" -eq 1
check 'host equal does not download deb bytes' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; installed 2099.1.1; write_conf; active
run commands/install-host.sh --version v2026.906.222525
check 'explicit lower host target preserves newer package' test "$RC" -eq 0
check 'explicit lower host target still fetches exact tag once' test "$(grep -c 'releases/tags/v2026.906.222525' "$CASE/log")" -eq 1
check 'explicit lower host target does not download' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; client_fixture
export MOCK_API_STATUS=403 MOCK_RATE_REMAINING=0 MOCK_RATE_RESET=1789145352 TZ=UTC
run commands/install-client.sh
check 'quota response stops client before asset mutation' test "$RC" -ne 0
check 'quota diagnostics include status and raw reset' contains "$CASE/out" 'HTTP 403，rate limit remaining=0，reset epoch=1789145352'
check 'quota diagnostics format reset time' contains "$CASE/out" '2026-09-11T16:49:12+00:00'
check 'quota failure makes no rate-limit followup or asset request' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; client_fixture
export MOCK_API_STATUS=403 MOCK_RATE_REMAINING=1
run commands/install-client.sh
check 'nonquota 403 stops client' test "$RC" -ne 0
check 'nonquota 403 is not mislabeled quota exhaustion' absent "$CASE/out" 'rate limit remaining=0'
end_case

new_case; client_fixture
export GITHUB_TOKEN='qd-token-sentinel'
run commands/install-client.sh
check 'single GitHub token authenticates metadata' contains "$CASE/log" 'auth=present'
check 'token absent from mock child environment' contains "$CASE/log" 'env_github= env_gh='
check 'token absent from fixture files and output' bash -c '! grep -R -Fq qd-token-sentinel "$CASE"'
end_case

new_case; client_fixture
export GITHUB_TOKEN='qd-token-sentinel' GH_TOKEN='qd-token-sentinel'
run commands/install-client.sh
check 'identical dual tokens use one authentication header' test "$RC" -eq 0
check 'identical dual tokens do not enter argv' contains "$CASE/log" 'argv_has_auth=no'
end_case

new_case; client_fixture
export GITHUB_TOKEN='qd-token-one' GH_TOKEN='qd-token-two'
run commands/install-client.sh
check 'conflicting token variables fail before network' test "$RC" -ne 0
check 'conflicting token variables make zero API calls' test ! -s "$CASE/log"
end_case

new_case; client_fixture
export GITHUB_TOKEN=$'unsafe\nvalue'
run commands/install-client.sh
check 'newline token is rejected before network' test "$RC" -ne 0
check 'newline token makes zero API calls' test ! -s "$CASE/log"
end_case

for invalid in draft prerelease null-digest; do
    new_case; client_fixture
    python3 - "$CASE/fixtures/api-moonlight.json" "$invalid" <<'PY_MUTATE_MOON'
import json, sys
p=sys.argv[1]; d=json.load(open(p)); kind=sys.argv[2]
if kind == 'draft': d['draft']=True
elif kind == 'prerelease': d['prerelease']=True
else: d['assets'][0]['digest']=None
a=open(p,'w'); json.dump(d,a); a.close()
PY_MUTATE_MOON
    run commands/install-client.sh
    check "Moonlight $invalid metadata fails before asset" test "$RC" -ne 0
    check "Moonlight $invalid has zero asset request" test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
    end_case
done

new_case; client_fixture
python3 - "$CASE/fixtures/api-moonlight.json" <<'PY_FUTURE_NULL'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['tag_name']='v6.2.0'; a=d['assets'][0]; a['name']='Moonlight-6.2.0-x86_64.AppImage'; a['browser_download_url']='https://github.com/moonlight-stream/moonlight-qt/releases/download/v6.2.0/Moonlight-6.2.0-x86_64.AppImage'; a['digest']=None; json.dump(d,open(p,'w'))
PY_FUTURE_NULL
run commands/install-client.sh
check 'future null digest fails before AppImage request' test "$RC" -ne 0
check 'future null digest never reuses audited v6.1 checksum' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; client_fixture
python3 - "$CASE/fixtures/api-moonlight.json" <<'PY_AUDIT_MISMATCH'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['id']=175337682; a=d['assets'][0]; a['id']=193059073; a['size']=55325888; a['digest']='sha256:'+'0'*64; json.dump(d,open(p,'w'))
PY_AUDIT_MISMATCH
run commands/install-client.sh
check 'audited tuple API digest disagreement fails before asset' test "$RC" -ne 0
check 'audited tuple mismatch does not mutate target' test ! -e "$HOME/.local/opt/moonlight/6.1.0"
end_case

new_case; client_fixture
mkdir -p "$HOME/.local/opt/moonlight/9.0.0" "$HOME/.local/bin"
printf '%s\n' 'a123456789012345678901234567890123456789012345678901234567890123' >"$HOME/.local/opt/moonlight/9.0.0/.quick-deploy-sha256"
touch "$HOME/.local/opt/moonlight/9.0.0/AppRun"; chmod +x "$HOME/.local/opt/moonlight/9.0.0/AppRun"
cat >"$HOME/.local/bin/moonlight" <<EOF_NEWER_WRAP
#!/bin/sh
# Managed by quick-deploy/sunshine-moonlight/install-client.sh
exec "$HOME/.local/opt/moonlight/9.0.0/AppRun" "\$@"
EOF_NEWER_WRAP
run commands/install-client.sh
check 'newer active client requires complete target structure' test "$RC" -ne 0
check 'broken newer active client is never downgraded' test ! -e "$HOME/.local/opt/moonlight/6.1.0"
end_case

new_case; client_fixture
mkdir -p "$HOME/.local/opt/moonlight/9.0.0" "$HOME/.local/bin"
printf '%064d\n' 1 >"$HOME/.local/opt/moonlight/9.0.0/.quick-deploy-sha256"
printf '#!/bin/sh\nexit 0\n' >"$HOME/.local/opt/moonlight/9.0.0/AppRun"; chmod +x "$HOME/.local/opt/moonlight/9.0.0/AppRun"
printf '[Desktop Entry]\nExec=old\nIcon=old\n' >"$HOME/.local/opt/moonlight/9.0.0/com.moonlight_stream.Moonlight.desktop"
printf '<svg/>\n' >"$HOME/.local/opt/moonlight/9.0.0/moonlight.svg"
cat >"$HOME/.local/bin/moonlight" <<EOF_VALID_NEWER
#!/bin/sh
# Managed by quick-deploy/sunshine-moonlight/install-client.sh
exec "$HOME/.local/opt/moonlight/9.0.0/AppRun" "\$@"
EOF_VALID_NEWER
run commands/install-client.sh
check 'valid newer active client remains active' test "$RC" -eq 0
check 'valid newer active client skips selected download' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
check 'valid newer active wrapper is not repointed' grep -Fq '/moonlight/9.0.0/AppRun' "$HOME/.local/bin/moonlight"
: >"$CASE/log"; run commands/install-client.sh --version v6.1.0
check 'explicit lower client target still does not downgrade' test "$RC" -eq 0
check 'explicit lower client target makes no asset request' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

# A non-executable but exact historical wrapper remains parseable for repair.
# Doctor independently treats it as unhealthy; newer-local repair never repoints it.
new_case; client_fixture
run commands/install-client.sh
chmod 644 "$HOME/.local/bin/moonlight"
run commands/doctor.sh --client
check 'equal nonexecutable wrapper makes doctor fail' test "$RC" -ne 0
check 'equal doctor identifies nonexecutable wrapper' contains "$CASE/out" 'CLI 包装不可执行'
: >"$CASE/log"; run commands/install-client.sh
check 'equal nonexecutable wrapper repair succeeds' test "$RC" -eq 0
check 'equal wrapper repair restores mode 0755' test "$(stat -c %a "$HOME/.local/bin/moonlight")" = 755
check 'equal wrapper repair downloads no payload' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
end_case

new_case; client_fixture
mkdir -p "$HOME/.local/opt/moonlight/9.0.0" "$HOME/.local/bin"
printf '%064d\n' 1 >"$HOME/.local/opt/moonlight/9.0.0/.quick-deploy-sha256"
printf '#!/bin/sh\nexit 0\n' >"$HOME/.local/opt/moonlight/9.0.0/AppRun"; chmod +x "$HOME/.local/opt/moonlight/9.0.0/AppRun"
printf '[Desktop Entry]\nExec=old\nIcon=old\n' >"$HOME/.local/opt/moonlight/9.0.0/com.moonlight_stream.Moonlight.desktop"
printf '<svg/>\n' >"$HOME/.local/opt/moonlight/9.0.0/moonlight.svg"
cat >"$HOME/.local/bin/moonlight" <<EOF_NONEXEC_NEWER
#!/bin/sh
# Managed by quick-deploy/sunshine-moonlight/install-client.sh
exec "$HOME/.local/opt/moonlight/9.0.0/AppRun" "\$@"
EOF_NONEXEC_NEWER
chmod 644 "$HOME/.local/bin/moonlight"
target_before="$(find "$HOME/.local/opt/moonlight/9.0.0" -type f -exec sha256sum {} + | sha256sum | awk '{print $1}')"
run commands/doctor.sh --client
check 'newer nonexecutable wrapper makes doctor fail' test "$RC" -ne 0
check 'newer doctor identifies nonexecutable wrapper' contains "$CASE/out" 'CLI 包装不可执行'
: >"$CASE/log"; run commands/install-client.sh
check 'latest preserves and repairs newer wrapper' test "$RC" -eq 0
check 'latest newer repair fetches one metadata document' test "$(grep -c '^api url=' "$CASE/log")" -eq 1
check 'latest newer repair downloads zero payloads' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
check 'latest newer repair restores wrapper mode' test "$(stat -c %a "$HOME/.local/bin/moonlight")" = 755
check 'latest newer repair preserves wrapper target' grep -Fq '/moonlight/9.0.0/AppRun' "$HOME/.local/bin/moonlight"
target_after="$(find "$HOME/.local/opt/moonlight/9.0.0" -type f -exec sha256sum {} + | sha256sum | awk '{print $1}')"
check 'latest newer repair preserves target bytes' test "$target_before" = "$target_after"
run commands/doctor.sh --client
check 'newer doctor passes after wrapper repair' test "$RC" -eq 0
chmod 644 "$HOME/.local/bin/moonlight"
: >"$CASE/log"; run commands/install-client.sh --version v6.1.0
check 'explicit lower preserves and repairs newer wrapper' test "$RC" -eq 0
check 'explicit lower newer repair fetches one metadata document' test "$(grep -c '^api url=' "$CASE/log")" -eq 1
check 'explicit lower newer repair downloads zero payloads' test "$(grep -c '^asset url=' "$CASE/log")" -eq 0
check 'explicit lower newer repair restores wrapper mode' test "$(stat -c %a "$HOME/.local/bin/moonlight")" = 755
check 'explicit lower newer repair preserves wrapper target' grep -Fq '/moonlight/9.0.0/AppRun' "$HOME/.local/bin/moonlight"
end_case

new_case; client_fixture
mkdir -p "$HOME/.local/bin"; printf '#!/bin/sh\nexit 0\n' >"$HOME/.local/bin/moonlight"; chmod +x "$HOME/.local/bin/moonlight"
run commands/install-client.sh
check 'foreign active wrapper fails before API query' test "$RC" -ne 0
check 'foreign active wrapper makes zero API calls' test ! -s "$CASE/log"
end_case

for script in "$MODULE_DIR"/*.sh "$MODULE_DIR"/commands/*.sh "$MODULE_DIR"/lib/common.sh "$MODULE_DIR"/lib/*.py "$TESTS_DIR"/*.sh "$TESTS_DIR"/*.py; do case "$script" in *.sh) check "syntax: ${script##*/}" bash -n "$script";; *) check "syntax: ${script##*/}" python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])' "$script";; esac; done
check 'scoped diff whitespace' git --no-pager -C "$MODULE_DIR" diff --check -- .
# Installation in any supported state must not mutate the real checkout: whatever the
# module root had before the suite (absent or an installed example / configured
# inventories) must still have the same existence, type, mode, size, and mtime.
check 'real checkout example state preserved' test "$(checkout_path_state "$MODULE_DIR/machines.example.yaml")" = "$REAL_EXAMPLE_STATE"
check 'real checkout actual inventory state preserved' test "$(checkout_path_state "$MODULE_DIR/machines.yaml")" = "$REAL_ACTUAL_STATE"
check 'real checkout legacy inventory state preserved' test "$(checkout_path_state "$MODULE_DIR/machines.local.yaml")" = "$REAL_LEGACY_STATE"
printf '\nAssertions: passed=%d failed=%d\n' "$PASSED" "$FAILED"
[ "$FAILED" -eq 0 ]
