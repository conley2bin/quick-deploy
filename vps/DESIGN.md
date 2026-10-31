# One VPS: native TCP/REALITY + UDP/Hysteria2, an exact-path TLS front

Selected architecture, 2026-10-18. This replaces the earlier sing-box/relay and
independent-static-publisher proposal. Production contact requires a separate
operator gate; a source implementation is not deployed-client acceptance.

## Ownership

- **3x-ui** owns SQLite schema, admin authentication, device accounts, Xray
  generation, full Mihomo profiles and base64 URI subscriptions. No public
  converter and no local node-format engine.
- **Xray** serves VLESS/TCP/REALITY/Vision and native Hysteria2 on TCP443 and
  UDP443 by default. One validated `config.proxy_port` setting owns both typed
  listeners, native exports, health and backup; no separate UDP port knob exists.
  optional `--port` still means SSH only. Fresh installations default to
  company/home/phone; every manifest-owned device has an independent UUID
  and SubID, plus the native Hysteria2 `Auth` credential. One global native Client
  joins exactly the two owned inbounds (three after the optional CDN three-entry
  migration; see CDN 入口模式); Native Clients operations own changes.
- **Caddy** serves trusted HTTPS on TCP8443, proxying exactly two paths per
  enabled manifest-owned device, without wildcards.
  Panel2053 and subscription2096 bind127.0.0.1. Management is through SSH forwarding.
- **TCP80** belongs exclusively to HTTP01 validation. All normal HTTP requests
  return404. Automatic redirects and TLS-ALPN challenges are disabled; Caddy
  must not claim443. Unavailable/invalid certificates cannot become HTTP feeds.
- **Operator** owns DNS routing/proxy state and the registered hostname. No DNS API credential
  is needed or installed. DNS propagation, client refresh and cold-replacement
  downtime are real operational steps, not automatic failover.
- **Management PC** holds a private0700/0600 off-node recovery set. The default
  archive is unencrypted. SSH transport encryption is not encryption at rest.

The wrapper owns one private remote tree and explicitly named systemd units,
not sshd, authorized_keys, Tailscale, DERP, firewall policy, PC routes or apps.
Conflicting services, ports, legacy trees and unreviewed firewall rules are
refused rather than adopted, flushed, masked or deleted.

## Source and release policy

