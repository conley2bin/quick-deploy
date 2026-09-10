#!/bin/bash
# Isolated HOME/PATH fixtures. Only the dummy .deb test invokes real apt, with --simulate.
set -euo pipefail
TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(dirname "$TESTS_DIR")"
BASE_PATH="$PATH"
ORIGINAL_HOME="$HOME"
# shellcheck source=../lib/common.sh
. "$MODULE_DIR/lib/common.sh"
CASE=''
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
run() {
    RC=0
    bash "$MODULE_DIR/$1" "${@:2}" >"$CASE/out" 2>"$CASE/err" || RC=$?
    cat "$CASE/err" >>"$CASE/out"
}
end_case() {
    rm -rf -- "$CASE"; CASE=''
    export PATH="$BASE_PATH" HOME="$ORIGINAL_HOME"
    unset XDG_CONFIG_HOME CONFIGURATION_DIRECTORY
    # All mutable mock controls are case-local.
    while IFS= read -r key; do unset "$key"; done < <(compgen -v | grep '^MOCK_' || true)
    unset QD_TEST_MOONLIGHT_SHA256 QD_TEST_MOONLIGHT_SIZE TMPDIR
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
    python3 - "$CASE" "$1" "$2" <<'PY'
import hashlib,json,pathlib,sys
case=pathlib.Path(sys.argv[1]); name=sys.argv[3]
data={'tag_name':sys.argv[2], 'assets':[{'name':name,'browser_download_url':'https://example.invalid/'+name,'digest':'sha256:'+hashlib.sha256((case/'fixtures/sunshine.deb').read_bytes()).hexdigest()}]}
(case/'fixtures/api.json').write_text(json.dumps(data))
PY
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
    export QD_TEST_MOONLIGHT_SHA256 QD_TEST_MOONLIGHT_SIZE
    QD_TEST_MOONLIGHT_SHA256="$(sha256sum "$CASE/fixtures/moonlight.AppImage" | cut -d' ' -f1)"
    QD_TEST_MOONLIGHT_SIZE="$(stat -c %s "$CASE/fixtures/moonlight.AppImage")"
}
write_mocks() {
    cat >"$CASE/bin/curl" <<'MOCK'
#!/bin/bash
printf 'curl %s\n' "$*" >>"$CASE/log"
out=''; url=''
while [ "$#" -gt 0 ]; do case "$1" in -o) out="$2"; shift;; https:*) url="$1";; esac; shift; done
case "$url" in *api.github.com*) src=api.json;; *.deb) src=sunshine.deb;; *.AppImage) src=moonlight.AppImage;; *) exit 22;; esac
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
      DropInPaths) echo "${MOCK_DROPS:-}";;
      ExecStart) echo "${MOCK_EXECSTART:-{ path=/usr/bin/sunshine ; argv[]=/usr/bin/sunshine ; }}";;
      Environment) echo "${MOCK_UNIT_ENV:-}";;
      EnvironmentFiles|ConfigurationDirectory) echo '';;
      MainPID) if [ -f "$CASE/active" ]; then echo 4242; else echo 0; fi;;
      *) exit 99;;
    esac; exit 0;;
 is-active) [ -f "$CASE/active" ]; exit $?;;
 is-enabled) [ -f "$CASE/enabled" ]; exit $?;;
 daemon-reload) exit 0;;
 enable) touch "$CASE/enabled"; exit 0;;
 disable|stop)
    [ "${MOCK_STOP_FAIL:-0}" = 0 ] || exit 1
    rm -f "$CASE/active"
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
bind="${MOCK_LISTEN_BIND:-$(sed -n 's/^bind_address *= *\([^ #]*\).*/\1/p' "$conf") }"; bind="${bind% }"
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
run install-host.sh
check 'fresh native-name install reaches listening state' test "$RC" -eq 0
check 'apt receives .deb suffix' contains "$CASE/log" '.deb'
check 'fresh install owns package' contains "$QD_HOST_STATE_DIR/host.state" 'package_preexisting=false'
check 'host prints Moonlight base port' contains "$CASE/out" '100.64.0.2:47989'
check 'host distinguishes untested live stream' contains "$CASE/out" '尚未验证实际 Desktop'
check 'new config mode is 600' test "$(stat -c %a "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf")" = 600
: >"$CASE/log"
run install-host.sh
check 'same Debian-revision upstream is idempotent' test "$RC" -eq 0
check 'same upstream skips apt' absent "$CASE/log" 'apt-get install'
check 'same upstream skips restart' absent "$CASE/log" 'systemctl --user restart'
check 'temporary files cleaned' test -z "$(find "$TMPDIR" -mindepth 1 -print -quit)"
run doctor.sh --host
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
 run install-host.sh
 check "reject $failure before apt" test "$RC" -ne 0
 check "no privileged changes on $failure" absent "$CASE/log" 'sudo '
 check "no config on $failure" test ! -e "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 end_case
