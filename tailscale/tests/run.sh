#!/bin/bash
# All tests run against PATH mocks and a private TMPDIR. Host apt/systemd/tailscale
# are never executed or written.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
MOCK="$TMP/mock"
STATE="$TMP/state"
TEMP_ROOT="$TMP/temp"
mkdir -p "$MOCK" "$STATE" "$TEMP_ROOT"
export TEST_STATE="$STATE"

fail() { echo "FAIL: $*" >&2; exit 1; }
expect() { grep -Fq "$2" "$1" || fail "expected '$2' in $1"; }
expect_not() { ! grep -Fq "$2" "$1" || fail "did not expect '$2' in $1"; }
expect_empty_temp() { [ -z "$(find "$TEMP_ROOT" -mindepth 1 -type f -print -quit)" ] || fail "temporary files leaked in $TEMP_ROOT"; }
line_of() { grep -n -F "$2" "$1" | head -n1 | cut -d: -f1; }

cat > "$MOCK/sudo" <<'SH'
#!/bin/bash
exec "$@"
SH
cat > "$MOCK/dpkg" <<'SH'
#!/bin/bash
if [ "$1" = --compare-versions ]; then
  [ "${MOCK_VERSION_OK:-yes}" = yes ] && exit 0 || exit 1
fi
exit 0
SH
cat > "$MOCK/dpkg-query" <<'SH'
#!/bin/bash
# The product must query the complete database: exactly -W plus one -f= argument,
# with no tailscale positional selector. Reject regressions inside the mock.
echo "dpkg-query $*" >> "$TEST_STATE/calls"
if [ "$#" -ne 2 ] || [ "$1" != -W ] || [ "$2" != '-f=${binary:Package}\t${db:Status-Status}\t${Version}\n' ]; then
  echo 'dpkg query unexpected positional or format' >&2
  exit 98
fi
count=0; [ -f "$TEST_STATE/dpkg_query_count" ] && count="$(cat "$TEST_STATE/dpkg_query_count")"
count=$((count + 1)); printf '%s' "$count" > "$TEST_STATE/dpkg_query_count"
case "${MOCK_DPKG_QUERY_FAIL:-}" in
  all) echo 'dpkg query failure' >&2; exit 77 ;;
  second) if [ "$count" -eq 2 ]; then echo 'dpkg query failure' >&2; exit 77; fi ;;
esac
mode="${MOCK_DPKG_RECORD_MODE:-}"
[ -n "$mode" ] || { [ "${MOCK_INSTALLED:-yes}" = yes ] && mode=installed || mode=absent; }
# Post-install modes deliberately model an apt command that returned success but
# did not leave the required dpkg record/state behind on the second full query.
case "$mode" in
  post-absent) [ "$count" -eq 1 ] && mode=absent || mode=absent ;;
  post-config-files) [ "$count" -eq 1 ] && mode=absent || mode=config-files ;;
  post-empty-version) [ "$count" -eq 1 ] && mode=absent || mode=empty-version ;;
  *)
    # absent/config-files model a healthy pre-install database; successful mocked
    # apt changes either record into installed for the normal post-install reread.
    if { [ "$mode" = config-files ] || [ "$mode" = absent ]; } && [ -f "$TEST_STATE/package_installed" ]; then mode=installed; fi
    ;;
esac
case "$mode" in
  absent) ;;
  installed) printf 'tailscale\tinstalled\t1.98.10\n' ;;
  config-files) printf 'tailscale\tconfig-files\t1.98.10\n' ;;
  empty-version) printf 'tailscale\tinstalled\t\n' ;;
  *) echo "unknown dpkg record mode: $mode" >&2; exit 97 ;;
esac
exit 0
SH
cat > "$MOCK/curl" <<'SH'
#!/bin/bash
echo "curl $*" >> "$TEST_STATE/calls"
if [[ " $* " == *" --proxy "* ]]; then
  exit "${MOCK_PROXY_PROBE_RC:-0}"
