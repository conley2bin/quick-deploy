# tmux 安装模块

本模块为 Ubuntu 24.04+ 主机安装 tmux 和 [Oh my tmux!](https://github.com/gpakosz/.tmux)（gpakosz/.tmux）配置：

```bash
./install.sh
```

## 安装内容

1. `apt install tmux git xclip wl-clipboard` —— 后两者是 gpakosz 配置“复制到系统剪贴板”功能在 Linux 下的依赖：X11 会话用 `xclip`，Wayland 会话用 `wl-copy`，两个都装以覆盖两种会话。
2. 克隆 `https://github.com/gpakosz/.tmux` 到 `~/.tmux`（`--single-branch`）。
3. 符号链接 `~/.tmux.conf` → `~/.tmux/.tmux.conf`。
4. 复制 `~/.tmux/.tmux.conf.local` → `~/.tmux.conf.local`。

## 定制入口

上游明确要求**不要改主配置** `~/.tmux/.tmux.conf`（改了后续 `git pull` 更新会冲突），一切定制写在 `~/.tmux.conf.local` 副本里。该文件本质是 tmux 配置片段，可以直接写 `set -g ...`；若某行被主配置覆盖，按上游说明在行尾加 `#!important`。

### 本地副本与上游模板是脱钩的

`~/.tmux.conf.local` 是安装时从仓库**复制**出来的副本，从复制那一刻起就是私有文件。重跑脚本时 `git pull` 只更新仓库里的模板，本地副本**永不被覆盖或合并**——否则你写的定制（如 `set -g mouse on`）会被冲掉。

代价：上游以后在模板里新增选项时，你不会自动获得。这不会弄坏任何东西（主配置对所有选项都有默认值），只是看不到新选项。想查看上游新增了什么，手动对比并挑想要的行抄过来：

```bash
diff ~/.tmux.conf.local ~/.tmux/.tmux.conf.local
```

## 幂等语义

重跑本脚本：

- apt 包已装则跳过；
- `~/.tmux` 已是克隆则用 `git pull --ff-only` 更新；更新失败（离线、本地有改动）只警告不中止，保留现有版本；
- `~/.tmux.conf.local` 已存在则**永不覆盖**；
- 替换既有 `~/.tmux.conf` 或非仓库的 `~/.tmux` 目录前，先备份为 `*.bak.<时间戳>`。

在 `setup.sh` 中本步骤为 tolerate：tmux 本体走 apt 很可靠，但配置仓库要从 GitHub 克隆，全新机器还没配代理时可能失败——只提示不中止，网络就绪后重跑本脚本即可。

## 使用要点

- 前缀键保留默认 `Ctrl+b`，同时新增第二前缀 `Ctrl+a`。
- `<前缀> e` 打开 `.tmux.conf.local`，`<前缀> r` 重载配置。
- `<前缀> m` 切换鼠标模式；`<前缀> -` / `<前缀> _` 分屏；`<前缀> h/j/k/l` 在窗格间移动。
- 内置 TPM 插件支持：在 `.tmux.conf.local` 里写 `set -g @plugin ...`，`<前缀> I` 安装，`<前缀> u` 更新，`<前缀> M-u` 卸载。
- 完整键位与状态栏变量见 `~/.tmux.conf.local` 注释和上游 README。