done
new_case
export MOCK_APT_VERSION=2099.1.1
run install-host.sh
check 'exact installed Debian version required' test "$RC" -ne 0
check 'wrong installed version cannot claim ownership' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case
new_case
export MOCK_APT_FAIL=1
run install-host.sh
check 'apt failure is surfaced without false consistency claim' test "$RC" -ne 0
check 'failed install keeps no ownership proof' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case
new_case
export MOCK_ARCH=arm64
make_deb sunshine arm64 "$QD_SUNSHINE_VERSION-1+ubuntu24.04"
write_api "v$QD_SUNSHINE_VERSION" "sunshine_$QD_SUNSHINE_VERSION-1+ubuntu24.04_arm64.deb"
run install-host.sh
check 'native arm64 asset accepted' test "$RC" -eq 0
end_case
new_case
write_api "v$QD_SUNSHINE_VERSION" 'sunshine-ubuntu-22.04-amd64.deb'
run install-host.sh
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
 run install-host.sh
 check "$change change succeeds" test "$RC" -eq 0
 check "$change change restarts active daemon" contains "$CASE/log" 'systemctl --user restart'
 if [ "$change" = package ]; then check 'old Debian epoch explicitly handled' contains "$CASE/log" '--allow-downgrades'; fi
 check "$change retains preexisting package ownership" contains "$QD_HOST_STATE_DIR/host.state" 'package_preexisting=true'
 end_case
done
new_case; installed 2099.1.1; write_conf; active
run install-host.sh
check 'newer upstream preserved' test "$RC" -eq 0
check 'newer upstream not downloaded' absent "$CASE/log" 'curl '
end_case
new_case; installed '1:2026.516.143833-99'; write_conf; active
run doctor.sh --host
check 'epoch cannot satisfy upstream floor' test "$RC" -ne 0
end_case

# Release identity and digest are validated even when the payload itself is well-formed.
for issue in wrong-tag missing-digest; do
 new_case
 if [ "$issue" = wrong-tag ]; then
     write_api v2099.1.1 'sunshine-ubuntu-24.04-amd64.deb'
 else
     python3 - "$CASE/fixtures/api.json" <<'PY_TEST'
import json,sys
p=sys.argv[1]; d=json.load(open(p)); d['assets'][0]['digest']=None
with open(p,'w') as f: json.dump(d,f)
PY_TEST
 fi
 run install-host.sh
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
 if [ "$scenario" = foreign-address ]; then run install-host.sh --bind-address 192.168.1.2; else run install-host.sh; fi
 check "$scenario refused before mutations" test "$RC" -ne 0
 check "$scenario performs no sudo" absent "$CASE/log" 'sudo '
 check "$scenario performs no service start" absent "$CASE/log" 'systemctl --user start '
 end_case
done
for ip in 0.0.0.0 1.2.3.4. 010.0.0.1 999.1.1.1; do
 new_case; run install-host.sh --bind-address "$ip"
 check "invalid/wildcard $ip rejected" test "$RC" -ne 0
 check "$ip rejected before sudo" absent "$CASE/log" 'sudo '
 end_case
done
new_case
unset QD_SUNSHINE_CONFIG_DIR
export XDG_CONFIG_HOME="$HOME/xdg-config" CONFIGURATION_DIRECTORY="$HOME/service-config"
export MOCK_MANAGER_ENV="$(printf 'DISPLAY=:1\nXDG_CONFIG_HOME=%s\nCONFIGURATION_DIRECTORY=%s' "$XDG_CONFIG_HOME" "$CONFIGURATION_DIRECTORY")"
run install-host.sh
check 'matching service CONFIGURATION_DIRECTORY wins over XDG' test "$RC" -eq 0
check 'actual service directory configured' test -f "$CONFIGURATION_DIRECTORY/sunshine/sunshine.conf"
check 'unused default config not created' test ! -e "$HOME/.config/sunshine"
run doctor.sh --host
check 'doctor uses matching effective directory' test "$RC" -eq 0
run uninstall.sh --destroy-host-state
check 'destroy uses matching effective directory' test ! -e "$CONFIGURATION_DIRECTORY/sunshine"
end_case

