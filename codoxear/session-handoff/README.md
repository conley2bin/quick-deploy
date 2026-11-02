# Pi 会话接力

此扩展由共享 `web-host` 显式加载，与 `owner-groups` 并列，不修改 Codoxear 上游、Pi 全局提示词或负责人登记。仅支持 Pi；Codex/其他后端返回明确的 422。

## 用户流程

在当前原生会话页面选择接力后，新建一个 **新的 tmux Pi 会话**，继承源会话已明确的 cwd、model_provider、model 和 reasoning_effort。恢复提示作为原生创建请求的唯一 `args` 字符串传入；不克隆旧 argv，不 resume，不改变提供商。

1. 记录源日志的真实 Pi header、完整 JSONL 截止边界及哈希。投影是 `latest_committed`，并不声称知道 Pi 尚未持久化的树游标。
2. 新会话独立阅读历史、当前文件及项目记忆，提交理解报告；在收到审阅输入前不推进任务。
3. 原会话空闲且身份仍一致时，收到一次仅审阅消息；它提交针对该报告的审阅，之后仍只担任审阅者。
4. 新会话收到一次意见，按当前证据核对/纠正。提交 `ready` 回执后正常继续；无法消除的目标分歧提交 `conflict` 并停下等待用户。不再发起第二轮互审。

### 新窗口名称

接力以请求时原生列表的 `display_name` 为准（旧列表无此字段时采用原生的 trimmed alias / cwd basename 规则），冻结原名与新名。普通名称追加 `_01`；只有末尾 ASCII 两位 `_NN` 才递增，含 `_00 → _01`、`_09 → _10`、`_98 → _99`。`_99` 在创建操作与新会话之前返回 `sequence_limit`；`_1`、`_001`、`_100`、全角数字及中间的 `_NN` 保留为名称正文后追加 `_01`。没有全局计数或碰撞跳号。同一操作刷新、换请求 UUID、源改名后重试均沿用已冻结名称。

新名称必须能被原生 alias 和窗口镜像原样保存：最多 80 字符，不能包含控制字符、会被原生折叠的空白，也不能以 `-` 开头；不满足则创建前返回 422，请先缩短或调整源名称，不会静默清洗或截断。中文、单空格、引号及 shell 特殊字符按普通文本保留。

创建参数与首条恢复提示保持不变。目标经 launch/broker/nonce 和真实 Pi header 绑定后，扩展仅对该目标调用一次原生 `/rename`，核实响应 alias 及新列表中的 alias/display_name，再进入原有审阅闭环；原会话不改名。名称从不参与身份匹配。

原生 `/api/sessions` 会触发已有异步 tmux 镜像，以 `spawn_nonce ↔ @codoxear_nonce` 和实际 `@windowID` 定位。**alias 已验证不代表真实窗口已经完成改名**：原生 mirror 随运行时初始化启动，没有单独启用开关；窗口同步依赖 worker 正常运行、可达的 tmux 与其 1.5 秒 debounce / 10 秒 throttle。扩展不直接操作 tmux，也不读取 launch-time `tmux_window` 冒充当前标题。

`reviewed=true` 只表示符合协议的审阅产物已被接受，不是语义正确的保证。HTTP 200 是原生接受输入，不是理解完成；`ready` 必须有匹配这次 source、understanding、review 哈希的 resolution 回执。

源会话暂时不可见、迟迟不空闲或审阅超时显示 **未校对**。只有用户明确点击继续，服务才允许 `review_sha256=unreviewed` 的接续回执；不会把未经校对说成审阅通过。若超时后审阅已经到达，继续操作会先采用该审阅，不隐藏它。

## 失败与恢复

- 创建返回的 `launch_id`（pending 字符串）或 `broker_pid`（数字）用于绑定目标，随后固定 `spawn_nonce`、原生路由及真实 Pi header/日志身份。不按 alias、cwd、文件名或“最新日志”猜测。
- 新 Pi 的日志可以懒生成，列表也可能先公布尚未写完的日志路径。首条恢复提示在创建时已经传入；完整日志头出现是后续身份/产物核验门槛，不是启动输入的前置条件。
- 源在审阅前被重新绑定、目标在接续前被替换、产物格式错误/过期/被覆盖，都会阻止下一次发送。
- 原生创建、命名或发送丢失响应，以及创建已接受后的列表/身份/产物观察错误、接续回执超时，显示 `creation_unknown` / `delivery_unknown`。不会自动重建或重发；这些操作仍占用源会话，换 request UUID 仍返回原操作。只有创建前失败或原生创建明确返回无启动证据的 400 拒绝才以 `failed` 释放源会话。
- 刷新/关闭浏览器不取消服务端接力。应用关闭会取消并等待其所有任务；下次启动将未结束操作标为 `interrupted`，不重放任何变更。服务停止不会撤销已经进入 Pi 的输入，`interrupted` 不代表目标 Pi 已停止。
- 接续投递未知或服务中断后，点击“刷新接力状态”会只读核验目标的固定身份和完整产物哈希链；匹配的迟到 resolution 可推进为 `ready` / `conflict`，缺失或不匹配则保留未知/中断状态。此过程不创建、不发送，重启后仍有效。命名响应丢失、名称不符或命名后的列表失败保留源租约，即使刷新已看见目标名称，也不会自动重命名或恢复审阅阶段；须人工核对。其他中断/未知结果需要人工核对真实源/目标会话；此版本没有“重试创建/重发消息”接口。旧会话不会被删除。

