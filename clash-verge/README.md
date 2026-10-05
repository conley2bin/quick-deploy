# Clash Verge 本地路由工具

把「哪些流量直连、哪些走代理」放进两个可编辑的 YAML，生成已登记的全局 Script，
再在 Verge 里重载。更新规则不改订阅、Merge、DNS、TUN、运行 YAML 或 SSH；
只有单独选择“配置 GitHub SSH”才会修改 SSH 配置。

```bash
./clash-verge/tun-fix.sh          # 打开菜单；回车 = 更新规则
```

菜单：

```text
1. 更新直连/代理规则（默认，回车执行）   校验并渲染两个 YAML，替换已登记的全局 Script
2. 配置 GitHub SSH                     可选；只改 ~/.ssh/config 中本工具管理的块
3. 查看本机应用识别结果                 只读盘点五个受支持应用的路径与未解决原因
4. 恢复上次规则                        从全局 Script 最近的同目录备份恢复
0. 退出
```

菜单顶部始终打印本次实际使用的绝对路径：`rules/direct.yaml`、`rules/proxy.yaml`、
已登记的全局 Script，以及只读的 `profiles.yaml`/订阅文件。输入空行等于选项 1；
EOF 直接退出且不写任何文件；某个动作失败会打印原因并回到菜单，不会假装成功。

## `tun.stack` 被应用设置接管

如果 Verge 提示：

> 扩展配置或脚本写入了由应用设置接管的字段 tun.stack，这些值已被丢弃

说明扩展执行后得到的栈值与应用设置不同。带此提示的版本在生成末尾恢复应用拥有的值，
但**不会删除扩展文件中的赋值**。当前 `tun-fix.sh` 生成的 Script 只赋值 `config.rules`，
不写 `tun.stack`；更新规则不会清理其它 Merge/Script。旧优化器被移除时也没有清空
已安装的扩展，因此需要定位实际输入，不能凭这条提示认定是本工具或某个旧 Merge。

在**实际报错的机器**上处理：

1. 先核对实际运行的 Verge 版本与它使用的配置目录。打开 **设置 → 系统设置 →
   虚拟网卡模式旁的齿轮 → TUN 模式堆栈**；保留当前选择，无需为消除提示而保存。
   如果确实需要更换栈，在这里设置目标值并保存，不把扩展中被丢弃的值自动搬过来。
   保存的栈属于应用 `config.yaml` 的 `tun.stack`，TUN 开关另由应用设置管理。
2. 定位并备份确定写入该字段的全局或订阅级扩展。若是 Merge，**只删除 `tun` 下的
   `stack` 叶**，保留其它 TUN、DNS、规则和注释；不要删除整个 `tun` 块或清空 Merge。
   若是自定义 Script，只手动修改确认写栈的赋值，不执行或批量重写未知 JS。
   找不到来源时先核对输入，缺失的 Merge 文件不需要为了消除提示而创建。
3. 在 Verge 中**重新生成/重载配置**，核对应用栈选择、TUN 开关和运行配置。
   不改订阅、注册表或运行 YAML；重生成前不能声称核心已加载新配置。
   后续自定义 Script 若读取被删除的值，需要同时检查其逻辑。重复相同字段的提示可能被
   抑制，所以第二次没有提示不能单独证明扩展已清理。

版本边界：官方 v2.5.5–v2.5.7 有这条提示与生成末尾的 TUN 字段接管；仓库内置的
官方 **2.5.2 没有这条提示，也没有新版的 TUN 接管**。在 2.5.2 删除扩展栈值可能改变
有效栈，不能照搬上述操作。包版本、运行产物与截图来源需分别核实。