new_case
unset QD_SUNSHINE_CONFIG_DIR
export XDG_CONFIG_HOME="$HOME/xdg-only"
export MOCK_MANAGER_ENV="$(printf 'DISPLAY=:1\nXDG_CONFIG_HOME=%s' "$XDG_CONFIG_HOME")"
run install-host.sh
check 'XDG_CONFIG_HOME alone is honored' test "$RC" -eq 0
check 'XDG config created at actual service location' test -f "$XDG_CONFIG_HOME/sunshine/sunshine.conf"
end_case
new_case
export MOCK_SESSION_TYPE=wayland MOCK_MANAGER_ENV='WAYLAND_DISPLAY=wayland-0'
run install-host.sh --capture portal
check 'Wayland portal configuration reaches modeled control listener' test "$RC" -eq 0
run doctor.sh --host
check 'Wayland doctor discloses portal lock limitation' contains "$CASE/out" '锁屏会终止'
: >"$CASE/log"
run install-host.sh --capture x11
check 'x11 on actual Wayland is preflight error' test "$RC" -ne 0
check 'x11 mismatch does not mutate packages' absent "$CASE/log" 'sudo '
end_case

# Config: valid choices preserved, explicit auto, comments, conflict checks, atomic backups.
for capture in kms portal x11 nvfbc wlr kwin; do
 new_case; write_conf
 case "$capture" in wlr|kwin) export MOCK_SESSION_TYPE=wayland MOCK_MANAGER_ENV='WAYLAND_DISPLAY=wayland-0';; esac
 printf 'capture = %s # intentional\n' "$capture" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run install-host.sh
 check "preserve compatible $capture selection" test "$RC" -eq 0
 check "preserve $capture bytes/comment" contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "capture = $capture # intentional"
 run doctor.sh --host
 check "doctor accepts compatible $capture session" test "$RC" -eq 0
 end_case
done
for capture in wlr kwin; do
 new_case; installed; write_conf; active
 run install-host.sh --capture "$capture"
 check "explicit $capture on X11 rejected" test "$RC" -ne 0
 check "explicit $capture conflict precedes mutation" absent "$CASE/log" 'sudo '
 check "explicit $capture conflict preserves config" absent "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'capture ='
 printf 'capture = %s # intentional\n' "$capture" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run install-host.sh
 check "preserved $capture on X11 rejected" test "$RC" -ne 0
 check "$capture conflict does not erase selection" contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "capture = $capture # intentional"
 check "$capture conflict leaves active service untouched" absent "$CASE/log" 'systemctl --user restart'
 run doctor.sh --host
 check "doctor rejects $capture on X11" test "$RC" -ne 0
 check "doctor names $capture Wayland requirement" contains "$CASE/out" "capture=$capture 需要 Wayland"
 end_case
done
for capture in xcb auto typo; do
 new_case; write_conf
 printf 'capture = %s\n' "$capture" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run install-host.sh
 check "invalid existing $capture is preflight error" test "$RC" -ne 0
 check "$capture not silently deleted" contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" "capture = $capture"
 check "$capture fails before apt" absent "$CASE/log" 'apt-get'
 run install-host.sh --capture auto
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
run install-host.sh
check 'commented config converges successfully' test "$RC" -eq 0
check 'unknown scalar/comment preserved' contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'custom_key = custom value # untouched'
check 'IPv4 setting converged with comment' contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'address_family = ipv4 # old choice'
check 'origin inserted before comment' contains "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf" 'https://existing.example:47990, https://100.64.0.2:48001 #trusted'
check 'custom Moonlight address uses base' contains "$CASE/out" '100.64.0.2:48000'
check 'custom UI address uses base+1' contains "$CASE/out" 'https://100.64.0.2:48001'
check 'backup retains original bytes' cmp -s "$CASE/before" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
: >"$CASE/log"; run install-host.sh
check 'unchanged rerun leaves backup intact' cmp -s "$CASE/before" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
check 'unchanged rerun does not restart' absent "$CASE/log" 'systemctl --user restart'
end_case
for port in 01029 1028 65515 999999999999999999999999999999 foo; do
 new_case; write_conf; printf 'port = %s\n' "$port" >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
 run install-host.sh
 check "invalid port $port rejected before sudo" absent "$CASE/log" 'sudo '
 check "invalid port $port fails" test "$RC" -ne 0
 end_case
