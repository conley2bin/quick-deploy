# Clash Verge 配置优化工具

一键解决 Clash Verge TUN 模式下的 SSH、企业应用和本地网络访问问题。

## 问题

使用 Clash Verge 的 TUN + Fake-IP 模式时：

- **SSH 失败**: `git push` 报错 "Connection closed by 198.18.x.x"
- **企业应用无法访问**: 飞书、钉钉连接失败
- **本地网络不通**: 局域网设备无法访问

## 解决方案

两层配合解决 SSH 连接问题：

1. **DNS 层**: `fake-ip-filter` 让 GitHub 等域名返回真实 IP（否则 SSH 拿到 198.18.x.x 无法握手）
2. **路由层**: `DST-PORT,22,DIRECT` 让所有出站 22 端口走直连（机场全部 84 个节点封禁出站 TCP/22，走代理必然失败）

fake-ip-filter 是必要条件（给 SSH 真实 IP），但不充分——流量仍需绕过代理才能到达目标。

## 安装 Clash Verge Rev

如果尚未安装，使用自动化安装脚本（国内无需科学上网）：

```bash
./install-clash-verge.sh
```

**功能特性**：
- 自动检测系统和架构（Ubuntu/Debian/Fedora/CentOS/Arch）
- 自动安装必需工具（curl/wget/aria2）
- 测试多个 GitHub 镜像，选择最快
- 三级下载回退机制（curl → aria2 → wget）
- 自动文件完整性验证（大小 + 类型检查）
- 安装成功后显示配置指引

## 使用方法

```bash
# 1. 运行配置脚本
./clash-verge-tun-fix.sh

# 2. 选择菜单选项
#    选项 1: 一键优化 Clash 配置 (必需)
#    选项 2: 配置 SSH for GitHub (可选但推荐)

# 3. 重启 Clash Verge

# 4. 完成！直接使用 Git SSH
git push  # 无需额外配置
```

## 工作原理

**重要**: 本脚本修改的是 **当前订阅的 Merge 配置文件**，持久化保存，不受订阅更新影响。

### 什么是 Merge 配置？

Clash Verge 使用配置合并系统：
```
订阅配置 (远程) + Merge 配置 (本地) = 最终运行配置
```

- **订阅配置**: 从订阅链接下载，更新时会被覆盖
- **Merge 配置**: 订阅级别的本地增强配置，持久化保存
- **运行配置**: Clash 自动合并生成 `clash-verge.yaml`

### 为什么要修改 Merge 配置？

| 特性 | 订阅配置 | Merge 配置 |
|-----|---------|----------------|
| 持久性 | ❌ 更新时覆盖 | ✅ 永久保留 |
| 作用域 | 单个订阅 | 当前订阅 |
| 维护成本 | 高（每次更新需重配）| 低（一次配置永久生效） |
| 订阅更新 | 配置丢失 | 配置保留 |

## 脚本功能

### Clash 配置（选项 1）

自动在 **当前订阅的 Merge 配置文件** 中添加以下内容：

### 1. Fake-IP Filter (DNS 层)

让这些域名使用真实 DNS 解析：

- **本地网络**: `*.local`, `*.lan`
- **企业应用**: 飞书（`feishu.cn`, `*.feishu.cn`）、钉钉（`dingtalk.com`, `*.dingtalk.com`）、字节跳动（`bytedance.com`, `*.bytedance.com`）
- **GitHub**: 主域名和通配符（`github.com`, `*.github.com`, `githubusercontent.com`, `*.githubusercontent.com`, `githubassets.com`, `*.githubassets.com`, `github.io`, `*.github.io`）
- **中国镜像源（教育网）**: 清华、科大、浙大、北理工、北交大、华科、上交、兰大等 16 个教育网镜像站
- **中国镜像源（企业）**: 阿里云、华为云、腾讯云、网易、搜狐等 7 个企业镜像站
- **协议过滤**: `+._tcp`, `+._udp`

**关键点**: 同时配置主域名和通配符，因为 `*.github.com` 不匹配 `github.com`

**中国镜像源完整列表**:

教育网镜像站（16个）:
- 清华大学（`*.tsinghua.edu.cn`, `*.tuna.tsinghua.edu.cn`）
- 中国科技大学（`*.ustc.edu.cn`）
- 浙江大学（`*.zju.edu.cn`）
- 北京理工大学（`*.bit.edu.cn`）
- 北京交通大学（`*.bjtu.edu.cn`）
- 华中科技大学（`*.hust.edu.cn`）
- 上海交通大学（`*.sjtu.edu.cn`）
- 兰州大学（`*.lzu.edu.cn`）
- 大连东软信息学院（`*.neusoft.edu.cn`）
- 重庆大学（`*.cqu.edu.cn`）
- 南京大学（`*.nju.edu.cn`）
- 哈尔滨工业大学（`*.hit.edu.cn`）
- 中科院软件所（`*.iscas.ac.cn`）
- 南京邮电大学（`*.njupt.edu.cn`）
- 西安交通大学（`*.xjtu.edu.cn`）

企业镜像站（7个）:
- 阿里云（`*.aliyun.com`, `*.aliyuncs.com`）
- 华为云（`*.huaweicloud.com`）
- 腾讯云（`*.cloud.tencent.com`）
- 网易（`*.163.com`）
- 搜狐（`*.sohu.com`）
- 首都在线（`*.yun-idc.com`）

### 2. TUN 配置 (网络层)

排除本地网络流量（解决局域网访问）：

