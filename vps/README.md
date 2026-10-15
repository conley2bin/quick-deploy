# VPS 网络出口与 Tailscale Peer Relay

`vps/setup.sh` 把**一台 Ubuntu 24.04 VPS** 配置成海外 API 代理出口、Tailscale Peer Relay，以及手机访问公司 Codoxear 的桥。两台 PC 继续使用现有 Tailscale IP、OpenSSH、剪贴板 RemoteForward 和 Sunshine；本模块不改 PC 的网络设置。手机只开 Shadowrocket，路径为：

```text
iPhone → SELF-Reality / SELF-Hy2 → VPS → tailscale0 → 公司 PC:8743
PC ↔ PC：Tailscale 自动选择 direct → peer-relay → DERP
```

TCP443 是 VLESS + Reality + Vision；UDP443 是 Hysteria2。两个节点手工选择，不自动切换。每台设备有独立 UUID、Hy2 密码。**只有 phone 身份能访问公司 Tailnet IPv4 的 TCP8743**；其余私网、CGNAT、保留地址、本机服务均拒绝。默认 Reality 目标 `www.microsoft.com`；部署前从 VPS 检查 TLS1.3 和 H2，检查失败就停止。

## 当前验收边界

实现与测试均离线完成，**尚未连接任何 VPS**。官方字段核对不等于实机互通：sing-box 本机未安装，远端 `sing-box check`、nft 内核加载、systemd 定时回滚、Tailscale 入网、手机导入、真实 API 可达性仍须首次部署验证。请准备服务商控制台恢复通道；确认前不要重启 VPS。不会修改 Clash Verge 运行配置、PC Tailscale、Codoxear、Sunshine 或 `/etc/wireguard`。

## 1. 初始化私有身份

在普通桌面用户下运行，不要 sudo 整个入口。PC 依赖 Python3 标准库、OpenSSL、OpenSSH、rsync；QR 可选依赖 `qrencode`，端到端检查依赖 `verge-mihomo`/`mihomo` 和 curl。

```bash
bash ~/quick-deploy/vps/setup.sh init
# 首次init可加 --company-name <公司Tailnet名> --home-name <家Tailnet名>；无机器名默认值
# 有稳定域名时首次 init 可加 --hostname relay.example.net
# 选其它 Reality 目标：init --reality-target www.example.net
```

私有目录固定默认在 `~/.local/state/quick-deploy-private/vps/`，目录0700、文件0600。高级离线测试可在子命令**之前**传 `--state-dir /private/path`。内容：

- `identity.json`：三个用户、Reality X25519 密钥与 short_id、Hy2 自签证书/私钥、可选 hostname。
- `inventory.json`：current、previous、两 PC 地址/名称、另一台 PC 公钥、backup_alias、本机 device。
- `generated/`：客户端凭据、二维码、策略片段；`backups/`：远端配置与本模块防火墙 checkpoint。

`init` 遇到已有身份只校验并保留，不轮换；损坏或权限过宽会显式失败。先备份再人工修复。修改现有 hostname/Reality 目标须编辑私有 identity 后重跑部署；不要删除 identity 来换 VPS。

编辑私有 `inventory.json`，保留已有字段，并填入另一台 PC 公钥和现有 SSH alias：

```json
{
  "version": 1,
  "current": null,
  "previous": [],
  "company_name": "公司实际Tailnet名称",
  "home_name": "家里实际Tailnet名称",
  "ssh_public_keys": ["ssh-ed25519 AAAA... 另一台PC公钥的完整一行"],
  "backup_alias": "另一台PC已有的SSH别名"
}
```

本机公钥读取 `~/.ssh/conley.pub`，部署要求至少两把不同且通过 `ssh-keygen` 验证的公钥；初次/新鲜登录使用 `~/.ssh/conley`。**不要把私钥放 inventory**。公司/家地址默认只读发现 `tailscale status --json` 的 Self 和 Peer，按上述名称匹配；不唯一就失败，不猜本机就是公司。可填 `company_ip`、`home_ip` 或给 deploy/render 传 `--company-ip`、`--home-ip`。必须是两个不同的 `100.64.0.0/10` IPv4。离线 render 需显式提供地址。

