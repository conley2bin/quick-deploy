# pi-agent

本目录是 quick-deploy 仓库中 Pi 功能及其专属组件、skills 和第三方补丁的源码归属地。

## 一键安装

先单独准备 Node.js ≥22.19、npm 和 PATH 上可用的 Pi（例如 `npm install -g --ignore-scripts @earendil-works/pi-coding-agent@latest`），用 `pi --version` 核验。在已克隆的仓库根目录执行：

```bash
bash pi-agent/install.sh            # 安装扩展，保存全局 fullscreen 默认值
bash pi-agent/install.sh --skills   # 同时交互式安装 skills（菜单选择 + API key 提示）
bash pi-agent/install.sh --skills-only # 仅交互式选择/复制 skills，不碰扩展或 fullscreen 设置
pi                                 # 新会话直接使用 fullscreen；无需另做 /settings
```

本安装器不安装 Node.js、npm、Pi 本体、客户端链接助手或桌面应用；目标 Ubuntu 主机须已有 GNU coreutils（用于保留设置文件元数据）。

行为：

- 仅浅层发现 `extensions/*/install.sh`，逐个执行；新增带 `install.sh` 的 Pi 功能即自动纳入，无需改本脚本。嵌套在功能目录内的组件不会被一键安装器单独执行或链接，按各功能 README 的步骤安装。Pi home 无需初始化 Git；是 Git worktree 时才做对应安装器的 index/exclude 保护。
- 各扩展安装器都是幂等的：已安装的跳过；旧 checkout 或旧名字的受管链接先备份再迁移；外部文件/目录/链接一律拒绝覆盖。
- 默认不碰 skills（其安装器是交互式的）；`--skills` 随扩展一起安装，`--skills-only` 只运行现有技能菜单/复制流程并退出，不调用扩展安装或全局设置写入。只选需要的技能即可保留其他已安装项；支持 `PI_AGENT_SKILLS_DIR` / `PI_AGENT_SKILLS_ZSHRC` 指定隔离目标。
- 扩展安装路径的所有步骤成功后，只将全局 `settings.json` 的 `tuiMode` 保存为 `fullscreen`（`--skills-only` 不执行此步骤）：位置为 `PI_CODING_AGENT_DIR/settings.json`，未指定时为 `$HOME/.pi/agent/settings.json`。已有全局 `regular` 会有意改为 `fullscreen`；其他字段保留，已是 `fullscreen` 时不重写文件。
- 首次创建设置应在首次启动 Pi **之前**运行；更新已有设置时与 Pi 共用锁，在锁内读取、合并并原子写入。保留已有软链接及其目标文件的权限、所有者、ACL 和扩展属性；无效/非对象 JSON、断开的软链接、需要修改的多硬链接、不可写文件或锁/写入失败会明确报错，不会重置配置。支持 UTF-8 BOM 和带空格的 Pi home。
- 项目 `.pi/settings.json` 与启动参数（如 `pi --tui-mode regular`）仍可覆盖全局默认，本脚本不改它们。脚本也不切换当前 TUI、不 reload 或重启 Pi；已有会话需在空闲时手动 `/reload` 或重启来加载更新，确认当前模式仍可用 `/settings`。

## 客户端链接：公司机 + Y9000p

先在每台适用的 Ubuntu 24.04+/amd64 主机运行 [基础安装](../fresh-install/README.md)；如经 Tailnet，分别按 [Tailscale 说明](../tailscale/README.md)安装、授权并验证互通。`fresh-install/setup.sh` 不安装 Pi 本体或客户端链接助手。以下从**各自主机的仓库根目录**执行：

