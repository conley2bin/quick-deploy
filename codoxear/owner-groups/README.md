# 负责人分组（独立、可选的服务端适配器）

给**电脑和手机使用同一 Codoxear 地址**增加原生会话分组。源码和依赖归 quick-deploy；不修改、复制覆盖或固定上游 checkout / site-packages。默认 `../run_server.sh` 行为保持不变，只有显式 `--owner-groups` 才启用。

## 看见什么

- 原有侧栏里的**同一批原生会话行**，按登记簿 `parent` 放进白色圆角细框，配轻微中性阴影。原生主题、字体、行高、时间/未读/忙碌标记、选中状态和操作不变；没有新增平行面板、角色看板或「打开会话」按钮。
- 每个有匹配会话的最高团队一个框，最高 `parent:user` 负责人固定第一行，在自己的原生显示名称**紧后方同行**显示圆润亮黄色 SVG 星星（提示/无障碍名称「最高负责人」），使用原生标题行间距/对齐；标题文字和省略号逻辑不变。星星随标题内容移动，不再绝对定位到卡片角落，也不在 hover 时隐藏。未选中的最高负责人也有独立浅中性色细边卡片，使用原生圆角/主题色，星号归属于该行；选中样式仍由原生主题决定。所有后代负责人平铺对齐，不做嵌套树卡片。同仓库/同项目不决定归组。
- 框的顺序不跟会话活跃度变化。首次按 canonical registry 的登记插入序号排列；之后新团队追加，现有框顺序保留，并仅在当前浏览器该地址的 localStorage 保存团队 ID 顺序。刷新/重载稳定；同样首次目录的客户端顺序确定。不同客户端若经历不同的重新挂接/新增根过程，历史追加顺序可能不同，不写服务器或登记簿来协调。
- 下级只按**真实上游最新原生渲染顺序**在自己的框内排序，最高到第二行；原生重建时更新排序依据，不从自己调整后的 DOM 反推活跃度。新增成员出现在所属框内。框的高度随成员增减，固定的是框序而不是屏幕像素坐标。
- 整个团队没有匹配原生行时不显示空框，但保留其顺序槽位；会话重新出现时回到原位。最高负责人没有匹配原生行、但下级有匹配行时，仅显示「姓名 · 会话不可用」的小字，不制造可点击会话、不给下级加星。未登记上级的可匹配行放在虚线框，标「上级待确认 · 未提升下级」，无星。身份缺失、重复或日志未绑定的会话留在框外；交接历史不推断当前身份。
- 无关/未绑定会话仍在同一列表框外。原生首段标题（通常为 NOW）保留在最上方，框紧随其后；首段计数包含所有框内会话及该段框外会话，全为 NOW 时等于原生总数，即使 NOW 会话全部入框也保留标题。后续 Waiting/Later 标题只统计各自剩余框外会话，没有剩余行时移除；停用时恢复所有原生标题和原始计数。空登记簿直接显示完整原生列表。不会根据标题、别名、cwd 或路径补造归属。
- 演示服务器使用合成 API/登记数据，侧栏底部有 **DEMO · 合成数据** 小字；不是生产负责人或在线状态。

## 安装、启用和回退

从 `quick-deploy/codoxear/`：

```bash
./owner-groups/setup.sh
```

只在 `owner-groups/.venv/` 安装锁定的 aiohttp 依赖，优先使用 uv，没有 uv 则使用 Python venv/pip；需要 Python 3.10+（无 uv 时系统需提供 venv/pip）。**不启动、更新或重启任何服务**，不写浏览器配置、全局 prompts/settings 或登记簿。

完成独立测试并获准切换后，在计划维护窗口停止原 Web server，再运行：

```bash
./run_server.sh --owner-groups
```

此命令仍先执行既有远程 main 同步/安装流程，然后：

1. 从原 `.env` / shell 环境读取**原前端 host、port、URL prefix、cookie 配置**。
2. 适配器占用原前端地址；上游同一可执行文件在临时私有 `127.0.0.1` 端口启动，HOME/状态/认证 secret/前缀不变。公网反代仍指向原地址；不新增 TLS、密码或公开端口。IPv6 wildcard 继续采用上游的 dual-stack socket 行为。
3. Ctrl-C 停止适配器和它自己启动的后台 Web server；上游提前退出也会停止这个入口，不无限重启。端口已占用则在启动后端前失败，不停止现有进程。后端刚启动时若还未就绪，请稍后重新加载；不会用其他服务兜底。

