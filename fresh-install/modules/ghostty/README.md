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

The managed zsh widget on local Wayland **interactive prompts (including local
tmux panes)** receives raw `^V` from both `Ctrl+V` and `Ctrl+Shift+V`. Local
input reads the current system CLIPBOARD through
`wl-paste --no-newline --type text` within two seconds; input from an SSH
client viewing a shared tmux pane reads the client's clipboard bridge instead.
Complete text is inserted into the ZLE editing buffer, preserving text after
the cursor and genuine trailing newlines. Newlines, semicolons, or command
substitutions inside pasted text do not execute during insertion; only an
explicit user submission may run the resulting buffer. A failed or timed-out
read reports at the prompt, leaves the buffer unchanged, and never falls back
to a tmux buffer or the other machine's clipboard. Literal quoted insertion
moved to **Esc then Ctrl+V** (`^[^V`). At load time the widget verifies that
this sequence is free and that `^V` uses zsh's default `quoted-insert` or
`vi-quoted-insert`; a conflict reports an error rather than taking a custom key.

The hook binds ZLE keys only in interactive zsh with native Ghostty child-shell
markers and a local Wayland socket. The terminal context must be
`TERM_PROGRAM=ghostty`, or `TERM_PROGRAM=tmux` **and a non-empty `TMUX`**;
both require a usable `GHOSTTY_RESOURCES_DIR`, a Wayland socket, and no
`SSH_TTY`. Tmux changes a child shell's `TERM_PROGRAM` to `tmux`, so a guard
that only accepts `ghostty` skipped real local tmux prompts and left `^V` as
quoted-insert. When Ghostty starts from an earlier Pi/tmux process, it can
inherit stale `SSH_CONNECTION`/`SSH_CLIENT`; those variables are not remote
proof, otherwise real local prompts would be skipped. A remote zsh allocated
an SSH PTY is excluded by `SSH_TTY`.

**A shared tmux paste chooses its clipboard when the key is pressed**: it lists
only clients currently displaying this pane, takes the newest `client_activity`,
then inspects that client's `/proc` ancestry. When the newest client came over
SSH, it reads text through the existing [pi-remote-clipboard
bridge](../../../pi-agent/extensions/pi-remote-clipboard/README.md) at
`127.0.0.1:37844` (override with `PI_REMOTE_CLIPBOARD_REMOTE_PORT`) within
eight seconds; a local client still reads the server's Wayland clipboard.
Therefore, after clicking `[copy]` in Pi over SSH, pasting at an executor
prompt from that same client reads the client clipboard written by the copy
rather than stale server text.

The configured bridge must point at that SSH client host; this reuses the
existing single client-host forward and adds no per-connection pairing. The
shell accepts only text and does not save images as temporary paths. A missing
client, failed tmux query, unavailable bridge, or image response reports an
error without falling back locally. `client_activity` has one-second precision
and remains newest-activity inference; if the newest time is tied by any SSH
client, the reader refuses to guess. Keep another client's keyboard/mouse idle
and retry after a second. Input from another client during the read can still
change the inferred origin; exact per-key provenance is outside this path. Raw
`^V` must first reach ZLE; the managed tmux copy-mode paste handoff, copy
bindings, tmux mouse behavior, and Pi copy/image paste are unchanged.

Raw-mode programs such as Pi do not use ZLE and keep their own Ctrl+V handlers
for image/text reads; the SSH clipboard bridge and OSC 52 write path are
unchanged. Other TUIs interpret raw `^V` themselves. Non-Wayland sessions,
remote SSH-PTY zsh, other shells, and terminals without the Ghostty child-shell
conditions do not receive this ZLE key; right-click or Ghostty's native
`Shift+Insert` paste remains available.

### Migrate only this key in an existing installation

From the repository root, run:

```bash
bash fresh-install/modules/ghostty/install.sh --paste-keybind-only
```

