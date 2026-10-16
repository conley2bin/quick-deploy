# pi-copy-links

给 Pi **fullscreen 模式**的助手 Markdown 增加两类受控操作：

- 代码块右下角 **[复制]**：左键复制逻辑源文，保留代码自身的缩进、Tab、空行和行尾空格；不复制围栏、Pi 显示缩进、右侧补空格或屏幕折行。
- Markdown 链接及可明确识别的文件路径（正文或行内代码）：仅 **Ctrl + 鼠标左键**激活；普通点击不打开链接，拖选继续交给 Pi。已证明的本机 Ghostty 输入让本机 Chrome/VS Code 显式请求新窗口；来自已验证配对 SSH 桌面的输入（直接 PTY 或 tmux 客户端）让该桌面的助手打开默认浏览器或 Code Remote-SSH。文件始终属于运行 Pi 的服务器，不属于接收点击的桌面。

按下可执行目标时会反色，拖走、失焦、弹窗、滚动、尺寸变化或修饰键不匹配都会取消。配对桌面的网页助手通过 `--new-window` 创建新 toplevel；GNOME workspace/焦点和最终页面显示仍需在实际桌面验收。

## 使用：在点击来源的桌面打开

两台机器既可运行 Pi，也可提供桌面。角色只由实际 OS hostname（大小写归一）选择，SSH 别名保留配置中的准确拼写：

| Pi 服务器 | 发出点击的桌面 / 服务器调用的助手别名 | 桌面 Code 的文件服务器 |
|---|---|---|
| `conley-company` | `conley-Y9000P` / `conley-Y9000p` | `ssh-remote+conley-company` |
| `conley-Y9000P` | `conley-company` / `conley-company` | `ssh-remote+conley-Y9000p` |