**本功能不会替你切换当前运行服务。** 需要记录实际启动者/停止方式再安排切换，不要对未知进程执行 kill。既有反代若只放行固定路径，需一并允许前缀下 `__owner_groups__/`；应由反代负责人确认，不在此自动修改。

回退：停止该可选入口，再按原方式运行 `./run_server.sh`。原生入口不依赖适配器。停用后可删除**本功能自己的** `owner-groups/.venv/`；保留 quick-deploy 源码以便维护。没有浏览器插件、native host 或系统服务需要卸载。上游 service worker 可能在离线时显示旧的缓存 HTML；联网重新加载原生入口恢复，适配器不可用时自动恢复原生列表。不自动清除用户浏览器缓存。

## 身份和数据边界

固定私有来源：`~/.pi/agent/.memory/local/responsibility-registry/registry.json`。先只读打开已存在的 `registry.lock` 并取共享锁，再读取快照；不运行会修复 dashboard 的 registry CLI，不初始化/修复/写入任何文件。

只导出当前 owner 的 id/name/role/parent、登记插入序号 registration_order（canonical CLI 保存对象插入顺序，无活动时间/历史）、**source=current-session 已核验 Pi ID**、身份缺失说明、简短 project/task/status。历史、recovery、联系别名、任命证据、目标/验收正文、目录路径、transcript 均不导出。500 owner / 64 层 / 8 MiB 原始快照上限，超限整树拒绝，不截断祖先。重复 Pi 身份不绑定、不挑一个；对应原生行保持在框外。

数据路由为前缀下 **`POST __owner_groups__/snapshot`，空 body、无 query 参数、只读**。GET 返回 405。选择 POST 是必要的兼容措施：当前上游 service worker 会手动缓存 no-store GET、可能用旧 GET 会话记录兜底；它不拦截 POST。适配器用调用浏览器的 cookie 对固定 loopback 上游 **重新 GET `/api/sessions`**，禁止重定向和缓存、要求 200 JSON 及预期结构，再读取目录，返回最小会话关联：

```text
session_id (完整 opaque broker route key)
thread_id (Pi backend 的实际会话身份)
agent_backend
has_bound_log (仅对可信上游返回的非空绝对 log_path 做只读 regular-file stat 推导；不读正文、不返回路径)
```

上游公开列表会删除内部 `log_exists` 字段，适配器不依赖该字段；stat 在线程中执行，避免阻塞 aiohttp。缺失/相对/目录/不可访问路径均视为未绑定，错误不含路径。浏览器不能提交 stat 目标。

无 cookie/认证失败 401；上游/API/目录不可用 503。无 CORS，拒绝跨 Origin / Sec-Fetch-Site:cross-site，不接受外部 URL、文件路径、owner 过滤或浏览器指定身份。服务不保存 cookie，不输出密码/token/访问 URL 日志。此路由的权限与当前 Codoxear 登录相同，已经登录的用户可读取以上最小目录；不另建账号体系。保留现有 HTTPS/VPN 边界；本功能不把现有明文 HTTP 变成 TLS。

绑定要求：当前 owner 的已核验 Pi ID **精确相等**于唯一 pi-backend 会话的 `thread_id`，该 ID 也只被一个 owner 使用，且有已存在的绑定日志。不使用 intercom ID、别名、文件名、目录、最近时间。全新 POST 快照用于计算分组，每 10 秒在可见页面刷新，失败则恢复完整原生列表并显示简短提示，不用旧绑定兜底。**不拦截或重放点击**：会话行、Edit/Delete/Duplicate、触摸滑动和键盘控件仍是原始节点及原生回调，直接操作该行原始 opaque broker key。分组快照不是事务性身份选择；两次快照之间的 broker 再绑定可能短暂留下旧归组，下一次成功快照会移出错误绑定。上游没有原子条件选择接口。

## 兼容性与诊断

当前探测的上游：`2b14d7092d5e41038361f6bd4026a89e391f7293`。这是测试边界，不是版本固定。依赖原生 `#root`/`dist/app.bundle.js` HTML、`.sidebar > #sessions.sessions`、完整 `.session[data-session-id]` 外层卡片（含 desktop inline / touch swipe DOM）和 `.sessionGroupHeader` 约定。

