# Pi 会话接力

此扩展由共享 `web-host` 显式加载，与 `owner-groups` 并列，不修改 Codoxear 上游或 Pi 全局提示词；负责人接续只调用固定 canonical service，不直接写登记文件。仅支持 Pi；Codex/其他后端返回明确的 422。

## 用户流程

在当前原生会话页面选择接力后，新建一个 **新的 tmux Pi 会话**，继承源会话已明确的 cwd、model_provider、model 和 reasoning_effort。恢复提示作为原生创建请求的唯一 `args` 字符串传入；不克隆旧 argv，不 resume，不改变提供商。

1. 创建前在应用拥有的线程内调用固定 installed succession API，冻结源实际 Pi 的只读 owner plan；服务缺失或无效则零原生变更失败。记录源日志的真实 Pi header、完整 JSONL 截止边界及哈希。投影是 `latest_committed`，并不声称知道 Pi 尚未持久化的树游标。
2. 新会话独立阅读历史、当前文件及项目记忆，提交理解报告；在收到审阅输入前不推进任务。
3. 原会话空闲且身份仍一致时，收到一次仅审阅消息；它提交针对该报告的审阅，之后仍只担任审阅者。
4. 新会话收到一次意见，按当前证据核对/纠正。`ready` helper 先确认登记 receipt 的当前绑定/无 owner 条件，再发布不可覆盖回执并返回成功，之后才正常继续；无法消除的目标分歧提交 `conflict` 并停下等待用户。不再发起第二轮互审。

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


## 自动负责人接续（未来新操作）

源会话有唯一既有 owner 时，新 Pi 继承**同一稳定 owner UUID**，由 canonical service 条件更新 identity/recovery；保留提交时最新上级、下级、assignment、依赖，不制造 assignment handover。无 owner 时也必须通过 fresh absence check，返回 `not_applicable/no_owner_valid`，不任命、不增加登记 revision。历史 manifest 无 `succession` 字段仍按旧 context-only 协议运行，不追溯迁移。

`succession.py` 只加载 `~/.pi/agent/skills/responsibility-registry/scripts/session_succession.py` 的四个 frozen v1 函数：`prepare_owner_session_succession`、`build_intent_digest`、`commit_owner_session_succession`、`get_owner_session_succession`。无 runtime path/Store/browser owner/authority 参数，无 stub、读缓存替代或静默降级。验证函数签名、精确 envelope/plan/receipt/error；摘要由 service 计算。Python 测试可显式注入 service double，HTTP 不能。

### 私有协议和放行顺序

所有文件位于本操作 0700 目录，0600、不可覆盖发布，除 `state.json` 外均不修改：

| 文件 | 内容 / 生产者 |
| --- | --- |
| `succession-source.json` | host 在 native create 前冻结 source plan、随机 challenge、本次按钮同意及窄功能授权 attestation；不从 note/history 导入授权 |
| manifest `succession` | version=1、source capsule SHA256、challenge SHA256；新增协议标记，不改变旧 manifest |
| `succession-target.json` | host 精确绑定 route/launch/nonce 后记录实际 Pi header ID、首行 hash、file/device/inode/cwd；不按名字/文件名猜身份 |
| `succession-identity.json` | target 用 `attest --kind identity` 提交实际 current-session Pi/file + challenge；实际 connected Intercom status 才能提供联系 ID，否则 null + 实际原因 |
| `succession-custody.json` | old 用 `attest --kind custody` 报告实际子代理 custody，关联 operation/understanding hash；known_active/none_verified/unknown，不自动 reparent |
| `succession-intent.json` | host 在 continuation 前冻结源 immutable plan + exact target/authority/custody、报告 hash、service digest；token/revision 不是 digest 输入 |
| `succession-attempt.json` | helper 在首次可能有副作用的 commit 前持久化 operation/digest，后续任何不确定结果必须 get-first |
| `succession-receipt.json` | canonical service 初次有效 envelope；不含原 challenge 或私有关系图；恢复与 host 核验仍 fresh get |
| `succession.lock` | 本地 helper `flock`，把 resolution 存在检查、canonical gate 和原子 no-overwrite 发布串行化 |

`attest` 输入只能是本操作内 `*-draft.json`，精确 JSON 字段见 `prompts.py` 与 `tests/test_succession.py`。这些是 cooperative provenance：host 的 header/transport 事实与 target 自报交叉核对，不是对恶意同 UID 文件控制者的密码学认证。无法调用实际 Intercom status 时只缺联系 ID，不阻断已证明的真实 Pi。旧会话审阅不能伪称子代理为空；显式未校对路线固定记录 unknown，后续不能执行依赖未解决 custody 转移的动作。

