#!/bin/bash

# 将 Ubuntu APT 源切换为清华大学镜像（TUNA）
# 等价于手动操作 https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu/ ：
#   - 仅支持 Ubuntu 24.04+ 的 DEB822 格式（/etc/apt/sources.list.d/ubuntu.sources）
#   - 版本代号自动探测（/etc/os-release），不会选错版本
#   - 官网默认模板：主仓库走清华镜像，安全更新（-security）保留官方源，
#     源码源（deb-src）与预发布（proposed）保持注释不启用
#   - 覆盖前自动备份，原子写入，重跑幂等

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

MIRROR_BASE="https://mirrors.tuna.tsinghua.edu.cn/ubuntu"
SOURCES_FILE="/etc/apt/sources.list.d/ubuntu.sources"
TMP_FILE="${SOURCES_FILE}.tmp"

# root 环境（如 Docker 容器）没有 sudo 也能跑
SUDO="sudo"
[ "$(id -u)" -eq 0 ] && SUDO=""

die() {
    echo -e "${RED}✗ $1${NC}" >&2
    exit 1
}

echo -e "${GREEN}=== 切换 APT 源为清华大学镜像 ===${NC}\n"

# 1. 校验系统身份：必须是 Ubuntu，并读取版本代号
echo -e "${YELLOW}[1/5] 检测系统版本...${NC}"
[ -r /etc/os-release ] || die "找不到 /etc/os-release，无法识别系统"
# shellcheck disable=SC1091
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || die "当前系统是 ${ID:-未知}，本脚本仅支持 Ubuntu"
[ -n "${VERSION_CODENAME:-}" ] || die "/etc/os-release 中没有 VERSION_CODENAME，无法确定版本代号"
CODENAME="$VERSION_CODENAME"
echo -e "  检测到: ${PRETTY_NAME:-Ubuntu} (代号: ${GREEN}${CODENAME}${NC})"

# 2. 校验架构：TUNA /ubuntu/ 仓库仅含 x86 软件包
echo -e "\n${YELLOW}[2/5] 检测系统架构...${NC}"
ARCH="$(dpkg --print-architecture)" || die "dpkg --print-architecture 执行失败"
if [ "$ARCH" != "amd64" ]; then
    die "检测到架构 ${ARCH}，清华 Ubuntu 镜像仅含 x86 软件包。
     ARM/PowerPC/RISC-V/S390x 设备请改用 ubuntu-ports 镜像：
     https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu-ports/"
fi
echo -e "  架构: ${GREEN}${ARCH}${NC}"

# 3. 校验 DEB822 配置文件存在（24.04+ 的标志）
echo -e "\n${YELLOW}[3/5] 检查 DEB822 配置文件...${NC}"
if [ ! -f "$SOURCES_FILE" ]; then
    die "找不到 ${SOURCES_FILE}。
     Ubuntu 24.04 之前的系统使用 /etc/apt/sources.list（传统格式），
     本脚本不支持，请按官网帮助手动配置。"
fi
echo -e "  ${GREEN}${SOURCES_FILE}${NC} 存在"

# 4. 在线验证清华源确实有该版本的目录（防止未发布版本写错配置；
#    EOL 版本在镜像站是冻结快照，不会 404，配置也能用）
echo -e "\n${YELLOW}[4/5] 验证清华源存在 ${CODENAME} 目录...${NC}"
RELEASE_URL="${MIRROR_BASE}/dists/${CODENAME}/Release"
if command -v curl &> /dev/null; then
    CHECK_OK=$(curl -fs --max-time 15 -o /dev/null "$RELEASE_URL" && echo yes || echo no)
elif command -v wget &> /dev/null; then
    CHECK_OK=$(wget -q --tries=1 --timeout=15 -O /dev/null "$RELEASE_URL" && echo yes || echo no)
else
    die "curl 和 wget 都不存在，无法在线验证镜像目录"
