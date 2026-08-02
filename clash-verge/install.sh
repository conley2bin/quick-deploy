#!/bin/bash

# 离线安装 Clash Verge Rev —— 使用仓库内置的 .deb
# 解决的死锁：全新机器还没有代理时，无法翻墙去下载代理工具。
# 安装包随 git 仓库携带（git clone 不需要科学上网），装完即可用。

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEB="$SCRIPT_DIR/Clash.Verge_2.5.2_amd64.deb"

die() {
    echo -e "${RED}✗ $1${NC}" >&2
    exit 1
}

echo -e "${GREEN}=== 安装 Clash Verge Rev（离线包） ===${NC}\n"

# 1. 安装包必须随仓库携带
[ -f "$DEB" ] || die "安装包不存在: $DEB
     仓库应内置该文件，请确认仓库是完整 clone 的。"

# 2. 架构校验：内置包只有 amd64
ARCH="$(dpkg --print-architecture)" || die "dpkg --print-architecture 执行失败"
[ "$ARCH" = "amd64" ] || die "内置安装包是 amd64 架构，当前系统是 ${ARCH}。
     请到官方 Releases 下载对应架构: https://github.com/clash-verge-rev/clash-verge-rev/releases"

# 3. 完整性校验：确认是合法的 deb 而不是下载中断的残件
dpkg-deb --info "$DEB" > /dev/null 2>&1 || die "安装包已损坏（dpkg-deb 无法解析），请重新 clone 仓库"

# 4. 安装（apt 会顺带解决依赖；重复运行 = 重装同版本，幂等）
echo -e "${YELLOW}安装 $DEB ...${NC}"
sudo apt install -y "$DEB"

# 5. 验证：以 dpkg 数据库为准，不以 apt 输出为准
if dpkg-query -W -f='${db:Status-Status}' clash-verge 2>/dev/null | grep -q installed; then
    VERSION="$(dpkg-query -W -f='${Version}' clash-verge)"
    echo -e "\n${GREEN}=== 完成 ===${NC}"
    echo -e "  Clash Verge Rev ${VERSION} 已安装"
    echo -e "\n下一步："
    echo -e "  1. 启动 Clash Verge，导入你的订阅"
    echo -e "  2. 运行 ${GREEN}$SCRIPT_DIR/tun-fix.sh${NC} 解决 TUN 模式下 SSH/局域网问题"
else
    die "apt 未报错，但 dpkg 数据库里没有已安装的 clash-verge——请检查上面的输出"
fi
