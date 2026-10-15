# 两个 VPS 角色：首尔代理、上海 Tailscale Peer Relay

本模块只改变**明确指定的 Ubuntu24.04 VPS**，不改两PC的Clash运行配置、Tailscale设置、SSH别名、tmux、Codoxear、Sunshine或WireGuard。海外节点由用户自己选择；不按模型/API域名强制分流。

| 角色 | 主机职责 | Tailscale身份 | 默认SSH用户 |
|---|---|---|---|
| `proxy`（默认） | 首尔Evoxt：Reality TCP443、Hy2 UDP443；第二阶段桥接手机→公司TCP8743 | `tag:egress`，**禁用Peer Relay** | root（可`--user ubuntu`等） |
| `relay` | 上海腾讯Lighthouse：只做PC↔PC Peer Relay，无sing-box、无代理身份或客户端输出 | `tag:relay`，UDP40000 | ubuntu（通常有免密sudo） |

PC之间仍用原Tailscale IP：能直接连就direct，否则使用获授权的上海Peer Relay，官方DERP最后兜底。策略只给两PC使用`tag:relay`，不给`tag:egress`中继权限。手机只开Shadowrocket：手机节点→首尔→tailscale0→公司8743；不需要手机SSH。

**离线交付边界：**测试使用临时目录/假VPS和真实mihomo配置检查，没有实际部署或访问任何VPS。内核nft、真实sshd/systemd/软件安装、Relay路径、手机pin及真实API仍需逐项验收。不要并发操作同一VPS；保留服务商控制台恢复入口。

## 1. 私有状态和身份

在普通PC用户下运行，不要sudo整个setup。依赖Python3、OpenSSL、OpenSSH；backup需rsync；QR可选qrencode；代理check需mihomo/verge-mihomo及curl。远端先装Python等依赖再解析配置；所有本模块APT命令等待dpkg锁最多300秒，noninteractive且不升级已有依赖包。官方Tailscale安装器通过临时`APT_CONFIG`继承`DPkg::Lock::Timeout "300";`，成功/失败/信号退出均删除临时文件。

```bash
# 需要代理时初始化业务身份；重跑绝不轮换已有身份。
bash ~/quick-deploy/vps/setup.sh init
# 仅配置relay的PC也可这样初始化私有inventory，不创建/读取proxy identity：
bash ~/quick-deploy/vps/setup.sh init --role relay
# 首次init可加 --company-name <公司Tailnet名> --home-name <家Tailnet名>
# proxy首次init还可加 --hostname relay.example.net --reality-target www.microsoft.com
```

状态在`~/.local/state/quick-deploy-private/vps/`，目录0700、文件0600，永不入Git。测试可在子命令前用`--state-dir /private/path`。

- `identity.json`：仅proxy使用，company/home/phone各自UUID和Hy2密码、Reality密钥/short_id、自签证书及可选hostname。损坏/权限过宽明确失败，不重生。
- `inventory.json`：共享PC地址/公钥输入，**两个独立role slot**各自current/previous。旧单主机inventory只迁入proxy，绝不猜成relay；不允许两个角色指向同一IP。
- `generated/`：仅proxy的客户端输出。`tailnet-policy.json`可独立在入网前渲染。
- `backups/proxy/`与`backups/relay/`：各角色的服务器checkpoint。远端`active-params.json`保存已应用角色/阶段，随checkpoint恢复；远端role所有权标记拒绝误用另一角色。

编辑私有inventory（示意；公钥和名称必须换成真实值）：

```json
{
  "version": 2,
  "roles": {
    "proxy": {"current": null, "previous": []},
    "relay": {"current": null, "previous": []}
  },
  "company_name": "公司实际Tailnet名称",
  "home_name": "家里实际Tailnet名称",
  "ssh_public_keys": ["ssh-ed25519 AAAA... 另一PC完整公钥"],
  "backup_alias": "对端PC已有SSH别名"
}
```

本机公钥固定读`~/.ssh/conley.pub`，要求两把不同且通过ssh-keygen验证的公钥；初次/新鲜登录使用`~/.ssh/conley`。不把私钥放inventory。PC地址从本机`tailscale status --json`的Self/Peer按私有名称只读发现；可填`company_ip`、`home_ip`或传对应IP flags。必须为不同的100.64/10 IPv4，不猜本机角色。替换一个role只更新它自己的current/previous，不动另一role。

## 2. 首尔proxy：先验代理，再加手机桥

先用服务商控制台核对SSH host key，保证OpenSSH可登录。默认root；若镜像禁root，明确`--user`指定实际免密sudo用户。脚本保留known_hosts验证，禁止ProxyCommand/ProxyJump；不修改PC路由。服务商防火墙先放TCP22（或实际SSH端口）、TCP443、UDP443；桥接阶段还需UDP41641。proxy不监听或开放UDP40000。