ready helper 的执行顺序是：验证原报告链/host target/challenged identity/immutable intent → fresh prepare 且 immutable plan 完全相同 → canonical commit → `committed|already_committed + current_binding_valid` 或 `not_applicable + no_owner_valid` → 本地 ready 发布 → CLI 0 → 任务动作。`conflict` resolution 不 commit。`needs_recovery/superseded/not_found`、reset/rebind/occupied target、服务异常或无效 capsule 都不放行。

只有明确 `document_conflict`、retryable=true、commit_state=not_committed 才允许同操作/同 digest 下再 prepare 一次并重试；不改 immutable plan/target。任何 unknown 都保留 attempt，下一次只 get 原 operation/digest，`not_found` 不是“没提交”的证明。登记 commit 与 ready 是两个资源：commit 后发布失败，目标仍须停止；同 intent fresh get 仍有效才能恢复本地发布，不产生第二登记事件。已经存在 resolution 时，重复 CLI 在服务副作用前拒绝，不算新许可。不承诺任意外部工作 exactly-once。

Engine 看到 ready 文件还会核验私有 capsule 和 fresh canonical get；不能靠单个文件或事后迁移宣称 ready。旧会话只审阅/保管旧子代理并转送真实结果，登记绑定改变后不再是当前 owner。新会话以 owner 身份动作前须核对自己实际 Pi，并运行固定 helper `check-owner --manifest ...` 重新 get 当前有效绑定；旧 receipt 在后续接力/reset 后不会继续授予 owner 身份。此限制依赖合作代理遵守，不能拦截任意同 UID 脚本。

### 验证证据边界

`tests/succession_mock.py` 是精确 v1 API 的隔离 double，绝不加载真实 registry。helper 的 temporal tests 覆盖 commit→ready→真实 CLI main 返回码→toy action、并发一次许可、unknown/get-first、commit+publish 失败恢复、superseded/reset/occupied/needs_recovery、无 owner revision 不变、token 刷新 digest 不变、身份/contact/null/custody 和 capsule 破坏。Engine fake native 与 HTTP tests 显式注入 double；缺安装 API 在 native create 前失败。

上述 double 测试证明 **handoff 调用契约与本地顺序**。独立的 `tests/test_succession_store.py` 则通过同一个实际 helper/CLI/Engine ready gate 调用 canonical API，所有 prepare/commit/get 均显式绑定 `Store(temp/private, temp/current.md)`；初始化真实 v3 current/audit，使用 registry peer `state()` 提供的 codec-valid 合成 owner/assignment/coordination 图，缺真实依赖会失败而非 skip。运行：

```sh
python3 -m unittest discover -s codoxear/session-handoff/tests -p test_succession_store.py -v
```

joint 用例直接观察：真实 session-succession event 和新 current binding 均先于 ready 发布/CLI 0/临时 toy 文件动作；保留最新完整非 carrier 图；commit 后仅 ready 发布失败则 CLI 2、无 toy 动作，登记已生效，重试只 get 原 operation/intent，仍为一个 event。另覆盖 no-owner current 字节/mtime/revision/图不变且 event 为零（允许 event:null 的私有 no-op metadata）、新注册/occupied/source swap/reset 阻断、两个并发 CLI 仅一次许可、另一源抢先提交后的同 immutable intent 有界 CAS 重试、A→B→C/reset 后历史 receipt/Engine/check-owner 拒绝，以及临时 HOME 安装布局、任意 cwd、被污染 generic module namespace 下的实际 loader。未启动真实 Pi、浏览器、HTTP 服务或安装器，未读取用户登记；此证据不覆盖模型理解、原生 transport、线上迁移、reader 展示或部署。

首批 joint checkpoint 使用 canonical codec `da0f428`（已与 main `1d13a40` 同 blob，不重复导入）、`5bfb383`、`d1d0373`，core `c727286`，API `5c45d7e` / `3e6390ed` / `6f651dff`，handoff `0349336`。在该 API 版本，合法的同 Pi contact/recovery/graph 更新被误判为 superseded；对应 joint 测试保留为真实失败，未使用 skip/expectedFailure 或替代服务。需 registry peer 的 current-binding 修复原 SHA 后重跑，不能据其他绿例宣称全部闭环。不得使用真实 owner 做 fixture，不在此组件部署/安装/修改 registry。

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