fi
count_file="$TEST_STATE/curl_download_count"
count=0; [ -f "$count_file" ] && count="$(cat "$count_file")"
count=$((count + 1)); printf '%s' "$count" > "$count_file"
if [ "${MOCK_CURL_FAIL_STAGE:-}" = "key" ] && [ "$count" -eq 1 ]; then echo 'curl key failure' >&2; exit 22; fi
if [ "${MOCK_CURL_FAIL_STAGE:-}" = "list" ] && [ "$count" -eq 2 ]; then echo 'curl list failure' >&2; exit 22; fi
out=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then shift; out="$1"; fi
  shift || true
done
case "$out" in
  *key*) printf 'mock key' > "$out" ;;
  *) printf 'deb [signed-by=/key] https://pkgs.tailscale.com/stable/ubuntu %s main\n' "${MOCK_CODENAME:-noble}" > "$out" ;;
esac
SH
cat > "$MOCK/apt-get" <<'SH'
#!/bin/bash
echo "apt-get $*" >> "$TEST_STATE/calls"
[ "${MOCK_APT_FAIL:-no}" = yes ] && { echo 'E: MergeList problem' >&2; exit 100; }
if [[ " $* " == *" install "* ]]; then
  [ "${MOCK_APT_INSTALL_FAIL:-no}" = yes ] && { echo 'E: tailscale install failure' >&2; exit 101; }
  touch "$TEST_STATE/package_installed"
