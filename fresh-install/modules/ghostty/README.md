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
  - `Ctrl+Shift+C` 绑定 `copy_to_clipboard:mixed`，`Ctrl+Shift+V` 绑定 `paste_from_clipboard`：本机复制/粘贴的两条键，显式写入，不依赖上游默认绑定（见“鼠标上报下的复制”一节）
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
- `~/.zshrc`
  - 追加一个标记包围的 hook 加载块（符号链接 `.zshrc` 不会被改写）

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
- **读拒绝**：OSC 52 读取会让终端里的程序把本机剪贴板内容取走。Ghostty 默认值是 `ask`（弹窗询问），本模块改为无条件拒绝：终端内程序永远读不到本机剪贴板。
- **日常粘贴不受影响**：`Ctrl+Shift+V`、鼠标右键、`Shift+Insert` 是 Ghostty 自己的本地动作，由终端直接把剪贴板内容写进 pty，不经过 OSC 52 读取；`Ctrl+Shift+C` 复制同样走 Ghostty 的本地选区。远端只需要用户按一次粘贴键。
- 策略与显示服务器、系统版本无关：脚本里没有任何按 Wayland/X11 或 Ubuntu 版本分叉的剪贴板逻辑。回归测试在 Wayland、X11、无会话变量三种环境下断言渲染结果逐字节相同——这只证明**配置渲染不分叉**，不证明任何剪贴板后端的实际行为（后者需要真实图形会话）。
- 本模块不写 `terminal-features` 之类的终端能力声明，也不强制声明 `Ms`；远端 tmux 能不能发 OSC 52 取决于它客户端侧的有效 `Ms`（可能来自 terminfo、配置的特性或 tmux 的运行时识别，见 `fresh-install/modules/tmux/README.md` 剪贴板一节）。本模块能做的是保证本机 `xterm-ghostty` 记录确实带 `Ms`：`--check` 会报告它，正式安装的验证阶段缺 `Ms` 会明确失败。

### 鼠标上报下的复制：先 Shift+拖选，再 Ctrl+Shift+C

在 Pi 全屏或 tmux `mouse on` 里，普通拖选会被申请了鼠标上报的程序接管
（Pi 自己画选区并在释放时复制，tmux 进 copy-mode）。此刻 Ghostty 没有本地选区，
`Ctrl+Shift+C` 无内容可复制，看起来像“快捷键失效”。本模块显式写死三行，
把用户实际使用的两条键与选择所有权固定下来：

```ini
mouse-shift-capture = never
keybind = ctrl+shift+c=copy_to_clipboard:mixed
keybind = ctrl+shift+v=paste_from_clipboard
```

| 动作 | 谁执行 | 结果 |
| --- | --- | --- |
| `Shift+拖选` | Ghostty | 按住 Shift 的鼠标事件永远不按鼠标协议上报给程序，而是扩展 Ghostty 本地选区 |
| `Ctrl+Shift+C` | Ghostty | 把本地选区写进**本机**系统剪贴板；`mixed` 同时提供纯文本与带样式两种格式（无选区时不做任何事） |
| `Ctrl+Shift+V` | Ghostty | 把**本机**剪贴板内容直接写进 pty；SSH 场景下内容作为输入送到远端，不需要远端有剪贴板工具 |

选择与复制全在本机 Ghostty 内完成，与远端有没有 Wayland/X11、有没有
`wl-copy`/`xclip` 无关；这也是 SSH 场景下这两条键仍然可用的原因。

两条键都显式写入，不依赖上游默认绑定：本文件由模块重置维护，即使将来上游
改了默认绑定或默认 `mouse-shift-capture`，这里的行为不变。这里刻意选 `never`
而不是上游默认 `false`：两者通常都把 Shift 留给 Ghostty，但 `false` 可被程序用
`XTSHIFTESCAPE` 覆盖；`never` 才能保证用户始终有 Shift+拖选这条本地选择路径。

边界：

- 程序申请了鼠标上报时，**普通拖选属于程序**；只有按住 Shift 的那次拖选才会
  产生 Ghostty 本地选区，`Ctrl+Shift+C` 复制的也是这个选区。
- 没有程序申请鼠标上报时（例如普通 shell 提示符），普通拖选本来就是
  Ghostty 选区，`Ctrl+Shift+C` 直接可用。
- `never` 会让终端内程序永远收不到 Shift+鼠标组合；这是为稳定保留本地复制
  手势付出的明确代价。本机当前的 Pi/tmux 工作流不依赖该组合。

### Pi 专用窗口：Ctrl+Shift+V 复用 Pi 的 Ctrl+V

**只部署这个已批准的快捷键入口**时，从仓库根目录运行：

```bash
bash fresh-install/modules/ghostty/install.sh --pi-launcher-only
```

