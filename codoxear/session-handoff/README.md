# Pi 会话接力

此扩展由共享 `web-host` 显式加载，不修改 Codoxear 上游或 Pi 全局提示词。仅支持 Pi；Codex/其他后端返回明确的 422。

## 用户流程

在当前原生会话页面选择接力后，新建一个 **新的 tmux Pi 会话**，保留源会话的 cwd。默认沿用原会话列表设置（model_provider、model、reasoning_effort），也可明确选择新会话的完整模型三元组；选择仅用于新会话创建，不修改原会话或全局偏好。恢复提示作为原生创建请求的唯一 `args` 字符串传入；不克隆旧 argv，不 resume。

1. 创建前在应用拥有的线程内记录源日志的真实 Pi header、完整 JSONL 截止边界及哈希。投影是 `latest_committed`，并不声称知道 Pi 尚未持久化的树游标。
2. 新会话独立阅读历史、当前文件及项目记忆，提交理解报告；在收到审阅输入前不推进任务。
3. 原会话空闲且身份仍一致时，收到一次仅审阅消息；它提交针对该报告的审阅，之后仍只担任审阅者。
4. 新会话收到一次意见，按当前证据核对/纠正。`ready` helper 发布不可覆盖回执并返回成功，之后才正常继续；无法消除的目标分歧提交 `conflict` 并停下等待用户。不再发起第二轮互审。

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

- `POST <prefix>/__session_handoff__/start`：`{source_session_id, request_id: UUID, note?, target?: {model_provider, model, reasoning_effort}}`。
- `POST .../status`：`{operation_id: UUID}`。
- `POST .../continue`：`{operation_id: UUID}`，只对未校对操作有效。

响应为 `{operation: {...}}`，包含操作标识、源/目标原生路由、阶段、中文消息、校对标记和更新时间；新操作还返回 `target_settings: {model_provider, model, reasoning_effort, origin}`，`origin` 为 `inherited` 或 `explicit`。错误为 `{error, message}`。禁止 HTTP 调用者覆盖文件路径、cwd、args、后端或 URL。没有 CORS。同一 request UUID 绑定源与完整请求选择（包括未传 target）；不同源或选择返回 `409 request_conflict`。同源未释放操作只有请求选择相同才合并，不同选择返回 `409 target_conflict`，不新增别名或绕过未知结果租约；同一操作最多登记 256 个重复 request UUID。

### 新会话模型参数

`target` 缺省才表示继承；显式值必须恰好包含三个非空字符串，不接受 null、缺项、额外字段、首尾空白、控制字符或超过 256 字符的值，推理级别必须小写。形状错误返回 `400 invalid_input`。新显式操作仅根据同一次认证 GET `/api/sessions` 的 `new_session_defaults.backends.pi` 验证精确 provider/model 成员及 `provider/model` 推理能力（无此列表时采用后端能力，不采用 bare-model key）；`model: "default"` 的任意大小写均不允许。非成员返回 `422 target_invalid`；缺少目录/能力或 `warnings.pi` 说明原生读取回退时返回 `422 target_unverifiable`。这些拒绝不产生操作目录、租约或原生变更。已有操作的 UUID/同源合并先于目录验证，因此目录变更不会阻断已接受操作的观察恢复。

接受时，服务先将 `requested_target` 和 `target_settings` 原子持久化到 `state.json`，再安排工作或调用原生创建。继承只冻结该源列表行的三元组，不取全局默认值、不另读日志推断当前模型；显式选择只要求源 cwd 与原身份有效，不要求源行的旧模型字段齐全。manifest 保持 v1 不变。历史操作可省略新字段；重启不重放创建或投递，也不把损坏的新目标字段当作缺省继承。

`target_settings` 是冻结的**创建参数**，不证明运行时有效值。原生列表在单独切换推理级别后可能暂时保留旧值；同 ID 的第二提供商模型可能未出现在原生目录；原生可选推理级别也可能与 Pi 实际采用值不同。此扩展保留原生边界，不建立第二目录、不自动 fallback、不锁定用户之后的原生模型切换。