fi
exit 0
SH
cat > "$MOCK/install" <<'SH'
#!/bin/bash
echo "install $*" >> "$TEST_STATE/calls"
[ "${MOCK_INSTALL_FAIL:-no}" = yes ] && { echo 'install failure' >&2; exit 73; }
if [[ " $* " == *" -d "* ]]; then
  for arg in "$@"; do [[ "$arg" = /* ]] && mkdir -p "$arg"; done
  exit 0
fi
src="${@: -2:1}"; dst="${@: -1}"
mkdir -p "$(dirname "$dst")"; cp "$src" "$dst"
SH
cat > "$MOCK/systemctl" <<'SH'
#!/bin/bash
echo "systemctl $*" >> "$TEST_STATE/calls"
manager="$TEST_STATE/manager_env"
process="$TEST_STATE/process_env"
sync_manager() {
  if [ -f "$TEST_STATE/dropin/quick-deploy-clash-proxy.conf" ]; then
    awk -F'"' '/Environment="/ {sub(/^.*Environment="/, ""); sub(/"$/, ""); print}' "$TEST_STATE/dropin/quick-deploy-clash-proxy.conf" | paste -sd' ' > "$manager"
  else
    printf '%s\n' "${MOCK_EXTERNAL_ENVIRONMENT:-}" > "$manager"
  fi
}
case "$1" in
  is-enabled)
    case "${MOCK_MASKED:-}" in masked|masked-runtime) printf '%s\n' "$MOCK_MASKED"; exit 1;; esac
    [ "${MOCK_ENABLED_OK:-yes}" = yes ] && exit 0 || exit 1
    ;;
  is-active)
    if [ -f "$TEST_STATE/restarted" ] && [ "${MOCK_ACTIVE_AFTER_RESTART:-yes}" = no ]; then exit 3; fi
    [ "${MOCK_ACTIVE_OK:-yes}" = yes ] && exit 0 || exit 1
    ;;
  show)
    [[ "$*" == *Environment* ]] && { [ -f "$manager" ] && cat "$manager" || printf '%s\n' "${MOCK_EXTERNAL_ENVIRONMENT:-}"; }
    exit 0
    ;;
  daemon-reload)
    count=0; [ -f "$TEST_STATE/reload_count" ] && count="$(cat "$TEST_STATE/reload_count")"; count=$((count + 1)); printf '%s' "$count" > "$TEST_STATE/reload_count"
    sync_manager
    if [ "${MOCK_DAEMON_RELOAD_FAIL_ONCE:-no}" = yes ] && [ ! -f "$TEST_STATE/reload_failed_once" ]; then touch "$TEST_STATE/reload_failed_once"; echo 'daemon-reload failure' >&2; exit 44; fi
    ;;
  restart)
    count=0; [ -f "$TEST_STATE/restart_count" ] && count="$(cat "$TEST_STATE/restart_count")"; count=$((count + 1)); printf '%s' "$count" > "$TEST_STATE/restart_count"
    if [ "${MOCK_RESTART_FAIL_ONCE:-no}" = yes ] && [ ! -f "$TEST_STATE/restart_failed_once" ]; then touch "$TEST_STATE/restart_failed_once"; echo 'restart failure' >&2; exit 55; fi
    [ -f "$manager" ] && cp "$manager" "$process" || : > "$process"
    touch "$TEST_STATE/restarted"
    ;;
esac
exit 0
SH
cat > "$MOCK/tailscale" <<'SH'
#!/bin/bash
echo "tailscale $*" >> "$TEST_STATE/calls"
if [ "$1" = status ]; then
  case "${MOCK_STATUS_MODE:-logged}" in
    error) echo 'status backend unavailable' >&2; exit 9 ;;
    logged)
      if [ -f "$TEST_STATE/logged" ] || [ "${MOCK_LOGGED_IN:-yes}" = yes ]; then
        printf '{"BackendState":"Running","CurrentTailnet":{"Name":"test-tailnet"},"Self":{"TailscaleIPs":["100.64.0.1"]}}\n'
      else
        printf '{"BackendState":"NeedsLogin"}\n'
      fi
      ;;
    needs)
      if [ -f "$TEST_STATE/logged" ]; then
        printf '{"BackendState":"Running","CurrentTailnet":{"Name":"test-tailnet"},"Self":{"TailscaleIPs":["100.64.0.1"]}}\n'
      else
        printf '{"BackendState":"NeedsLogin"}\n'
      fi
      ;;
    stopped) printf '{"BackendState":"Stopped"}\n' ;;
  esac
  exit 0
fi
if [ "$1" = up ]; then
  if [ "${MOCK_REQUIRE_PROXY:-no}" = yes ] && ! grep -Fq 'HTTP_PROXY=http://127.0.0.1:7897' "$TEST_STATE/process_env" 2>/dev/null; then
    echo 'up failed: proxy was not applied first' >&2; exit 42
  fi
  touch "$TEST_STATE/logged"
fi
exit 0
SH
cat > "$MOCK/pgrep" <<'SH'
#!/bin/bash
[ "${MOCK_CLASH_PROCESS:-no}" = yes ]
SH
cat > "$MOCK/ip" <<'SH'
#!/bin/bash
[ "${MOCK_CLASH_ROUTE:-no}" = yes ] && printf '0.0.0.0/1 via 198.18.0.2 dev Meta\n'
SH
chmod +x "$MOCK"/*

ubuntu24="$TMP/ubuntu24"
ubuntu22="$TMP/ubuntu22"
oracular="$TMP/oracular"
printf 'ID=ubuntu\nVERSION_ID="24.04"\nVERSION_CODENAME=noble\nPRETTY_NAME="Ubuntu test"\n' > "$ubuntu24"
printf 'ID=ubuntu\nVERSION_ID="22.04"\nVERSION_CODENAME=jammy\nPRETTY_NAME="Ubuntu old"\n' > "$ubuntu22"
printf 'ID=ubuntu\nVERSION_ID="24.10"\nVERSION_CODENAME=oracular\nPRETTY_NAME="Ubuntu Oracular test"\n' > "$oracular"
valid_clash="$TMP/valid-clash.yaml"
bad_clash="$TMP/bad-clash.yaml"
printf 'mixed-port: 7897\ntun:\n  enable: true\n' > "$valid_clash"
printf 'mixed-port: nope\ntun:\n  enable: true\n' > "$bad_clash"

reset_state() {
  rm -rf "$STATE"/* "$TEMP_ROOT"/*
  mkdir -p "$STATE/dropin"
  : > "$STATE/calls"
}

run_install() {
  local os="$1"; shift
  reset_state
  PATH="$MOCK:$PATH" TMPDIR="$TEMP_ROOT" OS_RELEASE_FILE="$os" \
  TAILSCALE_KEYRING_FILE="$STATE/key" TAILSCALE_SOURCE_LIST_FILE="$STATE/tailscale.list" \
  TAILSCALE_DROPIN_DIR="$STATE/dropin" CLASH_CONFIG="${TEST_CLASH_CONFIG:-$TMP/no-clash.yaml}" \
  MOCK_CODENAME="${MOCK_CODENAME:-noble}" "$ROOT/install.sh" "$@" </dev/null > "$STATE/out" 2>&1
}

run_install_tty() {
  local answers="$1"; shift
  reset_state
  printf '%b' "$answers" | script -qefc \
    "env PATH=$MOCK:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin TMPDIR=$TEMP_ROOT TEST_STATE=$STATE OS_RELEASE_FILE=$ubuntu24 TAILSCALE_KEYRING_FILE=$STATE/key TAILSCALE_SOURCE_LIST_FILE=$STATE/tailscale.list TAILSCALE_DROPIN_DIR=$STATE/dropin CLASH_CONFIG=${TEST_CLASH_CONFIG:-$TMP/no-clash.yaml} MOCK_CODENAME=noble MOCK_STATUS_MODE=${MOCK_STATUS_MODE:-needs} MOCK_LOGGED_IN=${MOCK_LOGGED_IN:-no} MOCK_CLASH_PROCESS=${MOCK_CLASH_PROCESS:-no} MOCK_CLASH_ROUTE=${MOCK_CLASH_ROUTE:-no} MOCK_REQUIRE_PROXY=${MOCK_REQUIRE_PROXY:-no} $ROOT/install.sh $*" \
    /dev/null > "$STATE/out" 2>&1
}

start_proxy_scenario() {
  local external="${MOCK_EXTERNAL_ENVIRONMENT:-${MOCK_ENVIRONMENT:-}}"
  reset_state
  printf '%s\n' "$external" > "$STATE/manager_env"
  printf '%s\n' "$external" > "$STATE/process_env"
}

invoke_proxy() {
  local config="$1"; shift
  local external="${MOCK_EXTERNAL_ENVIRONMENT:-${MOCK_ENVIRONMENT:-}}"
  PATH="$MOCK:$PATH" TMPDIR="$TEMP_ROOT" TAILSCALE_DROPIN_DIR="$STATE/dropin" CLASH_CONFIG="$config" \
    MOCK_EXTERNAL_ENVIRONMENT="$external" \
    "$ROOT/configure-clash-proxy.sh" "$@" </dev/null > "$STATE/out" 2>&1
}

run_proxy() {
  local config="$1"; shift
  start_proxy_scenario
  invoke_proxy "$config" "$@"
}

# Ubuntu acceptance and package branches; no broad package action is allowed.
if MOCK_VERSION_OK=no run_install "$ubuntu22" --no-clash-check; then fail 'old Ubuntu accepted'; fi
expect "$STATE/out" '仅支持 Ubuntu 24.04'
MOCK_DPKG_RECORD_MODE=absent MOCK_STATUS_MODE=logged run_install "$ubuntu24" --no-clash-check
expect "$STATE/out" '未安装 Tailscale'
expect "$STATE/calls" 'apt-get install -y tailscale'
expect "$STATE/calls" 'dpkg-query -W -f=${binary:Package}\t${db:Status-Status}\t${Version}\n'
expect_not "$STATE/calls" 'dpkg-query -W -f=${binary:Package}\t${db:Status-Status}\t${Version}\n tailscale'
[ "$(cat "$STATE/dpkg_query_count")" -eq 2 ] || fail 'absent query did not run before and after apt'
expect_not "$STATE/calls" 'upgrade'
expect_not "$STATE/calls" 'autoremove'
MOCK_DPKG_RECORD_MODE=config-files MOCK_STATUS_MODE=logged run_install "$ubuntu24" --no-clash-check
expect "$STATE/out" '当前状态为 config-files；开始安装'
expect "$STATE/calls" 'apt-get install -y tailscale'
if MOCK_DPKG_QUERY_FAIL=all run_install "$ubuntu24" --no-clash-check; then fail 'all dpkg-query failure accepted'; fi
expect "$STATE/out" 'dpkg query failure'
expect "$STATE/out" '无法查询 Tailscale 软件包状态'
expect_not "$STATE/calls" 'apt-get install -y tailscale'
expect_not "$STATE/calls" 'systemctl enable'
expect_not "$STATE/calls" 'tailscale status'
if MOCK_DPKG_RECORD_MODE=installed MOCK_DPKG_QUERY_FAIL=second run_install "$ubuntu24" --no-clash-check; then fail 'second dpkg-query failure accepted'; fi
expect "$STATE/out" 'dpkg query failure'
expect "$STATE/out" 'apt 已返回，但无法重新读取 Tailscale 软件包状态'
expect "$STATE/calls" 'apt-get install -y tailscale'
expect_not "$STATE/calls" 'systemctl enable'
expect_not "$STATE/calls" 'systemctl start'
expect_not "$STATE/calls" 'tailscale status'

# Apt can return success while dpkg still lacks the required post-install state.
# Each mode changes its answer on the second full-database query and must stop
# before service setup or login.
if MOCK_DPKG_RECORD_MODE=post-absent run_install "$ubuntu24" --no-clash-check; then fail 'post-absent accepted'; fi
expect "$STATE/out" 'apt 已返回，但无法重新读取 Tailscale 软件包状态'
expect "$STATE/calls" 'apt-get install -y tailscale'
expect_not "$STATE/calls" 'systemctl enable'
expect_not "$STATE/calls" 'systemctl start'
expect_not "$STATE/calls" 'tailscale status'
if MOCK_DPKG_RECORD_MODE=post-config-files run_install "$ubuntu24" --no-clash-check; then fail 'post-config-files accepted'; fi
expect "$STATE/out" 'Tailscale 状态仍为 config-files'
expect "$STATE/calls" 'apt-get install -y tailscale'
expect_not "$STATE/calls" 'systemctl enable'
expect_not "$STATE/calls" 'systemctl start'
expect_not "$STATE/calls" 'tailscale status'
if MOCK_DPKG_RECORD_MODE=post-empty-version run_install "$ubuntu24" --no-clash-check; then fail 'post-empty-version accepted'; fi
expect "$STATE/out" 'Tailscale 版本为空'
expect "$STATE/calls" 'apt-get install -y tailscale'
expect_not "$STATE/calls" 'systemctl enable'
expect_not "$STATE/calls" 'systemctl start'
expect_not "$STATE/calls" 'tailscale status'
printf 'TRACE dpkg-post-install: absent=blocked config-files=blocked empty-version=blocked\n'
MOCK_INSTALLED=yes MOCK_STATUS_MODE=logged run_install "$ubuntu24" --no-clash-check
expect "$STATE/out" '已安装 Tailscale'
expect "$STATE/out" '已登录 tailnet: test-tailnet'
expect_not "$STATE/calls" 'tailscale up'
printf 'TRACE dpkg-records: full-db-query=no-positional absent=install config-files=install all-failure=blocked second-failure=blocked\n'
if MOCK_APT_FAIL=yes run_install "$ubuntu24" --no-clash-check; then fail 'APT failure accepted'; fi
expect "$STATE/out" 'MergeList problem'
expect "$STATE/out" '脚本不会自动删除 lists'

if MOCK_APT_INSTALL_FAIL=yes run_install "$ubuntu24" --no-clash-check; then fail 'APT install failure accepted'; fi
expect "$STATE/out" 'tailscale install failure'
expect_not "$STATE/calls" 'systemctl enable'
expect_not "$STATE/calls" 'tailscale status'
MOCK_CODENAME=oracular MOCK_INSTALLED=yes MOCK_STATUS_MODE=logged run_install "$oracular" --no-clash-check
expect "$STATE/calls" 'oracular.noarmor.gpg'
expect "$STATE/calls" 'oracular.tailscale-keyring.list'

# Missing curl and temporary cleanup on download/write failures.
mkdir -p "$TMP/no-curl"
cat > "$TMP/no-curl/dirname" <<'SH'
#!/bin/bash
case "$1" in */*) printf '%s\n' "${1%/*}";; *) printf '.\n';; esac
SH
cp "$MOCK/dpkg" "$TMP/no-curl/dpkg"
chmod +x "$TMP/no-curl/dirname" "$TMP/no-curl/dpkg"
if PATH="$TMP/no-curl" OS_RELEASE_FILE="$ubuntu24" "$ROOT/install.sh" --no-clash-check > "$STATE/out" 2>&1; then fail 'missing curl accepted'; fi
expect "$STATE/out" 'sudo apt install curl'
if MOCK_CURL_FAIL_STAGE=key run_install "$ubuntu24" --no-clash-check; then fail 'key download failure accepted'; fi
expect "$STATE/out" 'curl key failure'; expect_empty_temp
if MOCK_CURL_FAIL_STAGE=list run_install "$ubuntu24" --no-clash-check; then fail 'list download failure accepted'; fi
expect "$STATE/out" 'curl list failure'; expect_empty_temp
if MOCK_INSTALL_FAIL=yes run_install "$ubuntu24" --no-clash-check; then fail 'repository write failure accepted'; fi
expect "$STATE/out" 'install failure'; expect_empty_temp