此模式先只读核对已安装的 `/usr/bin/ghostty`、受保护的
`~/.local/bin/ghostty` 的受管字节与可执行性，以及 Wayland 背压库和校验副本
逐字节一致；不匹配就拒绝写入。然后只在不存在时安装
`~/.local/bin/ghostty-pi`（已是受管基准则跳过）。不安装软件包、不编译、
不更新全局 Ghostty 配置/字体/desktop/默认终端，也不运行 GUI 冒烟。
不要为单独部署此入口运行无参数的完整 `install.sh`。

需使用另一条粘贴读取路径时，主动启动 `~/.local/bin/ghostty-pi`。它先通过
`~/.local/bin/ghostty` 保留 Wayland 背压修复，再以独立 Ghostty 进程覆盖**一条**
按键：`--keybind=ctrl+shift+v=text:\x16`。Ghostty 的 `text` 动作向 pty 发送
原始 0x16（Ctrl+V），由 Pi 已有的 `app.clipboard.pasteImage` 绑定进入原生
图片优先/文本回退粘贴入口；SSH 输入时仍由已安装的 Pi 剪贴板桥按原有逻辑
处理。它不调用 Ghostty 的 `paste_from_clipboard`，所以避开该窗口内可能陈旧的
GTK 剪贴板读取。`Ctrl+V`、`Ctrl+C`、Ghostty 的 `Ctrl+Shift+C`、OSC 52
复制链和全局配置一律不变。

```bash
~/.local/bin/ghostty-pi                    # 打开新窗口；在其中运行 piox
~/.local/bin/ghostty-pi -e tmux attach-session -t quick-deploy_pi-agent
                                            # 附着已有 tmux/Pi session，不新起 Pi
```

第二条命令的 `-e` 后面原样传给 Ghostty。若已有 tmux session 同时被其他
客户端观看，直接切换它的窗口会影响那些客户端。此时先创建与原 session 共享
窗口、但各自独立选窗的 tmux grouped session，再在新 session 选中 Pi window，
从 `ghostty-pi` 附着新 session；无需启动第二个 Pi 进程。例如：

```bash
tmux new-session -d -s quick-deploy_pi-paste -t quick-deploy_pi-agent
tmux list-windows -t quick-deploy_pi-paste  # 找到现有 Pi window 的 index
tmux select-window -t quick-deploy_pi-paste:3  # 示例：将 3 换成找到的 index
~/.local/bin/ghostty-pi -e tmux attach-session -t quick-deploy_pi-paste
```

仅在 grouped session 尚不存在时执行 `new-session`；重开专用窗口时只需最后一行。
不要加 `tmux attach -d`，它会断开其他正在观看该 session 的客户端。这个入口
是**窗口级**而不是程序检测：切到同一窗口的 shell/tmux 其他 pane 时，
`Ctrl+Shift+V` 仍发送原始 Ctrl+V，**不会**执行 Ghostty 的文本粘贴；需要
终端粘贴就使用普通 Ghostty 窗口。现有运行中的 Ghostty 窗口不会因安装而
改变按键，必须从新入口打开。安装器只接受目标不存在或逐字节匹配的受管
`ghostty-pi`；用户自有文件、符号链接及目录均明确拒绝覆盖。

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

它报告完整安装流程的 apt/配置等计划，不代表 `--pi-launcher-only` 会执行
这些步骤。两个选项不可组合。

```bash
./install.sh --check
./install.sh --check --deb-only --default-terminal
```

离线回归测试（不安装软件、不联网、不改真实 HOME）：

```bash
bash ../../tests/ghostty/run.sh
```

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

10. SSH 进远端并进入 Pi 全屏（或 tmux `mouse on`）后，在**普通 Ghostty 窗口**验证“鼠标上报下的复制”一节的两条键：普通拖选被程序接管；按住 **Shift** 拖选产生 Ghostty 本地选区，再按 `Ctrl+Shift+C`；`Ctrl+Shift+V` 应把本机剪贴板内容粘贴进去。
11. 从 `ghostty-pi` 的新窗口进入同一 Pi 输入，先后按 `Ctrl+V` 和 `Ctrl+Shift+V`，核对两者读取相同的当前文本；图片粘贴也应进入 Pi 已有路径。普通窗口仍按第 10 条工作。

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
`render_ssh_mouse_hook` 直接包含 zsh hook 源码；安装时生成
`~/.config/ghostty/ghostty-ssh-mouse-reset.zsh`，并让 `.zshrc` 在每个交互式
shell 启动时加载它。安装器退出以后，SSH 返回提示符时仍需这个本机运行时
文件。测试脚本位于模块外的 `fresh-install/tests/ghostty/`，会渲染 hook 并用
`zsh -n` / 交互式 PTY 验证。打包这个模块只需 `install.sh` 与非 shell 的
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
