# Ubuntu 桌面串流：Sunshine + Moonlight

**每次连接中，A 是被控主机，运行 Sunshine；B 是控制端，运行 Moonlight。** 桌面机与笔记本都可安装两者：控制哪台，哪台就是本次的 A；反向连接时角色互换。两端通过 Tailscale 连接，接管 A 已登录的图形桌面。本目录独立使用，不接入 `fresh-install`。

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

它先检查最新稳定 Sunshine，再检查最新稳定 Moonlight，并只在系统 Python 无法导入 PyYAML 时通过 `sudo apt-get install python3-yaml` 安装该依赖。每个 vendor 组件独立查询一次其 GitHub release：缺失或较旧时下载/更新，相同版本跳过载荷但仍收敛既有配置，本机较新时保留且不降级。主机阶段失败时客户端不会查询或修改。只安装 A 时使用 `./install.sh --host-only`，只安装 B 时使用 `./install.sh --client-only`；已完成的角色无需重复安装。

所选 vendor 阶段成功后，含主机角色的安装再运行独立的显示控制伴随阶段（下节）；客户端单角色跳过它。伴随阶段只资格检查已安装的 **Sunshine 2026.914.233613/source63d35f7**，不降级其它版本、不把 vendor 安装成功当作兼容性证明。固定 SSH shim 缺失时，默认可完成私人控制器发布，但会明确返回失败、报告 `Shim NOT PUBLISHED`；默认不自动提权发布它。确认允许新增系统入口后，可选 `./install.sh --publish-display-control-shim`（或加在 `--host-only` 后）；已成功安装 vendor 时，优先单独补跑 `commands/install-display-control.sh --publish-shim`，避免为了补伴随程序重新触发 vendor 更新/重启。

所选阶段**全部成功**后，本入口仅在模块根目录缺少 `machines.yaml` 时新建全字段中文占位清单（见第 3 节）：模板不含真实地址、不做地址发现；已有普通清单始终原样保留，包括内容、权限与修改时间。伴随阶段失败时明确报告 vendor 已成功，但不新建清单、不声称全部完成、不回滚已完成阶段。高级 vendor 版本、捕获或绑定选项仍直接传给 `commands/install-host.sh` 或 `commands/install-client.sh`。本根入口仍是 Ubuntu 安装器，不是 Windows/macOS vendor 包安装器。

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

### 在已安装的 A 独立安装显示控制伴随程序

这是与官方 Sunshine 包分开拥有的程序；协议见 [`display-control/README.md`](display-control/README.md)。客户端已接入实时选屏、配对与受管串流。当前主机 lifecycle 支持 Linux/KMS；Windows/macOS 的服务/图形会话控制器与安装适配仍待实现，原生枚举代码不能替代它们。Linux 的组合验收目前使用隔离运行事实和私有终端，实际捕获画面仍须按第 4 节核对。

以运行 Sunshine 的同一桌面用户，在 A 本地执行（不要 `sudo` 整个安装器）：

```bash
./commands/install-display-control.sh --help
./commands/install-display-control.sh
# 仅在明确同意新增下面的系统级入口后：
./commands/install-display-control.sh --publish-shim
# 仅在无已有/在途非受管串流且同意所有受管连接使用同一控制器时：
quick-deploy-display-control --enroll
```

