# Clash Verge 配置优化工具

为 Clash Verge Rev 生成本地 DNS、TUN 和路由增强配置，并配置 GitHub SSH。脚本写入的文件需要被 Verge 合并、加载后才会生效。

## 本地路由覆盖（推荐的日常维护入口）

路由的唯一可编辑来源是两个版本控制文件：

```text
rules/direct.yaml   # 只能写 DIRECT 目标
rules/proxy.yaml    # 只能写当前订阅已有的代理组目标；不写节点
```

两者都使用严格的 `version: 1`、`pre: [...]`、`post: [...]` YAML 结构。规则是完整的 Mihomo 规则字符串，含明确的策略目标；例如 `"DOMAIN,example.com,DIRECT"` 或 `"DOMAIN,example.com,Proxy"`。解析器使用 [PyYAML](rules/requirements.txt)，而不是用 shell 文本匹配猜 YAML；在可编辑环境中先安装 `python3 -m pip install -r clash-verge/rules/requirements.txt`（系统包 `python3-yaml` 也可以）。

```bash
# 以下两项不读取 Clash Verge 的 profiles registry，因此可在任意 cwd 预检
./clash-verge/tun-fix.sh rules check
./clash-verge/tun-fix.sh rules render > /tmp/Script.js

# 仅替换 profiles.yaml 中已登记的全局 Script.js；不改 Merge、DNS、TUN、SSH、
# 订阅级扩展、运行时 YAML，也不重载核心。
./clash-verge/tun-fix.sh rules apply
```

`apply` 先渲染候选 Script，再检查已登记的全局 Script 目标；未登记时会报错而不会写一个 Verge 永远不会加载的孤儿文件。已有非本工具脚本会要求确认并备份。完成后在 Verge GUI 中重载当前订阅/配置。`clash-verge.yaml` 是 Verge 生成的运行时输出，不能手改，也不是这些 YAML 的来源。

Mihomo 是**首条命中**，不会因为 `DOMAIN` 比 `DOMAIN-SUFFIX` 更具体而自动获胜。`pre` 的顺序为 `direct.pre`、`proxy.pre`，并移到订阅规则前；相同的订阅条目按完整规范化规则串去重并提升。订阅原有规则在第一个 `MATCH` 前保持原序；缺失的 `direct.post`、`proxy.post` 插入在该 `MATCH` 前，已有的相同 `post` 条目保留原位置。这样 `post` 是订阅规则的补充，不会覆盖订阅已有例外。生成器拒绝不存在的代理组、缺少 `MATCH`、未知 YAML 字段、同一选择器相反目标，以及同一阶段具有相反目标的可证明域名重叠（`DOMAIN`/`DOMAIN-SUFFIX`、后缀嵌套）。它不会猜测规则特异性或重排订阅。

迁移说明：旧脚本中六条 `forceTop` 规则现在在 `direct.pre`；其余原有本地 DIRECT 规则都在 `direct.post`。每条匹配器、目标和 `no-resolve` 选项均保留。宽泛补充规则现在会让位于订阅中即使没有完全相同字符串的更早例外；这是为消除旧版“全部 prepend”遮蔽订阅例外的有意语义变化。

## 只修 GitHub SSH

如果只想在本机拉取 GitHub 仓库，不必运行会修改多个站点路由的“一键优化”。需要配合两处设置。

### 1. 指定 GitHub SSH 的直连路由

在所用订阅的 **Rules 扩展**中加入：

```yaml
prepend:
  - DOMAIN,ssh.github.com,DIRECT

append: []
delete: []
```

保留文件中已有的其他条目，不要覆盖它们。Rules 扩展文件由 `profiles.yaml` 中该订阅的 `option.rules` 绑定；不要把扩展直接写进会被订阅更新覆盖的原始订阅文件。切换订阅时，需要对应订阅也有这条规则。

保存后让 Verge 重新生成并加载配置。这个精确域名规则只改变 GitHub SSH 的出站路径；`github.com` 网站、API、下载等仍按原有规则处理。

`DST-PORT,22,DIRECT` 只匹配目标端口 22，**不会匹配 `ssh.github.com:443`**。SSH 本身不要求代理，改端口本身也不决定走直连还是代理。

### 2. 配置 SSH

菜单选项 2 生成如下 GitHub 专用设置；密钥路径应与自己的 GitHub 密钥一致：

```sshconfig
Host github.com ssh.github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/conley
    IdentitiesOnly yes
    IPQoS none
    ConnectTimeout 8
```

这里不设置 `ProxyCommand` 或 `ProxyJump`，不依赖其他机器。已有的跳板设置可能来自后面的 `Host` 或 `Include`，需要用 `ssh -G` 检查最终解析值。脚本不会删除不属于它的用户配置块。

`IPQoS none` 禁用 SSH 设置的 IP 优先级标记。在复现过的 TUN 环境中，同一域名、地址、密钥和 `DIRECT` 路由下，默认 QoS 在认证后切换标记并停顿；只改成 `IPQoS none` 后，Git 取回了远端分支信息，再切回默认值又复现停顿。它是针对这个 QoS 敏感问题的配置修正，不是“SSH 必须用代理”或“代理商故意封 SSH”的证据。

`ConnectTimeout` 限制建立连接和初始握手，**不是整个 Git 命令的超时**。

### 3. 验证默认 Git 操作和实际路由