| 角色（公司机与 Y9000p 均可兼任） | 安装与作用 |
| --- | --- |
| 每个运行 Pi 的服务器 | 单独准备 Node.js ≥22.19、npm 和 Pi，用 `pi --version` 核验。先更新已审阅扩展源码，再执行 `bash pi-agent/install.sh` 安装扩展并保存全局 fullscreen 默认值；之后新启动 Pi 无需另设模式。`pi-copy-links` 按实际运行时契约自动判定兼容，不要求逐版更新名单。Pi 不自动重载。 |
| 每个提供点击桌面的主机 | 在匹配的**已审阅 checkout** 执行 `bash pi-agent/extensions/pi-copy-links/client/install.sh`。同一 checkout 的旧路径软链接自动迁移；独立版本/外来软链接须有意加 `--replace`，普通文件/目录始终拒绝。这只链接当前 checkout，不取代码、不远程安装、不运行助手；详见[助手说明](extensions/pi-copy-links/client/README.md#install-on-each-desktop-and-check-versions)。 |

两机双向使用时，两机均需完成上表两项。实际 OS hostname（大小写归一）固定选择另一端，继承的 `HOSTNAME`/SSH/DISPLAY 或目标路径不能改角色：

| Pi 服务器 | 点击桌面 | 桌面 Code 的文件 authority |
| --- | --- | --- |
| `conley-company` | `conley-Y9000P` | `ssh-remote+conley-company` |
| `conley-Y9000P` | `conley-company` | `ssh-remote+conley-Y9000p` |

公司机须配置 `conley-Y9000p`，Y9000p 须配置 `conley-company`：两端 `ssh -G <对端别名>` 应解析为正确的**字面 IP**、`conley` 用户和端口，两端 SSH 服务（缺失时须单独安装/启用）、授权密钥与经核验指纹的 `known_hosts` 均应就绪。分别执行 `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ClearAllForwardings=yes <对端别名> true` 验证非交互双向连接。每个桌面还须有 `code` CLI/VS Code Remote-SSH 对另一服务器的连接、受支持的默认浏览器、活跃的本地图形会话和可由 user systemd 提供的桌面环境；助手不以 `xdg-open` 兜底。

逐台比对已审阅的 `pi-copy-links` 服务器源码与安装软链接，并比对 `client/pi-client-link-helper` 与**两台桌面已安装可执行文件**的 SHA-256/软链接目标；checksum/隔离测试命令见[助手说明](extensions/pi-copy-links/client/README.md#install-on-each-desktop-and-check-versions)。旧 checkout 上重跑安装器不会升级它的源码；只安装助手也不会更新另一服务器缺失的自动路径 renderer。确认扩展与助手匹配后，才由用户在各 Pi **空闲时** `/reload` 或重启；一键安装后的新会话默认 `fullscreen`，已有会话仍需确认当前模式。以所在桌面的 Ctrl+鼠标左键激活另一服务器上的助手 Markdown 路径；见 [pi-copy-links 操作说明](extensions/pi-copy-links/README.md)。文件只在 Pi 服务器侧 `stat`，同名桌面路径不改变资源 kind 或服务器。未知实际主机拒绝配对 SSH，已证明的本机 Ghostty 输入保留本机行为；失败不会改在错误桌面打开。

[pi-remote-clipboard](extensions/pi-remote-clipboard/README.md) 有自己的 provider/SSH 转发；本目录的一键安装会发现它的扩展安装器，但链接功能不依赖剪贴板桥。

## 布局约定

根目录按 Pi 资源种类划分，每类保留自己的安装入口。`extensions/` 的每个子目录是一个 Pi 功能，其顶层 `install.sh` 由一键安装器执行；功能所需的其他组件无论运行在哪台主机或哪个宿主，都放在功能目录内，由功能 README 写明部署步骤。一键安装器只发现 `extensions/*/install.sh`，从不进入更深层；功能安装器仍可部署其自有组件。

| 路径 | 内容 | 部署方式 |
| --- | --- | --- |
| `README.md`、`install.sh`、`configure-fullscreen.mjs`、`test/` | 本目录说明、一键安装器、其私有的全局 fullscreen 写入步骤及隔离测试 | `bash pi-agent/install.sh` |
| `extensions/<功能>/` | 一个 Pi 功能的 Pi 入口及专属组件 | 顶层 `install.sh` 创建 `~/.pi/agent/extensions/<name>` 受管链接；Pi home 是 Git worktree 时做 index/exclude 保护 |
| `extensions/pi-copy-links/client/` | 配对桌面的链接接收助手 | 每个提供桌面的主机单独运行 `client/install.sh` → `~/.local/bin/pi-client-link-helper` |
| `extensions/pi-copy-links/vscode-video-preview-repair/` | 公司本机 Code 的可选 MP4 预览修复（VS Code UI 扩展） | 单独[打包 VSIX 安装到 Code](extensions/pi-copy-links/vscode-video-preview-repair/README.md)；只修复无文件夹的本地文件窗口，链接打开不依赖它 |
| `extensions/pi-remote-clipboard/{src,pi-extension}/` | 本机 provider（systemd user service）与 Pi 入口 | 由该功能顶层 `install.sh` 一并安装 |
| `extensions/pi-inline-images/{patches,vendor}/` | 与本功能桥接协议共同演进的 pi-tmux-images 补丁与 vendored 代码 | 手动 guarded replay（见功能 README） |
| `patches/<包>-<版本>/` | 不属于任何自有功能的第三方 Pi 包修复 | 手动 guarded replay，仅适用于指定版本；`apply.sh --check` 先核验版本与哈希 |
| `skills/` | 自有技能 `skills/<name>/`；vendored `skills/<来源>/<name>/` + `UPSTREAM.txt` | `install.sh --skills-only` 调用现有 `skills/install-skills.sh`，交互式选中后复制到 `~/.pi/agent/skills/` |

`skills/responsibility-registry/` 是全局责任登记技能的源码；安装时在 `--skills-only` 菜单只选 `responsibility-registry`。`~/.pi/agent/skills/responsibility-registry/` 是安装副本，不在该目录开发。运行态登记簿位于 `~/.pi/agent/.memory/local/responsibility-registry/`，不随源码或安装复制；已有 Pi 会话 `/reload` 后加载更新。

`pi-copy-links` 提供代码块右下角复制按钮和 Ctrl+左键打开网页及 Pi 服务器文件；需使用 Pi 的 `fullscreen` 模式。扩展运行时及其单独的叶安装器不修改全局设置；全局 fullscreen 默认策略由本目录的一键安装器负责。版本号仅作诊断：TUI 启动时在自有隔离对象上检查实际接口与关键行为，契约成立即启用，无需用户逐版验证或编辑版本名单。具体接口/行为不兼容时只停用增强并提示原因，原生 Markdown/Pi 保持可用；`/copy-links` 查看实际状态。更新源码后须在 Pi 空闲时手动 `/reload` 或重启才会生效，详见 [使用说明](extensions/pi-copy-links/README.md)。

一键安装器的隔离测试用 `node --test pi-agent/test/installer.test.mjs`（从仓库根目录，需已安装 Pi 的锁依赖）；测试执行临时源码副本中的主入口，覆盖所有真实叶安装器的普通非 Git home 安装/重跑、顶层功能安装入口约定、含嵌套组件的实际 Pi 入口解析边界，以及受控失败；`npm` 和 `systemctl` 使用只记录调用的替身，不启动 Pi、不下载依赖或操作真实用户服务。扩展测试在各自 `test/` 下，例如 `extensions/pi-suspend-guard/test/run.sh`、`extensions/pi-tmux-window-status/test/installer.sh`；`pi-copy-links` 使用 `npm run check`。其[本地诊断命令](extensions/pi-copy-links/README.md#开发者诊断与本地复现)用于开发/排查，不是用户逐版升级的验证任务。