未知实际主机明确拒绝配对 SSH，不从 `HOSTNAME`、SSH/DISPLAY 环境或请求路径猜角色；已证明的本机 Ghostty 输入仍可本机打开。先在**每个 Pi 主机更新已审阅的扩展源码**（包含自动路径 renderer），在**每个提供桌面的主机单独安装匹配助手**。助手安装、checksum/软链接核验和隔离测试见 [客户端助手说明](client/README.md#install-on-each-desktop-and-check-versions)。旧 checkout 上重跑任一安装器只链接旧源码；只安装助手不会更新服务器的路径识别。匹配后：

1. 在 Pi **空闲时**执行 `/reload`，确认 `/settings` 中 **TUI mode = fullscreen**；`/copy-links` 查看扩展状态。也可用 `pi --tui-mode fullscreen` 启动。
2. 从任一桌面 SSH 到另一台机器启动 Pi 即可，无需 tmux。扩展从 Pi 的 fd0 PTY 找到实际主端持有者；只有该 PTY 由匹配上表对端别名的字面地址/用户/端口的 sshd 会话持有时才走该桌面的助手。只有输入契约已核实的 Codoxear broker 和 util-linux `/usr/bin/script` 可作为 relay：还必须确认其 fd0 是不同 PTY、该 PTY 同时是进程的控制终端，且进程组是前台组，才沿 fd0 继续解析。因此直接 SSH→Codoxear→Pi 和 tmux→`script`→Pi 保留外层来源；其他“看起来像 relay”的程序一律不推断。生产 Codoxear `keys` 路径只发送 `Esc` 中断，Web 消息走 bracketed paste 且不生成终端鼠标事件，因此不需要修改 Codoxear。
3. tmux 只用于一个 pane 可由多个客户端输入的场景。此时保留逐次鼠标事件的 client-PID 标记，并要求按下/松开来自同一已验证客户端。本机输入目前只支持 **Ghostty 自身**持有 Unix socket、且 peer 是本地 Wayland/X server socket 的情形；不从父/祖先继承显示权限。本机实际安装/工作流只使用 Ghostty，审阅接受其 GTK4 单一 default display 契约。gnome-terminal-server、kitty、alacritty、konsole、xterm、foot、tilix、xfce4-terminal 和 wezterm-gui 的逐窗口/复用输入契约未验证，都会带诊断拒绝；特别是 xfce4-terminal 可在同一进程保留本地 X 连接而把目标窗口放到转发 X，WezTerm 可接收远端 mux 鼠标事件。VS Code integrated terminal/ptyHost、ttyd/网络 PTY server、未知 GUI、sudo/su、非 tmux multiplexer 和嵌套 tmux 也明确拒绝，不会退回 Pi 服务器的 GUI。
4. 对仍有效的助手 Markdown 链接或下述文件路径按 **Ctrl + 鼠标左键**；打开动作跟随发出点击的桌面。Pi 服务器的回环网址在另一桌面需要助手自有的 SSH 本地转发，浏览器仍收到原网址。
5. 若报告 `port_conflict`，先在接收点击的桌面查明占用原端口的监听器及其所有者。例如 `ss -H -ltnp '( sport = :8795 )'`，再将输出中的 PID 交给 `ps -fp` 辨认旧转发；确认有无依赖它的标签页，**取得所有者同意后**才显式关闭旧转发并重试原链接。已有手动 `ssh -N -L 8795:127.0.0.1:8795 ...` 会占用该桌面的 8795；助手不会接管或自动终止它，也不会改端口打开替代网址。

## 安装和启用

在 quick-deploy 仓库根目录执行主安装器：

```bash
bash pi-agent/install.sh
```

主安装器发现并执行本扩展的叶安装器；全部安装成功后，它会把全局 `settings.json` 的 `tuiMode` 保存为 `fullscreen`，新 Pi 会话无需另做 `/settings`。项目/CLI 模式覆盖仍有效，运行中的 Pi 不会自动切换模式或重载；已有会话在空闲时执行 `/reload`。

只有有意单独维护这一个扩展时，才直接执行叶安装器：

```bash
bash pi-agent/extensions/pi-copy-links/install.sh
```

叶安装器把整个源码目录软链接到 `~/.pi/agent/extensions/pi-copy-links`，可用 `PI_CODING_AGENT_DIR` 指定其他 Pi home；它不修改 `settings.json`、Pi bundle 或桌面关联，因此单独使用时需自行确认 fullscreen。

客户端助手源码位于本扩展的 `client/`，在**每个提供桌面的主机（公司机与 Y9000p）**的匹配已审阅 checkout 根目录单独运行：

```bash
bash pi-agent/extensions/pi-copy-links/client/install.sh
# 独立版本/外来软链接只有有意重指向时才加：
bash pi-agent/extensions/pi-copy-links/client/install.sh --replace
```

当前源链接重跑不变；同一 checkout 的旧 `pi-agent/client-link-helper/` 链接自动迁移（断链也可），旧源码目录已删除、无别名/包装。其他软链接默认拒绝；`--replace` 也不覆盖普通文件/目录，不改旧目标。安装器不取代码、不远程安装、不启动助手或 GUI/SSH；两台主机的主入口都仅扫描 `extensions/*/install.sh`，不会运行嵌套的客户端入口。客户端也不增加 Pi 扩展入口。服务器扩展文件和桌面已安装助手的字节/软链接及 v2 协议核对后，按上面的使用步骤在 Pi 空闲时手动 `/reload`；安装器不替用户重载。普通模式把历史滚动交给终端/tmux，Pi 无法可靠命中滚动后的历史内容，因此扩展不在普通模式接管鼠标。tmux copy-mode 也会接管鼠标；点击前应先退出。

## 链接目标

扩展处理助手消息中的显式/引用 Markdown 链接、Pi 原生 HTTP(S) autolink，以及正文和行内代码中有明确文件证据的 POSIX 路径。它只给当前渲染增加不含目标内容的私有动作；消息、模型上下文、显示文字和原生样式不变，不增加下划线或打印 URI。普通点击/拖选仍归 Pi，只有 Ctrl+左键打开。

### 不写 Markdown 链接也可点击的路径

| 助手输出示例（正文或行内代码） | 自动目标 |
|---|---|
| `state43_real_grid_1080p.mp4`、`chart.png`、`notes.pdf`、`src/main.ts` | 普通文件名、带目录的文件名 |
| `data.jsonl`、`dataset.parquet`、`weights.pt`、`model.3mf` | 常见数据、模型和媒体产物 |
| `README`、`LICENSE`、`Dockerfile`、`Makefile`、`.gitignore` | 明确的常见文件 basename |
| `/tmp/output`、`./output`、`../output` | 绝对或显式相对路径，不要求扩展名 |
| `reports/`、`src/components/`、`./`、`../` | 目录拼写；无根、无扩展名的目录需尾斜杠 |
| 行内代码中的 `reports/my video.mp4`、`/tmp/中文 文件.pdf` | 完整、带路径前缀的含空格文件名 |

例如正文 `Open reports/result.png`、`输出文件 reports/result.png` 仍可点击其中的路径。普通文字按完整空白分隔词保留文件名上下文：`./reports/final(1).png` 和 `./reports/final,1.png` 的内部括号/逗号属于文件名，不会拆成可点击的前缀或后缀。完整外层配对括号/引号及句尾标点视为 prose，例如 `(reports/chart.png),`、`（reports/chart.png），` 和 `reports/chart.png。`；不配对或内部不支持的标点会令整个词保持不可点击，不重试碎片。行内代码必须整体是一个路径，不会从 `ffmpeg -i in.mp4 out.mp4`、`cat reports/out.png`、`python src/main.py` 中抽取文件名。带 shell prompt、赋值、选项、管道/重定向等命令语法或已知命令起始的正文行也不扫描；这是语法识别，不执行命令、不在渲染时 `stat`。

没有足够语法证据时保持文字：`and/or`、`input/output`、`src/components`、`v1.2.3`、`3.14`、`example.com`、未知扩展名和无路径前缀的含空格短语都不猜。常见扩展名/basename 集合见 [`src/paths.ts`](src/paths.ts)；不在集合中的文件可写成 `./name.custom` 或显式 Markdown 链接。`~/`、`~user/` 不自动展开；请写 Pi 服务器侧绝对路径。路径、命令与自然语言在个别拼写上不可区分时，使用显式链接表达意图。

URL/email 的子串、显式链接标签、图片 alt、raw HTML 所在段落、围栏和缩进代码块都不自动推断文件。Markdown 转义/实体导致源文与 native 文字不一致，或重复 native 片段可能对应不可点击的命令/HTML 源文时，不借其他位置的文件名猜目标。宽度极窄以致 native table 回退原始表格文字时，也不会凭空增加命中点。

**自动路径的 `%`、`#`、`?`、`:` 是文件名字面数据。** 例如行内代码 `图%20#L12?.png` 指向名称确实包含 `%20#L12?` 的 PNG；`./file.ts:12` 指向文件名 `file.ts:12`，不会跳到第 12 行。需要行列导航时使用显式链接 `[定位](./file.ts:12:3)` 或 `[定位](file.ts#L12)`，沿用下面的 href 规则。scheme-shaped 的裸 `file.ts:12` 不自动识别，含冒号文件可加 `./`。自动目标先按字面数据编码一次，再交给现有点击时解析器；不会生成 terminal-native `file:` / `vscode:` URI。

### 网页

接受绝对 `http://` 和 `https://` URL；查询参数和 fragment 原样作为浏览器数据，不解释为编辑器行号。空格、控制字符或无效 URL 会明确失败。

**已证明的本机输入**核对默认浏览器是 `google-chrome.desktop` 后执行 `google-chrome-stable --new-window URL`，其他默认浏览器或非 Linux 平台会明确失败。**配对 SSH 桌面输入**在该桌面的 GUI 环境执行 `xdg-settings get default-web-browser`，仅接受单个合法 desktop ID，并在 PREPARED 前确认对应可执行文件存在；COMMIT 后执行该浏览器的 `--new-window URL`，不会回退到 `xdg-open`。支持的 desktop ID/命令为：

- `google-chrome.desktop` → `google-chrome-stable`
- `google-chrome-beta.desktop` → `google-chrome-beta`
- `google-chrome-unstable.desktop` → `google-chrome-unstable`
- `chromium.desktop`、`chromium_chromium.desktop` → `chromium`
- `chromium-browser.desktop` → `chromium-browser`
- `firefox.desktop`、`firefox_firefox.desktop`、`firefox-esr.desktop` → `firefox`
- `microsoft-edge.desktop` → `microsoft-edge-stable`
- `brave-browser.desktop` → `brave-browser`

未知/非法 desktop ID 或缺少映射后的可执行文件会在 PREPARED 前返回 `browser_unsupported`。浏览器进程在短暂 grace 内退出 0 表示请求已转交给运行中的实例；非零返回 `browser_failed`；grace 后仍运行表示它成为主浏览器进程，助手会交接而不会因超时杀死它。Pi 服务器的回环网址需先建立原端口、双回环地址都归助手所有的 SSH 转发；外部网址不需要转发。

### Pi 服务器文件和目录

相对路径在**经过验证的 Ctrl+左键松开时**读取一次当前 Pi `ctx.cwd`，随后整次异步打开都使用这个快照。渲染时目录、扩展源码目录和 `process.cwd()` 都不参与解析。

下面的表格和 percent/selector 规则针对**显式 Markdown href**；自动路径的字面数据规则见上节，二者不混用。

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

已证明的本机输入的启动参数固定为：

```text
code --new-window -- /absolute/path
code --new-window --goto -- /absolute/path:12:3
```

带位置时仅接受普通文件。由于当前 Code CLI 的 `--goto` operand 不能安全表达含字面冒号的 POSIX 路径，这一组合会明确失败；不带位置的冒号路径仍可打开。启动环境复制当前桌面环境，但删除 `VSCODE_IPC_HOOK_CLI`，避免被远端 CLI socket 重定向。不会使用 `--reuse-window`。

**本机 MP4 预览需要单独安装 [VS Code 侧修复扩展](../../vscode-video-preview-repair/README.md)。** Code 1.135 的新建独立窗口可能先把 MP4 解析成二进制/文本编辑器；Pi 的通用启动参数不能指定自定义编辑器。修复扩展在目标 Code 窗口启动后仅对首个未修改的本地 MP4 文本标签调用 `vscode.openWith(..., 'vscode.videoPreview')`；它不随 Pi 扩展安装，不修改上述启动参数或 SSH 路由。

配对 SSH 路径使用上表中的**Pi 服务器 authority**和明确的文件/目录 URI：Y9000p 桌面打开公司文件用 `code --remote ssh-remote+conley-company --new-window`；公司桌面打开 Y9000p 文件用 `code --remote ssh-remote+conley-Y9000p --new-window`。`--file-uri`/`--folder-uri` 保留服务器 `stat` 得到的 kind，助手不在桌面查找同名路径。若同一文件夹已经在某个 VS Code 窗口中打开，VS Code 即使收到 `--new-window` 也可能聚焦那个已有窗口，因此它仍可能跳到该窗口所在的 workspace。启动成功只表示桌面接受请求，不保证窗口已绘制或 Remote-SSH 已连接；实际反向连接和可见窗口属于部署后的验收。

### 源文分歧

Pi 在 lexing 前把 Tab 展开成三个空格，native href 还可能保留 character-reference 拼写。扩展同时用 remark 读取源文 link/linkReference span（引用采用第一条 definition），把 raw span 按 Pi 的 Tab/换行规则归一化后与 native `[raw, href]` 证据核对。

自动路径要求 native text/codespan 的 raw/value 与源文叶片证据一致；完整 code span 才能成为路径。正文只匹配完整 remark 叶片，以及 native table cell 裁边、URL 或严格 `~~删除线~~` 产生的可行子叶边界；`chart.png` 不是 `reports/chart.png` 的另一叶片，`README` 也不是 `README.md` 的另一叶片。相同可行 native 叶片若对应命令、HTML 或不同源文语义，仍拒绝借用另一处的目标。源文每次变更都会重建本 frame 的证据索引；收集、table 测量和装饰复用 exact 查询结果，无路径正文直接跳过匹配，不逐 native token 重扫全部源文。路径只从完整原始词识别一次，并保留源文区间；remark strong/em 与 Pi 严格删除线的外层格式标记可以界定完整路径内容。AST/native 装饰分片只能接收完整候选区间的投影，不能重新从片段生成正向目标。因此 `./reports/final~~1~~.png`、`./reports/**final**.png` 这类只格式化部分文件名的拼写保持不可点击，不会打开现有前缀、后缀或猜测拼接后的文件；完整的 `~~reports/deleted.png~~`、`**reports/file.png**` 仍可用。文件名字面包含这些 Markdown 标记时，用行内代码或显式链接表达，不从格式化后的拼写猜文件。负向/歧义证据随真实分片传播，删除线中单个内部 `~` 也不会绕过命令排除。这个索引只保存源文证据，动作仍全部属于同一个 ActionStore。显式链接标签的递归渲染不参与自动发现。装饰只在临时 token payload 插入 OSC8，由 native 继续处理原有颜色、代码样式、换行和宽度，不修改 host token cache。

所有显式链接先绑定其唯一的 remark 语义目标，再分类为 web/local/unsupported。因此 `[web](https://example.com/?a=1&amp;b=2)` 打开的是语义 URL `...?a=1&b=2`，不会误开 query key `amp;b`；引用链接遵循同一规则。angle/bare HTTP(S) autolink 只有在原始文本证据存在且 remark/native 的规范化网页语义一致时才可打开，分歧会生成诊断。

本地链接仍额外要求唯一源文语义与 native href 一致。Tab/三个空格碰撞或 `a&amp;b.svg` 一类本地分歧会生成私有诊断动作；Ctrl+点击只解释拒绝原因，不猜文件名。Percent-encoded 等价写法是可靠表示。

## 维护者：兼容和生命周期边界

- **Pi 版本号只用于诊断，不作为启用名单。** 每次 TUI 会话启动先检查实际运行时接口与行为；未知版本标签只要契约成立也会启用，普通升级不需要用户修改版本列表或逐版执行验收脚本。缺少/不可替换的 `Markdown.render`、`renderToken`、`renderInlineTokens`、`TuiAltScreen.handleViewportInput`，或具名行为检查失败时，仅停用复制/链接增强，保留原生 Markdown/Pi。同一扩展实例的同一首要失败只自动提示一次有界、具体的原因；`/copy-links` 显示兼容检查、探针耗时和当前动作登记是否可用。启动后半段 UI/渲染失败也会先摘除并退役 adapter、释放鼠标 owner，再报告停用；清理继续尝试全部步骤，次要错误不替换首要原因。仍含私有 URI 的旧 frame 只保留既有最小被动 input guard，不保留活动 transformer/动作登记。
- 启动探针复用生产 adapter 与当前 Pi 的类身份，但只安装到自有 Markdown/fullscreen 子类 prototype；不会在异步等待时临时改写共享 live prototype。自有 hook-owner 标记阻止已有 adapter 经 foreign wrapper 接管探针对象；复制使用独立的内存队列，所有 clipboard/opener/terminal 端点均为假实现，不启动终端、不调用 GUI/进程/tmux、不修改全局 theme/capabilities。退出时退役自有动作、取消 timer、恢复自有原始 descriptor，并核对真实运行时 descriptor 未变。通过后才重新捕获生产 hooks，执行原有四方法原子安装。
- 有界探针覆盖 Tab/中文/行尾空格的精确代码、显式/引用网页及本地链接语义、动作和原生点击/拖选/wheel/overlay 路由、未知私有 URI containment 与清理；不会在启动时运行完整回归套件。先只将 Tab 换成三个空格，保持整份归一化源文不变并核对复制数据，再独立修改 href，最后改变宽度；不读取 token cache 字段。构造时尚未使用的 cache 值不参与准入，自有空白 fixture 建立 frame/尺寸/overlay 状态后检查实际原生 input 消费行为，允许真实 host 延迟初始化缓存。
- 启动 fixture 使用真实 Markdown 渲染和 viewport input，frame/overlay 几何由自有 fixture 提供；它不验证真实 compositor 的缓存生产。原生 fullscreen start 会在部分图片能力分支修改进程级 capabilities，因此探针不启动终端，也不引入私有运行标志。完整原生 composition/lifecycle、懒初始化及能力分支由下述本地隔离回归验证。非 TUI 的 RPC/JSON/print 模式不探针、不安装 hooks。每次真实源文渲染仍重新对齐代码块/链接，有限 fixture 不能证明所有未来解析/布局变化；原生滚轮与 overlay 焦点仍交给 Pi。
- 已证明的本机网页启动先以自有 `spawn`（`shell: false`、有界输出、2.5 秒硬 deadline）执行 `xdg-settings get default-web-browser`，要求单个合法 desktop ID，再以 `xdg-settings check default-web-browser <id>` 确认 `yes`；只接受 `google-chrome.desktop` 并执行 `google-chrome-stable --new-window URL`。不读取 `BROWSER`、desktop entry `Exec`，不调用 `xdg-open`/`gio open` 或备用启动器。deadline/abort/输出溢出独立结算，detached 进程组先 TERM、200 ms 内 KILL；leader `exit` 不代表继承管道的 descendants 已完成，正常结算等待 child `close` 或两个输出流关闭。
- 已证明的本机 Chrome/Code 使用参数数组、`shell: false`、`stdio: "ignore"`、detached 子进程。监听器安装、abort 脱钩与 `unref()` 成功才交接；spawn 后同步 handoff 失败先移除 observer，再 TERM+KILL 尚属扩展的组。已接受应用独立于后来代际 abort；可观测 lookup/权限/非零退出在动作仍有效时报告。
- 一个 mounted Markdown 拥有其全部 copy/open/diagnostic action。所有动作都使用同一随机 `pi-copy://<nonce>/<sequence>` 内存命名空间；URI 不包含路径、命令或代码内容。
- 缓存 miss 采用原生收集 pass、一次联合 action commit、装饰 pass；显式链接以 exact `[raw, href]`、自动路径以 native 类型/raw/text/区间/编码目标 key 和计数绑定，不用递归 cursor，因此 table 的测量/输出双渲染不会错位或多分配。代码块仍用源位置、容器路径、围栏结构、语言/meta 和逻辑文本做线性最早/最晚唯一对齐。若私有 action 登记不可用，transcript 中看似仍有的 native HTTP 链接也保持不可执行；不会退回 Marked href 绕过源文语义核对。
- 动作按字段比较，未变 rerender 复用 URI。owner batch 变化时同步删除并 unregister 被替代条目；任何 allocation、Map/WeakMap/WeakRef/finalizer、commit 或 cleanup 异常都会先摘除整个代际，再尽力清理并用全新 prefix 恢复。
- 每代有一个 `AbortController`，且异步工作的唯一 liveness 判据同时要求 adapter enabled、仍是 current generation、无 poison、signal 未 abort、store trusted。队列中的 clipboard 写入在真正开始前及所有成功/失败反馈前都重查；当前代际一旦 poisoned 会先 suspend/retire，不能继续执行已排队副作用。退役先从 current generation 摘除，再 abort 浏览器探测/文件检查并静默迟到反馈。已经完成 handoff 的 Chrome/Code 只移除 observer，不会被 kill，窗口可以独立于 Pi 存活。
- Clipboard 仍使用进程级 `Symbol.for` 串行队列，跨代际、adapter 和 `/reload` 保证最后点击的写入顺序；打开动作不进入该队列。
- 普通 local/private link 点击和拖选走 Pi 原生 viewport 状态机，但临时 opener filter 永远挡住所有 `pi-copy://`。只有 stored action kind 决定手势：copy 保留原行为；open/diagnostic/expired 必须是准确 Ctrl+左键，press/release 都满足且无 Shift/Alt/扩展按钮别名。
- Overlay URL 只由准确 geometry 或 overlay-owned capture 授权。卸载先退役 action、失效 owner、强制同步 composed frame；仅证明 fullscreen 中无私有 URI 后才恢复 input hook。失败则保留单层最小被动 guard；后续实例吸收它，不保留前任闭包，也不覆盖后装 foreign wrapper。
- tmux 3.4 的受管鼠标绑定在每个原始 SGR 事件前发送 `CSI 998;<client_pid>~`。仅在 `MouseDown1Pane` 等于已验证的 stock `select-pane -t = ; send-keys -M` 时接管它：先按原顺序选 pane，再对本扩展 pane 注入 marker、原样执行 `send-keys -M`；不带本扩展 owner 的 pane 仍依次执行 stock 的 `select-pane -t =` 和 `send-keys -M` 命令；真实双-pane 鼠标透传尚待单独验收。原本无绑定的 `MouseUp1Pane` 加入同样条件透传。旧版 Ctrl 绑定只在当前命令和记录的 receipt 完全一致、且属于已知旧版命令时升级；自定义/版本不符的绑定保留并在 `/copy-links` 告警。不改 `.tmux.conf`、`mouse` 设置或其他 pane。卸载只恢复 pane 所有权；共享绑定保持无主惰性状态，重载凭 receipt 复用。
- 扩展加载时从 Pi fd0 的 `/dev/pts/N` 出发，先验证会话首进程，再验证主端持有者的 `/dev/ptmx` `tty-index`；非 dumpable sshd 用 `sshd: <user>@pts/N` cmdline、父子关系和 UID 验证。候选失败时的 `/proc` 全扫描有 750 ms 总期限，超时即 unknown。tmux server 由运行中 image 身份、server comm、socket/pane 与 `pane_tty` 共同验证。Relay 只接受 `/usr/bin/script`，或 Python interpreter 加 Codoxear console-script/module cmdline；还要求 fd0 PTY 等于 stat 控制终端且 `tpgid == pgrp`，最多四层。任何其他 holder 不从 fd0/job-control 结构猜 relay。
- 本机显示证据只检查 Ghostty holder 自身。一次有界 `ss -xH -a -n` 必须证明它自己拥有 `socket:[inode]`，其 peer inode 对应 `/run/user/<uid>/wayland-*`（也接受该进程 XDG runtime/Wayland 的实际已连接路径）或 `/tmp/.X11-unix/X<n>`/`@/tmp/.X11-unix/X<n>`。祖先连接、环境变量、comm 或没有 SSH 痕迹均不授权。程序身份从 `stat(/proc/<pid>/exe)` 保存运行中 image 的 `dev+ino`；若 readlink 以 ` (deleted)` 结尾，会先 stat 那个**字面完整路径**：存在且 dev+ino 等于运行 image 时保留全名（所以 `ghostty (deleted)` 不是 Ghostty），只有字面路径不存在或指向另一 inode 时才判为内核删除注记并去掉一个尾缀。
- 动作派发时，已知拓扑廉价复核 Pi fd0、每跳 leader/holder PID+starttime、运行中 image dev+ino、原 tty-index/sshd 证明和 relay fd0/控制终端/前台组；一旦 image 相同就保留加载时已证明的 terminal/tmux/relay 角色，不再从可能被 rename 的当前路径重推角色，Codoxear 仍重查 broker argv。apt 替换路径或把运行 image 临时 rename aside 不会中断缓存路由；真正 exec 成另一 image 会拒绝。fresh `/reload` 对陌生 rename-aside basename 仍可拒绝。Ghostty holder 不重跑 `ss`。只有加载时 unknown 才在每次点击完整重试一次；若此时发现 tmux，提示空闲时 `/reload` 以获取绑定。直接拓扑出现 marker 仍拒绝。
- 每个 tmux 按下/松开都带相同 client PID 才能触发动作；窄 lost-press 恢复也要求先前普通 release 与 orphan Ctrl release 的 PID、cell、私有 URI/action kind 相同，并保持屏幕、尺寸、overlay ownership/snapshot 和无 motion。tmux Ctrl+点击缺失或畸形 marker 时报告错误，不打开链接。打开前再次枚举全部 tmux 客户端，校验准确 PID、当前 pane、完整 `/proc` 祖先链及 starttime；随后对该 client 的 PTY holder 应用相同的程序输入契约。sshd 直持 client PTY 才是远端，受支持 emulator 自身的 Unix display peer 才是本机；未知 holder/多路复用器拒绝。实际 Node `os.hostname()` 选择相反的固定别名，`ssh -G` 验证字面 IP/用户/端口；别名与对端一同冻结在 receiver 中，并参与拓扑相等性。派发只使用该已准入别名和钉住的字面地址，不从路径或 JSON 接受别名，避免 alias 在验证后变化时发往另一主机。

### 配对 SSH 输入、协议与隧道

Pi 服务器仍使用同一 `parseOpenTarget` 解析源文 href；文件在**服务器侧** `stat` 跟随 symlink，验证普通文件/目录、位置规则和冒号+goto 限制，向桌面发送资源 `kind` 与绝对路径。桌面助手只用实际 Python `socket.gethostname()` 选择相反的固定服务器：公司桌面→`conley-Y9000p`，Y9000p 桌面→`conley-company`。它不 stat 服务器路径，也不接受请求提供的主机 authority；失败不会回退到任一错误桌面。

服务器向已验证的桌面对端发起固定的非交互 SSH 命令，强制固定刚核实的 HostName/User/Port，带 `-T`、`BatchMode=yes`、`ClearAllForwardings=yes`、`StrictHostKeyChecking=yes` 和 `ConnectTimeout=8`；目标传给 `~/.local/bin/pi-client-link-helper` 的 **v2 JSON stdin**，不拼入 shell/SSH argv。连接保持打开：助手先验证目标、准备 GUI 与自有转发，返回携带 nonce 和原目标的 `PREPARED`，此时**不打开**浏览器/Code；服务器核对目标并重查动作仍有效，才在同一连接发送匹配 `COMMIT`。助手在有限非阻塞锁等待中检查 EOF 和最长 45 秒准备 deadline；COMMIT 前 abort/断线/超时不会在锁释放后补开窗口。接受 COMMIT 后桌面 handoff 独立进行，后来服务器超时不能撤销已授权的打开。最终结果仍需符合目标、回复次序、退出状态、64 KiB stdout 上限和服务器侧 60 秒 deadline；错误不降级为服务器本机打开。

服务器回环网址保留原 URL、主机名和端口。`localhost` 源网址在发 SSH 前并行探测服务器 `127.0.0.1`、`::1` 的原端口 TCP（不发 HTTP/TLS），共享 1.5 秒可中止期限；仅一边可达则选择该数字后端，两边可达按服务器 OS 的 `localhost` 解析顺序选首个已验证地址，均不可达或解析不明确即失败。v2 请求只在 `localhost` 情况附加 `backendHost`；显式 `127.0.0.1`/`[::1]` 以其源 IP 为服务器数字后端。桌面助手用一个钉住 `ssh -G <相反服务器别名>` 字面 IP/User/Port 的私有 SSH master，在桌面请求 **原端口**的 `-L localhost:<端口>:<数字服务器后端>:<端口>`。对所有受支持的回环源，必须在同一次 `ss` 清单中证明桌面的 `127.0.0.1` 与 `::1` 原端口监听都归同一个 master PID；在 `PREPARED` 前和浏览器 handoff 前各复核一次。服务器核对 `PREPARED`/最终回复的原 URL、数字后端和两端原端口。端口被其他监听器覆盖（包括任何单边占用）、无权绑定或 bind race 返回 `port_conflict`；不会接管、杀掉其他转发，不改写成替代端口/URL。旧单栈显式 IP record 不复用；无法覆盖同端口跳转目标的非标准 127/8、IPv4-mapped loopback 等地址会在打开前拒绝。

这保证已验证的**初始端口**和同端口跳转到 `localhost` 时，两种标准浏览器回环地址都通往选定的服务器后端；重定向到**其他端口或其他 authority** 尚不在这个所有权保证内。TCP 探测/隧道准备也不证明 HTTP 服务之后持续可用。外部网页不建服务器回环隧道；文件/目录使用 VS Code Remote-SSH typed URI 与明确的 `--file-uri`/`--folder-uri`，请求 `--new-window`。本机 Chrome/Code 也只请求新窗口，不控制 GNOME Wayland 的最终 workspace。桌面助手向受支持的默认浏览器显式发送 `--new-window`，是否获得焦点以及页面是否成功加载仍属于物理桌面验收边界。

## 开发者诊断与本地复现

以下命令用于扩展开发和故障排查，不是逐 Pi 版本的用户准入流程：

```bash
cd pi-agent/extensions/pi-copy-links
npm ci --ignore-scripts --legacy-peer-deps
npm run check

python3 test/native-smoke.py
python3 test/native-smoke.py --osc52
python3 test/native-smoke.py --tmux
python3 test/native-smoke.py --tmux --regular-start
python3 test/native-smoke.py --path-boundaries
```

客户端安装器及 fake-endpoint 接收助手回归从仓库根目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s pi-agent/extensions/pi-copy-links/client/tests -v
```

`test/paired-path.test.ts` 还把两个生产组件直接接起来：同一源路径在两行角色中经自动路径识别、真实服务器 `stat`、钉住的 SSH argv、真实管道上的 v2 PREPARED/COMMIT，最后到 Python 助手的精确 Code remote URI；桌面端同名路径 `stat` 是失败 tripwire。它只替换 SSH/桌面命令端点和测试进程的 OS hostname，unknown 服务器/助手均须拒绝。接收器 PTY/sshd 权限由 receiver 回归单独覆盖；此组合 fixture 不代替真实反向 SSH/GUI 证明。

`--legacy-peer-deps` 只用于测试依赖安装，避免 npm 自动装入与正在运行的 Pi 不同身份的 peer；`npm run check` 中的 `test/setup-pi.mjs` 核验并链接本机已安装的 Pi/TUI，发现不匹配的第二份运行时会拒绝执行。验收以实际 `pi` 的 bundled CLI 和上面的离线 PTY/recorder 为准，不用未打包入口代替。

单元/集成测试覆盖代码源文、typed action failure atomicity、poisoned-generation clipboard queue、live cwd snapshot、Markdown recursion/table/reference、显式/引用网页及本地链接语义、完整目标语法、browser/Code 启动、PTY holder/identity-bound relay/Ghostty-own-display/SSH 分类、signed `ss` inode、扩展 pts minor、deleted executable image、来源 PID 与客户端地址钉住、fake SSH 协议/失败/abort/no-fallback、async retirement、selection/overlay/tmux recovery/reload/quiescence。Native smoke 使用临时 Pi profile、真实 CLI/PTY 和 recorder executables：`wl-copy`、`xdg-settings`、`google-chrome-stable`、`code`；`xdg-open` 是失败 tripwire。普通和 tmux 模式把 Python 可执行文件复制或 hard-link 为测试私有的 `ghostty` executable 后重新 exec，使 PTY-master 的 `/proc/<pid>/exe` realpath basename 真正满足 emulator 身份；同一进程还持有到私有 `/tmp/.X11-unix/X<n>` fake server 的真实 Unix stream。软链接改名不被接受。`--osc52` 保持 Python 身份且不创建显示连接，链接必须拒绝且无 GUI 端点。四种模式还通过真实 PTY 拖选普通文字，核对原生 selection 写出的 `Native`，并核对普通链接/自动路径点击和 Ctrl-drag 不打开应用、`/reload` 后 action URI 更换、窄屏复制字节不变。截图文件名 `state43_real_grid_1080p.mp4` 和 `reports/中文%#?.png` 分别以行内代码/正文出现：本机模式 Ctrl+左键得到同一 Code new-window argv，窄屏重试 MP4 仍指向同一文件；OSC52 的未知来源保持拒绝，不启动 GUI。`--path-boundaries` 另建完整括号/逗号文件、部分格式化文件名和同时存在的前缀/后缀 decoy，记录没有普通点击 priming 的真实 Ctrl press/release：完整路径得到精确 Code argv，部分文件名和 `cat ~~a~chart.png~~` 碰撞不打开任何文件；单元回归以 100/200/400/800 项的索引工作计数和 query cache 检查增长，不使用易漂移的固定耗时门槛。自动测试不会启动物理 Chrome/Code，也不会修改物理剪贴板。用 `--artifacts <目录>` 为每次 native 运行指定独立证据目录；默认也保留临时目录。失败时仍保存 `run.json`、PTY/input 原始流、已有 recorder JSONL、snapshot 和人工 fixture 的 session/settings，便于从实际输入/输出定位分歧；这些文件属于隔离测试 profile，不来自用户会话。

卸载前确认 `~/.pi/agent/extensions/pi-copy-links` 是本扩展 symlink，删除后 `/reload`。源码和依赖可保留。
