# pi-copy-links

给 Pi **fullscreen 模式**的助手回复增加两项操作：

- 代码块右下角 **[复制]**：左键点击，将该块的代码原文送到剪贴板。保留代码自身的缩进、Tab、空行和行尾空格；不带围栏、Pi 额外的缩进、右侧补空格或屏幕自动折行。
- 网页链接：**Ctrl + 鼠标左键**，调用操作系统的默认浏览器。支持 Markdown 链接文字、裸 HTTP(S) 地址和折行后的链接；普通点击不会由扩展打开网页。

## 安装和启用

在 quick-deploy 仓库根目录执行：

```bash
bash pi-agent/extensions/pi-copy-links/install.sh
```

也已自动纳入 `pi-agent/install.sh` 的扩展发现流程。安装器将整个源码目录软链接到 `~/.pi/agent/extensions/pi-copy-links`；可通过 `PI_CODING_AGENT_DIR` 指定其他 Pi home。依赖保留在源码目录，遇到外部文件、外部链接或 Git 已跟踪的目标会拒绝覆盖。安装器不修改 `settings.json`。

在 Pi 空闲时：

1. 执行 `/reload`。
2. 打开 `/settings`，把 **TUI mode** 设为 **fullscreen**；Pi 会即时切换并保存选择。
3. 执行 `/copy-links` 可以查看启用状态。

也可以仅本次启动使用：

```bash
pi --tui-mode fullscreen
```

**为什么需要 fullscreen？** 普通模式把历史滚动交给终端/tmux，Pi 无法知道滚动后点击的是哪个历史代码块。fullscreen 由 Pi 管理视口，按钮才能随历史滚动、窗口缩放保持正确位置。扩展不劫持普通模式的鼠标，也不会自动切换你的会话。不要在 tmux copy-mode 中点击；先退出 copy-mode，让鼠标事件回到 Pi。

## 边界

- 当前适配 **Pi 0.85.1**。其他版本会明确提示不兼容，不修改渲染器。升级 Pi 后需重新验证适配。
- 复制的是 Markdown 块代码，不是行内反引号或工具调用的折叠预览。列表和引用里的块代码也支持。流式输出期间可以复制已正确解析的代码；未完成围栏造成解析不一致时暂不显示按钮。
- 不自动修剪代码本身的空格，因为 Python、Makefile、YAML、续行命令需要保留有效空白。围栏语法所需的最后一个换行不算代码内容。
- 通常要求鼠标按下和松开落在同一按钮/链接、画面未变且没有拖动；拖动、失焦、弹窗、滚动或尺寸改变会取消此次点击。tmux 3.4 在快速切换 Ctrl 修饰键时可能丢弃按下事件；扩展只在近期修饰键切换、画面未变且没有移动事件时补认松开，不接受任意单独的松开事件。
- 剪贴板使用 Pi 自带的 `copyToClipboard`：本地优先系统剪贴板工具，SSH 等环境可能走 OSC 52，实际落入客户端剪贴板取决于终端/tmux 是否允许。扩展不修改这些权限。
- 浏览器在 **Pi 进程所在机器**打开。SSH 到远端时不承诺打开本地浏览器；部分终端会自行接管 Ctrl+点击 OSC 8 链接并在客户端打开。扩展仅允许 HTTP(S)，不通过链接执行 shell 命令或打开任意 URI 协议。
- Linux 需有 `xdg-open` 和默认浏览器，macOS 使用 `open`，Windows 使用 `rundll32.exe`。操作系统启动器返回失败会显示错误。

## 实现与维护

公开的 Markdown transformer 只用于识别助手消息、取得归一化前的源文，原样返回内容，不改会话和模型上下文。`remark-parse` 的代码节点提供逻辑代码内容；不能用 Pi 的显示 token 复制，因为 Pi 已把 Tab 展开成空格。

Pi 0.85.1 尚未提供公开的代码块按钮 renderer 或优先于 fullscreen 的鼠标 hook。因此 `src/adapter.ts` 集中保管一个**可撤销的内存适配**：装饰 `Markdown.render/renderToken/renderInlineTokens`，在代码块结束行右对齐按钮；通过 `TuiAltScreen.handleViewportInput` 和实际合成后的屏幕行命中目标。它不改磁盘上的 Pi/npm 文件，不替换编辑器，不改变图片能力设置，卸载时恢复原方法；检测到后来其他扩展包装同一方法时不覆盖对方。

按钮只保存随机的进程内 URI，代码内容不写入 URI 或临时文件。内容引用随对应 Markdown 组件存活，切换会话/重载时清除；普通文本选择和滚轮继续交给 Pi。

### tmux 鼠标透传

tmux 默认键表可能吞掉 Ctrl+左键按下。`src/tmux.ts` 仅给**尚未绑定**的 Ctrl 鼠标键及 `SecondClick1Pane` 添加转发规则，规则要求目标 pane 带有本扩展的私有标记且应用已请求鼠标事件。已有绑定一律不覆盖；有冲突时 `/copy-links` 会明确提示。

pane 标记在会话关闭/重载时按归属清除或恢复。带条件的共享绑定和对应回执保留在该 tmux server 内，没有标记时不生效，server 退出即消失；不在“最后一个会话退出”时删除共享绑定，以免与另一 Pi 启动竞争。扩展不写 `.tmux.conf`，不改变 tmux 的 `mouse` 设置，也不接管其他 pane 的点击。

快速换修饰键丢按下的原因可见 [tmux 3.4 `server_client_check_mouse`](https://github.com/tmux/tmux/blob/3.4/server-client.c)：多击计时期间按钮字节不匹配时，事件仍为 `NOTYPE`，在键表查询前已被丢弃，单加绑定无法解决。恢复逻辑保留了拖动取消和视口变化校验。

升级时重点核对：上述四个方法，以及 fullscreen 的 `previousScreen`、`previousScreenWidth/Height`、`openUrl`。不要仅扩大版本白名单。

## 验证

```bash
cd pi-agent/extensions/pi-copy-links
npm ci --ignore-scripts
npm run check
```

测试从 `PATH` 定位已安装的 Pi，并只在忽略的 `node_modules` 中建立测试软链接；包装启动器可用 `PI_TEST_CORE` 指定包根目录。覆盖原生助手渲染和 fullscreen 输入分发、逐字符复制内容、列表里的 Tab、Unicode、窄窗口、滚动、缓存、拖动取消、弹窗、重载清理、浏览器参数与安装器的归属检查。

原生 CLI/PTY 冒烟测试（Linux，另需 Python 3；后两项需 tmux）：

```bash
python3 test/native-smoke.py
python3 test/native-smoke.py --osc52
python3 test/native-smoke.py --tmux
python3 test/native-smoke.py --tmux --regular-start
```

这些测试使用临时 Pi profile 和离线 fixture，不调用模型、不改用户会话。前两项分别检查真实 CLI 交给 `wl-copy` 的原文、OSC 52 解码内容；浏览器检查 `xdg-open` 收到的完整 URL 参数，不启动桌面浏览器。tmux 测试通过真实 attachment client 注入鼠标事件，覆盖快速换修饰键、拖动取消、重载与缩放；使用 `pi-agent` server 中按项目命名的独立窗口，不操作用户默认 server，结束后保留项目 anchor。

卸载：先确认 `~/.pi/agent/extensions/pi-copy-links` 是本扩展的软链接，再删除该链接并 `/reload`。源码及依赖目录可保留。
