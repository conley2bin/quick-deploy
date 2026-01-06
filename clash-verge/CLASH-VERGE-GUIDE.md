# Clash Verge 技术原理指南

理解 Clash Verge 的三个核心概念及其关系。

---

## 完整流程架构图

```
用户应用发起网络请求
        ↓
┌───────────────────────────────────────────────────────┐
│  第一层：网络设置 (流量如何被拦截)                      │
├───────────────────────────────────────────────────────┤
│                                                       │
│  [系统代理模式]              [TUN 虚拟网卡模式]        │
│   应用主动使用代理             操作系统网络层拦截      │
│   127.0.0.1:7890              全局透明拦截            │
│   仅 HTTP/HTTPS/SOCKS         支持 TCP/UDP/ICMP       │
│        │                            │                 │
│        └────────────┬───────────────┘                 │
│                     ↓                                 │
│              流量到达 Clash                            │
└───────────────────────────────────────────────────────┘
                      ↓
┌───────────────────────────────────────────────────────┐
│  第二层：DNS 模式 (域名如何解析)                       │
├───────────────────────────────────────────────────────┤
│                                                       │
│  [Fake-IP 模式]              [redir-host 模式]        │
│   返回虚拟 IP (198.18.x.x)     返回真实 IP            │
│   DNS 查询 ~1ms                DNS 查询 ~50ms         │
│   SSH 不兼容 ❌                 SSH 兼容 ✅            │
│        │                            │                 │
│        └────────────┬───────────────┘                 │
│                     ↓                                 │
│           [Fake-IP Filter 混合]                       │
│    特定域名用真实 IP，其他用 Fake-IP                   │
│    (本项目的核心方案) ⭐                               │
│                     │                                 │
│                     ↓                                 │
│          Clash 知道目标域名和 IP                       │
└───────────────────────────────────────────────────────┘
                      ↓
┌───────────────────────────────────────────────────────┐
│  第三层：代理模式 (流量如何路由)                       │
├───────────────────────────────────────────────────────┤
│                                                       │
│  用户在 Clash Verge 中选择一个模式（互斥选择）:         │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  [规则模式 Rule] ⭐ 推荐                     │     │
│  │   根据规则列表匹配:                          │     │
│  │   - DOMAIN-SUFFIX,github.com,Proxy          │     │
│  │   - IP-CIDR,192.168.0.0/16,DIRECT           │     │
│  │   - GEOIP,CN,DIRECT                         │     │
│  │   - MATCH,Proxy                             │     │
│  └─────────────────────────────────────────────┘     │
│                       或                              │
│  ┌─────────────────────────────────────────────┐     │
│  │  [全局模式 Global]                           │     │
│  │   所有流量 → Proxy                           │     │
│  └─────────────────────────────────────────────┘     │
│                       或                              │
│  ┌─────────────────────────────────────────────┐     │
│  │  [直连模式 Direct]                           │     │
│  │   所有流量 → DIRECT                          │     │
│  └─────────────────────────────────────────────┘     │
│                       ↓                               │
│              路由决策完成                              │
└───────────────────────────────────────────────────────┘
                      ↓
              ┌──────┴──────┐
              ↓             ↓
          走代理节点      直接连接
        (Proxy Group)     (DIRECT)
```

---

## 一、网络设置（第一层：拦截流量）

决定 Clash **如何拦截**系统的网络流量。

### 系统代理模式

```
应用 → 查系统代理设置 → 127.0.0.1:7890 → Clash
```

- **特点**: 轻量、需应用支持代理
- **限制**: 只能劫持 HTTP/HTTPS/SOCKS
- **场景**: 日常浏览器上网

### TUN 虚拟网卡模式

```
应用 → 操作系统网络栈 → TUN 虚拟网卡拦截 → Clash
```

- **特点**: 全局透明、支持所有协议（TCP/UDP/ICMP）
- **优势**: 不需要应用配置，真正的"透明代理"
- **场景**: 游戏、全局代理、不支持代理的应用

**对比:**

| 维度 | 系统代理 | TUN 模式 |
|------|---------|----------|
| 拦截方式 | 应用主动 | 系统层拦截 |
| 协议支持 | HTTP/HTTPS | TCP/UDP/ICMP |
| 透明性 | 应用感知 | 完全透明 |

---

## 二、DNS 模式（第二层：解析域名）

在拦截流量后，决定**域名如何解析为 IP**。

### Fake-IP 模式

```
github.com → 立即返回 198.18.0.26 (虚拟 IP)
           ↓
应用连接到虚拟 IP
           ↓
Clash 查映射表: 198.18.0.26 → github.com
           ↓
现在才解析真实 IP → 建立连接
```

