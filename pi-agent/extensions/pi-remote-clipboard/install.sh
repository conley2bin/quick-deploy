#!/usr/bin/env bash
# pi-remote-clipboard 安装脚本
# 每台机器都安装同一份：本机 provider + Pi 扩展 + SSH config remote forwarding。
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MODULE_NAME="pi-remote-clipboard"

# ---------- 路径 ----------
PROVIDER_SRC="$SCRIPT_DIR/src/provider.py"
REQUESTER_SRC="$SCRIPT_DIR/src/requester.py"
SSH_DETECT_SRC="$SCRIPT_DIR/src/ssh_detect.py"
CTRLV_SRC="$SCRIPT_DIR/src/ctrlv.py"
PUSH_SRC="$SCRIPT_DIR/src/push.py"

PROVIDER_DIR="$HOME/.local/lib/pi-remote-clipboard"
PROVIDER_BIN="$PROVIDER_DIR/provider.py"
REQUESTER_BIN="$PROVIDER_DIR/requester.py"
SSH_DETECT_BIN="$PROVIDER_DIR/ssh_detect.py"
CTRLV_BIN="$PROVIDER_DIR/ctrlv.py"
PUSH_BIN="$PROVIDER_DIR/push.py"

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/$MODULE_NAME.service"

PI_HOME="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
PI_EXT_DIR="$PI_HOME/extensions/$MODULE_NAME"
PI_EXT_SRC="$SCRIPT_DIR/pi-extension"

SSH_CONFIG="$HOME/.ssh/config"
SSH_CONFIG_BLOCK_BEGIN="# >>> fresh-install pi-remote-clipboard >>>"
SSH_CONFIG_BLOCK_END="# <<< fresh-install pi-remote-clipboard <<<"

# 需要自动 remote forwarding 的主机列表（空格分隔）。
# 仓库不携带任何设备名：优先取 PI_REMOTE_CLIPBOARD_HOSTS 环境变量；
# 否则安装时从本机 ~/.ssh/config 的既有 Host 条目自动发现（跳过通配符与公共代码托管站点）。
PROVIDER_PORT="37843"
REMOTE_FORWARD_PORT="37844"

# 从用户自己的 SSH config 发现登录型主机别名；跳过本模块托管块，避免自引用。
detect_ssh_hosts() {
    [ -f "$SSH_CONFIG" ] || return 0
    awk -v begin="$SSH_CONFIG_BLOCK_BEGIN" -v end="$SSH_CONFIG_BLOCK_END" '
        $0 == begin { inblock = 1; next }
        $0 == end   { inblock = 0; next }
        inblock { next }
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*Host[[:space:]]/ {
            for (i = 2; i <= NF; i++) {
                if ($i ~ /[*?!]/) continue
                if ($i ~ /^(github\.com|gitlab|gitlab\.com|bitbucket\.org|ssh\.github\.com)$/) continue
                if (!seen[$i]++) print $i
            }
        }
    ' "$SSH_CONFIG" | paste -sd' ' -
}

die() { echo "pi-remote-clipboard: $*" >&2; exit 1; }
warn() { echo "pi-remote-clipboard: 警告: $*" >&2; }

# ---------- 依赖检查 ----------
for cmd in python3 systemctl; do
    command -v "$cmd" >/dev/null 2>&1 || die "缺少必需命令: $cmd"
done

# ---------- 安装 provider ----------
mkdir -p "$PROVIDER_DIR"
cp "$PROVIDER_SRC" "$PROVIDER_BIN"
cp "$REQUESTER_SRC" "$REQUESTER_BIN"
cp "$SSH_DETECT_SRC" "$SSH_DETECT_BIN"
cp "$CTRLV_SRC" "$CTRLV_BIN"
cp "$PUSH_SRC" "$PUSH_BIN"
chmod 755 "$PROVIDER_BIN" "$REQUESTER_BIN" "$SSH_DETECT_BIN" "$CTRLV_BIN" "$PUSH_BIN"

# ---------- systemd user service ----------
mkdir -p "$SYSTEMD_USER_DIR"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=pi-remote-clipboard local clipboard provider
After=graphical-session.target

[Service]
Type=simple
ExecStart=$PROVIDER_BIN
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
if systemctl --user is-active --quiet "$MODULE_NAME.service"; then
    systemctl --user restart "$MODULE_NAME.service"
else
    systemctl --user enable --now "$MODULE_NAME.service" || warn "provider 启动失败；可稍后手动 systemctl --user start $MODULE_NAME"
fi

# ---------- Pi 扩展 ----------
if [ -d "$PI_EXT_SRC" ]; then
    mkdir -p "$PI_HOME/extensions"
    if [ -L "$PI_EXT_DIR" ]; then
        [ "$(readlink -f -- "$PI_EXT_DIR")" = "$PI_EXT_SRC" ] || die "Pi 扩展目录已存在且指向别处: $PI_EXT_DIR"
    elif [ -e "$PI_EXT_DIR" ]; then
        die "Pi 扩展目录已存在且不是本模块符号链接: $PI_EXT_DIR"
    else
        ln -s "$PI_EXT_SRC" "$PI_EXT_DIR"
    fi
    echo "pi-remote-clipboard: Pi 扩展已安装 -> $PI_EXT_DIR"
    echo "  在 Pi 空闲时执行 /reload 生效"
