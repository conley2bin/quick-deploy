# sunshine-moonlight — Tailscale 内网桌面串流

用 Sunshine（主机端）+ Moonlight（客户端）在 Tailnet 内串流 Ubuntu 桌面。
目标：可重复安装、可诊断、可回滚，且不改锁屏/睡眠设置、不向公网暴露任何端口。

- 主机（被串流的一方，如 Y9000P `100.123.34.64`）：Ubuntu 24.04+，安装 Sunshine。
- 客户端（发起串流的一方，本机 `100.125.138.103`）：Ubuntu 24.04+ x86_64，安装 Moonlight。
- 传输层：Tailscale。两端必须先在 Tailnet 内（见 `../tailscale/`）。

## 架构与角色划分

```
客户端 (Moonlight)  ──Tailscale 加密隧道──>  主机 (Sunshine)
  解码/显示/键鼠手柄输入                        捕获桌面画面 + 编码 + 注入输入
```

- **Sunshine 是 systemd 用户服务**，跑在主机上已登录的图形会话里，镜像的是"当前那个桌面"，
  不是登录界面，也不是独立虚拟桌面。
- **Moonlight 是客户端应用**，官方 AppImage 解包后装在用户目录，不经 Snap/Flatpak。
- **Tailscale 只做传输**。 pairing（配对）、画面、输入全部走 Tailnet 地址。

## 安装

两个脚本都不要用 root/sudo 运行；需要管理员权限的步骤会自行调用 sudo。

### 主机端（在 Y9000P 上执行）

```bash
cd ~/quick-deploy/sunshine-moonlight
./install-host.sh                     # 默认 v2026.516.143833，绑定当前 tailscale IPv4
# 可选：
./install-host.sh --capture kms       # 显式指定捕获后端：auto|kms|portal|x11
./install-host.sh --bind-address 100.123.34.64   # 显式绑定地址
```

行为要点：

- 版本下限 `v2026.516.143833`（修复 CVE-2026-32253，CVSS 9.8）。低于下限一律拒绝，**没有绕过开关**。
- 未安装或需要升级时，只选择与当前 Ubuntu `VERSION_ID` 和 CPU 架构完全匹配的官方 `.deb`，并与 GitHub API 返回的 SHA-256 digest 比对，通过才交给 apt；不匹配即放弃。已安装同版直接收敛配置，不重复下载/重装；已安装更高版会保留并拒绝降级。
- 官方包的 postinst 已经做了 setcap（`cap_sys_admin,cap_sys_nice+p`）、加载 uhid、
  安装 udev 规则；脚本只**验证并在缺失时修复**，不重复安装包所有的规则。
- 输入设备走 systemd-logind 的 uaccess ACL（图形会话用户天然可读写 `/dev/uinput`、`/dev/uhid`）；
  只有有效访问缺失时才退回把你加入 `input` 组（需重新登录生效）。
- 配置只做定向收敛：`upnp = disabled`、`bind_address = <tailnet IP>`、
  `csrf_allowed_origins` 追加本机来源、可选 `capture`。
  `~/.config/sunshine/sunshine.conf` 里的其它键、注释、凭据全部原样保留；内容变化时把修改前版本滚动备份为 `sunshine.conf.bak`，无变化不刷新备份、不重启正在串流的服务。
- 启用 canonical 用户服务 `app-dev.lizardbyte.app.Sunshine.service`
  （旧别名 `sunshine.service` 也可识别）。

### 客户端（在本机执行）

```bash
cd ~/quick-deploy/sunshine-moonlight
./install-client.sh                   # 默认 v6.1.0
```

行为要点：

- 官方 AppImage 用**固定 SHA-256 + 精确字节数**双重校验
  （`0e855ffd…80b400` / 55325888 字节），全部通过才落盘。
- 直接运行 AppImage 在 Ubuntu 24.04 会失败（只有 fuse3，缺 `libfuse.so.2`）。
  因此用 `--appimage-extract` 解包（不依赖 FUSE），安装解包后的目录：
  - 程序：`~/.local/opt/moonlight/6.1.0/`（含 `AppRun` 与摘要标记 `.quick-deploy-sha256`）
  - 启动包装：`~/.local/bin/moonlight`
  - 桌面项：`~/.local/share/applications/com.moonlight_stream.Moonlight.desktop`
- 不安装 libfuse2，不用 Snap/Flatpak。仅支持 x86_64。
- 重跑幂等：摘要一致则跳过下载，只修复包装/桌面项。

