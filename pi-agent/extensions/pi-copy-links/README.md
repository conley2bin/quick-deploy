# pi-copy-links

给 Pi **fullscreen 模式**的助手 Markdown 增加两类受控操作：

- 代码块右下角 **[复制]**：左键复制逻辑源文，保留代码自身的缩进、Tab、空行和行尾空格；不复制围栏、Pi 显示缩进、右侧补空格或屏幕折行。
- Markdown 链接：仅 **Ctrl + 鼠标左键**激活。HTTP(S) 在默认 Chrome 的显式新窗口打开；已存在的本地文件或目录在 VS Code 的显式新窗口打开。普通点击和拖选继续交给 Pi。

按下可执行目标时会反色，拖走、失焦、弹窗、滚动、尺寸变化或修饰键不匹配都会取消。每次激活都允许创建一个新应用窗口。

## 安装和启用

在 quick-deploy 仓库根目录执行：

```bash
bash pi-agent/extensions/pi-copy-links/install.sh
```

安装器把整个源码目录软链接到 `~/.pi/agent/extensions/pi-copy-links`，也已纳入 `pi-agent/install.sh` 的扩展发现流程。可用 `PI_CODING_AGENT_DIR` 指定其他 Pi home。安装器不修改 `settings.json`、Pi bundle 或桌面关联。

在 Pi 空闲时执行 `/reload`，再从 `/settings` 把 **TUI mode** 设为 **fullscreen**。`/copy-links` 显示当前状态。也可单次启动：

```bash
pi --tui-mode fullscreen
```

普通模式把历史滚动交给终端/tmux，Pi 无法可靠命中滚动后的历史内容，因此扩展不在普通模式接管鼠标。tmux copy-mode 也会接管鼠标；点击前应先退出。

## 链接目标

扩展只处理助手消息中真实的 Markdown `link` token，包括显式链接、引用链接和 Pi 原生 HTTP(S) autolink。它不从裸文件路径文字、代码 span、代码块、图片或任意 OSC 8 文件 URL 推断目标。

### 网页

接受绝对 `http://` 和 `https://` URL；查询参数和 fragment 原样作为浏览器数据，不解释为编辑器行号。空格、控制字符或无效 URL 会明确失败。

Linux 每次网页激活都会：

1. 以自有的 `spawn` 子进程、`shell: false`、2.5 秒硬 wall-clock deadline 和有界 stdout/stderr 执行 `xdg-settings get default-web-browser`；deadline/abort/输出溢出会先独立结算调用，再向 detached 进程组发送 SIGTERM，并在 200 ms 内升级 SIGKILL；leader 的 `exit` 只记录状态，不代表 ownership 完成；只有 child `close` 或两个输出流都关闭后才正常结算，因此继承管道的 descendants 会继续受到 deadline 约束；
2. 要求结果是单个合法 desktop ID；
3. 执行 `xdg-settings check default-web-browser <id>` 并要求 `yes`；
4. 只接受当前白名单 `google-chrome.desktop`；
5. 执行 `google-chrome-stable --new-window URL`。

不会读取 `BROWSER`，不会解析 desktop entry 的 `Exec`，不会搜索替代浏览器，也不会调用 `xdg-open`、`gio open` 或失败后的备用启动器。其他默认浏览器和非 Linux 平台在这个版本中明确失败。

### 本地文件和目录

相对路径在**经过验证的 Ctrl+左键松开时**读取一次当前 Pi `ctx.cwd`，随后整次异步打开都使用这个快照。渲染时目录、扩展源码目录和 `process.cwd()` 都不参与解析。

| Markdown href | 含义 |
|---|---|
| `out.svg`、`src/file.ts`、`./out.svg`、`../out.svg` | 相对激活时 `ctx.cwd` 的 POSIX 路径 |
| `/absolute/path` | 绝对路径 |
| `file:///absolute/path`、`file://localhost/absolute/path` | 本地 file URL |
| `./file.ts:12`、`src/file.ts:12:3`、`/tmp/file.ts:12` | 路径 + 行/列 |
| `file.ts#L12`、`src/file.ts#L12C3`、`file:///tmp/file.ts#L12` | fragment 行/列 |

解析顺序是：先识别 URI 命名空间，再识别**字面**位置选择器，最后仅 percent-decode 一次。因此：