# Masked services are explicit admin policy: no enable/start after either state.
for mask in masked masked-runtime; do
  if MOCK_MASKED="$mask" run_install "$ubuntu24" --no-clash-check; then fail "$mask accepted"; fi
  expect "$STATE/out" "被 $mask"
  expect_not "$STATE/calls" 'systemctl enable'
  expect_not "$STATE/calls" 'systemctl start'
done
if MOCK_ENABLED_OK=no run_install "$ubuntu24" --no-clash-check; then fail 'disabled verification accepted'; fi
expect "$STATE/out" '未处于 enabled'
if MOCK_ACTIVE_OK=no run_install "$ubuntu24" --no-clash-check; then fail 'inactive verification accepted'; fi
expect "$STATE/out" '未处于 active'

# Status command failure is a hard error, never a non-interactive "not logged" fallback.
if MOCK_STATUS_MODE=error run_install "$ubuntu24" --no-clash-check; then fail 'status failure accepted'; fi
expect "$STATE/out" 'status backend unavailable'
expect "$STATE/out" '状态不可读'
expect_not "$STATE/calls" 'tailscale up'
MOCK_STATUS_MODE=needs run_install "$ubuntu24" --no-clash-check
expect "$STATE/out" '非交互 stdin：未启动登录'
expect_not "$STATE/calls" 'tailscale up'
MOCK_STATUS_MODE=stopped run_install "$ubuntu24" --no-clash-check
expect "$STATE/out" 'Tailscale 尚未登录'
expect_not "$STATE/calls" 'tailscale up'

