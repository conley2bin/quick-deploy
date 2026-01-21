#!/bin/bash
# Fcitx5 + Chinese Addons 中文输入法安装配置脚本
# 适用于 Ubuntu 22.04/24.04 (支持 X11 和 Wayland)

set -e

echo "========================================"
echo "  Fcitx5 中文输入法安装"
echo "========================================"
echo
echo "将安装 fcitx5-chinese-addons 拼音输入法"
echo "支持 X11 和 Wayland，可导入搜狗词库"
echo

# 检测当前显示服务器
CURRENT_SESSION=$(echo $XDG_SESSION_TYPE)
echo "当前显示服务器: $CURRENT_SESSION"
echo

# ============================================
# 步骤 1: 删除冲突的输入法
# ============================================
# 说明: 只删除与 fcitx5 冲突的 fcitx4
#       保留所有不冲突的输入法 (fcitx5-rime, ibus)
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[1/6] 检查冲突的输入法"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# 查询 fcitx4 (冲突)
FCITX4_PACKAGES=$(dpkg -l | grep -E "^ii.*fcitx[^5]" | awk '{print $2}' || true)

if [ -n "$FCITX4_PACKAGES" ]; then
    echo "检测到 fcitx4 (与 fcitx5 冲突):"
    echo "$FCITX4_PACKAGES" | sed 's/^/  - /'
    echo
    echo "正在自动删除 fcitx4..."
    sudo apt purge -y $FCITX4_PACKAGES
    sudo apt autoremove -y
    rm -rf ~/.config/fcitx 2>/dev/null || true
    echo "fcitx4 已删除"
else
    echo "未检测到冲突的输入法"
fi

echo

# ============================================
# 步骤 2: 安装 Fcitx5 和中文输入法
# ============================================
# 说明: 安装 fcitx5 框架和 chinese-addons
#       chinese-addons 包含拼音、双拼、五笔等输入法
#       frontend 包提供对 GTK/Qt 应用的支持
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[2/6] 安装 Fcitx5 和中文输入法"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

sudo apt update
sudo apt install -y \
    fcitx5 \
    fcitx5-chinese-addons \
    fcitx5-config-qt \
    fcitx5-frontend-gtk2 \
    fcitx5-frontend-gtk3 \
    fcitx5-frontend-gtk4 \
    fcitx5-frontend-qt5 \
    im-config

echo "软件包安装完成"
echo

# ============================================
# 步骤 3: 配置输入法框架
# ============================================
# 说明: 使用 im-config 将 fcitx5 设置为系统默认输入法框架
#       这样系统会优先使用 fcitx5 而不是 ibus
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[3/6] 配置输入法框架"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

im-config -n fcitx5
echo "已设置 fcitx5 为默认输入法框架"
echo

# ============================================
# 步骤 4: 配置环境变量
# ============================================
# 说明: 设置环境变量告诉应用程序使用 fcitx5
#       GTK_IM_MODULE - GTK 应用 (Firefox, GNOME 应用)
#       QT_IM_MODULE  - Qt 应用 (Telegram, KDE 应用)
#       XMODIFIERS    - X11 应用
#       SDL_IM_MODULE - SDL 应用 (部分游戏)
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[4/6] 配置环境变量"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# 添加到 ~/.profile (用户级)
if ! grep -q "GTK_IM_MODULE=fcitx" ~/.profile 2>/dev/null; then
    cat >> ~/.profile << 'EOF'

# Fcitx5 输入法环境变量
export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx
EOF
    echo "已添加环境变量到 ~/.profile"
else
    echo "环境变量已存在于 ~/.profile"
fi

echo

# ============================================
# 步骤 5: 配置自动启动
# ============================================
# 说明: 创建 .desktop 文件，使 fcitx5 在登录时自动启动
#       文件位置: ~/.config/autostart/fcitx5.desktop
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[5/6] 配置自动启动"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

mkdir -p ~/.config/autostart

cat > ~/.config/autostart/fcitx5.desktop << 'EOF'
[Desktop Entry]
Name=Fcitx 5
GenericName=Input Method
Comment=Start Input Method
Exec=fcitx5
Icon=fcitx
Terminal=false
Type=Application
Categories=System;Utility;
StartupNotify=false
X-GNOME-Autostart-Phase=Applications
X-GNOME-AutoRestart=false
X-GNOME-Autostart-Notify=false
X-KDE-autostart-after=panel
EOF

echo "已配置 fcitx5 自动启动"
echo

# ============================================
# 步骤 6: 配置输入法列表
# ============================================
# 说明: 创建 fcitx5 配置文件，添加拼音输入法
#       配置文件: ~/.config/fcitx5/profile
#       包含: 键盘 (US) + 拼音输入法
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "[6/6] 配置输入法列表"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

mkdir -p ~/.config/fcitx5

cat > ~/.config/fcitx5/profile << 'EOF'
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=pinyin

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=pinyin
Layout=

[GroupOrder]
0=Default
EOF

echo "已添加拼音输入法到配置"
echo

# ============================================
# 启动 Fcitx5
# ============================================
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "启动 Fcitx5"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

pkill fcitx5 2>/dev/null || true
sleep 1

export GTK_IM_MODULE=fcitx
export QT_IM_MODULE=fcitx
export XMODIFIERS=@im=fcitx
export SDL_IM_MODULE=fcitx

fcitx5 -d 2>/dev/null || fcitx5 &
sleep 2

if pgrep -x fcitx5 > /dev/null; then
    echo "Fcitx5 已启动"
else
    echo "警告: Fcitx5 启动失败"
fi

echo

# ============================================
# 完成提示
# ============================================
echo "========================================"
echo "  安装完成"
echo "========================================"
echo
echo "重要: 注销并重新登录以使环境变量生效"
echo "     手动操作或者使用命令: gnome-session-quit --logout --no-prompt"
echo
echo "首次配置 (必须):"
echo "  1. 运行: fcitx5-configtool"
echo "  2. 在 Input Method 标签页中:"
echo "     - 右侧 Available Input Method 找到 'Pinyin'"
echo "     - 双击 'Pinyin' 添加拼音输入法"
echo "     - 添加 'Keyboard - English (US)' 英文键盘"
echo "     - 调整顺序 (推荐):"
echo "       * Pinyin 在第一位 (默认中文输入)"
echo "       * English 在第二位"
echo "     - 点击 'Apply' 保存"
echo "  3. 删除多余的 Group (如果有 Group 2):"
echo "     - 在 Group 下拉菜单中选择要删除的 Group"
echo "     - 点击下拉菜单右侧的 '-' 按钮"
echo "     - 点击 'Apply' 保存"
echo "  4. fcitx5 默认快捷键:"
echo "     - Ctrl+Space: 按列表顺序循环切换输入法"
echo "     - Left Shift: 临时切换到第一个输入法"
echo
echo "导入词库 (可选):"
echo "  运行: ./import-dict.sh"
echo
