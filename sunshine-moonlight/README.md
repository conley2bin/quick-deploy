# Ubuntu 桌面串流：Sunshine + Moonlight

**每次连接中，A 是被控主机，运行 Sunshine；B 是控制端，运行 Moonlight。** 桌面机 `conley-company` 与 Y9000P 都可安装两者：控制哪台，哪台就是本次的 A；反向连接时角色互换。两端通过 Tailscale 连接，接管 A 已登录的图形桌面。本目录独立使用，不接入 `fresh-install`。

## 开始之前

- 两端运行 Ubuntu 24.04；B 必须是 x86_64，A 支持 amd64/arm64。
- 两端先加入同一 Tailnet，按每次连接方向允许 B 访问 A；双向使用须分别允许两个方向。需要内核网络模式的 `tailscale0`，可先使用 `../tailscale/`。
- A 已登录本地图形桌面，有可捕获的显示输出及正常的显卡驱动。用该桌面用户运行脚本，**不要用 sudo 运行整个脚本**；包安装步骤会自行调用 sudo。
- 需要 `curl`、`python3` 等命令；脚本发现缺失会提示。主机安装从 GitHub 下载官方 Ubuntu/架构匹配的 deb；客户端解包官方 AppImage，不依赖 FUSE，不引入 Snap/Flatpak。

## 本机双角色安装

若这台机器既需要运行 Sunshine 又需要运行 Moonlight，可在目录根部运行：

```bash
./install.sh
```

它先检查最新稳定 Sunshine，再检查最新稳定 Moonlight，并只在系统 Python 无法导入 PyYAML 时通过 `sudo apt-get install python3-yaml` 安装该依赖。每个组件独立查询一次其 GitHub release：缺失或较旧时下载/更新，相同版本跳过载荷但仍收敛既有配置，本机较新时保留且不降级。主机阶段失败时客户端不会查询或修改。只安装 A 时使用 `./install.sh --host-only`，只安装 B 时使用 `./install.sh --client-only`；已在双角色安装中完成这两个角色时，不要在下面的编号步骤重复安装。高级版本、捕获或绑定选项仍直接传给 `commands/install-host.sh` 或 `commands/install-client.sh`。任一阶段失败会停止后续阶段，已成功完成的阶段不会自动回滚。

## 1. 在 A 安装 Sunshine

```bash
cd ~/quick-deploy/sunshine-moonlight
./install.sh --host-only
tailscale ip -4
```

记下 A 的 Tailnet IPv4。安装器会输出两项：

- **Web UI**：`https://<A 的 Tailnet IPv4>:47990`
- **Moonlight 手动添加地址**：`<A 的 Tailnet IPv4>:47989`

若配置过自定义端口，以安装器输出为准。默认保留已有有效的捕获选择；需要明确选择时：

```bash
./commands/install-host.sh --capture kms       # DRM/KMS
./commands/install-host.sh --capture portal    # 桌面门户
./commands/install-host.sh --capture x11       # Xorg 会话
./commands/install-host.sh --capture auto      # 删除 capture 键，恢复自动选择
```

这两台保留普通 GNOME Wayland 桌面，使用原生 `--capture kms`，不转换为 Xorg；KMS 不走桌面门户授权。不传 `--capture` 时已有有效后端设置会保留；`wlr`、`kwin` 需要 Wayland，`x11` 需要 Xorg。脚本拒绝与会话类型冲突的选择，具体 compositor/显卡是否可用仍需连接 Desktop 检查。无效值（如 `xcb` 或字面值 `auto`）会报错，由你明确选择修正方式。

## 2. 在 B 安装 Moonlight 并配对

```bash
cd ~/quick-deploy/sunshine-moonlight
./install.sh --client-only
~/.local/bin/moonlight
```

1. 在浏览器打开 A 的 Web UI，确认地址后接受自签名证书提示。首次设置 Sunshine 管理员用户名与密码。
2. 在 Moonlight 手动添加安装器输出的主机地址；Tailnet 上不依赖自动发现。
3. Moonlight 在 B 显示 PIN。到 A 的 Web UI **PIN** 页面，核对待配对客户端名称与来源地址，选择对应请求，**输入 B 显示的 PIN**。
4. 配对完成后，在 Moonlight 选择 **Desktop**。