- `name%3A12` 和 `name%23L12` 是包含字面 `:`、`#` 的文件名；
- `%2520` 指向包含字面 `%20` 的文件名；`+` 始终是 `+`；
- 本地 raw `?query`（包括空 `?`）被拒绝，文件名中的 `?` 必须写 `%3F`；
- `file:///tmp/name:12` 的冒号属于 pathname；file URL 导航必须用 `#L12`；
- `README.md:12`、`name:12` 是 scheme-shaped 歧义，必须改成 `./README.md:12` 或 `README.md#L12`；不会按文件是否存在猜测；
- 同时出现 `:line` 和 `#Lline`、章节 fragment、空 fragment、fragment-only、行范围、零/负数/符号、溢出位置都会失败；
- 行列范围为 `1..2_147_483_647`；
- 拒绝畸形 percent escape、无效 UTF-8、NUL、C0/C1 控制字符和编码 `/`；
- `file://other-host/path`、`file:relative`、`file:/path`、`//host/path` 被拒绝；
- `mailto:`、`vscode:`、`command:`、`javascript:`、`data:` 和自定义 scheme 不会重解释为文件；
- 不展开 shell、环境变量、glob 或 `~`；反斜杠不是 POSIX 路径分隔符，file URL 中会拒绝它。

激活时使用 `stat` 跟随 symlink。只接受现有普通文件和目录；明确拒绝缺失/无权限/悬空 symlink、FIFO、socket 和设备节点。目录不能带行列。传给 Code 的绝对路径保留解析所得拼写，不做 `realpath`、项目根限制或 inode 固定。

启动参数固定为：

```text
code --new-window -- /absolute/path
code --new-window --goto -- /absolute/path:12:3
```

带位置时仅接受普通文件。由于当前 Code CLI 的 `--goto` operand 不能安全表达含字面冒号的 POSIX 路径，这一组合会明确失败；不带位置的冒号路径仍可打开。启动环境复制当前桌面环境，但删除 `VSCODE_IPC_HOOK_CLI`，避免被远端 CLI socket 重定向。不会使用 `--reuse-window`。

Chrome 和 Code 都通过参数数组、`shell: false`、`stdio: "ignore"`、detached 子进程启动。监听器安装、abort 脱钩和 `unref()` 全部成功后才完成应用所有权交接；任何 spawn 后的同步 handoff 失败都会先移除全部 observer，再立即 TERM+KILL 尚属扩展的 detached 进程组，避免“报错但仍打开”或 pin 住 Pi。完成交接后，代际 abort 只移除 observer，不会 kill 已接受的应用。可观测的 executable lookup、权限和非零退出会在动作仍有效时报告。成功只表示显式新窗口请求已被接受，不宣称窗口已绘制或位于指定 workspace。

### 源文分歧

Pi 在 lexing 前把 Tab 展开成三个空格，native href 还可能保留 character-reference 拼写。扩展同时用 remark 读取源文 link/linkReference span（引用采用第一条 definition），把 raw span 按 Pi 的 Tab/换行规则归一化后与 native `[raw, href]` 证据核对。

所有显式链接先绑定其唯一的 remark 语义目标，再分类为 web/local/unsupported。因此 `[web](https://example.com/?a=1&amp;b=2)` 打开的是语义 URL `...?a=1&b=2`，不会误开 query key `amp;b`；引用链接遵循同一规则。angle/bare HTTP(S) autolink 只有在原始文本证据存在且 remark/native 的规范化网页语义一致时才可打开，分歧会生成诊断。

本地链接仍额外要求唯一源文语义与 native href 一致。Tab/三个空格碰撞或 `a&amp;b.svg` 一类本地分歧会生成私有诊断动作；Ctrl+点击只解释拒绝原因，不猜文件名。Percent-encoded 等价写法是可靠表示。

## 兼容和生命周期边界

