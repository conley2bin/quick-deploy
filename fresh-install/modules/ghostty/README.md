# Ghostty 安装模块

本模块为 Ubuntu 24.04+ 的 `amd64` / `arm64` 主机安装并配置 Ghostty：

```bash
./install.sh --check
./install.sh
```

默认先使用 `ppa:mkasberg/ghostty-ubuntu`；如果新机器尚未配置代理、无法访问 Launchpad，脚本会退回同一维护者 GitHub Release 中严格匹配当前 Ubuntu 版本与架构的 `.deb`。

可显式固定路径：

```bash
./install.sh --ppa-only
./install.sh --deb-only
```

## 软件来源与信任边界

Ghostty 项目**没有发布官方 Linux 二进制包**。本模块安装的是 Mike Kasberg 维护的**第三方社区包**，维护者不是 Ghostty 项目。Ghostty 上游为源码压缩包提供的 minisig 签名不覆盖这里安装的 PPA 包或 `.deb`。

两条安装路径的验证强度不同：

1. **PPA（优先）**：Launchpad 提供 PGP 签名的 `InRelease`，apt 会验证软件源的签名链；脚本还核对 PPA 公钥的完整指纹 `0721FDF5FECB88DC6920361657C8EF455CEAE491`。
2. **GitHub `.deb`（回退）**：脚本从 GitHub API 读取资产的 `sha256:<64hex>` digest，下载后先核对 SHA-256，再用 `apt-get install ./file.deb` 安装。资产与 digest 来自同一个 GitHub Release 通道，因此这个校验能发现传输损坏或文件错配，**不是独立的发布者签名或独立信任根**。

回退路径不会猜测相近版本。若当前主机的 `架构 + VERSION_ID` 没有唯一对应的资产，脚本会列出该发布实际提供的变体并明确失败。

## 两个渠道的版本号不同序

同一个上游 Ghostty 1.3.1，两个渠道的版本字符串不一样：

| 渠道 | 版本号 |
| --- | --- |
| GitHub `.deb` | `1.3.1-0~ppa2` |
| PPA | `1.3.1~ppa2-noble1` |

按 Debian 版本排序规则，`~` 排在一切之前，因此
`1.3.1~ppa2-noble1` 的上游部分小于 `1.3.1`，
**从 GitHub `.deb` 装的版本反而“更新”**：

```console
$ dpkg --compare-versions '1.3.1-0~ppa2' gt '1.3.1~ppa2-noble1' && echo greater
greater
```

所以一旦走过回退路径，apt 不会把它“降级”到 PPA 版。两者是同一个上游
版本，功能一致；等打包者发布更高的上游版本（如 1.3.2）时，PPA 会正常
接管后续 `apt upgrade`。若希望立即改由 PPA 管理，需手动指定版本安装：

```bash
sudo apt install --allow-downgrades ghostty=1.3.1~ppa2-noble1
```

## 脚本写入的内容

正常安装会由 apt 写入第三方 PPA 信息（PPA 路径）、系统级 Ghostty 包及其依赖。软件包本身提供：

- `/usr/bin/ghostty`
- `/usr/share/applications/com.mitchellh.ghostty.desktop`
- `/usr/share/terminfo/.../xterm-ghostty`
- Ghostty 自带主题资源，包括精确命名的 `Catppuccin Frappe`

模块写入用户配置：

- `~/.config/ghostty/config.ghostty`
  - 主字体 `JetBrains Mono`
  - 等宽 CJK 回退字体 `Noto Sans Mono CJK SC`
  - 字号 `12`，主题 `Catppuccin Frappe`
  - `F11` 全屏切换
  - 显式解除 `Ctrl+Alt+←/→` 的 Ghostty split 绑定，把按键交给 tmux 切 window
  - 为 `Ctrl+Alt+=/+` 显式发送 CSI-u 序列，交给 tmux 新建 window
  - `Ctrl+Shift+R` 绑定 Ghostty 原生 `reset`，用于清理异常 SSH 后残留的终端状态
  - `mouse-shift-capture = never`：鼠标上报的程序（Pi 全屏、tmux `mouse on`）里用 **Shift+拖选** 产生 Ghostty 本地选区，并禁止程序用 `XTSHIFTESCAPE` 抢走 Shift（见“鼠标上报下的复制”一节）
  - `Ctrl+Shift+C` 保留 `copy_to_clipboard:mixed`；全局 `Ctrl+Shift+V` 改为 `text:\x16`，向终端内发送原始 Ctrl+V，普通 Pi 会使用自身图片/文本粘贴动作（见下节）。`Ctrl+C/V` 不变
  - `clipboard-write = allow` / `clipboard-read = deny`：允许 OSC 52 写入（远端 tmux 复制回本机剪贴板的入口），拒绝 OSC 52 读取（见下节）
  - 新终端起始目录 `~/Documents`（不存在时回退到 XDG 文档目录，再不行就 `home`）
  - 关闭新窗口工作目录继承；新标签页和分屏仍保留继承
  - `gtk-single-instance = false`：每个外部启动的 Ghostty 用自己的进程（见下节）
- `~/.local/lib/ghostty/libwayland-backpressure.so` 与 `~/.config/ghostty/wayland-backpressure/enabled.library`
  - 从模块内 C 源码编译的用户级库及逐字节校验副本；只在新启动的原生 Wayland Ghostty 生效
- `~/.local/bin/ghostty`
  - 唯一受保护的用户级启动器；图形菜单、Ctrl+Alt+T 和交互式 PATH 都指向它
