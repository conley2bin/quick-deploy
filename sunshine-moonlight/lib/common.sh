# shellcheck shell=bash
# Shared pins and small helpers for the standalone Sunshine/Moonlight workflow.

QD_SUNSHINE_VERSION='2026.906.222525'
QD_SUNSHINE_FLOOR="$QD_SUNSHINE_VERSION"  # September 2026 upstream security fixes
QD_MOONLIGHT_VERSION='6.1.0'
QD_MOONLIGHT_FLOOR="$QD_MOONLIGHT_VERSION"
QD_MOONLIGHT_SHA256='0e855ffd22d407e18ab5fdb575fed5f01ca119a3f91993c5f0213f15ac80b400'
QD_MOONLIGHT_SIZE='55325888'
QD_CANONICAL_UNIT='app-dev.lizardbyte.app.Sunshine.service'
QD_ALIAS_UNIT='sunshine.service'
QD_BASE_PORT=47989

qd_info()    { printf '%s\n' "$*"; }
qd_warn()    { printf '警告: %s\n' "$*" >&2; }
qd_die()     { printf '错误: %s\n' "$*" >&2; exit 1; }
qd_section() { printf '\n========== %s ==========\n' "$*"; }
qd_require_not_root() {
    [ "$(id -u)" -ne 0 ] || qd_die '请不要用 root/sudo 运行；需要管理员权限的步骤会调用 sudo。'
}

# QD_* path/checksum overrides are only for isolated tests, never deployment settings.
QD_OS_RELEASE_FILE="${QD_OS_RELEASE_FILE:-/etc/os-release}"
qd_require_ubuntu() {
    [ -r "$QD_OS_RELEASE_FILE" ] || qd_die "无法读取 $QD_OS_RELEASE_FILE"
    # shellcheck disable=SC1090
    . "$QD_OS_RELEASE_FILE"
    [ "${ID:-}" = ubuntu ] && dpkg --compare-versions "${VERSION_ID:-0}" ge 24.04 \
        || qd_die '仅支持 Ubuntu 24.04 及更高版本'
    qd_info "系统: ${PRETTY_NAME:-Ubuntu $VERSION_ID}"
}
qd_require_cmd() {
    command -v "$1" >/dev/null 2>&1 || qd_die "缺少命令 $1；请先安装 ${2:-$1}。"
}
qd_sudo() { sudo "$@"; }

QD_TEMP_FILES=()
QD_TEMP_DIRS=()
qd_cleanup() {
    local f d
    for f in "${QD_TEMP_FILES[@]}"; do rm -f -- "$f"; done
    for d in "${QD_TEMP_DIRS[@]}"; do rm -rf -- "$d"; done
}
trap qd_cleanup EXIT

# Output-variable calls keep cleanup registration in the parent shell.
qd_mktemp_file() {
    local __qd_var="$1"; shift
    local f
    f="$(mktemp "$@")" || qd_die '无法创建临时文件'
    QD_TEMP_FILES+=("$f")
    printf -v "$__qd_var" '%s' "$f"
}
qd_mktemp_dir() {
    local __qd_var="$1"; shift
    local d
    d="$(mktemp -d "$@")" || qd_die '无法创建临时目录'
    QD_TEMP_DIRS+=("$d")
    printf -v "$__qd_var" '%s' "$d"
}

qd_valid_ipv4() {
    local ip="$1" octet
    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    local -a octets
    IFS='.' read -ra octets <<<"$ip"
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^(0|[1-9][0-9]{0,2})$ ]] && ((10#$octet <= 255)) || return 1
    done
}

qd_tailnet_ip() {
    local ip addresses
    ip="$(tailscale ip -4 2>/dev/null)" || { qd_warn '无法读取 Tailscale IPv4；请先加入 Tailnet'; return 1; }
    qd_valid_ipv4 "$ip" || { qd_warn 'Tailscale 未返回单个标准 IPv4 地址'; return 1; }
    local first second rest
    IFS=. read -r first second rest <<<"$ip"
    [ "$first" = 100 ] && ((second >= 64 && second <= 127)) \
        || { qd_warn "$ip 不在 Tailscale IPv4 地址范围 100.64.0.0/10"; return 1; }
    # Require both a running Tailscale identity and a kernel-assigned tunnel address.
    tailscale status --json 2>/dev/null | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("BackendState") != "Running")' \
        || { qd_warn 'Tailscale 未处于 Running 状态'; return 1; }
    addresses="$(ip -o -4 address show dev tailscale0 2>/dev/null)" || {
        qd_warn '找不到 tailscale0；本流程要求内核网络模式下已分配的 Tailnet IPv4'; return 1;
    }
    awk '$3 == "inet" {split($4, a, "/"); print a[1]}' <<<"$addresses" | grep -Fxq "$ip" \
        || { qd_warn "$ip 未分配给本机 tailscale0"; return 1; }
    printf '%s\n' "$ip"
}

