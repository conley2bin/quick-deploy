# tmux 安装模块

本模块为 Ubuntu 24.04+ 主机安装 tmux 和 [Oh my tmux!](https://github.com/gpakosz/.tmux)（gpakosz/.tmux）配置：

```bash
./install.sh
```

## 安装内容

1. `apt install tmux git xclip wl-clipboard` —— 后两者是 gpakosz 配置“复制到系统剪贴板”功能在 Linux 下的依赖：X11 会话用 `xclip`，Wayland 会话用 `wl-copy`，两个都装以覆盖两种会话。
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

- 单一事实源：改 `~/.tmux.conf.local` 就是改仓库文件（`<前缀> e` 打开的也是它），改完 `<前缀> r` 生效、`git commit` 入库；已链接的机器只靠 `git pull` 即可接收更新。
- 注意：gpakosz 每次加载配置都会用 `cut -c3- "$TMUX_CONF_LOCAL" | sh -s printf probe` 探测本文件是否旧式脚本格式——注释行剥掉前两个字符（`# `）后会**被 shell 真实执行**，因此注释里不要写 `> < ; | & $() 反引号` 等元字符（历史上的 `（CSI > 4 ; 2 m）` 曾在服务器工作目录生成空文件 `4`）；需要表达时用全角 `＞ ；` 代替。
- 基线只记真实改动（目前是鼠标模式、状态栏左键释放切换 window、禁用状态栏区域滚轮切换 window、copy-mode 字母键退出并原样输入、精简状态栏、选中 window 两端蓝色竖条、取消 `Ctrl+a` 第二前缀、`Ctrl+Alt+←/→` 切换 window、`Ctrl+Alt+=/+` 新建 window）；全部可用选项查上游模板 `~/.tmux/.tmux.conf.local`。该文件本质是 tmux 配置片段，可直接写 `set -g ...`；若某行被主配置覆盖，按上游说明在行尾加 `#!important`。

Pi suspend guard 与 breathing status 都不是本模块的源码：它们分别在 `pi-agent/extensions/pi-suspend-guard/` 和 `pi-agent/extensions/pi-tmux-window-status/`，本模块只负责链接安装。

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

测试使用独立 tmux socket 和临时目录，真实验证状态栏鼠标释放切换，以及 emacs、vi 两张 copy-mode 键表退出后向 pane 投递原字符；不会改动当前 tmux server。

## Pi 扩展自动发现

Pi 只在启动时扫描 `~/.pi/agent/extensions/` 下的目录，不会扫描本仓库——仓库里的 `pi-agent/extensions/pi-tmux-window-status` 必须通过受管符号链接暴露到 `~/.pi/agent/extensions/` 才会被加载。安装/更新扩展后需要**重启 Pi 或执行 `/reload`** 才生效；tmux 只须 `<前缀> r` 重载样式。

该扩展链接不受 conley 的 pi-agent fork 追踪；扩展安装器将路径写入本机仓库的 `.git/info/exclude`，因此 Git 不拥有该链接，`git pull` 与重跑安装互不干扰。

## 使用要点

- 前缀键仅保留默认 `Ctrl+b`；Oh my tmux! 默认新增的第二前缀 `Ctrl+a` 已取消。
- `<前缀> e` 打开 `.tmux.conf.local`，`<前缀> r` 重载配置。
- 状态栏左侧只显示 session 名，与右侧同为浅灰字、深灰底；右侧移除电池与时间、日期，只留 `用户名@主机名`。未选中 window 空闲时为 `#bcbcbc` 灰白块、深色字；bell 保留黄色前景和 `!` 标记；last/activity 不改变背景。选中 window 在色块左右末端各放两个整格实心 `#0077aa` 蓝色竖条（`██`；fg/bg 同设蓝，字体留缝隙也不漏底色），与 error 红底走正交视觉通道，竖条永不呼吸、永不变色。Pi 根回合或其异步子代理运行时，相应 window 背景以约 1s 周期在灰色路径上呼吸；模型/供应商可用性错误时显示稳定红底白字，后续语义输出或成功结束会清除。扩展语义与自动续跑细节见 `pi-agent/extensions/pi-tmux-window-status/README.md`。
- `<前缀> m` 切换鼠标模式；状态栏 window 标签在鼠标左键释放时切换，因此单击和快速连续点击都会落到释放位置对应的 window。状态栏区域的滚轮不再切换 window；普通 pane 中鼠标滚轮每格滚动 1 行。`<前缀> -` / `<前缀> _` 分屏；`<前缀> h/j/k/l` 在窗格间移动。应用主动开启 mouse reporting 时，滚轮仍交给应用自身处理。鼠标拖选复制后停留在 copy-mode、不跳回 pane 底部；按 `Esc` 只退出，按任意英文字母则退出并把该字母原样输入 pane，大小写保持不变且不会自动回车。
- `Ctrl+Alt+←/→` **不需要前缀**，直接切换上一个/下一个 window（底部状态栏的标签）。绑定落在 root 表：`C-M-Left=previous-window`、`C-M-Right=next-window`。Ghostty 模块显式 unbind 这两个键，确保按键进入 pty；gpakosz 检测到 `TERM_PROGRAM=ghostty` 后自动开启 extended-keys，tmux 才能识别组合键。
- `Ctrl+Alt+=` / `Ctrl+Alt++` **不需要前缀**，提示输入名称后在当前 pane 的目录新建 window；直接回车则让 tmux 按运行程序自动命名。Ghostty 模块为两者显式发送 CSI-u 序列，tmux 分别绑定 `C-M-=` / `C-M-+`，避免符号键修饰信息在终端编码中丢失。
- 内置 TPM 插件支持：在 `.tmux.conf.local` 里写 `set -g @plugin ...`，`<前缀> I` 安装，`<前缀> u` 更新，`<前缀> M-u` 卸载。
- 完整键位与状态栏变量见上游模板 `~/.tmux/.tmux.conf.local` 和上游 README。
