# Ubuntu 新机初始化

`fresh-install/` 是 Ubuntu 24.04+、amd64（x86）主机的一键初始化套件。`setup.sh` 编排安装，`modules/` 中的模块也可独立运行；`lib/` 放共享实现，`docs/` 放未接入安装的语音方案。个别模块支持更多架构，例如 Ghostty 同时支持 amd64 与 arm64；以模块自身文档为准。

```text
fresh-install/
├── README.md
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
├── docs/                    # 语音软件说明与待实施方案
│   ├── voice-input-software.md
│   └── voice-input-plan.md
└── tests/ghostty/            # Ghostty 的外置回归测试
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
| 6 | Ghostty（含原生 Wayland 背压修复，单一 `install.sh`） | 提示后继续 |
| 7 | tmux 与 Oh my tmux! | 提示后继续 |

所有步骤都按可重跑方式实现。必需步骤失败时，修复原因后重新执行 `setup.sh`；可容忍步骤失败时，也可单独执行对应模块。

## 两台主机的 Pi 链接功能

**只运行 `bash fresh-install/setup.sh` 不够。** 它编排上述七项 Ubuntu 基础安装；tmux 模块若成功会顺带链接 Pi 状态扩展，但不会安装 Pi 本体、客户端链接助手、Tailscale，或配置两机身份和 SSH 密钥。以下命令均从各自主机的 quick-deploy 仓库根目录运行：

| 顺序 / 主机 | 另需完成 |
| --- | --- |
| 1. 公司机与 Y9000p（适用 Ubuntu 24.04+/amd64 时） | 分别运行 `bash fresh-install/setup.sh`；检查提示后继续的步骤是否实际成功。 |
| 2. 两机互联 | 如走 Tailnet，分别运行 `bash tailscale/install.sh` 并按 [Tailscale 说明](../tailscale/README.md)完成入网；准备双向非交互 SSH，连接要求见[两机说明](../pi-agent/README.md#客户端链接公司机--y9000p)。 |
| 3. 每个运行 Pi 的主机（双向使用时两机都做） | 按 [Pi 安装说明](../pi-agent/README.md#一键安装)准备 Node.js、npm 和 Pi，用 `pi --version` 核验；在已更新、已审阅的 checkout 执行 `bash pi-agent/install.sh`，安装扩展并保存全局 fullscreen 默认值。 |
| 4. 每个点击来源桌面（公司机与 Y9000p） | 在匹配且已审阅的 checkout 单独运行 `bash pi-agent/extensions/pi-copy-links/client/install.sh`；Pi 主入口不会执行这个嵌套安装器。桌面环境、替换条件与逐台校验见[助手安装说明](../pi-agent/extensions/pi-copy-links/client/README.md#install-on-each-desktop-and-check-versions)。 |
| 5. 启用与验收 | 核对各 Pi 扩展和两台桌面助手的源码、软链接与 SHA-256；匹配后由用户在对应 Pi **空闲时** `/reload` 或重启。新会话默认 fullscreen，已有会话确认 `/settings` → `fullscreen`。两个方向都用 Ctrl+鼠标左键点击另一服务器的路径，核对 Code 的 SSH 主机与内容；见[操作说明](../pi-agent/extensions/pi-copy-links/README.md#使用在点击来源的桌面打开)。 |

主机角色、SSH 别名、Code Remote-SSH 和连接验证以 [Pi 两机说明](../pi-agent/README.md#客户端链接公司机--y9000p)为准。链接功能不依赖可选的[远端剪贴板桥](../pi-agent/extensions/pi-remote-clipboard/README.md)。

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

各模块自行定位仓库共享库，不依赖启动目录。Ghostty 模块按约定只保留 `install.sh` 一个 shell 源文件：它生成 zsh hook，并与相邻 Wayland C 源码一起交付；测试因此放在模块外的 `tests/ghostty/`。其他模块的测试仍随模块放置。参数、持久配置与恢复说明见 [Ghostty](modules/ghostty/README.md) 和 [tmux](modules/tmux/README.md)。

## 语音输入（现成软件与备选集成）

[现成软件说明](docs/voice-input-software.md)比较 Nextalk 与 Handy，并给出 Nextalk 的旧 Toggle 流程试用步骤。新增的“Ctrl长按3秒、松手停止、文字边说边进入Pi”不能仅靠这两款已核查版本的原生设置完成；文档区分实时浮窗预览与目标输入框投递，列出必要适配。

[客户端集成方案](docs/voice-input-plan.md)保留自行包装 Fcitx5-vinput 的备选设计，包含模块归属、版本与文本过滤要求、平台边界和验收顺序。

目前本仓库没有语音安装模块，`setup.sh` 也未增加语音步骤；现成应用可按其说明单独试用，文档链接不代表已经部署或验收。

## 共享实现

`lib/apt-lock-wait.sh` 被一键入口和所有执行 APT 操作的模块共同加载。它只等待真实持锁进程结束，不删除锁文件、不终止系统更新。保留模块自己的加载逻辑，是为了让模块脱离 `setup.sh` 单独运行时仍具备相同的首开机可靠性。

## 回归测试入口

从仓库根目录执行；Ghostty 和 tmux 的测试条件见各自模块说明。

```bash
bash fresh-install/modules/chinese-input-method/tests/run.sh
bash fresh-install/modules/tmux/tests/run.sh
env -u LD_PRELOAD bash fresh-install/tests/ghostty/run.sh
```