# Full main-entry TTY trace: first Y applies active Clash proxy/restarts it, second
# Y logs in. The mocked up command fails unless proxy was applied first.
TEST_CLASH_CONFIG="$valid_clash" MOCK_CLASH_PROCESS=yes MOCK_CLASH_ROUTE=yes MOCK_REQUIRE_PROXY=yes MOCK_STATUS_MODE=needs run_install_tty '\n\n'
expect "$STATE/out" '已应用并验证 tailscaled 代理'
expect "$STATE/out" '已登录 tailnet: test-tailnet'
restart_line="$(line_of "$STATE/calls" 'systemctl restart tailscaled.service')"
up_line="$(line_of "$STATE/calls" 'tailscale up')"
[ -n "$restart_line" ] && [ -n "$up_line" ] && [ "$restart_line" -lt "$up_line" ] || fail 'proxy restart did not precede tailscale up'
[ "$(grep -Fc 'tailscale up' "$STATE/calls")" -eq 1 ] || fail 'expected exactly one tailscale up'
printf 'TRACE proxy-before-login: restart-line=%s up-line=%s\n' "$restart_line" "$up_line"

# Explicit TTY decline is a successful choice: no up, and the next command is shown.
MOCK_STATUS_MODE=needs run_install_tty 'n\n' --no-clash-check
expect "$STATE/out" '下一步: sudo tailscale up'
expect_not "$STATE/calls" 'tailscale up'

