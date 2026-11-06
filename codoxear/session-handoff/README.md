# Pi 会话接力

共享 `web-host/host.py` 加载此扩展；不修改 Codoxear 上游或 Pi 全局提示词。仅支持 Pi。

## 使用

1. 在源会话点“会话接力”并确认。默认继承源会话列表中的模型、提供商和推理强度；也可通过原生模型目录和选择器指定新会话设置，不改源会话或全局偏好。
2. 源会话必须空闲，否则显示“源会话忙，稍后再试”，不发送消息、不创建操作。
3. 服务只向源会话发送一次请求，让它覆盖 `<cwd>/.memory/handover.md`：用不超过 15 行白话写已完成、链接/路径、未完成、下一步，写完停下。
4. 文件 mtime 必须晚于请求时间，且源会话再次空闲，才通过原生接口在同一 cwd 创建新的 tmux Pi 会话。最多等 20 分钟，超时失败，不自动重试。
5. 新会话首条提示要求读 `AGENTS.md`、`.memory/project/decisions.md`（如有）和交接文件，以实际文件、git 和运行结果核实后继续。源会话保持运行，可用 intercom 询问；服务不再给源会话发送消息，也不停止或改名它。
6. 面板显示“等待源会话写交接 → 创建新会话 → 完成（新会话链接）/ 失败（原因）”。完成表示原生创建和命名成功，不表示新会话已经完成任务。

新名称按源 `display_name` 冻结：普通名追加 `_01`，末尾 ASCII `_NN` 递增；`_99` 创建前拒绝。新名称最多 80 字符，不允许控制字符、前导 `-` 或原生会折叠的空白。不静默清洗或跳号。新会话通过原生 launch/broker/nonce 绑定后调用原生 rename；真实 tmux 标题由原生镜像异步同步。

## 状态与边界

- 同一源最多一个活动接力；不同源可并行。同一 request UUID 重复请求只返回原操作；更换目标选择会被拒绝。
- 同源 POST 和现有登录认证保护所有状态接口；Cookie 只在任务内存中，不写盘。
- `POST <prefix>/__session_handoff__/start` 接受 `{source_session_id, request_id: UUID, target?: {model_provider, model, reasoning_effort}}`；`status` 只接受 `{operation_id}`。无 continue 或 reconcile 接口。
- 可选目标三元组必须完整且匹配本次认证读取的原生目录；目录不可用时仍可继承源列表设置。设置是创建参数，不承诺 Pi 运行时实际采用值。
- 最小操作 JSON 保存在 `${XDG_STATE_HOME:-~/.local/state}/codoxear/session-handoff/<operation UUID>.json`。旧格式目录忽略，不迁移、不重放。
- 服务重启将未完成操作标记失败：“服务重启，未完成”。浏览器关闭不取消服务端任务。失败不自动重发或重建；原生响应丢失时可能已有消息或新会话，请先人工核对。
- 此流程只用新文件 mtime 和源空闲判断交接完成，不解析旧 JSONL，也不验证白话内容的质量；新会话负责核实。

## 验证与启用

```sh
codoxear/web-host/.venv/bin/python -m unittest discover -s codoxear/session-handoff/tests -v
CODOXEAR_STATIC_DIR="$PWD/codoxear/.runtime/venv/lib/python3.12/site-packages/codoxear/static" node --test codoxear/session-handoff/tests/*.test.cjs
```

测试使用临时目录和假原生 API，不启动真实 Pi 或操作现有会话。模型选择测试只读已安装的原生模块。

资源在宿主启动时冻结。要启用，在原启动终端手动停止 Web 宿主后，从仓库根运行：

```sh
codoxear/web-host/.venv/bin/python codoxear/web-host/host.py --managed-upstream
```

重启会中断 Web/私有原生 Web 子进程、浏览器连接和进行中的接力编排；未完成的新格式操作失败，不重放。Pi/broker/tmux 会话继续运行。不要为此运行会更新上游的 `run_server.sh`。
