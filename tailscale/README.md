# Tailscale（Ubuntu 24.04+）

这个目录安装或更新 Tailscale stable，并把 `tailscaled.service` 验证到 **enabled + active**。它只处理设备加入 Tailnet；不创建 Subnet Router、Exit Node、Serve 或 Funnel，也不写入 auth key、登录 URL、token 或密码。

`Tailscale` 需要身份授权，因此它保持为顶层独立功能，**不会**加入 `fresh-install/setup.sh`。

## 快速用法

普通网络：

```bash
cd tailscale
./install.sh
```

重复运行同一个入口会重新下载官方 stable APT 仓库元数据，并让 APT 仅检查/安装 `tailscale` 目标包；它不会执行广泛的 `apt upgrade` 或 `autoremove`。

完成包和服务检查后，脚本会**先**检测/处理 Clash Verge TUN 代理，随后才判断首次登录。已登录节点不会重新认证；未登录时交互终端会询问是否前台执行一次：

```bash
sudo tailscale up
```

默认回答是 `Y`。已登录节点只报告当前 tailnet 和本机 Tailscale IP，绝不触发 reauth。CI、管道或其他非交互 stdin 不会卡住，而会提示你随后在终端执行该命令。

## 当前两台设备互 ping

两台设备都加入同一个 Tailnet 后，在 **两端分别** 使用对端的 MagicDNS 名称或 `100.x` 地址：

```bash
# 设备 A 上：两种探测都指向设备 B
tailscale ping <设备-B 的 MagicDNS 名称或 100.x 地址>
ping <设备-B 的 MagicDNS 名称或 100.x 地址>

# 设备 B 上：两种探测都指向设备 A
tailscale ping <设备-A 的 MagicDNS 名称或 100.x 地址>
ping <设备-A 的 MagicDNS 名称或 100.x 地址>
```

`tailscale ping` 验证 Tailnet 隧道路径。普通 `ping` 还依赖目标系统放行 ICMP 入站防火墙；它失败不单独证明隧道不可用。服务运行、本机有 Tailscale IP，或单向成功，都不能证明两台设备已经双向互通。

## Clash Verge TUN / Fake-IP 兼容

Clash Verge 的 TUN + Fake-IP 在某些主机会让 `tailscaled` 无法直接连接控制面。`install.sh` 会在包与服务就绪后、**首次登录之前**执行内置自动检测：它只在以下条件同时满足时提出配置建议：

1. Clash Verge 最终配置 `~/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml` 的 `tun.enable` 为真；
2. Clash/Mihomo 进程和 TUN 路由表正在活动；
3. 最终配置中有有效的动态 `mixed-port`。

检测到后，脚本默认询问是否为 `tailscaled` 写入 HTTP(S) proxy。端口从最终配置读取，不会把 `7897` 当作通用默认值。**应用前会通过候选 proxy 以 HTTP CONNECT 实际探测 `https://controlplane.tailscale.com/key?v=138`**；端口能监听但控制面不可达时不会写 systemd 配置。普通网络、Clash 未安装/未运行、TUN 未启用或 `mixed-port` 无效时，Tailscale 安装照常完成，且不会写 proxy。

反向同样自动收敛：未检测到活动的 TUN、但存在本脚本写入的受管 drop-in 时，脚本会先探测该代理是否仍能连通控制面——能连通则保留（可能只是 Clash 暂时关了 TUN），**已失效则自动移除受管 drop-in 并重启 tailscaled**。这避免了 Clash 关闭或卸载后，残留的 `127.0.0.1` 代理把 tailscaled 的控制面连接拖断、节点掉线。撤销只删带 marker 的文件，外部 drop-in 不受影响。

受管文件是：

```text
/etc/systemd/system/tailscaled.service.d/quick-deploy-clash-proxy.conf
```

应用和撤销都是**收敛操作**：都会 `daemon-reload`、重启 `tailscaled`，并验证服务仍为 active 及实际的 systemd `Environment`。即使受管文件已经不存在，移除路径仍会 reload/restart，以恢复一次中断操作留下的 manager/process 不一致状态。外部 proxy（非本脚本写入的 drop-in）永远不被接管或删除：与检测值一致时直接保留；不一致时先探测它能否抵达控制面，成功则保留，失败则阻止首次登录而不是静默继续。

手工查看 tailscaled 当前有效代理：

```bash
systemctl show tailscaled.service -p Environment --value
```

## 仅为自建 DERP 绕过 daemon 的显式 HTTP proxy