反向连接时，在新的 B 添加新的 A，并到新 A 的 Sunshine Web UI 完成配对。**配对归各台 Sunshine 主机分别管理，一次配对不代表反方向已配对**；两台也分别设置自己的 Sunshine 管理员凭据。

Web UI 只绑定 A 的 Tailnet IPv4；远端 `localhost:47990` 不是它的监听地址。若使用 SSH 隧道，转发目标必须是 A 的 Tailnet IPv4 和实际 Web UI 端口。

## 3. 用本机清单连接

安装完成后，先从示例建立本机清单；它包含机器名、Tailnet IPv4 与 SSH 目标，不保存密码、PIN、私钥或命令参数：

```bash
cd ~/quick-deploy/sunshine-moonlight
[ -e machines.local.yaml ] || cp machines.example.yaml machines.local.yaml
$EDITOR machines.local.yaml
./run_server.sh --list
```

`machines.local.yaml` 被模块的 `.gitignore` 忽略；已有本地文件不会被安装器或连接器改写。默认清单始终是 `run_server.sh` 同目录的 `machines.local.yaml`，不会因当前目录改变。用 `--config PATH` 时，PATH 按当前工作目录解析，适合临时、明确指定的清单。

每个名称需有一个 `ssh`（单个别名或 `[user@]hostname/IP`）和 `tailnet_ip`（IPv4）；`moonlight_port` 可省略并默认 `47989`，`ssh_port` 可选。字段以 [`machines.example.yaml`](machines.example.yaml) 为准；未知字段、重复键、非字符串地址、布尔值/字符串端口、无效 IP 或端口都会在启动本地程序前报错。

在 **Moonlight 中独立完成配对** 后，以名称启动 Desktop 串流：

```bash
./run_server.sh desktop              # 默认 Moonlight Desktop
./run_server.sh --moonlight desktop  # 同上
./run_server.sh --config /path/to/inventory.yaml desktop
```

连接器实际执行固定的 Moonlight Qt 命令：`~/.local/bin/moonlight stream -- <Tailnet IPv4>:<基准端口> Desktop`。它不会自动配对、不会把 Moonlight 返回 0 解释成“已连接”，也不会远程安装、启动 Sunshine 或改写远端配置。Moonlight 仍是桌面 GUI，须从拥有正常图形会话的本机用户运行；未配对或运行时错误可能显示对话框后退出。

SSH 是另一个明确动作，只打开交互式登录并沿用现有 SSH config、密钥/agent 与 known_hosts：

```bash
./run_server.sh --ssh desktop
```

它不会启动串流、传入远程命令或放宽主机密钥检查。`--list` 只列出清单名称，必须指定名称的模式不会默认选择或遍历机器。

## 4. 先连接，再调整画质

先用 **1080p、60 FPS、20 Mbps**。在 A 打开一个空白编辑器，通过 B 检查画面持续更新、鼠标位置准确、键盘文字能输入；随后断开并重新连接，确认回到同一桌面。

- `Ctrl+Alt+Shift+Q`：退出串流；`Z`：切换键鼠捕获；`X`：切换全屏。
- `Ctrl+Alt+Shift+S`：性能统计；`M`：切换鼠标模式（以上均保留相同修饰键）。
- 卡顿时先看性能统计，再用 `tailscale ping` 检查到 A 的连接是 direct 还是 relay。延迟和可用吞吐会影响串流，不能只凭成功配对判断网络质量。

本流程捕获一个选定的显示输出，不保证同时映射 A/B 两块显示器。安装脚本不创建登录前桌面、不配置自动登录，也不提供文件传输或完整双向剪贴板；文件可走 `scp`/SFTP。

### 选择与切换显示输出

在 A 的 Sunshine 日志中查看实际报告的 connector 名称，将安装器所选配置目录内 `sunshine.conf` 的 `output_name` 设为该名称。以 **Sunshine 本次报告的名称**为准，不照抄另一台机器的名称、`/sys/class/drm` 路径或旧 `monitors.xml`。保存后，在 A 用运行 Sunshine 的同一桌面用户执行 `systemctl --user restart app-dev.lizardbyte.app.Sunshine.service` 使其生效（会中断当前串流），再重新连接 Desktop 核对捕获屏幕；仅断开重连或重跑安装器不保证应用这项手动修改。

