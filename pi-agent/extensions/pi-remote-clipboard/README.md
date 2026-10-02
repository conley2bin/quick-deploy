# pi-remote-clipboard：SSH 场景下 Pi 的剪贴板桥

两台机器都安装同一个模块，同时承担**提供者**和**请求者**两种角色。

- **提供者**：本机 localhost 上的一个小服务，被请求时读取或写入本机剪贴板，支持文本和图片
- **请求者**：Pi 在检测到当前输入来自 SSH 终端时，通过 SSH remote forwarding 访问对端提供者

## 方向与按键

| 方向 | 内容 | 机制 | 用户操作 |
|---|---|---|---|
| 复制（远端 Pi → 客户端） | 文本 | Pi 复制动作自动改走 OSC 52，经 tmux/SSH 到客户端剪贴板 | 照常拖选/点按钮，无需新键 |
| 复制（远端 Pi → 客户端） | 图片 | `/rpush [图片路径]` 经桥写入客户端剪贴板 | 显式命令 |
| 粘贴（客户端 → 远端 Pi） | 文本/图片 | 在 Pi 的粘贴入口可用时，`Ctrl+V` 按最近活动的 SSH/tmux 客户端分流：SSH 走桥，本地走 Pi 原生 | `Ctrl+V` |
| 粘贴（客户端 → 远端 Pi） | 文本/图片 | 显式通过桥拉取客户端剪贴板，不依赖 Pi 的私有粘贴入口 | `Ctrl+Alt+V` |

`Ctrl+V` 的自动分流依赖 Pi 的私有 `InteractiveMode.handleClipboardPaste`。若 Pi 不再提供可覆盖的入口，扩展会提示且不接管 `Ctrl+V`；`Ctrl+Alt+V` 和 `/rpush` 仍注册。终端自身的粘贴键（如 `Ctrl+Shift+V`）由终端配置决定，不是本扩展的图片桥。

## 解决的问题

- Pi 的复制默认写入 **Pi 进程所在机器**的剪贴板，SSH 场景下客户端拿不到；
- Wayland 下 `wl-copy`/`wl-paste` 在 GNOME 上会因焦点限制卡死（已实测）；
- 终端自己的纯文本粘贴路径不能通过此桥传图片；

## 安装

```bash
bash pi-agent/extensions/pi-remote-clipboard/install.sh
```

安装器会：

1. 安装本机 provider 为 systemd user service（`pi-remote-clipboard.service`）；
2. 安装 Pi 扩展 `pi-remote-clipboard` 到 `~/.pi/agent/extensions/`；
3. 根据本机 `.ssh/config` 中的登录型 `Host` 别名生成托管块（也可用 `PI_REMOTE_CLIPBOARD_HOSTS` 指定），为这些 SSH 连接设置 `RemoteForward 127.0.0.1:37844 127.0.0.1:37843`；
4. 不修改 `.zshrc`、`settings.json`、Pi bundle 或 tmux/Ghostty 配置。Pi 需在空闲时 `/reload` 或重启才能加载新扩展；安装器不会代替执行。

## 使用

### 本地 Pi

```bash
piox
```

`Ctrl+V` 走 Pi 原生路径，直接读本机剪贴板；`Ctrl+Alt+V` 尝试显式读取 SSH 对端，在本地会话会提示使用原生 `Ctrl+V`。

### SSH Pi

```bash
ssh <远端主机>
piox
```

- 复制（拖选、`[复制]` 按钮、`Ctrl+X`、`/copy`）：自动写入对端剪贴板（OSC 52）；
- `Ctrl+V`：Pi 的私有粘贴入口可用时，若最近活动的 tmux client 来自 SSH 则拉取对端剪贴板；本地 client 仍执行原生富粘贴；
- `Ctrl+Alt+V`：显式拉取对端剪贴板（文本直接插入，图片保存为 `/tmp/pi-remote-clipboard-<uuid>.<ext>` 并插入路径）；
- `/rpush`：把本机剪贴板（图片优先）推送到对端；`/rpush /path/to/img.png`：推送指定图片文件。

### 判定规则

| 场景 | 判定方式 |
|---|---|
| Pi 不在 tmux | Pi 的 SSH 环境变量；桥助手另行核查 SSH 进程祖先链 |
| Pi 在 tmux | 所在 session 中最近活动的 tmux client 的进程祖先链有 `sshd` |
| 拿不到 client 信息 | 保守地按本地处理 |

多个 client 共看同一 session 时，“最近活动”是启发式判定，不等于逐键的来源证明；本地 client 若更晚活跃，`Ctrl+V` 会走原生路径。可显式用 `Ctrl+Alt+V` 请求桥，但桥助手也会重新判定来源。

## Pi 兼容边界

扩展不按 Pi 版本号列白名单：公共 `Ctrl+Alt+V`、`/rpush` 在加载时注册。TUI 启动后分别探测可写的私有 `TuiAltScreen.copyTextToClipboard` 与 `InteractiveMode.handleClipboardPaste`，仅覆盖存在的入口；缺失时发出提示，不影响另一个入口或公共快捷键/命令。前者缺失时不接管文本复制；后者缺失时不接管 `Ctrl+V`，显式 `Ctrl+Alt+V` 仍可用。这是接口形状检查，未来 Pi 若保留同名方法却改变内部语义，仍可能需要更新扩展。

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
- 不保存剪贴板历史；
- 不监听非 localhost 地址；
- 不主动同步；
- Wayland 下依赖 `wl-paste`/`wl-copy`；X11 下依赖 `xclip`；
- 如果 `wl-paste` 在 GNOME Wayland 下卡住，provider 的读取会超时并返回空或错误，不会无限等待；
- remote forwarding 只在 SSH 会话期间有效，断开后自动失效；
- 同一 Pi session 同时被本地和 SSH client 连接时，以最近活动 client 的祖先链推断来源。

## 测试

```bash
python3 pi-agent/extensions/pi-remote-clipboard/tests/test_all.py
node --experimental-strip-types --test pi-agent/extensions/pi-remote-clipboard/tests/activation.test.mjs
cd pi-agent/extensions/pi-remote-clipboard/pi-extension && ../../pi-copy-links/node_modules/.bin/tsc -p tsconfig.json --noEmit
```

隔离测试使用合成上下文，不启动真实 provider、不读写真实剪贴板、不启动 SSH。TypeScript 检查使用仓库中 `pi-copy-links` 已安装的开发依赖。
