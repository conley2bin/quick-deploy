#!/bin/bash
# Fcitx5 维基百科词库自动安装脚本
# 自动下载并导入中文维基百科词库

set -e

echo "========================================"
echo "  Fcitx5 维基百科词库安装"
echo "========================================"
echo

# 创建词库目录
DICT_DIR="$HOME/.local/share/fcitx5/pinyin/dictionaries"
mkdir -p "$DICT_DIR"

# 创建临时工作目录
WORK_DIR="/tmp/fcitx5-zhwiki-install"
mkdir -p "$WORK_DIR"

echo "正在下载中文维基百科词库..."
echo "来源: https://github.com/felixonmars/fcitx5-pinyin-zhwiki"
echo

# 获取最新版本下载链接
LATEST_URL="https://github.com/felixonmars/fcitx5-pinyin-zhwiki/releases/latest"
echo "正在获取最新版本..."

# 下载最新的 .dict 文件
# 使用 GitHub API 获取最新 release
RELEASE_API="https://api.github.com/repos/felixonmars/fcitx5-pinyin-zhwiki/releases/latest"

# 获取所有 .dict 文件的下载链接
DOWNLOAD_URLS=$(curl -s "$RELEASE_API" | grep "browser_download_url.*\.dict\"" | cut -d '"' -f 4)

if [ -z "$DOWNLOAD_URLS" ]; then
    echo "错误: 无法获取下载链接"
    echo "请手动下载: https://github.com/felixonmars/fcitx5-pinyin-zhwiki/releases"
    exit 1
fi

echo "找到以下词库文件:"
echo "$DOWNLOAD_URLS" | sed 's|.*/||'
echo

# 下载所有词库文件
count=0
while IFS= read -r url; do
    filename=$(basename "$url")
    echo "正在下载: $filename"

    if wget -q -O "$WORK_DIR/$filename" "$url"; then
        # 移动到词库目录
        mv "$WORK_DIR/$filename" "$DICT_DIR/$filename"
        echo "  已安装: $filename"
        count=$((count + 1))
    else
        echo "  警告: 下载失败，跳过"
    fi
done <<< "$DOWNLOAD_URLS"

echo
echo "共安装 $count 个词库文件"
echo

# 清理临时文件
rm -rf "$WORK_DIR"

echo "========================================"
echo "  重新加载 Fcitx5"
echo "========================================"
echo

if pgrep -x fcitx5 > /dev/null; then
    echo "正在重新加载 Fcitx5..."
    fcitx5-remote -r
    sleep 1
    echo "Fcitx5 已重新加载"
else
    echo "Fcitx5 未运行，请手动启动: fcitx5 -d"
fi

echo
echo "========================================"
echo "  安装完成"
echo "========================================"
echo
echo "已安装的词库:"
ls -lh "$DICT_DIR"/*.dict 2>/dev/null | awk '{print "  " $9 " (" $5 ")"}'
echo
echo "验证词库是否生效:"
echo "  1. 切换到中文输入法 (Ctrl+Space)"
echo "  2. 输入测试词汇:"
echo "     - 维基百科专有词汇: 输入 'weijibaikequanci'"
echo "     - 网络流行语: 输入 'wangluoliuxingyu'"
echo "     - 如果出现对应候选词，说明词库已生效"
echo
echo "如果词库未生效:"
echo "  重启 Fcitx5: pkill fcitx5 && fcitx5 -d"
echo
