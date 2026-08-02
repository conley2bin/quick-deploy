#!/bin/bash

# 新装系统一键初始化（Ubuntu 24.04+，x86）
# 顺序有依赖关系，不要随意调整：
#   1. 清华镜像 —— 后面三步都要用 apt，先换源给全程加速
#   2. 卸载 Snap —— 先清垃圾再装东西
#   3. zsh —— 需要 apt 安装
#   4. 中文输入法 —— 需要 apt 安装，跑完本就要求注销，正好收尾
# 任何一步失败即中止；修复后重跑本脚本即可，
# 每个子脚本都幂等（会自动跳过或重做，无副作用）。

set -uo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STEPS=(
    "tsinghua-mirror/install.sh|切换 APT 源为清华镜像"
    "purge-snap/purge.sh|彻底卸载 Snap"
    "zsh/install.sh|安装 zsh 与 oh-my-zsh"
    "chinese-input-method/install.sh|安装配置中文输入法"
)

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   新装系统一键初始化 (共 ${#STEPS[@]} 步)   ${NC}"
echo -e "${GREEN}========================================${NC}"

# 预存 sudo 密码并后台保活：四步全程可能超过 sudo 的 15 分钟密码有效期，
# 保活循环每分钟续期一次，避免中途再次提示输入密码
sudo -v || { echo -e "${RED}✗ 需要 sudo 权限${NC}" >&2; exit 1; }
# 保活循环自检父进程存活：即使脚本被强杀（trap 跑不了），
# 循环也会在 60 秒内发现父进程消失而自行退出，不会成为无限续期 sudo 的孤儿
(while kill -0 "$$" 2>/dev/null; do sudo -n true 2>/dev/null; sleep 60; done) &
SUDO_KEEPALIVE=$!
trap 'kill $SUDO_KEEPALIVE 2>/dev/null' EXIT

TOTAL=${#STEPS[@]}
INDEX=0
for ENTRY in "${STEPS[@]}"; do
    INDEX=$((INDEX + 1))
    SCRIPT="${ENTRY%%|*}"
    NAME="${ENTRY#*|}"
    echo -e "\n${GREEN}━━━━━━━━━━ [${INDEX}/${TOTAL}] ${NAME} ━━━━━━━━━━${NC}\n"
    if bash "$SCRIPT_DIR/$SCRIPT"; then
        echo -e "\n${GREEN}✓ [${INDEX}/${TOTAL}] ${NAME} —— 完成${NC}"
    else
        # 注：RC=$? 必须是 else 分支的第一条语句，前面插入任何命令都会覆盖 $?
        RC=$?
        echo -e "\n${RED}✗ [${INDEX}/${TOTAL}] ${NAME} —— 失败 (退出码 ${RC})${NC}"
        echo -e "${YELLOW}已中止后续步骤。修复问题后重跑本脚本即可："
        echo -e "  已完成的步骤支持重跑（自动跳过或重做，无副作用）。${NC}"
        exit 1
    fi
done

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}   全部 ${TOTAL} 步完成   ${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "\n${YELLOW}最后一步：注销并重新登录${NC}，以下改动才会生效："
echo -e "  • 默认 shell 切换为 zsh"
echo -e "  • 中文输入法环境变量"
