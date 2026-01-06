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