# Clash absence and invalid dynamic port remain non-destructive in automatic mode.
MOCK_CLASH_PROCESS=no MOCK_CLASH_ROUTE=no run_proxy "$TMP/no-clash.yaml" --auto
expect "$STATE/out" '未检测到活动的 Clash Verge TUN'
MOCK_CLASH_PROCESS=yes MOCK_CLASH_ROUTE=yes run_proxy "$bad_clash" --auto
expect "$STATE/out" 'mixed-port 缺失或无效'

# Strict proxy URL rejection must happen before probe or privileged writing.
for bad in 'http://host:0' 'http://host:99999' 'http://u:p@host:80' 'http://host:80/path' 'http://host:80?x=y' 'http://host:80#x' $'http://host:80\nnext' 'http://host:80"'; do
  if run_proxy "$valid_clash" --apply --proxy "$bad"; then fail "bad proxy accepted: $bad"; fi
  expect "$STATE/out" '只能是无认证'
  expect_not "$STATE/calls" 'curl --proxy'
  expect_not "$STATE/calls" 'install -m'
done
if run_proxy "$valid_clash" --status --proxy http://127.0.0.1:7897; then fail '--proxy without apply accepted'; fi
expect "$STATE/out" '只能与 --apply'

# Managed apply validates proxy, manager environment, running process environment and daemon state.
MOCK_ENVIRONMENT='' run_proxy "$valid_clash" --apply
expect "$STATE/dropin/quick-deploy-clash-proxy.conf" 'Managed by quick-deploy/tailscale'
expect "$STATE/out" '已应用并验证 tailscaled 代理'
expect "$STATE/manager_env" 'HTTP_PROXY=http://127.0.0.1:7897'
expect "$STATE/process_env" 'HTTP_PROXY=http://127.0.0.1:7897'
if MOCK_ACTIVE_AFTER_RESTART=no run_proxy "$valid_clash" --apply; then fail 'apply accepted inactive restart'; fi
expect "$STATE/out" '重启后未处于 active'
if MOCK_INSTALL_FAIL=yes run_proxy "$valid_clash" --apply; then fail 'proxy write failure accepted'; fi
expect "$STATE/out" 'install failure'; expect_empty_temp

