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
│   SSH 需配合 filter ✅           SSH 兼容 ✅            │
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

这只是问题的第一层。即使通过 fake-ip-filter 解决了 DNS 层问题，流量仍可能走代理——而机场封禁出站 TCP/22（详见下文 Q4/Q5）。

### redir-host 模式（真实 IP）

```
github.com → 查询上游 DNS → 140.82.114.4 (真实 IP)
           ↓
应用连接到真实 IP
           ↓
Clash 通过 sniffing 识别域名
  ├─ HTTPS: 读取 SNI → github.com ✅
  └─ SSH: 无 SNI，但若域名在 fake-ip-filter 中，DNS 反向映射可恢复域名 ✅
```

**优点:**
- ✅ 所有协议兼容（SSH、P2P 等）
- ✅ 应用获得真实 IP

**缺点:**
- ❌ DNS 慢 (~50ms)
- ❌ 规则匹配可能不准（纯 TCP 无 SNI 时需依赖 DNS 反向映射）

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
TUN + Fake-IP + filter + DST-PORT,22,DIRECT
  ↓
github.com 用真实 IP → DNS 层解决 ✅
:22 流量直连 → 路由层解决 ✅
其他域名用 Fake-IP → 性能保持 ✅
```

fake-ip-filter 是必要条件（让 SSH 拿到真实 IP），但不充分——还需要路由层绕过代理（机场封 22）。

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

**根因**: fake-ip-filter 解决的是 DNS 层（让 SSH 拿到真实 IP），但流量路由是另一层问题。

实测发现：机场订阅全部 84 个节点封禁出站 TCP/22（通过 mihomo `/proxies/<node>/delay` 接口逐节点探测 portquiz.net:22，0/84 放行；443 对照 81/84 正常）。症状是 connect 成功后 0 字节即断开。这不是某个节点的问题，换节点无效。

**解决方案**:

1. **路由层**: 添加 `DST-PORT,22,DIRECT` 规则，让所有 :22 流量直连（脚本选项 1）

2. **SSH 层**: 配置 GitHub 走 ssh.github.com:443（脚本选项 2），这条路径通过代理正常工作

**完整检查清单:**
```bash
# 1. DNS 层检查
dig github.com  # 应返回真实 IP（20.x.x.x），而非 198.18.x.x

