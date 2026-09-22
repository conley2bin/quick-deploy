# pi-copy-links

给 Pi **fullscreen 模式**的助手回复增加两项操作：

- 代码块右下角 **[复制]**：左键点击，将该块的代码原文送到剪贴板。保留代码自身的缩进、Tab、空行和行尾空格；不带围栏、Pi 额外的缩进、右侧补空格或屏幕自动折行。
- 网页链接：**Ctrl + 鼠标左键**，调用操作系统的默认浏览器。支持 Markdown 链接文字、裸 HTTP(S) 地址和折行后的链接；普通点击不会由扩展打开网页。
- 按下按钮或 Ctrl 按下链接时目标会反色高亮，松开即恢复；拖走会立即取消高亮且不触发动作。复制成功后按钮短暂闪烁确认。

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

- 当前精确适配 **Pi 0.85.1 和 0.86.1**。其他版本会在修改任何渲染器方法前明确拒绝；已验证版本若缺少 `Markdown.render`、`renderToken`、`renderInlineTokens` 或 `TuiAltScreen.handleViewportInput`，诊断会指出具体缺失方法。升级 Pi 后需重新验证适配，不能只扩大版本白名单。
- 复制的是 Markdown 块代码，不是行内反引号或工具调用的折叠预览。列表和引用里的块代码也支持。每次缓存失效先收集完整的原生 code-token 序列，再用源文位置、容器路径、围栏 raw 结构、语言和归一化显示文本做单调一对一对齐。前向扫描求每个 token 最早可行源节点，反向扫描求最晚可行源节点；两者一致才显示按钮，因此只需 O(token 数) 辅助内存和线性指针遍历。解析器分歧造成歧义时会省略按钮并警告，不按“第一个相似块”猜测。流式输出期间只复制已唯一对应的代码；未完成围栏造成解析不一致时暂不显示按钮。
- 不自动修剪代码本身的空格，因为 Python、Makefile、YAML、续行命令需要保留有效空白。围栏语法所需的最后一个换行不算代码内容。
- 通常要求鼠标按下和松开落在同一按钮/链接、画面未变且没有拖动；拖动、失焦、弹窗、滚动或尺寸改变会取消此次点击。tmux 3.4 在快速切换 Ctrl 修饰键时可能丢弃按下事件；扩展只在近期修饰键切换、画面未变且没有移动事件时补认松开，不接受任意单独的松开事件。
- 扩展只在首次渲染某个 Markdown 实例时登记一次弱引用，并设置远低于 V8 2^24 Set 极限的硬上限。任何 WeakRef/Set/Map/WeakMap/FinalizationRegistry 主操作或回滚/清理失败都会先原子摘除整个登记代际，再尽力执行所有 noexcept 清理；旧代际和随机 URI 前缀永久退役，其迟到 finalizer 只能访问旧 Map，不能删除新动作。恢复会新建不同前缀并同时探测 owner 登记、CopyStore 提交和探针清理；只有全部成功的新代际才启用。连续 8 次失败只通知一次，卸载取消待执行探针。

- 剪贴板使用 Pi 自带的 `copyToClipboard`：本地优先系统剪贴板工具，SSH 等环境可能走 OSC 52，实际落入客户端剪贴板取决于终端/tmux 是否允许。Pi 0.86.1 在已有图形会话但系统工具缺失或无权访问时会明确拒绝，而不再用无法验证的 OSC 52 假报成功；扩展会显示当前登记代际的错误且不闪烁复制成功。剪贴板写入不可中止，因此用 `Symbol.for` 保存在进程全局的串行队列跨越动作代际、适配器替换和 `/reload` 后的模块实例：旧写入结算后新写入才开始，避免旧结果最后覆盖新点击；已退役代际的迟到失败不再弹通知或改变高亮。真正永久挂起的剪贴板端点也会阻塞后续写入，这是维持“最后点击最终生效”顺序所必需的边界。扩展不修改这些权限。
- 浏览器在 **Pi 进程所在机器**打开。SSH 到远端时不承诺打开本地浏览器；部分终端会自行接管 Ctrl+点击 OSC 8 链接并在客户端打开。扩展仅允许 HTTP(S)，不通过链接执行 shell 命令或打开任意 URI 协议。
- Linux 需有 `xdg-open` 和默认浏览器，macOS 使用 `open`，Windows 使用 `rundll32.exe`。操作系统启动器返回失败会显示错误。

