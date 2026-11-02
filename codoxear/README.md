# Codoxear

Codoxear 是本机 Codex/Pi 会话的浏览器接力工具，不是 MCP server。对外只提供两个脚本：

```bash
./setup.sh       # 首次配置，包含共享 Web 宿主的独立依赖
./run_server.sh  # 拉取远程 main，启动负责人分组 + 会话接力
```

`setup.sh` 会向 `.bashrc` 或 `.zshrc` 写入 `codox`/`piox` 函数，用于启动可被 Web 接力的终端会话。原始 `codex` 和 `pi` 命令不会被替换。

上游：<https://github.com/yiwenlu66/codoxear>

## 目录职责

```text
setup.sh
run_server.sh
.internal/       # 两个入口调用的内部实现，不直接运行
.runtime/        # upstream checkout、独立 venv、安装状态；Git 忽略
.env             # 本机配置；Git 忽略
web-host/        # 共享 HTTP 宿主、私有上游生命周期、冻结脚本资源、独立 .venv
owner-groups/    # 默认启用的负责人分组 feature（保留旧 proxy.py 入口）
session-handoff/ # 默认启用的 Pi 会话接力 feature，与分组并列维护
```

`.internal/` 包含远程 main 同步、安装、dotenv 加载、server/broker 启动和 shell rc 更新逻辑。它不是第三组用户命令。

## 首次配置

```bash
cd /path/to/quick-deploy/codoxear
./setup.sh
```

首次运行会：

1. clone/fetch `https://github.com/yiwenlu66/codoxear` 的远程 `main`。
2. 从当前远程 main 的 `.env.example` 创建本地 `.env`。
3. 创建 `.runtime/venv`、安装当前远程 main。
4. 自动调用 `web-host/setup.sh`，在 `web-host/.venv` 安装共享宿主的锁定依赖（aiohttp 3.13.5），再写入 `codox`/`piox` shell 函数。无需为两个 feature 分别建 venv；依赖安装失败会明确中止。升级已有安装也重新运行 `./setup.sh`，旧 `owner-groups/.venv` 不会被移动或删除。

setup 不启动、停止或重启任何服务。已有在线进程仍保留原行为，需安排 Web 重启才切换到新默认。

setup 不要求设置 Web 密码；未设置密码时，terminal-owned 的 `codox`/`piox` 仍可使用。准备启动 Web server 时再编辑：

```text
codoxear/.env
```

至少设置：

```dotenv
CODEX_WEB_PASSWORD=your-password
```

密码只在 `run_server.sh` 启动 Web server 时校验。setup 完成后按提示重新加载 `.bashrc` 或 `.zshrc`。

自定义上游仓库可以在首次运行时指定：

```bash
./setup.sh --repo-url https://github.com/yiwenlu66/codoxear.git
```

也可以设置环境变量 `CODOXEAR_REPO_URL`。

## 启动 Web server

```bash
./run_server.sh
```

如果 `CODEX_WEB_PASSWORD` 仍为空或为上游占位值 `change-me`，此时会明确失败并提示修改 `.env`。

每次成功启动前都执行：

1. fetch `origin/main`。
2. 精确读取 `refs/remotes/origin/main` 最新 commit。
3. 将 runtime checkout 切换到该 commit。
4. commit 变化时重新安装。
5. 在终端打印本次 revision、浏览器地址、监听范围、codox/piox、网页新建会话、tmux、Delete、停止方式和 HTTP/HTTPS 边界。
6. 默认启动 `web-host/host.py`，明确安装负责人分组和会话接力，由唯一宿主在私有 loopback 启动同一环境中的 `codoxear-server`；前端仍使用原 host/port/prefix/登录。显式 `--no-owner-groups` 或 `--no-extensions` 则直接启动原生 server，不加载宿主或任何 feature。

启动说明基于当前 `.env` 的实际 host、port、URL prefix、默认后端和 secure-cookie 配置生成，不显示密码值。

如果远程不可达、远程 main 无法解析，或 runtime checkout 有本地修改，启动会直接失败，不使用无法证明为最新的旧版本。共享宿主依赖会在更新上游前检查；缺失时提示运行 `./setup.sh` 并中止，不产生 updater 副作用。宿主启动时缺少任何一个 feature 或其资源都会报错，不静默省略功能或切换到原生服务。

默认访问地址由上游决定，当前为：

```text
http://<host>:8743
```

上游 `.env.example` 默认注释了 `CODEX_WEB_HOST`，因此 server 默认绑定 `::`。如果只通过本机、SSH tunnel 或 Tailscale Serve 访问，建议在 `.env` 加入：

```dotenv
CODEX_WEB_HOST=127.0.0.1
```

## 使用 terminal-owned 会话

```bash
cd /path/to/project
codox             # Codex
piox              # Pi
```

