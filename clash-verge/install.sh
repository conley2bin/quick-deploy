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
PACKAGE_NAME="clash-verge"
PACKAGE_PREFIX="Clash.Verge"
DEB=""

die() {
    echo -e "${RED}✗ $1${NC}" >&2
    exit 1
}

select_deb() {
    local arch="$1"
    local candidate package version package_arch best_version="" compare_status=0
    local candidates=()

    shopt -s nullglob
    candidates=("$SCRIPT_DIR/${PACKAGE_PREFIX}_"*"_${arch}.deb")
    shopt -u nullglob

    [ "${#candidates[@]}" -gt 0 ] || die "未找到适配 ${arch} 架构的仓库内置安装包。
     期望位置: $SCRIPT_DIR/${PACKAGE_PREFIX}_*_${arch}.deb
     请确认仓库是完整 clone 的，或到官方 Releases 下载对应架构:
     https://github.com/clash-verge-rev/clash-verge-rev/releases"

    for candidate in "${candidates[@]}"; do
        package="$(dpkg-deb --field "$candidate" Package 2>/dev/null)" || die "安装包已损坏（dpkg-deb 无法解析），请重新 clone 仓库: $candidate"
        version="$(dpkg-deb --field "$candidate" Version 2>/dev/null)" || die "安装包缺少 Version 元数据，请重新 clone 仓库: $candidate"
        package_arch="$(dpkg-deb --field "$candidate" Architecture 2>/dev/null)" || die "安装包缺少 Architecture 元数据，请重新 clone 仓库: $candidate"

        [ "$package" = "$PACKAGE_NAME" ] || die "安装包元数据不符（Package=$package），拒绝安装: $candidate"
        [ "$package_arch" = "$arch" ] || die "安装包元数据不符（Architecture=$package_arch，当前系统=$arch），拒绝安装: $candidate"
        dpkg --compare-versions "$version" eq "$version" 2>/dev/null || die "安装包版本号无效（Version=$version），拒绝安装: $candidate"

        if [ -z "$best_version" ]; then
            DEB="$candidate"
            best_version="$version"
            continue
        fi

        compare_status=0
        dpkg --compare-versions "$version" gt "$best_version" || compare_status=$?
        if [ "$compare_status" -eq 0 ]; then
            DEB="$candidate"
            best_version="$version"
        elif [ "$compare_status" -gt 1 ]; then
            die "安装包版本号无法比较（Version=$version），拒绝安装: $candidate"
        fi
    done

    [ -n "$DEB" ] || die "未能选择仓库内置安装包"
}

echo -e "${GREEN}=== 安装 Clash Verge Rev（离线包） ===${NC}\n"

# 1. 选择匹配当前架构的最新内置安装包
ARCH="$(dpkg --print-architecture)" || die "dpkg --print-architecture 执行失败"
select_deb "$ARCH"

# 2. 完整性校验：确认是合法的 deb 而不是下载中断的残件
dpkg-deb --info "$DEB" > /dev/null 2>&1 || die "安装包已损坏（dpkg-deb 无法解析），请重新 clone 仓库"

# 3. 安装（apt 会顺带解决依赖；重复运行 = 重装同版本，幂等）
echo -e "${YELLOW}安装 $DEB ...${NC}"
sudo apt install -y "$DEB"

# 4. 验证：以 dpkg 数据库为准，不以 apt 输出为准
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