# Asset bytes are public and may use GitHub/CDN redirects and bounded retries. Release
# metadata deliberately uses qd_github_release_fetch instead: one request, no redirect.
qd_curl() { curl -fL --retry 3 --connect-timeout 20 "$@"; }

qd_valid_release_tag() { [[ "$1" =~ ^v[0-9]+(\.[0-9]+)+$ ]]; }
qd_require_release_floor() {
    local tag="$1" floor="$2" component="$3"
    qd_valid_release_tag "$tag" || qd_die "$component release 标签格式无效: $tag"
    qd_version_ge "$tag" "$floor" || qd_die "$component release $tag 低于维护基线 v$floor"
}

_qd_http_header() {
    local headers="$1" wanted="${2,,}"
    awk -v wanted="$wanted" 'BEGIN { IGNORECASE=1 }
        { sub(/\r$/, ""); n=index($0, ":"); if (n) {
            key=tolower(substr($0, 1, n - 1)); if (key == wanted) {
                value=substr($0, n + 1); sub(/^[ \t]+/, "", value); print value; exit
            }
        }}' "$headers"
}

# Select without a precedence fallback: two different exported credentials make
# the chosen GitHub identity ambiguous. The caller never prints this variable.
qd_github_select_auth() {
    local github="${GITHUB_TOKEN:-}" gh="${GH_TOKEN:-}"
    if [[ "$github" == *$'\n'* || "$github" == *$'\r'* || "$gh" == *$'\n'* || "$gh" == *$'\r'* ]]; then
        qd_die 'GitHub token 含有换行符，拒绝构造认证请求'
    fi
    if [ -n "$github" ] && [ -n "$gh" ] && [ "$github" != "$gh" ]; then
        qd_die 'GITHUB_TOKEN 与 GH_TOKEN 同时设置但值不同；请只保留一个凭据'
    fi
    QD_GITHUB_TOKEN="${github:-$gh}"
    QD_GITHUB_AUTH_SUPPLIED=false
    [ -z "$QD_GITHUB_TOKEN" ] || QD_GITHUB_AUTH_SUPPLIED=true
}

_qd_github_report_failure() {
    local repo="$1" selector="$2" curl_rc="$3" status="$4" headers="$5"
    local remaining reset retry_after formatted
    remaining="$(_qd_http_header "$headers" x-ratelimit-remaining || true)"
    reset="$(_qd_http_header "$headers" x-ratelimit-reset || true)"
    retry_after="$(_qd_http_header "$headers" retry-after || true)"
    case "$status" in
        401)
            if [ "$QD_GITHUB_AUTH_SUPPLIED" = true ]; then qd_warn "GitHub API $repo/$selector: HTTP 401，提供的认证凭据被拒绝"
            else qd_warn "GitHub API $repo/$selector: HTTP 401，未认证请求被拒绝"; fi;;
        403)
            if [ "$remaining" = 0 ]; then
                if [[ "$reset" =~ ^[0-9]+$ ]]; then formatted="$(date -d "@$reset" --iso-8601=seconds 2>/dev/null || true)"; else formatted='不可用'; fi
                qd_warn "GitHub API $repo/$selector: HTTP 403，rate limit remaining=0，reset epoch=$reset，时间=$formatted"
                [ "$QD_GITHUB_AUTH_SUPPLIED" = true ] || qd_warn '未认证 GitHub API 默认每个公共出口 IP 每小时 60 次请求'
            else qd_warn "GitHub API $repo/$selector: HTTP 403，访问被禁止（remaining=${remaining:-不可用}）"; fi;;
        404) qd_warn "GitHub API $repo/$selector: HTTP 404，${selector#tags/} release 不存在或不可访问";;
        429) qd_warn "GitHub API $repo/$selector: HTTP 429，已限流（remaining=${remaining:-不可用}，reset=${reset:-不可用}，Retry-After=${retry_after:-不可用}）";;
        *) qd_warn "GitHub API $repo/$selector: curl exit $curl_rc，HTTP ${status:-000}";;
    esac
}