- **实际依赖**：Ubuntu 24.04+ 的官方 amd64/arm64 deb，系统 `/usr/bin/python3`（3.10+、标准库）、`curl`、`sha256sum`、`stat`、`dpkg`/`dpkg-query`/`dpkg-deb`、`systemctl`/`busctl`/`journalctl`，以及运行时 Linux KMS 的 `libdrm` 和可用图形会话。安装器检查依赖，不自动用 APT 补装。只有显式 shim 发布步骤需要 `sudo` 和写 `/usr/bin` 的权限；API 不需要 Sunshine 管理员、TLS 或 Moonlight 凭据。
- **来源资格**：复用 `common.sh` 的 GitHub API 凭据保护与 SHA256 校验，查询精确 `v2026.914.233613` 元数据，要求完整 `target_commitish=63d35f702ee9e362e43263742981836ec0710384`、对应 Ubuntu/架构的规范下载 URL、非空 SHA256 和精确 size。参考 deb 校验后，`dpkg-deb` 只读解出数据，不安装包、不执行二进制/maintainer scripts；逐项比较实际 root-owned `/usr/bin/sunshine`、加载的官方用户 unit 及整个 `/usr/share/sunshine` 资产文件集。该资产路径来自精确源码的编译/打包定义，不是猜测的工作目录。Package/Architecture/完整 Version 也须匹配已安装包；其它/较新版本、变化的 bytes 或缺少独立摘要都拒绝，不自动降级，也不支持未经证明的 source-build。
- **实际运行来源**：沿用已接受的 Linux 用户管理器/进程 ownership 检查，解析真正的 HOME、配置根、`sunshine.conf`、`file_apps`、Tailnet bind/base port、官方 unit 和受管 retry guard；UUID 取自该绑定的公开 `/serverinfo`，不读取服务器 credential state 来找 ID。普通配置父目录链接可用，配置/资产/出版物的叶链接、外来 unit/ExecStart/环境 override、冲突设置均拒绝。
- **固定 SSH 名称**：显式 `--publish-shim` 原子新建 root-owned、0755、非 setuid 的 `/usr/bin/quick-deploy-display-control`。非交互 SSH 的 PATH 必须含 `/usr/bin`，不依赖 `.local/bin`、登录 profile 或 shell 猜路径。已存在的同字节、正确归属 shim 保留；符号链接、非普通文件、外来/不同内容或发布时新出现的目标拒绝覆盖。运行 dispatcher 在 NSS 查找前拒绝 root EUID、UID/EUID 不一致和任意 command/path 参数；以有效 UID 的 NSS home 查找固定私人安装，不信任 `HOME`/`USER`/XDG 或 RPC 字段。RPC 保留有界 stdin JSON/stdout JSON，不运行 `sudo`。已完成的普通 stdin RPC 响应（包括协议错误/host refusal）沿用 host 的退出0契约，调用方仍须检查 JSON 的 `ok`/`error`，不能把退出0当作操作成功或释放证明；本地交互 `--enroll` 失败仍退出1。

私人路径以 **NSS home 的真实路径**为根，不受调用 cwd 或环境变量重定向：

| 路径 | 归属/含义 |
|---|---|
| `.local/share/quick-deploy/display-control/versions/v1-<source-sha256>/` | 当前用户0700；独立 controller 版本，代码与 `.owned.json` 为0600 |
| 版本内 `host-settings.json` | 当前用户0600；schema2 的 qualified build、config/apps/assets、UUID/base、runtime user/home/unit 和固定 state 路径 |
| `.local/share/quick-deploy/display-control/active/00000001.json` 等 | 当前用户0600；连续、create-only active 记录，最后完整记录指向当前版本，无易被覆盖的 symlink |
| `.local/share/quick-deploy/display-control/state/` | 唯一当前用户0700 state；永久 lock、reservation、operation、StartReceipt、prepare backup，跨版本保留 |

固定 dispatcher 及本地安装入口使用显式 `python3 -I -B` 启动 controller；`-I` 会忽略 `PYTHONDONTWRITEBYTECODE`，因此不能用该环境变量代替 `-B`。版本目录不生成 `__pycache__`，原有精确成员/hash 校验保持严格。

重新运行独立安装器会再次独立资格检查；相同代码不重写，代码更新发布新版本/active 记录，保留有效 settings 原始字节及 state 的 bytes/权限/mtime/inode，不重新 enrollment、不清除能力或 receipt。冲突、腐坏、不完整或未知的中断发布显式停止，等待可信人工核对，不自动迁移/重置。官方 Sunshine 更新不会覆盖这些 companion-owned 文件；改变 vendor bytes 后必须重新取得兼容资格，旧 receipt 不能冒充新进程的证明。安装器不读写个人 `machines.yaml` 或遗留清单。