## 配对与首次连接

1. 主机上打开 Sunshine Web UI（仅限 Tailnet 内访问）：
   `https://100.123.34.64:47990`（首次设置管理员用户名/密码，凭据只落在主机本机）。
   若希望通过 SSH 隧道访问，转发目标也必须是 Sunshine 实际绑定的 Tailnet 地址（它没有监听远端 localhost）：
   ```bash
   ssh -L 47990:100.123.34.64:47990 <主机用户>@100.123.34.64
   # 然后访问 https://localhost:47990（首次会看到自签名证书提示）
   ```
   Sunshine 内置允许 localhost Web UI origin；也可以直接在 Tailnet 内访问 `https://100.123.34.64:47990`。
2. 客户端启动 Moonlight（`~/.local/bin/moonlight` 或应用列表），手动添加主机 `100.123.34.64`。
3. Moonlight 显示 4 位 PIN；到主机 Web UI 的 **PIN** 页面输入完成配对。
4. 配对后即可在 Moonlight 里看到主机桌面/应用入口。

## 捕获后端：kms / portal / x11 怎么选

`--capture` 不传时脚本不写 `capture` 键，由 Sunshine 自选（相当于 auto）。

| 后端 | 适用 | 代价/限制 |
|---|---|---|
| `kms` | 低延迟，绕过桌面门户限制 | 需要活跃的 DRM connector；**屏幕 DPMS 关闭时会报 `Couldn't find monitor`**；需要 cap_sys_admin（脚本已收敛） |
| `portal` | Wayland/GNOME 桌面，可在部分熄屏场景存活 | 依赖 GNOME portal 授权策略，可能残留过期授权 token |
| `x11` | X11 会话 | 传统捕获；注意现行配置值是 `x11`，**`xcb` 是旧名已失效**，写了会导致捕获初始化失败 |

本机当前是 X11 会话；远端按实际会话类型选择。doctor 会把 `xcb` 标为失败项。

## 边界：DPMS / 锁屏 / 登录

- **Sunshine 镜像的是已登录的图形桌面**。GDM 登录界面之前没有会话，Sunshine 不在那种模式工作；
  `loginctl enable-linger` **不是**无人值守/预登录方案，脚本明确拒绝推荐它。
- **锁屏**：串流看到的是锁屏画面；解锁行为取决于桌面环境，不要用串流当作绕过锁屏的手段。
- **DPMS/睡眠**：屏幕省电关闭时 kms 捕获可能失败（无活跃 connector）。
  本仓库**不替你改**锁屏、睡眠、DPMS 任何设置——需要主机不熄屏请自己在系统设置里调整，这是一个显式的本机取舍。

## 端口与网络暴露

- 默认端口：TCP `47984`、`47989`、`47990`（Web UI）、`48010`；UDP `47998`、`47999`、`48000`、`48002`、`48010`。若修改 `sunshine.conf` 的基准 `port`（合法范围 `1029–65514`），这一组端口会按 Sunshine 的规则整体偏移，Web UI 为 `port + 1`。
- 安装后 Web UI 绑定在 tailnet 地址（默认 `100.123.34.64`），**不绑 0.0.0.0**；
  `upnp = disabled` 防止路由器自动映射端口。
- 不做任何公网/NAT 放行。防火墙保持默认即可；请确认没有对公网放行 47984-48010。
- 绑定 tailnet 地址的固有取舍：tailscaled 不在线或 IP 变化时 Sunshine 监听会失败——这是用可用性换安全。

## Moonlight 常用快捷键（串流会话内）

- `Ctrl+Alt+Shift+Q`：退出当前串流会话
- `Ctrl+Alt+Shift+Z`：切换键鼠捕获（抓/放）
- `Ctrl+Alt+Shift+X`：切换全屏/窗口
- `Ctrl+Alt+Shift+S`：打开性能统计叠加层
- `Ctrl+Alt+Shift+M`：切换鼠标模式（远程桌面/指针）

## 这条链路不做什么（及替代方案）

- **没有原生文件传输**：Sunshine/Moonlight 不传文件。用：
  - `sftp <主机用户>@100.123.34.64` 或 `scp`；
  - `tailscale file cp`（Taildrop）在两台 Tailscale 设备间互传。