# Fetch exactly one API metadata document. Auth is supplied only via an in-memory
# curl config on stdin; curl's child environment has both conventional token names
# removed and asset downloads never call this helper.
qd_github_release_fetch() {
    # Inspect and suppress xtrace before touching either token variable. Function
    # arguments are public repository metadata; token selection/config never is.
    local trace=false
    [[ $- == *x* ]] && trace=true
    [ "$trace" = true ] && set +x

    local repo="$1" selector="$2" __out_var="$3" body headers stderr_file status_file status curl_rc=0
    case "$selector" in latest|tags/*) ;; *) qd_die "内部错误：无效 GitHub release selector: $selector";; esac
    qd_require_cmd curl curl
    qd_require_cmd python3 python3
    qd_github_select_auth
    qd_mktemp_file body
    qd_mktemp_file headers
    qd_mktemp_file stderr_file
    qd_mktemp_file status_file
    {
        printf '%s\n' 'header = "User-Agent: quick-deploy-sunshine-moonlight/1"'
        printf '%s\n' 'header = "Accept: application/vnd.github+json"'
        printf '%s\n' 'header = "X-GitHub-Api-Version: 2022-11-28"'
        if [ -n "$QD_GITHUB_TOKEN" ]; then
            local escaped="${QD_GITHUB_TOKEN//\\/\\\\}"
            escaped="${escaped//\"/\\\"}"
            printf 'header = "Authorization: Bearer %s"\n' "$escaped"
        fi
    } | env -u GITHUB_TOKEN -u GH_TOKEN curl --disable --config - --silent --show-error --connect-timeout 20 \
        -D "$headers" -o "$body" -w '%{http_code}' \
        "https://api.github.com/repos/$repo/releases/$selector" >"$status_file" 2>"$stderr_file" || curl_rc=${PIPESTATUS[1]}
    QD_GITHUB_TOKEN=''
    status="$(cat "$status_file")"
    [[ "$status" =~ ^[0-9]{3}$ ]] || status=000
    if [ "$curl_rc" -ne 0 ] || [ "$status" != 200 ]; then
        _qd_github_report_failure "$repo" "$selector" "$curl_rc" "$status" "$headers"
        [ "$trace" = true ] && set -x
        return 1
    fi
    if ! python3 - "$body" <<'PY_JSON'; then
import json, sys
with open(sys.argv[1], encoding='utf-8') as fh:
    value = json.load(fh)
if not isinstance(value, dict):
    raise SystemExit(1)
PY_JSON
        qd_warn "GitHub API $repo/$selector: HTTP 200 但响应不是 JSON object"
        [ "$trace" = true ] && set -x
        return 1
    fi
    printf -v "$__out_var" '%s' "$body"
    [ "$trace" = true ] && set -x
    return 0
}

# Wrapper, not version-directory enumeration, is the active Moonlight source of
# truth. Return 0 for absent/active, 2 for a foreign wrapper, 3 for malformed
# managed state. OUT_VERSION and OUT_TARGET are populated only for active.
qd_client_active_version() {
    local opt="$1" wrapper="$2" __version_var="$3" __target_var="$4" line _version _target marker
    printf -v "$__version_var" '%s' ''
    printf -v "$__target_var" '%s' ''
    [ -e "$wrapper" ] || [ -L "$wrapper" ] || return 0
    [ -f "$wrapper" ] || return 2
    grep -Fqx '# Managed by quick-deploy/sunshine-moonlight/install-client.sh' "$wrapper" 2>/dev/null || return 2
    line="$(grep -E '^exec "[^"]+/AppRun" "\$@"$' "$wrapper" 2>/dev/null || true)"
    [ "$(printf '%s\n' "$line" | wc -l)" -eq 1 ] && [ -n "$line" ] || return 3
    _target="${line#exec \"}"; _target="${_target%/AppRun\" \"\$@\"}"
    case "$_target" in "$opt"/*) ;; *) return 3;; esac
    _version="${_target#"$opt/"}"
    qd_valid_release_tag "v$_version" || return 3
    cmp -s "$wrapper" <(printf '#!/bin/sh\n# Managed by quick-deploy/sunshine-moonlight/install-client.sh\nexec "%s/AppRun" "$@"\n' "$_target") || return 3
    marker="$_target/.quick-deploy-sha256"
    [ -f "$marker" ] || return 3
    printf -v "$__version_var" '%s' "$_version"
    printf -v "$__target_var" '%s' "$_target"
}

qd_verify_sha256() {
    local file="$1" expected="$2" actual
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || qd_die '来源没有有效 SHA-256 摘要'
    actual="$(sha256sum "$file" | awk '{print $1}')"
    [ "$actual" = "$expected" ] || qd_die "SHA-256 摘要不匹配；拒绝使用下载文件（期望 $expected，实际 $actual）"
    qd_info "SHA-256 校验通过: $actual"
}
qd_version_ge() { dpkg --compare-versions "${1#v}" ge "${2#v}"; }

# Debian epoch and revision do not change which upstream fixes are present.
qd_upstream_version() {
    local version="$1"
    if [[ "$version" == *:* ]]; then
        [[ "${version%%:*}" =~ ^[0-9]+$ ]] || return 1
        version="${version#*:}"
    fi
    version="${version%-*}"
    [[ "$version" =~ ^[0-9]+(\.[0-9]+)*$ ]] || return 1
    printf '%s\n' "$version"
}
qd_valid_capture() {
    case "$1" in ''|kms|portal|x11|nvfbc|wlr|kwin) return 0;; *) return 1;; esac
}

# The two binding scalars share the guard's native parser; other legacy helpers
# below retain their existing behavior (this is not a general config parser).
_qd_conf_begin_write() {
    local file="$1"
    __QD_CONF_MODE=''
    [ ! -f "$file" ] || __QD_CONF_MODE="$(stat -c %a "$file")"
    qd_mktemp_file __QD_CONF_TMP "$file.qdtmp.XXXXXX"
}
_qd_conf_finish_write() {
    local file="$1"
    if [ -f "$file" ] && cmp -s "$file" "$__QD_CONF_TMP"; then
        rm -f "$__QD_CONF_TMP"
        return 0
    fi
    [ -z "$__QD_CONF_MODE" ] || chmod "$__QD_CONF_MODE" "$__QD_CONF_TMP"
    mv -f -- "$__QD_CONF_TMP" "$file"
}
_qd_conf_read() { if [ -f "$1" ]; then cat -- "$1"; fi; }
qd_conf_get() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 1
    case "$key" in address_family|bind_address)
        python3 "$QD_SERVICE_SOURCE/check-tailnet.py" --binding-get "$file" "$key"
        return $?;;
    esac
    awk -v key="$key" '
        { line=$0; sub(/#.*/, "", line); sub(/^[ \t]+/, "", line)
          if (line ~ ("^" key "[ \t]*=")) {
              sub(("^" key "[ \t]*=[ \t]*"), "", line)
              sub(/[ \t\r]+$/, "", line); print line; found=1; exit
          }
        } END { if (!found) exit 1 }
    ' "$file"
}
qd_conf_set() {
    local file="$1" key="$2" value="$3"
    _qd_conf_begin_write "$file"
    case "$key" in address_family|bind_address)
        python3 "$QD_SERVICE_SOURCE/check-tailnet.py" --binding-set "$file" "$key" "$value" >"$__QD_CONF_TMP" || {
            rm -f "$__QD_CONF_TMP"; return 1;
        }
        _qd_conf_finish_write "$file"
        return $?;;
    esac
    _qd_conf_read "$file" | awk -v key="$key" -v value="$value" '
        { line=$0; stripped=line; sub(/^[ \t]+/, "", stripped)
          if (stripped ~ ("^" key "[ \t]*=")) {
              comment=""; p=index(line,"#"); if (p) comment=substr(line,p)
              if (!done) { print key " = " value (comment == "" ? "" : " " comment); done=1 }
              else if (comment != "") print comment
              next
          } print line
        } END { if (!done) print key " = " value }
    ' >"$__QD_CONF_TMP"
    _qd_conf_finish_write "$file"
}
qd_conf_unset() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 0
    _qd_conf_begin_write "$file"
    awk -v key="$key" '
        { stripped=$0; sub(/^[ \t]+/, "", stripped)
          if (stripped ~ ("^" key "[ \t]*=")) {
              p=index($0,"#"); if (p) print substr($0,p); next
          } print
        }
    ' "$file" >"$__QD_CONF_TMP"
    _qd_conf_finish_write "$file"
}
qd_conf_ensure_token() {
    local file="$1" key="$2" token="$3" value
    value="$(qd_conf_get "$file" "$key" || true)"
    if printf '%s\n' "$value" | tr ',' '\n' | sed 's/^[ \t]*//;s/[ \t]*$//' | grep -Fxq "$token"; then
        return 0
    fi
    qd_conf_set "$file" "$key" "${value:+$value, }$token"
}
qd_base_port() {
    local base
    base="$(qd_conf_get "$1" port || true)"
    base="${base:-$QD_BASE_PORT}"
    [[ "$base" =~ ^[1-9][0-9]{3,4}$ ]] && ((base >= 1029 && base <= 65514)) \
        || { qd_warn 'port 必须为 1029–65514 的十进制整数（不带前导零）'; return 1; }
    printf '%s\n' "$base"
}