首次发布是 **UNENROLLED**：没有收养已有串流，也没有签发受控启动 receipt。`--enroll` 必须从主机用户的交互终端调用，使用 `/dev/tty` 的 `ENROLL` 独占确认与新鲜 idle 事实；busy/非受管串流阻止 enrollment。普通 stdin RPC 即使提交 `action=enroll` 也拒绝，FREE 不会自动 enrollment。重新安装不重演仪式。后续必要的 idle restart/receipt 属于已预约的准备操作，不属于安装阶段；不要在受管预约中并行运行下文的手动改输出/重启步骤。

独立伴随安装不改 Sunshine 配置、vendor unit、retry 策略、sshd 或设备，不启动/reload/restart/cancel 服务。`NeedDaemonReload=yes` 是实际拒绝条件：本入口不发布 unit，因此不会为了通过资格检查而 reload。应先核实真正的待加载定义及维护权限；不能绕过 guard，更不能用服务重启伪造 readiness。现有 vendor 安装器本身仍可能因包/配置变化重启活动 Sunshine，使用它前须安排获准的维护窗口。

## 2. 在 B 安装 Moonlight

```bash
cd ~/quick-deploy/sunshine-moonlight
./install.sh --client-only
```

首次使用 A 时，在浏览器打开它的 Web UI，核对地址后接受自签名证书提示并设置 Sunshine 管理员凭据。随后按第 3 节填写清单并运行连接器；不必先在 Moonlight GUI 添加主机或启动 Desktop。

连接器在选屏、准备输出之后查询配对状态。只有官方客户端明确报告未配对时，才生成随机 4 位 PIN、打印 A 的 Web UI 地址与操作步骤，并运行官方 `pair`。你在 A 的 **PIN** 页面核对待配对客户端及来源地址，输入终端显示的 PIN。配对进程返回后再次查询确认；退出0不能替代确认。浏览器始终由你操作，连接器不打开或自动填写网页。

受管连接统一从连接器发起；不要同时用 Moonlight GUI、其它客户端或手工命令启动、恢复或切换串流。已有手工串流不会被收养或关闭；切换到受管流程前须由原操作者结束它，再在获准的空闲窗口登记主机控制器。

**Sunshine 网页（Web UI）的定位**：它是 Sunshine 服务自带的网页管理/配对界面，监听 `https://<A 的 Tailnet IPv4>:<基准端口+1>`，不是需要另外安装的软件。A 必须安装并运行 Sunshine 服务本体；网页只在首次设置管理员凭据、确认 PIN 配对和修改配置时需要。配对/配置完成后日常可以不打开网页，但不能单独删除网页而仍保留完整串流控制：网页由服务本体提供，删除或停用服务就失去被控端。反向连接时两台各自维护自己的 Sunshine 服务与网页。

反向连接时，在新的 B 添加新的 A，并到新 A 的 Sunshine Web UI 完成配对。**配对归各台 Sunshine 主机分别管理，一次配对不代表反方向已配对**；两台也分别设置自己的 Sunshine 管理员凭据。

Web UI 只绑定 A 的 Tailnet IPv4；远端 `localhost:47990` 不是它的监听地址。若使用 SSH 隧道，转发目标必须是 A 的 Tailnet IPv4 和实际 Web UI 端口。

## 3. 用本机清单连接

