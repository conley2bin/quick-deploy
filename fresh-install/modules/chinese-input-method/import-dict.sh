#!/bin/bash
# Fcitx5 维基百科词库自动安装脚本
# 从 fcitx5-pinyin-zhwiki 的最新 release 下载并导入词库

set -euo pipefail

REPO_URL="https://github.com/felixonmars/fcitx5-pinyin-zhwiki"
RELEASE_API="https://api.github.com/repos/felixonmars/fcitx5-pinyin-zhwiki/releases/latest"
DICT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/fcitx5/pinyin/dictionaries"
WORK_DIR=""

cleanup() {
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}

trap cleanup EXIT

section() {
    echo "========================================"
    echo "  $1"
    echo "========================================"
    echo
}

require_command() {
    local command="$1"

    if ! command -v "$command" >/dev/null 2>&1; then
        echo "错误: 缺少命令 $command"
        exit 1
    fi
}

ensure_not_root() {
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        echo "错误: 请不要用 sudo 运行本脚本。"
        echo "词库会安装到当前用户的 Fcitx5 数据目录，请直接运行: ./import-dict.sh"
        exit 1
    fi
}

check_requirements() {
    require_command curl
    require_command pgrep
    require_command sha256sum
    # python3 用来解析 release JSON。旧版用 awk -F '"' 取第 4 字段猜 URL，
    # 那只在 JSON 恰好被美化换行时成立。GitHub 不保证这一点：压缩成单行时
    # 整份文档同时命中 /browser_download_url/ 和 /\.dict"/，$4 取到的是
    # release 自身的 url，脚本会把 API 页面当词库装下去，还报成功。
    require_command python3

    if ! command -v fcitx5 >/dev/null 2>&1; then
        echo "错误: 未找到 fcitx5，请先运行 install.sh 安装 Fcitx5。"
        exit 1
    fi
}

# 输出 manifest，每行: 名称 <TAB> URL <TAB> 字节数 <TAB> sha256
#
# 只取每类语料最新的一个日期快照。上游一个 release 里同时挂着多个日期的
# 同名语料（实测 12 个 .dict / 116.9 MiB，zhwiki / zhwikisource /
# zhwiktionary / web-slang 各三份）。旧版全下，于是 Pinyin 会同时加载同一
# 语料的三个快照，重复词条一起参与候选，而且每次更新只会继续堆积。
# 只留最新后是 4 个文件 / 39.3 MiB。
build_manifest() {
    curl -fsSL "$RELEASE_API" | python3 -c '
import sys, json, re

data = json.load(sys.stdin)
newest = {}
for asset in data.get("assets", []):
    name = asset.get("name", "")
    if not name.endswith(".dict"):
        continue
    matched = re.match(r"^(.*?)-(\d{8})\.dict$", name)
    corpus, version = (matched.group(1), matched.group(2)) if matched else (name, "")
    if corpus not in newest or version > newest[corpus][0]:
        newest[corpus] = (version, asset)

for corpus, (version, asset) in sorted(newest.items()):
    url = asset.get("browser_download_url")
    if not url:
        continue
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        digest = digest[len("sha256:"):]
    else:
        digest = ""
    print("\t".join([asset["name"], url, str(asset.get("size", 0)), digest]))
'
}

# 用 D-Bus 重启 Fcitx5。
#
# 旧版调 fcitx5-remote -r 然后打印"Fcitx5 已重新加载"，是假的：
# -r 对应 Instance::reloadConfig()，只重读全局 config（fcitx5-remote --help
# 原文就是 "reload fcitx config"），不会让 Pinyin 重新扫描 dictionaries 目录。
# 词库要生效只能让引擎重新初始化，也就是重启进程。
reload_fcitx5() {
    local controller=(--session --dest org.fcitx.Fcitx5 --object-path /controller)

    if ! pgrep -x fcitx5 >/dev/null; then
        echo "Fcitx5 未运行，下次启动时会加载新词库。"
        return
    fi

    if command -v gdbus >/dev/null 2>&1 \
        && [ "$(gdbus call "${controller[@]}" \
                --method org.fcitx.Fcitx.Controller1.CanRestart 2>/dev/null)" = "(true,)" ]; then
        echo "正在重启 Fcitx5 以加载新词库..."
        if gdbus call "${controller[@]}" \
            --method org.fcitx.Fcitx.Controller1.Restart >/dev/null 2>&1; then
            sleep 1
            if pgrep -x fcitx5 >/dev/null; then
                echo "Fcitx5 已重启，新词库已生效"
                return
            fi
        fi
    fi

    echo "警告: 无法自动重启 Fcitx5，新词库尚未加载。"
    echo "      请手动执行 'fcitx5 -r -d'，或注销重新登录。"
}

ensure_not_root

section "Fcitx5 维基百科词库安装"

check_requirements

mkdir -p "$DICT_DIR"
WORK_DIR="$(mktemp -d "$DICT_DIR/.zhwiki-import.XXXXXX")"

echo "来源: $REPO_URL"
echo "正在获取最新 release 信息..."

# 不能写成 mapfile -t X < <(build_manifest): 进程替换里的失败不会传给 mapfile，
# 生成器先吐几行再出错时 mapfile 仍返回 0，脚本会拿着残缺列表继续装。
# 先落盘，让失败由父 shell 接住。
if ! build_manifest > "$WORK_DIR/manifest"; then
    echo "错误: 无法获取或解析最新 release 信息"
    echo "请检查网络，或手动访问: $REPO_URL/releases"
    exit 1
