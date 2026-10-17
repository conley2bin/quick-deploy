# 单 VPS：3x-ui / Xray 与可信 HTTPS 订阅

`setup.sh` 现在只管理一台专用 **Ubuntu24.04 amd64** VPS。3x-ui 原生 SQLite/API
管理 company、home、phone 三个独立账户并生成完整 Mihomo / base64 URI 订阅；
Caddy 只公开指定订阅路径。没有 Tailscale、DERP、公司8743、sshd 加固或客户端配置修改。
架构与信任边界见 [DESIGN.md](DESIGN.md)。**源码/隔离测试完成不代表生产或手机验收完成。**

## 前提与端口

管理 PC：Python3、OpenSSH、原生 `mihomo` 或 `verge-mihomo`。远端需已有 Python3、
OpenSSL、systemd/`systemd-analyze`、`ss`、`nft`、可信 CA 包及至少2GiB可用空间；工具缺失会明确停止，
脚本不自动安装系统依赖。仅支持 SSH 最终 HostName 为公网 IPv4 的已核验别名或
`user@IPv4`，以及 root / 免密 sudo；不绕过 known_hosts，不支持 NAT 地址映射。
首次先从服务商控制台核验 SSH host key，再按正常 OpenSSH 流程登记。

| 入口 | 归属 |
|---|---|
| TCP443（默认） | Xray VLESS/TCP/REALITY/Vision |
| TCP8443 | Caddy 可信 HTTPS；仅最多六条精确设备订阅路径 |
| TCP80 | Caddy HTTP01 证书签发/续期；普通 HTTP 请求全部404 |
| 127.0.0.1:2053 / :2096 / :62789 | 面板 / 原生订阅后端 / Xray API；不公开 |
| 实际 SSH 端口 | 原样保留，`--port` **只代表 SSH** |

唯一代理端口设置是首次 `--proxy-port`，默认443，保存在私有 config 中；不与上述
保留入口或实际 SSH 端口冲突。入站、原生导出、进程/端口检查和备份共用此设置。
以后不会隐式改监听端口；换端口需单独规划迁移。没有证据表明3280更不易被封，
工具不会自动选它。REALITY 目标站自身的 TLS 端口仍为443，独立于代理监听端口。

域名由用户控制并设置 **DNS-only/灰云** A 记录；不需要 Cloudflare API key。
HTTP01需要公网80可达，Caddy禁用 TLS-ALPN 挑战和自动重定向，明确不占用443。
已有防火墙规则、占用端口、外来同名 unit 或旧代理目录会拒绝，**不清空/开放规则、
不停止外来服务、不接管旧机器**。四个unit在实际systemd加载路径中的同名定义、
精确/前缀/类型drop-in以及有效FragmentPath/DropInPaths/SourcePath/运行时配置均检查；
执行服务操作前再次核验，发现外来或未重载配置就拒绝，不删除override来绕过。
云防火墙由操作者审阅并开放所选入口。

## 实际命令

在完整仓库的管理 PC 运行，不要 sudo 整个入口，不要在未批准时联系 VPS：

```bash
# 首次也可省略三项配置，终端会逐项询问；保存一次，重跑不重生身份。
bash vps/setup.sh preflight --ssh root@<公网IPv4> \
  --domain sub.example.org --reality-target <经验证的目标域名> \
  --acme-email operator@example.org
bash vps/setup.sh deploy --ssh root@<公网IPv4> \
  --domain sub.example.org --reality-target <同一目标域名> \
  --acme-email operator@example.org
# 非默认 SSH 端口/身份：追加 --port 2222 --identity "$HOME/.ssh/<实际密钥>"
# 非默认代理监听：仅首次追加 --proxy-port <已审阅端口>；默认443。

bash vps/setup.sh status
bash vps/setup.sh backup
bash vps/setup.sh deploy --ssh root@<同一公网IPv4>  # 同状态不轮换、不重启Xray
bash vps/setup.sh recover                       # 处理本地未完成事务

# 旧机完全不可达也可用；读取管理PC已保存的完整备份，不联系旧机。
bash vps/setup.sh replace --ssh root@<新公网IPv4>
# 可明确选 --backup /private/path/backup.tar；不依赖旧机在线备份。

# 受控恢复：显式例外，保留原代理身份；也可从新管理PC恢复。
bash vps/setup.sh restore --ssh root@<新公网IPv4> \
  --backup /private/path/backup.tar
```

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
三设备 Mihomo解析和URI隔离、实际进程/监听、可信证书及完整离机备份通过后，
再次明确确认才公开订阅。任何证书错误均不能降级为公网 HTTP 订阅。
这一步的程序门槛不替代真实客户端连接/刷新验收。