连接前先准备本机清单：它包含机器名、Tailnet IPv4 与 SSH 目标，不保存密码、PIN、私钥或命令参数。默认清单 `machines.yaml` 不是随仓库提供的成品文件，而是由模块根目录的 `./install.sh` 在所选安装阶段全部成功且文件缺失时新建的全字段中文通用占位清单（内容固定、不含真实地址，生成过程不做任何 Tailscale/SSH 发现）；前面第 1、2 节里成功跑过的那次安装（包括 B 的 `./install.sh --client-only`）已经生成过它，所以这里不要再补跑裸 `./install.sh`：默认安装同时启用主机与客户端，会把只装客户端的 B 扩大成 Sunshine 主机安装。全新 clone 在首次成功安装前根本没有这个文件，此时连接器会提示先运行安装器；若这台机器还没成功安装过，按实际用途在 `./install.sh`、`./install.sh --host-only`、`./install.sh --client-only` 中选一个执行一次，并保持第 1、2 节已经选定的角色不变。首次新建后直接填入真实信息；以后的成功安装会保留该普通文件：

```bash
cd ~/quick-deploy/sunshine-moonlight
# 第 1、2 节的成功安装已生成 machines.yaml；尚未安装过才需要先按实际用途选好角色跑一次安装，
# 不要在这里补跑默认的 ./install.sh（它同时启用主机与客户端）。
$EDITOR machines.yaml
./run_server.sh --list
```

`machines.yaml` 与旧文件名 `machines.local.yaml` 都被模块 `.gitignore` 忽略，不会入库。默认清单只在缺失时由安装器新建，之后由你直接编辑：**重新运行安装器会保留现有普通 `machines.yaml` 的内容、权限和修改时间，不会用模板覆盖你的机器信息。** 如需查看新版模板，可运行 `python3 lib/machines_inventory.py --stdout`，它只输出模板，不修改清单。旧版安装生成的清单头部若仍写着“每次安装重写”，那是过时注释；为保护你已填写的信息，安装器不会为了更新注释而改写该文件。旧文件 `machines.local.yaml` 与其它遗留清单文件（例如曾经生成过的 `machines.example.yaml`）不再被生成、读取、迁移或清理：安装器不碰它们，连接器默认也不看它们；需要沿用某个文件时，显式 `--config machines.local.yaml`（或其它路径）仍会照常读取。请自行核对遗留文件，确认无用后手动删除；本流程不会替你删除或改写。若 `machines.yaml` 已经是符号链接、目录或其它非普通文件，安装器显式拒绝并报错停止：既不追随链接去改写别的文件，也不声称安装已全部完成。权限不足或其它写入失败同样会显式报错停止，无论哪种失败都会单独报告已经完成的安装阶段。现有普通文件即使只读也会原样保留；若清单在新建模板的原子发布瞬间被另一进程创建，安装器会拒绝覆盖并报告冲突。**默认清单始终是模块根目录的 `machines.yaml`，从任意当前目录运行都取同一份；文件缺失时连接器直接报错并给出下一步：默认清单缺失时提示先按实际用途运行一次成功的 `./install.sh` 再直接编辑它，`--config` 路径缺失时提示核对路径，绝不自动改用遗留文件或第一台机器。** 用 `--config PATH` 指定其它清单时，PATH 按当前工作目录解析，适合临时、明确指定的文件。

每个名称需有一个 `ssh`（单个别名或 `[user@]hostname/IP`）和 `tailnet_ip`（IPv4）；`moonlight_port` 可省略并默认 `47989`（基准端口，不是 Web UI 端口），`ssh_port` 可选（省略时不传 `-p`）。字段的必填/可选、用途与取值形态以安装器生成的 `machines.yaml` 中文注释为准（克隆里没有静态副本可链接，安装成功后在本模块根目录直接打开即可）；未知字段、重复键、非字符串地址、布尔值/字符串端口、无效 IP 或端口都会在启动本地程序前报错。

清单只决定本机连接路由：连接器不查询 Tailscale（不枚举成员，也不探测远端地址），不读取或改写 SSH 配置、密钥与 known_hosts。`ssh` 写成别名时，由系统 SSH 自己按你的 `~/.ssh/config`、密钥/agent 与 known_hosts 解析；连接器只把清单里的目标追加到 `ssh` 参数。

