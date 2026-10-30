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

1. 安装本机 provider 为 systemd user service（`pi-remote-clipboard.service`），绑定 `graphical-session.target`，不在 `default.target` 的无图形环境阶段启动；
2. 安装 Pi 扩展 `pi-remote-clipboard` 到 `~/.pi/agent/extensions/`；
3. 根据本机 `.ssh/config` 中的登录型 `Host` 别名生成托管块（也可用 `PI_REMOTE_CLIPBOARD_HOSTS` 指定），为这些 SSH 连接设置 `RemoteForward 127.0.0.1:37844 127.0.0.1:37843`；
4. 不修改 `.zshrc`、`settings.json`、Pi bundle 或 tmux/Ghostty 配置。Pi 需在空闲时 `/reload` 或重启才能加载新扩展；安装器不会代替执行。

安装器只在旧版 `default.target.wants` 链接确实指向本模块 unit 时删除它，再用 `systemctl --user enable` 增加 `graphical-session.target.wants` 链接；其他用户自定义的启用链接保留。图形 target 已激活且 user manager 有 `WAYLAND_DISPLAY` 或 `DISPLAY` 时，启动/重启 provider；尚未激活时停止旧的提前启动实例，等待图形会话启动。若 target 已激活但 manager 仍缺显示环境，安装器会停止无效实例并告警；导入图形环境后需手动启动 provider，或在下次图形会话启动时自动尝试。它不从 SSH 安装 shell 隐式导入环境变量。

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
- `Ctrl+Alt+V`：显式拉取对端剪贴板（文本直接插入；图片落成临时目录下的 `pi-remote-clipboard-<uuid>.<ext>`（0600）并插入路径，归编辑器/历史所有，见“图片制品所有权”）；
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

## 图片制品所有权与助手协议

扩展每次粘贴先在系统临时目录创建本操作私有的 0700 暂存目录，再以 `python3 ctrlv-artifact-v1.py DIR` 运行助手（唯一被扩展运行的入口；`ctrlv.py --artifact-staging DIR` 仅为兼容保留）。助手从不创建 DIR，只往里以 `O_EXCL`、0600 写 `image.<ext>`，任何写入/chmod/解码失败都回滚半成品；DIR 被扩展取消并删除后助手不会重建它。stdout 第一行是版本化回执 JSON（`pi-remote-clipboard/artifact-1`）：

```
{"protocol":"pi-remote-clipboard/artifact-1","kind":"text"}\n<剪贴板文本原样>
{"protocol":"pi-remote-clipboard/artifact-1","kind":"image","name":"image.png"}\n
```

**准入**：旧版助手的 `ctrlv.py` 会无视命令行参数、自行把图片写到临时目录，扩展无法安全清理（也不得凭文本猜路径）。因此扩展只运行版本化入口 `ctrlv-artifact-v1.py`，它在旧目录里不存在：先对目录做只读存在性检查，缺失即在创建任何东西、运行任何程序之前报协议错误，绝不对旧脚本执行 `--version` 之类的探测。入口本身在读取 SSH/provider 之前先确认同目录的 `ctrlv`/`requester` 提供 `artifact_paste` 与 `staging` 参数，半更新的目录以退出码 70 拒绝。该协议下退出码 2/64/70 一律是协议/解释器错误，不是“本地会话”；本地会话只由回执 `{"kind":"local"}` 表示（手动 CLI 的退出码 2 不变）。声明兼容却写文件的自定义程序属于用户信任的代码，不做沙箱。

扩展据回执而非内容区分图片与文本：文本即使形如路径或回执 JSON 也只当文本粘贴，从不据其删除任何文件。图片回执只接受固定名 `image.(png|jpg|webp|gif)`，并校验它是暂存目录内本用户、单链接、非组/其他可访问的普通文件（拒绝路径穿越、符号链接、硬链接、目录）；通过后以硬链接交付为暂存目录之外的唯一 0600 文件，随后删除暂存目录，插入编辑器成功的那一刻文件才归编辑器/历史所有，此后（包括渲染失败、session shutdown、reload）永不删除。插入前的任何失败或取消，会在助手进程组消失后删除暂存目录和尚未交付的文件。暂存目录的所有权保留到 `rm` 成功为止（主体里的删除失败后由收尾阶段重试），暂存目录与未交付文件的释放相互独立，失败会明确记录到 stderr，已交付（已接收）的文件不受影响；重复的不可恢复 I/O 失败只会留下一个 0700 的残留目录，不设重试循环或清扫器。自动 Ctrl+V 的所有真实失败（协议、I/O、超时）都经 Pi 的错误通道显示，不回退原生路径；失败若发生在 shutdown 之后则按取消处理，不再提示。整个操作登记在 Owner 上，`session_shutdown` 的 await 会等到清理结束；业务结果（含 8 秒超时）先于清理返回。缺失/过期回执是显式协议错误（Ctrl+V 通过 Pi 的错误通道显示，Ctrl+Alt+V 通过通知显示），没有退回纯文本的兼容路径。