- `~/.local/bin/ghostty-pi`
  - 只供主动打开 Pi 专用窗口的入口；转交给上述受保护启动器，并仅在新进程内把 `Ctrl+Shift+V` 转为原始 `Ctrl+V` 输入（见下节）
- `~/.local/share/applications/com.mitchellh.ghostty.desktop`
  - 由系统模板 `/usr/share/applications/com.mitchellh.ghostty.desktop` 逐行生成，把两个 `Exec=` 改为
    用户级启动器与 `--gtk-single-instance=false`，并写 `DBusActivatable=false`；系统文件本身不改
  - 同目录的 `mimeinfo.cache` 由 `update-desktop-database` 刷新（查找缓存，失败不影响入口生效）
- `~/.config/ghostty/ghostty-ssh-mouse-reset.zsh`
  - zsh 在 SSH 返回本地提示符前自动清理常见鼠标模式
- `~/.config/ghostty/ghostty-local-paste.zsh`
  - 本地 Wayland 交互式 zsh 提示符（含本机 tmux pane）的 ZLE 粘贴 widget；由 `install.sh` 内嵌源码生成
- `~/.zshrc`
  - 一个标记包围的加载块 source 上述两个 hook；现有旧块按精确基准迁移，块外用户内容逐字节保留

### 为什么同时写 `working-directory` 和 `window-inherit-working-directory`

`working-directory` 只决定“没有可继承窗口时”的默认目录。Ghostty 默认开启
`window-inherit-working-directory = true`，而且它的优先级更高：同一进程内新建窗口
会继承该进程里焦点窗口报告的工作目录。实际表现是：只要焦点窗口位于某个项目
目录，该进程里以后新开的窗口都会黏在该项目目录，即使
`working-directory = ~/Documents` 已正确生效。

模块因此同时写入：

```ini
working-directory = ~/Documents
window-inherit-working-directory = false
```

这让每个新窗口固定从 `~/Documents` 启动。新标签页和分屏仍保留 Ghostty 的默认
继承行为，方便在当前项目中继续工作。改默认目录只需改脚本顶部的
`WORKING_DIRECTORY` 变量（可写绝对路径、`~/` 开头的路径，或 `home` / `inherit`），
然后重跑脚本。

### Ctrl+Alt+←/→ 的所有权：交给 tmux window

Ghostty 默认把这两个键绑给 `goto_split:left/right`。本机不用 Ghostty split，
真正需求是进入 tmux 后切换底部状态栏的 window，因此模块显式写：

```ini
keybind = ctrl+alt+arrow_left=unbind
keybind = ctrl+alt+arrow_right=unbind
```

`unbind` 是 Ghostty 的官方语法：移除前一个同触发器的默认动作，让按键正常编码
进入 pty。随后 `fresh-install/modules/tmux/tmux.conf.local` 在 tmux root 表绑定
`C-M-Left/Right` 到 `previous-window` / `next-window`。

这不是改绑 Ghostty 标签页；Ghostty 标签页继续使用上游默认的 `Ctrl+Tab` /
`Ctrl+Shift+Tab`。代价是 Ghostty 自己的 split 不再能用这两个键切换。

### OSC 52 剪贴板：只写不读

配置显式写两行：

```ini
clipboard-write = allow
clipboard-read = deny
```

- **写允许**：终端里的程序（包括 SSH 远端 tmux 发来的、由本地 tmux 重发的序列）可以发 OSC 52 把内容写进本机系统剪贴板。这是 `fresh-install/modules/tmux` 那条“远端 tmux 复制 → 本机剪贴板”路径的终点：序列经 SSH 与本地 tmux 到达 Ghostty，再由 Ghostty 用**它当前平台的剪贴板后端**（Wayland/X11/macOS 等由 Ghostty 自己选择）落下。因此远端与路径中间的机器都不需要 Wayland/X11 会话或剪贴板工具。
- **读拒绝**：OSC 52 读取会让终端里的程序向 Ghostty 查询本机剪贴板。Ghostty 默认值是 `ask`（弹窗询问），本模块改为无条件拒绝这种终端协议查询；本机程序自己调用 `wl-paste` 等系统接口不受该设置约束，Pi 的 Ctrl+V 正走自己的读取路径。
- **本地粘贴与 Pi 粘贴**：鼠标右键、`Shift+Insert` 仍可触发 Ghostty 的本地粘贴；`Ctrl+Shift+V` 发送原始 Ctrl+V，由 Pi 自己读取图片或文本，不经过 OSC 52 查询。`Ctrl+Shift+C` 复制仍走 Ghostty 本地选区。本地 Wayland 交互式 zsh 提示符通过下述 ZLE widget 消费原始 Ctrl+V 并读取当前剪贴板。
- **Ghostty 配置渲染**与显示服务器无关：回归测试在 Wayland、X11、无会话变量下断言这两条 OSC 52 策略及全局按键渲染逐字节相同。本地 zsh 粘贴 hook 则只在 Wayland 会话启用；配置渲染测试不能替代真实图形会话验收。
- 本模块不写 `terminal-features` 之类的终端能力声明，也不强制声明 `Ms`；远端 tmux 能不能发 OSC 52 取决于它客户端侧的有效 `Ms`（可能来自 terminfo、配置的特性或 tmux 的运行时识别，见 `fresh-install/modules/tmux/README.md` 剪贴板一节）。本模块能做的是保证本机 `xterm-ghostty` 记录确实带 `Ms`：`--check` 会报告它，正式安装的验证阶段缺 `Ms` 会明确失败。

### 鼠标上报下的复制与 Pi 粘贴

