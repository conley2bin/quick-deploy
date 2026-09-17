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
  - 新终端起始目录 `~/Documents`（不存在时回退到 XDG 文档目录，再不行就 `home`）
  - 关闭新窗口工作目录继承；新标签页和分屏仍保留继承
  - `gtk-single-instance = false`：每个外部启动的 Ghostty 用自己的进程（见下节）
- `~/.local/share/applications/com.mitchellh.ghostty.desktop`
  - 由系统模板 `/usr/share/applications/com.mitchellh.ghostty.desktop` 逐行生成，只把两个 `Exec=` 入口改为
    `--gtk-single-instance=false`、`DBusActivatable=false`；系统文件本身不改
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
+Exec=/usr/bin/ghostty --gtk-single-instance=false
-DBusActivatable=true
+DBusActivatable=false
```

两处都必须写，因为它们各自覆盖不同的启动路径：

| 启动路径 | 谁决定单实例 | 模块的处理 |
| --- | --- | --- |
| 应用菜单 / 桌面快捷方式 | 系统 desktop 的 `Exec=` 显式带 `--gtk-single-instance=true`；CLI 参数优先于配置文件 | 用户级覆盖把两个 `Exec=` 入口都改成 `--gtk-single-instance=false` |
| Ctrl+Alt+T（包装脚本 `exec /usr/bin/ghostty "$@"`） | 没有 CLI 参数，命令行 `--gtk-single-instance` 的默认值 `detect` 只在配置值仍为 `detect` 时才参与判断 | 配置里的 `false` 直接生效，不经过 `detect`，也就不靠 `TERM_PROGRAM` 这类环境事实 |

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
含 `--gtk-single-instance=false`，且 `DBusActivatable=false`。边界：模块固定写
`$HOME/.local/share/applications`（与它写 `~/.config/ghostty` 同一约定），不跟随
`XDG_DATA_HOME` / `XDG_CONFIG_HOME`；机器上若自定义了 `XDG_DATA_HOME`，需要自己把覆盖
放到该目录，否则系统入口不会被遮蔽。

这个用户级覆盖只接受三种状态，其余一律**拒绝覆盖并明确失败**，不静默降级：

1. 文件不存在：按系统模板生成；
2. 与当前系统模板生成的基准内容一致：重跑幂等，不重写、不备份；
3. 与系统模板只差上面那三处启动语义：视为不含用户自有内容，先备份 `.bak.<时间戳>` 再替换。

带用户改动的文件（换了 `Icon=`、加了自有字段、写了别的 `Exec=` 等）会被拒绝，安装脚本会
打印差异命令和恢复方式（把该文件 `mv` 移开再重跑）。系统模板结构变化导致无法生成时
同样明确失败：宁可报错，也不写出一个“看起来成功、实际仍复用进程”的覆盖。

`install.sh` 里还有一处 `--gtk-single-instance=false` 属于 GUI 冒烟测试：让那一次探针进程
独立于用户已在运行的 Ghostty。没有它，单实例模式下新进程会把请求交给已有实例后立即
退出，存活判定随即误报“提前退出”。模块的日常策略现在同为独立进程，但那里的显式参数
仍然保留：冒烟测试要能不依赖配置文件是否正确而自己保证探针独立。

## 默认接管 Ctrl+Alt+T

默认运行**会接管 Ctrl+Alt+T**。写入：

- `~/.local/bin/x-terminal-emulator`：执行 `/usr/bin/ghostty "$@"`，供 GNOME 的 `gsd-media-keys` 通过 PATH 启动；
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

## `--check` 是只读预检

```bash
./install.sh --check
./install.sh --check --deb-only --default-terminal
```

离线回归测试（不安装软件、不联网、不改真实 HOME）：

```bash
bash tests/run.sh
```

预检报告：系统与架构、Ghostty 版本、apt 候选版本、PPA 是否已在源中、配置状态、用户级 desktop 覆盖状态（已是基准内容 / 将被替换 / 将被拒绝）、两种字体、`xterm-ghostty` terminfo、SSH 鼠标自愈 hook、当前默认终端所有者以及计划动作。它不会添加软件源、调用修改状态的 apt 命令、访问 GitHub、下载文件或写入用户目录。

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

GUI 冒烟测试只在 `DISPLAY` 或 `WAYLAND_DISPLAY` 存在时运行。它不使用 xdotool、XTEST、libXtst 或任何合成键鼠事件；测试依据是进程持续存活、stderr 未出现错误，以及 `/proc/<pid>/maps` 中出现 fcitx5 GTK4 immodule。

判定 stderr 时只认行首的错误级别前缀（`^(err|error)`）。不能拿整行里的
"error" 字样判死：GTK 4.14 解析系统主题 CSS 时会打
`warning(glib): ... Theme parser error: ...`，它前缀是 warning、与 Ghostty 无关，
误杀它会把一次成功安装报成失败。配置解析另由 `ghostty +validate-config`
单独把关。

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

如果只把 `install.sh` 单独拷出而没有同目录的
`ssh-mouse-reset.zsh`，安装器会跳过自动 hook 并保留 `Ctrl+Shift+R` / `reset` 手动
恢复路径。