- `192.168.0.0/16` (私有网络 C 类)
- `10.0.0.0/8` (私有网络 A 类)
- `172.16.0.0/12` (私有网络 B 类)
- `127.0.0.0/8` (本地回环)

### 3. 直连规则 (路由层)

通过全局 Script.js prepend 规则，让特定流量绕过代理：

- `DST-PORT,22,DIRECT` — 所有出站 TCP/22 直连
- 中国大陆 IP/域名直连（GEOIP,CN 及常见国内站点）
- 本地网络: `192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, `*.local`

**为什么端口 22 必须直连**: 实测当前机场订阅全部 84 个节点封禁出站 TCP/22（逐节点用 portquiz.net:22 探测，0/84 放行；443 作为对照 81/84 正常）。症状是 TCP connect 成功后 0 字节即断开。走代理必然失败，DIRECT 是唯一可用路径。

**注意**: 当本地网络也封 22（少数情况）时，直连同样会超时。此时唯一方案是让目标服务监听非 22 端口。

### SSH 配置（选项 2 - 可选但推荐）

自动配置 `~/.ssh/config`，让 GitHub SSH 改走 443 端口：

#### 配置内容

```bash
Host github.com ssh.github.com
    Hostname ssh.github.com
    Port 443
    User git
```

#### 为什么需要 SSH 配置？

机场封禁出站 TCP/22 是全局性的（84 节点 0 放行），选项 1 的 `DST-PORT,22,DIRECT` 让 :22 走直连。但 GitHub 位于境外，直连 :22 依赖本地网络放行——大部分情况可行，少数网络环境仍有限制。

选项 2 将 GitHub SSH 改为 `ssh.github.com:443`，这条路径：
- 通过代理正常工作（代理只封 22，443 畅通）
- GitHub 从此不再使用端口 22，`DST-PORT,22,DIRECT` 规则不会命中它
- 两个选项组合使用不冲突

**结论**: 选项 1 覆盖所有 SSH 目标的通用情况；选项 2 专门让 GitHub 走代理上的 443，不依赖直连 :22 是否可达。

#### 对其他境外 SSH 主机的影响

你自己的境外服务器如果只监听 22，通过机场无解。修复方法：让 sshd 额外监听一个非 22 端口（443、2222 等），任何非 22 端口经代理正常转发。

#### 脚本功能

- 自动检测现有 SSH 配置
- 备份原配置（带时间戳）
- 安全添加 GitHub SSH 配置
- 设置正确的文件权限（600）

## 常见问题

**Q: 更新订阅后配置会失效吗？**
A: **不会**。脚本修改的是 Merge 配置，永久生效，不受订阅更新影响。

**Q: 切换到其他订阅后还生效吗？**
A: **不会自动生效**。Merge 配置是订阅级别的，切换订阅后需要重新运行脚本。

**Q: 如何查看当前订阅的 Merge 配置文件？**
A: 运行脚本，选择菜单选项 3 查看文件路径。

**Q: SSH 配置（选项 2）是必需的吗？**
A: **可选但推荐**。选项 1 让 :22 直连，对大部分网络环境已够用。选项 2 让 GitHub 走代理的 443 端口，不依赖本地 :22 是否可达，路径更稳定。两者组合使用不冲突。

**Q: 想了解技术原理？**
A: 查看 [CLASH-VERGE-GUIDE.md](CLASH-VERGE-GUIDE.md)，包含完整的技术架构和原理说明。

## 配置文件位置

```
# 订阅的 Merge 配置文件（脚本修改的目标）
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/<merge-uid>.yaml

# 订阅配置文件（不应手动修改）
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/<subscription-uid>.yaml

# 最终运行配置（自动生成）
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml

# SSH 配置文件（选项 2）
~/.ssh/config
```

## 故障排查

### SSH 连接仍然失败

按照以下步骤依次排查：

1. **确认 DNS 解析**:
   ```bash
   dig github.com
   # 应返回真实 IP（如 20.26.156.215），而非 Fake-IP（198.18.x.x）
   ```

2. **检查 fake-ip-filter 是否生效**:
   ```bash
   grep -A 20 "fake-ip-filter:" ~/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml
   # 应包含 github.com 和 *.github.com
   ```

3. **检查直连规则是否生效** (重要):
   ```bash
   # 检查全局脚本中是否包含 DST-PORT,22,DIRECT
   grep "DST-PORT,22" ~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/*.js
   ```

   如果没有该规则，SSH 流量会走代理，而机场封禁出站 22 导致失败。

4. **重启 Clash Verge**: 确保配置已重新加载

5. **测试 SSH 连接**:
   ```bash
   ssh -T git@github.com
   # 如果看到 "Hi username!" 说明成功
   ```

6. **配置 SSH（可选但推荐）**:
   ```bash
   # 运行脚本选择选项 2
   ./clash-verge-tun-fix.sh
   # 选择: 2. 配置 SSH for GitHub
   ```

**常见错误诊断**:
- `Connection closed by 198.18.x.x` → DNS 层问题，fake-ip-filter 未生效
- `Connection closed by 20.26.156.215` → 路由层问题，流量走了代理而机场封禁出站 22；需确认 DST-PORT,22,DIRECT 规则已生效
- connect 成功但 0 字节立即断开 → 典型的机场封 22 症状，确认选项 1 已应用或改用选项 2 走 443

### 企业应用无法访问

- 确认域名已添加到 fake-ip-filter
- 检查 TUN 模式是否正确排除本地网络
