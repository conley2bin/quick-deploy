#!/bin/bash
set -e

# 添加官方仓库密钥
echo "Adding Syncthing official repository key..."
sudo mkdir -p /etc/apt/keyrings
sudo curl -L -o /etc/apt/keyrings/syncthing-archive-keyring.gpg https://syncthing.net/release-key.gpg

# 添加 stable-v2 仓库
echo "Adding Syncthing repository..."
echo "deb [signed-by=/etc/apt/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing stable-v2" | sudo tee /etc/apt/sources.list.d/syncthing.list

# 设置仓库优先级
echo "Setting repository priority..."
printf "Package: *\nPin: origin apt.syncthing.net\nPin-Priority: 990\n" | sudo tee /etc/apt/preferences.d/syncthing.pref

# 安装
echo "Installing Syncthing..."
sudo apt update
sudo apt install -y syncthing

# 验证版本
echo "Verifying installation..."
syncthing --version

# 启用用户级服务
CURRENT_USER="${SUDO_USER:-$USER}"
echo "Enabling systemd service for user: $CURRENT_USER"
sudo systemctl enable syncthing@$CURRENT_USER.service
sudo systemctl start syncthing@$CURRENT_USER.service

# 检查服务状态
echo "Service status:"
sudo systemctl status syncthing@$CURRENT_USER.service --no-pager

echo ""
echo "Installation complete."
echo "Web interface: http://127.0.0.1:8384"
echo ""

# 使用说明
CURRENT_USER="${SUDO_USER:-$USER}"
echo "=========================================="
echo "Syncthing 使用说明"
echo "=========================================="
echo ""
echo "【服务管理】"
echo "  启动服务:   sudo systemctl start syncthing@$CURRENT_USER.service"
echo "  停止服务:   sudo systemctl stop syncthing@$CURRENT_USER.service"
echo "  重启服务:   sudo systemctl restart syncthing@$CURRENT_USER.service"
echo "  查看状态:   sudo systemctl status syncthing@$CURRENT_USER.service"
echo ""
echo "【开机自启】"
echo "  启用自启:   sudo systemctl enable syncthing@$CURRENT_USER.service"
echo "  禁用自启:   sudo systemctl disable syncthing@$CURRENT_USER.service"
echo "  查看自启:   systemctl is-enabled syncthing@$CURRENT_USER.service"
echo ""
echo "【本地访问】"
echo "  浏览器打开: http://127.0.0.1:8384"
echo ""
echo "【远程访问 - SSH 隧道】"
echo "  在本地机器（你的电脑）执行："
echo "    ssh -L 8385:127.0.0.1:8384 $CURRENT_USER@<服务器IP>"
echo ""
echo "  命令解释："
echo "    -L 8385:127.0.0.1:8384  建立端口转发隧道"
echo "       └─ 本地端口 8385 → 服务器 127.0.0.1:8384"
echo "    作用: 将服务器的 8384 端口映射到本地 8385 端口"
echo "    安全: 通过 SSH 加密传输，无需开放防火墙端口"
echo ""
echo "  保持 SSH 连接，本地浏览器访问:"
echo "    http://127.0.0.1:8385"
echo ""
echo "=========================================="
