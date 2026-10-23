# 负责人分组（独立、可选的服务端适配器）

给**电脑和手机使用同一 Codoxear 地址**增加负责人侧栏。源码和依赖归 quick-deploy；不修改、复制覆盖或固定上游 checkout / site-packages。默认 `../run_server.sh` 行为保持不变，只有显式 `--owner-groups` 才启用。

## 看见什么

- 以登记簿 `parent` 构造跨项目/方向的树；一个顶层团队一个框，下级嵌套、可逐项或全部展开/折叠。**真正 `parent:user` 的最高负责人标题左上角显示 ★，无障碍名称/提示为「最高负责人」**。
- 未登记上级显示「上级待确认：owner:…」，不提升为顶层、不加星。同仓库/同项目不会合并不同上级的团队。
- 姓名、角色、职责状态、范围和会话可定位数量可读；登记存在/责任进行中均不表示在线。无会话的祖先仍保留；身份缺失、重复或日志尚未绑定有明确说明。
- 原生会话列表和全部操作原位保留，增强不移动、隐藏或接管原生行；分组面板最多占视口高度 55%，原生列表仍可滚动。手机继续使用原生菜单按钮打开侧栏，选择分组会话后通过原生按钮关闭侧栏。
- 空登记簿明确显示「尚无已登记负责人」。不会根据会话标题、别名、cwd 或项目路径补造责任；演示数据只在测试服务器，带 **DEMO · 合成数据** 标记。

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

回退：停止该可选入口，再按原方式运行 `./run_server.sh`。原生入口不依赖适配器。停用后可删除**本功能自己的** `owner-groups/.venv/`；保留 quick-deploy 源码以便维护。没有浏览器插件、native host 或系统服务需要卸载。上游 service worker 可能在离线时显示旧的缓存 HTML；联网重新加载原生入口恢复，适配器停用时增强不能导航。不自动清除用户浏览器缓存。

## 身份和数据边界

固定私有来源：`~/.pi/agent/.memory/local/responsibility-registry/registry.json`。先只读打开已存在的 `registry.lock` 并取共享锁，再读取快照；不运行会修复 dashboard 的 registry CLI，不初始化/修复/写入任何文件。

只导出当前 owner 的 id/name/role/parent、**source=current-session 已核验 Pi ID**、身份缺失说明、简短 project/task/status。历史、recovery、联系别名、任命证据、目标/验收正文、目录路径、transcript 均不导出。500 owner / 64 层 / 8 MiB 原始快照上限，超限整树拒绝，不截断祖先。重复 Pi 身份显示冲突，不挑一个。

数据路由为前缀下 **`POST __owner_groups__/snapshot`，空 body、无 query 参数、只读**。GET 返回 405。选择 POST 是必要的兼容措施：当前上游 service worker 会手动缓存 no-store GET、可能用旧 GET 会话记录兜底；它不拦截 POST。适配器用调用浏览器的 cookie 对固定 loopback 上游 **重新 GET `/api/sessions`**，禁止重定向和缓存、要求 200 JSON 及预期结构，再读取目录，返回最小会话关联：

```text
session_id (完整 opaque broker route key)
thread_id (Pi backend 的实际会话身份)
agent_backend
has_bound_log (仅由非空 log_path 且 log_exists===true 推导；不返回路径)
```

无 cookie/认证失败 401；上游/API/目录不可用 503。无 CORS，拒绝跨 Origin / Sec-Fetch-Site:cross-site，不接受外部 URL、文件路径、owner 过滤或浏览器指定身份。服务不保存 cookie，不输出密码/token/访问 URL 日志。此路由的权限与当前 Codoxear 登录相同，已经登录的用户可读取以上最小目录；不另建账号体系。保留现有 HTTPS/VPN 边界；本功能不把现有明文 HTTP 变成 TLS。

绑定要求：当前 owner 的已核验 Pi ID **精确相等**于唯一 pi-backend 会话的 `thread_id`，该 ID 也只被一个 owner 使用，且有已存在的绑定日志。不使用 intercom ID、别名、文件名、目录、最近时间。点击时成功读取全新 POST 快照并再次核对 owner Pi ID + broker route，变化/失败就撤掉旧导航并提示，不使用内存旧快照兜底。通过原生 `hashchange` 选择完整 `session_id`，保留其他 hash 参数。**核验后与实际路由间仍可能发生 broker 再绑定；上游没有原子条件选择接口，因此不能声称事务性防竞态。**

## 兼容性与诊断

当前探测的上游：`2b14d7092d5e41038361f6bd4026a89e391f7293`。这是测试边界，不是版本固定。依赖原生 `#root`/`dist/app.bundle.js` HTML、`.sidebar > #sessions.sessions`、原生 active 行和 hash route 约定。

- 只有受识别的 UTF-8 entry HTML、兼容 CSP 才注入同源外部脚本；脚本带源码 content hash 版本，不覆盖上游 service worker。上游 CSP 不被放宽；当前 shadow 样式需要既有 inline-style allowance。
- 修改 HTML 后重算 Content-Length，去掉原 ETag/Last-Modified/内容摘要并设 no-store。未知 HTML/CSP/压缩或过大 entry 原样通过，响应 `X-Owner-Groups` 明确报告状态。
- `GET <prefix>/__owner_groups__/status` 是不含私有数据的兼容性诊断（`injected` / `unsupported-*` / `not-observed`）。已注入页面若侧栏锚点变化，显示浮动兼容警告，仅停用增强；原 app 仍可用。服务端无法向未注入的未知 HTML 再插入警告，需查看 status/header。
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

可用 `CHROME_BIN` 指定实际 Chrome 可执行文件。测试真实鼠标点击、原生 hash 选择/编辑对话框、native polling 重建、折叠保持、重复注入、logout/login shell 重建、缺失锚点、空/错误/冲突目录、XSS 字符串、当前上游 SW 的缓存 GET 与全新 POST 差异，以及 390px 手机布局和 root-only 星号。截图只含 DEMO，不能当作生产已激活或真实负责人在线的证据。