The narrow mode accepts an existing non-symlink
`~/.config/ghostty/config.ghostty` only when it **byte-exactly equals the old
managed baseline**, then atomically replaces it with the new baseline that
differs only in this key; a current file is not rewritten. Custom content, or
changed fonts/start-directory settings since the old install, fails explicitly
and preserves the file for manual handling. It does not call apt, rebuild the
Wayland backpressure library, update fonts/desktop/default-terminal state,
start the GUI, or modify `ghostty-pi` or the ordinary launcher. Do not run the
full argument-free `install.sh` for this migration: it resets the whole
configuration and performs the other steps above.

**A currently running Ghostty process** must reload its configuration normally
(Ghostty 1.3.1 defaults to `Ctrl+Shift+,`, action `reload_config`) or start a
new process from a managed entry before it receives the new binding; creating a
new Pi pane in an old window is not a process-level reload. After returning to
Pi, verify that `Ctrl+V` and `Ctrl+Shift+V` paste the same current text, then
try an image. The dedicated `ghostty-pi` still runs and applies the same
`--keybind=ctrl+shift+v=text:\x16` override in its new process.

### Add only the zsh prompt paste on an existing machine

After the global key migration, run from the repository root:

```bash
bash fresh-install/modules/ghostty/install.sh --shell-paste-only
```

This narrow mode first byte-checks the current managed Ghostty configuration
(which must remain `ctrl+shift+v=text:\x16`), the SSH mouse hook, and the
existing `.zshrc` marker block. The paste hook must be absent, a
**byte-exact deployed v1/v2/v3**, or the current baseline; historical content
is pinned by independent SHA-256 values in the installer and tests, so renderer
changes cannot redefine old versions. Customized configurations, hook edits,
block edits, symlinks, or missing prerequisites are refused **before writing**.
The first install adds `~/.config/ghostty/ghostty-local-paste.zsh` and one
source line inside the existing `.zshrc` managed block (creating
`.zshrc.bak.<timestamp>` first). A machine with v1/v2 only atomically replaces
the hook with this baseline and preserves `ghostty-local-paste.zsh.bak.
<timestamp>`; a current `.zshrc` managed block keeps the entire file's bytes and
inode unchanged. Re-running does not rewrite identical content; `.zshrc`
outside the block and its file mode remain unchanged. It does not call apt,
rebuild backpressure, change global Ghostty bindings/launchers/desktop
entries/fonts, start the GUI, or read/modify the real clipboard. After upgrade,
open a new zsh or run this at an existing **shell prompt**:

```zsh
source "$HOME/.config/ghostty/ghostty-local-paste.zsh"
```

An already-owned key is preserved while its function definition refreshes;
sourcing repeatedly does not overwrite Esc Ctrl+V's quoted-insert. Pi does not
need `/reload`. Copy text and try both paste keys; then try text containing
newlines and confirm it enters the editing buffer without executing. `Esc
Ctrl+V` should insert the next control character literally.

The argument-free full `install.sh` also renders this hook and writes the same
managed block while installing Ghostty; the module still has `install.sh` as
its only shell source file.

To add only the dedicated `ghostty-pi` entry on a machine lacking it, run
`install.sh --pi-launcher-only`; it verifies the protected launcher and
backpressure library and installs that entry, but **does not** migrate global
configuration. The three narrow modes cannot be combined. The retained
dedicated entry can start directly or attach an existing Pi session:

```bash
~/.local/bin/ghostty-pi
~/.local/bin/ghostty-pi -e tmux attach-session -t quick-deploy_pi-agent
```

Arguments after the second `-e` reach Ghostty unchanged. When other clients
attach the same tmux session, switching windows affects them; use a tmux
grouped session to select windows independently instead of `tmux attach -d`,
which would disconnect the original client.

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
env -u LD_PRELOAD bash ../../tests/ghostty/run.sh
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
交互式 PTY、假的 `wl-paste`、私有 tmux/客户端剪贴板桥验证。回归入口清除
继承的 `LD_PRELOAD`，让 Wayland 的未修复负对照不受已安装背压库影响；
正对照仍显式加载自己的测试库。打包这个模块只需 `install.sh` 与非 shell 的
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