## 实现与维护

Pi 0.85.1/0.86.1 的公开 Markdown transformer 只用于识别助手消息、取得归一化前的源文，原样返回内容，不改会话和模型上下文。`remark-parse` 的代码节点提供逻辑代码内容；不能用 Pi 的显示 token 复制，因为 Pi 已把 Tab 展开成空格。

这两个版本虽有公开的 `MouseRegion`/`handleMouse` 组件 API，但它只能包裹扩展自己拥有的组件，不能给 Pi 已有的助手 Markdown 代码块增加按钮；而且 fullscreen 的原生 OSC 8 打开器先于未捕获的组件 click 生效。要保留普通选择、滚动、失焦/键盘取消和 tmux 丢失按下事件的窄恢复，仍需在原生 Markdown 渲染与视口输入处分层集成。

因此 `src/adapter.ts` 集中保管一个**可撤销的内存适配**：装饰 `Markdown.render/renderToken/renderInlineTokens`，在代码块结束行右对齐按钮；通过 `TuiAltScreen.handleViewportInput` 和实际合成后的屏幕行命中目标。四个原始属性描述符会先全部捕获和预检，再作为一个事务安装；每次写入后核对 wrapper 身份，任一不可写属性、抛错 setter 或静默 no-op setter 都会使四项完整回滚后再报错。卸载先停用装饰、退役动作、失效已登记组件，再强制 `renderNow(true)`；只有合成后的 fullscreen 屏幕确认不含 `pi-copy://` 才恢复输入 hook。刷新或校验失败且仍持有 prototype 时，会把完整活动闭包替换成一个只保留原生输入方法、冻结描述符和静默义务的最小被动过滤器；选择、滚轮、弹窗和普通链接全部交还 Pi，只阻止 `pi-copy://` 到达原生 opener。后续重载会吸收并替换而不是套叠该过滤器，前任适配器闭包可被回收；若后装的外部 wrapper 已取得 prototype，则不覆盖它，原活动闭包作为其必要 delegate 进入同样的惰性状态。

代码按钮采用“原生收集 pass → 线性唯一对齐与新代际动作提交 → 装饰 pass”。第二个 pass 的 token 序列还必须与第一个 pass 完全相同；否则退役动作代际并做第三个纯原生 pass，避免半装饰输出。输入 hook 只截获已验证的扩展点击；所有其余鼠标事件仍进入 Pi 原生 `handleViewportInput`，因此滚轮、弹窗命中和跨按钮文本选择保留原生状态机。普通 URL 的原生打开权限来自本次事件是否命中 `renderedOverlayLayouts`，或是否沿已有 overlay `mouseCapture/mousePressTarget` 路径派发，而不是全局 `hasOverlay()`；小弹窗之外的 transcript 链接仍只响应 Ctrl+左键。调用原生处理前会捕获 `openUrl` 的精确 own descriptor/缺失状态和可观察前任值，但绝不调用任意 accessor setter：可配置 own accessor/data descriptor 直接替换为临时 own data descriptor，缺失的 own property 以 data descriptor 遮蔽继承 accessor，非配置但可写的 own data property 只改 value。只有确认取得属性所有权后才进入原生分发；非配置 accessor 或其他不能由 descriptor 精确回滚的形状会直接消费事件，绝不让私有 URI 走未过滤路径。嵌套调用会解开本扩展自己的前任过滤器；`finally` 仅在属性描述符和函数身份仍归当前过滤器时，用 `Object.defineProperty` 恢复精确前态或删除临时 own property，因此不会触发前任 setter，也不会覆盖原生处理期间同步安装的外部 opener。任何状态下 `pi-copy://` 都不会到达原生 opener。它不改磁盘上的 Pi/npm 文件、不替换编辑器，也不改变图片能力设置。

