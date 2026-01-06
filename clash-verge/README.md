# Clash Verge 配置优化工具

一键解决 Clash Verge TUN 模式下的 SSH、企业应用和本地网络访问问题。

## 问题

使用 Clash Verge 的 TUN + Fake-IP 模式时：

- **SSH 失败**: `git push` 报错 "Connection closed by 198.18.x.x"
- **企业应用无法访问**: 飞书、钉钉连接失败
- **本地网络不通**: 局域网设备无法访问

## 解决方案

通过配置 `fake-ip-filter` 让特定域名使用真实 DNS 解析，同时保持其他域名使用 Fake-IP 的高性能。

**工作原理**: TUN 模式下，Clash 会拦截所有 DNS 查询。fake-ip-filter 中的域名会被路由到真实 DNS 服务器，获取真实 IP 地址，从而解决 SSH 连接问题。

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

让 GitHub 流量直连，不走代理（解决 SSH 连接问题）：

- GitHub 相关域名: `github.com`, `githubusercontent.com`, `githubassets.com`, `github.io`
- 本地网络: `192.168.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, `*.local`

**为什么需要直连**: 即使 DNS 解析正常，SSH 流量仍可能被路由到代理节点，而代理节点通常不支持 SSH 协议

### SSH 配置（选项 2 - 可选但推荐）

自动配置 `~/.ssh/config`，进一步提高 SSH 连接稳定性：

#### 配置内容

```bash
Host github.com
    Hostname ssh.github.com
    Port 443                  # 使用 HTTPS 端口，更稳定
    User git
    ControlMaster no          # 禁用连接复用
    ControlPath none
    ControlPersist no
```

#### 为什么需要 SSH 配置？

1. **端口 443 更稳定**:
   - GitHub 端口 22 有约 2% 的间歇性失败率
   - 端口 443 (HTTPS) 是 GitHub 官方推荐的替代方案
   - 绕过某些网络对端口 22 的限制

2. **禁用 ControlMaster 避免间歇性问题**:
   - SSH ControlMaster（连接复用）会导致"第一次成功，后续失败"
   - 禁用后每次连接独立，互不影响

#### 脚本功能

- 自动检测现有 SSH 配置
- 备份原配置（带时间戳）
- 安全添加 GitHub SSH 优化配置
- 设置正确的文件权限（600）

## 常见问题

**Q: 更新订阅后配置会失效吗？**
A: **不会**。脚本修改的是 Merge 配置，永久生效，不受订阅更新影响。

**Q: 切换到其他订阅后还生效吗？**
A: **不会自动生效**。Merge 配置是订阅级别的，切换订阅后需要重新运行脚本。

**Q: 如何查看当前订阅的 Merge 配置文件？**
A: 运行脚本，选择菜单选项 3 查看文件路径。

**Q: SSH 配置（选项 2）是必需的吗？**
A: **可选但推荐**。Clash 配置（选项 1）已经支持 SSH，但选项 2 能进一步提高稳定性，避免间歇性失败。

**Q: 为什么有时 SSH 第一次成功，后续失败？**
A: 这是 SSH ControlMaster（连接复用）导致的。运行脚本选项 2 配置 SSH，禁用 ControlMaster 即可解决。

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
   grep "github.com,DIRECT" ~/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml
   # 应该有: - DOMAIN-SUFFIX,github.com,DIRECT
   ```

   如果没有直连规则，即使 DNS 正常，SSH 流量仍会走代理导致失败。

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
- `Connection closed by 20.26.156.215` → 路由层问题，缺少直连规则或代理不支持 SSH
- 间歇性失败（时好时坏）→ SSH 层问题，需要配置 SSH（脚本选项 2）

### 企业应用无法访问

- 确认域名已添加到 fake-ip-filter
- 检查 TUN 模式是否正确排除本地网络
