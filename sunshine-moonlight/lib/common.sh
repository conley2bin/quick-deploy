# shellcheck shell=bash
# quick-deploy/sunshine-moonlight 共享函数库。
# 只被本目录的脚本 source，不直接执行。
#
# 约定（与同仓库 tailscale/、fresh-install/ 模块一致）：
#   - 幂等：重跑收敛到同一状态
#   - 写后回读校验，不信任“写入动作完成”本身
#   - 任何校验失败都发生在系统被修改之前（先验证、后变更）

# ---- 输出 -----------------------------------------------------------------

qd_info()    { printf '%s\n' "$*"; }
qd_warn()    { printf '警告: %s\n' "$*" >&2; }
qd_die()     { printf '错误: %s\n' "$*" >&2; exit 1; }
qd_section() { printf '\n========== %s ==========\n' "$*"; }

# ---- 运行前提 --------------------------------------------------------------

# 主脚本一律禁止 root：需要管理员权限的步骤各自显式调用 sudo，
# 避免以 root 身份写坏用户目录的属主，也避免 systemctl --user 打到 root 的总线。
qd_require_not_root() {
    [ "$(id -u)" -ne 0 ] \
        || qd_die '请不要以 root 或 sudo 运行本脚本。需要管理员权限的步骤会自行调用 sudo。'
}

# 测试专用钩子：QD_OS_RELEASE_FILE 指向替代 os-release（tests/run.sh 使用，真实运行不要设置）。
# 注意：source os-release 会写入 VERSION/ID/NAME 等通用变量名，
# 调用方脚本不得使用这些名字存放自己的状态。
QD_OS_RELEASE_FILE="${QD_OS_RELEASE_FILE:-/etc/os-release}"

qd_require_ubuntu() {
    [ -r "$QD_OS_RELEASE_FILE" ] || qd_die "无法读取 $QD_OS_RELEASE_FILE"
    # shellcheck disable=SC1090
    . "$QD_OS_RELEASE_FILE"
    [ "${ID:-}" = ubuntu ] || qd_die "当前系统不是 Ubuntu: ${ID:-未知}"
    [ -n "${VERSION_ID:-}" ] || qd_die '系统版本信息缺失'
    dpkg --compare-versions "$VERSION_ID" ge 24.04 \
        || qd_die "仅支持 Ubuntu 24.04 及更高版本；当前为 ${PRETTY_NAME:-$VERSION_ID}"
    qd_info "系统: ${PRETTY_NAME:-Ubuntu $VERSION_ID}"
}

qd_require_cmd() {
    command -v "$1" >/dev/null 2>&1 || qd_die "缺少命令 $1。请先安装: sudo apt install ${2:-$1}"
}

qd_sudo() {
    sudo "$@"
}

# ---- 临时文件 --------------------------------------------------------------

QD_TEMP_FILES=()
QD_TEMP_DIRS=()

qd_cleanup() {
    local f d
    for f in "${QD_TEMP_FILES[@]}"; do rm -f "$f"; done
    for d in "${QD_TEMP_DIRS[@]}"; do rm -rf "$d"; done
}
trap qd_cleanup EXIT

# qd_mktemp_file VAR [MKTEMP_ARGS...] / qd_mktemp_dir VAR [MKTEMP_ARGS...]
# 通过“输出变量”把路径写回父 shell 并登记到 QD_TEMP_FILES/QD_TEMP_DIRS，
# EXIT trap 统一清理。禁止用 command substitution 调用这两个函数——
# $(qd_mktemp_file) 在子 shell 里执行，数组登记会随子 shell 一起被丢弃，
# trap 永远清理不到（历史上每个临时文件都因此泄漏）。
qd_mktemp_file() {
    local __qd_var="$1"; shift || true
    local f
    f="$(mktemp "$@")" || qd_die '无法创建临时文件'
    QD_TEMP_FILES+=("$f")
    printf -v "$__qd_var" '%s' "$f"
}

qd_mktemp_dir() {
    local __qd_var="$1"; shift || true
    local d
    d="$(mktemp -d "$@")" || qd_die '无法创建临时目录'
    QD_TEMP_DIRS+=("$d")
    printf -v "$__qd_var" '%s' "$d"
}

# ---- 输入校验 ------------------------------------------------------------------

