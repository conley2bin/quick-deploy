# Codoxear

Codoxear 是本机 Codex/Pi 会话的浏览器接力工具，不是 MCP server。对外只提供两个脚本：

```bash
./setup.sh       # 首次配置
./run_server.sh  # 拉取远程 main 最新版本并启动 Web server
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
3. 创建 `.runtime/venv`、安装当前远程 main，并写入 `codox`/`piox` shell 函数。

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
6. 启动同一环境中的 `codoxear-server`。

启动说明基于当前 `.env` 的实际 host、port、URL prefix、默认后端和 secure-cookie 配置生成，不显示密码值。

如果远程不可达、远程 main 无法解析，或 runtime checkout 有本地修改，启动会直接失败，不使用无法证明为最新的旧版本。

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

## 已知限制

- Codex `default`/`plan` 模式的交互式确认不能可靠地从浏览器处理。
- web-owned Codex 会话由上游启用绕过审批和 sandbox 的模式。
- 新建 Codex `/new` 后，UI 可能等待第一次 prompt 才绑定 rollout log。
- web-owned tmux 需要系统安装 `tmux`。
- 删除网页中的 live session 会终止底层 broker 和 agent 进程。