需要区分职责：主机安装器与 `./commands/doctor.sh --host` 仍会只读查询**本机** Tailscale 状态和 `tailscale0` 上已分配的 Tailnet IPv4，用于确认 Sunshine 的绑定与服务启动条件（见「重启、等待网络与熄屏」）；这属于被控主机的本机网络就绪检查，不生成、不读取也不维护远端机器清单。

A 已完成伴随安装、固定 SSH shim 发布和本地独占登记后，在 B 的正常图形会话终端按名称连接：

```bash
./run_server.sh desktop              # 实时选屏后连接受管 Desktop
./run_server.sh --moonlight desktop  # 同上
./run_server.sh --config /path/to/inventory.yaml desktop
```

1. 通过正常系统 SSH 调用 A 的固定控制器，读取当次连接的屏幕身份、当前物理尺寸和刷新率。忙碌、未登记或预约不确定时停止，不接管其它会话。
2. 取得主机预约后显示菜单：**左右箭头选择、Enter 确认、Esc 取消**。不可用屏幕说明原因；没有交互终端或数据失效时不默认选第一屏。拓扑变化会刷新菜单并要求重新选择。
3. 控制器为精确输出准备 Sunshine；需要改 `output_name` 或重新探测时，只在已证实空闲且属于本次预约的主机上受控重启。它不改变桌面分辨率、布局或电源策略。
4. 用官方 `list --csv` 查询配对与应用身份，按第 2 节处理明确未配对；核对 Moonlight 实际会解析的 Desktop ID 后才记录启动意图、运行官方串流。网络错误、未知诊断或身份不符均停止。
5. 预约覆盖准备、配对和整个串流子进程。当前官方 CLI 是单次连接，连接器没有自动重连循环；正常结束并确认释放后，可重新运行连接器。退出时先记录已拥有的正数 Desktop 的关闭意图，再调用官方 `quit`，确认同代关闭后释放。控制/启动/关闭结果不确定时保留预约，需要主机可信对账；不靠超时或进程退出强行清除。

PIN 只打印在终端，不写入清单或持久配置。客户端不远程安装软件、不使用 sudo、不修补 `Moonlight.conf`；已安装的主机控制器可执行上面的输出配置与空闲重启。`pair`/`stream` 保留本机图形环境。官方进程退出0既不是配对确认，也不是收到正确画面的证明。

Linux 默认使用精确的 `~/.local/bin/moonlight` 包装器。macOS 的客户端核心查找 `Moonlight.app`/PATH；Windows 用 `run_server.cmd`、Python3.10+/PyYAML/系统 OpenSSH 和 PATH 中的 `Moonlight.exe`。也可显式 `--moonlight-binary PATH` 指定已安装的本地官方程序；不会从清单读取任意程序路径。Windows/macOS 客户端原生运行及宿主生命周期仍未验收，不能据此宣称三平台已可部署。

SSH 是另一个明确动作，只打开交互式登录并沿用现有 SSH config、密钥/agent 与 known_hosts：

```bash
./run_server.sh --ssh desktop
```

它不会启动串流、传入远程命令或放宽主机密钥检查。`--list` 只列出清单名称，必须指定名称的模式不会默认选择或遍历机器。

## 4. 核对选中画面与画质

连接器请求选中屏幕的当前像素尺寸，并把已知有理数刷新率取整为请求 FPS。初始码率以 **4K60/20 Mbps** 为像素率起点，使用 SDR、4:2:0、auto codec 和本机 borderless 窗口；这是起点，不保证最优码率、画质或收到的 FPS。当前模式超出已支持 CLI 范围时显式拒绝，不静默降尺寸/降帧。

先确认 B 显示的确实是刚选中的 A 屏幕。在该屏幕的空白编辑器检查持续更新、鼠标位置与键盘输入；结束受管串流并确认释放后，再从连接器选择屏幕重连。屏幕 Hz、请求 FPS、性能统计中的收到/渲染 FPS 是不同事实。

