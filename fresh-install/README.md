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
| 2. 两机互联 | 如走 Tailnet，分别运行 `bash tailscale/install.sh` 并完成入网；依照 [Tailscale 说明](../tailscale/README.md)验证双向连通。确保两端 SSH 服务可用，配置双向授权密钥和已核验指纹的 `known_hosts`：公司机上的 `conley-Y9000p`、Y9000p 上的 `conley-company` 均须非交互可用。 |
| 3. 每个运行 Pi 的主机（双向使用时两机都做） | 单独安装 Node.js ≥22.19、npm 和 Pi（例如 `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest`），用 `pi --version` 核验。先更新已审阅的服务器扩展源码，再运行 `bash pi-agent/install.sh` 安装扩展并保存全局 fullscreen 默认值；见 [Pi 安装说明](../pi-agent/README.md)。 |
| 4. 每个点击来源桌面（公司机与 Y9000p） | 使用匹配且已审阅的仓库 checkout，分别运行 `bash pi-agent/extensions/pi-copy-links/client/install.sh`；同一 checkout 的旧路径链接自动迁移，独立版本/外来软链接须显式加 `--replace`。各桌面准备受支持的默认浏览器、VS Code Remote-SSH 对另一服务器的连接和可用的桌面会话环境，见 [助手安装、checksum 与隔离测试](../pi-agent/extensions/pi-copy-links/client/README.md#install-on-each-desktop-and-check-versions)。 |
| 5. 启用与验收 | 核对各 Pi 的已安装扩展源码/软链接，以及两台桌面已安装助手与已审阅源码的 SHA-256/软链接目标。匹配后由用户在对应 Pi **空闲时**执行 `/reload` 或重启；新会话默认 fullscreen，已有会话确认 `/settings` → `fullscreen`。两个方向都以 Ctrl+鼠标左键点击另一服务器的路径，并确认 Code 的 SSH 主机和文件内容；见 [链接操作说明](../pi-agent/extensions/pi-copy-links/README.md)。 |

角色由实际 OS hostname（大小写归一）固定选择：Y9000p 桌面→公司 Pi 的文件使用 `ssh-remote+conley-company`；公司桌面→Y9000p Pi 的文件使用 `ssh-remote+conley-Y9000p`。服务器侧 `stat` 决定文件/目录，桌面同名路径不能改变目标。未知机器明确拒绝配对 SSH，继承环境和请求不能指定主机。

两端还应分别用 `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes <对端别名> true` 验证免交互认证；别名须解析为正确的字面 IP、`conley` 用户和端口。助手安装器只链接**执行它的当前 checkout**，不会取代码、远程安装或运行助手，也不会升级旧 checkout；只装助手不会更新 Pi 服务器的旧 renderer/自动路径识别。外来软链接默认拒绝，只有有意指定 `--replace` 才重指向，普通文件/目录即使带此参数也拒绝。主入口在两机上的浅层扩展扫描均不执行嵌套的 `client/install.sh`，它必须在每个桌面单独运行。其他机器、用户或 SSH 别名需要有意适配两端配对，不会靠单次基础安装自动生效。安装器不重载或注入运行中的用户会话。链接功能无需可选的[远端剪贴板桥](../pi-agent/extensions/pi-remote-clipboard/README.md)；它有自己的 provider 和 SSH 转发。

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

各模块自行定位仓库共享库，不依赖从哪个工作目录启动。Ghostty 模块只保留 `modules/ghostty/install.sh` 一个 shell 源文件；zsh 运行时 hook 从它生成，Wayland C 源码与它一起打包，测试放在 `fresh-install/tests/ghostty/`。Ghostty 的库编译、启动器和桌面集成都由该安装入口一次完成。Ghostty 和 tmux 的参数、持久配置及恢复语义见各自目录中的 `README.md`。

## 语音输入（现成软件与备选集成）

[现成软件说明](docs/voice-input-software.md)比较 Nextalk 与 Handy，并给出 Nextalk 的旧 Toggle 流程试用步骤。新增的“Ctrl长按3秒、松手停止、文字边说边进入Pi”不能仅靠这两款已核查版本的原生设置完成；文档区分实时浮窗预览与目标输入框投递，列出必要适配。

[客户端集成方案](docs/voice-input-plan.md)保留自行包装 Fcitx5-vinput 的备选设计，包含模块归属、版本与文本过滤要求、平台边界和验收顺序。

目前本仓库没有语音安装模块，`setup.sh` 也未增加语音步骤；现成应用可按其说明单独试用，文档链接不代表已经部署或验收。

## 共享实现

`lib/apt-lock-wait.sh` 被一键入口和所有执行 APT 操作的模块共同加载。它只等待真实持锁进程结束，不删除锁文件、不终止系统更新。保留模块自己的加载逻辑，是为了让模块脱离 `setup.sh` 单独运行时仍具备相同的首开机可靠性。