## HTTP 和本地状态

全部状态接口都要求同源 POST、JSON，以及每次重新通过原生会话列表验证的现有认证：

- `POST <prefix>/__session_handoff__/start`：`{source_session_id, request_id: UUID, note?}`。
- `POST .../status`：`{operation_id: UUID}`。
- `POST .../continue`：`{operation_id: UUID}`，只对未校对操作有效。

响应为 `{operation: {...}}`，仅含操作标识、源/目标原生路由、阶段、中文消息、校对标记和更新时间；错误为 `{error, message}`。禁止 HTTP 调用者覆盖文件路径、cwd、模型或 URL。没有 CORS。相同 request UUID 返回同一操作，同时接力同一活动源也会合并；同一操作最多登记 256 个重复 request UUID。

运行状态位于 `${XDG_STATE_HOME:-~/.local/state}/codoxear/session-handoff`，目录 0700、文件 0600。状态以小型原子替换和 fsync 保存，**先保存副作用意图，再调用原生接口**。Cookie 只在应用任务内存中存在，不写状态、manifest 或产物；公开错误不返回上游正文或本地异常细节。不要将此目录提交到 Git。

日志快照和投影在应用拥有的线程任务中处理，避免大日志阻塞共享 HTTP 事件循环；关闭时也会等待该任务结束。状态和内存 manifest 均不保留原始 entries。

默认有限等待：原生请求 30 秒、启动身份 120 秒、理解报告 900 秒、源/目标空闲 300 秒、审阅 600 秒、接续回执 900 秒。轮询间隔 0.5 秒只用于观察条件，不是固定启动 settle sleep。Python 测试可以注入状态目录、时钟、sleep、原生 transport 和 deadlines，HTTP 不能更改它们。

## 验证边界

从仓库根目录使用共享虚拟环境运行：

```sh
codoxear/web-host/.venv/bin/python -m unittest discover -s codoxear/session-handoff/tests -p 'test_engine.py' -v
codoxear/web-host/.venv/bin/python -m unittest discover -s codoxear/session-handoff/tests -p 'test_feature.py' -v
```

原生 `/rename` 和 `/send` 没有 expected-header/nonce 条件参数：扩展会在发送前重新查验身份，但无法把“列表核验”和“原生接受输入”合并成原子事务。不要在这段时间手动重绑同一路由；真实原生联调需核验其路由生命周期。

隔离原生窗口命名验证（无模型 API / 凭据）可显式运行：

```bash
HANDOFF_NATIVE_PYTHON=codoxear/.runtime/venv/bin/python \
  codoxear/web-host/.venv/bin/python -m unittest discover \
  -s codoxear/session-handoff/tests -p 'test_native_naming.py' -v
```

该测试创建临时 HOME、appstate、Pi 日志和私有 `tmux -S` socket，通过原生 API 创建/命名、由真实 mirror 收敛，并按固定 nonce 读取真实 `window_name`；它还核验原窗口不变及 `_99` 零创建。Pi 替身仅写有效 header 后等待，不生成理解报告，所以预期接力停在 `understanding_timeout`，不代表语义闭环验收。所有测试窗口和 Web 子进程在结束时清理。

上述普通单元命令默认跳过此 opt-in 测试：使用临时 Pi JSONL、假原生创建/命名/发送响应、真实 Context 产物验证和应用内 aiohttp HTTP 服务；不会启动真实 Pi/tmux、访问用户数据或切换线上服务。它们验证身份、哈希、截止边界、状态和一次性副作用机制，不能证明模型真实理解了目标。需显式启用的专用隔离真实 Pi fixture 及已记录结果见 [真实 Pi 验收](tests/REAL_PI_ACCEPTANCE.md)，其语义证据来自实际理解报告、审阅、核对回执和任务文件结果。原生 UI 配合合成 API 的浏览器验证只覆盖界面交互与共享宿主、分组功能共存，不替代真实 Pi 的语义证据。
