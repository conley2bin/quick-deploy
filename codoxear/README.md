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
tests/           # .internal/launch.py 的隔离单元测试
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
helper 自己新增的选项（非上游模板）：下一节的 `PIOX_CLIPBOARD_BACKEND`。

## Pi 的 `Ctrl+V`：`PIOX_CLIPBOARD_BACKEND=x11`

SSH 到远端后 `piox` 进入 Pi 全屏，`Ctrl+V` 由 Pi 进程读取**远端主机**的剪贴板；
`Ctrl+Shift+V` 则由本机 Ghostty 把**本机**剪贴板写进 pty。两者归属不同主机，
这里只调整前者的后端。

Pi 0.86.1 按进程环境选择后端（已对照安装的 `@earendil-works/pi-coding-agent@0.86.1`
源码）：图片粘贴在 `WAYLAND_DISPLAY` 非空或 `XDG_SESSION_TYPE=wayland` 时先等
`wl-paste --list-types` 1 秒；文本粘贴只在 `WAYLAND_DISPLAY` 非空时先等
`wl-paste` 5 秒，失败后才回退 `xclip`。本机两个条件同时成立，所以实测会依次等待约 1 秒和 5 秒。
在 GNOME/XWayland 的 SSH 会话里，`wl-paste` 可能拿不到选区（已实测 `--list-types`
与文本读取均超过 7 秒不返回，而 `xclip` 立即返回），于是每次 `Ctrl+V` 都要等满
这段超时。在 `.env` 中显式选择 X11 后端：

```dotenv
PIOX_CLIPBOARD_BACKEND=x11
```

launcher 读到该值时，**只从 `piox` 入口**做三件事：要求 `DISPLAY` 非空；移除
`WAYLAND_DISPLAY`；把 `XDG_SESSION_TYPE` 设为 `x11`。这样图片与文本 `Ctrl+V`
都直接走 `xclip`，不再有 Wayland 等待。`codox` 与 Web server 不受影响。

- 留空或不写等于 `auto`：环境原样传递，行为与没有这个配置完全相同。
- 其它值（如 `wayland`、拼写错误）：`piox` 在启动任何进程前明确失败并打印当前值，
  不静默回到默认后端。
- `x11` 要求 `DISPLAY` 非空；缺失时同样明确失败。要恢复默认行为就删掉这一行。
- 当前 shell 的环境变量优先于 `.env`，可临时覆盖。
- 只影响新启动的 `piox`；已在运行的 Pi 不受影响，需重启后才生效。

边界：

- 这个选项选择 Pi 的剪贴板后端，不改变复制/粘贴的归属：`Ctrl+V` 仍是 Pi 在远端读剪贴板，`Ctrl+Shift+V` 仍是 Ghostty 在本机粘贴。
- 环境变化会由 `codoxear-broker` 传给 Pi，并继续被 Pi 启动的 shell 命令、扩展子进程和其它普通后代继承；因此 Wayland-only 工具可能不可用，GUI 工具可能改走 XWayland。需要原生 Wayland 的单条命令应显式补回真实的 `WAYLAND_DISPLAY` 与 `XDG_SESSION_TYPE=wayland`。真正只改变 Pi 剪贴板模块需要 Pi 上游提供专用后端开关，本 launcher 无法从父进程环境做到。
- 需要主机上真的存在可用的 X11/XWayland 显示；`DISPLAY` 失效时文本粘贴会失败，
  而不是悄悄走回 Wayland。
- 文本路径已被实测（XWayland 上 `xclip -selection clipboard -out` 立即返回）；
  图片路径改由 `xclip` 的 `TARGETS` 探测决定，能否拿到图片取决于 X11/XWayland
  剪贴板桥暴露哪些目标，本仓库没有实测结论。
- Pi 自己的鼠标释放复制（`copyToClipboard`）也会因此优先用 `xclip`，与该选项
  是同一个后端选择的两面。

隔离单元测试（不读真实 `.env`，不启动 broker/server）：

```bash
python3 tests/test_launch_clipboard.py
```

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