# qd_valid_ipv4 IP —— 真正的 IPv4 点分四段校验：恰好 4 段、每段 1-3 位数字、0-255。
# 正则整体先拒绝换行/空白/特殊字符（防配置注入）。
qd_valid_ipv4() {
    local ip="$1" octet
    [[ "$ip" =~ ^[0-9.]+$ ]] || return 1
    local -a octets
    IFS='.' read -ra octets <<<"$ip"
    [ "${#octets[@]}" -eq 4 ] || return 1
    for octet in "${octets[@]}"; do
        [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
        ((10#$octet <= 255)) || return 1
    done
    return 0
}

# ---- 下载与校验 --------------------------------------------------------------

qd_curl() {
    curl -fL --retry 3 --connect-timeout 20 "$@"
}

# qd_verify_sha256 FILE EXPECTED_HEX —— 不匹配即 die，调用方保证尚未做任何系统修改。
qd_verify_sha256() {
    local file="$1" expected="$2" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    [ "$actual" = "$expected" ] || {
        qd_warn "期望 sha256: $expected"
        qd_warn "实际 sha256: $actual"
        qd_die "SHA-256 摘要不匹配，已放弃；未对系统做任何修改。"
    }
    qd_info "SHA-256 校验通过: $actual"
}

# 版本号比较：剥离前导 v 后用 dpkg --compare-versions（纯数字点分版本，如 2026.516.143833）。
qd_version_ge() {
    local a="${1#v}" b="${2#v}"
    dpkg --compare-versions "$a" ge "$b"
}

# ---- sunshine.conf 合并（保留未知键、注释与行序） ----------------------------

# 写文件一律“同目录临时文件 + mv”原子替换，保留原文件权限位；
# 新文件的权限取 mktemp 默认的 0600（ sunshine.conf 可能含敏感路径/设置）。
# 同目录是为了让 mv 退化为 rename(2)，避免跨文件系统拷贝窗口里留下半写文件。

# _qd_conf_begin_write FILE —— 准备写入：打印临时文件路径到 __QD_CONF_TMP，原权限到 __QD_CONF_MODE
_qd_conf_begin_write() {
    local file="$1"
    __QD_CONF_MODE=''
    if [ -f "$file" ]; then
        __QD_CONF_MODE="$(stat -c %a "$file")"
    fi
    qd_mktemp_file __QD_CONF_TMP "$file.qdtmp.XXXXXX"
}

_qd_conf_finish_write() {
    local file="$1"
    [ -z "$__QD_CONF_MODE" ] || chmod "$__QD_CONF_MODE" "$__QD_CONF_TMP"
    mv -f "$__QD_CONF_TMP" "$file"
}

_qd_conf_read() { # FILE —— 存在则输出内容，不存在则输出空
    if [ -f "$1" ]; then
        cat -- "$1"
    fi
}

# qd_conf_set FILE KEY VALUE
# 幂等：键存在则原位替换（重复键只保留第一行），不存在则追加到文件末尾。
qd_conf_set() {
    local file="$1" key="$2" value="$3"
    _qd_conf_begin_write "$file"
    _qd_conf_read "$file" | awk -v key="$key" -v value="$value" '
        {
            line = $0
            stripped = line
            sub(/^[ \t]+/, "", stripped)
            if (stripped ~ ("^" key "[ \t]*=")) {
                if (!done) { print key " = " value; done = 1 }
                next
            }
            print line
        }
        END { if (!done) print key " = " value }
    ' >"$__QD_CONF_TMP"
    _qd_conf_finish_write "$file"
}

# qd_conf_unset FILE KEY
# 删除该键的所有行（例如 capture 自动侦测 = 配置里完全没有 capture 键）。
# 键不存在时不触碰文件（不触发备份、不改变 mtime）。
qd_conf_unset() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 0
    grep -qE "^[ \t]*${key}[ \t]*=" -- "$file" || return 0
    _qd_conf_begin_write "$file"
    awk -v key="$key" '
        {
            stripped = $0
            sub(/^[ \t]+/, "", stripped)
            if (stripped ~ ("^" key "[ \t]*=")) next
            print $0
        }
    ' "$file" >"$__QD_CONF_TMP"
    _qd_conf_finish_write "$file"
}

# qd_conf_ensure_token FILE KEY TOKEN
# 键的值按逗号分隔列表处理：列表已含精确 TOKEN 则原样保留，否则追加；键不存在则新建。
qd_conf_ensure_token() {
    local file="$1" key="$2" token="$3"
    _qd_conf_begin_write "$file"
    _qd_conf_read "$file" | awk -v key="$key" -v token="$token" '
        function trim(x) { gsub(/^[ \t]+|[ \t]+$/, "", x); return x }
        {
            line = $0
            stripped = line
            sub(/^[ \t]+/, "", stripped)
            if (stripped ~ ("^" key "[ \t]*=")) {
                if (done) { next }
                val = stripped
                sub(("^" key "[ \t]*=[ \t]*"), "", val)
                n = split(val, parts, ",")
                found = 0
                for (i = 1; i <= n; i++) {
                    if (trim(parts[i]) == token) found = 1
                }
                if (found) { print line } else {
                    newval = trim(val)
                    if (newval == "") { newval = token } else { newval = newval ", " token }
                    print key " = " newval
                }
                done = 1
                next
            }
            print line
        }
        END { if (!done) print key " = " token }
    ' >"$__QD_CONF_TMP"
    _qd_conf_finish_write "$file"
}

# qd_conf_get FILE KEY —— 打印首个匹配键的值（无匹配则无输出、返回 1）。只读。
qd_conf_get() {
    local file="$1" key="$2"
    [ -f "$file" ] || return 1
    awk -v key="$key" '
        {
            stripped = $0
            sub(/^[ \t]+/, "", stripped)
            if (stripped ~ ("^" key "[ \t]*=")) {
                sub(("^" key "[ \t]*=[ \t]*"), "", stripped)
                gsub(/[ \t]+$/, "", stripped)
                print stripped
                found = 1
                exit
            }
        }
        END { if (!found) exit 1 }
    ' "$file"
}