- **没有可靠的双向剪贴板**：不要依赖跨端复制粘贴作为工作流；重要内容走文件传输。
- **终端连续性**：`ssh <主机用户>@100.123.34.64` + `tmux` 是命令行工作的主通道；
  串流用于必须看图形界面的场景，两者互补。

## 日常运维

### 检查（只读，不改任何东西）

```bash
./doctor.sh            # 自动检测角色
./doctor.sh --host     # 只查主机项
./doctor.sh --client   # 只查客户端项
```

退出码 0=无失败项。检查项：系统版本、图形会话、Tailscale 状态、包版本与安全基线、
用户服务、capability、输入设备有效 ACL、配置键（含 xcb 旧值告警）、监听端口、GPU/编码器信号。
注意 doctor 从不执行 `sunshine --version`（它会写日志，不是只读）；版本一律从 dpkg 元数据读。

### 升级

- 主机：`./install-host.sh --version v<新版本>`（仍受版本下限约束；release 必须提供当前 Ubuntu/架构的官方 `.deb` 和 GitHub SHA-256 digest）。
- 客户端脚本只接受固定版本 `v6.1.0`。升级 Moonlight 时，先从官方 release 下载新 AppImage，人工核对 SHA-256 与精确大小，再更新 `install-client.sh` 顶部的版本、`PINNED_SHA256` 和 `PINNED_SIZE`；未固化校验值的新版本会被拒绝。

### 卸载与归属

```bash
./uninstall.sh --client                 # 移除 quick-deploy 安装的 Moonlight 文件
./uninstall.sh --host-package           # apt remove sunshine（仅限本脚本引入的安装）
./uninstall.sh --destroy-host-state     # 删除 ~/.config/sunshine（凭据/配对，不可恢复）
```

归属规则：

- 客户端只删带 `quick-deploy` 标记的文件/目录；外来内容一律保留并说明。
- 主机的 `sunshine` 包：只有 `~/.local/state/quick-deploy/sunshine-moonlight/host.state`
  证明它由本脚本首次引入时才允许移除；预先存在的安装默认拒绝，
  需要显式 `--force-remove-preexisting-package`。
- `~/.config/sunshine`（Web UI 凭据、配对状态）默认保留；删除必须是显式破坏性选择。

### 安全基线

- Sunshine 不得低于 `v2026.516.143833`（CVE-2026-32253，认证绕过，CVSS 9.8）。
- 所有下载先校验后落盘；摘要不匹配即放弃，系统保持原状。
- 脚本不以 root 运行；凭据、PIN、认证 URL 不进入本仓库。

## 故障排查

| 现象 | 先看 | 说明 |
|---|---|---|
| Web UI 打不开 | `./doctor.sh --host` 的"监听端口"项 | 绑定 tailnet 地址时 tailscaled 不在线会监听失败；确认 `tailscale ip -4` 与 `bind_address` 一致 |
| 配对 PIN 页面拒绝 | 浏览器访问的 origin 与 Web UI 端口 | 默认直接用 `https://<bind_address>:47990`；自定义基准端口时 Web UI 是 `port+1`。SSH 隧道必须转发到远端的 `<bind_address>:<web-ui-port>`，不能转发到远端 localhost |
| 串流黑屏/报 `Couldn't find monitor` | 主机屏幕是否 DPMS 关闭 | kms 捕获需要活跃 connector；唤醒屏幕或换 `--capture portal` |
| 报 `Unable to initialize capture method` | `sunshine.conf` 里 `capture` 的值 | `xcb` 是旧名，改成 `x11`（doctor 会标红） |
| 客户端键鼠无效 | doctor 的"输入注入"项 | 图形会话用户应经 uaccess 获得 `/dev/uinput`、`/dev/uhid` 读写；被加入 input 组后必须**重新登录**才生效 |
| 服务起不来 | `journalctl --user -u app-dev.lizardbyte.app.Sunshine.service -e` | 纯 SSH 且无图形会话时用户服务无法正常捕获；先登录本机图形会话 |
| Moonlight 直接运行 AppImage 报 `libfuse.so.2` | —— | 预期现象；请用安装后的 `~/.local/bin/moonlight`（解包部署，不需要 libfuse2） |
| 升级后想确认版本 | `dpkg-query -W sunshine` | 不要跑 `sunshine --version`（非只读） |

## 测试

```bash
./tests/run.sh
```

隔离测试：临时 HOME + PATH 命令 mock，不需要 root、不需要网络，
绝不触碰真实 Sunshine/Moonlight 安装与 `~/.config/sunshine`。