# Apply retry: daemon-reload first changes manager config, then restart fails. A
# second explicit apply must restart again rather than accepting that manager match.
start_proxy_scenario
if MOCK_RESTART_FAIL_ONCE=yes invoke_proxy "$valid_clash" --apply; then fail 'first apply retry scenario accepted'; fi
expect "$STATE/out" 'restart failure'
expect "$STATE/manager_env" 'HTTP_PROXY=http://127.0.0.1:7897'
expect_not "$STATE/process_env" 'HTTP_PROXY=http://127.0.0.1:7897'
MOCK_RESTART_FAIL_ONCE=yes invoke_proxy "$valid_clash" --apply
expect "$STATE/out" '已应用并验证 tailscaled 代理'
[ "$(cat "$STATE/restart_count")" -eq 2 ] || fail 'second apply did not restart'
expect "$STATE/process_env" 'HTTP_PROXY=http://127.0.0.1:7897'
# Auto mode sees the managed file too, so it also converges rather than trusting
# the manager-side match or proceeding to login.
MOCK_CLASH_PROCESS=yes MOCK_CLASH_ROUTE=yes invoke_proxy "$valid_clash" --auto
expect "$STATE/out" '执行收敛 apply'
[ "$(cat "$STATE/restart_count")" -eq 3 ] || fail 'managed auto did not converge with restart'
printf 'TRACE apply-retry: reloads=%s restarts=%s manager=matched process=matched\n' "$(cat "$STATE/reload_count")" "$(cat "$STATE/restart_count")"

# Foreign path removal is refused.
start_proxy_scenario
printf 'foreign\n' > "$STATE/dropin/quick-deploy-clash-proxy.conf"
if invoke_proxy "$valid_clash" --remove; then fail 'foreign remove accepted'; fi
expect "$STATE/out" '拒绝删除'

