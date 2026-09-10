# Ubuntu 桌面串流：Sunshine + Moonlight

**A 是被控主机，安装 Sunshine；B 是控制端，安装 Moonlight。** 两端通过 Tailscale 连接，接管 A 已登录的图形桌面。本目录独立使用，不接入 `fresh-install`。

## 开始之前

- 两端运行 Ubuntu 24.04；B 必须是 x86_64，A 支持 amd64/arm64。
- 两端先加入同一 Tailnet，允许 B 访问 A。需要内核网络模式的 `tailscale0`，可先使用 `../tailscale/`。
- A 已登录本地图形桌面，有可捕获的显示输出及正常的显卡驱动。用该桌面用户运行脚本，**不要用 sudo 运行整个脚本**；包安装步骤会自行调用 sudo。
- 需要 `curl`、`python3` 等命令；脚本发现缺失会提示。主机安装从 GitHub 下载官方 Ubuntu/架构匹配的 deb；客户端解包官方 AppImage，不依赖 FUSE，不引入 Snap/Flatpak。

## 1. 在 A 安装 Sunshine

```bash
cd ~/quick-deploy/sunshine-moonlight
./install-host.sh
tailscale ip -4
```

记下 A 的 Tailnet IPv4。安装器会输出两项：

- **Web UI**：`https://<A 的 Tailnet IPv4>:47990`
- **Moonlight 手动添加地址**：`<A 的 Tailnet IPv4>:47989`

若配置过自定义端口，以安装器输出为准。默认保留已有有效的捕获选择；需要明确选择时：

```bash
./install-host.sh --capture kms       # DRM/KMS
./install-host.sh --capture portal    # 桌面门户
./install-host.sh --capture x11       # Xorg 会话
./install-host.sh --capture auto      # 删除 capture 键，恢复自动选择
```

已有有效后端设置会保留；`wlr`、`kwin` 需要 Wayland，`x11` 需要 Xorg。脚本拒绝与会话类型冲突的选择，具体 compositor/显卡是否可用仍需连接 Desktop 检查。无效值（如 `xcb` 或字面值 `auto`）会报错，由你明确选择修正方式。

## 2. 在 B 安装 Moonlight 并配对

```bash
cd ~/quick-deploy/sunshine-moonlight
./install-client.sh
~/.local/bin/moonlight
```

1. 在浏览器打开 A 的 Web UI，确认地址后接受自签名证书提示。首次设置 Sunshine 管理员用户名与密码。
2. 在 Moonlight 手动添加安装器输出的主机地址；Tailnet 上不依赖自动发现。
3. Moonlight 在 B 显示 PIN。到 A 的 Web UI **PIN** 页面，核对待配对客户端名称与来源地址，选择对应请求，**输入 B 显示的 PIN**。
4. 配对完成后，在 Moonlight 选择 **Desktop**。

Web UI 只绑定 A 的 Tailnet IPv4；远端 `localhost:47990` 不是它的监听地址。若使用 SSH 隧道，转发目标必须是 A 的 Tailnet IPv4 和实际 Web UI 端口。

## 3. 先连接，再调整画质

先用 **1080p、60 FPS、20 Mbps**。在 A 打开一个空白编辑器，通过 B 检查画面持续更新、鼠标位置准确、键盘文字能输入；随后断开并重新连接，确认回到同一桌面。

- `Ctrl+Alt+Shift+Q`：退出串流；`Z`：切换键鼠捕获；`X`：切换全屏。
- `Ctrl+Alt+Shift+S`：性能统计；`M`：切换鼠标模式（以上均保留相同修饰键）。
- 卡顿时先看性能统计，再用 `tailscale ping` 检查到 A 的连接是 direct 还是 relay。延迟和可用吞吐会影响串流，不能只凭成功配对判断网络质量。

本流程捕获一个选定的显示输出，不保证同时映射 A/B 两块显示器。它不创建登录前桌面、不自动登录，也不提供文件传输或完整双向剪贴板；文件可走 `scp`/SFTP。

## 端口与访问范围

以基准端口 `p` 表示（默认 `47989`）：

| 用途 | 协议与端口 |
|---|---|
| 配对/控制 HTTP、HTTPS | TCP `p`、`p−5` |
| Web UI | TCP `p+1` |
| RTSP | TCP `p+21` |
| 视频、控制、音频 | UDP `p+9`、`p+10`、`p+11` |

默认 TCP 为 `47984,47989,47990,48010`，UDP 为 `47998,47999,48000`。UDP 可能只在串流时监听。