Clash 的 `DIRECT` 只选择出口，仍经过 Clash 应用转发。两端现有规则已让
`derp.dex-gem.ai:1443` 命中 `DIRECT`；若 tailscaled socket 仍连接
`127.0.0.1:7897`，重复加规则不会移除这一跳。为这个目标不要重跑 `install.sh`
（它还会执行 APT/完整代理收敛），使用下面的独立入口。

从仓库根目录在**公司机器先执行**，确认恢复访问后再到本机执行同一条命令：

```bash
sudo /usr/bin/python3 tailscale/derp-bypass.py apply
```

这需要用户在该机终端自行批准 sudo。代理/SSH 密码不会交给脚本，脚本也不尝试提权。
没有权限时停止，不能用另一个 DIRECT 规则假装已经绕过。

入口只管理一个文件：

```text
/etc/systemd/system/tailscaled.service.d/zz-quick-deploy-derp-bypass.conf
```

它只设置 `NO_PROXY` 与 `no_proxy`，保留原有两者条目的并集及 localhost 例外，再加：

```text
derp.dex-gem.ai:1443,39.184.215.16:1443
```

这是 **tailscaled 进程内、端口限定的 DERP 域子树**：Go 的域名条目还会匹配
`child.derp.dex-gem.ai:1443`，并非只匹配一个精确主机。它不新增整个 `dex-gem.ai`
的例外，不影响同域 443、其他 DexGem 主机、控制/日志或其他应用的代理；原有更宽的
NO_PROXY 条目仍保留。IP 条目只匹配 IP 形式请求，不替代域名条目。大小写同时设置为
相同并集，避免不同 Go 版本的优先级差异。

公司原有非受管 `proxy.conf` 不改动；本机旧的 `quick-deploy-clash-proxy.conf`
也保留。新文件按文件名排在它们之后。若已有其他 EnvironmentFile 定义 NO_PROXY，
入口会明确拒绝，因为它的值优先于 `Environment=`，不能仅凭文件生成成功声称生效。
`install.sh` 的受管模板和匹配复用同一策略，保留原有上下写例外并写入两个变量；
它不接管或删除外部代理 drop-in。

### SSH 中断时的独立恢复

`apply` 在任何服务修改前，将原独立文件、权限、候选、原服务来源 hash、节点身份及
冻结入口保存到仓库 **git-ignored** 的 `.memory/local/tailscale-derp-bypass/<任务>/`。
文件有凭据邻近信息，不提交或分享整目录。原代理 URL 只保存 hash，不输出凭据。

随后在执行机上用 **root 所属的 `tmux -L pi-agent`** 创建两个独立任务：先确认回滚
计时任务已运行，再启动应用任务。不依赖 SSH 会话或之后的仓库文件。应用只执行一次
`daemon-reload` 和 **仅 tailscaled 的 restart**，会短暂中断 tailnet/SSH；不重启
Clash/主机、不关闭其他应用连接、不改规则、TUN 开关、DNS、MTU 或 NAT。

入口打印任务路径和两条非 root 命令：`status <任务路径>`、`confirm <任务路径>`。
应用任务必须证明运行中两个环境变量已消费、其他代理变量未变、节点身份/IP 未变、
Clash 配置/规则/选择未变；还必须看到 tailscaled 自己持有到真实 DERP 的 TCP socket，
且其来源端口没有再出现在 Clash 的显式代理/TUN 连接里，marked underlay 路由也不是
Meta/tailscale0/loopback。仅有 `DIRECT` 不能通过。

**即使这些检查通过，也不会自动取消回滚。** 在 10 分钟内从另一端建立新 SSH
（自动化仍通过 ShellGate），核验其他代理行为和实际路径后，原用户执行打印的
`confirm` 命令；计时任务会再次验证，状态必须变为 `confirmed` 才算保留。只看到
`verified-awaiting-confirmation` 或 confirm 文件存在不能当成完成。不要同时操作两台。

未确认、检查失败、SSH/启动终端中断时，独立计时任务恢复原独立文件（原先不存在则删除）；
其他原代理文件不改。**原文件已恢复不等于恢复完成**：还必须核对原 systemd manager
代理环境、实际 daemon 的代理环境、active 服务和原节点身份/IP/Running 状态。原环境的
大小写、未设置与空值分别校验，只保存相关值的 hash。状态分别记录文件恢复、manager
验收和运行进程验收；全部通过后才报告 `rolled-back`/`rolled-back-no-change` 并释放任务锁。

若运行状态未恢复，一次恢复只做一次 daemon-reload、一次 tailscaled restart 并重新核验。
任一步失败均保留锁和最后验证阶段，记录 `recovery-needs-operator`，不自动循环重试。
原文件或其他服务来源被并行修改、备份损坏时仍拒绝覆盖。应用/回滚期间不要并行编辑服务
配置。已确认后或恢复部分失败后，可用原任务快照发起一次明确的恢复/重试：