在 Pi 全屏或 tmux `mouse on` 里，普通拖选由申请鼠标上报的程序接管（Pi
自己画选区并在释放时复制，tmux 进入 copy-mode）。此时 Ghostty 没有本地
选区，`Ctrl+Shift+C` 无内容可复制。按住 Shift 拖选可创建 Ghostty 本地
选区，再用 `Ctrl+Shift+C` 写入本机剪贴板。配置固定为：

```ini
mouse-shift-capture = never
keybind = ctrl+shift+c=copy_to_clipboard:mixed
keybind = ctrl+shift+v=text:\x16
```

`never` 阻止程序通过 `XTSHIFTESCAPE` 抢走 Shift+鼠标；普通拖选仍属于申请
鼠标上报的程序。`Ctrl+Shift+C` 的 `mixed` 同时提供纯文本和带样式格式。
`Ctrl+C` 和 `Ctrl+V` 原有行为保持不变。

**所有普通 Ghostty 窗口**的 `Ctrl+Shift+V` 现在向 pty 发送原始 0x16，
等同按下 `Ctrl+V`。本地 Pi 的现有 `app.clipboard.pasteImage` 动作先尝试
图片再回退文本，读取当前剪贴板，避开 Ghostty/GTK 可能陈旧的文本粘贴
视图；已有 `ghostty-pi` 以同一覆盖参数启动，保持兼容但不再是必需入口。
SSH 中的 Pi 若启用了已安装的剪贴板桥，则继续由该桥处理 Ctrl+V；这项
Ghostty 配置不改变远端 Pi 本身的剪贴板能力。

本地 Wayland **交互式 zsh 提示符（含本机 tmux pane）**加载模块托管的
ZLE widget：`Ctrl+V` 和 `Ctrl+Shift+V` 都以原始 `^V` 到达 zsh，然后每次由 `wl-paste
--no-newline --type text` 读取**当前**系统 CLIPBOARD，用 2 秒上限将完整
文本直接插入 ZLE 编辑缓冲区。真实结尾换行会保留；粘贴中的换行、分号、
命令替换等不会在粘贴时执行，只有用户之后主动提交编辑缓冲区才可能运行
其中命令。读取失败或超时会在提示符报错，保持原缓冲区，不用 tmux 历史
缓冲区或过期结果回退。原有 quoted-insert 搬到 **Esc 再按 Ctrl+V**
（`^[^V`）；载入时先核对该序列未被占用以及 `^V` 是 zsh 默认
`quoted-insert` / `vi-quoted-insert`，冲突会报错而不抢用户自有按键。

hook 只在**具备原生 Ghostty 子 shell 标记**、有本地 Wayland socket 的
交互式 zsh 中绑定 ZLE 键。终端上下文仅接受 `TERM_PROGRAM=ghostty`，或
`TERM_PROGRAM=tmux` **且 `TMUX` 非空**；两者都须有可用的
`GHOSTTY_RESOURCES_DIR` 目录、Wayland socket，且无 `SSH_TTY`。tmux 会将
子 shell 的 `TERM_PROGRAM` 改成 `tmux`，所以仅检查 `ghostty` 的已部署 v2
会跳过真正的本机 tmux 提示符，让 `^V` 留在 quoted-insert；本次只补上该
tmux 子 shell 条件。Ghostty 若从先前的 Pi/tmux 进程启动，可能继承过期的
`SSH_CONNECTION`/`SSH_CLIENT`；这两个变量不作为远端判据，否则真实本地
提示符会被错误跳过。SSH 分配了 PTY 的远端 zsh 被 `SSH_TTY` 守卫拦住。

**剪贴板属于 zsh 所在的主机/Wayland 会话**：在 tmux 中，`wl-paste` 读取
shell/server 主机的剪贴板，不是发送此按键的远端客户端剪贴板。另一台机器
附着同一 pane 不会改变已运行 shell 的环境；这些标记不能判定共享 pane 中
每次输入的实际来源，hook 不做按客户端路由或“最近活动客户端”推断。
粘贴时还须已退出 tmux copy-mode，让原始 `^V` 到达 ZLE；本次不改变
copy-mode、复制绑定或 tmux 鼠标行为。

Pi 等 raw-mode 程序不经过 ZLE，仍由自己的 Ctrl+V 处理器读图片/文本，
SSH 剪贴板桥和 OSC 52 写入路径不变。其他 TUI 仍自行解释原始 `^V`；
非 Wayland、SSH PTY zsh、其他 shell 和不满足上述 Ghostty 子 shell 条件的
终端不安装该 ZLE 按键，可继续用右键或 `Shift+Insert` 的 Ghostty 原生入口。

### 现有安装只迁移这一个按键

从仓库根目录运行：

```bash
bash fresh-install/modules/ghostty/install.sh --paste-keybind-only
```

窄模式只接受已存在、非符号链接、**逐字节等于旧版受管基准**的
`~/.config/ghostty/config.ghostty`，将其原子替换为仅按键行不同的新基准；
已是新基准则不重写。若文件有自定义内容，或旧安装以来字体/起始目录
环境已改变使基准不再吻合，就明确拒绝，保留原文件供人工处理。
不会调用 apt、重建 Wayland 背压库、更新字体/桌面入口/默认终端或启动 GUI，
也不会修改已安装的 `ghostty-pi` 或普通启动器。不要为这次迁移运行无参数的
完整 `install.sh`，因为完整模式会重置整份配置并执行以上其它步骤。

**当前运行中的 Ghostty 进程**需要正常重载 Ghostty 配置（Ghostty 1.3.1
默认 `Ctrl+Shift+,`，动作为 `reload_config`），或从受管入口开启新进程，
才能获得新绑定；仅在旧窗口里新建 Pi pane 不构成进程级重载。重新进入
Pi 后可先后按 `Ctrl+V` 与 `Ctrl+Shift+V` 验证相同当前
文本，再试图片。专用 `ghostty-pi` 仍可运行，仍在新进程用同一
`--keybind=ctrl+shift+v=text:\x16` 参数覆盖这条键。

