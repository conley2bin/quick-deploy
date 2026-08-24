# tmux 安装模块

本模块为 Ubuntu 24.04+ 主机安装 tmux 和 [Oh my tmux!](https://github.com/gpakosz/.tmux)（gpakosz/.tmux）配置：

```bash
./install.sh
```

## 安装内容

1. `apt install tmux git xclip wl-clipboard` —— 后两者是 gpakosz 配置“复制到系统剪贴板”功能在 Linux 下的依赖：X11 会话用 `xclip`，Wayland 会话用 `wl-copy`，两个都装以覆盖两种会话。
2. 克隆 `https://github.com/gpakosz/.tmux` 到 `~/.tmux`（`--single-branch`）。

## 脚本创建的链接

gpakosz/.tmux 的两个固定查找路径都以符号链接落盘，目标一个指向上游克隆、一个指向本仓库：

| 路径 | 指向 | 作用 |
| --- | --- | --- |
| `~/.tmux.conf` | `~/.tmux/.tmux.conf` | 主配置入口（tmux 固定读取位置）；随重跑时的 `git pull` 自动更新 |
| `~/.tmux.conf.local` | `fresh-install/tmux/tmux.conf.local`（安装时本仓库的绝对路径） | 定制入口；改动即仓库改动 |

约束与修复：

- 替换任何既有文件前一律先改名为 `*.bak.<时间戳>`，从不删除。
- `~/.tmux.conf.local` 记录的是仓库的绝对路径：仓库搬走后链接悬空，gpakosz 主配置照常加载（启动时报一条 source 错误），只是定制失效；到新位置重跑一次 install.sh 即可重新链接。
- 少数编辑器写文件时会把符号链接替换成普通文件，重跑 install.sh 同样自动备份并重建链接。

## 定制入口

上游明确要求**不要改主配置** `~/.tmux/.tmux.conf`（改了 `git pull` 会冲突），一切定制写在 `.tmux.conf.local` 里。本模块把这个入口符号链接到仓库文件（见上节链接表），因此：

- 单一事实源：改 `~/.tmux.conf.local` 就是改仓库文件（`<前缀> e` 打开的也是它），改完 `<前缀> r` 生效、`git commit` 入库。
- 多机同步只拉不装：别的机器 `git pull` 本仓库即生效，无需重跑 install.sh。
- 基线只记真实改动（目前是鼠标模式、取消 `Ctrl+a` 第二前缀、`Ctrl+Alt+←/→` 切换 window、`Ctrl+Alt+=/+` 新建 window）；全部可用选项查上游模板 `~/.tmux/.tmux.conf.local`。该文件本质是 tmux 配置片段，可直接写 `set -g ...`；若某行被主配置覆盖，按上游说明在行尾加 `#!important`。

## 幂等语义

重跑本脚本：

- apt 包已装则跳过；
- `~/.tmux` 已是克隆则用 `git pull --ff-only` 更新；更新失败（离线、本地有改动）只警告不中止，保留现有版本；
- `~/.tmux.conf.local` 已是指向模块基线的符号链接则跳过；若它被换成普通文件（少数编辑器写文件时会替换符号链接）或指向别处，先备份为 `*.bak.<时间戳>` 再重新链接——重跑即修复；
- 替换既有 `~/.tmux.conf` 或非仓库的 `~/.tmux` 目录前同样先备份。

在 `setup.sh` 中本步骤为 tolerate：tmux 本体走 apt 很可靠，但配置仓库要从 GitHub 克隆，全新机器还没配代理时可能失败——只提示不中止，网络就绪后重跑本脚本即可。

## 使用要点

- 前缀键仅保留默认 `Ctrl+b`；Oh my tmux! 默认新增的第二前缀 `Ctrl+a` 已取消。
- `<前缀> e` 打开 `.tmux.conf.local`，`<前缀> r` 重载配置。
- `<前缀> m` 切换鼠标模式；普通 pane 中鼠标滚轮每格滚动 1 行；`<前缀> -` / `<前缀> _` 分屏；`<前缀> h/j/k/l` 在窗格间移动。应用主动开启 mouse reporting 时，滚轮仍交给应用自身处理。
- `Ctrl+Alt+←/→` **不需要前缀**，直接切换上一个/下一个 window（底部状态栏的标签）。
  绑定落在 root 表：`C-M-Left=previous-window`、`C-M-Right=next-window`。
  Ghostty 模块显式 unbind 这两个键，确保按键进入 pty；gpakosz 检测到
  `TERM_PROGRAM=ghostty` 后自动开启 extended-keys，tmux 才能识别组合键。
- `Ctrl+Alt+=` / `Ctrl+Alt++` **不需要前缀**，提示输入名称后在当前 pane 的目录新建 window；直接回车则让 tmux 按运行程序自动命名。Ghostty 模块为两者显式发送 CSI-u 序列，tmux 分别绑定 `C-M-=` / `C-M-+`，避免符号键修饰信息在终端编码中丢失。
- 内置 TPM 插件支持：在 `.tmux.conf.local` 里写 `set -g @plugin ...`，`<前缀> I` 安装，`<前缀> u` 更新，`<前缀> M-u` 卸载。
- 完整键位与状态栏变量见上游模板 `~/.tmux/.tmux.conf.local` 和上游 README。
