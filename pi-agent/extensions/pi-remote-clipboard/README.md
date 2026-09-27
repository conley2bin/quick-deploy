# pi-remote-clipboard：SSH 场景下 Pi 的剪贴板桥

两台机器都安装同一个模块，同时承担**提供者**和**请求者**两种角色。

- **提供者**：本机 localhost 上的一个小服务，被请求时读取或写入本机剪贴板，支持文本和图片
- **请求者**：Pi 在检测到当前输入来自 SSH 终端时，通过 SSH remote forwarding 访问对端提供者

## 方向与按键

| 方向 | 内容 | 机制 | 用户操作 |
|---|---|---|---|
| 复制（远端 Pi → 客户端） | 文本 | Pi 复制动作自动改走 OSC 52，经 tmux/SSH 到客户端剪贴板 | 照常拖选/点按钮，无需新键 |
| 复制（远端 Pi → 客户端） | 图片 | `/rpush [图片路径]` 经桥写入客户端剪贴板 | 显式命令 |
| 粘贴（客户端 → 远端 Pi） | 文本 | Ghostty 本地粘贴 | `Ctrl+Shift+V` |
| 粘贴（客户端 → 远端 Pi） | 文本/图片 | 桥拉取客户端剪贴板 | `Ctrl+Alt+V` |

`Ctrl+V` 保持 Pi 原生语义：读取 **Pi 所在机器**的剪贴板（本地富粘贴），不被本模块接管。

## 解决的问题

- Pi 的复制默认写入 **Pi 进程所在机器**的剪贴板，SSH 场景下客户端拿不到；
- Wayland 下 `wl-copy`/`wl-paste` 在 GNOME 上会因焦点限制卡死（已实测）；
- `Ctrl+Shift+V` 只能传文本，不能传图片。

## 安装

```bash
bash pi-agent/extensions/pi-remote-clipboard/install.sh
```

安装器会：

1. 安装本机 provider 为 systemd user service（`pi-remote-clipboard.service`）；
2. 安装 Pi 扩展 `pi-remote-clipboard` 到 `~/.pi/agent/extensions/`；
3. 在 `.zshrc` 中注册 SSH remote forwarding 函数（`ssh-clipboard`），自动带 `-R` 参数；
4. 不修改 `settings.json`、不改 Pi bundle、不改 tmux/Ghostty 配置。

## 使用

### 本地 Pi

```bash
piox
```

`Ctrl+V` 走 Pi 原生路径，直接读本机剪贴板。

### SSH Pi

```bash
ssh <远端主机>
piox
```

- 复制（拖选、`[复制]` 按钮、`Ctrl+X`、`/copy`）：自动写入对端剪贴板（OSC 52）；
- `Ctrl+V`：保持 Pi 原生（本地剪贴板）；
- `Ctrl+Alt+V`：拉取对端剪贴板（文本直接插入，图片保存为 `/tmp/pi-remote-clipboard-<uuid>.<ext>` 并插入路径）；
- `/rpush`：把本机剪贴板（图片优先）推送到对端；`/rpush /path/to/img.png`：推送指定图片文件。

### 判定规则

| 场景 | 判定方式 |
|---|---|
| Pi 不在 tmux | 进程祖先链里有 `sshd` |
| Pi 在 tmux | 当前输入对应的 tmux client 祖先链里有 `sshd` |
| 拿不到 client 信息 | 保守地按本地处理 |

## 协议

提供者监听 `127.0.0.1:37843`（可用 `PI_REMOTE_CLIPBOARD_PORT` 覆盖）。SSH remote forwarding 在对端开 `127.0.0.1:37844` 指回本机 37843；两个端口刻意不同，避免对端的 forward 与本机 provider 抢同一个监听口。

请求（JSON Lines over TCP）：

```json
{"action":"read"}
{"action":"write","kind":"text","text":"..."}
{"action":"write","kind":"image","mime":"image/png","b64":"..."}
```

响应：

```json
{"kind":"text","text":"..."}
{"kind":"image","mime":"image/png","b64":"..."}
{"kind":"empty"}
{"kind":"ok"}
{"kind":"error","message":"..."}
```

## 边界

- 只支持文本和常见图片 MIME（png/jpeg/webp/gif）；
- 图片默认上限 32 MiB，文本默认上限 1 MiB；
- 不写入远端剪贴板；
- 不保存剪贴板历史；
- 不监听非 localhost 地址；
- 不主动同步；
- Wayland 下依赖 `wl-paste`/`wl-copy`；X11 下依赖 `xclip`；
- 如果 `wl-paste` 在 GNOME Wayland 下卡住，provider 的读取会超时并返回空或错误，不会无限等待；
- remote forwarding 只在 SSH 会话期间有效，断开后自动失效；
- 同一 Pi session 同时被本地和 SSH client 连接时，按当前输入 client 分别判定。

## 测试

```bash
python3 pi-agent/extensions/pi-remote-clipboard/tests/test_all.py
```

隔离测试不启动真实 provider、不读真实剪贴板、不启动 SSH。