# 2. 路由层检查
grep "DST-PORT,22" ~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/*.js

# 3. SSH 层检查（可选但推荐）
grep "Host github.com" ~/.ssh/config

# 4. 测试连接
ssh -T git@github.com  # 应稳定成功
```

### Q5: 为什么需要 DST-PORT,22,DIRECT 而不是 GitHub 域名直连？

**核心原因: 机场封禁出站 TCP/22 是全局性的**

实测结果：84 个节点无一放行出站 22，不是“某些节点不支持”而是全部封禁。SSH 走代理的表现是 connect 成功后立即 0 字节断开。

mihomo 日志证实流量已正确路由：
```
[TCP] 198.18.0.1:40260 --> ssh.github.com:22 match DomainKeyword(github) using TaiShan Net[HK07]
```
流量到达了代理节点，是出口端丢弃了 TCP/22。同一节点 :443 和 :9418 正常。

**SSH 协议本身通过代理没有问题**——它是普通 TCP，代理节点可以正常转发。问题专属于端口 22，不是协议。

**域名匹配在 TUN 下对 SSH 仍然有效**——因为 github.com 在 fake-ip-filter 中，DNS 返回真实 IP，mihomo 的 DNS 反向映射恢复了域名，所以 DOMAIN-KEYWORD 规则能匹配 port-22 连接。

**为什么用 DST-PORT 而不是域名规则**:
- 机场封 22 是全局的，不只影哓 GitHub
- DST-PORT,22,DIRECT 一条规则覆盖所有 :22 目标
- Clash 规则只能选择出站，无法改写目标地址或端口（没有 REWRITE/DNAT 规则类型）

**直连 :22 在本地网络上是否可行**: 实测将 socket 绑定物理网卡 192.168.11.76，ssh.github.com:22、github.com:22、gitlab.com:22 等均正常返回 SSH banner（0.5-0.8s）。因此 DIRECT 路径在当前网络可用。

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

# 代理模式（使用全局 Script.js prepend 规则）
prepend-rules:
  # 出站 TCP/22 直连
  # 原因：机场全部 84 个节点封禁出站 TCP/22，走代理必然失败
  - DST-PORT,22,DIRECT
  # 中国大陆直连
  - GEOIP,CN,DIRECT,no-resolve
  - DOMAIN-SUFFIX,cn,DIRECT
  - DOMAIN-SUFFIX,com.cn,DIRECT
  # 本地网络直连
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  # 其他规则由订阅配置提供...
```

**配置说明:**

1. **TUN 配置**: 完整的 route-exclude-address，排除本地网络
2. **Fake-IP Filter**: 完整列表，包含：
   - 本地网络
   - 企业应用（飞书、钉钉等）
   - GitHub（主域名 + 通配符）
   - 中国镜像源（16个教育网 + 7个企业镜像站）
   - 协议过滤
3. **Prepend-Rules**: DST-PORT,22,DIRECT + 中国大陆直连 + 本地网络直连
   - 使用全局 Script.js 写入 prepend 规则
   - 所有出站 TCP/22 直连（机场封禁出站 22，走代理必失败）
   - 本地网络流量直连

**Clash 层面配置效果:**
- ✅ 全局透明代理（TUN）
- ✅ 高性能 DNS（Fake-IP）
- ✅ SSH 可用（GitHub 在 filter 中返回真实 IP + :22 直连）
- ✅ 精细分流（规则模式）
- ✅ 本地网络正常访问
- ✅ 企业应用正常工作
- ✅ 中国镜像源正常访问（apt/yum 等包管理器）

---

## 七、SSH 配置优化（可选但推荐）

Clash 配置（选项 1）的 `DST-PORT,22,DIRECT` 让 :22 流量直连，对大部分网络环境已够用。SSH 配置（选项 2）让 GitHub 改走 ssh.github.com:443，这条路径通过代理正常工作，不依赖直连 :22 是否可达。

### 为什么需要 SSH 配置？

**问题背景:**
- 机场封禁出站 TCP/22（84 节点 0 放行），走代理必失败
- 选项 1 让 :22 直连，当本地网络也封 22 时仍会失败
- GitHub 提供 ssh.github.com:443 作为替代入口

**解决方案:**
- 使用端口 443 连接 GitHub SSH，该端口通过代理正常转发

### SSH 配置内容

在 `~/.ssh/config` 中添加：

```bash
# GitHub SSH 走 443 端口
# 原因：机场封禁出站 TCP/22，443 通过代理正常转发
Host github.com ssh.github.com
    Hostname ssh.github.com
    Port 443
    User git
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
- ✅ 通过代理正常工作（机场只封 22，443 畅通）
- ✅ 不依赖直连 :22 是否可达
- ✅ GitHub 官方支持的替代入口

**与选项 1 的分工:**
- 选项 1 的 `DST-PORT,22,DIRECT` 覆盖所有 :22 目标（自己的服务器等）
- 选项 2 让 GitHub 改用 443，从此不走 :22，DIRECT 规则不会命中它
- 两者不冲突，组合使用效果最佳

### 完整解决方案总结

**Clash 配置（必需）:**
1. DNS 层: fake-ip-filter 让 GitHub 返回真实 IP
2. 网络层: TUN route-exclude-address 排除本地网络
3. 路由层: DST-PORT,22,DIRECT 让所有 :22 流量直连（机场封禁出站 22）

**SSH 配置（推荐）:**
4. SSH 层: GitHub 走 ssh.github.com:443，通过代理正常工作

四层配置共同作用，实现最稳定的 GitHub SSH 连接。

---

**参考:**
- [Clash 文档](https://github.com/Dreamacro/clash/wiki)
- [Clash.Meta 文档](https://wiki.metacubex.one/)
- [GitHub SSH over HTTPS](https://docs.github.com/en/authentication/troubleshooting-ssh/using-ssh-over-the-https-port)