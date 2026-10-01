# pi-agent

本目录是 quick-deploy 仓库中 Pi 侧资源（extensions / skills）的源码归属地。

## 一键安装

先单独准备 Node.js ≥22.19、npm 和 PATH 上可用的 Pi（例如 `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest`），用 `pi --version` 核验。在已克隆的仓库根目录执行：

```bash
bash pi-agent/install.sh            # 安装扩展，保存全局 fullscreen 默认值
bash pi-agent/install.sh --skills   # 同时交互式安装 skills（菜单选择 + API key 提示）
pi                                 # 新会话直接使用 fullscreen；无需另做 /settings
```

本安装器不安装 Node.js、npm、Pi 本体、客户端链接助手或桌面应用；目标 Ubuntu 主机须已有 GNU coreutils（用于保留设置文件元数据）。

行为：

- 自动发现 `extensions/*/install.sh`，逐个执行；新增带 `install.sh` 的扩展即自动纳入，无需改本脚本。
- 各扩展安装器都是幂等的：已安装的跳过；旧 checkout 或旧名字的受管链接先备份再迁移；外部文件/目录/链接一律拒绝覆盖。
- 默认不碰 skills（其安装器是交互式的）；不加 `--skills` 时只打印提示。
- 所有安装步骤成功后，只将全局 `settings.json` 的 `tuiMode` 保存为 `fullscreen`：位置为 `PI_CODING_AGENT_DIR/settings.json`，未指定时为 `$HOME/.pi/agent/settings.json`。已有全局 `regular` 会有意改为 `fullscreen`；其他字段保留，已是 `fullscreen` 时不重写文件。
- 首次创建设置应在首次启动 Pi **之前**运行；更新已有设置时与 Pi 共用锁，在锁内读取、合并并原子写入。保留已有软链接及其目标文件的权限、所有者、ACL 和扩展属性；无效/非对象 JSON、断开的软链接、需要修改的多硬链接、不可写文件或锁/写入失败会明确报错，不会重置配置。支持 UTF-8 BOM 和带空格的 Pi home。
- 项目 `.pi/settings.json` 与启动参数（如 `pi --tui-mode regular`）仍可覆盖全局默认，本脚本不改它们。脚本也不切换当前 TUI、不 reload 或重启 Pi；已有会话需在空闲时手动 `/reload` 或重启来加载更新，确认当前模式仍可用 `/settings`。

## 客户端链接：公司机 + Y9000p

先在每台适用的 Ubuntu 24.04+/amd64 主机运行 [基础安装](../fresh-install/README.md)；如经 Tailnet，分别按 [Tailscale 说明](../tailscale/README.md)安装、授权并验证互通。`fresh-install/setup.sh` 不安装 Pi 本体或客户端链接助手。以下从**各自主机的仓库根目录**执行：

| 主机 | 安装与作用 |
| --- | --- |
| 公司机（运行 Pi） | 单独准备 Node.js ≥22.19、npm 和 Pi，用 `pi --version` 核验，再执行 `bash pi-agent/install.sh` 安装 `extensions/*/install.sh` 并保存全局 fullscreen 默认值；之后新启动 Pi 无需另设模式。`pi-copy-links` 按实际运行时契约自动判定兼容，不要求逐版更新名单。Pi 不自动重载。 |
| Y9000p（提供桌面） | 更新到与公司助手源码匹配的**已审阅 checkout**，执行 `bash pi-agent/client-link-helper/install.sh`。这只把当前 checkout 的助手链接到 `~/.local/bin/`，不会安装 Pi 扩展、从公司机获取更新或覆盖外来软链接；详见[助手说明](client-link-helper/README.md#install-and-version-check)。 |

公司机须配置 `conley-Y9000p`，Y9000p 须配置 `conley-company`：两端 `ssh -G <对端别名>` 应解析为正确的**字面 IP**、`conley` 用户和端口，两端 SSH 服务（缺失时须单独安装/启用）、授权密钥与经核验指纹的 `known_hosts` 均应就绪。分别执行 `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes <对端别名> true` 验证非交互双向连接。Y9000p 还须有 `code` CLI/VS Code Remote-SSH 对公司机的连接、`xdg-open` 默认浏览器，以及活跃的本地图形会话和可由 user systemd 提供的桌面环境。

在公司机比对 `pi-agent/client-link-helper/pi-client-link-helper` 与 Y9000p **已安装可执行文件**的 SHA-256，并核对后者的软链接目标；操作见[助手说明](client-link-helper/README.md#install-and-version-check)。旧 Y9000p checkout 上重跑安装器不是升级。确认匹配后才在公司机 Pi **空闲时** `/reload`；一键安装后的新会话默认 `fullscreen`，已有会话需确认当前模式（必要时用 `/settings` 设置）。以 Y9000p 的 Ctrl+鼠标左键激活助手 Markdown 链接；见 [pi-copy-links 操作说明](extensions/pi-copy-links/README.md)。代码中配对的主机、别名和用户是固定的；换机器或用户必须有意适配，失败不会改在公司桌面打开。

[pi-remote-clipboard](extensions/pi-remote-clipboard/README.md) 有自己的 provider/SSH 转发；本目录的一键安装会发现它的扩展安装器，但链接功能不依赖剪贴板桥。

## 布局约定

| 路径 | 内容 | 部署方式 |
| --- | --- | --- |
| `extensions/<name>/` | 扩展源码（`index.ts` 入口） | 各自的 `install.sh` 创建 `~/.pi/agent/extensions/<name>` 受管符号链接；PI home 是 git worktree 时同时写入 `info/exclude` |
| `skills/` | 技能源码（`SKILL.md`） | `skills/install-skills.sh` 交互式复制到 `~/.pi/agent/skills/` |

`pi-copy-links` 提供代码块右下角复制按钮和 Ctrl+左键打开网页及公司文件；需使用 Pi 的 `fullscreen` 模式。扩展运行时及其单独的叶安装器不修改全局设置；全局 fullscreen 默认策略由本目录的一键安装器负责。版本号仅作诊断：TUI 启动时在自有隔离对象上检查实际接口与关键行为，契约成立即启用，无需用户逐版验证或编辑版本名单。具体接口/行为不兼容时只停用增强并提示原因，原生 Markdown/Pi 保持可用；`/copy-links` 查看实际状态。更新源码后须在 Pi 空闲时手动 `/reload` 或重启才会生效，详见 [使用说明](extensions/pi-copy-links/README.md)。

一键安装器的隔离测试用 `node --test pi-agent/test/installer.test.mjs`（从仓库根目录，需已安装 Pi 的锁依赖）；测试只执行临时源码副本中的主入口和受控叶安装器，不启动 Pi 或真实包安装。扩展测试在各自 `test/` 下，例如 `extensions/pi-suspend-guard/test/run.sh`、`extensions/pi-tmux-window-status/test/installer.sh`；`pi-copy-links` 使用 `npm run check`。其[维护者兼容 CI](../.github/workflows/pi-copy-links-compat.yml) 在相关 push/PR 和每周计划任务中，以临时 Ubuntu runner 的 npm Pi latest 运行完整回归和离线 native recorder，并保留失败证据；[本地诊断命令](extensions/pi-copy-links/README.md#开发者诊断与本地复现)用于开发/排查，不是用户逐版升级的验证任务。CI 文件须先推送到启用了 Actions 的远端仓库才会执行；本地提交不会启动远端检查。
