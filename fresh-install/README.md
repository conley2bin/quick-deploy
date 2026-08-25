# Ubuntu 新机初始化

`fresh-install/` 是 Ubuntu 24.04+、amd64（x86）主机的一键初始化套件。根目录只保留编排入口、共享实现和导航文档；可独立运行的安装单元集中在 `modules/`。个别模块支持更多架构，例如 Ghostty 模块同时支持 amd64 与 arm64；支持范围以模块自身文档为准。

```text
fresh-install/
├── setup.sh                 # 一键入口：按固定顺序执行模块
├── lib/                     # 仅供脚本 source 的共享实现，不单独执行
│   └── apt-lock-wait.sh
├── modules/                 # 可独立执行的安装模块
│   ├── tsinghua-mirror/
│   ├── purge-snap/
│   ├── zsh/
│   ├── chinese-input-method/
│   ├── ghostty/
│   └── tmux/
└── tmux -> modules/tmux     # 兼容旧机器上的绝对配置链接
```

## 一键安装

```bash
bash fresh-install/setup.sh
```

执行顺序与失败语义由 `setup.sh` 的 `STEPS` 清单定义：

| 顺序 | 模块 | 失败语义 |
| --- | --- | --- |
| 1 | 清华 APT 镜像 | 中止 |
| 2 | 移除 Snap | 中止 |
| 3 | zsh 与 Oh My Zsh | 中止 |
| 4 | Fcitx5 中文输入法 | 中止 |
| 5 | 维基百科拼音词库 | 提示后继续 |
| 6 | Ghostty | 提示后继续 |
| 7 | tmux 与 Oh my tmux! | 提示后继续 |

所有步骤都按可重跑方式实现。必需步骤失败时，修复原因后重新执行 `setup.sh`；可容忍步骤失败时，也可单独执行对应模块。

## 单独运行模块

```bash
bash fresh-install/modules/tsinghua-mirror/install.sh
bash fresh-install/modules/purge-snap/purge.sh
bash fresh-install/modules/zsh/install.sh
bash fresh-install/modules/chinese-input-method/install.sh
bash fresh-install/modules/chinese-input-method/import-dict.sh
bash fresh-install/modules/ghostty/install.sh
bash fresh-install/modules/tmux/install.sh
```

各模块自行定位仓库共享库，不依赖从哪个工作目录启动。Ghostty 和 tmux 的参数、持久配置及恢复语义见各自目录中的 `README.md`。

## 共享实现

`lib/apt-lock-wait.sh` 被一键入口和所有执行 APT 操作的模块共同加载。它只等待真实持锁进程结束，不删除锁文件、不终止系统更新。保留模块自己的加载逻辑，是为了让模块脱离 `setup.sh` 单独运行时仍具备相同的首开机可靠性。

## tmux 兼容路径

`tmux/install.sh` 历史上将 `~/.tmux.conf.local` 绝对链接到仓库内的 `fresh-install/tmux/tmux.conf.local`。模块移入 `modules/` 后，根目录保留 `tmux → modules/tmux` 符号链接，使旧机器仅执行 `git pull` 也不会出现悬空链接。新安装以 `fresh-install/modules/tmux/` 为 canonical 路径。
