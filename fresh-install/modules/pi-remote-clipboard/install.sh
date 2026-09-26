#!/usr/bin/env bash
# pi-remote-clipboard 安装脚本
# 每台机器都安装同一份：本机 provider + Pi 扩展 + SSH remote forwarding 函数。
set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MODULE_NAME="pi-remote-clipboard"

# ---------- 路径 ----------
PROVIDER_SRC="$SCRIPT_DIR/src/provider.py"
REQUESTER_SRC="$SCRIPT_DIR/src/requester.py"
SSH_DETECT_SRC="$SCRIPT_DIR/src/ssh_detect.py"
CTRLV_SRC="$SCRIPT_DIR/src/ctrlv.py"

PROVIDER_DIR="$HOME/.local/lib/pi-remote-clipboard"
PROVIDER_BIN="$PROVIDER_DIR/provider.py"
REQUESTER_BIN="$PROVIDER_DIR/requester.py"
SSH_DETECT_BIN="$PROVIDER_DIR/ssh_detect.py"
CTRLV_BIN="$PROVIDER_DIR/ctrlv.py"

SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/$MODULE_NAME.service"

PI_HOME="${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}"
PI_EXT_DIR="$PI_HOME/extensions/$MODULE_NAME"
PI_EXT_SRC="$SCRIPT_DIR/pi-extension"

ZSHRC_BLOCK_BEGIN="# >>> fresh-install pi-remote-clipboard >>>"
ZSHRC_BLOCK_END="# <<< fresh-install pi-remote-clipboard <<<"

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
chmod 755 "$PROVIDER_BIN" "$REQUESTER_BIN" "$SSH_DETECT_BIN" "$CTRLV_BIN"

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
systemctl --user enable --now "$MODULE_NAME.service" || warn "provider 启动失败；可稍后手动 systemctl --user start $MODULE_NAME"

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

# ---------- .zshrc SSH remote forwarding 函数 ----------
ZSHRC="$HOME/.zshrc"
if [ -f "$ZSHRC" ] && [ ! -L "$ZSHRC" ]; then
    if grep -Fqx "$ZSHRC_BLOCK_BEGIN" "$ZSHRC" 2>/dev/null; then
        echo "pi-remote-clipboard: .zshrc 已有托管块，跳过"
    else
        tmp="$(mktemp "$ZSHRC.tmp.XXXXXX")"
        cat "$ZSHRC" > "$tmp"
        printf '\n' >> "$tmp"
        cat >> "$tmp" <<'HOOK'
# >>> fresh-install pi-remote-clipboard >>>
# ssh-clipboard：普通 ssh 的包装，额外带 remote forwarding，
# 让远端 Pi 的 Ctrl+V 能回头请求本机剪贴板。
# 用法：ssh-clipboard <host> [其他 ssh 参数...]
ssh-clipboard() {
    ssh -R 127.0.0.1:37843:127.0.0.1:37843 "$@"
}
# <<< fresh-install pi-remote-clipboard <<<
HOOK
        chmod --reference="$ZSHRC" "$tmp"
        mv "$tmp" "$ZSHRC"
        echo "pi-remote-clipboard: .zshrc 已注册 ssh-clipboard 函数"
    fi
elif [ -L "$ZSHRC" ]; then
    warn ".zshrc 是符号链接，跳过注册；请手动添加 ssh-clipboard 函数"
else
    warn ".zshrc 不存在，跳过注册；请手动添加 ssh-clipboard 函数"
fi

echo "pi-remote-clipboard: 安装完成"
echo "  provider: systemctl --user status $MODULE_NAME"
echo "  测试: python3 $SCRIPT_DIR/tests/test_all.py"
