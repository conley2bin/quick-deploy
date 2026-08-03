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