可选的 `target_check: {result, stage, observed: {model_provider, model, reasoning_effort}}` 仅来自已有、身份核实后的目标读取，阶段为 `launch` / `named` / `continuation`。`result` 按 `model_differs > effort_differs > not_contradicted` 保留首次最高级别观察，不降级或覆盖同级观察；差异只表示“接力期间列表曾报告 X，与创建选择 Y 不同”，不推断调整原因。相等可能只是原生回显创建字段，不能宣称“已生效”。诊断不改变状态、租约、命名、审阅或重试行为；status 不采集新诊断，历史操作没有冻结参数时跳过。

运行状态位于 `${XDG_STATE_HOME:-~/.local/state}/codoxear/session-handoff`，目录 0700、文件 0600。状态以小型原子替换和 fsync 保存，**先保存副作用意图，再调用原生接口**。Cookie 只在应用任务内存中存在，不写状态、manifest 或产物；公开错误不返回上游正文或本地异常细节。不要将此目录提交到 Git。

日志快照和投影在应用拥有的线程任务中处理，避免大日志阻塞共享 HTTP 事件循环；关闭时也会等待该任务结束。状态和内存 manifest 均不保留原始 entries。

默认有限等待：原生请求 30 秒、启动身份 120 秒、理解报告 900 秒、源/目标空闲 300 秒、审阅 600 秒、接续回执 900 秒。轮询间隔 0.5 秒只用于观察条件，不是固定启动 settle sleep。Python 测试可以注入状态目录、时钟、sleep、原生 transport 和 deadlines，HTTP 不能更改它们。


## 验证边界

从仓库根目录使用共享虚拟环境运行：

```sh
codoxear/web-host/.venv/bin/python -m unittest discover -s codoxear/session-handoff/tests -v
# 可选：现有只读 native checkout + 合成 defaults / 依赖，纯解析和 launch-plan join，不启动进程。
HANDOFF_NATIVE_SOURCE=codoxear/.runtime/upstream \
  codoxear/web-host/.venv/bin/python -B -m unittest discover \
  -s codoxear/session-handoff/tests -p test_engine.py -k test_target_native_pure -v
```

纯 join 将实际捕获的接力创建体传入原生 `parse_new_session_launch_request` / `prepare_launch_plan`，核验精确 provider/model/thinking argv、`CODEX_WEB_*` 环境字段和唯一恢复提示；它证明参数传递，不证明 Pi 运行时采用值。未设置 `HANDOFF_NATIVE_SOURCE` 时仅这一可选用例跳过，不读取默认安装或用户配置。

原生 `/rename` 和 `/send` 没有 expected-header/nonce 条件参数：扩展会在发送前重新查验身份，但无法把“列表核验”和“原生接受输入”合并成原子事务。不要在这段时间手动重绑同一路由；真实原生联调需核验其路由生命周期。

隔离原生窗口命名验证（无模型 API / 凭据）可显式运行：

```bash
HANDOFF_NATIVE_PYTHON=codoxear/.runtime/venv/bin/python \
  codoxear/web-host/.venv/bin/python -m unittest discover \
  -s codoxear/session-handoff/tests -p 'test_native_naming.py' -v
```

该测试创建临时 HOME、appstate、Pi 日志和私有 `tmux -S` socket，通过原生 API 创建/命名、由真实 mirror 收敛，并按固定 nonce 读取真实 `window_name`；它还核验原窗口不变及 `_99` 零创建。Pi 替身仅写有效 header 后等待，不生成理解报告，所以预期接力停在 `understanding_timeout`，不代表语义闭环验收。所有测试窗口和 Web 子进程在结束时清理。

上述普通单元命令默认跳过此 opt-in 测试：使用临时 Pi JSONL、假原生创建/命名/发送响应、真实 Context 产物验证和应用内 aiohttp HTTP 服务；不会启动真实 Pi/tmux、访问用户数据或切换线上服务。它们验证身份、哈希、截止边界、状态和一次性副作用机制，不能证明模型真实理解了目标。需显式启用的专用隔离真实 Pi fixture 及已记录结果见 [真实 Pi 验收](tests/REAL_PI_ACCEPTANCE.md)，其语义证据来自实际理解报告、审阅、核对回执和任务文件结果。原生 UI 配合合成 API 的浏览器验证只覆盖界面交互与共享宿主集成，不替代真实 Pi 的语义证据。