### 现有机器只补装 zsh 提示符粘贴

全局键已迁移后，从仓库根目录运行：

```bash
bash fresh-install/modules/ghostty/install.sh --shell-paste-only
```

该窄模式先逐字节核对当前受管 Ghostty 配置（必须仍是
`ctrl+shift+v=text:\x16`）、SSH 鼠标 hook 和已有 `.zshrc` 标记块。粘贴
hook 仅接受不存在、**已部署 v1/v2 的精确字节**、或本次基准；历史内容
由安装器和测试中固定的 SHA-256 核对，不能因改动渲染器而重新定义旧版本。
自定义配置、hook 内用户改动、块内用户改动、符号链接、缺失的前置文件会
**在写入前拒绝**。首次安装新增 `~/.config/ghostty/ghostty-local-paste.zsh`，
并在现有 `.zshrc` 托管块中增加一条 source（更改前备份
`.zshrc.bak.<时间戳>`）。已装 v1/v2 的机器只将 hook 原子替换为本次基准，
并留存 `ghostty-local-paste.zsh.bak.<时间戳>`；已有新 `.zshrc` 托管块的
整个文件原字节/原 inode 保持不变。重复运行不重写同样内容；块外 `.zshrc`
内容和文件权限保持原样。
不会执行 apt、重建背压库、
更改全局 Ghostty 绑定/启动器/桌面入口/字体、启动 GUI，也不读取或改动
真实剪贴板。升级后开启新的本地 zsh 提示符，也在本机 tmux 普通 pane
中开启新 zsh（已有 shell 不会自动重载 hook）。复制一段文本，分别试两种
粘贴键；再试含换行的文本，确认它进入编辑缓冲区但未自动执行。
`Esc Ctrl+V` 应能逐字插入下一个控制字符。

无参数的完整 `install.sh` 也会在安装 Ghostty 时渲染这份 hook，写入同一
托管块；模块中依然只有 `install.sh` 一个 shell 源文件。

若只在尚未安装专用入口的机器补装 `ghostty-pi`，仍可运行
`install.sh --pi-launcher-only`；它只核对受保护启动器及背压库并安装该入口，
**不会**迁移全局配置。三种窄模式不可组合。保留的专用入口仍可直接启动，
也可附着原有 Pi 会话：

```bash
~/.local/bin/ghostty-pi
~/.local/bin/ghostty-pi -e tmux attach-session -t quick-deploy_pi-agent
```

第二条 `-e` 后的参数原样交给 Ghostty。若同一 tmux session 有其他客户端，
切换窗口会影响它们；可使用 tmux grouped session 独立选窗，避免
`tmux attach -d` 断开原客户端。

## 字体

两个字体缺失时由脚本用 apt 补齐（`fonts-jetbrains-mono`、`fonts-noto-cjk`），
而不是静默降级——写一个系统里不存在的字体名，Ghostty 会回落到 fontconfig
的选择，用户看到的中文并不是配置声明的那个。脚本本来就为 apt 调 sudo，
顺带装字体不引入新的权限要求。

字体安装失败不会终止整个安装：那只是观感降级，不值得让已经可用的终端
装不上。此时脚本会警告并在配置中省略对应的 `font-family` 行。
安装后不信任 apt 的退出码，而是回读 fontconfig 确认字族名真的可用。

## 中文 locale 下的等宽字体劫持（重要）

在 `LANG=zh_CN.UTF-8` 的机器上，**配置里写的等宽字体会被静默忽略**，
终端实际渲染的是 `DejaVu Sans Mono`。机制：

```
Ghostty src/font/discovery.zig 无条件给字体查询加 FC_SPACING=FC_MONO
  → 命中 /etc/fonts/conf.d/69-language-selector-zh-cn.conf 的等宽规则
  → 该规则用 binding="strong" 把 DejaVu Sans Mono prepend 到最前
  → 应用显式请求的字体被挤到后面
```

该文件来自 Ubuntu 的 `language-selector-common` 包，影响中文环境下
**所有**等宽字体（Liberation、Nimbus、Noto Mono、Ubuntu Mono 均实测中招），
不是 Ghostty 的 bug，也不是某个字体特有。

实测证据（同一份配置，用 `lsof` 看真实加载的字体文件）：

| locale | 实际加载 |
| --- | --- |
| `en_US.UTF-8` | `JetBrainsMono-Regular.ttf` |
| `zh_CN.UTF-8` | `DejaVuSansMono.ttf` |

脚本的处理：检测到劫持时，写入
`~/.config/fontconfig/conf.d/89-ghostty-zh-mono.conf`（文件名 89 > 69，
必须排在 language-selector 之后才能盖过它）。规则内容由脚本的
`FONT_FAMILY` / `CJK_FONT_FAMILY` 变量生成，不在两处重复维护字体名。

两个刻意的设计选择：

- **门控用实测症状，不是判断 locale 名**：只有 `fc-match` 确实返回了别的
  字体才写规则。非中文机器不会被写入无用文件；将来 Ubuntu 修了那条规则，
  本脚本也会自动不再干预。
- **规则保守**：只在查询**已点名**该字体时才把它提前，而不是对所有
  zh + 等宽查询无差别 prepend。实测：写入后 `monospace` 与 `Liberation Mono`
  仍返回系统默认，其它程序不受影响。