**优点:**
- ✅ DNS 极快 (~1ms)
- ✅ 规则匹配 100% 准确（完整保留域名）
- ✅ 防 DNS 污染

**缺点:**
- ❌ SSH 协议不兼容（需要真实 IP 握手）
- ❌ 部分应用不兼容

**为什么 SSH 会失败？**

```
SSH 客户端 → 解析 github.com → 198.18.0.26 (虚拟 IP)
           ↓
       连接到虚拟 IP 的 22 端口
           ↓
       SSH KEX 握手需要真实端点
           ↓
       虚拟 IP 无法完成握手 → 失败 ❌
       "Connection closed by 198.18.0.26"
```

### redir-host 模式（真实 IP）

```
github.com → 查询上游 DNS → 140.82.114.4 (真实 IP)
           ↓
应用连接到真实 IP
           ↓
Clash 尝试 sniffing 识别域名
  ├─ HTTPS: 读取 SNI → github.com ✅
  └─ SSH: 无域名信息 → 只知道 IP ❌
```

**优点:**
- ✅ 所有协议兼容（SSH、P2P 等）
- ✅ 应用获得真实 IP

**缺点:**
- ❌ DNS 慢 (~50ms)
- ❌ 规则匹配可能不准（SSH/纯 TCP 无法识别域名）

### Fake-IP Filter（混合方案）⭐

**本项目的核心解决方案**

```yaml
dns:
  enhanced-mode: fake-ip
  fake-ip-filter:
    - '*.github.com'    # 这些用真实 DNS
    - '*.feishu.cn'
```

**工作流程:**

```
example.com (不在 filter)
  → Fake-IP (198.18.0.1) → 快速 ✅

github.com (在 filter 中)
  → 真实 DNS (140.82.114.4) → SSH 可用 ✅
  → TUN 仍拦截 → 规则匹配 → 走代理 ✅
```

**优势:**
- ✅ 保持 Fake-IP 性能（大部分域名）
- ✅ 解决 SSH 兼容性（特定域名）
- ✅ 规则仍 100% 准确

---

## 三、代理模式（第三层：路由流量）

在知道域名和 IP 后，决定**流量如何路由**。

### 规则模式（Rule）⭐ 推荐

```yaml
rules:
  - IP-CIDR,192.168.0.0/16,DIRECT    # 本地网络直连
  - DOMAIN-SUFFIX,github.com,Proxy   # GitHub 走代理
  - GEOIP,CN,DIRECT                  # 国内直连
  - MATCH,Proxy                      # 其他走代理
```

**匹配流程:**
```
流量到达 → 从上到下检查规则 → 第一条匹配 → 执行动作
```

**优点:**
- ✅ 精细控制
- ✅ 节省流量（国内直连）
- ✅ 性能最优

### 全局模式（Global）

```
所有流量 → 强制走代理
```

- ❌ 国内网站也绕路（变慢）
- ❌ 浪费流量
- ⚠️ 本地网络无法访问

**不推荐日常使用**

### 直连模式（Direct）

```
所有流量 → 不走代理
```

相当于关闭代理，临时测试用。

---

## 四、概念关系总结

### 处理顺序

```
1. 网络设置 → 拦截流量
        ↓
2. DNS 模式 → 解析域名为 IP
        ↓
3. 代理模式 → 决定路由（代理/直连）
```

### 最佳配置组合

```
网络设置: TUN 模式          (全局透明)
    +
DNS 模式: Fake-IP + filter  (性能 + 兼容)
    +
代理模式: 规则模式          (精细分流)
```

**这就是本项目的配置方案！**

---

## 五、常见问题

### Q1: 为什么需要 Fake-IP Filter？

**问题:**
```
TUN + Fake-IP + 规则模式
  ↓
SSH 失败 (虚拟 IP 无法握手)
```

**解决:**
```
TUN + Fake-IP + filter + 规则模式
  ↓
github.com 用真实 IP → SSH 成功 ✅
其他域名用 Fake-IP → 性能保持 ✅
```

### Q2: filter 后规则还能匹配吗？

**能！** Clash 仍然知道域名。

```
github.com → 真实 DNS (140.82.114.4)
           ↓
Clash 记录: 140.82.114.4 来自 github.com
           ↓
规则匹配: DOMAIN-SUFFIX,github.com,Proxy ✅
```

### Q3: 为什么不直接用 redir-host？

**性能差异:**

