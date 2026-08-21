# tmux 安装模块

本模块为 Ubuntu 24.04+ 主机安装 tmux 和 [Oh my tmux!](https://github.com/gpakosz/.tmux)（gpakosz/.tmux）配置：

```bash
./install.sh
```

## 安装内容

1. `apt install tmux git xclip wl-clipboard` —— 后两者是 gpakosz 配置“复制到系统剪贴板”功能在 Linux 下的依赖：X11 会话用 `xclip`，Wayland 会话用 `wl-copy`，两个都装以覆盖两种会话。
2. 克隆 `https://github.com/gpakosz/.tmux` 到 `~/.tmux`（`--single-branch`）。
3. 符号链接 `~/.tmux.conf` → `~/.tmux/.tmux.conf`。
4. 符号链接 `~/.tmux.conf.local` → 模块自带的 `tmux.conf.local`（见下节）。

## 定制入口

上游明确要求**不要改主配置** `~/.tmux/.tmux.conf`（改了 `git pull` 会冲突），一切定制写在 `.tmux.conf.local` 里。本模块把这个入口**直接符号链接到仓库文件** `fresh-install/tmux/tmux.conf.local`：

- 单一事实源：改 `~/.tmux.conf.local` 就是改仓库文件（`<前缀> e` 打开的也是它），改完 `<前缀> r` 生效、`git commit` 入库。
- 多机同步只拉不装：别的机器 `git pull` 本仓库即生效，无需重跑 install.sh。
- 基线只记真实改动（目前仅鼠标模式）；全部可用选项查上游模板 `~/.tmux/.tmux.conf.local`。该文件本质是 tmux 配置片段，可直接写 `set -g ...`；若某行被主配置覆盖，按上游说明在行尾加 `#!important`。

路径依赖：符号链接记录的是安装时本仓库的绝对路径。仓库搬走后链接会悬空——此时 gpakosz 主配置照常加载（启动时会报一条 source 错误），只是定制失效；到新位置重跑一次 install.sh 即可重新链接。

## 幂等语义

重跑本脚本：

- apt 包已装则跳过；
- `~/.tmux` 已是克隆则用 `git pull --ff-only` 更新；更新失败（离线、本地有改动）只警告不中止，保留现有版本；
- `~/.tmux.conf.local` 已是指向模块基线的符号链接则跳过；若它被换成普通文件（少数编辑器写文件时会替换符号链接）或指向别处，先备份为 `*.bak.<时间戳>` 再重新链接——重跑即修复；
- 替换既有 `~/.tmux.conf` 或非仓库的 `~/.tmux` 目录前同样先备份。

在 `setup.sh` 中本步骤为 tolerate：tmux 本体走 apt 很可靠，但配置仓库要从 GitHub 克隆，全新机器还没配代理时可能失败——只提示不中止，网络就绪后重跑本脚本即可。

## 使用要点

- 前缀键保留默认 `Ctrl+b`，同时新增第二前缀 `Ctrl+a`。
- `<前缀> e` 打开 `.tmux.conf.local`，`<前缀> r` 重载配置。
- `<前缀> m` 切换鼠标模式；`<前缀> -` / `<前缀> _` 分屏；`<前缀> h/j/k/l` 在窗格间移动。
- 内置 TPM 插件支持：在 `.tmux.conf.local` 里写 `set -g @plugin ...`，`<前缀> I` 安装，`<前缀> u` 更新，`<前缀> M-u` 卸载。
- 完整键位与状态栏变量见 `~/.tmux.conf.local` 注释和上游 README。
