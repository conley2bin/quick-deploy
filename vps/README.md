# 单台 VPS 快速上手

新安装默认为公司电脑、家里电脑、手机创建独立账户；可继续添加、删除和轮换设备。
VPS 上运行 3x-ui / Xray，Caddy 提供 HTTPS 订阅；脚本不修改客户端或现有机场。

## 先准备好

- 专用 **Ubuntu 24.04 amd64、公网 IPv4** VPS，root 或免密 sudo。
- 管理电脑有 Python3、OpenSSH、`mihomo` 或 `verge-mihomo`；不要 sudo 整个脚本。
- 远端已有 Python3、OpenSSL、systemd/`systemd-analyze`、`ss`、`nft`、可信 CA 包，
  至少 2GiB 可用空间。缺工具会停止，不自动安装。
- 自己的订阅域名，A 记录指向 VPS，**DNS-only / 灰云**，不要开 CDN 代理。
- 从服务商控制台核验 SSH host key，再按正常 OpenSSH 流程登记；支持已核验别名或 `user@公网IPv4`。
- 审阅云防火墙：默认代理 TCP443、订阅 TCP8443、证书签发/续期 TCP80 和实际 SSH 端口可达。
  脚本不改防火墙；已有冲突服务、旧代理目录或未审阅规则会拒绝接管。
- 自行选好并验证支持 TLS1.3+h2 的 REALITY 目标域名。

## 1. 检查 VPS

以下命令都在**仓库根目录**运行。把示例地址和目标域名换成自己的；两步使用同一组变量：

```bash
VPS_SSH='root@203.0.113.10'
SUB_DOMAIN='sub.example.org'
REALITY_TARGET='target.example.org'
bash vps/setup.sh preflight --ssh "$VPS_SSH" \
  --domain "$SUB_DOMAIN" --reality-target "$REALITY_TARGET"
```

这一步检查，不保存部署配置、不创建远端目录。默认不向 CA 提供邮箱。

## 2. 安装并发布订阅

```bash
bash vps/setup.sh deploy --ssh "$VPS_SSH" \
  --domain "$SUB_DOMAIN" --reality-target "$REALITY_TARGET"
```

按提示确认；原生配置、可信证书和离机备份通过后，最后确认才公开订阅。
非默认 SSH 端口/密钥：在以上**两条命令**末尾都追加
`--port 2222 --identity "$HOME/.ssh/vps_ed25519"`（`--port` 只指 SSH）。
同状态重跑不轮换账户、不重启 Xray；真实客户端连接仍需自行验收。

## 3. 旧 VPS 坏了，换新机

管理电脑必须保留原私有状态和完整备份，旧机无需在线。新机需满足相同前提：

```bash
bash vps/setup.sh replace --ssh root@203.0.113.20
```

如用非默认端口/密钥，同样追加新机的 `--port` / `--identity`。
可加 `--backup /private/path/backup.tar` 指定备份。换机会更换代理身份、保留订阅地址；
随后把灰云 DNS 改指新机，各客户端**刷新订阅**。有停机，工具不访问或撤销旧机。
[证书已过期、受控恢复和泄露处理](DESIGN.md#备份冷替换与恢复边界)另有要求。

## 订阅文件在哪里

成功部署、换机、恢复及已提交事务的恢复核对后，自动生成：
**[`vps/subscription-urls.json`](subscription-urls.json)**。
文件0600，已被 Git 忽略；含凭据，勿分享或贴到工单。
已有安装或本地导出失败时，运行 `bash vps/setup.sh export`，只读本地已保存的提交快照，不联系 VPS。

| JSON 键 | 设备 | 使用字段 |
|---|---|---|
| `company` | 公司电脑 | `mihomo`：Verge 独立完整 profile 订阅 |
| `home` | 家里电脑 | `mihomo`：Verge 独立完整 profile 订阅 |
| `phone` | 手机 | `shadowrocket`：Shadowrocket URI 订阅 |
| `device4` | 执行下方 add 后新增的设备4 | 按客户端选择 `mihomo` / `shadowrocket` |
| `device5` | 执行下方 add 后新增的设备5 | 按客户端选择 `mihomo` / `shadowrocket` |

已禁用设备不会导出。Shadowrocket 请保留实测可用的 **Chrome140 / X25519MLKEM768**
TCP 指纹；URI 不带手机分流策略，国内/Bili 直连需单独核验。[手机设置与排障](DESIGN.md#导入管理和设备变更)。

## 设备与钥匙管理

以下是**独立操作示例，按需选择**；名字须以小写字母开头，仅含小写字母、数字、短横线，最长32字符：

```bash
bash vps/setup.sh devices list                  # 只列名称/启用状态，不打印钥匙
bash vps/setup.sh devices add device4 device5    # 一次新增多个独立账户；原3个不变
bash vps/setup.sh devices rotate device4         # 仅换该设备 UUID + 订阅钥匙，需重新导入
bash vps/setup.sh devices remove device5         # 撤销该设备代理认证和订阅地址
```

变更会重启自有核心/订阅前端，**所有设备可能短暂重连**。成功后自动更新离机备份及上方JSON；
失败先运行 `bash vps/setup.sh recover`；提示 queued 时等恢复服务退出后再跑，不要重复轮换。
自选核心会保存路径；失效时可用 `recover --mihomo /path/to/mihomo` 替换。旧备份含历史钥匙，恢复可能复活旧账户；
请用最新撤销后备份。[事务、重试和恢复边界](DESIGN.md#设备名册与钥匙事务)。

## 日常查看与备份

```bash
bash vps/setup.sh status
bash vps/setup.sh backup
```

权威状态与完整备份仍在 `~/.local/state/quick-deploy-private/vps`，没有搬入仓库。
备份是**明文 TAR**，需另存加密离机副本；不支持原机版本升级，当前流量统计不可信。
[完整运维说明、恢复约束和已知限制](DESIGN.md#运维参考)；[隔离测试](DESIGN.md#隔离测试与生产门槛)。