串流中用 `Ctrl+Alt+Shift+F1`…`F12` 切换 Sunshine 枚举的第 1…12 个输出，逐个确认对应屏幕。它只切换捕获对象，不修改 GNOME 布局或电源策略。两台均先验证 NVIDIA 外接 HDMI/DP 输出，再使用已通过画面、输入检查的输出。Y9000P 内屏走 Intel，KMS 可枚举的输出还受编码器影响；外接屏切换成功不代表可跨 GPU 切到内屏，内屏捕获须单独实测。

## 重启、等待网络与熄屏

安装器沿用原生 Sunshine 用户服务及其桌面启动延迟，添加一个启动前检查：**配置中的确切 Tailnet IPv4 必须已分配给 `tailscale0`，才启动 Sunshine**。地址缺失时检查失败，服务在失败后等待 5 秒重试；每次还保留原生的 5 秒桌面等待，不会因原来的 500 秒启动限流窗口而放弃。显式停止服务或停止 `graphical-session.target` 会取消重试：

```bash
systemctl --user stop app-dev.lizardbyte.app.Sunshine.service
```

检查同时固定安装时选中的配置目录；运行时 `XDG_CONFIG_HOME`/`CONFIGURATION_DIRECTORY` 若指向另一目录，即使那里也有合法配置，也会拒绝启动，避免换用另一套配置/凭据。此机制只处理启动前地址未就绪，不监控运行后的网络、捕获健康或任意正常退出。

### 每台主机的登录前提

重启后直连需要 GDM（Ubuntu 图形登录管理器）先创建普通 GNOME Wayland 桌面。`conley-company` 与 Y9000P 当前均未启用自动登录；**每台分别确认允许后**，在 Ubuntu「设置」中找到「用户」，解锁管理设置，为指定桌面账号开启「自动登录」。这意味着**任何能实体接触该机器的人，都可以在开机后进入该账号桌面，无需登录密码**。安装脚本不代为开启；在获准的重启中验收，不在远程工作中重启 GDM。

两机当前磁盘检查未见 `crypto_LUKS`，且用户确认没有开机解密提示，因此不为这两台配置磁盘解锁。锁屏密码、sudo 密码与启动前磁盘解密是不同关卡；自动登录也不取消账号密码。

### 分别验证物理黑暗与活动输出

KMS 需要活动、可读取的 scanout/framebuffer，物理屏幕变暗时也必须保留它。显示器电源关闭、GNOME 空闲熄屏/DPMS、内屏背光归零、合盖和整机挂起不能互相代替；本方案不要求支持合盖。以下电源或桌面设置调整均须另经授权，不提供未经实测的配置脚本。

- **桌面机 `conley-company`**：空闲熄屏已是 Never（`idle-delay=0`），先保持现有策略，测试外接显示器电源关闭：逐台关闭，再测试两台都关。即使 Never，显示器断电仍可能断开显示连接，使 GNOME 移除活动输出；若画面冻结或丢失，先换另一组接口/显示器验证，不把“仍 connected”当作通过。
- **Y9000P**：空闲熄屏目前是 900 秒，观测时所有已连接输出均为 disabled/DPMS Off；这与空闲熄屏相符，但尚未证明原因。获准后先唤醒现有桌面，将「设置 → 电源 → 空白屏幕」设为 Never，保持整机不挂起，并开启外接显示器，恢复亮屏基线；等待超过 900 秒确认输出仍可捕获。优先在 NVIDIA 外接输出验证串流，然后逐项测试外屏电源关闭，最后才评估内屏背光设为 0：先核实背光节点确实控制该内屏，再确认物理不发光且串流画面、输入仍正常。已有亮度 0 的读数不证明该路线可用；输入、重连是否重新点亮以及重启后能否保持，均需实测。

**保留锁屏设置，不默认关闭锁屏。** Never 会改变依赖空闲熄屏触发的自动锁屏行为；手动锁屏仍可能另行触发 DPMS，不能从 Never 推断锁屏后可远控。必须做下面的手动锁屏、延迟重连及远端输入账号密码测试；失败后不自动改成免锁屏。

### 每个方向的验收顺序

先以桌面机为 A、Y9000P 为 B，在实际跨网连接中依次完成：