- `Ctrl+Alt+Shift+Q`：退出串流；`Z`：切换键鼠捕获；`X`：切换全屏。
- `Ctrl+Alt+Shift+S`：性能统计；`M`：切换鼠标模式（以上均保留相同修饰键）。
- 卡顿时先看性能统计，再用 `tailscale ping` 检查到 A 的连接是 direct 还是 relay。延迟和可用吞吐会影响串流，不能只凭成功配对判断网络质量。

本流程捕获一个选定的显示输出，不保证同时映射 A/B 两块显示器。安装脚本不创建登录前桌面、不配置自动登录，也不提供文件传输或完整双向剪贴板；文件可走 `scp`/SFTP。

### 选择与切换显示输出

日常选屏使用连接器的实时菜单。要换屏，先结束本次受管串流并确认释放，再运行 `./run_server.sh <名称>` 选择新输出；不在受管流程中手改 `output_name`、重启服务或用 F1…F12 绕过选择。仅改变视频请求尺寸不能选择远端捕获输出。

控制器区分原生枚举、Sunshine selector 映射与实际捕获证明；屏幕 connected 不代表当前可捕获。混合显卡的内屏和外接输出可能受实际编码器/GPU过滤；外接屏通过不证明跨 GPU 内屏可用。每台主机、每个可选输出都须核对真实画面，不能照抄其它机器的 connector 名称或旧日志。

## 重启、等待网络与熄屏

安装器沿用原生 Sunshine 用户服务及其桌面启动延迟，添加一个启动前检查：**配置中的确切 Tailnet IPv4 必须已分配给 `tailscale0`，才启动 Sunshine**。地址缺失时检查失败，服务在失败后等待 5 秒重试；每次还保留原生的 5 秒桌面等待，不会因原来的 500 秒启动限流窗口而放弃。显式停止服务或停止 `graphical-session.target` 会取消重试：

```bash
systemctl --user stop app-dev.lizardbyte.app.Sunshine.service
```

检查同时固定安装时选中的配置目录；运行时 `XDG_CONFIG_HOME`/`CONFIGURATION_DIRECTORY` 若指向另一目录，即使那里也有合法配置，也会拒绝启动，避免换用另一套配置/凭据。此机制只处理启动前地址未就绪，不监控运行后的网络、捕获健康或任意正常退出。

### 每台主机的登录前提

重启后直连需要 GDM（Ubuntu 图形登录管理器）先创建普通 GNOME Wayland 桌面。两台机器当前均未启用自动登录；**每台分别确认允许后**，在 Ubuntu「设置」中找到「用户」，解锁管理设置，为指定桌面账号开启「自动登录」。这意味着**任何能实体接触该机器的人，都可以在开机后进入该账号桌面，无需登录密码**。安装脚本不代为开启；在获准的重启中验收，不在远程工作中重启 GDM。

两机当前磁盘检查未见 `crypto_LUKS`，且用户确认没有开机解密提示，因此不为这两台配置磁盘解锁。锁屏密码、sudo 密码与启动前磁盘解密是不同关卡；自动登录也不取消账号密码。

### 分别验证物理黑暗与活动输出

KMS 需要活动、可读取的 scanout/framebuffer，物理屏幕变暗时也必须保留它。显示器电源关闭、GNOME 空闲熄屏/DPMS、内屏背光归零、合盖和整机挂起不能互相代替；本方案不要求支持合盖。以下电源或桌面设置调整均须另经授权，不提供未经实测的配置脚本。

- **桌面机**：空闲熄屏已是 Never（`idle-delay=0`），先保持现有策略，测试外接显示器电源关闭：逐台关闭，再测试两台都关。即使 Never，显示器断电仍可能断开显示连接，使 GNOME 移除活动输出；若画面冻结或丢失，先换另一组接口/显示器验证，不把“仍 connected”当作通过。
- **笔记本**：空闲熄屏目前是 900 秒，观测时所有已连接输出均为 disabled/DPMS Off；这与空闲熄屏相符，但尚未证明原因。获准后先唤醒现有桌面，将「设置 → 电源 → 空白屏幕」设为 Never，保持整机不挂起，并开启外接显示器，恢复亮屏基线；等待超过 900 秒确认输出仍可捕获。优先在 NVIDIA 外接输出验证串流，然后逐项测试外屏电源关闭，最后才评估内屏背光设为 0：先核实背光节点确实控制该内屏，再确认物理不发光且串流画面、输入仍正常。已有亮度 0 的读数不证明该路线可用；输入、重连是否重新点亮以及重启后能否保持，均需实测。