```
访问 100 个新域名:
  Fake-IP: 100 × 1ms = 100ms
  redir-host: 100 × 50ms = 5000ms

性能差距: 50 倍！
```

**Fake-IP + filter 两全其美:**
- 大部分域名用 Fake-IP（快）
- 少数特殊域名用真实 IP（兼容）

### Q4: 配置了 fake-ip-filter，SSH 仍然失败？

**可能的原因:**

1. **只配置了 DNS 层，忘记配置路由层**

```
问题: fake-ip-filter 返回真实 IP，但流量仍走代理
原因: 规则中 GitHub 配置为 Proxy 而非 DIRECT
解决: 添加 prepend-rules，配置 GitHub 为 DIRECT
```

2. **SSH 端口 22 不稳定**

```
问题: 有时成功，有时失败（间歇性）
原因: GitHub 端口 22 有约 2% 失败率 + ControlMaster 连接复用问题
解决: 配置 ~/.ssh/config，使用端口 443 + 禁用 ControlMaster
```

**完整检查清单:**
```bash
# 1. DNS 层检查
dig github.com  # 应返回真实 IP（20.x.x.x），而非 198.18.x.x

# 2. 路由层检查
grep "github.com,DIRECT" ~/.local/share/.../profiles/[merge-uid].yaml

# 3. SSH 层检查（可选但推荐）
grep "Host github.com" ~/.ssh/config

# 4. 测试连接
ssh -T git@github.com  # 应稳定成功
```

### Q5: 为什么 GitHub 需要 DIRECT 而不是 Proxy？

**SSH 协议特性:**
- SSH 需要端到端的直接连接
- 大多数代理节点（HTTP/SOCKS5）不支持 SSH 协议转发
- 即使 DNS 返回真实 IP，走代理仍会失败

**技术原因:**
```
SSH 握手过程:
  客户端 → SSH KEX (密钥交换) → 服务器
  ↓
  需要直接 TCP 连接，不能经过 HTTP/SOCKS5 代理层
```

**其他协议对比:**
- HTTPS: 可以走 HTTP/SOCKS5 代理 ✅
- SSH: 不能走 HTTP/SOCKS5 代理 ❌
- Git over HTTPS: 可以走代理 ✅
- Git over SSH: 必须直连 ⚠️

---

## 六、本项目配置

```yaml
# 网络设置
tun:
  enable: true
  stack: system
  auto-route: true
  auto-detect-interface: true
  exclude-routes:
    - 192.168.0.0/16
    - 10.0.0.0/8
    - 172.16.0.0/12
    - 127.0.0.0/8

# DNS 模式
dns:
  enhanced-mode: fake-ip
  fake-ip-filter:
    # 本地网络
    - '*.local'
    - '*.lan'
    # 企业应用
    - '*.feishu.cn'
    - '*.larkoffice.com'
    - '*.bytedance.com'
    - '*.dingtalk.com'
    # GitHub (SSH 支持)
    - 'github.com'
    - '*.github.com'
    - 'githubusercontent.com'
    - '*.githubusercontent.com'
    - 'githubassets.com'
    - '*.githubassets.com'
    - 'github.io'
    - '*.github.io'
    # 中国镜像源（教育网）
    - '*.tsinghua.edu.cn'
    - '*.tuna.tsinghua.edu.cn'
    - '*.ustc.edu.cn'
    - '*.zju.edu.cn'
    - '*.bit.edu.cn'
    - '*.bjtu.edu.cn'
    - '*.hust.edu.cn'
    - '*.sjtu.edu.cn'
    - '*.lzu.edu.cn'
    - '*.neusoft.edu.cn'
    - '*.cqu.edu.cn'
    - '*.nju.edu.cn'
    - '*.hit.edu.cn'
    - '*.iscas.ac.cn'
    - '*.njupt.edu.cn'
    - '*.xjtu.edu.cn'
    # 中国镜像源（企业）
    - '*.aliyun.com'
    - '*.aliyuncs.com'
    - '*.huaweicloud.com'
    - '*.cloud.tencent.com'
    - '*.163.com'
    - '*.sohu.com'
    - '*.yun-idc.com'
    # 协议过滤
    - '+._tcp'
    - '+._udp'

# 代理模式（使用 prepend-rules 在订阅规则前添加）
prepend-rules:
  # GitHub SSH 直连（不走代理）
  # 原因：SSH 协议需要端到端连接，大多数代理节点不支持 SSH 协议转发
  - DOMAIN-SUFFIX,github.com,DIRECT
  - DOMAIN-SUFFIX,githubusercontent.com,DIRECT
  - DOMAIN-SUFFIX,githubassets.com,DIRECT
  - DOMAIN-SUFFIX,github.io,DIRECT
  # 本地网络直连
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  # 其他规则由订阅配置提供...
```