```bash
# 第一阶段：只装代理、防火墙、公钥/sshd；跳过VPS所有Tailscale安装/CLI/入网。
bash ~/quick-deploy/vps/setup.sh deploy --role proxy --host <首尔IP> --device company --without-tailscale --dry-run
bash ~/quick-deploy/vps/setup.sh deploy --role proxy --host <首尔IP> --device company --without-tailscale --provider Evoxt
bash ~/quick-deploy/vps/setup.sh check --role proxy
```

`--device company|home`对proxy必填；render/check选本机独立凭据。第一阶段health/status/check显示Tailscale **SKIP**，check仍逐节点探测三家API，无API key。自行更新Clash、手选节点并验收，随后再做桥接。

**第二阶段先导出并合并Tailnet策略，后入网**（下节）；tag必须已存在。再运行：

```bash
bash ~/quick-deploy/vps/setup.sh deploy --role proxy --host <同一首尔IP> --device company
bash ~/quick-deploy/vps/setup.sh check --role proxy --ping
```

此阶段请求`tag:egress`、accept-dns=false、accept-routes=false，且显式将`--relay-server-port`设为空，关闭旧版本可能留下的relay。不提供exit node或子网路由。默认打印登录URL等最多10分钟；可用0600的`--auth-key-file`或`TS_AUTHKEY`，密钥只经SSH stdin到root私有文件、用file:路径传给CLI，用后删除。without阶段不读取/上传auth输入、不安装/入网或up/down。全新主机没有CLI时不运行Tailscale；若已有active tailscaled，只清空relay端口与static endpoints并验证（不改其他prefs）；若CLI存在但daemon inactive则不失败，apply及health/status明确警告relay prefs无法验证。

两阶段复用身份。配置/防火墙相同、UID/监听健康且`readlink /proc/MainPID/exe`精确为`/usr/bin/sing-box`时，不stop/restart代理、不换配置、不重载nft；只事务化更新阶段状态。已删除/旧二进制不走此捷径，必须正常重启并检查。失败或未确认的纯阶段变化只恢复阶段/sshd，不打断未改动的代理。包安装与Tailscale入网不是配置回滚的一部分。

### 固定版本与明确升级

