#!/usr/bin/env bash
# Isolated tests that drive the production installer through mocked boundaries.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLER="$SCRIPT_DIR/install.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
PASS=0; FAIL=0
fail() { printf 'FAIL: %s\n' "$*" >&2; FAIL=$((FAIL + 1)); }
pass() { printf 'PASS: %s\n' "$*"; PASS=$((PASS + 1)); }
assert_eq() { [[ $1 == "$2" ]] || fail "${3:-values differ}: expected [$2], got [$1]"; }
assert_contains() { grep -Fq -- "$2" "$1" || fail "${3:-missing text}: $2"; }
assert_file() { [[ -f $1 && ! -L $1 ]] || fail "expected regular file: $1"; }
assert_not_exists() { [[ ! -e $1 && ! -L $1 ]] || fail "unexpected path: $1"; }

MOCK_BIN="$TMP_ROOT/mock-bin"; FIXTURES="$TMP_ROOT/fixtures"
mkdir -p "$MOCK_BIN" "$FIXTURES"
cat > "$MOCK_BIN/id" <<'MOCK'
#!/usr/bin/env bash
[[ $1 == -u ]] || exit 2
printf '%s\n' "${MOCK_UID:-$(command id -u)}"
MOCK
cat > "$MOCK_BIN/uname" <<'MOCK'
#!/usr/bin/env bash
[[ $1 == -m ]] || exit 2
printf '%s\n' "${MOCK_ARCH:-x86_64}"
MOCK
cat > "$MOCK_BIN/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
url=''; out=''; auth=''
while (($#)); do
    case $1 in
        -o) out=$2; shift 2 ;;
        -H) [[ $2 == 'Authorization: Bearer '* ]] && auth=$2; shift 2 ;;
        https://*) url=$1; shift ;;
        *) shift ;;
    esac
done
printf '%s|%s\n' "$url" "$auth" >> "$MOCK_CURL_LOG"
case "$url" in
    https://api.github.com/repos/zellij-org/zellij/releases/latest) cat "$MOCK_RELEASE_JSON" ;;
    https://github.com/zellij-org/zellij/releases/download/*/*.tar.gz) cp "$MOCK_ARCHIVE" "$out" ;;
    https://github.com/zellij-org/zellij/releases/download/*/*.sha256sum) cp "$MOCK_CHECKSUM" "$out" ;;
    *) printf 'unexpected URL: %s\n' "$url" >&2; exit 22 ;;
esac
MOCK
chmod +x "$MOCK_BIN/id" "$MOCK_BIN/uname" "$MOCK_BIN/curl"