fi
if [ "$CHECK_OK" != "yes" ]; then
    die "镜像站没有 ${CODENAME} 的目录（${RELEASE_URL} 不可达）。
     可能原因：无网络连接，或该代号尚未被镜像（如未发布的新版本）。
     未对系统做任何修改。"
fi
echo -e "  ${GREEN}✓${NC} ${RELEASE_URL} 可达"

# 5. 备份、原子写入新配置、验证并刷新
BACKUP_FILE="${SOURCES_FILE}.bak-$(date +%Y%m%d-%H%M%S)"
echo -e "\n${YELLOW}[5/5] 备份并写入新配置...${NC}"
$SUDO cp -a "$SOURCES_FILE" "$BACKUP_FILE"
echo -e "  原文件已备份到: ${GREEN}${BACKUP_FILE}${NC}"

# 模板与清华官网默认一致（三个开关全关）：
#   主仓库/updates/backports 走清华镜像，security 保留官方源，
#   deb-src 与 proposed 保持注释不启用
# 先写 .tmp 再 mv 改名（同目录 rename 是原子操作），
# 避免写入中途断电/Ctrl-C 留下截断的配置文件
$SUDO tee "$TMP_FILE" > /dev/null << EOF
Types: deb
URIs: ${MIRROR_BASE}
Suites: ${CODENAME} ${CODENAME}-updates ${CODENAME}-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# 默认注释了源码镜像以提高 apt update 速度，如有需要可自行取消注释
# Types: deb-src
# URIs: ${MIRROR_BASE}
# Suites: ${CODENAME} ${CODENAME}-updates ${CODENAME}-backports
# Components: main restricted universe multiverse
# Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# 以下安全更新软件源为官方源配置（镜像同步有延迟，安全更新不建议走镜像）
Types: deb
URIs: http://security.ubuntu.com/ubuntu/
Suites: ${CODENAME}-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# Types: deb-src
# URIs: http://security.ubuntu.com/ubuntu/
# Suites: ${CODENAME}-security
# Components: main restricted universe multiverse
# Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

# 预发布软件源，不建议启用
# Types: deb
# URIs: ${MIRROR_BASE}
# Suites: ${CODENAME}-proposed
# Components: main restricted universe multiverse
# Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
$SUDO mv "$TMP_FILE" "$SOURCES_FILE"
echo -e "  ${GREEN}✓${NC} 已写入清华镜像配置 (${CODENAME})"

# 解析断言：DEB822 格式若丢失分隔空行，apt 会静默丢弃整个源段且不报任何错。
# 直接问 apt 解析器"你现在认到的源里有没有清华镜像"，认不到就自动还原。
if ! apt-get indextargets --format '$(REPO_URI)' 2>/dev/null | grep -q "mirrors.tuna.tsinghua.edu.cn"; then
    $SUDO cp -a "$BACKUP_FILE" "$SOURCES_FILE"
    die "写入后的配置未被 apt 正确解析（未检测到清华镜像源），已自动还原备份。"
fi

# 刷新软件包信息。--error-on=any 让下载失败（WARNING）也以非零退出：
# 裸 apt update 对下载失败只警告、退出码仍为 0，不能当作验证
echo -e "\n${YELLOW}刷新软件包信息 (apt-get update --error-on=any)...${NC}"
if $SUDO apt-get update --error-on=any; then
    echo -e "\n${GREEN}=== 完成 ===${NC}"
    echo -e "  APT 源已切换为清华大学镜像 (${CODENAME})，索引刷新成功"
    echo -e "  安全更新保留官方源，源码源/proposed 未启用"
    echo -e "  如需还原: ${SUDO} cp -a '${BACKUP_FILE}' '${SOURCES_FILE}' && ${SUDO} apt-get update"
else
    echo -e "\n${RED}apt update 失败。${NC}注意先看报错涉及哪个源——可能是本机其他"
    echo -e "  第三方源（如 vscode/chrome）自身的问题，与本脚本无关。"
    echo -e "  还原命令: ${SUDO} cp -a '${BACKUP_FILE}' '${SOURCES_FILE}' && ${SUDO} apt-get update"
    exit 1
fi