不采用用 `LANG=en_US.UTF-8` 启动 Ghostty 的做法：那会把终端里所有程序的
locale 一并换成英文（日期、报错、man 页）。病灶在 fontconfig 层，就在
那一层修。

卸载只需删掉那个 `.conf` 文件。

配置采用“幂等重置”语义：重跑会把它恢复为模块的基准内容。内容变化时，脚本先备份为 `config.ghostty.bak.<时间戳>`，再用同目录临时文件和原子 `mv` 替换；写完会回读，并通过 Ghostty 自身解析配置。

本模块不会另写主题文件，也不改写软件包提供的系统 desktop 文件：关闭单实例只通过
用户级同 ID 覆盖完成，`/usr/share/applications/com.mitchellh.ghostty.desktop` 保持原样。
也不注入 `GDK_BACKEND`，在 Wayland 会话中继续使用原生 Wayland。

### 每个外部启动一个进程（`gtk-single-instance = false`）

本模块把“每个外部启动的 Ghostty 都用自己的进程”作为启动策略，同时在两处落地：

```ini
# ~/.config/ghostty/config.ghostty
gtk-single-instance = false
```

```diff
# ~/.local/share/applications/com.mitchellh.ghostty.desktop（从系统模板生成）
-Exec=/usr/bin/ghostty --gtk-single-instance=true
+Exec=/home/<用户名>/.local/bin/ghostty --gtk-single-instance=false
-DBusActivatable=true
+DBusActivatable=false
```

两处都必须写，因为它们各自覆盖不同的启动路径：

| 启动路径 | 谁决定单实例 | 模块的处理 |
| --- | --- | --- |
| 应用菜单 / 桌面快捷方式 | 系统 desktop 的 `Exec=` 显式带 `--gtk-single-instance=true`；CLI 参数优先于配置文件 | 用户级覆盖把两个 `Exec=` 改为受保护的启动器并传 `--gtk-single-instance=false` |
| Ctrl+Alt+T（包装脚本转到 `~/.local/bin/ghostty`） | 没有 CLI 参数，命令行 `--gtk-single-instance` 的默认值 `detect` 只在配置值仍为 `detect` 时才参与判断 | 配置里的 `false` 直接生效，不经过 `detect`，也就不靠 `TERM_PROGRAM` 这类环境事实 |

`DBusActivatable=false` 不是可选项：它为 `true` 时桌面外壳可以走 D-Bus 激活（系统里
装着 `com.mitchellh.ghostty.service`），那条路径根本不经过 `Exec=`，光改 `Exec=` 里的
参数会被绕过。实测：配置写 `gtk-single-instance = true` 时，即使带 CLI 参数（`detect`
本该判为单实例），`ghostty +show-config` 的解析结果仍是 `true`；写成 `false` 则固定为
独立进程。

隔离范围：**一个外部启动 = 一个进程 = 一个崩溃域**。进程级退出（崩溃、被杀，或该
进程正常退出）只带走它自己那个窗口，其它窗口继续运行；同一进程内的多个窗口、
标签页和分屏仍然共享进程生命周期，不承诺彼此隔离。副作用：窗口之间不再共享工作
目录，每个新窗口按 `working-directory` 启动（见下节）。

确认用户级覆盖确实遮蔽了系统同 ID 入口：

```bash
desktop_id=com.mitchellh.ghostty.desktop
{
  printf '%s\n' "${XDG_DATA_HOME:-$HOME/.local/share}"
  printf '%s\n' "${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" | tr : '\n'
} | while IFS= read -r data_dir; do
  candidate="$data_dir/applications/$desktop_id"
  if [ -f "$candidate" ]; then
    printf 'XDG 首个匹配: %s\n' "$candidate"
    grep -E '^(Exec|DBusActivatable)=' "$candidate"
    break
  fi
done
```

预期首个匹配是 `~/.local/share/applications/com.mitchellh.ghostty.desktop`，两个 `Exec=` 都
指向 `~/.local/bin/ghostty` 且含 `--gtk-single-instance=false`，并有 `DBusActivatable=false`。边界：模块固定写
`$HOME/.local/share/applications`（与它写 `~/.config/ghostty` 同一约定），不跟随
`XDG_DATA_HOME` / `XDG_CONFIG_HOME`；机器上若自定义了 `XDG_DATA_HOME`，需要自己把覆盖
放到该目录，否则系统入口不会被遮蔽。

这个用户级覆盖只接受可识别的托管状态，其余一律**拒绝覆盖并明确失败**，不静默降级：

1. 文件不存在：按系统模板生成；
2. 与新基准内容一致：重跑幂等，不重写、不备份；
3. 与旧 `install.sh` 的 `/usr/bin/ghostty --gtk-single-instance=false` 基准、此前独立修复脚本生成的新基准，或与厂商模板仅有旧独立进程差异的副本一致：保留备份，迁移到统一启动器。

带用户改动的文件（换了 `Icon=`、加了自有字段、写了别的 `Exec=` 等）会被拒绝，安装脚本会
打印差异命令和恢复方式（把该文件 `mv` 移开再重跑）。系统模板结构变化导致无法生成时
同样明确失败：宁可报错，也不写出一个“看起来成功、实际仍复用进程”的覆盖。

`install.sh` 里还有一处 `--gtk-single-instance=false` 属于 GUI 冒烟测试：让那一次探针进程
独立于用户已在运行的 Ghostty。没有它，单实例模式下新进程会把请求交给已有实例后立即
退出，存活判定随即误报“提前退出”。模块的日常策略现在同为独立进程，但那里的显式参数
仍然保留：冒烟测试要能不依赖配置文件是否正确而自己保证探针独立。

## 默认接管 Ctrl+Alt+T

