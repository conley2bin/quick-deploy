# Pi 会话接力

此扩展由共享 `web-host` 显式加载，与 `owner-groups` 并列，不修改 Codoxear 上游、Pi 全局提示词或负责人登记。仅支持 Pi；Codex/其他后端返回明确的 422。

## 用户流程

在当前原生会话页面选择接力后，新建一个 **新的 tmux Pi 会话**，继承源会话已明确的 cwd、model_provider、model 和 reasoning_effort。恢复提示作为原生创建请求的唯一 `args` 字符串传入；不克隆旧 argv，不 resume，不改变提供商。

1. 记录源日志的真实 Pi header、完整 JSONL 截止边界及哈希。投影是 `latest_committed`，并不声称知道 Pi 尚未持久化的树游标。
2. 新会话独立阅读历史、当前文件及项目记忆，提交理解报告；在收到审阅输入前不推进任务。
3. 原会话空闲且身份仍一致时，收到一次仅审阅消息；它提交针对该报告的审阅，之后仍只担任审阅者。
4. 新会话收到一次意见，按当前证据核对/纠正。提交 `ready` 回执后正常继续；无法消除的目标分歧提交 `conflict` 并停下等待用户。不再发起第二轮互审。

`reviewed=true` 只表示符合协议的审阅产物已被接受，不是语义正确的保证。HTTP 200 是原生接受输入，不是理解完成；`ready` 必须有匹配这次 source、understanding、review 哈希的 resolution 回执。

源会话暂时不可见、迟迟不空闲或审阅超时显示 **未校对**。只有用户明确点击继续，服务才允许 `review_sha256=unreviewed` 的接续回执；不会把未经校对说成审阅通过。若超时后审阅已经到达，继续操作会先采用该审阅，不隐藏它。

## 失败与恢复

- 创建返回的 `launch_id`（pending 字符串）或 `broker_pid`（数字）用于绑定目标，随后固定 `spawn_nonce`、原生路由及真实 Pi header/日志身份。不按 alias、cwd、文件名或“最新日志”猜测。
- 新 Pi 的日志可以懒生成，列表也可能先公布尚未写完的日志路径。首条恢复提示在创建时已经传入；完整日志头出现是后续身份/产物核验门槛，不是启动输入的前置条件。
- 源在审阅前被重新绑定、目标在接续前被替换、产物格式错误/过期/被覆盖，都会阻止下一次发送。
- 原生创建或发送丢失响应显示 `creation_unknown` / `delivery_unknown`。不会自动重建或重发；这些操作仍占用源会话，避免换 request UUID 伪装重试。
- 刷新/关闭浏览器不取消服务端接力。应用关闭会取消并等待其所有任务；下次启动将未结束操作标为 `interrupted`，不重放任何变更。服务停止不会撤销已经进入 Pi 的输入，`interrupted` 不代表目标 Pi 已停止。
- 中断/未知投递之后，需要人工核对真实源/目标会话；此版本没有“重试创建/重发消息”接口。旧会话不会被删除。

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

```sh
python -m unittest discover -s codoxear/session-handoff/tests -p 'test_engine.py' -v
python -m unittest discover -s codoxear/session-handoff/tests -p 'test_feature.py' -v
```

原生 `/send` 没有 expected-header/nonce 条件参数：扩展会在发送前重新查验身份，但无法把“列表核验”和“原生接受输入”合并成原子事务。不要在这段时间手动重绑同一路由；真实原生联调需核验其路由生命周期。

测试使用临时 Pi JSONL、假原生创建/发送响应、真实 Context 产物验证和应用内 aiohttp HTTP 服务；不会启动真实 Pi/tmux、访问用户数据或切换线上服务。它们验证身份、哈希、截止边界、状态和一次性副作用机制，不能证明模型真实理解了目标。部署前仍需专用隔离 Pi 完整验证首消息→理解→旧审阅→核对回执，并用真实页面核验共享宿主与分组功能共存。Workflow 分支没有合并或上线权限。