# Matches Sunshine appdata(): CONFIGURATION_DIRECTORY, then XDG_CONFIG_HOME, then HOME.
# Keep the selected path available for destructive callers before resolving symlinks.
qd_host_config_path() {
    local root="${1-${CONFIGURATION_DIRECTORY:-}}" xdg="${2-${XDG_CONFIG_HOME:-}}" dir
    [[ "$root" != *:* ]] || { qd_warn '不支持多目录 CONFIGURATION_DIRECTORY'; return 1; }
    dir="${QD_SUNSHINE_CONFIG_DIR:-${root:-${xdg:-$HOME/.config}}/sunshine}"
    [[ "$dir" = /* && "$dir" != *$'\n'* ]] || { qd_warn 'Sunshine 配置目录必须为绝对单行路径'; return 1; }
    printf '%s\n' "$dir"
}
qd_host_config_dir() {
    local dir
    dir="$(qd_host_config_path "$@")" || return 1
    realpath -m -- "$dir"
}
qd_unit_property() { systemctl --user show "$1" -p "$2" --value; }
qd_find_unit() {
    local unit
    for unit in "$QD_CANONICAL_UNIT" "$QD_ALIAS_UNIT"; do
        if [ "$(qd_unit_property "$unit" LoadState 2>/dev/null)" = loaded ]; then
            printf '%s\n' "$unit"; return 0
        fi
    done
    return 1
}

# The only supported override is our byte-verified retry policy. No runtime library
# is installed: the one-shot guard uses only Python's standard library and ip.
QD_SERVICE_SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../service" && pwd)"
qd_retry_dir() { printf '%s/systemd/user/%s.d\n' "${XDG_CONFIG_HOME:-$HOME/.config}" "$QD_CANONICAL_UNIT"; }
qd_retry_content() {
    python3 - "$(qd_retry_dir)/check-tailnet.py" "$1" <<'PY'
import sys

def quote(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

print('''# quick-deploy/sunshine-moonlight: managed retry policy
[Unit]
StartLimitIntervalSec=0
PartOf=graphical-session.target

[Service]
Restart=on-failure
RestartSec=5s''')
# ':' disables environment expansion, '%%' escapes unit specifiers in paths.
print('ExecStartPre=:/usr/bin/python3 ' + ' '.join(map(quote, sys.argv[1:])))
PY
}
qd_check_retry_files() {
    local wanted="$1" dir file
    dir="$(qd_retry_dir)"
    [ ! -L "$dir" ] || { qd_warn "拒绝符号链接 retry 目录: $dir"; return 1; }
    [ ! -e "$dir" ] || [ -d "$dir" ] || { qd_warn "retry 目录被外来文件占用: $dir"; return 1; }
    # Reject overrides not yet seen by daemon-reload; non-.conf siblings are unowned.
    for file in "$dir"/*.conf "${dir%/*}/$QD_ALIAS_UNIT.d"/*.conf; do
        [ -e "$file" ] || [ -L "$file" ] || continue
        [ "$file" = "$dir/quick-deploy-retry.conf" ] || { qd_warn "拒绝额外 Sunshine drop-in: $file"; return 1; }
    done
    for file in "$dir/check-tailnet.py" "$dir/quick-deploy-retry.conf"; do
        [ -e "$file" ] || [ -L "$file" ] || continue
        [ -f "$file" ] && [ ! -L "$file" ] || { qd_warn "拒绝外来 retry 文件: $file"; return 1; }
        if [[ "$file" = *.py ]]; then
            cmp -s "$file" "$QD_SERVICE_SOURCE/check-tailnet.py" || { qd_warn "retry guard 已修改/非本流程文件: $file"; return 1; }
        else
            cmp -s "$file" <(qd_retry_content "$wanted") || { qd_warn "retry drop-in 已修改/配置目录不符: $file"; return 1; }
        fi
    done
}
qd_check_retry_effective() {
    local unit="$1" drops="$2" wanted="$3" dir actual
    dir="$(qd_retry_dir)"
    [ -f "$dir/check-tailnet.py" ] && [ -f "$dir/quick-deploy-retry.conf" ] || {
        qd_warn 'retry 策略文件不完整；请重跑安装器'; return 1;
    }
    python3 - "$drops" "$dir/quick-deploy-retry.conf" <<'PY' || return 1
from pathlib import Path
import shlex, sys
try:
    paths = shlex.split(sys.argv[1])
    owned = Path(sys.argv[2])
    loaded = Path(paths[0]) if len(paths) == 1 else None
    # systemd 255 resolves HOME/XDG parent links in DropInPaths. Match the
    # canonical pathname, not merely an inode (foreign hardlinks stay foreign).
    # Leaf names and leaf file/directory symlink refusals remain exact.
    matches = (loaded is not None and loaded.is_absolute()
               and loaded.name == owned.name
               and not loaded.is_symlink() and not loaded.parent.is_symlink()
               and loaded.resolve(strict=True) == owned.resolve(strict=True))
except (OSError, ValueError, RuntimeError):
    matches = False
if not matches:
    print('警告: 拒绝未知/额外 Sunshine drop-in: ' + sys.argv[1], file=sys.stderr)
    sys.exit(1)
PY
    local property expected
    for property in Restart RestartUSec StartLimitIntervalUSec PartOf; do
        case "$property" in
            Restart) expected=on-failure;; RestartUSec) expected=5s;;
            StartLimitIntervalUSec) expected=0;; PartOf) expected=graphical-session.target;;
        esac
        actual="$(qd_unit_property "$unit" "$property")" || return 1
        [ "$actual" = "$expected" ] || { qd_warn "retry 有效 $property=$actual，期望 $expected；请核对/reload"; return 1; }
    done
    actual="$(qd_unit_property "$unit" ExecStartPre)" || return 1
    python3 - "$actual" "$dir/check-tailnet.py" "$wanted" <<'PY'
import re, sys
# Ignore runtime pid/timestamp/status fields, not the executable/argv or '-' prefix.
commands = re.findall(r'\{ path=(.*?) ; argv\[\]=(.*?) ; ignore_errors=(yes|no) ;', sys.argv[1])
expected = [('/bin/sleep', '/bin/sleep 5', 'no'),
            ('/usr/bin/python3', '/usr/bin/python3 ' + sys.argv[2] + ' ' + sys.argv[3], 'no')]
if commands != expected:
    print('警告: retry 有效 ExecStartPre 不符（须保留 vendor sleep 5 和受管 guard）', file=sys.stderr)
    sys.exit(1)
PY
}
qd_install_retry() {
    local wanted="$1" dir staged file
    qd_check_retry_files "$wanted" || qd_die 'retry 文件冲突，未覆盖'
    dir="$(qd_retry_dir)"
    mkdir -p "$dir"
    for file in check-tailnet.py quick-deploy-retry.conf; do
        [ ! -f "$dir/$file" ] || continue
        qd_mktemp_file staged "$dir/.qd-retry.XXXXXX"
        if [[ "$file" = *.py ]]; then cp "$QD_SERVICE_SOURCE/check-tailnet.py" "$staged";
        else qd_retry_content "$wanted" >"$staged"; fi
        chmod 644 "$staged"
        # link(2) is atomic and refuses a collision appearing after the check.
        ln -T -- "$staged" "$dir/$file" || qd_die "retry 文件创建冲突: $dir/$file"
        rm -f "$staged"
        SERVICE_CHANGED=true
    done
}
qd_remove_retry() {
    local wanted="$1" dir
    qd_check_retry_files "$wanted" || qd_die 'retry 文件已修改，保留；未删除'
    dir="$(qd_retry_dir)"
    if [ -f "$dir/quick-deploy-retry.conf" ] || [ -f "$dir/check-tailnet.py" ]; then
        rm -f "$dir/quick-deploy-retry.conf" "$dir/check-tailnet.py"
        rmdir "$dir" 2>/dev/null || true
        systemctl --user daemon-reload || qd_die '移除 retry 策略后 reload 失败'
    fi
}

# Refuse arbitrary execution overrides; accept only our exact retry policy.
# allow-pending is installer-only, for repairing an interrupted write/reload.
qd_check_service_config() {
    local wanted="$1" pending="${2:-}" env root xdg actual unit load fragment drops execstart settings
    qd_check_retry_files "$wanted" || return 1
    env="$(systemctl --user show-environment)" || { qd_warn '无法连接 systemd 用户管理器'; return 1; }
    root="$(sed -n 's/^CONFIGURATION_DIRECTORY=//p' <<<"$env")"
    xdg="$(sed -n 's/^XDG_CONFIG_HOME=//p' <<<"$env")"
    [ "$(realpath -m "${XDG_CONFIG_HOME:-$HOME/.config}")" = "$(realpath -m "${xdg:-$HOME/.config}")" ] || {
        qd_warn 'shell 与用户管理器的 systemd 配置目录不一致；请统一 XDG_CONFIG_HOME'; return 1;
    }
    actual="$(qd_host_config_dir "$root" "$xdg")" || return 1
    [ "$wanted" = "$actual" ] || {
        qd_warn "当前 shell 配置目录 $wanted 与用户服务目录 $actual 不一致；请在同一图形登录环境运行并统一 XDG_CONFIG_HOME/CONFIGURATION_DIRECTORY"; return 1;
    }
    for unit in "$QD_CANONICAL_UNIT" "$QD_ALIAS_UNIT"; do
        load="$(qd_unit_property "$unit" LoadState)" || return 1
        [ "$load" != not-found ] || continue
        [ "$load" = loaded ] || { qd_warn "$unit 的 LoadState=$load；先处理 masked/error 状态"; return 1; }
        fragment="$(qd_unit_property "$unit" FragmentPath)" || return 1
        drops="$(qd_unit_property "$unit" DropInPaths)" || return 1
        case "$fragment" in
            /usr/lib/systemd/user/"$QD_CANONICAL_UNIT"|/lib/systemd/user/"$QD_CANONICAL_UNIT"|/usr/lib/systemd/user/"$QD_ALIAS_UNIT"|/lib/systemd/user/"$QD_ALIAS_UNIT") ;;
            *) qd_warn "拒绝修改自定义 Sunshine 单元: $fragment；请先核对其 ExecStart/配置路径"; return 1;;
        esac
        if [ -n "$drops" ]; then
            qd_check_retry_effective "$unit" "$drops" "$wanted" || return 1
        elif [ -f "$(qd_retry_dir)/quick-deploy-retry.conf" ] && [ "$pending" != allow-pending ]; then
            qd_warn '受管 retry drop-in 尚未加载；请重跑安装器'; return 1
        fi
        execstart="$(qd_unit_property "$unit" ExecStart)" || return 1
        [[ "$execstart" == *'argv[]=/usr/bin/sunshine ;'* ]] || {
            qd_warn "$unit 使用非默认 ExecStart；请先核对实际配置文件，不会修改可能未使用的配置"; return 1;
        }
        for settings in Environment EnvironmentFiles ConfigurationDirectory; do
            actual="$(qd_unit_property "$unit" "$settings")" || return 1
            [ -z "$actual" ] || { qd_warn "$unit 有自定义 $settings；请先核对有效配置目录"; return 1; }
        done
        break
    done
}

qd_graphical_session() {
    local sessions sid uid rest info type
    sessions="$(loginctl list-sessions --no-legend --no-pager)" || return 1
    while read -r sid uid rest; do
        [ "$uid" = "$(id -u)" ] || continue
        info="$(loginctl show-session "$sid" -p Type -p Active -p Remote 2>/dev/null)" || continue
        type="$(sed -n 's/^Type=//p' <<<"$info")"
        case "$type" in x11|wayland) ;; *) continue;; esac
        if grep -qx 'Active=yes' <<<"$info" && grep -qx 'Remote=no' <<<"$info"; then
            printf '%s (%s)\n' "$sid" "$type"; return 0
        fi
    done <<<"$sessions"
    return 1
}
qd_check_display_environment() {
    local pattern='^(DISPLAY|WAYLAND_DISPLAY)=.+'
    case "${1:-}" in
        *'(x11)') pattern='^DISPLAY=.+';;
        *'(wayland)') pattern='^WAYLAND_DISPLAY=.+';;
    esac
    systemctl --user show-environment | grep -qE "$pattern" \
        || { qd_warn '用户服务没有匹配图形会话的 DISPLAY/WAYLAND_DISPLAY；请重新登录图形会话后运行'; return 1; }
}
qd_sunshine_binary() { dpkg -L sunshine | grep -E '/bin/sunshine$' | head -n1; }
qd_running_binary_current() {
    local unit="$1" pid bin proc
    pid="$(qd_unit_property "$unit" MainPID)" || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || { qd_warn "$unit 没有运行中 MainPID"; return 1; }
    bin="$(qd_sunshine_binary)" || return 1
    proc="${QD_PROC_ROOT:-/proc}/$pid/exe"
    if [ ! -r "$proc" ]; then
        qd_warn "无法读取 PID $pid 的 executable；未验证运行版本"; return 2
    fi
    [ "$(stat -Lc '%d:%i' "$bin")" = "$(stat -Lc '%d:%i' "$proc")" ] || {
        qd_warn "PID $pid 仍在运行旧的/已删除的 Sunshine executable；需要重启用户服务"; return 1;
    }
}
qd_sunshine_processes() { ps -eo pid=,uid=,comm= | awk '$3 == "sunshine" {print "PID=" $1 " UID=" $2}'; }

# Idle control TCP ports only. UDP media sockets are created for streaming sessions.
qd_check_listeners() {
    local bind="$1" base="$2" unit="$3" pid out
    pid="$(qd_unit_property "$unit" MainPID)" || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || { qd_warn "$unit 没有 MainPID"; return 1; }
    out="$(ss -H -ltnp)" || return 1
    printf '%s\n' "$out" | python3 -c '
import re, sys
bind, base, pid = sys.argv[1], int(sys.argv[2]), sys.argv[3]
rows = [line.split() for line in sys.stdin if line.strip()]
failed = False
for port in (base - 5, base, base + 1, base + 21):
    matches = [r for r in rows if len(r) >= 5 and r[0] == "LISTEN" and r[3].rsplit(":", 1)[-1] == str(port)]
    if not matches:
        print(f"TCP {port} 未监听"); failed = True; continue
    for row in matches:
        if row[3] != f"{bind}:{port}":
            print(f"TCP {port} 监听地址不符: {row[3]}，期望 {bind}"); failed = True; continue
        owners = re.findall(r"pid=(\d+)", " ".join(row[5:]))
        if owners and pid not in owners:
            print(f"TCP {port} 属于其他 PID {owners}，期望服务 PID {pid}"); failed = True
        elif not owners:
            print(f"TCP {port} 地址正确；ss 未提供 PID，未验证所有者")
        else:
            print(f"TCP {port} 地址/PID 正确")
sys.exit(1 if failed else 0)
' "$bind" "$base" "$pid"
}