else
    warn "Pi 扩展源码目录不存在，跳过扩展安装: $PI_EXT_SRC"
fi

# ---------- SSH config remote forwarding ----------
# 让登录型主机的 ssh 连接自动带 -R，不需要用户记新命令。
mkdir -p "$(dirname "$SSH_CONFIG")"

SSH_HOSTS="${PI_REMOTE_CLIPBOARD_HOSTS:-$(detect_ssh_hosts)}"
if [ -z "${SSH_HOSTS// }" ]; then
    warn "未能从 $SSH_CONFIG 发现主机别名，跳过 remote forwarding 配置"
    warn "可设置 PI_REMOTE_CLIPBOARD_HOSTS 后重跑，例如："
    warn "  PI_REMOTE_CLIPBOARD_HOSTS='主机A 主机B' bash $0"
else
    echo "pi-remote-clipboard: 本次纳入 remote forwarding 的主机: $SSH_HOSTS"

if [ -f "$SSH_CONFIG" ] && grep -Fqx "$SSH_CONFIG_BLOCK_BEGIN" "$SSH_CONFIG" 2>/dev/null; then
    # 已有托管块：检查内容是否过期，过期则原地替换
    desired="$SSH_CONFIG_BLOCK_BEGIN
Host $SSH_HOSTS
    RemoteForward 127.0.0.1:$REMOTE_FORWARD_PORT 127.0.0.1:$PROVIDER_PORT
$SSH_CONFIG_BLOCK_END"
    current="$(awk -v b="$SSH_CONFIG_BLOCK_BEGIN" -v e="$SSH_CONFIG_BLOCK_END" '
        $0==b {inblock=1} inblock {printf "%s\n", $0; if ($0==e) inblock=0}
    ' "$SSH_CONFIG")"
    if [ "$current" = "$desired" ]; then
        echo "pi-remote-clipboard: SSH config 托管块已是最新，跳过"
    else
        echo ""
        echo "pi-remote-clipboard: SSH config 托管块将更新为（$SSH_CONFIG）："
        echo ""
        echo "$desired" | sed 's/^/  /'
        echo ""
        tmp="$(mktemp "$SSH_CONFIG.tmp.XXXXXX")"
        awk -v b="$SSH_CONFIG_BLOCK_BEGIN" -v e="$SSH_CONFIG_BLOCK_END" '
            $0==b {inblock=1; next} $0==e {inblock=0; next} !inblock {print}
        ' "$SSH_CONFIG" > "$tmp"
        printf '\n%s\n' "$desired" >> "$tmp"
        chmod --reference="$SSH_CONFIG" "$tmp"
        mv "$tmp" "$SSH_CONFIG"
        echo "pi-remote-clipboard: SSH config 已更新"
    fi
else
    echo ""
    echo "pi-remote-clipboard: 将写入 SSH config（$SSH_CONFIG）："
    echo ""
    echo "  $SSH_CONFIG_BLOCK_BEGIN"
    echo "  Host $SSH_HOSTS"
    echo "      RemoteForward 127.0.0.1:$REMOTE_FORWARD_PORT 127.0.0.1:$PROVIDER_PORT"
    echo "  $SSH_CONFIG_BLOCK_END"
    echo ""
    echo "  效果：ssh 到这些主机时自动带 remote forwarding，"
    echo "  远端 Pi 能回头请求本机剪贴板，无需记新命令。"
    echo ""

    tmp="$(mktemp "$SSH_CONFIG.tmp.XXXXXX")"
    if [ -f "$SSH_CONFIG" ]; then
        cat "$SSH_CONFIG" > "$tmp"
        printf '\n' >> "$tmp"
    fi
    cat >> "$tmp" <<EOF
$SSH_CONFIG_BLOCK_BEGIN
Host $SSH_HOSTS
    RemoteForward 127.0.0.1:$REMOTE_FORWARD_PORT 127.0.0.1:$PROVIDER_PORT
$SSH_CONFIG_BLOCK_END
EOF
    if [ -f "$SSH_CONFIG" ]; then
        chmod --reference="$SSH_CONFIG" "$tmp"
    else
        chmod 600 "$tmp"
    fi
    mv "$tmp" "$SSH_CONFIG"
    echo "pi-remote-clipboard: SSH config 已更新"
fi
fi

echo "pi-remote-clipboard: 安装完成"
echo "  provider: systemctl --user status $MODULE_NAME"
echo "  测试: python3 $SCRIPT_DIR/tests/test_all.py"
echo ""
echo "使用："
echo "  ssh <任一登录主机>   # 自动带 remote forwarding"
echo "  piox                  # 远端 Pi 中 Ctrl+V 按输入来源自动分流"
echo "                        Ctrl+Alt+V 强制粘贴对端剪贴板"