`release-lock.json` pins official Linux amd64 artifacts: 3x-ui3.10.2,
Xray26.10.10 and Caddy2.11.7. Downloads are checked against fixed SHA256s.
The source audit obtained the official release API digests and compared actual
bytes with official checksum files (Caddy's checksum file uses SHA512).
This trusts upstream release publishing and GitHub TLS; it does not claim
independently signed/reproducible 3x-ui or Xray builds.

Extract only executables and official geodata, not upstream installers, units,
CLI menus or Fail2ban configuration. Do not use panel one-click upgrades or
unbounded latest. A release change requires a newly tested lock, off-node
backup and a matching-database restore drill; never claim config-only rollback
also rolls back binaries/schema. Client versions are diagnostic evidence, not
an arbitrary allow-list.

## Lifecycle invariants

1. Resolve explicit SSH target/alias with normal verified host trust, no
   multiplexing, forwarding or proxy jump. Pin the public endpoint and remote
   machine-id before uploading credentials; bind resolved endpoint/user/port in
   the transport itself, retaining strict host-key checks and saved selections.
   Initial support is a dedicated
   Ubuntu24.04 amd64 machine; other platforms stop before installation.
2. Create/reuse native accounts without rotating them on repeated deploy.
   Missing/corrupt or legacy private state must not be regenerated silently.
3. Candidate feeds remain unpublished while native Xray checks, all enabled devices'
   native Mihomo profiles, URI scope and public-CA TLS are validated. An
   off-node complete backup receipt and a fresh SSH call precede publication.
4. Persistent pending state records boot identity/deadline. Recovery must work
   after lost SSH and reboot; a transient timer alone is insufficient. Periodic
   recovery never waits on a busy operation flock; the next tick retries. Commit
   checks the lease under that lock immediately before publication. Failed
   private candidates are retained for inspection, not erased.
5. Replacement needs no old-node contact: restore the off-node native DB,
   account/SubID mapping, certificates and exact releases on a fresh host.
   Default replacement rotates node keys and proxy UUIDs, preserving SubIDs.
   Dedicated native client endpoints perform rotation from complete fresh Client
   objects plus the native account's HWID limit; no reduced projection is written
   back. Inbound forms cannot overwrite authoritative global clients in v3.10.2.
   The native Hosts table/API owns exported addresses: validate the single owned
   groups/inbounds/old-endpoint membership and update complete group addresses
   only. Legacy snapshots have exactly one REALITY group/inbound; explicit dual
   snapshots have that unchanged group and exactly one H2 group/inbound. Preserve
   other Host fields, then verify DB/API and true native feeds.
   Fresh creation records group identity; a missing source-era archive identity
   is derived only from its verified closed OLD-endpoint snapshot. Extra/hidden/
   disabled duplicates, dangling membership and saved-ID conflicts refuse.
   Controlled restore performs no client updates. Publication metadata is derived
   from and verified against the exact closed SQLite snapshot, preserving a
   revocation even if it interleaves with export. Export rejects source links
   before dereference and preserves links during staging to reject raced-in ones.
6. Only existing owned resources may change. Inspect all effective systemd load
   paths, exact/prefix/type drop-ins, outgoing `.wants`/`.requires` edges, loaded
   fragments and runtime configuration before service execution; refuse foreign
   definitions instead of overriding. Declared fragment dependencies and reverse
   target links from our enable operation remain allowed.
   Repeating the same desired state is health/backup work, not an unnecessary
   service restart or new identity. URI identity checks are explicit errors,
   independent of Python optimization.

Mihomo domestic/Bili and node/subscription-underlay DIRECT rules belong in the
upstream supported inline routing setting. The phone URI feed updates nodes,
not Shadowrocket policy: its local domestic-direct rule mode must be verified.
No existing airport profile, TUN, system proxy or app selection is overwritten.

## Limits that survive the implementation

A single node cannot provide availability while it is dead. A stable URL can
resume after database/certificate restoration and DNS changes; it does not
migrate existing TCP connections or revoke a lost host. If the old host leaked
SubIDs, retaining those URLs retains those leaked capabilities. Compromise
recovery requires new SubIDs and client re-import. Upstream management remains
a privileged process; loopback/public-path isolation limits network exposure,
not the consequences of a privileged-process exploit.

Cloud firewall, HTTP01, resource use, REALITY/negative-authentication handshakes
and desktop/mobile refresh are per-deployment acceptance gates. Existing live
observations do not establish future renewal/reboot behavior or other client
paths. Isolated parsing alone cannot establish them.


## 运维参考

日常三步入口见 [README](README.md)。以下保留故障恢复和维护所需的完整约束。

## 前提与端口

管理 PC：Python3、OpenSSH、原生 `mihomo` 或 `verge-mihomo`。远端需已有 Python3、
OpenSSL、systemd/`systemd-analyze`、`ss`、`nft`、可信 CA 包及至少2GiB可用空间；工具缺失会明确停止，
脚本不自动安装系统依赖。仅支持 SSH 最终 HostName 为公网 IPv4 的已核验别名或
`user@IPv4`，以及 root / 免密 sudo；不绕过 known_hosts，不支持 NAT 地址映射。
首次先从服务商控制台核验 SSH host key，再按正常 OpenSSH 流程登记。

| 入口 | 归属 |
|---|---|
| TCP443 / UDP443（默认同一数值） | 同一 Xray 的 VLESS/TCP/REALITY/Vision 与原生 Hysteria2 |
| TCP8443 | Caddy 可信 HTTPS；每个启用设备仅两条精确订阅路径 |
| TCP80 | Caddy HTTP01 证书签发/续期；普通 HTTP 请求全部404 |
| 127.0.0.1:2053 / :2096 / :62789 | 面板 / 原生订阅后端 / Xray API；不公开 |
| 实际 SSH 端口 | 原样保留，`--port` **只代表 SSH** |

cdn 三入口迁移后 TCP443 归 Caddy（CDN TLS 终止 → 回环 XHTTP2080），REALITY 迁移到选定的独立
TCP 端口（默认9443），UDP443 仍属 Xray H2，见下文「CDN 入口模式」。

唯一代理端口设置是首次 `--proxy-port`，默认443，保存在原有四键私有 config 中；TCP与UDP共用数值，
不与上述保留入口或实际 SSH 端口冲突。两个入站、原生导出、分开核验的TCP/UDP进程/端口及备份共用此设置。
以后不会隐式改监听端口；换端口需单独规划迁移——cdn 三入口迁移即是该规划迁移：REALITY 监听/广告端口
改为 tcp_cdn.reality_port，config.proxy_port 与 H2 UDP 端口不变。没有证据表明3280更不易被封，
工具不会自动选它。REALITY 目标站自身的 TLS 端口仍为443，独立于代理监听端口。

域名由用户控制并设置 **DNS-only/灰云** A 记录；不需要 Cloudflare API key。
HTTP01需要公网80可达，Caddy禁用 TLS-ALPN 挑战和自动重定向，明确不占用443。
ACME邮箱**默认不提供、不提示填写**；配置保留`acme_email: ""`。只有用户首次明确传
`--acme-email`才使用经验证的联系人，不从截图或账户自动取值；旧配置已保存的联系人
在省略参数时原样保留，不自动清除。签发机构固定为Let's Encrypt，不自动换其他CA。
Caddy自动续期依靠其任务和HTTP01/DNS可达，不依赖邮箱提醒。
[Let's Encrypt公告](https://letsencrypt.org/2025/06/26/expiration-notification-service-has-ended/)
说明到期通知已于2025-06-04结束、新提交联系人转交ISRG独立系统；此工具默认不传联系人，
不承诺CA邮件提醒，也不额外接入通知服务。
已有防火墙规则、占用端口、外来同名 unit 或旧代理目录会拒绝，**不清空/开放规则、
不停止外来服务、不接管旧机器**。四个unit在实际systemd加载路径中的同名定义、
精确/前缀/类型drop-in、四个名称的`.wants/`/`.requires/`外加依赖边，以及有效
FragmentPath/DropInPaths/SourcePath/运行时配置均检查；悬空/链接依赖目录也拒绝。
自己的正常依赖写在完整unit里，enable生成的反向target.wants链接不受此限制。
执行服务操作前再次核验，发现外来或未重载配置就拒绝，不删除override或依赖边来绕过。
云防火墙由操作者审阅并开放所选入口。

## 其他运维命令

首次检查/部署/换机的完整配方见 [README](README.md)。以下仍从仓库根目录运行：

```bash
bash vps/setup.sh deploy --ssh root@203.0.113.10 # 同目标短重跑，保留已保存端口/密钥
bash vps/setup.sh recover                     # 处理本地未完成事务
# 从新管理电脑受控恢复，明确保留原代理身份；必须指定完整私有备份和新目标。
bash vps/setup.sh restore --ssh root@203.0.113.20 --backup /private/path/backup.tar
# 使用独立权威状态时，全局参数放在子命令之前：
bash vps/setup.sh --state-dir /private/vps-state status
```

首次可选 `--proxy-port 4443` 更换代理监听端口；这不是 SSH 端口。
仅在明确愿意提供联系人时首次加 `--acme-email operator@example.org`，默认不提供。
需要指定管理端原生核心时，deploy/replace/restore 和设备变更命令可加 `--mihomo /path/to/mihomo`。

`preflight` 不保存部署配置或创建远端目录。所有远端命令都使用正常 host-key
验证和新 SSH 进程，禁用复用、跳板和转发；显式传递的身份路径只保存引用，不复制私钥。
同一已保存SSH目标的短重跑保留原身份/端口，除非明确覆盖。解析得到的IP、用户和端口
直接绑定每次OpenSSH传输，仍保留严格host-key验证；别名在预检后改变也不能把私有归档
发送到另一台已信任主机。缺少可恢复状态、损坏文件或错误机器身份会停止，不自动猜测当前节点。

默认私有目录为 `~/.local/state/quick-deploy-private/vps`，目录0700、文件0600。
需独立目录时在子命令**前**传 `--state-dir /private/directory`。
`state.json` 保存配置、当前 SSH/machine-id、历史目标和离机备份位置；
`outputs/` 保存原生 profile、URI 文件及 `subscription-urls.json`；均含秘密，不入 Git。

部署只解包 [release-lock.json](release-lock.json) 中核验的官方3x-ui3.10.2、
Xray26.10.10、Caddy2.11.7二进制和 geodata，**不运行上游安装器**。
候选先运行回环订阅；Caddy取得可信证书但返回404。原生 Xray检查/运行版本、
全部启用设备的 Mihomo解析和URI隔离、实际进程/监听、可信证书及完整离机备份通过后，
直接公开订阅，不要求二次输入确认。首次缺少域名/REALITY目标时仍需提供配置数据。
任何证书错误均不能降级为公网 HTTP 订阅。
这一步的程序门槛不替代真实客户端连接/刷新验收。

## 仓库内订阅副本与提交边界

固定便利副本为 `vps/subscription-urls.json`，不取代外部 `state.json`、
备份或原生数据库。原生 UUID/SubID 与设备键 company/home/phone（公司电脑/家里电脑/手机）
不因导出改变。仅从本地**已提交状态指向的、验证通过且 owner/config/当前端点一致的完整备份**
生成启用设备 URL；不读取可能来自失败候选的 `outputs/subscription-urls.json`。
正常部署的备份在发布前生成，其 manifest 的 published 标志可能为 false；本地提交记录及匹配备份
才是离线导出的依据，不能单凭备份中 published 字段判断提交结果。

- deploy（包括同状态重跑）、replace、restore、devices 变更在本地保存提交并移除 pending 后导出。
  recover 仅在确认远端早已提交、健康核对并取回新备份、保存本地提交后导出；中止候选不导出。
- 导出失败不会回滚服务器、伪造 pending 或擦除恢复依据；命令以失败码明确说明**部署已经提交**。
  修正本地目录/权限/Git 忽略后，运行 `bash vps/manage.sh export`（先 connect 读取、不改服务器；有 pending 则跳过 connect 并拒绝导出），无需重装。
  存在 pending 时拒绝离线导出，先 recover，不猜测提交结果。
- 副本是**已保存快照**，不是实时账户查询。面板里撤销/轮换账户后，按下节重跑同目标 deploy，
  核对原生状态并生成新备份；单独 export 不会发现未同步到备份的远端变更。
- `--state-dir` 只改变外部权威状态，副本仍固定写到当前仓库的 `vps/subscription-urls.json`；
  **最后一次成功导出覆盖前一次副本**，请先确认选对状态。不同状态不构成订阅合并。
- 写入前检查实际 Git 忽略和未跟踪状态；发现已跟踪的订阅副本或专用临时文件就拒绝，不自动 untrack。
  沿用现有 `vps/` 代码目录及权限，不创建 private 目录，不 chmod 代码目录或其他模块文件。
  目录要求当前用户所有，已有订阅文件要求0600、普通文件，拒绝符号链接。
  固定目录描述符下写入临时0600文件再原子替换；写入/替换失败保留旧副本，正常退出清理临时文件。
  `/vps/subscription-urls.json` 和 `/vps/.subscription-urls-*.tmp` 均精确忽略；
  中断留下的专用临时文件也不暴露为待提交文件。不修正或删除无关路径。

## 导入、管理和设备变更

- Verge：从私有 `subscription-urls.json` 选自己的 **Mihomo完整profile URL**，
  作为独立订阅导入；不覆盖机场、Script、TUN或系统代理。使用上游支持的内联规则：
  节点IP/订阅域名/私网与中国/Bili直连，其余 `MATCH,PROXY`，保留手选 PROXY 组。
- Shadowrocket：导入自己的 base64 URI订阅 URL。URI只更新节点，**不包含手机分流策略**；
  必要时先停VPN再直连刷新。
  - 当前固定的 **Xray26.10.10 REALITY** 要求客户端 TLS 指纹提供 `X25519MLKEM768`
    key share，且位于可选的 `X25519` 之前。到 **设置 → TCP → 指纹** 选择明确标注
    支持 `X25519MLKEM768` 的 profile；**Chrome140 已经真人手机复测可用**，其他 profile
    未在本部署实测。以实际握手能力为准，不把浏览器名称/版本当作永久兼容名单。
    订阅中的泛称 `fp=chrome` 不保证有效指纹满足要求；同时检查节点级指纹覆写，勿假定全局选择必然生效。
  - 排障可临时设全局路由为「代理」、选自己的节点并关闭回退，再验收实际 HTTPS 浏览；
    TCP 延迟或 VPN 开关亮起不能证明代理认证成功。成功后保留可用 TLS profile，切回「配置」/规则模式，
    **单独核验国内/Bili直连及代理选择**，不要把全局代理测试当作日常分流验收。
  - 维护依据：[固定 REALITY 握手条件](https://github.com/XTLS/REALITY/blob/8cdf7bf9c7f0/tls.go#L214-L238)、
    [Shadowrocket 指纹选择说明](https://github.com/MHSanaei/3x-ui/issues/6596#issuecomment-5936563160)。
- 面板通过 `ssh -N -L 2053:127.0.0.1:2053 <已核验目标>` 管理，按需加自己的 `-i/-p`。
  私有备份中 `deployment/manifest.json` 保存 admin_path/admin_user/admin_password；
  在私人本地查看器读取，勿贴聊天/工单。浏览器只访问该 SSH 隧道的本地管理路径。
- 首选脚本 `devices list/add/rename/remove/reset` 管理名册和钥匙，见下节。面板 **Clients** 可单独禁用
  已有设备或修改其策略；之后重跑同目标 `deploy` 同步精确公开路径及备份。面板擅自新增/删除成员会拒绝，
  status/backup/export 不会把未知原生账户静默收编。
  v3.10.2 的全量 inbound 更新会保护全局 client 记录，不能把旧表单当作凭据轮换接口。

重跑同一状态不改变 UUID、SubID、REALITY密钥或Xray进程。为一致地复制证书/密钥，
每次完整备份会短暂停止并恢复**自己的订阅前端**；不停止Xray或SSH。

## 设备名册与钥匙事务

命令配方见 [README](README.md#设备与钥匙管理)。`devices list` 只读当前原生名称/启用状态，
不输出 UUID、SubID、URL。新装仍默认3个；`devices add device4 device5` 才扩到5个。
任意合规设备名都可使用，不存在第二套五设备白名单。manifest 的 `accounts` 键拥有成员关系，
原生 SQLite/API 拥有完整策略与凭据；旧快照校验唯一入站关联，双协议快照校验所有 global clients 到两个受管入站的精确全部关联，
三入口快照校验到三个受管入站的精确全部关联，
包含禁用、未挂载账户及悬空关联。未知成员、不同 owner/machine、Host归属冲突、重复名字或
凭据碰撞会拒绝，不使用直接修改账户表的 SQL。UUID按解析后的字节身份比较，
不改已有DB/元数据的大小写拼写；新增/轮换要避开整个旧名册（包括目标自己的旧UUID和SubID）及本批新钥匙。旧3、新5、删除后的4以及空名册均可备份/恢复；
恢复不会隐式添加默认账户。移除最后一个设备是原生支持的空名册，前端全部404。

- `add <name> [<name> ...]` 通过原生客户端新增接口建立独立 UUID/SubID，只写新账户；
  原账户完整对象、禁用/到期/配额/HWID/reset策略及 Reality密钥保留。全部名字已存在时幂等；
  新旧混合名字、重复名字明确拒绝。已删除账户不会因重跑 deploy 自动复活。
- `remove <name>` 删除该原生账户并移除其两条前端路径，旧 UUID 不能建立新代理连接，旧 URL404。
  已不存在的名字为幂等无变化。便利JSON不再列它，历史归档不自动删除。
- `reset <name>` 从新读取的完整 Client DTO及独立LimitHwid更换该设备 UUID、SubID及双协议时的Hysteria2 Auth，
  保留完整策略与禁用状态、其他设备、全局Reality密钥；旧订阅不能取回新钥匙。
  客户端必须重新导入新 URL。它不是幂等的新操作：每次明确发起都会产生新钥匙，失败先 recover。设备动作唯一名称是`reset`，无旧命令别名或转换层。含已移除动作的pending明确拒绝并原样保留供人工核对，不转换/重放。
- `rename <old> <new>` 使用原生`/clients/update/<old>`，提交完整新读取Client及LimitHwid，只改email。
  原生稳定ClientRecord ID、入站join、UUID/SubID、完整策略、HWID限制及已持久化用量随新名保留；
  不删除重建，也不直接改账户SQL。已用名称/不存在旧名/非法名拒绝，同名明确无写入拒绝。
  本地先通过只读status核对名册再备份，远端事务写入前复核；operationID绑定有序OLD/NEW。
  新名若仍有原生流量/IP跟踪历史（即使没有账户）也拒绝：原生接口可能复用/丢弃这些行，工具不合并或删除历史。
  预检只返回指定新名的占用标志，使用与原生一致的参数化相等查询，并在原生写入前再查一次。
  最新JSON键改为new，URL和全部公开能力路径不变，无需重新导入；仍沿用核心重启，所有设备可能重连。
  本地提交先保存权威backup指针，再仅移除原名两个受管理0600普通profile文件，最后清pending；
  未知文件/链接拒绝删除并保留已提交pending供recover重试，不清理无关输出/历史归档。
  旧`outputs/`或失败候选不是导出权威；canonical JSON始终从最新已提交备份生成。

设备变更沿用既有 operation flock、15分钟 boot/deadline pending、guard及非阻塞周期恢复。
不增加后台管理服务或另一套数据库：

1. 管理端先检查名字/原生Mihomo可用性（命令名按PATH解析为绝对路径并存入pending），以只读status核对存储adapter摘要（未知摘要不触发备份停前端），
   提示所有设备可能短暂重连（不等待确认），再取得变更前离机备份。
2. 远端核对 owner/machine、单一Host/入站、全局账户/关联、unit及二进制。
   存储适配器只接受当前上传源码摘要及三个固定已审阅公共源码前身：18d1b3b `30430e73…09a000e`、
   历史918103e `8777a3d4…ed701f1`、reset-only ae72036 `b52adbbf…25f6d7e`（与基线6c02d25的host.py字节相同）。
   当前生产只读核验的adapter属于最后一种，不能沿用旧记忆假定仍为918103e；
   未知摘要在任何适配器/服务变更前拒绝。不是包/数据库版本升级，也不能用任意scp替代。
3. 旧manifest/front/adapter及服务启用/运行状态、恢复意图先写入临时私有目录，再原子发布为
   `device-rollback/`。操作ID绑定owner/machine/端点/config/动作/名字的请求摘要；未提交绑定
   与已提交receipt区分，回滚后也不能用同一ID偷换请求。无变化操作同样留提交receipt。
4. 安装经摘要核验的当前guard/recover适配器后才写pending、停止自有front/panel，生成关闭的
   原始DB快照；`snapshot`阶段失败仅恢复原服务，不用不完整快照覆盖原DB。完整快照后先写`ready`
   再执行原生API。逐项新增失败会恢复整个原名册，不遗留未知第4账户。快照前适配器安装中断
   可由已落盘的恢复意图识别；尚未发布的暂存目录不影响旧服务。
5. API后显式停止并启动自有panel/core进程：旧已建立流会关闭，**所有设备都可能短暂重连**。
   原公开精确路径在前端恢复后继续使用；新增/轮换后的路径在此时仍404。原生feed策略受限时
   不放松HWID/到期等策略来通过验证；需单独授权设备上下文，否则验证失败并回滚。
6. 原生feed校验和候选离机备份通过后commit才写新精确allowlist，等待可信TLS/进程及lease验证，
   然后持久记录提交receipt。发布尝试与receipt之间失败会恢复原状态；它不是跨DB/HTTPS的零停机原子切换。
   receipt之后丢失SSH回应按已提交恢复，不再次轮换。变更后另取一份已发布状态的离机备份，
   验证输出，先保存本地最新backup指针/清pending，再导出便利JSON。

`recover` 在未提交时恢复关闭的原DB、manifest、front、旧adapter及原服务启用/运行状态。
恢复前先持久写`recovering`并停止相关服务；此时guard无论lease是否到期都拒绝启动，防止半恢复DB运行。
原DB/manifest/front全部恢复后才写`restore-queued`，按原enabled/running状态用`start --no-block`排队，
立即返回恢复oneshot，**本轮不等待API/TLS**。PANEL/FRONT均After=recover，必须等该oneshot退出才可启动。
下一轮既有timer或显式recover先确认服务已实际active，才能核验API/TLS/进程、清pending和备份停机意图，
最后恢复旧adapter。服务尚未起来则重排并返回queued；就绪失败停止相关服务并保留意图，绝不假报恢复完成。
中断/重启可以重试；仅完整恢复后的`restore-queued`可以越过旧候选lease启动，过期候选本身不能复活。
已提交与未开始/已回滚由设备操作receipt区分，旧active目录存在不代表设备操作已提交。
已提交后的离机备份/传输失败保留本地pending，运行recover取新备份并对齐指针；不谎称回滚。
queued/pending响应（即使committed为true）也不会清本地pending或导出；等oneshot退出后重跑recover。
`--mihomo`选择会保留；旧pending或原路径失效可用`recover --mihomo /path/to/mihomo`覆盖，仍必须通过原生解析门槛。
仅便利JSON失败时，本地权威已提交，修正权限/忽略后运行export即可，不需要重新轮换。
成功/失败的私有回滚目录和历史备份保留供审阅，容量手动管理。

历史归档不是撤销账本：**恢复删除/轮换之前的旧归档会恢复旧账户/钥匙**，应选择最新撤销后备份。
replace继续轮换该归档名册全部UUID及Reality、保留其SubID/策略；restore保留该归档全部身份，
两者都不访问或撤销旧节点。设备管理没有另建PKI或自动清理历史凭据。

## 在新电脑连接已有节点（connect）

`connect --ssh` 以服务器为唯一事实来源：先 `connect-info` 取 owner/machine_id/node_id/endpoint/config，再 `connect-pull`
拉取一份当前的标准完整归档（不调用 `backup`，不停前端），本地用不变的 `inspect_backup`、`validate_outputs`
（含 Mihomo 格式门）和 `remember_commit` 提交，因此 `state.json` 是最后一步。两个远程动作只在这两个精确 action 下
把 host.py 原字节与公开的 `connect_read.py` 作为非 main 模块执行，传输仍是 `vps.remote`（严格 known_hosts、不复制密钥）。
不读 `config`（目标由 manage.sh 传入 `--ssh`）、不生成随机 owner；已有绑定仅在 owner/config/endpoint/machine_id/node_id 一致时刷新，
旧记录无 node_id 时若旧归档仍可用才核对，缺失不阻止恢复。读取是乐观的：并发变更导致拒绝重试，而非停机。

## 备份、冷替换与恢复边界

每次部署公开前及 `backup` 都把恢复集取回管理PC：一致、关闭且独立于 WAL/SHM 的
SQLite数据库，SQLite/schema/version信息，原生账户映射，外部证书与Caddy续期存储，
完整release包、校验信息、环境/unit配置、适配器。账户元数据从**同一关闭的数据库快照**
读取，恢复前核对一致性；不把较早的API投影当作账户写回来源。受控restore不更新client，
replace从完整、新读取的原生Client/账户对象仅更换UUID，并保留禁用、配额、到期、IP/HWID
限制和reset策略。新IP必须通过原生**Hosts完整group API**迁移；只改旧externalProxy元数据
不会更新真正订阅。工具先核对唯一SELF-main归属、已存groupId、全部Host成员和旧地址/端口，
完整读取Host group，仅变更地址，再回读数据库/API和实际订阅；其余Host字段保留。
源版本原生归档若缺groupId，只能在完整归档验证后从同一关闭数据库的唯一旧端点关系推导，
不改原始归档；额外/重复/隐藏或禁用的额外成员、悬空关联、已存ID或地址不符一律拒绝。
这不是对旧sing-box归档的自动导入。备份不要求受限/过期账户能成功下载feed；但部署发布的feed门槛仍独立检查。
导出前拒绝来源树中的文件/目录symlink，复制时也保留链接再拒绝，不跟随到树外秘密。
损坏DB、账户元数据不一致、缺证书/私钥或版本不匹配会拒绝。

所有共享备份入口（包括首次变更前、提交后、recover核对及独立backup）在停止前端前都会：
核验并安装当前已审阅恢复adapter，仅接受当前摘要及固定的18d1b3b/918103e/ae72036公共源码摘要；
新增ae72036全摘要为 `b52adbbfb7efc3593c5fa266e8f148ed60deaa2b685d72b926e549b9425f6d7e`，
由本地Git的ae72036和6c02d25两份相同公共host.py字节独立核验，不以远端自报摘要建立信任。
旧adapter先存私有摘要命名文件。18d1b3b导出为`previous-host.py`，918103e为`previous-host-918103e.py`，
ae72036为`previous-host-ae72036.py`。三个可选输入都逐一验证，不动态扩张信任表。
可选旧副本不存在是合法的；已存在时以no-follow/nonblocking文件描述符读取，
仅接受普通文件且字节摘要必须匹配其文件名对应的固定已审阅摘要，链接（含悬空）、FIFO/目录或坏摘要在迁移/停前端前拒绝。
新副本完整落盘后以不覆盖方式发布并复验，迁移后再复验；归档只用已验证缓存字节，不再按路径读取外部输入。
安装前中断旧服务未动；安装后中断已有新adapter可恢复，旧adapter不会忽略新停机意图。
随后持久写`backup-stop.json`，绑定owner/machine/node及front原running/enabled状态，再stop FRONT。
正常finally启动/等待可信TLS后清意图；硬退出不依赖finally，由既有recover先排队非阻塞恢复front，退出oneshot，
下一轮验证TLS后清意图。原front未运行就保持不运行。已提交情形只恢复当前front，不回滚已提交钥匙。
过期未提交候选优先走设备/新机回滚，不能借备份恢复启动过期候选。每次恢复核对中的再次备份也用同一停机意图。
管理端在没有已有pending时先保存backup模式意图；没有本地pending也可凭已保存current运行recover修复远端停机。
这次经过核验的恢复adapter迁移是共享备份恢复的一部分，不升级原生包/DB，也不改变账户/路由/系统unit依赖。

**备份是未加密 TAR。** SSH传输加密、0700/0600权限不是静态加密；请另用既有的
加密存储/导出流程保存到第二个离机位置。工具没有内置加密导出。定期演练新管理PC恢复，
使用最新、尤其是设备撤销之后的备份；旧备份仍可能包含已撤销的订阅能力。

`replace` 在新机恢复原生 DB、SubID和证书，默认更新节点密钥及每设备代理UUID；
`restore` 是明确保留代理身份的受控恢复。两者只接受新目标，不做原机破坏性覆盖。
随后操作者将灰云DNS指向新机并让各端 REFRESH；证书过期时，签发前就需要新机HTTP01
可以验证DNS。换机有停机，DNS不由工具修改，旧机没有被访问、清理或撤销。
泄露的SubID若保留URL也保留泄露能力；失控旧机/订阅泄露需轮换SubID并重新导入客户端。

失败候选的持久pending记录包含启动标识及15分钟期限，配合systemd恢复服务、周期检查
和启动前guard，在SSH丢失、超时或重启后停止/禁用候选。周期恢复遇到事务锁会立即退出，
等待下一轮，不与有服务顺序依赖的start/restart互相等待。guard检查启动标识和期限；
commit在锁内、发布前再次检查lease，不依赖timer恰好先触发。URI身份门槛使用显式错误，
Python优化模式不会删除它。失败私有目录保留供检查，不删除。
同状态重跑不替换已有工作包；新机失败不会修改旧节点。没有假称“配置回滚也降级了包”。
**原机二进制/数据库版本升级尚未实现**：不同release lock仍拒绝，不能点面板一键升级或改成latest绕过。
同目标 `deploy` 的TCP→双协议功能迁移使用下述专属事务，不改变三份已固定原生版本。
磁盘上的历史备份/失败目录不会自动清理，需操作者审阅容量。

旧 `identity.json` / `inventory.json` / `generated/` 和远端 sing-box/旧role目录明确拒绝，
不会删除或导入为新账户。旧 `init/render/check/rollback`、`--role/--host/--device/--without-tailscale`
接口已删除；历史实现保留在Git，不复制归档。其他模块的可选旧JSON读取器未改，新的profile
不会写入它们的旧输入路径。

## 本地静态连接资料

`dashboard [--output-dir DIR] [--device NAME]` 与设备 bootstrap export 共用现有 pending 拒绝、
`checked_backup` 和 `read_generation`：完整已提交备份 SHA/owner/config/端点/名册/原生文件 hash
全部匹配后才绘制。当前与 bootstrap 一样要求与归档模式精确匹配的 generation（旧单 TCP、双协议或三入口）；
旧模式快照不会被静默忽略或补造节点。
节点 URI 仅从原生 base64 feed 解码，排除记录换行后保留 UTF-8 字节及百分号转义；fragment 只作界面标签。
订阅 URL 复用 `subscription_urls`，不另做入站/节点编码器，不读取/写入生产数据库，不建立网络连接。
禁用设备可显示状态但无凭据条目；“启用”不是在线连通性或 TCP 可用性判断。

固定本地编码器是官方 Nayuki qrcodegen1.8.0 单文件 MIT 实现，`lib/vendor/manifest.json` 记录
官方 PyPI sdist SHA256、GitHub v1.8.0 源码 URL 及文件 SHA256；两官方来源的代码字节相同。
编码使用 UTF-8 ECI/byte segment、中等纠错、确定性黑白 SVG、四模块 quiet zone，容量不足明确失败且不回显秘密。
这是本地工具 pin，不是服务器第四组件；原 `release-lock.json` 不变，无 pip/系统依赖安装或运行时下载。

渲染先在内存生成全部字节。输出复用 no-follow 目录描述符与当前 owner0700/普通文件0600检查，拒绝 Git 内目录。
页面只引用 SHA256 命名的不可变本地 CSS/JS/QR/配置；现有四个 bootstrap 文件名及独立管理配置语义保留。
`.qd-dashboard.json` schema1 精确绑定内容 hash 文件名及完整设备 bootstrap 文件组，不接受任意路径。
发布前检查全部旧受管及新目标文件，先持久记录新旧 inventory 并集，再写新文件，最后原子替换 `index.html`；
随后仅清理旧 inventory 中不再引用的文件并压缩 inventory。发布中断不会令旧 index 引用新的 QR 或配置，重试可清理已登记残留。
用户旁置文件不参与清理；已有未知 index、篡改的受管资产或不安全路径明确拒绝。
已有管理运行时仍要求显式管理参数并重新执行本地 Mihomo 格式校验，不因网页缓存跳过。

HTML 的单一 `__QD_DATA__` 替换使用 JSON，转义 `<>&` 和 Unicode 行分隔符，阻断 script-end 注入；
界面标签通过 DOM textContent 显示。schema1 的附加 `files` 映射只内嵌页面明确列出的 QR/配置下载的原始字节（base64），
用于 file:// 下 Blob 下载，避免浏览器忽略本地 `<a download>` 或拒绝本地 fetch；不枚举私有目录，不内嵌备份/状态或其他文件。
所有二维码、URI、HTML 和下载都是私有能力资料，隐藏链接不等于加密。
生成时间指本次渲染时间，备份 hash 绑定原生来源；页面没有健康探测、远端刷新或账户 CRUD。
重置/撤销后需要先同步权威快照再重新生成，历史导出、已分发二维码及旧备份不会自动擦除。

## 隔离测试与生产门槛

```bash
python3 -B -m unittest discover -s vps/tests -v
# 实际native包验证：先从release-lock固定URL下载并核验三份包到私有目录。
QD_VPS_ARTIFACTS=/private/verified-artifacts python3 -B -m unittest discover -s vps/tests -v
bash -n vps/setup.sh
```

未给包目录时native测试明确skip，其余是假主机/文件/API边界测试。Native测试启动临时
**高端口回环**3x-ui/Xray/Caddy，检查真正输出、凭据轮换/禁用、Mihomo解析、闭合数据库恢复、
TLS精确路径和错误证书拒绝。TLS正向测试使用fixture CA，**不是公网ACME签发证据**。
设备测试还使用本地Caddy混合TLS目标、原生Mihomo经REALITY到回环echo服务，证明新钥匙可用、
旧UUID拒绝和reset/remove已有流关闭，并核对真实Caddy旧URL404/新URL200、未影响的其他设备。
echo测试的精确origin端口路由例外和freedom最终规则只存在于临时fixture，绝不写入生产；
原统计API阻断仍保留。systemd控制/公网CA探测以fixture替代，存储adapter代码被独立载入验证
boot/deadline及恢复中断。额外fork/os._exit测试绕过finally，覆盖首次/提交后/核对备份stop；
根据真实unit的Before/After/oneshot构造的任务图只在oneshot退出后执行排队start，并禁止提前等待就绪；
旧82235b2源码在同图内重现自等超时，新源码通过崩溃/换boot/失败关闭对照。这不等于真正OS重启/生产服务验收。
真实systemd、云防火墙、HTTP01签发和真实客户端公网HTTPS/出口必须逐部署验收；
当前节点已有的验收不能证明未来续期、重启恢复、持续资源峰值或其他网络路径。
新的生产接触仍须单独授权，本地快照导出不接触生产。

## 原生双协议与同目标功能迁移

私有 config 仍是原来的四键，global FORMAT仍为1，三份release pin和四个systemd unit不变。
manifest增加 `management.ssh_port`，值由已认证SSH连接的服务端端口核验，不猜22；
`hysteria2` 为显式schema1元数据，保存原生H2 inbound ID、Host group ID以及相对deployment的证书/密钥路径。
缺少该标记只在严格旧TCP单入站、单Host、每账户单join拓扑下合法；双协议要求原SELF-main与新增SELF-h2、
两个精确Host和每个global client到两入站的精确join，不收编外来或隐藏/禁用的额外行。
三入口模式额外要求 tcp_cdn 元数据及三个入站/三个单入站 Host/每 client 三个精确 join（见 CDN 入口模式）。
原REALITY inbound/Host、UUID/SubID、完整原生策略和密钥在功能迁移中保持；H2 Auth由原生attach生成。
VLESS inbound DTO是协议投影，不携带H2 Auth；校验从global原生DB读Auth并跨核对H2及原生feed，
改名/重置/换机则从global ClientRecord补Auth与独立LimitHwid后提交完整Client DTO，绝不把Record形状直接写回。

新机顺序为私有TCP候选 → Caddy可信HTTP01证书且前端404 → 原生空H2入站及existing client attach →
双协议核心/端口/feed验证 → 离机完整备份receipt → 发布。恢复旧TCP归档到新目标也走这一步，不改原归档字节。
双协议归档恢复时，原生绝对证书路径按新active位置重写；Host完整group地址更新，其他字段保留。
同目标deploy的功能迁移使用 `transport-prepare` / `transport-commit`，不是二进制升级，也不是device add/replace。
请求含owner/config/endpoint/machine、operation ID、上传源码摘要、已验证ssh_port及 `mode=transport-upgrade`；
`before_sha256`是必需的变更前离机备份摘要，始终纳入请求绑定并核对远端export-sha；源/目标拓扑也可绑定。
独立transport-receipts区分同ID不同请求，已提交重放不生成新Auth。
功能迁移写入任何rollback intent/receipt或操作服务之前，ROOT/host.py必须已等于上传的当前适配器摘要。
正常变更前备份先通过原有受核验机制安装能识别transport journal的新恢复代码，并保留固定旧源码；
不能把旧TCP-only适配器保存为新功能事务的回滚执行者，否则断电/重启可能将完整active误认成失败新机。
已经双协议的设备变更也要求当前恢复适配器先接手；旧TCP设备事务仍保留固定已审阅前身的兼容路径。

功能迁移复用设备事务的device-rollback关闭DB快照、manifest/front/adapter、原服务状态、15分钟boot/deadline及
recovering→restore-queued→后续就绪核验。新H2仅存在于同一原生DB/生成配置中，无额外daemon/unit/账户数据库。
准备期间旧订阅前端改为404，避免原有SubID在离机receipt之前泄露新增H2能力；commit才恢复相同URL。
commit要求候选离机备份摘要与远端最新export-sha匹配，且必须不同于before_sha256；旧TCP备份没有新Auth，不能充当双协议发布凭证。
提交后的传输失败凭durable receipt核对而不重新迁移；管理端取得并验证发布后备份再推进本地权威指针。
备份/outputs仅在prepared阶段接受该事务；unknown adapter和foreign unit拒绝机制不放宽。

证书必须是受管Caddy storage内该域名唯一的一对普通文件，拒绝链接、重复对、错误SAN或不匹配私钥。
不复制另一套key，不增加签发机构/续期daemon。原生TLS设 `oneTimeLoading:false`，Xray以默认3600秒周期重读稳定路径；
因此续期到消费者加载最多存在一个周期延迟，文件读失败可终止该ticker，需要修复后显式重启核心。
换证测试应替换真实匹配证书对并重启消费者，验证客户端所见证书变化；它不等于实际ACME续期证明。
生产仍要求正常公开CA/SNI验证，`allowInsecure:false`，不得skip-cert-verify。

原生订阅每设备仍是原来的两个精确URL，内容各含VLESS与Hysteria2；完整Mihomo仍由3x-ui编码，
supported inline规则将PROXY初始顺序设为 `[SELF-h2, SELF-main, DIRECT]`，保留手选TCP能力。
双协议profile另有 `FEED` fallback `[SELF-h2, DIRECT]`；仅TCP8443到订阅域名或本机公网/32的两条AND规则
位于原domain/IP DIRECT规则之前，其他私网/国内/Bili及MATCH规则tail不变，不截获UDP443或SSH。
FEED用无SubID的 `https://域名:8443/` 做HEAD健康检查，expected-status字符串`404`，interval300秒、
timeout5000毫秒、lazy=false。它依据该URL最近健康状态选首个可用成员，不会为同一失败请求同步重试DIRECT；
需等健康检查收敛并重试。root404只证明TLS前端/路径可达，不代表订阅token或原生feed内容有效。
旧TCP-only profile字节行为不变。已有客户端可能保留旧selector选择，刷新后需显式选择H2；
首次导入不能依赖已经不可达的TCP8443。离线local导入不会自动成为remote订阅；随后须显式通过
Verge的Clash-proxy导入/更新原remote URL，并确认实际生效。其更新顺序可先configured、再Clash/system-proxy，
不能保证每次请求都进入当前core；全局前置规则也可能遮蔽FEED。因此保留独立H2管理listener及私有curl配置刷新。
手机URI不携带FEED/Mihomo策略，手机实际代理刷新能力需单独核验；reset更换SubID后旧URL不能发现新秘密，仍须重新导入。
原生全局删除/禁用作用于两入站，reset同时轮换Auth/UUID/SubID；冷replace轮换Auth/UUID/REALITY并保留SubID，
rename保留Auth/UUID/SubID及完整策略。panel/core完整停启用于关闭已有TCP与QUIC会话。
HWID限制原生订阅访问，不是对已导入H2密码的物理设备绑定；quota/expiry仍使用原生全局账户机制，
当前统计API路由缺陷不因双协议自动修复，不能承诺不可信计数的配额精度。

仅认证SELF-h2入站新增首匹配路由：TCP到本机已验证公网/32的实际SSH端口和8443，送往唯一受核验direct/freedom。
其他self/private/API阻断保持；不开放127管理端口、不绑定服务端出口eth0、不改sshd/firewall/Tailscale。
H2管理依赖同一核心，重启/重置操作使用独立管理carrier直至恢复完成，不能把自断开的连接当作事务提交证据。
TCP与UDP监听分别核验PID/可执行文件/cgroup；TCP8443证书成功不能替代真实H2认证/HTTPS及错误凭据拒绝。

旧fast FakeHost只验证旧DB形状的事务控制，其enable_hysteria为明确mock、UDP inventory独立为空；
新test_dual_transport验证双拓扑/证书/receipt边界，真实三份固定二进制的双协议行为由test_native_dual单独验证。
所有这些均不代替真实systemd、公开ACME、公司/Y/手机实际导入和SSH-over-H2的逐路径验收。

## CDN 入口模式：新增 VLESS/XHTTP 经 Cloudflare，保留 REALITY 与 H2

`cdn` 命令把同一节点从双协议受控扩展为**三条并存的原生入口**，复用既有 `transport-prepare` / `transport-commit`
事务（`feature=tcp-cdn`），不是新事务框架。最终恰好三个原生入站、三个单入站 Host、每个 global client 精确三个 join：
SELF-main（保留的 REALITY，新 TCP 端口）、SELF-cdn（新增 XHTTP）与 SELF-h2（不变的 H2）；
不新增 daemon/unit/账户数据库/节点转换器/软件 pin，global FORMAT 不变。一个原生 global Client 同时加入三个入口，
UUID/Auth/SubID/启用/到期/额度/HWID 等策略不变。

- REALITY：保留原 main 入站 ID、remark SELF-main、根 Host group ID、全部密钥/shortID/目标/UUID 与 Vision flow；
  唯一预期变化是监听/广告端口迁到 `tcp_cdn.reality_port`（默认9443，命令行显式核验选择，无自动空闲端口搜索）。
  原生 HostGroup 更新会删除并重建其物理 Host 行：逻辑 groupId/入站身份保留，REALITY 物理行 ID 及自动
  created/updated 时间戳随端口变更重建——这是已证实的原生行为，不是账户/密钥身份丢失。H2 的完整 Host 行不变。
- CDN：新增入站 tag/remark SELF-cdn，监听 127.0.0.1:2080 明文 XHTTP（security none、path、mode packet-up），
  顶层 `disableFlow:true`；全局 Client flow 保留原 Vision，REALITY 有效 flow 仍为 Vision，仅 CDN 的
  per-inbound/per-join 投影为空，不做全局清空再修复。新增 Host 广告 CDN 域名:443 TLS（匹配 SNI/Host/path、
  ALPN h2、allowInsecure false），与回环明文入站分离。
- H2：UDP443、Auth、原订阅域名证书绑定、Host 与管理路由完全不变。
- Caddy 最终监听 CDN 域名:443，仅把精确 XHTTP 路径及子孙反代到 127.0.0.1:2080（不去前缀），其余路径404；
  全局 `protocols h1 h2` 保持不变——不引入 h3，UDP443 始终属于 H2。原 sub:8443 订阅与 HTTP01:80 保留。
  仅 CDN 模式设置全局 `grace_period 5s`，使持有的 XHTTP 下载在 FRONT 先于 PANEL 停止时有界关闭，早于控制调用的60秒期限；旧单/双入口渲染及 unit 不变。
  新证书是该域名在受管 Caddy storage 内唯一匹配的一对普通文件；H2 证书选择器按原订阅域名过滤，第二对证书不造成歧义。
- Cloudflare443 → 源站443，同主机名/SNI，无长期443→8443 Origin Rule。端口归属按活动模式：
  CDN 模式 FRONT 拥有 TCP443/8443/80，PANEL 拥有回环2080、REALITY 选定 TCP 端口与 UDP443；
  双协议模式仍是 PANEL 拥有 TCP443/UDP443。
- config 四键不变（本迁移要求 proxy_port=443，H2 继续使用它）；`tcp_cdn` 为独立 schema1 元数据
  （domain/inbound_id/host_group_id/reality_port/reality_tag/path/mode/证书相对路径）。`reality_tag`
  保存迁移前核验的原 main 原生 tag（通常 `in-443-tcp`），端口迁移、重启与冷恢复后必须显式保持。
  三种精确归档模式并存：旧单 TCP、REALITY+H2、三入口；旧 TAR 不改写，恢复旧归档不隐式升级模式；
  未发布过的旧两入口 CDN 形状缺少新字段，明确拒绝，不做兼容模式。
  PROXY 顺序 `[SELF-h2, SELF-cdn, SELF-main, DIRECT]`，FEED 仍为 `[SELF-h2, DIRECT]`；
  H2 保持默认，无 GUI 自动选择；原生 H2 管理导出字节语义不变。

事务有两个入口：控制器在任何备份前经物理接口查询权威 A；精确单一源站 IP 保留灰云流程，
否则必须全部为 CF IPv4 边缘并通过正常 CA/SNI TLS、精确 peer 与匹配 CF trace，才选择已橙云流程。
混合/错误/私有 DNS 或无效 TLS/trace 在备份前拒绝。已橙云不读 stdin、不等聊天、不要求切回灰云。
Host 不以 ambient DNS 的 A/AAAA 作为源站所有权：绑定的 SSH/owner/machine/endpoint/config、原生 API、
正常源站证书、精确 native nonce 与实际 VPS 出口继续独立核验；CF IP 不是源站归属证明。
HTTP01 始终走 HTTP80；橙云 Full 下 HTTP 请求仍可明文回源，实际挑战路径须原样到达 Caddy80，
不导入候选证书、不改变现有 bootstrap 顺序或900秒租约。

事务顺序：只读核验旧双协议状态与选定 REALITY TCP 端口可用性、变更前离机备份（先安装当前 guard）→
持久化绑定请求/回滚意图 → FRONT/PANEL 停止期间取**关闭的 Caddy storage 子树**与 DB/manifest/front 快照
（phase=ready 前完成，半成品不用于恢复）→ 旧 REALITY 仍占443期间，临时8443纯证书 vhost 取得新域名公开可信证书
（可预测的 HTTP01 bootstrap，不宣称 HTTP01 普遍必须灰云）→ 经完整原生对象把 REALITY 迁移到选定端口
（保留 tag/密钥/设置/Clients）、新增顶层 disableFlow 的 CDN 入站（完整 Client DTO，不以 manifest 投影代替）
并显式只加一个 CDN Host → 核验 REALITY 在新端口、XHTTP 仅回环2080、H2 UDP443、精确三角色与有效 flow，
**确认 TCP443 已释放后才启动最终 Caddy443** → prepared 返回绝对 deadline，订阅不发布，H2 保持默认 →
仅灰云入口由操作员在有限窗口把 cdn 转橙云并输入 ORANGE；已橙云入口直接继续 → 控制器重新查询权威 DNS/TLS，基于实际原生输出，在专用 noTUN/loopback 临时客户端中强制
唯一 SELF-cdn 路由探针（绑定物理接口与新鲜 CF 边缘；私有127控制器 PUT 相同配置待204为就绪屏障；
核验真实 CF 边缘 TLS、内层 HTTPS+VPS 出口、错误 UUID 拒绝后正确恢复、重连/短空闲、原 H2 新鲜认证）→
全部通过后持久化私有 proof，随候选归档摘要一并 commit（proof 绑定 operation/请求指纹/候选 SHA/域名，
是控制器证明而非密码学远端观测，不设永久在线健康标志）。超时/失败走既有 abort/recover：**先停候选 FRONT 释放443，
再恢复旧 REALITY443**；恢复关闭的原 DB/manifest/sub:8443-only Caddyfile 与关闭的证书存储，移除新增 CDN 入站/Host
并还原 REALITY 端口/Host/tag，保留原 H2；recovering → restore-queued → 后续实际就绪（含原 H2 证书对/绑定/Auth）后才解除；
CF 侧手动改动不在 guest 回滚范围内，cdn 可保持橙云；重试前必须完成对账。丢 ACK 凭 durable receipt 对账，不重放迁移或轮换。

错误 UUID witness 复用既有有界机制：仅捕获原模板、停止 PANEL 后只写 xrayTemplateConfig、临时 info/error 日志
（access:none）、钉住实际日志路径/inode/offset/核心 epoch、新鲜 nonce 的精确原生拒绝判定、**最终归档B之前
无条件精确恢复原模板**；不产生常开流量日志，服务器日志保持 root 私有。原单 VLESS 假设替换为精确双 VLESS 归属：
按保存的 REALITY tag 与 SELF-cdn 分别查询两个实际活跃 Xray 注册表，nonce 排除覆盖两注册表与已配置 UUID 的并集；
路径归属由原生精确拒绝判定加控制器强制的唯一 CDN 路由组合而成，不臆造日志格式中不存在的入站标签字段。
服务器端口空闲或注册表不变不等于公司直连可达；REALITY 直连实测按所测网络单独记录，
公司 TCP 失败不否决 CDN 验收，也不构成 REALITY 从该公司网络可用的声明。

生命周期：add 为三个入口各加精确 join；rename 保留全局身份；reset 一次轮换 UUID/Auth/SubID 并同时撤销三个节点；
remove 删除全局身份与全部 join。每次原生变更后重新断言 per-role flow。三入口冷恢复保留入站 ID/逻辑 Host
group ID/tag、选定 REALITY 端口、CDN 域名/path/Host TLS 与 H2 原证书绑定；replace 遵循既有轮换语义。
已知 createdAt 毫秒尾数归一披露不变，不做账户 SQL 或政策放宽。
设备事务等待离机备份/receipt 期间，在线 FRONT 仍保留旧精确订阅路径；未发布三入口候选归档在临时打包目录
按候选 manifest 生成不发布订阅的 Caddyfile，冷恢复时与其 published=false 精确一致。在线 FRONT、回滚快照及
已存在的原始归档不因此改写；已提交归档仍保留原配置字节，归档校验不接受任意 front 配置。
原生 AddClientStat 会把共享流量行的 inboundId 重指到新增入站，H2 API clientStats 因此反映该共享关联；
这不是 H2 传输归属或 join 成员关系变化，不据此断言配额/统计精度（下文统计限制不变）。

模块只核验原生 YAML/URI 字段，不写 converter；dashboard 仅按原生 transport/security 区分显示三个节点，
URI/QR 字节不变；每设备5个二维码（3个节点+2个订阅 URL），旧双协议/单 TCP 模式为4/3个。
探针所需 mihomo/PyYAML 仅属本命令，普通本地导出依赖不变。同一 CDN 域名已三入口后重复运行 cdn 拒绝重新验证
（刷新事务未实现），换域名/端口同样拒绝；status/backup/export 正常。

## 已知限制与流量统计

固定3x-ui 3.10.2（源码提交 `608ef4e4010bf83e2e78328e08f77efc051ad4d7`）的原生
[UpdateInbound](https://github.com/MHSanaei/3x-ui/blob/608ef4e4010bf83e2e78328e08f77efc051ad4d7/internal/web/service/inbound.go#L1972-L1974)
保留入站已存客户端投影；[SyncInbound / applyClientRecordMerge](https://github.com/MHSanaei/3x-ui/blob/608ef4e4010bf83e2e78328e08f77efc051ad4d7/internal/web/service/client_link.go#L81-L82)
选择更早的创建时间；[ClientUpdate 入站同步](https://github.com/MHSanaei/3x-ui/blob/608ef4e4010bf83e2e78328e08f77efc051ad4d7/internal/web/service/client_inbound_apply.go#L808-L822)
也会覆盖提交的 `created_at`。因此冷 restore/replace、reset/rename 可能把全局 `createdAt` 的毫秒尾数
归一到较早的整秒入站元数据；已验证样本整秒不变，变化小于1000ms。这不改变网络、认证身份或到期/配额等策略语义，
原始TAR中的关闭DB仍保留精确原始元数据。首次双协议上线迁移保留了基线记录；后续实际三入口迁移中，
3条记录的整数毫秒 `createdAt` 向更早的已有入站投影归一，各减少993ms，符合上述同秒且小于1000ms的边界；
认证身份、到期、配额及其他全部客户端字段不变。因此功能迁移及上述操作不能承诺 `createdAt` 位级一致。`test_native_dual_recovery.py` 分开核验功能及该已知原生精度限制，
保留精确相等的 `KNOWN_NATIVE_LIMIT` expected-failure；上游修正后应出现 unexpected-success 并审阅重锁版本，
不能把它报告为完整记录精度通过，也不能据此容许任意创建日期丢失。

历史部署的 `geoip:private` 阻断规则位于 `api → api` 例外之前，阻断内部127.0.0.1
控制 API。下述修复仅恢复认证验收所需的 HandlerService；未验证统计计数准确性，
流量展示需求仍已取消。实际连接以客户端 HTTPS/出口验收为准。
备份明文、容量手动管理、原机二进制升级未实现及冷换机停机等边界见上文。

### Native control API routing invariant

The exact native `inboundTag:[api] -> api` dispatch precedes the unrestricted
private/self-IP block; the narrow H2 self-management rule remains first. Only
that single API rule moves: business inbounds cannot forge its tag, and all
non-API routing order/private blocking stays unchanged. Fresh generation and
additive dual→triple preparation repair the owned template through native
settings; the latter runs only after the closed rollback journal. Prepared
triple validation reads both actual HandlerService registries on127.0.0.1:62789
before client/auth probes. Read-only status and exact rollback/old archives do
not repair historical ordering. This control-plane dependency fix makes no
statistics/counter accuracy claim.

## Persistent local manager UI

`vps/index.html` is a private generated file (0600, ignored) rendered by `lib/manage_entry.py::render_entry`:
manager JS/CSS are inlined and covered by exact SHA-256 CSP hashes (no `unsafe-inline`), `connect-src` is the
single loopback origin, and the only secret is the bootstrap key in an inert `application/json` block. A server
cannot supply script to it, so a local impostor on the fixed port gains nothing without the key.
The page performs hello (Origin is `null` for `file://`, set by the browser), verifies the server proof, derives the
session key, signs each request over method/path/body hash and verifies each response MAC before parsing.
GETs re-hello once on an unknown session; mutating POSTs are sent once and reconciled by reading.
Persistence is a property of the service, so the page has no session deadline. Carrier status is shown separately:
no configuration or a down listener leaves committed subscriptions visible and disables writes with the reason.

## 前台管理（manage.sh）

`manage.sh` 是前台程序：自动 `connect` 到 `config` 的 `VPS_SSH`（pending 时跳过），启动同一套签名 ManagementServer，打印 `file://` 管理页面地址；运行期间可用，Ctrl+C/SIGTERM/SIGHUP 关闭。SSH 路径是调用期 `runtime_carrier`（默认直连并视为独立；代理须 `--independent-carrier`，否则只读），不读写 `carrier.json`。关闭时先停 API，活动原生任务有 20 秒完成回执，之后终止进程组并记为 `interrupted_unknown`，不重放。无 systemd 单元、无自动打开浏览器。详见 PERSISTENT-MANAGEMENT.md。
