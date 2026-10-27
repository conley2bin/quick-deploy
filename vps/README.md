# 单台 VPS：TCP + UDP 双协议

新安装默认创建公司电脑、家里电脑、手机三个独立原生账户，可继续添加、重命名、删除和重置。
同一账户同时使用 TCP/REALITY 和 UDP/Hysteria2；3x-ui/Xray 管理账户及完整订阅格式，Caddy 提供可信 HTTPS 订阅和证书。
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
原生字节不经过节点转换。电脑以本地文件导入完整 YAML，手机私下传送**自己的** URI 文件导入。
H2 是新订阅的默认首选，TCP/REALITY 仍可选；GUI 可能缓存旧选择，导入后要明确选中 `SELF-h2` 并核验实际连接。
手机保留已验证的 **Chrome140 / X25519MLKEM768** TCP 指纹；URI 本身不提供手机分流策略，国内/Bili直连需单独核验。
有 pending、备份/快照 hash 不匹配、文件篡改、禁用/删除设备或跨设备凭据时，离线导出拒绝，不会改用候选文件。
文件含凭据，不贴工单、不放 Git；旧导出和旧备份可能保留已经撤销的钥匙。

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