**保留锁屏设置，不默认关闭锁屏。** Never 会改变依赖空闲熄屏触发的自动锁屏行为；手动锁屏仍可能另行触发 DPMS，不能从 Never 推断锁屏后可远控。必须做下面的手动锁屏、延迟重连及远端输入账号密码测试；失败后不自动改成免锁屏。

### 每个方向的验收顺序

先以桌面机为 A、笔记本为 B，在实际跨网连接中依次完成：

1. **亮屏画面与输入**：在 A 的空白编辑器输入文字、移动窗口，确认持续更新与键鼠位置正确。
2. **逐项变暗**：按各机路线一次只改变一项，恢复亮屏基线后再测下一项，最后测目标黑暗组合；每次均检查变化中的画面和输入，黑屏、冻结或无法操作均不通过。
3. **切换输出**：在目标黑暗条件下切换已验证的输出，核对捕获的是目标屏幕，画面与输入均正常。
4. **延迟重连**：保持目标黑暗条件，断开后等待一段时间（笔记本超过原先的 900 秒）再连接，确认回到同一桌面且仍可操作。
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
| 连接器提示未配对并给出 PIN | 按编号步骤在 A 的 Web UI **PIN** 页面输入该 PIN；脚本会在配对返回后重新确认，确认已配对才启动串流。PIN 只在本机终端显示，不要在其它地方留存。 |
| 连接器提示“无法确认配对状态” | A 离线、Sunshine 服务未运行或 Tailnet 不可达时会出现；按提示核对后重跑，脚本不会静默配对或串流。 |
| 连接器提示“配对未完成” | 配对窗口的退出码不能证明配对成功；在 A 的 Web UI 核对是否还有待配对请求，修正后重跑脚本（会重新检测并重新给出 PIN）。 |
| 主机返回 `Error 404` 或应用列表没有 `Desktop` | Sunshine 已连接但应用列表没有 `Desktop`，或 Moonlight 缓存了旧应用列表。登录目标主机的 Sunshine Web UI → Applications/应用，添加或恢复 `Desktop`；修改 `apps.json` 后重启 Sunshine，再关闭并重新打开本机 Moonlight（必要时删除并重新添加该主机）刷新应用列表。连接器会在发送串流请求前检查应用列表，缺少 `Desktop` 时先在终端提示。 |
| 黑屏或 `Couldn't find monitor` | 检查输出选择、驱动和活动 scanout。KMS 在 DPMS 关闭或无头时可能丢失可捕获 framebuffer；并非所有这类错误都由熄屏引起。 |
| GNOME 锁屏后 portal 断开 | GNOME 46 会终止门户捕获，先解锁，必要时重新授权。KMS/X11 的锁屏和熄屏表现须在目标机器确认。 |
| 键鼠无效 | 检查 `/dev/uinput`、包内 udev 规则和活动会话 ACL；节点缺失不是组权限问题。`/dev/uhid` 主要关系到手柄。 |

脚本不改锁屏、睡眠、DPMS，不把 `loginctl enable-linger` 当作登录前远控方案。

## 升级、卸载与维护

