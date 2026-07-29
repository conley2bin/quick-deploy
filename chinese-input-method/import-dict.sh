#!/bin/bash
# Fcitx5 维基百科词库自动安装脚本
# 自动下载并导入中文维基百科词库

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

    if ! command -v fcitx5 >/dev/null 2>&1; then
        echo "错误: 未找到 fcitx5，请先运行 install.sh 安装 Fcitx5。"
        exit 1
    fi
}

get_download_urls() {
    local release_json

    if ! release_json="$(curl -fsSL "$RELEASE_API")"; then
        echo "错误: 无法获取最新 release 信息" >&2
        echo "请检查网络，或手动访问: $REPO_URL/releases" >&2
        exit 1
    fi

    if command -v jq >/dev/null 2>&1; then
        printf '%s\n' "$release_json" \
            | jq -r '.assets[]? | .browser_download_url? | select(type == "string" and endswith(".dict"))'
    else
        printf '%s\n' "$release_json" \
            | awk -F '"' '/browser_download_url/ && /\.dict"/ {print $4}'
    fi
}

ensure_not_root

section "Fcitx5 维基百科词库安装"

check_requirements

mkdir -p "$DICT_DIR"
WORK_DIR="$(mktemp -d "$DICT_DIR/.zhwiki-import.XXXXXX")"
mkdir -p "$WORK_DIR"

echo "正在下载中文维基百科词库..."
echo "来源: $REPO_URL"
echo

# 获取最新版本下载链接
echo "正在获取最新版本..."

# 获取所有 .dict 文件的下载链接
mapfile -t DOWNLOAD_URLS < <(get_download_urls)

if [ "${#DOWNLOAD_URLS[@]}" -eq 0 ]; then
    echo "错误: 无法获取下载链接"
    echo "请手动下载: $REPO_URL/releases"
    exit 1
fi

echo "找到以下词库文件:"
printf '  - %s\n' "${DOWNLOAD_URLS[@]##*/}"
echo

# 下载所有词库文件
count=0
failed=0
downloaded_files=()
for url in "${DOWNLOAD_URLS[@]}"; do
    filename="${url##*/}"
    tmp_file="$WORK_DIR/$filename"

    echo "正在下载: $filename"

    if curl -fL --retry 3 --connect-timeout 20 -o "$tmp_file" "$url"; then
        if [ ! -s "$tmp_file" ]; then
            echo "  错误: 下载文件为空，跳过"
            failed=$((failed + 1))
            continue
        fi

        echo "  已下载: $filename"
        downloaded_files+=("$filename")
        count=$((count + 1))
    else
        echo "  错误: 下载失败"
        failed=$((failed + 1))
    fi
done

echo
echo "共安装 $count 个词库文件"
echo

if [ "$count" -eq 0 ]; then
    echo "错误: 没有成功安装任何词库文件"
    exit 1
fi

if [ "$failed" -gt 0 ]; then
    echo "错误: 有 $failed 个词库文件下载失败"
    echo "未替换现有词库文件"
    exit 1
fi

for filename in "${downloaded_files[@]}"; do
    mv -f "$WORK_DIR/$filename" "$DICT_DIR/$filename"
done

echo "词库文件已安装到: $DICT_DIR"

section "重新加载 Fcitx5"

if pgrep -x fcitx5 > /dev/null; then
    if command -v fcitx5-remote >/dev/null 2>&1; then
        echo "正在重新加载 Fcitx5..."
        if fcitx5-remote -r; then
            echo "Fcitx5 已重新加载"
        else
            echo "警告: Fcitx5 重载失败，请注销重新登录后再确认词库。"
        fi
    else
        echo "警告: 找不到 fcitx5-remote，请注销重新登录后再确认词库。"
    fi
else
    echo "Fcitx5 未运行，请注销重新登录后再确认词库。"
fi

echo
section "安装完成"

echo "已安装的词库:"
# 不解析 ls -l 的输出: 它的列位置随 LC_TIME 变化，且遭遇带空格的文件名会错位。
for dict in "$DICT_DIR"/*.dict; do
    [ -e "$dict" ] || continue
    printf '  %s (%s)\n' "${dict##*/}" "$(LC_ALL=C du -h --apparent-size "$dict" | cut -f1)"
done
echo
echo "如果当前会话未立即生效，请注销并重新登录。"
echo