# Remove retry A: the file is deleted, but daemon-reload fails. The second
# explicit remove sees absent and still reloads/restarts to converge.
MOCK_ENVIRONMENT='' run_proxy "$valid_clash" --apply
MOCK_DAEMON_RELOAD_FAIL_ONCE=yes invoke_proxy "$valid_clash" --remove || true
expect "$STATE/out" 'daemon-reload failure'
[ ! -e "$STATE/dropin/quick-deploy-clash-proxy.conf" ] || fail 'first remove did not delete managed file'
MOCK_DAEMON_RELOAD_FAIL_ONCE=yes invoke_proxy "$valid_clash" --remove
expect "$STATE/out" '已不存在；仍重新加载/重启'
[ "$(cat "$STATE/reload_count")" -eq 3 ] || fail 'absent remove did not reload again'
[ "$(cat "$STATE/restart_count")" -eq 2 ] || fail 'absent remove did not restart after reload failure'
printf 'TRACE remove-reload-retry: reloads=%s restarts=%s\n' "$(cat "$STATE/reload_count")" "$(cat "$STATE/restart_count")"

# Remove retry B: daemon-reload succeeds and updates manager state, restart
# fails, then absent-file remove retries the restart and converges the process.
MOCK_ENVIRONMENT='' run_proxy "$valid_clash" --apply
MOCK_RESTART_FAIL_ONCE=yes invoke_proxy "$valid_clash" --remove || true
expect "$STATE/out" 'restart failure'
[ ! -e "$STATE/dropin/quick-deploy-clash-proxy.conf" ] || fail 'restart-failed remove kept managed file'
MOCK_RESTART_FAIL_ONCE=yes invoke_proxy "$valid_clash" --remove
expect "$STATE/out" '已不存在；仍重新加载/重启'
[ "$(cat "$STATE/restart_count")" -eq 3 ] || fail 'absent remove did not retry restart'
printf 'TRACE remove-restart-retry: reloads=%s restarts=%s manager=cleared process=cleared\n' "$(cat "$STATE/reload_count")" "$(cat "$STATE/restart_count")"

# Remove preserves an external effective environment after the managed file is gone.
MOCK_ENVIRONMENT='' run_proxy "$valid_clash" --apply
MOCK_EXTERNAL_ENVIRONMENT='HTTP_PROXY=http://external.example:8080 HTTPS_PROXY=http://external.example:8080 NO_PROXY=localhost,127.0.0.1,::1' \
  invoke_proxy "$valid_clash" --remove
expect "$STATE/out" '其他 systemd 配置提供的 HTTP_PROXY 仍然有效'
expect "$STATE/manager_env" 'HTTP_PROXY=http://external.example:8080'
expect "$STATE/process_env" 'HTTP_PROXY=http://external.example:8080'

# Equal external proxy is preserved. Different external proxies are probed without
# disclosing their URL; an unreachable one blocks the caller instead of continuing.
MOCK_ENVIRONMENT='HTTP_PROXY=http://127.0.0.1:7897 HTTPS_PROXY=http://127.0.0.1:7897 NO_PROXY=localhost,127.0.0.1,::1' \
  MOCK_CLASH_PROCESS=yes MOCK_CLASH_ROUTE=yes run_proxy "$valid_clash" --auto
expect "$STATE/out" '由外部配置有效使用 http://127.0.0.1:7897'
MOCK_ENVIRONMENT='HTTP_PROXY=http://user:secret@external.example:8080 HTTPS_PROXY=http://user:secret@external.example:8080' \
  MOCK_CLASH_PROCESS=yes MOCK_CLASH_ROUTE=yes run_proxy "$valid_clash" --auto
expect "$STATE/out" '保留外部配置'
expect_not "$STATE/out" 'user:secret'
if MOCK_ENVIRONMENT='HTTP_PROXY=http://external.example:8080 HTTPS_PROXY=http://external.example:8080' MOCK_PROXY_PROBE_RC=7 MOCK_CLASH_PROCESS=yes MOCK_CLASH_ROUTE=yes run_proxy "$valid_clash" --auto; then fail 'unreachable external proxy allowed auto'; fi
expect "$STATE/out" '拒绝继续登录'

echo 'PASS: isolated Tailscale scenarios with proxy-before-login trace'
