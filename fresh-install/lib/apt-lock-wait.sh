#!/bin/bash
# 共享助手：等待 apt/dpkg 锁释放。被各安装脚本 source，不单独执行。
#
# 为什么需要它：新装系统首次开机时，GNOME Software（aptd）/ unattended-upgrades
# 常在后台跑全量系统更新并长时间持锁——内核升级 + NVIDIA DKMS 编译可达十几
# 分钟。此时任何 apt update/install 都会立刻报「无法获得锁」失败。等它结束
# 再继续，比失败中止再手动重跑省心。
#
# 接口：wait_for_apt_lock [超时秒数，默认 1800]
#   返回 0 = 锁已空闲；返回 1 = 超时仍被占用（调用方决定中止还是仅警告跳过）
#
# 只定义 wait_for_apt_lock 函数与 APT_LOCK_WAIT_HAVE_FUSER 两个全局名，
# 不读调用方任何变量（颜色等），在 set -e / set -u 下均安全。

# fuser 来自 psmisc（Ubuntu 官方源预装）。万一缺失则退化为不做等待：
# 行为与引入本助手之前一致，不会更糟
if command -v fuser >/dev/null 2>&1; then
    APT_LOCK_WAIT_HAVE_FUSER=yes
else
    APT_LOCK_WAIT_HAVE_FUSER=no
fi

wait_for_apt_lock() {
    [ "$APT_LOCK_WAIT_HAVE_FUSER" = "yes" ] || return 0

    local max_wait="${1:-1800}"
    # apt update 持 lists 锁，install/purge 持 dpkg 锁，daily_lock 归
    # apt-daily 服务；fuser 对任一文件命中即返回 0，任一被占用都等
    local all_locks=(
        /var/lib/apt/lists/lock
        /var/lib/apt/daily_lock
        /var/lib/dpkg/lock-frontend
        /var/lib/dpkg/lock
    )
    # 只保留存在的锁文件，避免 fuser 对缺失文件打印 stat 警告
    local locks=()
    local f
    for f in "${all_locks[@]}"; do
        if [ -e "$f" ]; then
            locks+=("$f")
        fi
    done
    if [ "${#locks[@]}" -eq 0 ]; then
        return 0
    fi

    # 持锁的都是 root 进程，普通用户的 fuser 看不到（/proc 不可读），必须 sudo；
    # 等待期间每 5 秒一次 sudo 调用会持续刷新 sudo 时间戳，等再久也不过期
    local sudo_cmd=""
    if [ "$(id -u)" -ne 0 ]; then
        sudo_cmd="sudo"
    fi

    local waited=0
    while $sudo_cmd fuser "${locks[@]}" >/dev/null 2>&1; do
        if [ "$waited" -eq 0 ]; then
            echo "  检测到另一个 apt/dpkg 进程正在运行（新装系统常在后台自动更新），等它完成..."
            $sudo_cmd fuser -v "${locks[@]}" 2>&1 | sed 's/^/    /' || true
        elif [ $((waited % 60)) -eq 0 ]; then
            echo "  仍在等待 apt 锁释放（已 ${waited}s，上限 ${max_wait}s）..."
        fi
        sleep 5
        waited=$((waited + 5))
        if [ "$waited" -ge "$max_wait" ]; then
            echo "  等待 ${max_wait}s 后 apt 锁仍被占用。" >&2
            return 1
        fi
    done
    if [ "$waited" -gt 0 ]; then
        echo "  apt 锁已释放（等了 ${waited}s），继续。"
    fi
    return 0
}
