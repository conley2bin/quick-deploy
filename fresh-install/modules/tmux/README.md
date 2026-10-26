# tmux 安装模块

本模块为 Ubuntu 24.04+ 主机安装 tmux 和 [Oh my tmux!](https://github.com/gpakosz/.tmux)（gpakosz/.tmux）配置：

```bash
./install.sh
```

## 安装内容

1. `apt install tmux git xclip wl-clipboard` —— 后两者服务的是 gpakosz 配置自带的 `<前缀> y` 绑定（后台任务 `save-buffer - | <探测到的工具>`，在图形会话里才写得进去），在本地图形会话里有意义；**远端复制回本机走的是原生 OSC 52，不需要这两个包**，见下文剪贴板一节。
2. 克隆 `https://github.com/gpakosz/.tmux` 到 `~/.tmux`（`--single-branch`）。
3. 链接本仓库的 `tmux.conf.local`（见下表）。
4. 运行 `pi-agent/extensions/pi-tmux-window-status/install.sh`，把 Pi→tmux breathing status 扩展链接进 `~/.pi/agent/extensions/`。

## 脚本创建的链接

| 路径 | 指向 | 作用 |
| --- | --- | --- |
| `~/.tmux.conf` | `~/.tmux/.tmux.conf` | 主配置入口（tmux 固定读取位置）；随重跑时的 `git pull` 自动更新 |
| `~/.tmux.conf.local` | `fresh-install/modules/tmux/tmux.conf.local`（安装时本仓库的绝对路径） | 定制入口；改动即仓库改动 |
| `~/.pi/agent/extensions/pi-tmux-window-status` | `pi-agent/extensions/pi-tmux-window-status`（安装时本仓库的绝对路径） | Pi 生命周期到 tmux breathing status 的受管扩展 |

约束与修复：

- 替换任何既有文件前一律先改名为 `*.bak.<时间戳>`，从不删除。
- `~/.tmux.conf.local` 记录的是仓库的绝对路径：若整个仓库搬走，链接会悬空；到新位置重跑一次 install.sh 即可重新链接。少数编辑器写文件时会把符号链接替换成普通文件，重跑 install.sh 同样自动备份并重建链接。
- Pi 扩展链接的所有权与迁移规则（旧名 `quick-deploy-tmux-status` 的 legacy 迁移、foreign 路径拒绝改动等）见扩展自身的 `pi-agent/extensions/pi-tmux-window-status/README.md` 与 `install.sh`。

## 定制入口

上游明确要求**不要改主配置** `~/.tmux/.tmux.conf`（改了 `git pull` 会冲突），一切定制写在 `.tmux.conf.local` 里。本模块把这个入口符号链接到仓库文件，因此：

- 单一事实源：改 `~/.tmux.conf.local` 就是改仓库文件（`<前缀> e` 打开的也是它）；保存只更新基线文件，运行中的 tmux server 仍需 `<前缀> r` 重载；`git commit` 入库后，已链接的机器只靠 `git pull` 即可接收更新。
- 注意：gpakosz 每次加载配置都会用 `cut -c3- "$TMUX_CONF_LOCAL" | sh -s printf probe` 探测本文件是否旧式脚本格式——注释行剥掉前两个字符（`# `）后会**被 shell 真实执行**，因此注释里不要写 `> < ; | & $() 反引号` 等元字符（历史上的 `（CSI > 4 ; 2 m）` 曾在服务器工作目录生成空文件 `4`）；需要表达时用全角 `＞ ；` 代替。
- 基线只记真实改动（目前是鼠标模式、DCS 透传 `allow-passthrough all`（机制与代价见下节）、`set -s set-clipboard on`（原生 OSC 52，见下节）、状态栏左键释放切换 window、禁用状态栏区域滚轮切换 window、copy-mode 字母键退出并原样输入、Ctrl+V 退出后交给应用粘贴、vi Alt+V 矩形选择、pane-owned Pi error Esc sentinel 路由、精简状态栏、选中 window 两端蓝色竖条、取消 `Ctrl+a` 第二前缀、`Ctrl+Alt+←/→` 切换 window、`Ctrl+Alt+=/+` 新建 window）；全部可用选项查上游模板 `~/.tmux/.tmux.conf.local`。该文件本质是 tmux 配置片段，可直接写 `set -g ...`；若某行被主配置覆盖，按上游说明在行尾加 `#!important`。

仓库编辑、`git pull` 和本模块测试都不会向当前用户的 tmux server 写选项。已安装上述链接的机器，运行中 server 在重载后读取新基线；尚未启动的 server 在下次启动时读取。

Pi suspend guard 与 breathing status 都不是本模块的源码：它们分别在 `pi-agent/extensions/pi-suspend-guard/` 和 `pi-agent/extensions/pi-tmux-window-status/`，本模块只负责链接安装。

## 剪贴板：远端复制回到本机（原生 OSC 52）

基线的 `.tmux.conf.local` 显式设置（服务端 scope）：

```tmux
set -s set-clipboard on
```

复制时 tmux 把选中的内容用 OSC 52 序列发给外层终端，由外层终端（本仓库对应 Ghostty）写入本机系统剪贴板。完整路径（设计意图；自动化测试只钉住两个 tmux 边界，SSH 传输与终端本身不在测试里执行）：

```text
远端 tmux 复制 → 远端 tmux 发出 OSC 52 → SSH 承载字节
              → 本地 tmux 解析并重发该序列（set-clipboard on）
              → Ghostty 收到并写入本机剪贴板
```

这条链不经过任何一台机器的图形剪贴板：**远端与路径中间的机器都不需要 X11 / Wayland 会话，也不需要 xclip、wl-copy 等工具**，headless 服务器同样可用。最终写入用的是**本地终端当前平台的剪贴板后端**（Ghostty 自己选择 Wayland/X11/macOS 等后端），所以 Ghostty 模块显式写 `clipboard-write = allow` 与 `clipboard-read = deny`（见该模块 README）。

### 中继保留什么、发给谁（不是字节级转发）

本地 tmux 收到应用/内层 tmux 的 OSC 52 后**不是**把字节原样转发：

- 选择器字段按允许集合 `cpqs01234567` **过滤并去重**，其它字符被丢弃。
- 载荷 base64 会**解码后重新编码**再发出（`tty_set_selection` 用 `b64_ntop` 重新编码）。
- 所以保留的只是“剪贴板类别（选择器语义）+ 解码后的字节”，不是发送端的字面文本或字面编码；空选择器（tmux 自己复制时用的就是空）重发后仍为空。
- 序列只发给**terminfo 带 `Ms`、且其所在 session 当前正显示的 window 就是源 pane 所在 window** 的每个已连接客户端（`screen_write_set_client_cb` 的比较条件）。源 window 在后台（session 当前显示别的 window）或客户端在别的 session 时，不会发给任何终端，但复制仍会写入 tmux buffer——实测另一个 session 的 Ms 客户端收到 0 条、buffer 已更新。（window 大于客户端视口、pane 滚出可视区域的情况没有单独验证。）
- 尺寸边界：tmux 3.4 的输入缓冲上限 `INPUT_BUF_LIMIT` 是 1 MiB，超长 OSC 会被**静默丢弃**（`INPUT_DISCARD`，无提示）；按 base64 的 4/3 膨胀，中继能承载的原文上限约 768 KiB。超过这个尺寸的复制仍会进远端 tmux 的 buffer，但到不了本地剪贴板。

### 为什么是 `on` 而不是 `external`

`set-clipboard` 自 tmux 2.6 起就是三态选项，而默认值一直是 `external`（本机 tmux 3.4 实测 `show-options -g set-clipboard` 即为 `external`），而 `external` 会把**应用/内层 tmux 发来的 OSC 52 直接丢弃**：tmux 3.4 源码 `input.c:input_osc_52` 只在取值 `on` 时处理应用序列（man 的措辞是 “ignore attempts by applications to set tmux buffers”）。而这条链里本地 tmux 正是中间层——远端 tmux 的 OSC 52 走 SSH 落在本地 tmux 的 pane pty 上，会被当作应用输入解析。隔离实测（独立 tmux server + 带 `Ms` 的 PTY 客户端，`tests/run.sh` 里固定下来）：

| 外层 tmux 取值 | tmux 自身复制时发 OSC 52 | 处理并重发应用/内层发来的 OSC 52 | 应用内容写入 tmux buffer |
| --- | --- | --- | --- |
| `on` | 是 | 是（选择器过滤/去重、载荷解码后重新编码） | 是 |
| `external` | 是 | **否，直接丢弃** | 否 |
| `off` | 否 | 否 | 否 |

因此 `external` 只能覆盖“Ghostty → SSH → 远端 tmux”（路径上没有本地 tmux）的直接拓扑；本机日常是“Ghostty → 本地 tmux → SSH → 远端 tmux”，中间层必须 `on` 才会把序列重发给 Ghostty。

### `on` 的代价（明确边界）

- pane 应用（不只是远端 tmux）也能向外层终端发起剪贴板写入，并改写 tmux 自己的粘贴 buffer；此前 tmux 会把这类序列吞掉。写入方向仍受终端策略约束：Ghostty 的 `clipboard-write = allow` 是这条链成立的前提。
- 应用用 OSC 52 查询剪贴板时，tmux 用**自己的 buffer** 回复（源码 `input_osc_52` 的 `?` 分支），读到的是 tmux buffer，不是本机系统剪贴板；Ghostty 的 `clipboard-read = deny` 仍然挡住终端层面的系统剪贴板读取。

### 能力依赖，而不是发行版或显示服务器

机制本身与发行版、内核版本、Wayland/X11 都无关，只依赖：

1. 外层终端支持并允许 OSC 52 写入（Ghostty 模块已显式允许）。
2. tmux 支持 OSC 52 输出（`set-clipboard`，tmux 2.6+）；本基线显式写值，不依赖各版本默认值。
3. 路径上每个 tmux **客户端**要有**有效**的 `Ms` 能力（tmux 3.4 `tty_set_selection` 依赖它决定是否发序列）。来源可以是：
   - 它实际使用的 terminfo，例如 `tmux-256color`（Ubuntu 24.04 的 ncurses 里带 `Ms`）；`screen-256color` 不带，`xterm-ghostty` 带。
   - 显式配置的 `terminal-features` 里的 `clipboard` 特性（tmux 会注入 `Ms=...`）——本模块不写。
   - tmux 的运行时识别：tmux 依据客户端终端的 DA2/XTVERSION 自报类型（tmux、XTerm、iTerm2、mintty 等）套用默认特性，其中含 `clipboard`。

   Ghostty 模块的 `ssh-terminfo` 只在 Ghostty 的 SSH 包装真正生效时把 `xterm-ghostty` 记录装到远端：直接在 Ghostty 集成 shell 里发起 ssh，或显式 `ghostty +ssh`。**本地 tmux pane 里起的嵌套 shell 不会继承那个包装**，所以嵌套路径（Ghostty → 本地 tmux → ssh → 远端 tmux）不该归功于 `ssh-terminfo`；此时远端 tmux 的客户端类型通常是本地 tmux 给 pane 的 `TERM`（即该 server 的 `default-terminal`：tmux 3.4 内置默认 `tmux-256color`，gpakosz 基线写 `screen-256color`），或者靠上面的运行时识别拿到能力。
4. 路径上的终端复用层不吞掉该序列；中间的 tmux 需要 `set-clipboard on` 才会重发。

`install.sh` 自身仍只支持 Ubuntu 24.04+（apt、PPA、desktop 集成都依赖 Ubuntu 机制），但剪贴板机制不要求远端是 Ubuntu，也不要求远端有图形会话。

### 与 `save-buffer | xclip/wl-copy`（gpakosz `<前缀> y`）的区别

gpakosz 配置自带的复制到系统剪贴板是 `<前缀> y`：跑一个后台任务 `save-buffer - | <探测到的工具>`（`xsel` / `xclip` / `wl-copy` / `pbcopy` / `clip.exe` 之一）。那是另一条路径：**管道命令在哪台机器执行，就写哪台机器的剪贴板**。在远端 SSH 会话里按 `y`，写的是远端的剪贴板；远端没有图形会话时 `xclip` 需要 `DISPLAY`、根本写不进去，因此远端装 `xclip`/`wl-clipboard` 对“复制回本地”没有帮助。

copy-mode 表里的绑定只有在可选项 `tmux_conf_copy_to_os_clipboard` 显式开启时才会被改写成 `copy-pipe <工具>`；本基线与上游默认都让它保持 `false`（未开启时上游反而会把已有的 `copy-pipe <命令>` 剥回 `copy-pipe`/`copy-selection`）。本模块仍安装 `xclip`/`wl-clipboard`，因为它们服务的是另一个场景：直接在本地图形会话里跑 tmux、用 `<前缀> y` 写本地剪贴板。两条路径各自独立，`on` 不会覆盖或禁用 `y` 绑定。

## 滚动/拖选后的粘贴

滚轮和普通拖选都会进入 copy-mode。拖选释放已经复制，但仍保留历史位置；
清除选区不等于退出模式。默认 emacs 表把 `Ctrl+V` 用作翻页，vi 表用作矩形
选择，会吞掉物理 `Ctrl+V` 的原始 `0x16`，Pi/ZLE 收不到。本基线仅在
两张 mode table 中让物理 Ctrl+V **先退出，再转交原始字节**：

```tmux
bind -T copy-mode    C-v send-keys -X cancel \; send-keys -H 16 #!important
bind -T copy-mode-vi C-v send-keys -X cancel \; send-keys -H 16 #!important
bind -T copy-mode-vi M-v send-keys -X rectangle-toggle #!important
```

只转交一个字节，不发送 Esc/回车，不读取系统剪贴板或粘贴 tmux buffer；
root 表保持正常转发。滚动与拖选释放仍停留在原位置，直到用户按 Ctrl+V。
Ghostty 的 `Ctrl+Shift+V` 现在固定为原生**文字**粘贴，不再产生 `C-v`。
文字可能被 copy-mode 的键表或快速/括号粘贴路径消费，不能依靠上述绑定
自动转交；原生粘贴前先按 Esc 退出历史模式。不为此改回 raw shifted-V，
也不增加 Pi 进入/退出时的动态切换。vi 的矩形选择改用 **Alt+V**，emacs 原来的翻页仍可用 PageDown。行尾
`#!important` 保证 Oh My Tmux 重建键位后重放这三条绑定。

复制的所有者各不相同：

- **Ghostty**：Shift+拖选产生终端选区，再用 Ctrl+Shift+C；普通拖选被应用
  接管时，Ctrl+Shift+C 不会自动获取应用选区。
- **tmux**：应用未请求鼠标上报时，普通拖选释放通过 OSC 52 自动复制，无需
  再按 Ctrl+Shift+C；复制后仍在历史模式，物理 Ctrl+V 才走上述退出转交。
- **Pi**：Pi 请求鼠标上报时普通拖选交给 Pi，由 Pi 的剪贴板适配器复制。
  转交的 Ctrl+V 继续使用 Pi 原有图片/文本粘贴或 SSH 桥。

本地 Wayland zsh 的物理 Ctrl+V 需要 Ghostty 模块的受管 ZLE hook；本地
输入读 shell/server 的 Wayland 剪贴板，共享 pane 最近活动客户端来自 SSH
时读已有客户端桥（是活动时间推断，不是逐键来源证明）。原生 Ctrl+Shift+V
直接由输入端 Ghostty 读文字，不依赖该 hook。其他程序自行解释 Ctrl+V。
打开的 tmux 命令/搜索 prompt 先于 mode table 消费按键，这三条绑定不处理
prompt 编辑。真实 Ghostty/系统剪贴板新鲜度仍须人工验收。

### 现有机器的窄部署

从仓库根目录只升级 zsh hook，然后开启新 zsh；已有 shell 不会自动重载：

```bash
bash fresh-install/modules/ghostty/install.sh --shell-paste-only
```

如只想更新运行中 server 的这三个键，不用重跑安装器或完整配置重载。
**先确认目标 server 的 socket 路径**，再显式指定它（下列命令是部署说明，
回归测试不会执行到用户 server）：

```bash
socket=/path/to/confirmed/tmux.sock
tmux -S "$socket" bind-key -T copy-mode C-v 'send-keys -X cancel ; send-keys -H 16'
tmux -S "$socket" bind-key -T copy-mode-vi C-v 'send-keys -X cancel ; send-keys -H 16'
tmux -S "$socket" bind-key -T copy-mode-vi M-v send-keys -X rectangle-toggle
```

CLI 中须像上例把两步命令列表整体引用；单独传未转义的分号会在安装绑定时
执行第二条命令，不能保存完整路由。配置文件中则保留上文的 `\;`。部署后
分别在 emacs/vi 模式滚历史、拖选，按物理 Ctrl+V 确认单字节转交 Pi/新 zsh；
原生 Ctrl+Shift+V 先退出历史模式再测文字，多行不自动提交，vi Alt+V 仍切换
矩形选择。此过程不改变全局 Ghostty
快捷键、OSC 52 策略或 Pi 剪贴板实现。

## 幂等语义

重跑本脚本：

- apt 包已装则跳过；
- `~/.tmux` 已是克隆则用 `git pull --ff-only` 更新；更新失败（离线、本地有改动）只警告不中止，保留现有版本；
- `~/.tmux.conf.local` 已是指向模块基线的符号链接则跳过；否则备份后重新链接——重跑即修复；
- Pi 扩展安装器可独立运行（`bash pi-agent/extensions/pi-tmux-window-status/install.sh`），幂等语义与隔离测试环境变量见其 README；
- 替换既有 `~/.tmux.conf` 或非仓库的 `~/.tmux` 目录前同样先备份。

在 `setup.sh` 中本步骤为 tolerate：tmux 本体走 apt 很可靠，但配置仓库要从 GitHub 克隆，全新机器还没配代理时可能失败——只提示不中止，网络就绪后重跑本脚本即可。

## 隔离回归测试

```bash
./tests/run.sh
```

测试使用 $WORK 内的独立 tmux socket（`-S`，不留 `/tmp/tmux-<uid>/` 节点）、私有 HOME 和无图形/SSH 会话变量的临时环境，source 仓库基线后用 `show-options -Apv -t` 精确断言目标 pane 的有效 `allow-passthrough` 值为 `all`；随后真实验证状态栏鼠标释放切换、emacs/vi 两张 copy-mode 键表的字母原样输入，以及两 pane 红色 window 中 root/copy-mode Esc 只有在当前 pane 持有 recovery marker 时才投递 sentinel + Esc。未 armed pane 的 root 只收到普通 Esc，copy-mode 则零注入。不会改动当前 tmux server。

Ctrl+V 部分复用 OSC 52 的 attached PTY 驱动，在 emacs/vi × wheel/drag 四种
情形断言滚动位置保留到粘贴请求、随后退出模式，并且 raw 接收器恰好收到
`16`，没有额外 Esc/Enter 或 tmux buffer 文本；normal mode 原始转发不变。
vi Alt+V 保持模式/滚动位置、切换矩形选择且零应用输入。另核对完整存储的
两步绑定；本机安装了 Oh My Tmux 时，在私有 HOME/server 跑两次完整主配置
重载，确认 `#!important` 重放仍保留三条键，root Ctrl+V 没有被接管。

OSC 52 部分用 `tic` 编译两份只差 `Ms` 能力的 terminfo，让两个 PTY 客户端同时显示同一个 pane 做对照（拖动故意由**不带 Ms** 的客户端发起，证明 tmux 按接收方能力分发）；每个阶段用全新 socket 启动独立 server，客户端进程有界收尾（超时强杀，不无期限 waitpid）：

- tmux 自身复制：emacs/vi 两张键表各拖选一次，带 Ms 的客户端必须收到唯一一条 OSC 52（选择器为空），base64 解码后的字节与预期选中内容逐字节相等（emacs 丢右下角字符与行尾换行、vi 保留，所以两种模式的预期字节按 tmux 语义分别计算）；不带 Ms 的客户端零条；同时断言复制后仍在 copy-mode 且选区已清除（自定义 `MouseDragEnd1Pane` 的语义）。
- 应用/内层 tmux 来源：pane 直接打印 `OSC 52;;<base64>`（选择器字段就取上一条实测到的空值），带 Ms 的客户端必须收到选择器同样为空、解码后字节完全相同的重发序列，不带 Ms 的客户端零条，且内容确实进入外层 tmux 自己的 buffer（`on` 的代价在测试里也被行为覆盖）。

这两段只钉住**两个 tmux 边界**：内层/发送端 tmux 发出的序列形式，以及外层/中继端 tmux 对同形式序列的处理。SSH 传输与 Ghostty 终端本身没有被测试执行到（需要真实主机与真实终端）。全过程只读 PTY 字节，不碰物理剪贴板，也不碰真实 tmux server。

## Pi 扩展自动发现

Pi 只在启动时扫描 `~/.pi/agent/extensions/` 下的目录，不会扫描本仓库——仓库里的 `pi-agent/extensions/pi-tmux-window-status` 必须通过受管符号链接暴露到 `~/.pi/agent/extensions/` 才会被加载。安装/更新扩展后需要**重启 Pi 或执行 `/reload`** 才生效；tmux 还须 `<前缀> r` 重载状态格式与 Esc 键位。

该扩展链接不受 conley 的 pi-agent fork 追踪；扩展安装器将路径写入本机仓库的 `.git/info/exclude`，因此 Git 不拥有该链接，`git pull` 与重跑安装互不干扰。

## 使用要点

- 前缀键仅保留默认 `Ctrl+b`；Oh my tmux! 默认新增的第二前缀 `Ctrl+a` 已取消。
- 复制到本机系统剪贴板走原生 OSC 52：远端 tmux 里拖选/复制后，序列经 SSH 由本地 tmux 解析后重发（选择器会过滤/去重，载荷解码后重新编码），再由 Ghostty 用本机平台的剪贴板后端写入（`set -s set-clipboard on` 让本地 tmux 也会转发应用/内层 tmux 发来的序列）。远端不需要图形会话或剪贴板工具；尺寸上限、后台 window、发给哪些客户端等边界见上文剪贴板一节。
- `allow-passthrough all` 允许 pane 把 tmux DCS passthrough 发给外层 Ghostty，因此 pane 内应用的 Kitty graphics 命令不再被 tmux 挡住。刻意选 `all` 而不是 `on`：`on` 在每个 DCS 派发时刻（tmux 3.4 `input.c` 的 `input_dcs_dispatch`）还要过两道瞬态门——某个 client 当前显示的 window 必须就是这个 window（`screen_write_set_client_cb` 的 `curw->window` 比较），且 pane 没有 `PANE_REDRAW`/`PANE_DROP` 待重绘、client 没有 deferred redraw（命中时回调返回 -1，整条 rawstring 写出被跳过）；一次图片事务由多条独立的 tmux DCS 分段拼成一次写入，命中这些事件循环轮询之间的瞬态时，落在瞬态里的分段会被 tmux 静默丢弃，外层终端拿到不完整的多段上传：占位符已经画在屏幕上，图像数据却到不了（表现为 Ghostty 侧的 missing image for virtual placement 与 kitty_gfx 警告，而不是可恢复的重试）。`all` 把这两道门短路为「该 client 的 session 含有这个 window 且 client 就绪（attached 且未 suspended）」，事务不再因瞬态丢分段；隐藏 pane 的主动图像上传仍然由 pi-inline-images 的 viewer monitor 抑制——没有就绪的兼容观看者就不上传，所以 `all` 并不会把常规图像流量引到隐藏 pane。代价必须明确：`all` 会转发隐藏 pane 的任意 DCS，不只是图像命令；它只写给就绪且属于该 session 的 client，不是给 detached 或未来 client 的离线队列；依赖隐藏 pane 做 DCS 隔离的会话不应套用本基线。
- Pi 的模型或供应商错误把 window 标红并准备自动发送 `continue` 后，animator 会从 live error lease 的 pane ID 派生私有 recovery marker，并在同一个 tmux command batch 中更新 pane marker 与聚合 window 红色；lease 过期、publisher 崩溃、pane 复用及 animator 启动 sweep 都会清除 stale marker。Batch 并非事务，因此 animator 在调用 tmux 前先保留 prior + attempted targets 的清理责任，只有整批成功才缩回 live 集合，尾部 client refresh 失败不会遗留前面已写入的 marker。owning pane 的手动 `Esc` 由 tmux 先投递取消 sentinel、再投递真实 Esc，因此 fullscreen 搜索即使吞掉真实 Esc，也不会漏掉取消意图；扩展立即撤销本 run 的错误与待发送 `continue`，同一 run 的延迟 error 事件不能重新 armed。其他 pane 即使共享红色 window 也没有 marker，不会收到 sentinel。同一 window 若还有其他 Pi root 保持 error lease，聚合标签仍保持红色。
- `<前缀> e` 打开 `.tmux.conf.local`；保存后按 `<前缀> r`，运行中的 server 才会读取新基线。
- 状态栏左侧只显示 session 名，与右侧同为浅灰字、深灰底；右侧移除电池与时间、日期，只留 `用户名@主机名`。未选中 window 空闲时为 `#bcbcbc` 灰白块、深色字；bell 保留黄色前景和 `!` 标记；last/activity 不改变背景。选中 window 在色块左右末端各放两个整格实心 `#0077aa` 蓝色竖条（`██`；fg/bg 同设蓝，字体留缝隙也不漏底色），与 error 红底走正交视觉通道，竖条永不呼吸、永不变色。Pi 根回合或其异步子代理运行时，相应 window 背景以约 1s 周期在灰色路径上呼吸；模型/供应商可用性错误时显示稳定红底白字，后续语义输出或成功结束会清除。扩展语义与自动续跑细节见 `pi-agent/extensions/pi-tmux-window-status/README.md`。
- `<前缀> m` 切换鼠标模式；状态栏 window 标签在鼠标左键释放时切换，因此单击和快速连续点击都会落到释放位置对应的 window。状态栏区域的滚轮不再切换 window；普通 pane 中鼠标滚轮每格滚动 1 行。`<前缀> -` / `<前缀> _` 分屏；`<前缀> h/j/k/l` 在窗格间移动。应用主动开启 mouse reporting 时，滚轮仍交给应用自身处理。鼠标拖选复制后停留在 copy-mode、不跳回 pane 底部；`Esc` 通常只退出 copy-mode，只有当前 pane 同时持有 recovery marker 且 window 聚合状态为红色时，才在退出后投递 sentinel + 真实 Esc；按任意英文字母则退出并把该字母原样输入 pane，大小写保持不变且不会自动回车。Tmux 搜索 prompt 先于这些键表消费按键：vi `/`、`?` 保留原生 submit-on-close 搜索；emacs `C-s`、`C-r` 因 `-i` 在 prompt 打开时就释放 queue，明确改为保留方向、初值和文案的 submit-on-close 搜索，以获得可靠 close edge。Tmux 3.4 无法区分 Esc 取消与 Enter 接受，所以 armed 状态下两种关闭都会取消自动恢复；未 armed 时 pane 零注入。
- `Ctrl+Alt+←/→` **不需要前缀**，直接切换上一个/下一个 window（底部状态栏的标签）。绑定落在 root 表：`C-M-Left=previous-window`、`C-M-Right=next-window`。Ghostty 模块显式 unbind 这两个键，确保按键进入 pty；gpakosz 检测到 `TERM_PROGRAM=ghostty` 后自动开启 extended-keys，tmux 才能识别组合键。
- `Ctrl+Alt+=` / `Ctrl+Alt++` **不需要前缀**，提示输入名称后在当前 pane 的目录新建 window；直接回车则让 tmux 按运行程序自动命名。Ghostty 模块为两者显式发送 CSI-u 序列，tmux 分别绑定 `C-M-=` / `C-M-+`，避免符号键修饰信息在终端编码中丢失。
- 内置 TPM 插件支持：在 `.tmux.conf.local` 里写 `set -g @plugin ...`，`<前缀> I` 安装，`<前缀> u` 更新，`<前缀> M-u` 卸载。
- 完整键位与状态栏变量见上游模板 `~/.tmux/.tmux.conf.local` 和上游 README。