1. **亮屏画面与输入**：在 A 的空白编辑器输入文字、移动窗口，确认持续更新与键鼠位置正确。
2. **逐项变暗**：按各机路线一次只改变一项，恢复亮屏基线后再测下一项，最后测目标黑暗组合；每次均检查变化中的画面和输入，黑屏、冻结或无法操作均不通过。
3. **切换输出**：在目标黑暗条件下切换已验证的输出，核对捕获的是目标屏幕，画面与输入均正常。
4. **延迟重连**：保持目标黑暗条件，断开后等待一段时间（Y9000P 超过原先的 900 秒）再连接，确认回到同一桌面且仍可操作。
5. **锁屏重连**：手动锁屏后断开，等待超过 30 秒再连接，用远端键盘输入该账号密码解锁，确认画面、输入恢复且物理屏幕仍保持目标黑暗状态。
6. **暗屏安排下重启**：保存工作、确认自动登录已获准开启，在目标黑暗条件已安排好时进行获准的重启；无人本地操作即可连接并使用。外屏关电开机、内屏背光归零能否跨重启保持，分别按所需条件验收。
7. **反方向**：交换 A/B，使用另一台 Sunshine 主机的配对，完整重复以上步骤。

每个方向还须恢复物理显示，确认看到远端刚才操作的同一会话和编辑内容。断线不应另建桌面；重启则创建新会话，不保留旧进程。

**未经对应实测，不宣称暗屏串流、锁屏控制、跨 GPU 切屏、背光持久化、关显示器开机或跨网无人值守可用。** 服务已启动、端口已监听或输出显示 connected 均不能替代上述验收。

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

`address_family`/`bind_address` 按 Sunshine 原生顶层键读取；列表中的同名文本不算绑定配置，也不会被这两个键的更新改写。原生标量保留行尾和 `#` 前的空白，因此安装器把这两个键的行内注释保留为独立注释行，输出无尾随空白的值。重复、缺值或非标量的绑定键，以及未闭合列表会在安装前拒绝；请先人工修正。其它键继续沿用原有配置处理。

## 检查与排障

```bash
./commands/doctor.sh --host       # 在 A
./commands/doctor.sh --client     # 在 B
journalctl --user -u app-dev.lizardbyte.app.Sunshine.service -e
```

Doctor 只读；退出 1 表示必需条件不满足。它检查包、实际服务配置路径、图形会话、输入节点及控制 TCP 监听；不会主动启动捕获。安装器显示“已监听”仍需上面的 Desktop 连接检查。

| 现象 | 处理 |
|---|---|
| Web UI 不通 | 核对 A 的 `tailscale ip -4`、安装器输出端口、服务日志与 ACL。日志若提示 `not assigned to tailscale0`，服务正等待配置中的确切地址；地址恢复后自动重试。若 Tailnet 地址已改变，在图形桌面重跑安装器更新配置。 |
| 日志提示 `configuration directory changed` | 运行时配置来源与安装选择不一致。核对用户服务环境中的 `XDG_CONFIG_HOME`/`CONFIGURATION_DIRECTORY`，恢复安装时的目录；不要用另一套配置绕过检查。 |
| 提示配置路径/override 冲突 | 统一 shell 与用户管理器的配置来源；核对自定义 service/drop-in。仅接受本流程原样生成的 retry 文件及有效策略，修改过或外来的文件会保留并拒绝操作。 |
| 提示 `origin_web_ui_allowed=pc` | 该设置只允许本机来源。若同意 Tailnet Web UI 访问，手动改为 `lan` 后重跑；不需要 `wan`。 |
| 黑屏或 `Couldn't find monitor` | 检查输出选择、驱动和活动 scanout。KMS 在 DPMS 关闭或无头时可能丢失可捕获 framebuffer；并非所有这类错误都由熄屏引起。 |
| GNOME 锁屏后 portal 断开 | GNOME 46 会终止门户捕获，先解锁，必要时重新授权。KMS/X11 的锁屏和熄屏表现须在目标机器确认。 |
| 键鼠无效 | 检查 `/dev/uinput`、包内 udev 规则和活动会话 ACL；节点缺失不是组权限问题。`/dev/uhid` 主要关系到手柄。 |

脚本不改锁屏、睡眠、DPMS，不把 `loginctl enable-linger` 当作登录前远控方案。

## 升级、卸载与维护

