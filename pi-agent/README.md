# pi-agent

本目录是 quick-deploy 仓库中 Pi 侧资源（extensions / skills）的源码归属地。

## 一键安装

```bash
./install.sh            # 安装所有带 install.sh 的扩展
./install.sh --skills   # 之后再交互式安装 skills（菜单选择 + API key 提示）
```

行为：

- 自动发现 `extensions/*/install.sh`，逐个执行；新增带 `install.sh` 的扩展即自动纳入，无需改本脚本。
- 各扩展安装器都是幂等的：已安装的跳过；旧 checkout 或旧名字的受管链接先备份再迁移；外部文件/目录/链接一律拒绝覆盖。
- 默认不碰 skills（其安装器是交互式的）；不加 `--skills` 时只打印提示。
- 任何一步失败立即中止；脚本不会 reload 或重启 Pi，完成后按提示在 Pi 里执行 `/reload`。

## 客户端链接：公司机 + Y9000p

先在每台适用的 Ubuntu 24.04+/amd64 主机运行 [基础安装](../fresh-install/README.md)；如经 Tailnet，分别按 [Tailscale 说明](../tailscale/README.md)安装、授权并验证互通。`fresh-install/setup.sh` 不安装 Pi 本体或客户端链接助手。以下从**各自主机的仓库根目录**执行：

| 主机 | 安装与作用 |
| --- | --- |
| 公司机（运行 Pi） | 单独安装 Node.js ≥22.19 和受支持的 Pi，例如 `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.99.1`；用 `pi --version` 核验，再执行 `bash pi-agent/install.sh` 安装 `extensions/*/install.sh`。Pi 不自动重载。 |
| Y9000p（提供桌面） | 更新到与公司助手源码匹配的**已审阅 checkout**，执行 `bash pi-agent/client-link-helper/install.sh`。这只把当前 checkout 的助手链接到 `~/.local/bin/`，不会安装 Pi 扩展、从公司机获取更新或覆盖外来软链接；详见[助手说明](client-link-helper/README.md#install-and-version-check)。 |

公司机须配置 `conley-Y9000p`，Y9000p 须配置 `conley-company`：两端 `ssh -G <对端别名>` 应解析为正确的**字面 IP**、`conley` 用户和端口，两端 SSH 服务（缺失时须单独安装/启用）、授权密钥与经核验指纹的 `known_hosts` 均应就绪。分别执行 `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes <对端别名> true` 验证非交互双向连接。Y9000p 还须有 `code` CLI/VS Code Remote-SSH 对公司机的连接、`xdg-open` 默认浏览器，以及活跃的本地图形会话和可由 user systemd 提供的桌面环境。

在公司机比对 `pi-agent/client-link-helper/pi-client-link-helper` 与 Y9000p **已安装可执行文件**的 SHA-256，并核对后者的软链接目标；操作见[助手说明](client-link-helper/README.md#install-and-version-check)。旧 Y9000p checkout 上重跑安装器不是升级。确认匹配后才在公司机 Pi **空闲时** `/reload`，把 `/settings` 的 TUI mode 设为 `fullscreen`，以 Y9000p 的 Ctrl+鼠标左键激活助手 Markdown 链接；见 [pi-copy-links 操作说明](extensions/pi-copy-links/README.md)。代码中配对的主机、别名和用户是固定的；换机器或用户必须有意适配，失败不会改在公司桌面打开。

[pi-remote-clipboard](extensions/pi-remote-clipboard/README.md) 有自己的 provider/SSH 转发；本目录的一键安装会发现它的扩展安装器，但链接功能不依赖剪贴板桥。

## 布局约定

| 路径 | 内容 | 部署方式 |
| --- | --- | --- |
| `extensions/<name>/` | 扩展源码（`index.ts` 入口） | 各自的 `install.sh` 创建 `~/.pi/agent/extensions/<name>` 受管符号链接；PI home 是 git worktree 时同时写入 `info/exclude` |
| `skills/` | 技能源码（`SKILL.md`） | `skills/install-skills.sh` 交互式复制到 `~/.pi/agent/skills/` |

`pi-copy-links` 提供代码块右下角复制按钮和 Ctrl+左键打开网页及公司文件；需使用 Pi 的 `fullscreen` 模式。它不修改全局设置，源码精确支持 Pi 0.85.1、0.86.1、0.87.1、0.99.1；更新源码后须在 Pi 空闲时手动 `/reload` 或重启才会生效，详见 [使用说明](extensions/pi-copy-links/README.md)。

扩展测试在各自 `test/` 下，例如 `extensions/pi-suspend-guard/test/run.sh`、`extensions/pi-tmux-window-status/test/installer.sh`；`pi-copy-links` 使用 `npm run check`。