done
new_case; write_conf; printf 'origin_web_ui_allowed = pc # local only\n' >>"$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
run install-host.sh
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
run install-host.sh
check 'failed config promotion is surfaced' test "$RC" -ne 0
check 'failed config promotion preserves original' cmp -s "$CASE/change" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf"
check 'failed config promotion preserves original backup' cmp -s "$CASE/change" "$QD_SUNSHINE_CONFIG_DIR/sunshine.conf.bak"
check 'failed config promotion does not restart daemon' absent "$CASE/log" 'systemctl --user restart'
end_case

# Required input and listener failures do not become readiness success.
new_case; rm "$QD_UINPUT_NODE"; run install-host.sh
check 'missing uinput blocks ready result' test "$RC" -ne 0
check 'missing device never triggers usermod' absent "$CASE/log" 'usermod'
check 'missing device never starts service' absent "$CASE/log" 'systemctl --user start '
end_case
new_case; chmod 000 "$QD_UINPUT_NODE"; run install-host.sh
check 'inaccessible uinput blocks readiness' test "$RC" -ne 0
check 'inaccessible uinput has no automatic group mutation' absent "$CASE/log" 'usermod'
end_case
new_case; rm "$QD_UHID_NODE"; run install-host.sh
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
 run doctor.sh --host
 check "doctor fails $scenario" test "$RC" -ne 0
 check "doctor never mutates on $scenario" absent "$CASE/log" 'sudo '
 end_case
done
new_case; export MOCK_LISTEN=no; run install-host.sh
check 'installer fails bounded listener wait' test "$RC" -ne 0
check 'timeout does not claim ready' absent "$CASE/out" '控制端口已监听'
end_case
new_case; export MOCK_HIDE_OWNER=1; run install-host.sh
check 'unobservable listener PID is disclosed' contains "$CASE/out" '未验证所有者'
end_case

# Uninstall protects ownership and stops the daemon before destructive actions.
new_case; installed; write_conf; active
run uninstall.sh --host-package
check 'unowned package removal refused' test "$RC" -ne 0
check 'ownership checked before stopping service' test -f "$CASE/active"
run uninstall.sh --host-package --force-remove-preexisting-package
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
 run uninstall.sh --host-package --force-remove-preexisting-package
 check "$mode blocks package removal" test "$RC" -ne 0
 check "$mode leaves package installed" test -s "$CASE/version"
 check "$mode never calls apt remove" absent "$CASE/log" 'apt-get remove'
 end_case
done
new_case; installed; write_conf; active
run uninstall.sh --destroy-host-state
check 'state deletion succeeds' test "$RC" -eq 0
check 'state deletion leaves process stopped' test ! -e "$CASE/active"
check 'state deletion disables next-login startup' test ! -e "$CASE/enabled"
check 'state deletion removes effective config' test ! -e "$QD_SUNSHINE_CONFIG_DIR"
check 'state deletion does not remove package' test -s "$CASE/version"
check 'state deletion requires configured reinstall before reuse' contains "$CASE/out" 'install-host.sh'
end_case
new_case; installed; write_conf; active
export MOCK_STILL_ENABLED=1
run uninstall.sh --destroy-host-state
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
     run uninstall.sh --host-package --force-remove-preexisting-package --destroy-host-state
 else
     run uninstall.sh --destroy-host-state
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
 run uninstall.sh --destroy-host-state
 check "$parent parent symlink allowed for state deletion" test "$RC" -eq 0
 check "$parent parent symlink keeps sibling contents" contains "$HOME/.config/sibling" 'keep parent content'
 check "$parent parent symlink service disabled" test ! -e "$CASE/enabled"
 check "$parent parent symlink state removed" test ! -e "$HOME/.config/sunshine"
 end_case
done
new_case; run install-host.sh; run uninstall.sh --host-package
check 'owned uninstall clears stale ownership proof' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case

