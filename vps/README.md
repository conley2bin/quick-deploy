# 单台 VPS：TCP + UDP 双协议

新安装默认创建公司电脑、家里电脑、手机三个独立原生账户，可继续添加、重命名、删除和重置。
同一账户同时使用 TCP/REALITY 和 UDP/Hysteria2；3x-ui/Xray 管理账户及完整订阅格式，Caddy 提供可信 HTTPS 订阅和证书。
已部署的双协议节点可随后把 TCP 入口受控切换为 Cloudflare CDN + VLESS/XHTTP（见下文 cdn 一节），H2 不受影响。
脚本不修改客户端、机场、SSH、防火墙或 Tailscale。
固定原生API在冷恢复/换机、重置/改名时可能归一创建时间的毫秒尾数；认证及策略保留语义不变，原始备份保留精确元数据，详见 [DESIGN 已知限制](DESIGN.md#已知限制与流量统计)。

## 先准备好

- 专用 **Ubuntu24.04 amd64、公网 IPv4** VPS，root 或免密 sudo。
- 管理电脑有 Python3、OpenSSH、`mihomo` 或 `verge-mihomo`；不要 sudo 整个脚本。
  可选的管理配置导出需要本地 **PyYAML**；普通部署、URL导出和原生离线文件导出不需要它。缺依赖会明确停止，不自动安装。
- 远端已有 Python3、OpenSSL、systemd/`systemd-analyze`、`ss`、`nft`、可信 CA 包，至少2GiB可用空间。
- 订阅域名 A 记录指向 VPS，**DNS-only / 灰云**，不要开 CDN 代理。
- 从服务商控制台核验 SSH host key，再按正常 OpenSSH 流程登记；支持已核验别名或 `user@公网IPv4`。
- 操作员审阅云防火墙：同一代理端口默认 **TCP443 + UDP443**，订阅 TCP8443、HTTP01 TCP80 和实际 SSH 端口可达。
  两种传输可共用数字443；脚本不添加/清空防火墙规则，不修改 sshd/authorized_keys。
- 自行验证支持 TLS1.3+h2 的 REALITY 目标域名；Hysteria2 使用订阅域名的正常受信任 TLS 证书，不跳过校验。

## 1. 新 VPS 检查和部署

以下命令在**仓库根目录**运行，把地址、域名换成自己的：

```bash
VPS_SSH='root@203.0.113.10'
SUB_DOMAIN='sub.example.org'
REALITY_TARGET='target.example.org'
bash vps/setup.sh preflight --ssh "$VPS_SSH" \
  --domain "$SUB_DOMAIN" --reality-target "$REALITY_TARGET"
```

预检查不保存部署配置、不创建远端部署目录；默认不向 CA 提供邮箱。

```bash
bash vps/setup.sh deploy --ssh "$VPS_SSH" \
  --domain "$SUB_DOMAIN" --reality-target "$REALITY_TARGET"
```

命令直接执行，无二次 YES 确认。首次流程先建立 TCP 候选及 Caddy 可信证书，再启用原生 Hysteria2；
双协议订阅、原生 Mihomo 格式和完整离机备份验证通过后才发布，随后保存已发布备份及离线原生配置快照。
非默认 SSH 端口/密钥，在上述两条命令都追加
`--port 2222 --identity "$HOME/.ssh/vps_ed25519"`（`--port` 只指 SSH，`--proxy-port` 才是首次保存的代理端口）。

固定版本不自动换镜像或版本。若官方固定资源返回404，部署会明确提示；原有有效缓存继续可用。
新状态可显式追加 `--artifacts-dir /path/to/verified-releases`：该目录必须包含 release-lock.json 中的三个原始文件，
工具先验证**全部文件名及 SHA256**，再复制进私有0600缓存。来源可为普通0644公开文件，不接受组件符号链接，
不会读取/导入其他文件，也不需要把二进制放进 Git。冷恢复备份本身已含固定资源。

## 2. 旧 TCP 节点受控升级

保留原私有状态，通过一个**独立、已工作的管理中转**运行相同的 `deploy`。
工具先只读核验旧节点及已审阅 adapter，再做完整升级前备份；独立 transport-upgrade 事务增加原生 H2，
保留原 REALITY 密钥、UUID、SubID、URL、账户策略与历史。不会用“添加设备”冒充升级。
已有双协议节点重复 deploy 不再生成 Auth 或重启核心；仍核对原生投影并同步 Caddy公开路径（例如面板中更改启用状态后），再刷新离机备份和提交绑定的原生快照。
备份过程可能短暂停止订阅前端。

SSH 默认直连。任一远程命令末尾可显式追加
`--ssh-proxy socks5://127.0.0.1:10808` 或 `--ssh-proxy http://127.0.0.1:10808`（本地 HTTP CONNECT）；
支持 deploy/preflight/replace/restore、status/backup/recover 及所有 devices 子命令，export 仅本地、不接受此参数。
使用随仓库附带的 Python 标准库助手，无需 nc；仅接受字面量127.0.0.1和端口1..65535，拒绝密码、其他 URL 部分和任意命令。
参数仅本次有效，不保存中转端口、不采用环境变量/GUI/SSH配置中的代理，失败不退回直连；原 VPS 地址、SSH端口及严格主机密钥校验不变。
已有 current 可省略 --ssh；恢复只使用原 pending 目标与操作ID，换中转不改变事务身份。

## 受控切换 TCP 入口到 Cloudflare（cdn）

把现有双协议节点的 TCP 入口从 REALITY 切换为 **Cloudflare CDN + VLESS/XHTTP/TLS**。
Hysteria2（UDP443）完全保留：Auth、UUID、SubID、端口、证书路径和启用/到期/流量策略不变，仍是订阅默认首选；
没有额外 daemon、转换器或新版本 pin。原四个配置字段（订阅域名、REALITY 目标、ACME 邮箱、代理端口）不变，
CDN 域名是本命令独立的新参数，不改动订阅域名。切换被真实验收后旧 REALITY 入口才退役——
同一个443 socket 不能同时属于两个服务；验收前旧 REALITY 始终可回滚恢复。

前提：节点已是 REALITY+H2 双协议、代理端口443且无未完成事务；一个独立 CDN 主机名（示例 `cdn.example.org`），
A 记录指向 VPS 公网 IP，先保持 **DNS-only/灰云**；知道该域名所在 zone 的**权威 DNS 服务器 IP**
（从 CF zone 分配的 NS 记录解析得到，不要用 1.1.1.1 之类的公共递归代替）；本机有 mihomo 和 PyYAML（仅本命令的探针需要，
普通部署/URL导出/离线文件导出依赖不变）。现有 sub 记录保持操作员自己选择的橙/灰状态，本流程不要求翻转它。

**全程通过独立管理中转**（机场或服务商控制台 SSH）启动和跟进：切换会重启 Xray 核心，作为载体的 H2 会中断，
不能把本机现有 H2 管理连接当作唯一操作通道。

```bash
bash vps/setup.sh cdn --domain cdn.example.org --interface eno1 \
  --dns-server 203.0.113.2 --mihomo /path/to/mihomo
```

`--interface` 是**管理电脑本机的物理网卡**（不是服务器接口），保证探针走真实物理链路，
不被桌面 TUN/环境代理冒充“CDN 直连”。`--dns-server` 传上面核验过的权威 NS IP：
灰云期间的查询可在公共递归缓存中存活最长约300秒，橙云切换后旧缓存可能把源站地址伪装成 CDN 边缘，
显式权威查询避免这种假失败/假成功。仍可追加 `--ssh-proxy socks5://127.0.0.1:10808`（本地回环中转）。

操作顺序（有界事务，不无限等待）：

1. 在 CF 控制台把 cdn A 记录设为灰云并确认其权威解析到 VPS IP；不盲改无关 zone 设置。
   工具在灰云状态下为该域名取得正常公开可信证书（可预测的 HTTP01 bootstrap）。
2. 核验 CF 边缘证书处于 active、zone 的 SSL/TLS 模式与源站证书匹配。源站是正常 CA 证书，对应 Full(strict) 级源站校验；
   是否调整全局模式由操作员审慎决定，工具不做 zone 级变更，客户端 TLS 正常本身不等于 CF 全局模式已审计。
3. 通过独立中转运行命令。候选就绪后工具打印**绝对截止时间**（租约900秒）并明确提示：候选未提交，
   现在只把 cdn 记录转为橙云，然后在期限内确认。订阅能力在提交前不发布。
4. 工具随后做真实探针：经实际 CF 边缘的正常 TLS（h1/h2）、原生 XHTTP 内层 HTTPS 与 VPS 出口、
   错误 UUID 被拒绝后正确凭据恢复、短空闲/新连接重连、原 H2 全新认证。边缘 TCP 延迟或根路径404都不是验收。
5. 全部通过才提交：只新增提交证明和一份新的完整离机归档。超时或失败时 VPS 侧恢复原 DB/前端/证书存储/端口归属，
   控制器按 queued 路径恢复；**你在 CF 的手动改动不自动回滚**——把 cdn 记录转回灰云（或保留不用），sub 及其他 zone 配置不动。
6. 验收后 H2 仍是默认首选、GUI 选择不变；需要时在客户端显式选择 `SELF-cdn`，或重新导出/导入原生 profile。
   桌面复制 URL/YAML 与手机扫二维码都可用；URI/QR 字节不重新编码，dashboard 只把该 VLESS 正确显示为 XHTTP/CDN。

备份/恢复/换机/设备/status 按精确模式保存与恢复：旧 REALITY 归档仍是旧模式，恢复旧归档不会被隐式升级；
已退役的 REALITY 保留在历史归档中供回滚。本流程不承诺免封、固定延迟或持续吞吐，也不是真实 ACME 续期验收；
CDN 路径的实际表现以探针和你的真实验收为准。

## 3. 换新 VPS / 恢复

管理电脑必须保留原私有状态和完整备份；旧机无需在线，新机满足相同前提：

```bash
bash vps/setup.sh replace --ssh root@203.0.113.20
```

非默认端口/密钥追加新机的 `--port` / `--identity`，可加 `--backup /private/path/backup.tar`。
replace 面向**新虚拟机**，会轮换代理认证身份，保留订阅地址；操作员更新灰云 DNS 后，各客户端刷新或重新离线导入。
有停机，工具不访问或撤销旧机。受控 `restore --backup ... --ssh ...` 保留归档身份，具体约束见 [DESIGN](DESIGN.md#备份冷替换与恢复边界)。
同一旧节点的功能升级用 deploy；同一 guest 直接换公网IP不属于受支持的 replace 路径，不能借此绕过机器/端点身份校验。

## 保留当前节点，另建一台

另建独立节点使用新的状态目录和新域名，不用 replace；`--state-dir` 放在子命令之前：

```bash
bash vps/setup.sh --state-dir "$HOME/.local/state/quick-deploy-private/vps-second" deploy \
  --ssh root@203.0.113.30 --domain second-sub.example.org --reality-target target.example.org \
  --artifacts-dir /path/to/verified-releases
```

每个状态目录各自保存权威账户、备份和提交快照。仓库固定的 `vps/subscription-urls.json` 只是**最近一次成功导出**的副本，
不是多节点聚合；对具体节点使用对应 `--state-dir` 导出。只有替换原节点时才用 replace。

## 离线首次导入：不依赖已经阻断的旧订阅

已提交原生文件按对应完整备份的 SHA256 保存为不可变私有 generation；只有成功提交/核对后才推进状态指针。
候选 `outputs/` 不是导出权威。旧版只有 URL 的状态仍可导出 URL；要离线配置，先通过工作中的管理路径运行 deploy 或 backup 捕获快照。

为每个设备单独导出到 **Git之外** 的私有目录（例子沿用默认状态目录，目录0700、文件0600）：

```bash
VPS_STATE="$HOME/.local/state/quick-deploy-private/vps"
bash vps/setup.sh export --device company --bootstrap-dir "$VPS_STATE/bootstrap/company"
bash vps/setup.sh export --device phone --bootstrap-dir "$VPS_STATE/bootstrap/phone"
```

得到该设备的原生 `.yaml` 完整 profile、原生 base64 `.txt` 订阅、解码后的 `.uris.txt` 和私有 `.urls.json`；
原生字节不经过节点转换。另有该设备的离线 `index.html`、每节点及两个订阅 URL 的 SVG 二维码；直接用浏览器打开 `index.html`。
电脑以本地文件导入完整 YAML，手机私下传送**自己的** URI 文件导入。
H2 是新订阅的默认首选，TCP/REALITY 仍可选；GUI 可能缓存旧选择，导入后要明确选中 `SELF-h2` 并核验实际连接。
手机保留已验证的 **Chrome140 / X25519MLKEM768** TCP 指纹；URI 本身不提供手机分流策略，国内/Bili直连需单独核验。
有 pending、备份/快照 hash 不匹配、文件篡改、禁用/删除设备或跨设备凭据时，离线导出拒绝，不会改用候选文件。
文件含凭据，不贴工单、不放 Git；旧导出和旧备份可能保留已经撤销的钥匙。

## 本地连接资料网页与二维码

```bash
bash vps/setup.sh dashboard
# 可选：只导出一个启用设备，或指定 Git 之外的私有目录
bash vps/setup.sh dashboard --device phone --output-dir "$HOME/.local/state/quick-deploy-private/vps/dashboard-phone"
```

默认打开 `~/.local/state/quick-deploy-private/vps/dashboard/index.html`；自选 `--state-dir` 时位于该状态的 `dashboard/`。
这是**已提交 generation 的离线连接资料**：按设备搜索、复制、查看/下载二维码及原生配置，不启动 HTTP 服务，不联网，不增删服务器账户。
每个启用设备包含原生订阅中的全部节点 URI，以及 Mihomo / Shadowrocket 两个订阅 URL；订阅二维码导入的是 URL，不是单节点。
页面中的“启用”只表示备份内账户状态，**不代表在线或已测连通**。当前优先使用 H2，保留 TCP 选项不意味着当前 TCP 路径可用。

二维码使用仓库内固定的 Nayuki `qrcodegen 1.8.0` 纯 Python 编码器，直接编码原生 URI 的 UTF-8 字节，不转码密码/URL，不调用在线制码。
其版本、官方来源及 SHA256 见 `lib/vendor/manifest.json`；这是本地工具依赖，服务器的三个 release-lock pin 不变。过长链接明确失败，不截断。

整个输出目录包含凭据，目录0700、文件0600，必须位于 Git 之外；不要上传、公开托管或共享完整目录。
仅更新/清理 `.qd-dashboard.json` 精确记录的本模块文件，用户旁置文件保留。
网页不是实时面板：reset/remove/禁用后，先通过正常模块流程同步备份及原生 generation，再重新运行 dashboard 或对应 bootstrap export。
浏览器刷新不读取服务器；旧二维码、已复制链接、另存网页和历史备份不会被自动擦除。以最新撤销后备份为准。

## 独立 H2 管理监听

GUI/TUN 的规则可能把 VPS 地址、SSH22强制 DIRECT，普通 GUI7897监听不代表 SSH 已走 H2。
选择实际**物理接口**，显式渲染独立、无TUN的管理配置；接口名不自动猜测：

```bash
bash vps/setup.sh export --device company --bootstrap-dir "$VPS_STATE/bootstrap/management" \
  --management-interface eno1 --management-port 10808
```

此操作需要 PyYAML 和本地 Mihomo格式检查；可用 `export --mihomo /path/to/mihomo` 显式选择本地核心。
原生 `<设备>.yaml` 与 `<设备>.management.yaml` 分开保存，从已提交原生 YAML 完整复制 H2 映射，只替换运行时外壳。
已有管理运行时的目录若不带管理参数重导出，会明确拒绝，避免留下旧 Auth；无来源绑定的旧 `management.yaml` 需人工清理，不猜测删除。
管理进程仅监听127.0.0.1，仅允许该 VPS 公网IP的实际 SSH 端口及8443经 H2，其余拒绝。
外层 QUIC 使用数值公网IP和指定物理接口，TLS SNI 仍为证书域名；不依赖代理 DNS 自举，不开控制器/LAN/TUN。
按命令输出的 core 启动命令运行独立进程，例如：

```sh
mihomo -d "$VPS_STATE/bootstrap/management" -f "$VPS_STATE/bootstrap/management/company.management.yaml"
bash vps/setup.sh status --ssh-proxy socks5://127.0.0.1:10808
```

模块 SSH 通过随附 `ssh_proxy.py` 转发字节，校验的仍是原 VPS 的主机密钥。也可在普通交互 SSH 中使用同一助手：

```sh
ssh -o StrictHostKeyChecking=yes -o ControlMaster=no -o ControlPath=none -o ProxyJump=none \
  -o 'ProxyCommand=python3 vps/lib/ssh_proxy.py socks5://127.0.0.1:10808 203.0.113.10 22' root@203.0.113.10
```

示例端点/SSH端口须换成已核验值；路径有空格或百分号时优先使用模块生成的传输，不手拼 ProxyCommand。
首次部署不能依赖尚未安装的 H2。deploy、设备变更和某些恢复会重启核心；若中转本身就是这个核心/账户，SSH会中断，
删除/重置该账户还会阻止重连。**这些操作全程使用独立机场管理中转**，不要用自身 H2 完成破坏其载体的操作。
中断后不要重复 reset/rename，改用独立中转 `recover --ssh-proxy http://127.0.0.1:10809`；queued 时稍后再核对。
保留服务商控制台作为核心/UDP/中转均不可用时的入口，不靠换 sshd 端口解决链路问题。

后续刷新需明确经过独立管理监听：把私有 `.urls.json` 中自己的 URL 放入0600的 curl配置文件，而不是命令行；
配置 `proxy="socks5h://127.0.0.1:10808"`、`noproxy=""` 和
`connect-to="订阅域名:8443:公网IPv4:8443"`，保留 URL/Host/SNI 的正常 CA 校验，设连接/总时限并失败即停。
用 `curl --config 私有配置文件` 写入私有新文件、验证后再导入；不要假定 GUI 的自动刷新已采用此路径。

双协议原生 Mihomo 配置另带窄范围 `FEED` fallback：仅订阅域名/节点IP的 **TCP8443** 在 `[SELF-h2,DIRECT]` 中按健康检查选择，
不接管外层 UDP443、SSH或普通流量；域名根路径的可信 HTTPS404健康检查不等于实际私有订阅获取成功。
Verge 更新会依次尝试配置的方式、显式本地 mixed 代理、系统代理；第一步不保证经过已加载核心。
**离线导入是 local profile，不会自行变成自动更新的 remote 订阅。** 先用离线完整配置启动 H2，再通过应用的
Clash/self-proxy 导入原 `.urls.json` 中自己的远程URL，确认保存为 remote 且已激活，核验实际 FEED→H2及成功订阅内容。
其他全局规则、未加载的核心或绕过代理的客户端仍可能使 FEED无效；失效时使用上面的专用监听/curl确定性路径。
手机 URI没有 Mihomo的 FEED组；手机是否通过自身代理刷新，需按实际版本/设置验收，仍保留私下重发自己 URI文件的办法。
reset 有意撤销旧 SubID，旧 URL无法经 FEED 或 DIRECT发现新秘密URL，必须重新导入；rename保留URL，replace保留SubID但仍依赖新端点/DNS可达。

## 设备与钥匙

以下是独立操作示例，按需选择，不要整块执行。名字以小写字母开头，仅含小写字母/数字/短横线，最长32字符：

```bash
bash vps/setup.sh devices list
bash vps/setup.sh devices add device4 device5
bash vps/setup.sh devices rename device4 tablet
bash vps/setup.sh devices reset tablet
bash vps/setup.sh devices remove device5
```

rename 保留 UUID、H2 Auth、SubID和URL；reset 同时轮换 UUID、H2 Auth、SubID，需要重新导入。
remove 撤销两种认证及两个公开订阅路径；核心重启关闭已建立连接，所有设备都可能短暂重连。
未知设备/同名/新名占用或历史冲突拒绝。成功后刷新离机备份、原生 generation 和 URL 副本。
自选核心路径会保存在 pending；失效可 `recover --mihomo /path/to/mihomo` 替换，不重放原操作。

## 日常查看、备份与 URL 副本

```bash
bash vps/setup.sh status
bash vps/setup.sh backup
bash vps/setup.sh recover
bash vps/setup.sh export
```

权威状态和完整备份位于 `~/.local/state/quick-deploy-private/vps`，不搬入仓库。
成功提交/核对后自动更新忽略的0600 **[`vps/subscription-urls.json`](subscription-urls.json)**；普通 export 只读已提交本地备份、不联系 VPS。
备份为**明文 TAR**，另存加密离机副本；用最新撤销后备份，恢复旧档可能复活旧账户。
`backup --mihomo /path/to/mihomo` 会保留所选路径供 recover 覆盖。完整归档不依赖 live feed 或本地核心：
若离线配置捕获因 HWID/到期/缺核心等失败，仍保存已验证的当前节点备份、清除备份 pending，并非零返回
“Backup committed; offline snapshot not captured”。最新便捷离线文件及 generation 指针失效，不能把旧文件当作新配置；历史备份/generation保留。
设备/传输升级的提交前配置校验仍是强制门槛；普通 URL export 不需要本地核心。
Caddy 继续负责 HTTP01签发/续期；原生 Xray 在稳定受控证书路径上按小时重读，非即时续期钩子。
需要保留证书重叠时间并监控续期；这不是已经完成真实 ACME 续期/重启验收的声明。

UDP同样可能被按协议、端口、IP或特征阻断，也可能被 ISP 对持续流量限速；H2不是永久免封承诺。
保留 TCP选项、独立管理载体及控制台。当前原生统计/API路由的已知问题不在本功能中修复，不能用统计值证明配额执行。
[完整架构、恢复与验收边界](DESIGN.md)；真实公司/家里/手机连接、持续UDP及未来续期仍需分别验收。