固定[sing-box1.14.3](https://github.com/SagerNet/sing-box/releases/tag/v1.14.3)，官方签名APT安装并hold，版本写入params。1.13.x的utls限制可使Microsoft目标Reality全部失败，不能继续使用。1.14.3的[go.mod](https://github.com/SagerNet/sing-box/blob/v1.14.3/go.mod)使用utls1.8.7。

已有版本不符默认拒绝；只有`deploy --upgrade ...`并额外YES确认才unhold→安装精确`sing-box=1.14.3`（`--force-confold`保留已有配置）→hold，失败也重新hold。不退回latest，包升级不降级回滚。新装包的公开示例配置在unit仍mask时删除，pristine checkpoint无配置，unit禁用，避免回滚后重启暴露示例代理。

## 3. 上海relay：腾讯Lighthouse

新建**Ubuntu24.04** Lighthouse实例。通常用户`ubuntu`且免密sudo，root SSH可能禁用，故relay默认ubuntu。先核对控制台host key和SSH登录。

**Lighthouse还有独立的控制台防火墙：必须放行TCP22（或实际SSH端口）、UDP40000、UDP41641。** 不开放443；relay不安装sing-box，不读取/创建代理身份，不生成Clash/Shadowrocket输出。

先按下一节备份并合并策略，再部署：

```bash
bash ~/quick-deploy/vps/setup.sh deploy --role relay --host <上海IP> --dry-run
bash ~/quick-deploy/vps/setup.sh deploy --role relay --host <上海IP> --user ubuntu --provider Tencent
bash ~/quick-deploy/vps/setup.sh check --role relay
```

relay的`--host`必须是global unicast数字IP，域名、私网、CGNAT、保留地址在任何contact前拒绝。Tailscale请求`tag:relay`，relay UDP40000只在已武装timer的apply阶段开启，同时固定`--relay-server-static-endpoints=<公网IPv4>:40000`；IPv6为`[公网IPv6]:40000`，不依赖海外DERP/STUN才能发现Lighthouse的公网NAT映射。官方安装前检查`pkgs.tailscale.com`，不可达则明确停止，不换非官方镜像；安装器后续失败也明确提示。policy/login批准需要Tailscale控制面可达。

服务器check验证tailscaled运行、tag:relay、40000UDP监听和模块nft表，并从`tailscale debug prefs`确认`RelayServerStaticEndpoints`包含期望endpoint。`check --role relay`打印已验证的advertised endpoint；pristine restore和proxy apply明确清空static endpoints，proxy rollback也绝不恢复relay prefs。PC侧ping该**role当前主机的数字Tailnet IP**，再只读status JSON，报告另一PC当前direct / peer-relay / DERP；若没有活动流量则明确idle/unknown，不编造路径。flags可覆盖PC IP。不会改PC配置、强制断开direct或删DERP；请在两PC实际通信时观察peer-relay是否为上海地址。

## 4. Tailnet策略：先备份/合并，再enroll

不需要proxy identity即可提前生成：

```bash
bash ~/quick-deploy/vps/setup.sh render --policy-only --company-ip <公司100.x> --home-ip <家100.y>
# 输出私有 ~/.local/state/quick-deploy-private/vps/tailnet-policy.json
```

片段包含：
- tagOwners：tag:relay及tag:egress归admin；
- 私有输入填充的company/home host aliases；
- member→self `*`，替换原allow-all；
- company+home→tag:relay，app `tailscale.com/cap/relay`；
- tag:egress→company `tcp:8743`，不给relay访问Codoxear的授权。

**先导出当前完整policy备份，合并并通过admin console校验，才部署enroll。** 工具在任何enroll上传前额外要求YES确认；不会替你修改控制台策略。 grant允许是累加的，必须移除旧allow-all、过宽relay授权；只让上海实例持有tag:relay，首尔改为tag:egress，清理旧中继节点。保留不相关精细授权。autogroup:self只涵盖同一用户自己的未打标签设备，两PC不同拥有者须另外明确授权。DERP仍是最后回退，不删控制面；10%丢包不保证触发换路。

## 5. 客户端：手动选节点

```bash
bash ~/quick-deploy/vps/setup.sh render --device company
# 无部署记录时可离线指定 --host <首尔IP> --company-ip <公司100.x> --home-ip <家100.y>
```

`generated/clash-nodes.json`为本机company/home；两PC各自render。恢复备份到另一PC后必须换本机device。稳定hostname的已知数字IP来自proxy current.host；额外IPv4/IPv6可填私有`vps_ips`。不联网猜DNS，没有已知数字IP就失败。

Clash生成器把SELF-Reality/SELF-Hy2加为普通可选节点，只追加到已有订阅单节点的select组；url-test/fallback/load-balance及纯DIRECT/REJECT/组策略不增加。保留所有已有选项顺序、默认项、selection。**没有专用self组，没有API/认证域名强制路由。** VPS /32、/128 DIRECT规则仍最先，避免非22SSH/临时check客户端绕回VPS。无私有文件时Script逐字节不变；节点内联，不需放宽SAFE_PATHS。

本模块不自动调用tun-fix apply、不改live Script或重载Verge。手动查看候选、更新已登记Script后再GUI重载；Script/备份含凭据，不能入Git。Hy2使用证书fingerprint pin、skip-cert-verify=false。

### Shadowrocket：仅Codoxear需要私网规则

导入`shadowrocket.txt`的两URI，或base64订阅文本`shadowrocket-subscription.txt`；不自动托管URL。qrencode存在才生成QR，否则明确提示。海外/API流量自己选节点，不默认走VPS。

第二阶段桥接验收后，一次性手工编辑：
1. 从tun-excluded-routes移除100.64/10，并确认skip-proxy没有它；
2. `[Proxy Group]`添加`SELF = select,policy-regex-filter=^SELF-`（仅供此手机私网目的地）；
3. `[Rule]`第一行`IP-CIDR,<公司IP>/32,SELF,no-resolve`。

浏览器访问`http://<公司IP>:8743`并使用已有Codoxear密码。不改Codoxear或开放公网8743；手机不配置SSH。服务端仅phone身份可到该TCP端口，其他身份及私网/CGNAT/本机目的地拒绝；独立proxy UID的nft OUTPUT guard补充DNS重绑定/本机公网IP防护。

Hy2 URI按[官方URI Scheme](https://v2.hysteria.network/docs/developers/URI-Scheme/)带`pinSHA256=<小写hex>`，默认不带insecure。**手机故意改错pin必须连接失败**；正确pin因自签CA被拒时才可加insecure=1，但保留pin并重做错pin测试。错pin仍能连接时不要使用Hy2，改Reality。Shadowrocket实际导入/pin/规则仍待实机验收，不能从协议规范推断App已执行。

## 6. 事务、check、回滚和按角色备份

所有管理操作默认proxy，另一角色必须显式选：

```bash
bash ~/quick-deploy/vps/setup.sh status --role relay
bash ~/quick-deploy/vps/setup.sh check --role proxy --ping
bash ~/quick-deploy/vps/setup.sh rollback --role relay
bash ~/quick-deploy/vps/setup.sh abort --role relay
# 首次inventory尚未commit时：abort --role relay --host <上海IP> --user ubuntu
bash ~/quick-deploy/vps/setup.sh backup --role proxy --alias <对端PC已有SSH别名>
bash ~/quick-deploy/vps/setup.sh backup --role relay --alias <对端PC已有SSH别名>
```

缺少该role current明确拒绝，不回退到另一台；同IP两角色或服务器role不符也拒绝。relay操作不要求代理identity。

本地render→上传→候选检查→checkpoint（本地/服务器）→独立180秒timer→原子nft/config操作→健康检查→全新非multiplex公钥登录→YES→sshd禁密码/键盘交互、root仅公钥→再新登录→commit。relay同样执行timer/新登录/sshd流程，只没有proxy操作。SSH有效配置捕获`sshd -T -C`完整输出再grep，不用pipefail下早退出管道。rollback也有undo timer和新登录/确认，服务最多轮询20次等就绪。

只有模块表inet qd_vps，绝不flush其他nft/Tailscale表。输入默认drop，保留lo、established、ICMP、link-local DHCPv6回复。relay仅放实际SSH端口和UDP40000/41641。proxy有独立UID guard、443节点及41641；不开放新tailscale0入站服务连接，8743回包按conntrack允许。FW独立unit重启加载。

abort无限等待操作锁；timer使用RemainAfterElapse=no并清理stale timer。恢复proxy先disable/stop，完整guarded checkpoint才重新启用；pristine保持OFF/configless。relay恢复之前的relay广告/daemon激活状态，pristine不再监听40000；**包安装、添加公钥、Tailscale节点入网不撤销，不克隆daemon身份**。需要时在管理台删除失效节点。prepare清理auth-key/拥有的runtime mask，替换stale prepared；恢复fallback先新登录再试既有ControlMaster。保留服务商控制台，pending时不要重启。

每角色backup去对端`vps-backups/<来源hostname>/<role>/`，不覆盖对端live状态，也不备份另一role的inventory/history/秘密。无--delete，不复制锁。先恢复到独立目录，核对role/current/identity，再手工合并所选role，不能把空的另一slot覆盖正在使用的另一角色。proxy恢复后重新render本机device。旧单主机本地archive不删除；新checkpoint按role分目录。

```bash
umask 077
restore_dir=$(mktemp -d /tmp/qd-vps-restore.XXXXXX)
rsync -a --chmod=D700,F600 '<对端别名>:.local/state/quick-deploy-private/vps-backups/<来源hostname>/<role>/' "$restore_dir/"
# 替换占位后使用；先核对，不直接覆盖live vps/。
```

远端保留最新3个checkpoint及previous/prepared/pending/target引用；不跟随symlink清理其他目录。备份通过SSH加密传输，本地文件未额外加密，另存离机加密副本并做恢复演练。

## 7. 离线测试和边界

```bash
RM_GUARD_CONFIRM=1 python3 -m unittest discover -s vps/tests -v
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/vps-integration.py
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/route-menu.py
RM_GUARD_CONFIRM=1 TUN_FIX_WG_ENDPOINTS=off python3 clash-verge/tests/app-integration.py
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/discover-apps.py
RM_GUARD_CONFIRM=1 python3 clash-verge/tests/install-package-selection.py
bash -n vps/setup.sh vps/lib/remote.sh
```

假VPS测试执行shell流程但stub服务/包/网络，不能证明内核或真实云主机行为。native mihomo -t验证代理节点和真实形态订阅生成结果；不碰live Verge。proxy check用临时loopback mihomo分别访问三家API，无API key；HTTP401/403/400代表可达，不代表账户权限。relay check只ping数字relay IP和读取当前PC路径。

家里电信国际出口高峰11–21%丢包仍可能影响首尔代理；Hy2不是带宽保证。上海relay把PC流量留在国内，实际性能必须在新实例上测；direct优先、官方DERP最后回退。家里公网IPv4是可选直连改进，不替代旅行时的relay。

官方依据：[sing-box安装](https://sing-box.sagernet.org/installation/package-manager/)、[TLS/Reality](https://sing-box.sagernet.org/configuration/shared/tls/)、[route](https://sing-box.sagernet.org/configuration/route/rule/)、[mihomo VLESS](https://wiki.metacubex.one/en/config/proxies/vless/)、[Hy2](https://wiki.metacubex.one/en/config/proxies/hysteria2/)、[Tailscale Peer Relay/grants](https://tailscale.com/docs/features/peer-relay)、[官方Linux安装](https://tailscale.com/kb/1031/install-linux)。当前路径字段CurAddr/PeerRelay/Relay参考官方Tailscale1.102.3 ipnstate源码；idle不等于当前DERP。