new_case
mkdir -p "$QD_HOST_STATE_DIR"
printf 'package_preexisting=false\n' >"$QD_HOST_STATE_DIR/host.state"
run uninstall.sh --host-package
check 'already absent package clears stale ownership' test ! -e "$QD_HOST_STATE_DIR/host.state"
end_case

# Client extraction, pins/provenance, ownership and interrupted replacement.
new_case; client_fixture; run install-client.sh
check 'extracted AppImage client installs' test "$RC" -eq 0
check 'extracted AppRun resolves to executable' test -x "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/AppRun"
: >"$CASE/log"; run install-client.sh
check 'client rerun skips download based on provenance' absent "$CASE/log" 'curl '
check 'client provenance wording does not claim rehash' contains "$CASE/out" '未重新校验'
rm "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/com.moonlight_stream.Moonlight.desktop"
run install-client.sh
check 'missing extracted desktop metadata triggers repair' test "$RC" -eq 0
check 'extracted desktop metadata restored' test -f "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/com.moonlight_stream.Moonlight.desktop"
# The fake payload is intentionally not the published payload; test both marker paths.
run doctor.sh --client
check 'doctor rejects wrong pinned provenance' test "$RC" -ne 0
printf '%s\n' "$QD_MOONLIGHT_SHA256" >"$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/.quick-deploy-sha256"
run doctor.sh --client
check 'modeled client with published marker passes structural checks' test "$RC" -eq 0
rm "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/usr/bin/moonlight"
run doctor.sh --client
check 'broken AppRun symlink fails client doctor' test "$RC" -ne 0
mkdir -p "$HOME/.local/opt/moonlight/foreign-build"
run uninstall.sh --client
check 'client removes owned payload only' test ! -d "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION"
check 'client preserves foreign directory' test -d "$HOME/.local/opt/moonlight/foreign-build"
end_case
new_case; mkdir -p "$HOME/.local/opt/moonlight"; run doctor.sh --client
check 'empty client directory is a failure' test "$RC" -ne 0
end_case
for failure in digest size extract foreign; do
 new_case; client_fixture
 case "$failure" in
 digest) export QD_TEST_MOONLIGHT_SHA256="$(printf '%064d' 0)";;
 size) export QD_TEST_MOONLIGHT_SIZE=1;;
 extract) export MOCK_EXTRACT_FAIL=1;;
 foreign) mkdir -p "$HOME/.local/bin"; printf 'foreign\n' >"$HOME/.local/bin/moonlight";;
 esac
 run install-client.sh
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
 run install-client.sh
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
run uninstall.sh --client
check 'hidden residue uninstall succeeds' test "$RC" -eq 0
check 'marked hidden staging removed' test ! -e "$opt/.staging-$QD_MOONLIGHT_VERSION.123"
check 'marked hidden backup removed' test ! -e "$opt/.backup-$QD_MOONLIGHT_VERSION.123"
check 'unmarked hidden contents preserved' diff -qr "$CASE/hidden-before" "$opt/.foreign-hidden"
end_case
new_case; client_fixture; run install-client.sh
printf 'old marker\n' >"$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/.quick-deploy-sha256"
export MOCK_FAIL_PROMOTE=1
run install-client.sh
check 'failed client promotion is surfaced' test "$RC" -ne 0
check 'failed client promotion restores old target' contains "$HOME/.local/opt/moonlight/$QD_MOONLIGHT_VERSION/.quick-deploy-sha256" 'old marker'
end_case
new_case; export MOCK_UID=0; run install-host.sh
check 'root installer refused' test "$RC" -ne 0
end_case
new_case; run install-host.sh --version v2026.516.143833
check 'obsolete security baseline rejected' test "$RC" -ne 0
check 'obsolete version fails before network' absent "$CASE/log" 'curl '
run install-client.sh --version v9999
check 'unpinned client version rejected' test "$RC" -ne 0
end_case

for script in "$MODULE_DIR"/*.sh "$MODULE_DIR"/lib/common.sh "$TESTS_DIR/run.sh"; do check "syntax: ${script##*/}" bash -n "$script"; done
check 'scoped diff whitespace' git -C "$MODULE_DIR" diff --check -- .
printf '\nAssertions: passed=%d failed=%d\n' "$PASSED" "$FAILED"
[ "$FAILED" -eq 0 ]