默认运行**会接管 Ctrl+Alt+T**。写入：

- `~/.local/bin/x-terminal-emulator`：执行 `"$HOME/.local/bin/ghostty" "$@"`，供 GNOME 的 `gsd-media-keys` 通过 PATH 启动；
- `~/.config/xdg-terminals.list`：内容为 `com.mitchellh.ghostty.desktop`，供遵守 `xdg-terminal-exec` 的程序使用。

不想接管时显式关闭：

```bash
./install.sh --no-default-terminal
```

若这些文件当前属于其它终端，脚本先保存 `.bak.<时间戳>` 再替换，不会删除它们。本模块也不会调用 `update-alternatives` 修改系统级默认终端。

### 与 X11 / Wayland 无关

接管机制是“GSettings 值 + 进程启动”，不依赖 X11：`gsd-media-keys` 读
`org.gnome.desktop.default-applications.terminal`（值为 `x-terminal-emulator`），
然后按 PATH 启动。名字里的 “x-” 是 Debian alternatives 的历史命名（X terminal
emulator），不是对 X11 API 的依赖。切到 Wayland 后，只有“按键怎么被抓到”
变了（X11 走 XGrabKey，Wayland 由 Mutter 内部路由），启动终端那一步两者完全
相同，本模块写的包装脚本两边都生效。

> fcitx5 中文输入则是另一回事：上游 issue #12679 报告 Ghostty 在
> **GNOME Wayland** 下 fcitx5 候选框错位、中文不上屏。本模块不设置
> `GDK_BACKEND`，在 Wayland 会话中继续使用原生 Wayland；若真的遇到该症状，再临时用
> `GDK_BACKEND=x11 ghostty` 强制走 XWayland 验证，那是排障手段，不是本模块
> 写入的配置。

## 原生 Wayland：GTK 发送背压修复随唯一安装脚本交付