**配置说明:**

1. **TUN 配置**: 完整的 exclude-routes，排除本地网络
2. **Fake-IP Filter**: 完整列表，包含：
   - 本地网络
   - 企业应用（飞书、钉钉等）
   - GitHub（主域名 + 通配符）
   - 中国镜像源（16个教育网 + 7个企业镜像站）
   - 协议过滤
3. **Prepend-Rules**: GitHub 直连（DIRECT）+ 本地网络直连
   - 使用 `prepend-rules` 在订阅规则前添加
   - GitHub 流量不走代理（SSH 协议需要端到端直连）
   - 本地网络流量直连

**Clash 层面配置效果:**
- ✅ 全局透明代理（TUN）
- ✅ 高性能 DNS（Fake-IP）
- ✅ SSH 可用（GitHub 在 filter 中返回真实 IP）
- ✅ 精细分流（规则模式）
- ✅ 本地网络正常访问
- ✅ 企业应用正常工作
- ✅ GitHub 流量直连（不经过代理节点）
- ✅ 中国镜像源正常访问（apt/yum 等包管理器）

---

## 七、SSH 配置优化（可选但推荐）

虽然 Clash 配置已经支持 SSH（DNS 返回真实 IP + 流量直连），但为了进一步提高连接稳定性，推荐配置 SSH。

### 为什么需要 SSH 配置？

**问题背景:**
- GitHub SSH 在端口 22 上有约 2% 的间歇性失败率
- SSH ControlMaster（连接复用）可能导致"第一次成功，第二次失败"的问题
- 某些网络环境对端口 22 有限制

**解决方案:**
- 使用端口 443（HTTPS 端口）替代默认的 22
- 禁用 ControlMaster 避免连接复用问题

### SSH 配置内容

在 `~/.ssh/config` 中添加：

```bash
# GitHub SSH over HTTPS port (443) - 最可靠的解决方案
# 原因：
# 1. 端口 443 比端口 22 更稳定（GitHub 官方推荐用于防火墙受限环境）
# 2. 禁用 ControlMaster 避免连接复用导致的间歇性失败
Host github.com
    Hostname ssh.github.com
    Port 443
    User git
    # 禁用连接复用避免间歇性问题
    ControlMaster no
    ControlPath none
    ControlPersist no
```

### 配置方式

**方法 1: 使用脚本自动配置（推荐）**

运行脚本后，选择菜单选项 2：

```bash
./clash-verge-tun-fix.sh
# 选择 1: 一键优化 Clash 配置
# 选择 2: 配置 SSH for GitHub
```

脚本会：
- 自动检测现有配置
- 备份原配置（如果存在）
- 添加优化后的 SSH 配置
- 设置正确的文件权限

**方法 2: 手动配置**

```bash
# 1. 创建或编辑 SSH 配置
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/config

# 2. 添加上述配置内容

# 3. 设置权限
chmod 600 ~/.ssh/config
```

### 测试验证

```bash
# 测试 SSH 连接
ssh -T git@github.com

# 预期输出
Hi <your-username>! You've successfully authenticated, but GitHub does not provide shell access.
```

### 配置效果

**端口 443 的优势:**
- ✅ 更稳定（GitHub 服务器针对此端口优化）
- ✅ 绕过防火墙限制（大多数网络允许 443 端口）
- ✅ 官方推荐方案

**禁用 ControlMaster 的效果:**
- ✅ 避免连接复用导致的"第一次成功，后续失败"问题
- ✅ 每次连接独立，互不影响
- ⚠️ 轻微性能损失（每次都建立新连接）

### 完整解决方案总结

**Clash 配置（必需）:**
1. DNS 层: fake-ip-filter 让 GitHub 返回真实 IP
2. 网络层: TUN exclude-routes 排除本地网络
3. 路由层: prepend-rules 让 GitHub 流量直连（不走代理）

**SSH 配置（推荐）:**
4. SSH 层: 端口 443 + 禁用 ControlMaster

四层配置共同作用，实现最稳定的 GitHub SSH 连接。

---

**参考:**
- [Clash 文档](https://github.com/Dreamacro/clash/wiki)
- [Clash.Meta 文档](https://wiki.metacubex.one/)
- [GitHub SSH over HTTPS](https://docs.github.com/en/authentication/troubleshooting-ssh/using-ssh-over-the-https-port)