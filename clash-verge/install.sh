#!/bin/bash

# Clash Verge Rev 自动安装脚本 (国内无需科学上网版本)
# 支持系统: Ubuntu/Debian (deb), CentOS/Fedora/RHEL (rpm), Arch Linux (AUR)
# 项目地址: https://github.com/clash-verge-rev/clash-verge-rev

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# GitHub 加速镜像列表 (优先级从高到低)
MIRRORS=(
    "https://mirror.ghproxy.com"
    "https://gh.api.99988866.xyz"
    "https://ghproxy.vip"
    "https://gh-proxy.com"
)

# 版本信息
REPO="clash-verge-rev/clash-verge-rev"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

# 打印带颜色的消息
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检测系统类型
detect_system() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VERSION=$VERSION_ID
    else
        print_error "无法检测操作系统类型"
        exit 1
    fi

    case "$OS" in
        ubuntu|debian|linuxmint|pop)
            PKG_TYPE="deb"
            PKG_MANAGER="apt"
            ;;
        fedora|rhel|centos|rocky|almalinux)
            PKG_TYPE="rpm"
            PKG_MANAGER="dnf"
            # CentOS 7 使用 yum
            if [ "$OS" = "centos" ] && [ "${VERSION%%.*}" -eq 7 ]; then
                PKG_MANAGER="yum"
            fi
            ;;
        arch|manjaro|endeavouros)
            PKG_TYPE="arch"
            PKG_MANAGER="pacman"
            ;;
        *)
            print_error "不支持的操作系统: $OS"
            print_info "支持的系统: Ubuntu, Debian, Fedora, CentOS, RHEL, Arch Linux"
            exit 1
            ;;
    esac

    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)
            FILE_ARCH="amd64"
            ;;
        aarch64|arm64)
            FILE_ARCH="arm64"
            ;;
        *)
            print_error "不支持的架构: $ARCH"
            exit 1
            ;;
    esac

    print_info "检测到系统: $OS $VERSION ($ARCH)"
    print_info "包管理器: $PKG_MANAGER, 包类型: $PKG_TYPE, 架构: $FILE_ARCH"
}

# 测试镜像可用性
test_mirror() {
    local mirror=$1
    local test_url="${mirror}/https://github.com"

    if curl -sL --max-time 5 -o /dev/null -w "%{http_code}" "$test_url" | grep -q "200\|301\|302"; then
        return 0
    else
        return 1
    fi
}

# 选择最快的镜像
select_mirror() {
    print_info "正在测试 GitHub 加速镜像..."

    for mirror in "${MIRRORS[@]}"; do
        print_info "测试镜像: $mirror"
        if test_mirror "$mirror"; then
            SELECTED_MIRROR="$mirror"
            print_info "选择镜像: $SELECTED_MIRROR"
            return 0
        fi
    done

    print_warn "所有镜像均不可用，将尝试直连 GitHub (可能速度较慢)"
    SELECTED_MIRROR=""
    return 1
}

# 获取最新版本信息
get_latest_version() {
    print_info "获取最新版本信息..."

    # 尝试使用镜像加速 API 请求
    if [ -n "$SELECTED_MIRROR" ]; then
        RELEASE_INFO=$(curl -sL "${SELECTED_MIRROR}/${API_URL}")
    else
        RELEASE_INFO=$(curl -sL "$API_URL")
    fi

    if [ -z "$RELEASE_INFO" ]; then
        print_error "无法获取版本信息"
        exit 1
    fi

    VERSION=$(echo "$RELEASE_INFO" | grep -o '"tag_name": *"[^"]*"' | head -1 | sed 's/"tag_name": *"//;s/"//')

    if [ -z "$VERSION" ]; then
        print_error "解析版本号失败"
        exit 1
    fi

    print_info "最新版本: $VERSION"
}