make_os_release() {
    OS_RELEASE="$TMP_ROOT/os-release"
    cat > "$OS_RELEASE" <<OS
ID=ubuntu
VERSION_ID="${1:-24.04}"
PRETTY_NAME="Ubuntu ${1:-24.04}"
OS
}
platform_names() {
    case ${TEST_ARCH:-x86_64} in
        x86_64|amd64) TARGET_TRIPLE=x86_64-unknown-linux-musl ;;
        aarch64|arm64) TARGET_TRIPLE=aarch64-unknown-linux-musl ;;
        *) fail "unsupported fixture architecture: ${TEST_ARCH}"; return 1 ;;
    esac
    ARCHIVE_NAME="zellij-$TARGET_TRIPLE.tar.gz"; CHECKSUM_NAME="zellij-$TARGET_TRIPLE.sha256sum"
    UPSTREAM_PATH="target/$TARGET_TRIPLE/release/zellij"
}
make_assets() {
    local tag=$1 mode=${2:-valid} payload="$TMP_ROOT/payload"
    platform_names; rm -rf "$payload"; mkdir -p "$payload"
    cat > "$payload/zellij" <<EOF_BINARY
#!/usr/bin/env bash
if [[ \${1:-} == --version ]]; then printf 'zellij ${tag#v}\\n'; else exit 64; fi
EOF_BINARY
    chmod 755 "$payload/zellij"
    if [[ $mode == unsafe-tar ]]; then printf extra > "$payload/extra"; tar -czf "$FIXTURES/archive.tar.gz" -C "$payload" zellij extra
    else tar -czf "$FIXTURES/archive.tar.gz" -C "$payload" zellij; fi
    if [[ $mode == bad-binary-checksum ]]; then printf '%064d  %s\n' 0 "$UPSTREAM_PATH" > "$FIXTURES/checksum.sha256sum"
    else sha256sum "$payload/zellij" | awk -v n="$UPSTREAM_PATH" '{print $1 "  " n}' > "$FIXTURES/checksum.sha256sum"; fi
}
make_release() {
    local tag=$1 mode=${2:-valid}; platform_names
    case "$mode" in
        malformed) printf '{ invalid JSON' > "$FIXTURES/release.json"; return ;;
        prerelease) printf '{"tag_name":"%s","draft":false,"prerelease":true,"assets":[]}' "$tag" > "$FIXTURES/release.json"; return ;;
    esac
    local digest="sha256:$(sha256sum "$FIXTURES/archive.tar.gz" | awk '{print $1}')"
    [[ $mode == wrong-checksum-name ]] && CHECKSUM_NAME="$ARCHIVE_NAME.sha256sum"
    cat > "$FIXTURES/release.json" <<JSON
{"tag_name":"$tag","draft":false,"prerelease":false,"assets":[{"name":"$ARCHIVE_NAME","browser_download_url":"https://github.com/zellij-org/zellij/releases/download/$tag/$ARCHIVE_NAME","digest":"$digest"},{"name":"$CHECKSUM_NAME","browser_download_url":"https://github.com/zellij-org/zellij/releases/download/$tag/$CHECKSUM_NAME"}]}
JSON
}
run_installer() {
    local home=$1; shift; mkdir -p "$home"
    MOCK_CURL_LOG="$TMP_ROOT/curl.log" MOCK_RELEASE_JSON="$FIXTURES/release.json" MOCK_ARCHIVE="$FIXTURES/archive.tar.gz" MOCK_CHECKSUM="$FIXTURES/checksum.sha256sum" \
    MOCK_ARCH="${TEST_ARCH:-x86_64}" MOCK_UID="${TEST_UID:-$(id -u)}" HOME="$home" PATH="${TEST_PATH:-$MOCK_BIN:/usr/bin:/bin}" \
    CURL_CMD="$MOCK_BIN/curl" UNAME_CMD="$MOCK_BIN/uname" ID_CMD="$MOCK_BIN/id" OS_RELEASE_FILE="$OS_RELEASE" "$INSTALLER" "$@"
}
curl_count() { [[ -f $TMP_ROOT/curl.log ]] && wc -l < "$TMP_ROOT/curl.log" | tr -d ' ' || printf '0\n'; }
asset_download_count() { grep -Ec '\|(.*)$' "$TMP_ROOT/curl.log" >/dev/null 2>&1 || true; grep -Ec 'releases/download/' "$TMP_ROOT/curl.log" || true; }
reset_log() { : > "$TMP_ROOT/curl.log"; }

# Numeric Ubuntu versions accept 24.10 and reject 23.10 before API access.
make_os_release 24.10; reset_log; TEST_ARCH=x86_64 make_assets v1.2.3; TEST_ARCH=x86_64 make_release v1.2.3
VERSION_HOME="$TMP_ROOT/version-home"
TEST_ARCH=x86_64 run_installer "$VERSION_HOME" --check > "$TMP_ROOT/2410.out"
assert_contains "$TMP_ROOT/2410.out" 'Platform: Ubuntu 24.10' 'Ubuntu 24.10 accepted'
make_os_release 23.10; reset_log
if TEST_ARCH=x86_64 run_installer "$TMP_ROOT/old-home" --check > "$TMP_ROOT/2310.out" 2>&1; then fail 'Ubuntu 23.10 unexpectedly accepted'
else assert_contains "$TMP_ROOT/2310.out" 'unsupported Ubuntu version: 23.10' 'Ubuntu 23.10 rejected'; fi
assert_eq "$(curl_count)" 0 'unsupported version avoids API'
pass 'numeric Ubuntu version policy is correct'