上游依据（固定 v2.5.7 源码）：[提示原文](https://github.com/clash-verge-rev/clash-verge-rev/blob/ea509b82363a40c3c32e951d7ce9d66d66da411f/src/locales/zh/profiles.json#L54)、
[生成末尾提示与接管](https://github.com/clash-verge-rev/clash-verge-rev/blob/ea509b82363a40c3c32e951d7ce9d66d66da411f/src-tauri/src/enhance/mod.rs#L938-L963)、
[GUI 保存栈设置](https://github.com/clash-verge-rev/clash-verge-rev/blob/ea509b82363a40c3c32e951d7ce9d66d66da411f/src/components/setting/mods/tun-viewer.tsx#L102-L134)。
2.5.2 的扩展顺序见[旧版本实现](https://github.com/clash-verge-rev/clash-verge-rev/blob/28f2efc504059b1dc75c793618b775c8e1b2a5f1/src-tauri/src/enhance/mod.rs#L685-L773)。

## 日常修改路由

只有两个文件需要编辑：

```text
rules/direct.yaml   # 只能写 DIRECT 目标
rules/proxy.yaml    # 只能写当前订阅已有的代理组名；不写节点
```

两者都是严格的 `version: 1` + `pre` / `post` 列表，条目可以混用完整 Mihomo 规则字符串
和五个受支持的应用声明。解析使用 [PyYAML](lib/requirements.txt)；可编辑机器上先装
`python3 -m pip install -r clash-verge/lib/requirements.txt`（系统包 `python3-yaml` 亦可）。

```yaml
# direct.yaml
version: 1
pre:
  - app: baidunetdisk
  - "DOMAIN,example.com,DIRECT"
post:
  - "DOMAIN-SUFFIX,cn,DIRECT"

# proxy.yaml：必须写明当前订阅已有的组名
version: 1
pre:
  - app: wemeet
    target: Proxy
post: []
```

改完执行菜单回车（或 `./clash-verge/tun-fix.sh rules apply`），然后在 Verge 中重载/重新
生成配置。**重载前运行中的核心仍是旧规则**；工具只报告写入成功，不会声称已经生效。

### 应用声明

受支持的 ID 只有 `baidunetdisk`（百度网盘）、`wemeet`（腾讯会议）、`feishu`（飞书）、
`wechat`（微信）、`spark-store`（星火应用商店）。声明只写 `app`（direct 里）或
`app` + `target`（proxy 里），没有路径、命令或任意名称字段。

每次更新做一次只读发现，把声明展开成该位置上的 `PROCESS-PATH` 规则：

- 证据来自固定 Debian 包 ID 的 dpkg 清单，以及指定 desktop 文件和相关的 `/proc`
  `comm`/`exe`；不扫描磁盘、不启动应用、不读私有数据。
- 包 launcher 不等于可归属主体：软链必须指向包清单内的原生 ELF，共享 runtime
  （`node`、`python3`、`aria2c`、shell、Wine）不会被归给应用。
- 百度网盘的 GUI 与 `netdisk_service` 会一起展开。
- Flatpak、AppImage、Wine、RPM 和任意桌面包装器会明确显示为 missing/unsupported，
  不会猜测。
- 声明的应用未安装、路径不可表示、或多处冲突时，更新在写任何文件之前失败。

安装或升级应用后需要重新更新，才能把新路径写进 Script。发现结果和生成结果都不证明
某个连接已经路由。

### 规则顺序与去重

Mihomo 按**首条命中**判定，`DOMAIN` 不会因为比 `DOMAIN-SUFFIX` 更具体而自动获胜。
生成结果固定为：`direct.pre` → `proxy.pre` → 订阅原有规则 → 缺失的 `post` → 第一个
`MATCH`。已有相同 `post` 条目保留原位置，所以 `post` 是订阅规则的补充，而不是覆盖。

解析器拒绝：未知 YAML 字段、未知应用 ID、未知 matcher、非法 payload、
proxy 目标写成内置策略（`DIRECT`/`REJECT`/…）、同一选择器相反目标、同阶段可证明的
域名重叠。支持的可编辑 matcher 是 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、
`DST-PORT`、`GEOIP`、`IP-CIDR`、`IP-CIDR6`、`PROCESS-NAME`、`PROCESS-PATH`；
`no-resolve` 只能作为最后一个字段出现在 `GEOIP`/`IP-CIDR`/`IP-CIDR6` 上。
`PROCESS-NAME` 是字面可执行文件**基名**，同名但无关的程序也会命中，它不是应用发现
失败时的回退。

### 默认直连：Tailscale

`direct.yaml` 的 `pre` 在 `DST-PORT,22` 之后固定带一组 Tailscale 直连：
`DOMAIN-SUFFIX,tailscale.com,DIRECT` 覆盖控制面/日志/DERP 服务域名，
`IP-CIDR,100.64.0.0/10` 与 `IP-CIDR6,fd7a:115c:a1e0::/48` 覆盖 tailnet 的 CGNAT 与
ULA 对端地址。这组规则只说明规则表会把之后的连接判给 `DIRECT`，不是「当前 SSH 或中继
流量已经直连」的运行证据：重载前已建立的连接可能继续沿用旧出口，要重连或看实际
socket/路由才能确认；自建 DERP 域名不在覆盖范围内。

### 迁移：清掉旧的订阅级重复规则

如果之前用订阅级 Rules 扩展手工加过同一条本地规则（例如
`DOMAIN,ssh.github.com,DIRECT`），更新会报错并列出精确的 `路径:行号:规则`。只手动删除
列出的本地扩展条目后重试；生成器不会改写订阅或扩展文件。删除 YAML 里的规则只停止
**本生成器**注入它，独立存在的订阅规则仍保留自己的行为。

更新前会检查**所有**已订阅 profile 绑定的本地 Rules 扩展，因此某个较早、非当前的
profile 绑定了已不存在或不可读的扩展文件时，更新同样会被阻止并报出该文件路径。这类
绑定需要在 Verge 里修好或删除后再重试：工具不会跳过、也不会自动改动你的 registry；
缺失文件本身也可能意味着该订阅的本地覆盖来源不明，所以这里选择停下来而不是猜。

## 写入范围与恢复

一次更新只做这些事：按 `profiles.yaml` 中 `Script` 条目定位目标 → 渲染候选 → 已有目标
先建立同目录唯一备份 → 原子替换。

- `rules/direct.yaml`、`rules/proxy.yaml` 永不被改写。
- 生成结果与现有目标逐字节相同时是空操作：不改写、不新建备份、mtime 与权限不变。
- 目标不存在时直接创建；目标存在但不是本工具生成的文件（首行缺少生成标记）会要求
  确认，EOF 视为取消。
- `profiles.yaml` 未登记 Script、登记类型或文件名不安全、目标目录不存在时，报错并且
  不创建任何孤儿文件。**不需要 Merge 登记**，路由只依赖 Script。
- 还原：菜单选项 4 只认「已登记 Script 文件名 + `.backup.YYYYMMDD_HHMMSS[.后缀]`」的
  同目录普通文件，选最新一个，显示备份与目标并确认；先把当前内容另存为新的唯一备份，
  再原子替换。没有可用备份时不删除当前 Script，只报告没有可恢复版本。
  恢复不回改两个 YAML 来源——之后再更新会按当前来源重新生成。
- 不自动重载核心，也不声称已经生效，请在 Verge 中手动重载/重新生成配置。

新备份名形如 `Script.js.backup.20260917_191624.aB3xY9`，时间戳后追加随机后缀并独占创建，
避免同一秒内的备份互相覆盖。恢复也兼容旧的无后缀、数字后缀备份；这些只是本工具的
同目录回滚点，不涉及订阅或云端。

## GitHub SSH（可选的独立动作）

只有菜单选项 2 会碰 `~/.ssh/config`；更新、恢复、应用识别都不会。脚本备份原文件后，
把下面这段原子写到文件顶部并保留其他用户的 Host/Include 配置：

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

写入后脚本用 `ssh -G github.com` 检查最终解析值（hostname/port/ipqos/跳板），这是静态
检查，不建立连接。`IPQoS none` 针对的是本机 TUN 路径上复现过的认证后停顿：同样的
域名、地址、密钥和 `DIRECT` 路由下，只有 QoS 设置不同，默认值会停顿，
`IPQoS none` 可以完成取回。它不设置 `ProxyCommand`/`ProxyJump`，也不代表 GitHub 被
封或必须走代理。真实权限与传输仍要自己验证：

```bash
ssh -G github.com | grep -E '^(hostname|port|ipqos|proxycommand|proxyjump) '
git ls-remote origin
```

GitHub SSH 走 `ssh.github.com:443`，`DST-PORT,22` 不会匹配它，所以
`direct.yaml` 里需要 `DOMAIN,ssh.github.com,DIRECT`（默认已包含）。这条规则依赖
`ssh.github.com` 的域名上下文：不要在 `fake-ip-filter` 里用 `*.github.com` 把它排除，
原始 SSH 没有 TLS SNI，嗅探无法补回丢失的域名。

## 安装 Clash Verge Rev

仓库内置 2.5.2 的 amd64 安装包，`./install.sh` 会校验架构、用 dpkg 安装并回查结果。
其他架构请用官方安装包。

配置文件常见位置：

```text
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml     # 注册表
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js # 生成的路由
~/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml   # Verge 生成，不要手改
~/.ssh/config
```

订阅和运行 YAML 可能包含凭据或节点信息；排查时只给出必要字段。

## 代码结构与自动化入口

```text
tun-fix.sh          # 菜单与 CLI 分派
lib/config.sh       # registry 定位、候选渲染、原子写入、备份与恢复
lib/ssh.sh          # GitHub SSH 配置
lib/rules.py        # 唯一的 YAML/registry/规则读取器与 Script 渲染器
lib/discover_apps.py# 只读原生应用发现
lib/report_apps.py  # 菜单选项 3 的人类可读渲染
rules/*.yaml        # 日常编辑的路由来源
```

```bash
./clash-verge/tun-fix.sh rules check     # 只校验两个 YAML 的语法/策略/冲突
./clash-verge/tun-fix.sh rules render    # 输出解析后的 Script.js 到 stdout，不写文件
./clash-verge/tun-fix.sh rules apply     # 更新已登记的全局 Script（等价菜单选项 1）
./clash-verge/tun-fix.sh apps discover   # JSON 形式的只读应用盘点
```

`rules check/render` 不读注册表；`rules apply` 在取消时以退出码 2 结束。

## 离线回归检查

```bash
python3 clash-verge/tests/route-menu.py       # 菜单、更新、恢复、SSH 隔离与写入边界
python3 clash-verge/tests/app-integration.py  # 规则语法、registry、应用展开、Script 守卫
python3 clash-verge/tests/discover-apps.py    # 原生应用发现
```

从仓库根目录执行。需要 Python 3、PyYAML、Bash、Node.js 和 `verge-mihomo`。全部用例
只使用专用临时 HOME 与合成配置：不读真实 HOME、不启动应用、不访问控制器、不建立网络
连接，`dpkg-query`/`ssh` 被替身取代，`curl`/`openssl`/`ip` 等被守卫。