# 构建下载 URL
build_download_url() {
    local filename=""

    # 移除版本号中的 'v' 前缀（v2.4.3 -> 2.4.3）
    local version_number="${VERSION#v}"

    case "$PKG_TYPE" in
        deb)
            filename="Clash.Verge_${version_number}_${FILE_ARCH}.deb"
            ;;
        rpm)
            filename="Clash.Verge_${version_number}_${FILE_ARCH}.rpm"
            ;;
        arch)
            print_error "Arch Linux 请使用 AUR 安装:"
            print_info "  yay -S clash-verge-rev-bin"
            print_info "  或 paru -S clash-verge-rev-bin"
            exit 1
            ;;
    esac

    DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${filename}"

    # 如果有镜像，则使用镜像 URL
    if [ -n "$SELECTED_MIRROR" ]; then
        DOWNLOAD_URL="${SELECTED_MIRROR}/${DOWNLOAD_URL}"
    fi

    FILENAME="$filename"
    print_info "下载 URL: $DOWNLOAD_URL"
}

# 验证下载文件
verify_download() {
    local file_path=$1
    local min_size=50000000  # 50MB minimum

    if [ ! -f "$file_path" ]; then
        return 1
    fi

    local file_size=$(stat -c%s "$file_path" 2>/dev/null || stat -f%z "$file_path" 2>/dev/null)

    if [ "$file_size" -lt "$min_size" ]; then
        print_error "文件异常 (大小: $file_size 字节，小于 50MB)"

        # 检查是否为 HTML/JSON 错误页面
        if file "$file_path" | grep -qE "HTML|JSON"; then
            print_error "下载的是错误页面，不是安装包"
            return 1
        fi
        return 1
    fi

    print_info "文件验证通过 (大小: $file_size 字节)"
    return 0
}

# 使用 curl 下载
download_with_curl() {
    local url=$1
    local output=$2

    print_info "方法 1: 使用 curl 下载..."
    if curl -# -L --connect-timeout 15 --max-time 300 -o "$output" "$url"; then
        if verify_download "$output"; then
            return 0
        fi
    fi
    return 1
}

# 使用 aria2 下载
download_with_aria2() {
    local url=$1
    local output=$2

    print_info "方法 2: 使用 aria2 多线程下载..."

    # 检查 aria2 是否可用（应在 ensure_dependencies 已安装）
    if ! command -v aria2c &> /dev/null; then
        print_warn "aria2 未安装，跳过此方法"
        return 1
    fi

    # aria2: 16线程，1MB分片，30秒超时
    if aria2c -x 16 -s 16 -k 1M --connect-timeout=30 --timeout=300 \
              --allow-overwrite=true -o "$output" "$url"; then
        if verify_download "$output"; then
            return 0
        fi
    fi
    return 1
}

# 使用 wget 下载
download_with_wget() {
    local url=$1
    local output=$2

    print_info "方法 3: 使用 wget 重试机制下载..."

    if command -v wget &> /dev/null; then
        # wget: 无限重试，1秒等待，20秒读取超时
        if wget --tries=0 --retry-connrefused --waitretry=1 \
                --read-timeout=20 --timeout=15 --no-check-certificate \
                "$url" -O "$output"; then
            if verify_download "$output"; then
                return 0
            fi
        fi
    else
        print_warn "wget 未安装，跳过此方法"
    fi
    return 1
}

# 下载文件（多种方法自动回退）
download_file() {
    print_info "开始下载 Clash Verge Rev $VERSION ..."

    DOWNLOAD_PATH="/tmp/$FILENAME"

    # 方法 1: curl (快速，适合网络良好情况)
    if download_with_curl "$DOWNLOAD_URL" "$DOWNLOAD_PATH"; then
        print_info "下载完成 (方法: curl)"
        return 0
    fi

    print_warn "curl 下载失败，尝试其他方法..."
    rm -f "$DOWNLOAD_PATH"

    # 如果使用了镜像但失败，切换到 GitHub 直连
    if [ -n "$SELECTED_MIRROR" ]; then
        print_info "切换到 GitHub 直连..."
        SELECTED_MIRROR=""
        DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${FILENAME}"
        print_info "新 URL: $DOWNLOAD_URL"
    fi

    # 方法 2: aria2 (多线程，适合限速情况)
    if download_with_aria2 "$DOWNLOAD_URL" "$DOWNLOAD_PATH"; then
        print_info "下载完成 (方法: aria2)"
        return 0
    fi

    print_warn "aria2 下载失败，尝试最后方法..."
    rm -f "$DOWNLOAD_PATH"

    # 方法 3: wget (重试机制，适合不稳定网络)
    if download_with_wget "$DOWNLOAD_URL" "$DOWNLOAD_PATH"; then
        print_info "下载完成 (方法: wget)"
        return 0
    fi

    print_error "所有下载方法均失败"
    print_error "请检查网络连接或防火墙设置"
    exit 1
}