如 `port = 48000`，Moonlight 添加 `<A_IP>:48000`，Web UI 为 `https://<A_IP>:48001`。基准端口须为 `1029–65514` 的十进制整数，不带前导零。

脚本验证本机在线且已分配的 Tailnet IPv4，设置 `address_family=ipv4` 与 `upnp=disabled`，不修改防火墙、不添加公网/NAT 映射。已有防火墙与 Tailnet ACL 仍须允许 B→A。`--bind-address` 不能用来改绑 LAN、公网或通配地址。

## 检查与排障

```bash
./doctor.sh --host       # 在 A
./doctor.sh --client     # 在 B
journalctl --user -u app-dev.lizardbyte.app.Sunshine.service -e
```

Doctor 只读；退出 1 表示必需条件不满足。它检查包、实际服务配置路径、图形会话、输入节点及控制 TCP 监听；不会主动启动捕获。安装器显示“已监听”仍需上面的 Desktop 连接检查。

| 现象 | 处理 |
|---|---|
| Web UI 不通 | 核对 A 的 `tailscale ip -4`、安装器输出端口、服务日志与 ACL。Tailscale 恢复后服务若触发启动限流，可执行 `systemctl --user reset-failed app-dev.lizardbyte.app.Sunshine.service`，再重跑安装器。 |
| 提示配置路径/override 冲突 | 统一 shell 与用户管理器的 `XDG_CONFIG_HOME`/`CONFIGURATION_DIRECTORY`；先核对自定义 service/drop-in。脚本拒绝猜测自定义 ExecStart 使用的配置。 |
| 提示 `origin_web_ui_allowed=pc` | 该设置只允许本机来源。若同意 Tailnet Web UI 访问，手动改为 `lan` 后重跑；不需要 `wan`。 |
| 黑屏或 `Couldn't find monitor` | 检查输出选择、驱动和活动 scanout。KMS 在 DPMS 关闭或无头时可能丢失可捕获 framebuffer；并非所有这类错误都由熄屏引起。 |
| GNOME 锁屏后 portal 断开 | GNOME 46 会终止门户捕获，先解锁，必要时重新授权。KMS/X11 的锁屏和熄屏表现须在目标机器确认。 |
| 键鼠无效 | 检查 `/dev/uinput`、包内 udev 规则和活动会话 ACL；节点缺失不是组权限问题。`/dev/uhid` 主要关系到手柄。 |

脚本不改锁屏、睡眠、DPMS，不把 `loginctl enable-linger` 当作登录前远控方案。

## 升级、卸载与维护

版本、维护基线及客户端固定摘要集中在 [`lib/common.sh`](lib/common.sh)。Sunshine 基线包含上游 2026 年 9 月公布的修复；下载先校验摘要和 deb 元数据，再安装。相同上游版本跳过重装，更高版本保留；包、capability 或配置变化时会重启活动服务，中断当前串流。

主机升级可用 `./install-host.sh --version v版本号`。保留原配置及凭据，配置变化前备份为同目录的 `sunshine.conf.bak`。新版本可能改变显示编号，升级后核对选中的显示器。

客户端只接受固定版本；升级前核对官方新 AppImage 的 SHA-256 与精确大小，再更新共享定义。目录中的摘要标记记录下载来源，不表示已重新校验解包后的全部内容。

```bash
./uninstall.sh --client                 # 只删本流程带标记的客户端内容
./uninstall.sh --host-package           # 先停用当前用户服务，再移除本流程引入的包
./uninstall.sh --destroy-host-state     # 先停止并禁用服务，再删除有效配置目录；不可恢复
```

主机包移除默认保留凭据，预先存在的包默认拒绝删除；`--help` 说明显式强制选项。状态删除会停止并禁用服务，避免下次图形登录用默认配置启动；再次使用前重跑 `./install-host.sh` 恢复 Tailnet 配置与服务。配置目录本身若为符号链接，会在停止服务前拒绝删除；普通 HOME/XDG 父目录链接不受此限制。目录外的凭据不在删除范围；其他用户或手动启动的 Sunshine 实例会报告 PID/UID，不会被终止。

维护时运行 `./tests/run.sh`：测试用临时 HOME、命令 mock 和真实 apt 模拟，不安装包或操作真实服务。

参考：[Sunshine 最新发行与安全修复](https://github.com/LizardByte/Sunshine/releases/latest) · [Sunshine 配置文档](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html) · [Moonlight 使用指南](https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide)