- 只有受识别的 UTF-8 entry HTML、兼容 CSP 才注入同源外部脚本；脚本带源码 content hash 版本，不覆盖上游 service worker。上游 CSP 不被放宽；最小包装框/星号样式需要既有 inline-style allowance；不覆盖原生行样式。
- 修改 HTML 后重算 Content-Length，去掉原 ETag/Last-Modified/内容摘要并设 no-store。未知 HTML/CSP/压缩或过大 entry 原样通过，响应 `X-Owner-Groups` 明确报告状态。
- `GET <prefix>/__owner_groups__/status` 是不含私有数据的兼容性诊断（`injected` / `unsupported-*` / `not-observed`）。已注入页面若侧栏锚点/行结构变化，恢复捕获的原生卡片及原有标题/计数，在可用侧栏显示简短兼容提示；原 app 仍可用。服务端无法向未注入的未知 HTML 再插入警告，需查看 status/header。
- 普通 API、上传、下载和当前 SSE 都流式转发，固定上游、不跟随后端重定向，保留多条 Set-Cookie；移除 hop-by-hop header。当前上游未使用 WebSocket；未来 Upgrade 请求明确 501，不能假称支持。若上游改协议/身份字段/DOM/CSP，修这个独立适配器或回退原生入口，不修改上游来迁就增强。

## 独立演示和验证

以下测试不使用真实 owner、会话、消息或密码。先安装本功能依赖；从 `codoxear/`：

```bash
CODOXEAR_TEST_PYTHON="$PWD/.runtime/venv/bin/python" \
  owner-groups/.venv/bin/python -m unittest discover -s owner-groups/tests -p 'test_*.py' -v
node --test owner-groups/tests/core.test.cjs
```

可选真实上游认证集成测试使用**现有 runtime 解释器只读导入**，临时 HOME/cwd、随机且不打印的测试密码、loopback 随机端口；从不启动真实 agent。未设置 `CODOXEAR_TEST_PYTHON` 则跳过该项。其他 HTTP 测试验证认证失败/重定向拒绝、无跨请求 cookie 缓存、固定请求、SSE 首事件未等上游完成即到达、上传/压缩/重定向和 cookie 保留。

可视化演示使用实际安装的上游 frontend + service worker **原样只读**，由独立合成 API 和 CLI 在临时目录建立的合成登记簿供数：

```bash
STATIC_DIR="$(.runtime/venv/bin/python -c 'from pathlib import Path; import codoxear; print(Path(codoxear.__file__).parent / "static")')"
owner-groups/.venv/bin/python owner-groups/tests/demo_server.py --static-dir "$STATIC_DIR" --port 8875
# 独立地址 http://127.0.0.1:8875/demo/；输入 demo 即可。不是生产账号。
```

只监听 loopback；`__fixture__/state` 测试控制端点仅存在于这个测试服务器，生产适配器不存在。退出删除临时登记数据，不留下真实任命。长任务应放在独立 tmux 中，用完成通知收集结果；不要改动当前服务端口。

另一个终端运行已安装的 agent-browser（独立临时浏览器，不用用户 profile）：

```bash
AGENT_BROWSER_BIN=/path/to/agent-browser \
OWNER_GROUPS_SCREENSHOTS=/tmp/owner-groups-screenshots \
  node owner-groups/tests/browser.test.cjs
```

可用 `CHROME_BIN` 指定实际 Chrome 可执行文件。测试两框固定顺序、根始终第一、B 活动只在 B 内到第二、三级后代平铺、成员/新组追加、重载稳定、交接/重复/未解析/缺失根、零会话团队隐藏/回归槽位、全入框时首段标题/混合分段计数、未选中根卡片、空列表/失败恢复、native polling 重建、重复注入、logout/login、无 observer 自触发循环和 SW 缓存 GET 与全新 POST 差异。桌面测试通过临时 Chrome 启动包装器设置真实 Blink fine-pointer/hover 媒体能力，执行上游 desktop inline 分支的选择、悬停 Edit 保存、Tab/Enter/Escape、Delete 取消及 Duplicate；另用独立无 hover 的 390px 浏览器执行上游 touch 分支：通过该临时浏览器自己的 CDP Input.dispatchTouchEvent 发送真实浏览器触摸输入，验证左右滑动、Edit 和 Delete 取消，并用真实点击验证原生选择关闭侧栏。普通 mouseup 的兼容 click 会被上游用于关闭滑动，不能用鼠标拖动冒充触摸滑动。没有真实触屏硬件/iOS Safari 覆盖。临时包装器不是上游源码修改，测试结束删除。

截图使用内置 Slate/Light（由测试点击原生 Settings 切换，仅影响隔离测试浏览器），与批准示意图一致；增强不会替用户切主题。截图只含 DEMO，不能当作生产已激活或真实负责人在线的证据。