**文本粘贴帧**：`Ctrl+Alt+V` 的文本经公共 `ctx.ui.pasteToEditor`，Pi 会用原生括号粘贴定界符 `ESC[200~`/`ESC[201~` 包裹后交给编辑器；编辑器在第一个 `ESC[201~` 处结束数据帧，其后的内容被当作原始按键重新输入，绕过粘贴的 C0 过滤。因此扩展在调用前删除对端文本里的这两个原生定界符（反复删除直至稳定，防止拼接重组），其余内容（可见字符、换行、制表符、Unicode、字面反斜杠转义串）原样交给编辑器的常规粘贴过滤。自动 `Ctrl+V` 走 `insertTextAtCursor`，不经此解析器；图片路径不是剪贴板文本，不做改写。仅含定界符的文本按空剪贴板处理。

不带参数的手动 CLI（`python3 ctrlv.py`）行为不变：stdout 为 `sys.stdout.write` 的原文（无额外换行），图片写为临时目录下的 0600 文件并打印路径；本地会话退出码 2。提供者 TCP 协议不变。

### 部署与激活

`install.sh` 把助手**复制**到 `~/.local/lib/pi-remote-clipboard`，而 Pi 扩展是指向本检出的符号链接。Node 扩展默认使用与自身真实路径同级的 `../src` 助手（经受管符号链接解析），因此协议两端永远来自同一检出，过期的安装副本不会被默认调用；设置 `PI_REMOTE_CLIPBOARD_DIR` 仍可显式指定助手目录，若该目录里没有 `ctrlv-artifact-v1.py`（旧助手），会得到明确的协议错误且旧程序不会运行。真实的 Pi jiti 加载器经受管符号链接加载时同样取检出的 `src/`（有测试）。仅当扩展被单独复制、没有同级 `src/` 时才回退安装副本（旧副本因缺少入口而被拒绝，直到 `install.sh` 复制了 `ctrlv-artifact-v1.py`）。手动 CLI 用的安装副本需重新运行 `install.sh`（或仅同步 `requester.py`、`ctrlv.py`、`ctrlv-artifact-v1.py` 三个文件）才会获得新行为，不需要重启 provider。在 Pi 空闲时 `/reload` 或重启后新扩展代码生效。

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

会话关闭（`session_shutdown`）会取消本会话启动的全部剪贴板操作，并在返回前等待清理完成：POSIX 下每个 tmux/Python 辅助进程自成进程组，取消或 8 秒超时时对整个进程组先 TERM、1 秒后 KILL（含已提前退出的领导进程遗留的、仍持有管道的后代；只向本扩展自己启动的进程组发信号），Windows 下只能终止直接子进程。操作的 promise 结算（超时/取消/溢出）与资源清理分离，shutdown 的 await 会等待已超时但仍在 TERM→KILL 宽限中的辅助进程，因此 Pi 在 shutdown 后退出进程不会遗留它们；重复 start 会取消并记录前任，随后的 shutdown 一并等待。之后不再插入编辑器、不再发出 OSC 52/原生回退/通知；已退役的复制 sink 以 `AbortError` 拒绝（取消发生在“非对端”判定之后同样如此）。输出预算与 `execFile` 的 `maxBuffer` 相同：stdout、stderr 各 1 MiB **字节**。

```bash
python3 pi-agent/extensions/pi-remote-clipboard/tests/test_all.py
python3 pi-agent/extensions/pi-remote-clipboard/tests/test_installer.py
python3 pi-agent/extensions/pi-remote-clipboard/tests/native_activation.py
node --experimental-strip-types --test pi-agent/extensions/pi-remote-clipboard/tests/activation.test.mjs pi-agent/extensions/pi-remote-clipboard/tests/lifecycle.test.mjs pi-agent/extensions/pi-remote-clipboard/tests/paste-framing.test.mjs
cd pi-agent/extensions/pi-remote-clipboard/pi-extension && ../../pi-copy-links/node_modules/.bin/tsc -p tsconfig.json --noEmit
```

隔离测试使用合成上下文，不启动真实 provider、不读写真实剪贴板、不启动 SSH。native 测试在临时 Pi home 中执行已安装的真实 CLI：用假 Python 桥和图形剪贴板命令检测键盘路径，不连接活跃会话。Pi 的 bundled CLI 与 unbundled public package 的 class 对象不同；CLI 扩展加载器把包名导入映射到 **bundled** class，因此以真实 CLI 输入测试为准。TypeScript 检查使用仓库中 `pi-copy-links` 已安装的开发依赖。