# Restore supported version; check remains path-write-free.
make_os_release 24.04; reset_log; TEST_ARCH=x86_64 make_assets v1.2.3; TEST_ARCH=x86_64 make_release v1.2.3
CHECK_HOME="$TMP_ROOT/check-home"
TEST_ARCH=x86_64 TEST_PATH="$MOCK_BIN:/usr/bin:/bin" run_installer "$CHECK_HOME" --check > "$TMP_ROOT/check.out"
assert_not_exists "$CHECK_HOME/.local"; assert_contains "$TMP_ROOT/check.out" 'PATH warning:' 'PATH warning'; assert_eq "$(curl_count)" 1 'check queries API'
PATH_HOME="$TMP_ROOT/path-home"
TEST_ARCH=x86_64 TEST_PATH="$PATH_HOME/.local/bin:$MOCK_BIN:/usr/bin:/bin" run_installer "$PATH_HOME" --check > "$TMP_ROOT/path-present.out"
assert_contains "$TMP_ROOT/path-present.out" "PATH ($PATH_HOME/.local/bin): present" 'non-final PATH component present'
if grep -Fq 'PATH warning:' "$TMP_ROOT/path-present.out"; then fail 'PATH warning emitted for non-final PATH component'; fi
pass 'check is read-only and recognizes PATH component anywhere'

# amd64 fresh install includes locally managed binary digest sidecar.
reset_log; HOME_ONE="$TMP_ROOT/home-one"; TEST_ARCH=x86_64 make_assets v1.2.3; TEST_ARCH=x86_64 make_release v1.2.3
TEST_ARCH=x86_64 GITHUB_TOKEN=token-one GH_TOKEN=token-two run_installer "$HOME_ONE" > "$TMP_ROOT/fresh.out"
assert_file "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij"; assert_file "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij.sha256"
assert_eq "$(readlink "$HOME_ONE/.local/bin/zellij")" "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij" 'fresh target'
assert_contains "$TMP_ROOT/curl.log" 'https://api.github.com/repos/zellij-org/zellij/releases/latest|Authorization: Bearer token-one' 'GITHUB_TOKEN API auth'
if grep 'releases/download/' "$TMP_ROOT/curl.log" | grep -q 'Authorization:'; then fail 'asset request received API token'; fi
pass 'fresh install writes verified sidecar and token stays API-only'

# Same version skips artifacts only while sidecar verifies binary.
reset_log; TEST_ARCH=x86_64 make_release v1.2.3
TEST_ARCH=x86_64 run_installer "$HOME_ONE" > "$TMP_ROOT/rerun.out"
assert_eq "$(curl_count)" 1 'same version queries API'; [[ $(grep -c 'releases/download/' "$TMP_ROOT/curl.log" || true) == 0 ]] || fail 'same version redownloaded artifacts'
pass 'valid sidecar permits no-redownload rerun'

# Tampering or missing sidecar blocks the selected target without execution/overwrite.
printf 'corruption' >> "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij"; reset_log; TEST_ARCH=x86_64 make_release v1.2.3
if TEST_ARCH=x86_64 run_installer "$HOME_ONE" > "$TMP_ROOT/corrupt.out" 2>&1; then fail 'corrupted same target unexpectedly succeeded'
else assert_contains "$TMP_ROOT/corrupt.out" 'locally unverified existing latest target' 'corruption blocked'; fi
rm -f "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij"; cp "$TMP_ROOT/payload/zellij" "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij"; chmod 755 "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij"; rm "$HOME_ONE/.local/opt/zellij/v1.2.3/zellij.sha256"
if TEST_ARCH=x86_64 run_installer "$HOME_ONE" > "$TMP_ROOT/missing-sidecar.out" 2>&1; then fail 'missing sidecar unexpectedly succeeded'
else assert_contains "$TMP_ROOT/missing-sidecar.out" 'locally unverified existing latest target' 'missing sidecar blocked'; fi
pass 'corruption and missing sidecar are blocked'