fi

if [ ! -s "$WORK_DIR/manifest" ]; then
    echo "错误: release 中没有找到任何 .dict 资源"
    echo "请手动下载: $REPO_URL/releases"
    exit 1
fi

echo
echo "将安装以下词库 (每类语料只取最新快照):"
# LC_ALL=C 不可省: 中文 locale 下 awk 会把小数点输出成逗号，
# bash 的 printf %f 接到 "31,2" 会报错并在 set -e 下终止脚本。
while IFS=$'\t' read -r name _url size digest; do
    printf '  - %-32s %6.1f MiB%s\n' "$name" \
        "$(LC_ALL=C awk -v s="$size" 'BEGIN{print s/1048576}')" \
        "$([ -n "$digest" ] && echo "  (含 sha256)" || echo "  (无校验值)")"
done < "$WORK_DIR/manifest"
echo

# 下载到暂存目录并逐个校验。校验全部通过之前不碰 DICT_DIR 里的现有词库。
installed_names=()
while IFS=$'\t' read -r name url size digest; do
    staged="$WORK_DIR/$name"
    echo "正在下载: $name"

    if ! curl -fL --retry 3 --connect-timeout 20 -o "$staged" "$url"; then
        echo "  错误: 下载失败"
        exit 1
    fi

    # curl -f 不会拒绝返回 200 的错误页，仅判非空是不够的。
    # GitHub 的 release API 已经给出 size 和 sha256，直接拿来验。
    actual_size="$(stat -c %s "$staged")"
    if [ "$actual_size" != "$size" ]; then
        echo "  错误: 大小不符 (期望 $size，实际 $actual_size)"
        exit 1
    fi

    if [ -n "$digest" ]; then
        actual_digest="$(sha256sum "$staged" | cut -d' ' -f1)"
        if [ "$actual_digest" != "$digest" ]; then
            echo "  错误: sha256 不符"
            echo "        期望 $digest"
            echo "        实际 $actual_digest"
            exit 1
        fi
        echo "  已校验 (大小 + sha256)"
    else
        echo "  已校验 (仅大小，上游未提供 sha256)"
    fi

    installed_names+=("$name")
done < "$WORK_DIR/manifest"

echo
section "安装词库"

# 逐个 mv 是原子的，整组不是。中途失败会留下新旧混装且无从得知装到哪一步，
# 所以先把将被覆盖的旧文件挪进回滚目录，全部成功才丢弃。
ROLLBACK_DIR="$WORK_DIR/rollback"
mkdir -p "$ROLLBACK_DIR"

rollback_publish() {
    local file
    echo "错误: 安装中断，正在回滚..." >&2
    for file in "$ROLLBACK_DIR"/*; do
        [ -e "$file" ] || continue
        mv -f "$file" "$DICT_DIR/${file##*/}"
    done
    echo "已回滚到安装前的词库状态。" >&2
}

trap 'rollback_publish; cleanup' EXIT

for name in "${installed_names[@]}"; do
    if [ -e "$DICT_DIR/$name" ]; then
        mv -f "$DICT_DIR/$name" "$ROLLBACK_DIR/$name"
    fi
    mv -f "$WORK_DIR/$name" "$DICT_DIR/$name"
    echo "已安装: $name"
done

trap cleanup EXIT

# 清理被取代的旧快照。只删本脚本装过的、同语料且日期更早的文件，
# 命名必须严格匹配 <语料>-YYYYMMDD.dict，绝不碰用户自备的词库。
superseded=()
for name in "${installed_names[@]}"; do
    corpus="${name%-*}"
    keep_version="${name##*-}"
    keep_version="${keep_version%.dict}"
    [[ "$keep_version" =~ ^[0-9]{8}$ ]] || continue

    for existing in "$DICT_DIR/$corpus"-*.dict; do
        [ -e "$existing" ] || continue
        base="${existing##*/}"
        version="${base##*-}"
        version="${version%.dict}"
        [[ "$version" =~ ^[0-9]{8}$ ]] || continue
        if [ "$version" -lt "$keep_version" ]; then
            superseded+=("$base")
        fi
    done
done

if [ "${#superseded[@]}" -gt 0 ]; then
    echo
    echo "清理被取代的旧快照 (同一语料的更早日期版本):"
    for base in "${superseded[@]}"; do
        rm -f "$DICT_DIR/$base"
        echo "  已删除: $base"
    done
fi

echo
echo "词库目录: $DICT_DIR"

section "重新加载 Fcitx5"

reload_fcitx5

echo
section "安装完成"

echo "当前已安装的词库:"
# 不解析 ls -l 的输出: 它的列位置随 LC_TIME 变化，且遭遇带空格的文件名会错位。
total=0
for dict in "$DICT_DIR"/*.dict; do
    [ -e "$dict" ] || continue
    size="$(stat -c %s "$dict")"
    total=$((total + size))
    printf '  %s (%s)\n' "${dict##*/}" "$(LC_ALL=C du -h --apparent-size "$dict" | cut -f1)"
done
printf '  合计 %.1f MiB\n' "$(LC_ALL=C awk -v t="$total" 'BEGIN{print t/1048576}')"
echo