# 安装软件包
install_package() {
    print_info "开始安装 Clash Verge Rev..."

    case "$PKG_MANAGER" in
        apt)
            sudo apt install -y "$DOWNLOAD_PATH"
            ;;
        dnf|yum)
            sudo $PKG_MANAGER install -y "$DOWNLOAD_PATH"
            ;;
        *)
            print_error "不支持的包管理器: $PKG_MANAGER"
            exit 1
            ;;
    esac

    if [ $? -eq 0 ]; then
        print_info "安装成功!"
    else
        print_error "安装失败"
        exit 1
    fi
}

# 清理下载文件
cleanup() {
    if [ -f "$DOWNLOAD_PATH" ]; then
        print_info "清理下载文件..."
        rm -f "$DOWNLOAD_PATH"
    fi
}

# 显示后续步骤
show_next_steps() {
    echo ""
    print_info "===== 安装完成 ====="
    echo ""
    print_info "启动方式:"
    echo "  1. 图形界面: 在应用菜单中找到 'Clash Verge' 启动"
    echo "  2. 命令行: clash-verge"
    echo ""
    print_info "配置文件位置:"
    echo "  ~/.local/share/io.github.clash-verge-rev.clash-verge-rev/"
    echo ""
    print_info "推荐配置脚本 (来自本项目):"
    echo "  ./clash-verge-tun-fix.sh"
    echo ""
    print_warn "注意事项:"
    echo "  1. 首次启动需要导入订阅链接"
    echo "  2. TUN 模式需要 root 权限 (推荐使用)"
    echo "  3. 建议运行配置脚本优化 DNS 和路由规则"
    echo ""
}

# 检查并安装必需工具
ensure_dependencies() {
    print_info "检查必需的下载工具..."

    local missing_tools=()

    # 检查 curl
    if ! command -v curl &> /dev/null; then
        missing_tools+=("curl")
    fi

    # 检查 wget
    if ! command -v wget &> /dev/null; then
        missing_tools+=("wget")
    fi

    # aria2 可选，但有助于提高下载成功率
    if ! command -v aria2c &> /dev/null; then
        missing_tools+=("aria2")
    fi

    if [ ${#missing_tools[@]} -eq 0 ]; then
        print_info "所有下载工具已安装"
        return 0
    fi

    print_warn "缺少以下工具: ${missing_tools[*]}"
    print_info "正在自动安装..."

    case "$PKG_MANAGER" in
        apt)
            sudo apt update -qq || {
                print_error "apt update 失败"
                return 1
            }
            for tool in "${missing_tools[@]}"; do
                print_info "安装 $tool..."
                sudo apt install -y "$tool" || {
                    print_warn "$tool 安装失败，但可能不影响下载"
                }
            done
            ;;
        dnf|yum)
            for tool in "${missing_tools[@]}"; do
                print_info "安装 $tool..."
                sudo $PKG_MANAGER install -y "$tool" || {
                    print_warn "$tool 安装失败，但可能不影响下载"
                }
            done
            ;;
        *)
            print_error "不支持的包管理器: $PKG_MANAGER"
            print_info "请手动安装: ${missing_tools[*]}"
            return 1
            ;;
    esac

    print_info "依赖工具安装完成"
}

# 主流程
main() {
    echo "======================================"
    echo " Clash Verge Rev 自动安装脚本"
    echo " 国内无需科学上网版本"
    echo "======================================"
    echo ""

    # 检查是否为 root 用户 (安装需要 sudo)
    if [ "$EUID" -eq 0 ]; then
        print_warn "请不要以 root 用户运行此脚本"
        print_info "脚本会在需要时自动请求 sudo 权限"
        exit 1
    fi

    # 检测系统
    detect_system

    # 检查并安装必需工具
    ensure_dependencies

    # 选择镜像
    select_mirror

    # 获取版本信息
    get_latest_version

    # 构建下载 URL
    build_download_url

    # 下载文件
    download_file

    # 安装
    install_package

    # 清理
    cleanup

    # 显示后续步骤
    show_next_steps
}

# 捕获错误并清理
trap cleanup EXIT

# 运行主程序
main