```bash
# 查看最终 SSH 设置，不建立网络连接
ssh -G github.com | grep -E '^(hostname|port|ipqos|proxycommand|proxyjump) '

# 在目标仓库中验证权限和传输，不切分支、不合并内容
git ls-remote origin
git fetch --dry-run origin
```

预期 `hostname ssh.github.com`、`port 443`、`ipqos none none`，且没有有效的跳板设置。`ssh -T git@github.com` 的欢迎消息只验证账号认证，不证明某个具体仓库的访问权限；GitHub 的该命令通常以退出码 1 结束。

在 Clash 的连接列表中，确认这次新建的 `ssh.github.com` 连接命中 `Domain` 规则、出站为 `DIRECT`。只看到配置文件中有一行规则还不够。

使用默认 Unix 控制套接字时，可只读查看相关运行规则：

```bash
curl -fsS --unix-socket /tmp/verge/verge-mihomo.sock http://localhost/rules \
  | python3 -c 'import json,sys
for i,r in enumerate(json.load(sys.stdin)["rules"]):
    if r.get("payload") in ("ssh.github.com", "github.com", "22"):
        print(i, r.get("type"), r.get("payload"), r.get("proxy"))'
```

专用 `ssh.github.com → DIRECT` 规则应排在更宽泛的 `github.com → Proxy` 规则之前。

### Fake-IP 与直连可以同时工作

`198.18.x.x` 或 `fdfe:dcba:9876::/48` 地址可以是本机 TUN 使用的 Fake-IP。Mihomo 根据映射恢复域名，再按规则用本机出口建立连接；看到 Fake-IP 本身并不说明 DNS 错误，也不代表流量一定经过境外代理节点。

这条 GitHub SSH 域名规则需要保留 `ssh.github.com` 的域名上下文。不要用 `*.github.com` 等 `fake-ip-filter` 条目把它排除掉。原始 SSH 没有 TLS SNI，不能指望 TLS 嗅探把丢失的域名补回来。脚本保留 GitHub 主站和资源域名原有的精确过滤，但不再生成 `*.github.com` 过滤。

## 安装 Clash Verge Rev

仓库内置 2.5.2 的 amd64 安装包：

```bash
./install.sh
```

安装器校验架构与安装包，并用 dpkg 查询安装结果。内置包不需要另从 GitHub Releases 下载；系统依赖是否需要联网取决于本机状态。其他架构请使用官方对应安装包。

## 完整脚本的使用范围

```bash
./tun-fix.sh
```

- **选项 1：一键优化 Clash 配置。** 生成全局 Merge 和 Script，涉及 DNS、TUN 本地网段排除、GitHub SSH、飞书/Lark、模型网关和国内站点路由。会备份并清空已绑定的订阅级 Merge，以避免其覆盖全局增强。只修 GitHub 时不必执行这一整套操作。
- **选项 2：配置 GitHub SSH。** 备份后更新 `~/.ssh/config` 中脚本管理的块，保留其他用户块；检查解析出的端口、QoS 和跳板状态。它不替代 Clash 路由加载。
- **选项 3：查看配置路径。** 从 `profiles.yaml` 查找实际绑定的文件名。
- **选项 4：备份管理。** 查看、恢复或按提示清理脚本备份。

配置生成后，通过 Verge 重载。随后核对运行规则和实际 Git 请求，不要把菜单的“已写入”提示当成网络验证结果。

## 配置的来源与生效链

```text
本地订阅 Rules 扩展 / 全局 Merge、Script
              + 原始订阅
                    ↓ Verge 合并生成
              clash-verge.yaml
                    ↓ 加载
              Mihomo 运行规则
```

全局 Merge 和 Script 的文件名由 `profiles.yaml` 中 `Merge`、`Script` 条目决定，通常是 `profiles/Merge.yaml`、`profiles/Script.js`。**条目已登记不等于文件仍存在，也不等于运行中的核心已加载。** 缺失文件要先恢复或重新生成；仅重载旧运行文件不会自动恢复它们。

菜单选项 1 使用全局扩展，适用于加载这些扩展的订阅；手动添加的订阅级 Rules 则只属于其绑定的订阅。订阅更新通常保留本地扩展，但删除本地文件、改变绑定或未加载都会使增强失效。

常见位置：

```text
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/<rules-uid>.yaml
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Merge.yaml
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml
~/.ssh/config
```

原始订阅与运行 YAML 可能包含订阅凭据、节点密码等信息。排查时只输出必要字段，不要公开整份配置。

## 仍然卡住时

1. 用 `ssh -G` 确认目标、端口、QoS 和跳板设置。
2. 确认本地增强文件存在、绑定正确，并出现在 **运行规则**中。
3. 在新建连接中确认实际命中规则和出站链。
4. 若认证成功后停顿，保持相同目标和路由比较 QoS 设置；若仅 IPv4 路径成功，再单独比较地址族。每次只改一个变量。
5. 用 `git ls-remote` 验证目标仓库，而不是仅看端口连通或 SSH 欢迎消息。

`Connection closed`、超时和 Fake-IP 地址本身都不足以定位故障。多个节点测试共享同一套本机 TUN，也不能单凭全部失败认定出口策略。`ls-remote` 耗时包含握手、服务端处理等，不能当作大文件带宽。

## 离线回归检查

```bash
python3 clash-verge/tests/github-ssh.py
```

从仓库根目录执行。需要 Python 3、Bash、OpenSSH 和 Node.js；测试只在专用临时目录生成配置、解析 SSH 设置和运行规则合并函数，不连接远端、不修改真实 HOME 或代理设置。