vendor 版本、维护基线及审计例外集中在 [`lib/common.sh`](lib/common.sh)；独立 companion 的精确兼容 pin 位于 [`display-control/qualification.py`](display-control/qualification.py)，不会改 vendor 最新/不降级策略。生成清单的模板（含各字段中文说明）集中在 [`lib/machines_inventory.py`](lib/machines_inventory.py)：改模板只影响下一次缺失清单时的新建内容；已有普通清单保持原样，可用 `--stdout` 手动查阅新版模板。默认安装会检查最新稳定 release；最新不是“无条件成功”的承诺：每个资产必须有 GitHub API 提供的 `sha256:` 摘要和正的精确大小，下载后再次核对大小和 SHA-256。缺少/无效摘要会在任何相关载荷修改前停止，绝不把 HTTPS 下载、下载后自己计算的 hash 或旧版本冒充最新。

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

维护时运行 `./tests/run.sh --fixtures-only`、`python3 ./tests/display_install.py` 及 `python3 ./tests/run_server.py`。companion 测试用真实构造的临时 synthetic deb、独立假 release 元数据和注入的 host 事实，覆盖 qualifier→schema2 decoder→实际协议请求、shim 名称解析/NSS 分派、零自动提权/无自动 enrollment、busy 拒绝、升级保留、publication race/多进程互斥与根阶段失败顺序；不运行实际 Sunshine、SSH、服务、设备或网络。`run.sh` 的历史 vendor root fixtures 显式注入伴随阶段，而独立的 `display_install.py` 验证真正的伴随实现，避免把旧版 vendor fixture 当作 pinned build。`--fixtures-only` 跳过旧 APT 原生模拟与两个 systemd 原生静态探针；无该参数的完整历史维护套件仍包含这些探针。

`run.sh` 在临时 HOME/PATH 下实际执行启动检查，覆盖地址缺失、配置来源变化、归属与卸载顺序，以及 HOME/XDG 父目录链接下的安装、复装、doctor 和移除；根安装器的测试在只复制公开代码的临时模块副本中实际运行安装与生成：三种角色成功时只在模块目录缺失 `machines.yaml` 时新建中文模板，同版本重跑保留现有清单，`--help`、参数错误、依赖或安装阶段失败都不生成；任意 cwd 与带空格路径仍取模块目录；清单路径为符号链接、目录或非普通文件时显式拒绝并保留原有字节与链接；父目录不可写等写入失败时显式报错、不创建清单，并单独报告已完成的安装阶段；已存在的普通 `machines.yaml` 字节、权限和修改时间保持不变，发布时出现的新目标不会被覆盖，旧 `machines.local.yaml` 与遗留 `machines.example.yaml` 的字节与权限保持不动；真实 checkout 只对照运行前后的存在形态、类型、权限与时间戳，从不读取实际清单。`run_server.py` 在带空格的临时模块副本中实际执行 `run_server.sh`→Python→假 Moonlight/SSH，核对参数、stdin、退出码与无副作用拒绝，缺失清单由真实生成器（而非被追踪的成品文件）创建，覆盖默认清单取自模块目录且不受当前目录影响、尚未生成时提示先运行一次成功的 `./install.sh` 并直接编辑、只有旧文件名或遗留 `machines.example.yaml` 时都不采用、两份并存时只读默认清单、`--config` 相对调用目录，以及不探测 Tailscale、不改写既有 SSH 配置。延迟超过 500 秒及停止重试采用离散时间模型，不是真实 systemd 运行验收。完整历史套件有 `systemd-analyze` 时另做静态单元解析和加载路径核对，并用真实 APT 模拟文件名分类；`--fixtures-only` 不执行这些原生探针。两种门禁都不安装真实包、不操作真实服务，也不验证实际画面/输入。

绑定解析测试使用已核对的原生解析结果。若本地有对应标签源码和 C++ 编译器，可额外运行 `python3 tests/binding.py --native-source /path/to/Sunshine-2026.906.222525/src/config.cpp`，离线编译该文件的原始解析函数，对照输入、重写结果和隔离安装器输出；不会构建或运行 Sunshine。

参考：[Sunshine 最新发行与安全修复](https://github.com/LizardByte/Sunshine/releases/latest) · [Sunshine 配置文档](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html) · [Moonlight 使用指南](https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide)