```bash
sudo /usr/bin/python3 tailscale/derp-bypass.py rollback <任务路径>
```

公开 rollback 不因锁文件存在就只写请求标记：它核对冻结入口 hash、进程 UID/argv/启动
时间及准确 tmux pane PID/项目归属，确认原 guard 仍能接收请求；guard 已退出则在同一任务
锁约束下启动冻结的独立恢复任务。任务内内核锁序列化交接、恢复和确认；固定恢复窗口槽
再次拒绝并发重复启动，不关闭或接管未知 pane。请求已投递/任务已续接都不代表恢复完成，
必须查看最终状态。并发请求若任务正被占用会明确拒绝，不另起第二个重启。

手动恢复可能短暂影响 SSH；一个明确重试最多再尝试一次 manager/service 恢复。健康且原
环境/身份均已恢复的重复请求会重新核验，但不重复重启。已生效且实际路径验证通过的重复
apply 不改写、不重启。只有状态记录和实际 socket 共同成立才是部署证据；root 实机启动/
恢复仍需执行验收，离线注入不能代替它。本入口不承诺消除 DERP/WAN 抖动；部署后仍需重复
同步测量。

### 非 root 验证

```bash
python3 -B tailscale/tests/test_derp_bypass.py -v
python3 -B tailscale/tests/test_derp_bypass.py --live-parser
bash -n tailscale/install.sh
```

离线测试保留并集/转义、安装器匹配、systemd 实际语法、tmux 归属/槽冲突及 DIRECT 仍进
TUN 的拒绝检查。恢复故障注入经真实公开命令分支覆盖：reload 失败、文件恢复后 restart
失败、原文件下停止/错环境/错身份的 daemon、死 guard 留锁、并发明确重试排斥及健康重复
请求；只有原 manager/实际进程环境与身份验收才成功。系统操作由替身和临时文件隔离，
**不能代替 root 实机恢复/部署验证**。

`--live-parser` 不需要 Go 编译器：使用已安装的 `tailscale debug ts2021 --verbose`
输出的真实 `tshttpproxy.ProxyFromEnvironment` 决策，读取后立即终止这个诊断子进程，
不调用 daemon debug API、不登录、不更改 prefs。测试只设置子进程的假代理
`127.0.0.1:9`；一个公开 `/key` GET 可能与终止竞速，不发送真实身份密钥/应用数据。
它验证域/子域/IP 的 1443 直连，而域/IP 的 443、其他 DexGem 主机 1443 和控制/日志
仍选择假代理。该测试证明 parser 语义，不证明 daemon 已加载新环境或网络已改善。
依据：[Tailscale v1.104.1 wrapper](https://github.com/tailscale/tailscale/blob/v1.104.1/net/tshttpproxy/tshttpproxy.go)、
[对应 x/net v0.59.0 parser](https://github.com/golang/net/blob/v0.59.0/http/httpproxy/proxy.go)。

## 服务与状态

```bash
systemctl is-enabled tailscaled
systemctl is-active tailscaled
tailscale version
tailscale status --json
tailscale netcheck
```

常用服务操作：

```bash
sudo systemctl restart tailscaled
sudo systemctl status tailscaled --no-pager
journalctl -u tailscaled -b --no-pager
```

## APT 索引异常

下载、`apt-get update`、`apt-get install` 或 systemd 操作失败时，脚本保留原始输出并以非零退出。被管理员 `mask` 的 `tailscaled.service` 被视为显式禁用：脚本会失败，绝不自动 `unmask`。若 APT 输出出现 `MergeList`、`Package lists` 或 `Hash Sum mismatch`，先停止并发的 APT 操作，检查错误涉及的源，再按 Ubuntu 文档人工修复 `/var/lib/apt/lists` 后重试。

脚本**不会**自动删除 APT lists：缓存损坏是异常状态，自动清理会掩盖根因并可能干扰其他 APT 操作。

## 功能边界

- 支持 Ubuntu **24.04 及更高版本**；版本判断使用 `dpkg --compare-versions`。CPU 架构由 Tailscale 官方 APT 仓库的支持范围决定。
- 默认目标是“本机作为 Tailscale 设备”。访问无客户端 LAN、全流量出口、Tailnet 内服务发布或公网发布分别需要 Subnet Router、Exit Node、Serve 或 Funnel 的明确设计与 Tailnet 策略，不应隐含开启。
- Clash proxy 是当前网络条件下的可选适配，不是所有 Tailscale 用户的要求；本地代理必须在 `tailscaled` 需要访问控制面时可用。