# Symlinked .local is rejected before API/link traversal in check and normal mode.
reset_log; SYMLINK_HOME="$TMP_ROOT/symlink-home"; mkdir -p "$TMP_ROOT/outside-local" "$SYMLINK_HOME"; ln -s "$TMP_ROOT/outside-local" "$SYMLINK_HOME/.local"
if TEST_ARCH=x86_64 run_installer "$SYMLINK_HOME" --check > "$TMP_ROOT/local-check.out" 2>&1; then fail 'symlinked .local check unexpectedly succeeded'
else assert_contains "$TMP_ROOT/local-check.out" 'refusing unsafe directory path' 'check symlink rejection'; fi
assert_eq "$(curl_count)" 0 'symlinked .local check avoids API'
if TEST_ARCH=x86_64 run_installer "$SYMLINK_HOME" > "$TMP_ROOT/local-normal.out" 2>&1; then fail 'symlinked .local normal unexpectedly succeeded'
else assert_contains "$TMP_ROOT/local-normal.out" 'refusing unsafe directory path' 'normal symlink rejection'; fi
assert_not_exists "$TMP_ROOT/outside-local/opt/zellij"
pass 'symlinked local ancestor cannot redirect check or install'

# arm64 mapping still follows real checksum stem.
reset_log; ARM_HOME="$TMP_ROOT/arm-home"; TEST_ARCH=aarch64 make_assets v1.2.4; TEST_ARCH=aarch64 make_release v1.2.4
TEST_ARCH=aarch64 run_installer "$ARM_HOME" --check > "$TMP_ROOT/arm.out"
assert_contains "$TMP_ROOT/arm.out" 'zellij-aarch64-unknown-linux-musl.tar.gz' 'arm archive'; assert_contains "$TMP_ROOT/arm.out" 'zellij-aarch64-unknown-linux-musl.sha256sum' 'arm checksum'
pass 'arm64 target mapping remains correct'

# Existing integrity failures still preserve prior active target (fresh isolated setup).
reset_log; SAFE_HOME="$TMP_ROOT/safe-home"; TEST_ARCH=x86_64 make_assets v1.2.4; TEST_ARCH=x86_64 make_release v1.2.4; TEST_ARCH=x86_64 run_installer "$SAFE_HOME" >/dev/null
TEST_ARCH=x86_64 make_assets v1.2.5 bad-binary-checksum; TEST_ARCH=x86_64 make_release v1.2.5
if TEST_ARCH=x86_64 run_installer "$SAFE_HOME" > "$TMP_ROOT/badchecksum.out" 2>&1; then fail 'bad checksum succeeded'
else assert_contains "$TMP_ROOT/badchecksum.out" 'extracted zellij binary' 'binary checksum failure'; fi
assert_eq "$(readlink "$SAFE_HOME/.local/bin/zellij")" "$SAFE_HOME/.local/opt/zellij/v1.2.4/zellij" 'active target preserved'
pass 'failed update preserves active release'

# Normal runs serialize before latest API resolution; --check never creates/locks.
reset_log; LOCK_HOME="$TMP_ROOT/lock-home"; mkdir -p "$LOCK_HOME/.local/opt/zellij"
flock "$LOCK_HOME/.local/opt/zellij/.install.lock" sleep 3 & LOCK_PID=$!
sleep 0.1
TEST_ARCH=x86_64 make_assets v1.2.6; TEST_ARCH=x86_64 make_release v1.2.6
if TEST_ARCH=x86_64 run_installer "$LOCK_HOME" > "$TMP_ROOT/lock.out" 2>&1; then fail 'lock contention unexpectedly succeeded'
else assert_contains "$TMP_ROOT/lock.out" 'another Zellij installer is already running' 'lock contention error'; fi
assert_eq "$(curl_count)" 0 'contended installer avoids latest API'
wait "$LOCK_PID"
pass 'normal installer lock serializes latest resolution'

# Root rejection remains before network.
reset_log
if TEST_UID=0 TEST_ARCH=x86_64 run_installer "$TMP_ROOT/root-home" > "$TMP_ROOT/root.out" 2>&1; then fail 'root invocation succeeded'
else assert_contains "$TMP_ROOT/root.out" 'do not run this installer as root' 'root rejection'; fi
assert_eq "$(curl_count)" 0 'root avoids API'
pass 'root execution is rejected'

if ((FAIL)); then printf '\n%d assertion(s) failed; %d groups passed.\n' "$FAIL" "$PASS" >&2; exit 1; fi
printf '\nAll %d isolated Zellij installer test groups passed.\n' "$PASS"
