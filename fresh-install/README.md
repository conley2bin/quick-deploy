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
| 3. 公司机（Pi 所在端） | 单独安装 Node.js ≥22.19 和兼容版本 Pi（推荐 `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.87.1`），再运行 `bash pi-agent/install.sh` 安装扩展；见 [Pi 安装说明](../pi-agent/README.md)。 |
| 4. Y9000p（点击来源桌面） | 使用匹配且已审阅的仓库 checkout 运行 `bash pi-agent/extensions/pi-copy-links/client/install.sh`；同一 checkout 的旧路径链接自动迁移，独立版本/外来软链接须显式加 `--replace`。准备默认浏览器、VS Code Remote-SSH 和可用的桌面会话环境，见 [助手安装、checksum 与隔离测试](../pi-agent/extensions/pi-copy-links/client/README.md#install-and-version-check)。 |
| 5. 启用 | 对比公司机助手源码与 Y9000p 已安装助手的 SHA-256 及软链接目标；匹配后在公司机 Pi **空闲时**执行 `/reload`，设 `/settings` → `fullscreen`，从 Y9000p 以 Ctrl+鼠标左键点击助手 Markdown 链接；见 [链接操作说明](../pi-agent/extensions/pi-copy-links/README.md)。 |

两端还应分别用 `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes <对端别名> true` 验证免交互认证；别名须解析为正确的字面 IP、用户名和端口。助手安装器只链接**当前 Y9000p checkout**，不会取代码、远程安装或运行助手，也不会升级旧 checkout；外来软链接默认拒绝，只有有意指定 `--replace` 才重指向，普通文件/目录即使带此参数也拒绝。公司主入口的浅层扩展扫描不执行嵌套的 `client/install.sh`。其他机器、用户或 SSH 别名需要有意适配两端配对，不会靠单次基础安装自动生效。链接功能无需可选的[远端剪贴板桥](../pi-agent/extensions/pi-remote-clipboard/README.md)；它有自己的 provider 和 SSH 转发。

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

## 共享实现

`lib/apt-lock-wait.sh` 被一键入口和所有执行 APT 操作的模块共同加载。它只等待真实持锁进程结束，不删除锁文件、不终止系统更新。保留模块自己的加载逻辑，是为了让模块脱离 `setup.sh` 单独运行时仍具备相同的首开机可靠性。