- 精确支持 **Pi 0.85.1 和 0.86.1**。其他版本会在改写任何 prototype 前拒绝；已验证版本若缺少 `Markdown.render`、`renderToken`、`renderInlineTokens` 或 `TuiAltScreen.handleViewportInput` 会指出具体方法。
- 一个 mounted Markdown 拥有其全部 copy/open/diagnostic action。所有动作都使用同一随机 `pi-copy://<nonce>/<sequence>` 内存命名空间；URI 不包含路径、命令或代码内容。
- 缓存 miss 采用原生收集 pass、一次联合 action commit、装饰 pass；链接以 exact `[raw, href]` key 和计数绑定，不用递归 cursor，因此 table 的测量/输出双渲染不会错位或多分配。代码块仍用源位置、容器路径、围栏结构、语言/meta 和逻辑文本做线性最早/最晚唯一对齐。若私有 action 登记不可用，transcript 中看似仍有的 native HTTP 链接也保持不可执行；不会退回 Marked href 绕过源文语义核对。
- 动作按字段比较，未变 rerender 复用 URI。owner batch 变化时同步删除并 unregister 被替代条目；任何 allocation、Map/WeakMap/WeakRef/finalizer、commit 或 cleanup 异常都会先摘除整个代际，再尽力清理并用全新 prefix 恢复。
- 每代有一个 `AbortController`，且异步工作的唯一 liveness 判据同时要求 adapter enabled、仍是 current generation、无 poison、signal 未 abort、store trusted。队列中的 clipboard 写入在真正开始前及所有成功/失败反馈前都重查；当前代际一旦 poisoned 会先 suspend/retire，不能继续执行已排队副作用。退役先从 current generation 摘除，再 abort 浏览器探测/文件检查并静默迟到反馈。已经完成 handoff 的 Chrome/Code 只移除 observer，不会被 kill，窗口可以独立于 Pi 存活。
- Clipboard 仍使用进程级 `Symbol.for` 串行队列，跨代际、adapter 和 `/reload` 保证最后点击的写入顺序；打开动作不进入该队列。
- 普通 local/private link 点击和拖选走 Pi 原生 viewport 状态机，但临时 opener filter 永远挡住所有 `pi-copy://`。只有 stored action kind 决定手势：copy 保留原行为；open/diagnostic/expired 必须是准确 Ctrl+左键，press/release 都满足且无 Shift/Alt/扩展按钮别名。
- Overlay URL 只由准确 geometry 或 overlay-owned capture 授权。卸载先退役 action、失效 owner、强制同步 composed frame；仅证明 fullscreen 中无私有 URI 后才恢复 input hook。失败则保留单层最小被动 guard；后续实例吸收它，不保留前任闭包，也不覆盖后装 foreign wrapper。
- tmux 只给未绑定的 Ctrl 鼠标键和 `SecondClick1Pane` 增加 pane-marker 条件透传；不改 `.tmux.conf`、`mouse` 设置或别的 pane。窄 lost-press 恢复要求前一次普通 release 与 orphan Ctrl release 命中完全相同的 cell、私有 URI/action kind，并保持屏幕、尺寸、overlay ownership/snapshot 和无 motion；任意空白 cell 的 release 不能授权别处链接。

显式 `--new-window` 避免扩展主动复用其他 GNOME workspace 的窗口，但 Wayland 下最终 placement 属于 Mutter。真实 GNOME 验收必须作为有界、用户可见的手工步骤：在其他 workspace 已有 Chrome/Code 窗口时，从 Pi workspace 分别 Ctrl+点击网页、文件和目录，观察新窗口是否仍落在当前 workspace。若 compositor 行为不满足预期，需要单独的 GNOME Shell companion；本扩展不虚构 workspace 控制权。

## 验证

```bash
cd pi-agent/extensions/pi-copy-links
npm ci --ignore-scripts
npm run check

python3 test/native-smoke.py
python3 test/native-smoke.py --osc52
python3 test/native-smoke.py --tmux
python3 test/native-smoke.py --tmux --regular-start
```

单元/集成测试覆盖代码源文、typed action failure atomicity、poisoned-generation clipboard queue、live cwd snapshot、Markdown recursion/table/reference、显式 web entity/reference 语义与 autolink disagreement、完整目标语法、symlink/type/existence、browser default 检测、硬 subprocess deadline 与 TERM/KILL 升级、transactional unref handoff、Chrome/Code argv、async retirement、selection/overlay/opener descriptor/tmux exact-target recovery/reload/quiescence。Native smoke 使用临时 Pi profile、真实 CLI/PTY 和 recorder executables：`wl-copy`、`xdg-settings`、`google-chrome-stable`、`code`；`xdg-open` 是失败 tripwire。它创建真实临时 `.3mf`、`.svg`、`.json` 和目录，只按显示 label 选择目标，不假定每个 `pi-copy://` 都是复制按钮。自动测试不会启动物理 Chrome/Code，也不会修改物理剪贴板。

卸载前确认 `~/.pi/agent/extensions/pi-copy-links` 是本扩展 symlink，删除后 `/reload`。源码和依赖可保留。