参数会原样传递：

```bash
codox <codex args>
piox <pi args>
```

`codox`/`piox` 调用 `.internal/launch.py`。内部 launcher 每次读取本目录 `.env`，并只执行 `.runtime/venv/bin/codoxear-broker`，不会搜索 PATH 中的其他 Codoxear 安装。

## `.env` 来源

`.env` 直接复制自当前远程 main 的上游 `.env.example`，helper 不维护另一份模板。已有 `.env` 永远不会被覆盖。

上游当前模板包括密码、host、port、`CODEX_HOME` 和 `CODEX_BIN`。其他支持项见上游 README 的 Configuration，例如：

```dotenv
PI_HOME=/home/you/.pi
PI_BIN=pi
CODEX_WEB_COOKIE_SECURE=1
CODEX_WEB_DEFAULT_AGENT_BACKEND=pi
```

当前 shell 已设置的环境变量优先于 `.env`。`CODEX_HOME` 和 `PI_HOME` 会在启动前展开 `~`。

## HTTPS

Codoxear 只提供密码门禁，不提供 TLS。需要远程访问时使用 VPN、SSH tunnel 或 HTTPS reverse proxy。例如：

```bash
tailscale serve --bg --yes --https=8443 http://127.0.0.1:8743
```

使用 HTTPS 时在 `.env` 设置：

```dotenv
CODEX_WEB_COOKIE_SECURE=1
```

## 默认：跨电脑/手机的负责人侧栏分组

[owner-groups/README.md](owner-groups/README.md) 记录分组行为与测试；当前共享入口和安装流程以本页及 [web-host/README.md](web-host/README.md) 为准。普通 `./setup.sh` 安装 `web-host/.venv`；无参数 `./run_server.sh` 默认启用两个 feature，旧命令 `./run_server.sh --owner-groups` 与默认行为相同。旧 `owner-groups/proxy.py` CLI 仍可用，内部委托给共享宿主；其 Python 导入接口保留 owner-only 测试/演示兼容。

前端仍使用现有 host/port/prefix/登录，上游不变并运行在私有 loopback 后端；每次启动仍执行既有远程 main 更新流程。显式原生回退（无需适配器依赖）：

```bash
./run_server.sh --no-extensions
# 旧命令等价，仍直接运行原生 server：
./run_server.sh --no-owner-groups
```

**安装源码/依赖不等于当前服务已启用**；已有在线进程不会自动切换，需确认原 Web 服务的启动者和停止方式，在获准维护窗口停止旧入口后再用新命令启动。不要在原端口仍被现有进程占用时重启或杀未知服务；不要停止 broker/agent 来切换 Web 入口。

分组按真实登记的上级关系（不是项目/目录）；最高负责人原生名称后有 ★，缺失上级不冒充顶层。保留原生全部会话和操作；真实登记为空时显示空状态，演示仅使用隔离合成数据。

## 会话接力与共享宿主

会话接力的协议与使用边界见 [session-handoff/README.md](session-handoff/README.md)。只支持 Pi，不自动迁移负责人登记或切换模型提供商。新会话独立恢复、旧会话审阅、一次纠正后续接；不能把 HTTP 接收当成理解完成。旧会话无法审阅时明确显示未校对，不伪装成已通过。

两个 feature 分别使用 `__owner_groups__`、`__session_handoff__` 同源路径和独立 `defer` 脚本，保留各自的 `document.currentScript`。宿主启动时冻结脚本字节和内容 hash；源码变化不会热替换在线资源，必须在批准的 Web 重启后生效。原生 HTML/CSP 不兼容时原样透传，不放宽 CSP；SSE、上传和下载保持流式。

共享宿主只拥有前端和它创建的私有 Web 子进程，不拥有 broker/agent。上线前重新核验实际启动者、监听端口和 Web 子进程归属；仅停止已确认的旧 Web 入口/私有 Web 子进程，再以新命令启动。不要使用文档中的历史 PID 或为切换 Web 去停止会话进程。原生回退同样只切换 Web；不改登录配置或职责登记。宿主端口若已占用，会在创建后端之前失败，不尝试杀占用者。

验证命令与 HostContext 开发接口见 [web-host/README.md](web-host/README.md)。共享宿主的隔离 fake-feature 测试不等于真实 Pi 接力端到端验收。

## 已知限制

- Codex `default`/`plan` 模式的交互式确认不能可靠地从浏览器处理。
- web-owned Codex 会话由上游启用绕过审批和 sandbox 的模式。
- 新建 Codex `/new` 后，UI 可能等待第一次 prompt 才绑定 rollout log。
- web-owned tmux 需要系统安装 `tmux`。
- 删除网页中的 live session 会终止底层 broker 和 agent 进程。