## 导入、管理和设备变更

- Verge：从私有 `subscription-urls.json` 选自己的 **Mihomo完整profile URL**，
  作为独立订阅导入；不覆盖机场、Script、TUN或系统代理。使用上游支持的内联规则：
  节点IP/订阅域名/私网与中国/Bili直连，其余 `MATCH,PROXY`，保留手选 PROXY 组。
- Shadowrocket：导入自己的 base64 URI订阅 URL。URI只更新节点，**不包含手机分流策略**；
  手机国内/Bili直连及代理选择必须由用户在实际规则模式核验。必要时先停VPN再直连刷新。
- 面板通过 `ssh -N -L 2053:127.0.0.1:2053 <已核验目标>` 管理，按需加自己的 `-i/-p`。
  私有备份中 `deployment/manifest.json` 保存 admin_path/admin_user/admin_password；
  在私人本地查看器读取，勿贴聊天/工单。浏览器只访问该 SSH 隧道的本地管理路径。
- 原生 **Clients** 操作可单独禁用设备、轮换 UUID/SubID；之后重跑同目标 `deploy`
  校验并同步精确公开路径、生成新离机备份。固定设备成员之外的更改需手动审阅。
  v3.10.2 的全量 inbound 更新会保护全局 client 记录，不能把旧表单当作凭据轮换接口。

重跑同一状态不改变 UUID、SubID、REALITY密钥或Xray进程。为一致地复制证书/密钥，
每次完整备份会短暂停止并恢复**自己的订阅前端**；不停止Xray或SSH。

## 备份、冷替换与恢复边界

每次部署公开前及 `backup` 都把恢复集取回管理PC：一致、关闭且独立于 WAL/SHM 的
SQLite数据库，SQLite/schema/version信息，原生账户映射，外部证书与Caddy续期存储，
完整release包、校验信息、环境/unit配置、适配器。账户元数据从**同一关闭的数据库快照**
读取，恢复前核对一致性；不把较早的API投影当作账户写回来源。受控restore不更新client，
replace从完整、新读取的原生Client/账户对象仅更换UUID，并保留禁用、配额、到期、IP/HWID
限制和reset策略。备份不要求受限/过期账户能成功下载feed；但部署发布的feed门槛仍独立检查。
导出前拒绝来源树中的文件/目录symlink，复制时也保留链接再拒绝，不跟随到树外秘密。
损坏DB、账户元数据不一致、缺证书/私钥或版本不匹配会拒绝。

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
**原机版本升级尚未实现**：不同release lock拒绝，不能点面板一键升级或改成latest绕过。
磁盘上的历史备份/失败目录不会自动清理，需操作者审阅容量。

旧 `identity.json` / `inventory.json` / `generated/` 和远端 sing-box/旧role目录明确拒绝，
不会删除或导入为新账户。旧 `init/render/check/rollback`、`--role/--host/--device/--without-tailscale`
接口已删除；历史实现保留在Git，不复制归档。其他模块的可选旧JSON读取器未改，新的profile
不会写入它们的旧输入路径。

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
真实systemd启动/重启恢复、云防火墙、HTTP01签发续期、961MiB机器峰值资源、REALITY目标、
错认证握手、实际PC/手机刷新及国内/Bili路径仍须在独立生产授权后验收。