## 2. 首次部署（拿到 VPS 后才运行）

1. 安装 Ubuntu24.04，确认服务商防火墙放行 TCP22/443、UDP443/40000/41641；自定义 SSH 端口也须放行。VPS 账号须 root 或有 `sudo -n` 权限。
2. 用服务商控制台核对 SSH host key，先完成可用的 OpenSSH 登录。脚本不跳过 known_hosts 检查，管理 SSH 禁用 ProxyCommand/ProxyJump；PC现有TUN仍可能截获底层连接，默认22由现有Clash规则直连，自定义端口须自行核对。
3. 管理控制台先备份 Tailnet policy，添加 `tag:relay` 的 tagOwners，并收紧旧 allow-all（策略章节）。准备批准登录和标签。
4. 先看完全离线的计划；此模式不读身份、SSH、Tailscale、DNS，不创建状态目录：

```bash
bash ~/quick-deploy/vps/setup.sh deploy --host <VPS数字IP> --device company --dry-run
bash ~/quick-deploy/vps/setup.sh deploy --host <VPS数字IP> --device company --provider Evoxt
# --device 必填；在家里PC执行时用 --device home；可加 --user ubuntu --port 2222
```

部署先确认目标，安装 sing-box 官方签名APT包，固定为[稳定版1.13.12](https://github.com/SagerNet/sing-box/releases/tag/v1.13.12)（`vps.py:SING_BOX_VERSION`），并`apt-mark hold`；版本写入上传params。已有版本必须精确匹配，否则明确停止，不自动升/降级。APT仓库若已不保留该版本也失败，不退回latest。版本变更须先修改固定值并重新审查/验收。Tailscale用官方安装器。新包安装期间临时 mask，避免默认配置自动启动。只接受 Ubuntu24.04，Tailscale 现场检查 CLI flags。默认 `tailscale up` 打印登录 URL，等待最多10分钟；在浏览器登录并批准标签。然后设置 `--relay-server-port=40000`，不提供 exit node 或子网路由。

可选用 `--auth-key-file /private/auth-key`（当前用户0600）或 `TS_AUTHKEY` 环境变量。密钥通过 SSH stdin 的 root-only 临时文件传输，CLI 只传 `file:` 路径，不打印密钥；用后删除。不保存 auth key 为长期恢复凭据，换机通常新建一次性 tagged key。

### 配置事务

本地渲染 → SSH 上传 → 远端 `sing-box check` 和 `nft -c` → 私有远端/本地 checkpoint → 启动独立 **180秒自动恢复 timer** → 停 proxy、原子替换配置与本模块表 → 重启/监听/标签检查 → **禁用 SSH multiplex 的全新公钥登录** → 输入 `YES` → sshd 禁密码/键盘交互、root仅公钥 → 再次新登录检查 → 持久化并取消 timer。

新装包的公开示例配置在unit仍mask时删除，并禁用unit；pristine checkpoint不含代理配置。snapshot记录原有启用状态；恢复首先disable --now sing-box，只有同时具备配置、UID drop-in和持久防火墙的完整checkpoint才允许重新启动/启用。commit最后才enable sing-box，避免回滚后重启跑起无guard代理。

180秒内未确认或检查失败，会恢复之前的配置、nft表、sshd片段与服务 override。失败不会静默降级。包安装、Tailscale 入网、追加公钥不在回滚范围内。遇到断线等待 timer；上传/prepare完成但尚未apply时没有改运行配置，遗留prepared标记会在下一次prepare时替换，也可用`setup.sh abort --host <IP>`清除（未提交inventory也能用）。没有待处理状态时abort明确拒绝，不假装执行了恢复。自动fallback先尝试新登录，再尝试现有ControlMaster连接。prepare对成功、失败及HUP/INT/TERM/PIPE都清理auth-key和本模块拥有的runtime mask；APT显式noninteractive。必要时从服务商控制台执行：

```bash
sudo /usr/local/lib/quick-deploy-vps/remote.sh abort
```

nft只管理 `table inet qd_vps`，不清空其他表或改 `/etc/nftables.conf`；独立 systemd unit 在重启时加载。输入默认drop，仅允许既有连接、lo、ICMP、管理端口和代理/relay UDP端口，拒绝新的 tailscale0 入站服务连接；公司8743的回包按 conntrack 放行。sing-box 使用独立 `qd-vps-proxy` UID，OUTPUT 对该 UID 拒绝本机（含自身公网地址）和保留目的地，仅允许 tailscale0 → company:8743；协议层 `auth_user` 再限制为 phone。先 DNS resolve 再按目的 IP 拒绝，避免域名绕过范围规则。DNS使用公共1.1.1.1，VPS 本地解析器不需要私网例外。

## 3. 客户端输出

部署成功自动 render，也可单独执行：

```bash
bash ~/quick-deploy/vps/setup.sh render --device company
# 离线示例（使用自己的真实Tailnet IP替换占位）：
bash ~/quick-deploy/vps/setup.sh render --device home --host <VPS数字IP> \
  --company-ip <公司100.x> --home-ip <家100.y>
```

### Clash Verge（两台 PC 各自执行）

`generated/clash-nodes.json` 只包含本机选择的 company 或 home 两节点。`clash-company.json`、`clash-home.json` 供人工核对/分发，不能让手机使用PC身份访问8743。恢复同一私有目录到另一台PC后，先 `render --device home`（反之 company），再更新规则。

现有 `clash-verge/tun-fix.sh rules render/apply` 在私有文件存在时把节点与 `🛰️ 自建 VPS` select 组**内联**进生成的 Script。self组选项为Reality（默认）、Hy2、存在时的`🚀 节点选择`；🚀仅添加两SELF叶节点，不能反向引用self组，否则mihomo检测组循环。缺少🚀时仍保留所有本地规则/TUN exclusions。先注入VPS所有已知数字IP的DIRECT规则，再注入API/认证域名到self组的规则，保证API不随🚀普通选择而变化。清单唯一维护在`clash-verge/lib/rules.py:VPS_MODEL_DOMAINS`，Google不使用宽泛后缀。

`server_ips`随私有输出携带数字IP；稳定hostname从inventory current.host取得主IP，额外已知IPv4/IPv6可填私有`vps_ips`列表。无已知IP就明确失败，不联网猜DNS。不使用外部file provider，不涉及SAFE_PATHS外读。文件不存在时输出与原版逐字节一致；文件损坏或权限过宽显式失败。订阅节点和其他选择保留。

本模块的 render/deploy **不执行** `tun-fix apply` 或重载 Verge。请自行查看候选输出后更新已登记 Script，再在 GUI 重载。生成 Script/备份包含凭据，不能提交到 Git 或分享日志。

Hy2 节点启用 SHA256证书指纹 pin，并保持 `skip-cert-verify: false`；SNI为证书SAN `qd-vps.invalid`。不人为设置带宽上限，让 Hysteria2 默认拥塞控制工作。

### iPhone Shadowrocket

`shadowrocket.txt` 有两条 URI；`shadowrocket-subscription.txt` 是相同内容的 base64订阅文本，**不自动托管为公网URL**。有 qrencode 时输出每节点二维码，否则明确提示。导入二维码或粘贴 URI 后，一次性修改自己的配置：

1. `[General]` 的 `tun-excluded-routes` 删除 `100.64.0.0/10`，确认 `skip-proxy` 不含它。
2. `[Proxy Group]` 添加 `SELF = select,policy-regex-filter=^SELF-`。
3. `[Rule]` 最前面添加 `IP-CIDR,<公司TailnetIP>/32,SELF,no-resolve`。

完整中文指令与指纹在 `generated/手机和策略说明.txt`。只导入节点不会带上规则。随后浏览器访问 `http://<公司TailnetIP>:8743`，继续用现有 Codoxear 密码；不公网开放8743。

Hy2 URI按[Hysteria2官方URI Scheme](https://v2.hysteria.network/docs/developers/URI-Scheme/)输出大小写敏感的 `pinSHA256=<小写十六进制>`，默认不带 `insecure=1`。**手机必须把pin故意改错并确认无法连接，再恢复正确pin**；这验证当前Shadowrocket真正执行证书绑定。若正确pin仍因自签CA被拒，才手工加 `insecure=1` 兼容，并保留pin、再验错pin失败。错pin仍能连接时不要用Hy2，改用Reality。开发者网站未说明Shadowrocket的具体导入行为，不能把官方协议支持视为App已验收。

### Tailnet policy

**先导出当前完整策略备份**，再在管理控制台合并 `tailnet-policy.json`，不要覆盖不相关授权。片段包含 tagOwners、company/home hosts、member→self、relay→company TCP8743、company+home→relay capability。

必须移除/收紧旧的 allow-all ACL/grant；规则是累加允许，新增狭窄 grant 不会撤销旧允许。`autogroup:self` 只授权同一用户自己的未标记设备，两PC若属不同用户须另行明确授权。让控制台校验通过后，验证VPS能到8743、不能到公司22或Sunshine。relay capability只授权中继，不授予VPS其他overlay服务权限。

## 4. check / status / rollback / backup

```bash
bash ~/quick-deploy/vps/setup.sh status
bash ~/quick-deploy/vps/setup.sh check             # 此命令会访问VPS和三家API，无API key
bash ~/quick-deploy/vps/setup.sh check --ping      # 额外可选 tailscale ping qd-vps-relay
bash ~/quick-deploy/vps/setup.sh rollback
bash ~/quick-deploy/vps/setup.sh abort             # 当前inventory目标；或 --host <IP> --user root --port 22
bash ~/quick-deploy/vps/setup.sh backup            # 使用 inventory backup_alias
# 或 backup --alias 已有SSH别名
```

status/check 检查 sing-box、Tailscale运行/标签、443 TCP+UDP及40000UDP监听；用数字化nft输出核对真实proxy UID guard、检查UID drop-in/加载User，并通过MainPID的实际EUID确认进程不以root运行。candidate阶段也核对UID替换成功。check 再在临时目录启动独立 mihomo（可 `--mihomo /path/to/binary`），绑定临时127.0.0.1 mixed端口，不开TUN、控制器、系统代理或DNS监听，不碰Verge。每节点请求 OpenAI/Anthropic/Gemini models endpoint，TLS验证启用，不发送密钥。收到HTTP状态即可证明该HTTPS端点可达（401/403/400正常）；逐节点、逐服务输出PASS/FAIL。它不能证明账号额度、地域资格或模型权限。端口竞争或启动失败显式失败并清理进程，没有直连fallback。

rollback也使用独立180秒timer：先保存当前状态用于撤销，再恢复最近一次成功deploy之前的配置与本模块nft表，通过新鲜公钥登录并输入YES才保留；否则恢复到rollback前。pristine目标必须保持sing-box停用且无配置。重复确认的rollback恢复同一checkpoint，不来回切换。abort等待操作锁直至取得，timer设置RemainAfterElapse=no；应用前与abort/commit都会清理旧timer，timer缺失不妨碍恢复。不轮换身份、不删除软件、不退网。backup用已有SSH别名和rsync写入对端独立的 `~/.local/state/quick-deploy-private/vps-backups/<来源hostname>/`，绝不覆盖对端live `vps/` 中的inventory、device或generated。保留0700/0600，不用`--delete`，不复制活动锁。备份未做磁盘文件加密，但通过SSH加密传输；请另存离机加密副本。

恢复时先取到独立临时目录，不直接覆盖本机live身份：

```bash
umask 077
restore_dir=$(mktemp -d /tmp/qd-vps-restore.XXXXXX)
rsync -a --chmod=D700,F600 '对端别名:.local/state/quick-deploy-private/vps-backups/来源hostname/' "$restore_dir/"
# 核对 identity 指纹、inventory current/previous 和所有权；另备份本机已有vps目录。
# 仅确认需要恢复该业务身份后，手工复制到自己的private vps目录；不能覆盖一份不同的既有identity。
# 恢复后先更新本机角色与清单，再运行 render --device home（或company），才更新Clash。
```

恢复源的inventory只是备份快照，不能当成本机最新host事实；务必核对目标IP，做独立恢复演练。

## 5. 更换 VPS

1. 先 backup，停止旧VPS提供服务，保留原 identity 和新机SSH host key的带外核对；不克隆 Tailscale daemon 状态。
2. 新Ubuntu24.04上执行相同 `deploy --host 新IP --device 本机角色`；默认交互重新入网或用新的一次性tagged auth key。
3. 有稳定hostname则更新DNS；无hostname则刷新各PC生成Script和手机两节点地址。UUID、Reality公私钥、Hy2密码与证书均复用。
4. 合并/核对策略，运行check，并在手机Wi-Fi和蜂窝下分别验证8743及拒绝用例；验PC双向SSH/剪贴板/Moonlight，观察`tailscale status`是否peer-relay。
5. 全部验收后在Tailscale管理控制台删旧节点、销毁旧VPS。原host保留在private inventory previous中；不恢复/跳过旧host key验证。

## 6. 链路限制与离线测试

家里电信国际出口晚高峰实测11–21%丢包，IPv4/IPv6都存在。Hy2可改善有损代理链路的使用体验，不能创造带宽或保证低延迟；Peer Relay不因一般丢包自动切DERP。首尔中继PC↔PC约两段70ms，晚高峰SSH仍可能因重传停顿。**家里申请可达公网IPv4、恢复国内直接路径才是PC↔PC低延迟的根本改进**，不是换一台同线路VPS。

```bash
RM_GUARD_CONFIRM=1 python3 -m unittest discover -s vps/tests -v
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/vps-integration.py
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/route-menu.py
RM_GUARD_CONFIRM=1 TUN_FIX_WG_ENDPOINTS=off python3 clash-verge/tests/app-integration.py
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/discover-apps.py
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/install-package-selection.py
bash -n vps/setup.sh vps/lib/remote.sh
```

单元测试验证身份/权限/不重生、inventory、路由/URI/policy、隔离临时client配置、干跑和部署失败路径；远端shell在重写到临时目录的假VPS里运行事务/故障注入，系统服务与联网命令均为stub。已安装mihomo以`-t -d 临时目录`核验节点schema；这些都不等于真实防火墙、relay或手机端验收。

## 官方字段核对来源

- sing-box：[官方APT安装](https://sing-box.sagernet.org/installation/package-manager/)、[VLESS inbound](https://sing-box.sagernet.org/configuration/inbound/vless/)、[Hysteria2 inbound](https://sing-box.sagernet.org/configuration/inbound/hysteria2/)、[TLS/Reality](https://sing-box.sagernet.org/configuration/shared/tls/)、[auth_user/ip_cidr/ip_is_private](https://sing-box.sagernet.org/configuration/route/rule/)、[resolve/route/reject动作](https://sing-box.sagernet.org/configuration/route/rule_action/)。Context7 `/sagernet/sing-box` 与官方网站交叉核对；不使用废弃block outbound/geosite/geoip字段。
- mihomo：[VLESS](https://wiki.metacubex.one/en/config/proxies/vless/)、[Hysteria2](https://wiki.metacubex.one/en/config/proxies/hysteria2/)、[TLS fingerprint/SNI](https://wiki.metacubex.one/en/config/proxies/tls/)。使用`flow: xtls-rprx-vision`、`client-fingerprint: chrome`、`reality-opts.public-key/short-id`、`fingerprint`证书pin。
- Tailscale：[Linux安装](https://tailscale.com/kb/1031/install-linux)、[Peer Relay](https://tailscale.com/docs/features/peer-relay)、[CLI](https://tailscale.com/kb/1080/cli)。官方要求≥1.86，确认`tailscale.com/cap/relay`及`--relay-server-port`；本机只读`up/set --help`另确认`--auth-key=file:`、`--timeout`等，部署时再核对远端版本。
- Hysteria2：[官方URI Scheme](https://v2.hysteria.network/docs/developers/URI-Scheme/)确认 `pinSHA256` 与 `insecure`；本模块默认输出pin，不默认降级。Shadowrocket [开发者官网](https://shadowlaunch.com/)没有完整配置规范；**其对pin的实际执行、VLESS pbk/sid/fp、tun-excluded-routes/skip-proxy、policy-regex-filter和IP-CIDR规则仍须实机验收**。