按钮只保存随机的进程内 URI，代码内容不写入 URI 或临时文件。内容引用随对应 Markdown 组件存活，切换会话/重载时清除；普通文本选择和滚轮继续交给 Pi。

### tmux 鼠标透传

tmux 默认键表可能吞掉 Ctrl+左键按下。`src/tmux.ts` 仅给**尚未绑定**的 Ctrl 鼠标键及 `SecondClick1Pane` 添加转发规则，规则要求目标 pane 带有本扩展的私有标记且应用已请求鼠标事件。已有绑定一律不覆盖；有冲突时 `/copy-links` 会明确提示。

pane 标记在会话关闭/重载时按归属清除或恢复。带条件的共享绑定和对应回执保留在该 tmux server 内，没有标记时不生效，server 退出即消失；不在“最后一个会话退出”时删除共享绑定，以免与另一 Pi 启动竞争。扩展不写 `.tmux.conf`，不改变 tmux 的 `mouse` 设置，也不接管其他 pane 的点击。

快速换修饰键丢按下的原因可见 [tmux 3.4 `server_client_check_mouse`](https://github.com/tmux/tmux/blob/3.4/server-client.c)：多击计时期间按钮字节不匹配时，事件仍为 `NOTYPE`，在键表查询前已被丢弃，单加绑定无法解决。恢复逻辑保留了拖动取消和视口变化校验。

升级时重点核对：上述四个方法，以及 fullscreen 的 `previousScreen`、`previousScreenWidth/Height`、`openUrl`、`renderedOverlayLayouts`、`overlayStack`、`mouseCapture/mousePressTarget` 和 `containsComponent`。不要仅扩大版本白名单。

## 验证

```bash
cd pi-agent/extensions/pi-copy-links
npm ci --ignore-scripts
npm run check
```

测试从 `PATH` 定位已安装的 Pi，并只在忽略的 `node_modules` 中建立测试软链接；包装启动器可用 `PI_TEST_CORE` 指定包根目录。Pi 0.86.1 回归覆盖 Marked 18.0.11 边界、隐藏 Tab/可见空格碰撞、合法重复块与歧义省略；四 hook 原子回滚；主操作和二次回滚失败；提交后抛错的 finalizer 与退役前缀隔离；滚轮、跨私有 URI 选择、宽 8 小弹窗内外 URL、overlay capture/release 和无弹窗 Ctrl-only 策略；嵌套 opener 过滤、同步外部替换、可配置 no-op/抛错 accessor、三种 commit-then-refuse accessor 形状、own-property 缺失恢复和不可配置描述符的拒绝分发；成功/失败同步静默、重载点击窗口、替换安装失败、重复重载的单层最小过滤器、强制 GC 闭包回收和外部 wrapper 归属；跨代际及跨适配器的剪贴板顺序、失败后队列恢复和永久挂起边界；恢复构造/探针/清理失败；finalizer 回调 delete 失败；以及 5000 个重复块的 10000 次前后向匹配上界和禁止密集矩阵分配。

原生 CLI/PTY 冒烟测试（Linux，另需 Python 3；后两项需 tmux）：

```bash
python3 test/native-smoke.py
python3 test/native-smoke.py --osc52
python3 test/native-smoke.py --tmux
python3 test/native-smoke.py --tmux --regular-start
```

这些测试使用临时 Pi profile 和离线 fixture，不调用模型、不改用户会话。前两项分别检查真实 CLI 交给 `wl-copy` 的原文、OSC 52 解码内容；浏览器检查 `xdg-open` 收到的完整 URL 参数，不启动桌面浏览器。tmux 测试通过真实 attachment client 注入鼠标事件，覆盖快速换修饰键、拖动取消、重载与缩放；使用 `pi-agent` server 中按项目命名的独立窗口，不操作用户默认 server，结束后保留项目 anchor。

卸载：先确认 `~/.pi/agent/extensions/pi-copy-links` 是本扩展的软链接，再删除该链接并 `/reload`。源码及依赖目录可保留。