在 Ubuntu 24.04 的 GTK 4.14.5 中，`gdk/wayland/gdkeventsource.c` 把
`wl_display_flush()` 的任何负返回都记录为 `Error flushing display`，随后
`_exit(1)`。其中 `EAGAIN`（日志常显示“资源暂时不可用”）是非阻塞 Wayland
套接字暂时写不进去，按 libwayland 的约定应等 fd 可写后重试。tmux 附着时的
画面重绘可能让背压显现，但现有事故日志无法区分文字、图片与 compositor
处理速度各自的贡献。[GTK 4.14.5 源码](https://github.com/GNOME/gtk/blob/4.14.5/gdk/wayland/gdkeventsource.c#L83-L89)、
[libwayland 1.22 源码](https://github.com/wayland-mirror/wayland/blob/1.22.0/src/connection.c#L289-L317)。

**只运行本模块的 `install.sh`**；`fresh-install/setup.sh` 已按固定顺序调用它，
无需第二次手工启用：

```bash
bash fresh-install/setup.sh                 # 一键安装时自动包含修复
# 或只重跑此模块：
bash fresh-install/modules/ghostty/install.sh
bash fresh-install/modules/ghostty/install.sh --check  # 只读状态
```

`install.sh` 若发现缺少编译器，会通过 apt 安装 `build-essential`；编译失败
会显式中止 Ghostty 模块，不生成看似成功却没有保护的桌面入口。它将 C 源码
编译到用户级 `~/.local/lib/ghostty/`，保留逐字节校验副本；两个桌面 Exec、
Ctrl+Alt+T 和交互式 `ghostty` 命令统一由用户级启动器进入。
启动器仅在 `WAYLAND_DISPLAY` 存在时加载该库，**不设置 `GDK_BACKEND`**，
不修改系统 GTK/Ghostty 二进制，也不影响此前已运行的进程。

库在 libwayland 实际发送的 `sendmsg` 边界，只对已登记的 Wayland 套接字
的 `EAGAIN` 等待可写并重试，最多 10 秒；其他 socket、`EPIPE` 等错误
按原样传递。只在 GTK 的 `wl_display_flush` 外层重试不够：libwayland
可能已在填满其内部 4096 字节环时锁存错误。私有 socketpair 测试覆盖了
这一分支的阴性对照、延迟 300ms 后恢复的 600 条有序请求、SCM_RIGHTS
fd 完整性、持续阻塞超时和子进程晚加载 Wayland。真实 GUI 冒烟验证
GTK/libwayland 符号进入库；事故原始负载没有复现，不能许诺消除所有退出。

库缺失或与校验副本不符时启动器会明确拒绝启动，不静默回退；持续超过
10 秒的堵塞或实际连接断开仍会失败。`LD_PRELOAD` 由终端子程序继承，
拦截只针对每个进程自己登记的 Wayland fd。显式执行系统路径
`/usr/bin/ghostty` 会绕过受保护的用户入口。

历史迁移：此前若运行了短暂提供的独立 `wayland-backpressure.sh`，直接
重跑 **本** `install.sh` 就会识别原有用户 desktop 和启动器并原位接管，
保留原文件备份；无须先禁用或清理。今后重装、升级和配置重置均只调用
`install.sh`。

## `--check` 是只读预检

它报告完整安装流程的 apt/配置等计划，不代表 `--pi-launcher-only`、
`--paste-keybind-only` 或 `--shell-paste-only` 会执行这些步骤；窄模式与
其它选项不可组合。

```bash
./install.sh --check
./install.sh --check --deb-only --default-terminal
```

离线回归测试（不安装软件、不联网、不改真实 HOME）：

```bash
bash ../../tests/ghostty/run.sh
```

粘贴测试使用私有 HOME、假的 `wl-paste` 和私有 Wayland socket；真实 tmux
测试另用独占 `-S` socket，从 Ghostty 环境启动 pane，核对 tmux 实际设置的
`TERM_PROGRAM=tmux` 和 ZLE 缓冲区精确文本。覆盖当前剪贴板重新读取、光标
插入、多行/多个结尾换行、不自动执行、quoted-insert、冲突与读取失败；
已部署 v2、`SSH_TTY` 和未知终端的 PTY 对照保持 quoted-insert 且零剪贴板
读取。测试还验证 v1/v2 窄迁移与已有新 `.zshrc` 块的字节/inode 保留。
这些隔离测试不证明真实 Ghostty 图形按键、物理剪贴板或复制流程已验收。

预检报告：系统与架构、Ghostty 版本、apt 候选版本、PPA 是否已在源中、配置状态、C 编译器与 Wayland 背压库/启动器状态、Pi 专用启动器状态（缺失 / 基准 / 将拒绝覆盖）、用户级 desktop 覆盖状态（已是基准内容 / 将被替换 / 将被拒绝）、两种字体、`xterm-ghostty` terminfo（含 OSC 52 所需的 `Ms` 能力是否在场）、SSH 鼠标自愈 hook、当前默认终端所有者以及计划动作。它不会添加软件源、调用修改状态的 apt 命令、访问 GitHub、下载文件或写入用户目录。

## 安装后的用户验收清单

1. 运行 `ghostty --version`，确认版本可读。
2. 从 GNOME 应用菜单启动 Ghostty，确认图标入口能打开窗口。
3. 确认主题为 Catppuccin Frappe，字号为 12；按 `F11` 能切换全屏。新窗口应落在 `~/Documents`。
4. SSH 异常断开后点击若出现 `0;xx;xxM` / `0;xx;xxm` 乱码，按 `Ctrl+Shift+R` 确认 Ghostty 能复位终端；正常通过 zsh 执行的 SSH 会在返回提示符前自动清理。
5. 在 Ghostty 中用 fcitx5 输入一段中文。安装脚本的 GUI 冒烟测试只证明 GTK4 的 `libim-fcitx5.so` 已载入进程；最终文本提交仍应人工确认。
6. 运行 `infocmp xterm-ghostty`，确认本机 terminfo 可读。
7. 按 Ctrl+Alt+T 确认启动 Ghostty（默认已接管；若用了 `--no-default-terminal` 则跳过此项）；同时从文件管理器测试“在终端中打开”。
8. 隔离验证：从应用菜单连开两个窗口，`pgrep -a ghostty` 应看到两个进程；关掉或杀掉其中一个，另一个不受影响。
9. SSH 到不认识 `xterm-ghostty` 的远端时，本模块已开启 `ssh-terminfo` 自动处理（见下节）。若需手动处理：

   ```bash
   infocmp -x xterm-ghostty | ssh HOST -- tic -x -
   ```

10. 在普通 Ghostty/Pi 窗口验证：按住 **Shift** 拖选产生 Ghostty 本地选区，`Ctrl+Shift+C` 复制；先后按 `Ctrl+V` 与 `Ctrl+Shift+V`，确认两者读取相同的当前文本，再试图片。现有窗口须先正常重载配置，或打开新进程。
11. 在新开的本地 Wayland zsh 提示符和本机 tmux 普通 pane（已退出 copy-mode）的新 zsh 中，两键均应粘贴当前文本且不提交命令；多行与结尾换行应保持在编辑缓冲区。`Esc Ctrl+V` 应提供 quoted-insert。若提示符显示 `wl-paste` 错误或绑定冲突，检查本地 Wayland/SSH 环境与自有 zsh 键映射。共享 tmux 的远端客户端仍读取 shell 主机剪贴板。
12. 可选：从 `ghostty-pi` 新窗口进入同一 Pi 输入，确认其同样工作。SSH Pi 的粘贴依赖该 Pi 的 Ctrl+V/剪贴板桥行为，不把全局按键改动误当作远端桥的验证。

GUI 冒烟测试只在 `DISPLAY` 或 `WAYLAND_DISPLAY` 存在时运行。它不使用 xdotool、XTEST、libXtst 或任何合成键鼠事件；测试依据是从受保护启动器启动的进程持续存活、stderr 未出现错误，以及 `/proc/<pid>/maps` 中出现 fcitx5 GTK4 immodule（在 Wayland 下还要出现背压库）。

判定 stderr 时只认行首的错误级别前缀（`^(err|error)`）。不能拿整行里的
"error" 字样判死：GTK 4.14 解析系统主题 CSS 时会打
`warning(glib): ... Theme parser error: ...`，它前缀是 warning、与 Ghostty 无关，
误杀它会把一次成功安装报成失败。配置解析另由系统二进制
`/usr/bin/ghostty +validate-config` 单独把关；它不依赖用户启动器。

## SSH 与 terminfo

终端与程序靠 **terminfo** 沟通：程序查 `TERM` 指向的那条记录，才知道怎么清屏、
支持多少颜色、功能键发什么编码。Ghostty 的 `TERM` 是 `xterm-ghostty`。

这条记录很新，只在 **ncurses >= 6.5-20241228** 里才有。Ubuntu 24.04 自带的是 6.4，
本机这条是 Ghostty 的包装进 `/usr/share/terminfo` 的。远端服务器多半也没有，
于是 `vim`/`htop`/`less` 会报 `unknown terminal type` 或花屏。

把它弄到远端，**搬的是一份 3.8KB 的数据文件，不是把 Ghostty 装到远端**。
Ghostty 是本地程序，负责画窗口、渲染字体、调 GPU；远端只跑 shell 和 vim 这些程序，
它们需要的只是那张“能力说明书”。

本模块默认写入 `shell-integration-features = ssh-env,ssh-terminfo`，Ghostty 会在 SSH 时
自动用 `infocmp` + `tic` 把记录装到远端的 `~/.terminfo`（**不需要 sudo**，也不影响
其它用户）。上游默认是关闭的（`no-ssh-env,no-ssh-terminfo`），这里刻意开启。

`xterm-ghostty` 的 terminfo 带 `Ms`（OSC 52 剪贴板）能力，这是终端侧“远端复制回本机”
能成立的一个前提；本模块的安装验证会检查它（缺 `Ms` 明确失败，`--check` 也会报告）。
但注意 `ssh-terminfo` 并不是嵌套路径的一环：它只在 Ghostty 的 SSH 包装真正生效时把
`xterm-ghostty` 记录装到远端（直接在 Ghostty 集成 shell 里发起 ssh，或显式 `ghostty +ssh`）；
**本地 tmux pane 里起的嵌套 shell 不会继承那个包装**。嵌套路径上远端 tmux 依赖的是它
客户端侧的有效 `Ms`，来源见 `fresh-install/modules/tmux/README.md` 剪贴板一节。

安全网：官方文档声明两项同时开启时，安装失败会**自动回退到** `xterm-256color`，
不会把人卡在花屏状态。前提是远端有 `infocmp` 与 `tic`（ncurses 自带）；
极简容器镜像可能没有，那种情况下走回退。

`shell-integration-features` 只写**与默认值的差异项**，不抄全量串。文档明确
“省略某个特性就用它的默认值”，因此上游将来调整其它特性默认值时本模块能自动
跟上，而不会把一份过期快照冻在用户配置里（尤其 `no-sudo` 这种安全相关项）。
实测写入后生效值为 `cursor,no-sudo,title,ssh-env,ssh-terminfo,path`。

## SSH 异常断开后的鼠标乱码

`0;62;39M` / `0;42;40m` 不是字体乱码，而是 SGR 鼠标报文的一部分：完整报文形如
`ESC[<按钮;列;行M`（按下）或末尾为 `m`（释放）。远端的 tmux、vim、htop 等程序
打开鼠标上报后，如果 SSH 因超时、断网或进程被杀而结束，程序来不及发送关闭鼠标
模式的序列；Ghostty 不知道哪个 SSH 进程已死，因此会按协议继续把下一次点击送进
当前本地 shell。zsh 的行编辑器消费报文前缀后，数字尾部就会显示在提示符中。
这与 `ssh-env` / `ssh-terminfo` 无关：那两个特性只处理环境变量和 terminfo，继续保留。

Ghostty 模块目录里只有 **`install.sh` 一个 shell 源文件**。其中的
`render_ssh_mouse_hook` 和 `render_local_paste_hook` 直接包含两份 zsh hook
源码；安装时生成
`~/.config/ghostty/ghostty-ssh-mouse-reset.zsh` 与
`~/.config/ghostty/ghostty-local-paste.zsh`，并让 `.zshrc` 加载它们。安装器退出
以后，SSH 返回提示符和本地 ZLE 粘贴仍需这些运行时文件。测试脚本位于模块
外的 `fresh-install/tests/ghostty/`，会渲染 hook 并用 `zsh -n` / 私有
交互式 PTY、假的 `wl-paste` 验证。打包这个模块只需 `install.sh` 与非 shell 的
`wayland-backpressure.c`；C 源码缺失时 `--check` 与安装都会在写入前失败。

本模块采用两层处理：

1. `~/.config/ghostty/config.ghostty` 中绑定
   `keybind = ctrl+shift+r=reset`。它调用 Ghostty 原生 reset 动作，会重置所有鼠标
   上报模式；SSH 由 `exec`、脚本或其它工具启动时也能直接使用。也可以运行 `reset`，
   或从终端右键菜单选择 **Reset**。
2. 若安装时存在 zsh，模块会写入
   `~/.config/ghostty/ghostty-ssh-mouse-reset.zsh`，并在 `~/.zshrc` 末尾加入标记托管
   块。它只在 Ghostty 的交互式 zsh 中生效：识别首个命令为 `ssh`（包括常见的
   `command`、`builtin`、`env`、`sudo` 和 `VAR=value` 修饰），待 SSH 返回提示符前
   发送 `1000,1001,1002,1003,1005,1006,1015,1016` 的 DECRST。它不替换 `ssh`、不改变退出
   码，也不在 SSH/TUI 运行期间干预鼠标；因此 `tmux` 的鼠标功能仍可用。若本地使用
   GNU screen（`STY`），hook 会跳过自动复位，使用 `Ctrl+Shift+R` 手动恢复。

自动 hook 的边界是 `exec ssh`（没有本地提示符可回）、非交互式脚本/GUI 工具，以及
符号链接 `.zshrc`（为避免破坏 stow/chezmoi 等管理文件，安装器会跳过并提示手动
source）。hook 文件内容变化时重跑会先保存 `.bak.<时间戳>` 并同步基准；已有完整标记的 `.zshrc`
托管块保持原位置，其它 `.zshrc` 内容保持不动。该问题的上游说明见
[Ghostty discussion #10547](https://github.com/ghostty-org/ghostty/discussions/10547)、
[#6679](https://github.com/ghostty-org/ghostty/discussions/6679)；上游建议的恢复动作
也是 `reset`。

模块源码不再需要单独的 `.zsh` 文件；运行时的 hook 由安装器直接生成。
单独拷出 `install.sh` 却漏掉 `wayland-backpressure.c` 会被预检拒绝，
不会报告一个缺少 Wayland 修复的成功安装。
