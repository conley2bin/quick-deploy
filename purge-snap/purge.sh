#!/bin/bash

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== 彻底删除 Snap 和 snapd ===${NC}\n"

# 1. 列出已安装的 snap 包
echo -e "\n${YELLOW}[1/8] 列出已安装的 snap 包...${NC}"
if command -v snap &> /dev/null; then
    snap list
else
    echo -e "${YELLOW}snap 命令不存在，可能已卸载${NC}"
fi

# 2. 删除所有 snap 包
echo -e "\n${YELLOW}[2/8] 删除 snap 包...${NC}"
if command -v snap &> /dev/null; then
    # 获取已安装的 snap 包列表（排除表头）
    SNAPS=$(LC_ALL=C snap list 2>/dev/null | awk 'NR>1 {print $1}')

    if [ -z "$SNAPS" ]; then
        echo -e "${YELLOW}没有安装的 snap 包${NC}"
    else
        # 按依赖顺序删除（先删除应用，最后删除基础包）
        # 先删除非基础包
        for snap in $SNAPS; do
            if [[ ! "$snap" =~ ^(bare|core|core18|core20|core22|snapd)$ ]]; then
                echo -e "删除: $snap"
                sudo snap remove --purge "$snap" 2>/dev/null || echo -e "${YELLOW}  跳过: $snap${NC}"
            fi
        done

        # 删除基础包（按依赖顺序）
        for base in bare core22 core20 core18 core snapd; do
            if echo "$SNAPS" | grep -q "^$base$"; then
                echo -e "删除基础包: $base"
                sudo snap remove --purge "$base" 2>/dev/null || echo -e "${YELLOW}  跳过: $base (将通过 apt 删除)${NC}"
            fi
        done
    fi
    echo -e "${GREEN}✓ snap 包删除完成${NC}"
else
    echo -e "${YELLOW}snap 命令不存在，跳过此步骤${NC}"
fi

# 3. 禁用 snapd 服务
echo -e "\n${YELLOW}[3/8] 禁用 snapd 服务...${NC}"
sudo systemctl disable snapd.socket 2>/dev/null || true
sudo systemctl disable snapd.service 2>/dev/null || true
sudo systemctl disable snapd.seeded.service 2>/dev/null || true
sudo systemctl stop snapd.socket 2>/dev/null || true
sudo systemctl stop snapd.service 2>/dev/null || true
echo -e "${GREEN}✓ snapd 服务已禁用${NC}"

# 4. 移除 snapd 包
echo -e "\n${YELLOW}[4/8] 移除 snapd 包...${NC}"
sudo apt autoremove --purge snapd -y
echo -e "${GREEN}✓ snapd 包已移除${NC}"

# 5. 防止 snapd 重新安装
echo -e "\n${YELLOW}[5/8] 防止 snapd 重新安装...${NC}"
sudo apt-mark hold snapd
echo -e "${GREEN}✓ snapd 已标记为 hold${NC}"

# 6. 创建 APT 偏好设置文件
# Pin-Priority: -10 表示永不安装此包（负数优先级阻止安装）
# 即使依赖关系要求安装 snapd，APT 也会拒绝
echo -e "\n${YELLOW}[6/8] 创建 APT 偏好设置文件...${NC}"
sudo mkdir -p /etc/apt/preferences.d
sudo tee /etc/apt/preferences.d/nosnap.pref > /dev/null << 'EOF'
Package: snapd
Pin: release a=*
Pin-Priority: -10
EOF
echo -e "${GREEN}✓ APT 偏好设置已创建 (Pin-Priority: -10 阻止 snapd 安装)${NC}"

# 7. 清理残留目录
echo -e "\n${YELLOW}[7/8] 清理残留目录...${NC}"
sudo rm -rf /var/cache/snapd/
sudo rm -rf "$HOME/snap"
sudo rm -rf /snap
sudo rm -rf /var/snap
sudo rm -rf /var/lib/snapd
echo -e "${GREEN}✓ 残留目录已清理${NC}"

# 8. 刷新软件包信息
echo -e "\n${YELLOW}[8/8] 刷新软件包信息...${NC}"
sudo apt update
echo -e "${GREEN}✓ 软件包信息已更新${NC}"

# 验证删除结果
echo -e "\n${GREEN}=== 删除完成 ===${NC}"
echo -e "\n验证结果："

# 检查 snap 命令是否还存在
if command -v snap &> /dev/null; then
    echo -e "  snap 命令: ${RED}仍存在${NC}"
else
    echo -e "  snap 命令: ${GREEN}已删除${NC}"
fi

# 检查 snapd 服务状态
if systemctl is-active --quiet snapd.service; then
    echo -e "  snapd 服务: ${RED}仍在运行${NC}"
else
    echo -e "  snapd 服务: ${GREEN}已停止${NC}"
fi

# 检查 snapd 包是否还存在
if [ "$(dpkg-query -W -f='${db:Status-Status}' snapd 2>/dev/null)" = "installed" ]; then
    echo -e "  snapd 包: ${RED}仍存在${NC}"
else
    echo -e "  snapd 包: ${GREEN}已删除${NC}"
fi

echo -e "\n${YELLOW}注意事项：${NC}"
echo -e "  1. 某些系统更新可能尝试重新安装 snapd，已通过 apt-mark hold 防止"
echo -e "  2. 如需重新启用 snap，执行: ${GREEN}sudo apt-mark unhold snapd${NC}"