版本、维护基线及审计例外集中在 [`lib/common.sh`](lib/common.sh)。默认安装会检查最新稳定 release；最新不是“无条件成功”的承诺：每个资产必须有 GitHub API 提供的 `sha256:` 摘要和正的精确大小，下载后再次核对大小和 SHA-256。缺少/无效摘要会在任何相关载荷修改前停止，绝不把 HTTPS 下载、下载后自己计算的 hash 或旧版本冒充最新。

唯一受审计例外是 Moonlight 的精确 `v6.1.0` `Moonlight-6.1.0-x86_64.AppImage`（release ID `175337682`、asset ID `193059073`、大小 `55325888`、SHA-256 `0e855ffd22d407e18ab5fdb575fed5f01ca119a3f91993c5f0213f15ac80b400`）。它的旧 API 记录没有 digest；一旦 API 为这个精确资产给出 digest，必须与该记录一致。未来任一 digest-null Moonlight 最新版会停止，等待单独审阅的 tag 专用校验值，而不会降级或复用 6.1.0 的摘要。

主机升级可用 `./commands/install-host.sh --version v版本号`，客户端同样支持该形式；tag 必须是稳定的数字版本且不低于维护基线。显式 tag 也不会降级本机较新版本。主机更新保留原配置及凭据，配置变化前备份为同目录的 `sunshine.conf.bak`；包、capability、配置或 retry 策略变化时会重启活动服务，中断当前串流。客户端目录中的摘要标记记录安装时的来源，不表示重新校验解包后的全部内容。

若公共 GitHub API 配额耗尽，可临时在调用环境提供 `GITHUB_TOKEN` 或 `GH_TOKEN`；相同的双变量值可用，不同值会在联网前拒绝。脚本只通过内存中的 curl 配置发送认证头，不把 token 写入文件、日志、命令行或下载请求，也不会持久化它。

```bash
./commands/uninstall.sh --client                 # 只删本流程带标记的客户端内容
./commands/uninstall.sh --host-package           # 先停用当前用户服务，再移除本流程引入的包
./commands/uninstall.sh --destroy-host-state     # 先停止并禁用服务，再删除有效配置目录；不可恢复
```

受管 retry 文件位于 `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/app-dev.lizardbyte.app.Sunshine.service.d/`，仅有 `quick-deploy-retry.conf` 和 `check-tailnet.py`。安装器不覆盖外来/改写的文件；卸载先停止并禁用服务，再移除原样的受管文件并 reload，保留无关文件。普通 HOME/XDG 父目录链接可用：加载的 drop-in 按真实路径核对，启动参数仍保留安装时的字面路径；不接受 retry 目录或两个受管文件本身的符号链接。

主机包移除默认保留凭据，预先存在的包默认拒绝删除；`--help` 说明显式强制选项。状态删除会停止并禁用服务，避免下次图形登录用默认配置启动；再次使用前重跑 `./commands/install-host.sh` 恢复 Tailnet 配置与服务。配置目录本身若为符号链接，会在停止服务前拒绝删除；普通 HOME/XDG 父目录链接不受此限制。目录外的凭据不在删除范围；其他用户或手动启动的 Sunshine 实例会报告 PID/UID，不会被终止。

维护时运行 `./tests/run.sh` 及 `python3 ./tests/run_server.py`：前者在临时 HOME/PATH 下实际执行启动检查，覆盖地址缺失、配置来源变化、归属与卸载顺序，以及 HOME/XDG 父目录链接下的安装、复装、doctor 和移除；后者在带空格的临时模块副本中实际执行 `run_server.sh`→Python→假 Moonlight/SSH，核对参数、stdin、退出码和无副作用拒绝。延迟超过 500 秒及停止重试采用离散时间模型，不是真实 systemd 运行验收。有 `systemd-analyze` 时另做静态单元解析和加载路径核对；APT 仅模拟，不安装包或操作真实服务，也不验证实际画面/输入。

绑定解析测试使用已核对的原生解析结果。若本地有对应标签源码和 C++ 编译器，可额外运行 `python3 tests/binding.py --native-source /path/to/Sunshine-2026.906.222525/src/config.cpp`，离线编译该文件的原始解析函数，对照输入、重写结果和隔离安装器输出；不会构建或运行 Sunshine。

参考：[Sunshine 最新发行与安全修复](https://github.com/LizardByte/Sunshine/releases/latest) · [Sunshine 配置文档](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html) · [Moonlight 使用指南](https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide)
