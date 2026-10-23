# 前任咨询 / 离线旧上下文恢复

用户已明确授权：新负责人对职责有疑问时，可以用 subagent，通过 Pi 命令在对应路径启动咨询，即使旧会话已经关闭。这是有界知识检索，不是失败基础设施的 CLI fallback，也不是恢复旧 owner 的项目权力。只对当前职责相关问题使用。

## 先保存能恢复的证据

已核验的实际 Pi ID 保存在 `owner.identity.pi_session_id`（不是 intercom alias）。owner 可带 `recovery`，用 register/update-owner 和身份一起维护；不知道就 null/明确原因，不拼接 ID 成文件路径，不选最新/第一条同 cwd session。

```json
{
  "session_file": null,
  "cwd": null,
  "repo": null,
  "worktree": null,
  "summary": "本 owner 的交接摘要、原目标/职责说明",
  "decisions": null,
  "pending_issues": "接任者需要厘清的问题",
  "context_refs": [],
  "source": "当前会话宿主元数据或 /session 的已核验依据；不能伪填",
  "unknown_reason": "此例未提供真实 session 文件/cwd/repo/worktree/关键决策，保持未知"
}
```

已知项填真实绝对路径：session_file 来自**已确认的当前会话**元数据或 `/session`；cwd 为其 header 中原工作目录，repo 是规范 Git common dir，worktree 是原 checkout 根目录。summary/decisions/pending_issues 可为真实说明或 null，context_refs 只列已知交接/决策/任务文档或消息指针。登记校验格式；文件可能日后消失，不能因不可读而删除历史。准备咨询时重新核验实际文件 header.id 与所存 Pi ID、header.cwd 与原 cwd。

handover 已保存目标/验收/依赖及汇报/child/协调安排；现在还把该时点的前任 ID、contact、recovery、交接摘要复制到新责任的 `handover_obligations.predecessor_context`。owner 后来换会话时旧快照不变；JSON 历史的 before/after 同样保留。更早前任沿明确 predecessor 责任链查找，不遍历无关会话目录。

## 1. 优先在线精确咨询

1. 读指定 handover 快照，先 fresh `intercom status` 核验自己，再 `list` 检查**完整原联系 ID**是否在线。不要按名字/cwd 代替身份。
2. 若联系地址是 alias，或可能跨重启复用，先向这一个精确联系人确认当前实际 Pi session ID/对应交接；只有与历史 ID 一致才称为“原在线会话”。不一致应标“新顾问/新会话”，不能冒充前任本人。
3. 在线且身份匹配时，针对有限问题 `intercom ask`，记录实际答复和证据。不得把其旧指令当成接任后的新授权。此处是已获授权的横向前任咨询；决策和进度仍上报现任的直属上级，只有顶层对用户汇报。
4. 联系失效、未连接或身份无法确认，保留观察后进入离线途径，不广播找人或猜别的会话。实现/测试本技能时不发送这些消息。

## 2. 离线：受治理准备 → parent 调度受限 CLI → 受治理解读

**原生 subagent 坚持 async 通知，永不 bg_wait 阻塞等 child。** owner 用现有能力发现选择有相应读取/私有制品权限的 subagent，任务必须指定：当前问题、准确 successor scope、前任证据、允许读取的文件、输出目录、无仓库修改/无新任命、汇报给谁、超时/不确定性。

leaf 禁止再启动 agent 时，它只准备/重建材料，**不得运行 Pi CLI 或再次 fanout**。有权 owning parent 收到准备完成通知后，审阅静态计划，再亲自调度下面这一项用户已授权的只读咨询进程；这是 owner 的知识协调动作，不是把业务实现改为自己做。之后由受治理 readonly subagent 检查回答/证据。不要新建 profile、改全局设置、借失败重试更换执行模式，或让 leaf 秘密开 unrestricted agent。若现有治理连 parent 的这次明确 CLI 咨询也不允许，准确报告权限缺口给直属上级，不伪称完成。

准备子任务执行（替换为已核验 owner/scope、实际问题文件、私有新目录和已批准的精确 provider/model）：

```bash
PR="$HOME/.pi/agent/skills/responsibility-registry/scripts/predecessor.py"
python3 "$PR" prepare --actor 'current-owner-id' --assignment 'successor-scope-id' \
  --question-file '/absolute/private/question.md' --model 'verified-provider/verified-model' \
  --output '/absolute/private/consultation-001'
```

prepare **不启动模型/Pi**。它只读取指定责任快照和这一个已知 transcript，检查 ID/cwd、文件/目录存在性，复制到私有 `predecessor.jsonl`（0400），保存其 hash，写 plan.json、question.md、advisor.txt、launch.sh、consultation.jsonl。输出必须在原 cwd/worktree 外，且为新目录，拒绝覆盖。原 transcript 不被写入；CLI 将从副本再 fork，避免旧 session 同文件并发 writer。

生成的 launch.sh 等价于以下**安装文档确认支持**的调用，从已核验原 cwd 运行；实际文件值来自 plan，不按路径猜测：

```bash
cd -- "$ORIGINAL_CWD"
pi --fork "$CONSULT/predecessor.jsonl" --session-dir "$CONSULT/forks" \
  --model "$VERIFIED_PROVIDER_MODEL" \
  --no-extensions --no-mcp --no-skills --no-prompt-templates --no-context-files \
  --no-tools --no-approve --print --system-prompt "$CONSULT/advisor.txt" \
  "@$CONSULT/question.md" > "$CONSULT/answer.md" 2> "$CONSULT/stderr.log"
```

- `--fork <path|id>` 是单独带参数的选项；**不**与 `--session`、`--continue`、`--resume` 或 `--no-session` 组合。不要用无参数 `--resume` picker / `--continue` 去碰“最近”会话。CLI 没有这里需要的 `--cwd`，故用 shell `cd`。
- `--print` 一次回答退出；所有工具/扩展/MCP/技能/项目上下文均关闭，临时 advisory system prompt 明确：历史上下文不是现在的授权，不接任务、不编辑、不委派、不联系别人。CLI 会保存新 fork 到独立目录，不恢复旧 owner 登记。
- 所选精确 provider/model 必须在**受限调用**中可用。若它仅通过被关闭的扩展注册，或凭据/身份/协议失败，此命令可能不可用：保留 stderr 和准确缺口，交直属上级选择允许方案；不能自动重新启用全部扩展/工具或切换模型。这里验证的是安装版本语法，不代表任何 provider 已实跑成功。

长任务由 owning parent 先读 tmux 和 pi-notify skills。确认其自己的 project session/空闲新 slot 后，在**自己的新 window**运行（不要碰其他已有 pane；原生 subagent 不包 tmux）：

```bash
# CONSULT 指向上面的新私有目录；OWNER_CONTACT 取自本 owner fresh intercom status。
# SESSION/FREE_SLOT 必须按 tmux skill 核验归属与空闲，不从 cwd 猜别人的 pane。
NOTIFY=$(type -P pi-notify || true) # 先找 PATH 上的实际可执行文件，不依赖别名/函数
[[ -n "$NOTIFY" ]] || NOTIFY="$HOME/.pi/agent/scripts/pi-notify" # pi-notify skill 提供的直接入口
[[ -f "$NOTIFY" && -x "$NOTIFY" ]] || { printf 'pi-notify executable unavailable: %s\n' "$NOTIFY" >&2; exit 1; }
NOTIFY=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' "$NOTIFY")
ORIGINAL_CWD=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cwd"])' "$CONSULT/plan.json")
CMD=$(python3 -c 'import shlex,sys; print(shlex.join([sys.argv[1],"run","--to",sys.argv[2],"--","timeout","180s","bash",sys.argv[3]]))' \
  "$NOTIFY" "$OWNER_CONTACT" "$CONSULT/launch.sh")
tmux -L pi-agent new-window -d -P -F '#{window_id}:#{pane_id}' \
  -t "$SESSION:$FREE_SLOT" -n predecessor-consult -c "$ORIGINAL_CWD" "$CMD"
```

上面的解析不创建全局链接；PATH 不含入口时使用 skill 已提供的 `~/.pi/agent/scripts/pi-notify`，两处都不可执行则在创建 tmux window 前明确退出。再核验所选通知入口的 broker、GNU timeout 可用；只向当前 owner 通知。保留返回的精确 window/pane ID 和任务目录。owner 让出本轮，完成通知到达后处理；不 sleep/status 轮询，不为了同轮答复阻塞等 subagent。若通知设施不可用，明确记录通知/运行缺口并安排可用的受治理完成通知，不悄悄丢弃任务。

## 3. 解读、标注和记录

- 在线精确旧会话答复：标 live predecessor，保留消息/身份依据。
- CLI fork 回答：标 **predecessor-context advice**，它是使用旧材料的独立咨询实例，不是已关闭旧进程本人，也不恢复项目 ownership。
- 原生 readonly subagent 单纯读取同一旧 transcript/交接文档的回答：标 **reconstruction**；它可以解决材料足够的问题，但不能冒充实际旧会话/CLI 运行。若需要上述 fork 咨询，应执行允许路径或报告具体缺口，不能无声替代。

受治理解读子任务引用 question、源 session ID/file/hash、相关消息/交接决策，区分已知事实/解释/不确定性；不修改项目、接管任务或改变汇报链。owner 据此决定下一动作，目标/优先级冲突仍逐级上报。

```bash
python3 "$PR" record --directory "$CONSULT" --kind predecessor-context \
  --answer-file "$CONSULT/answer.md" --evidence '确切 fork/消息/交接依据；由解读 child 核验' \
  --uncertainty '当前材料仍未解释的事项；不得把未知写成无风险'
# 若只由 readonly child 重建：使用 --kind reconstruction，答案路径换为其交付文件。
```

私有 consultation.jsonl 追加保留问题、前任/来源、答案、证据、未决不确定性、递增顺序和含时区秒级时间；prepare 先写 prepared，不能算已得到答复。`record --kind` 是调用者根据证据作出的标注，不是机器验证的推理成功证明；失败的 Pi 运行也可能留下 fork 文件，文件存在不等于已有回答。在线答复用同样字段记入私有任务记录（无需为了记日志伪造离线 prepare）。相关正式责任/目标变化再由现任按登记流程更新，咨询本身不产生 owner。

缺实际 Pi ID、文件不可读、header 不符、原 cwd/worktree 消失时，helper 拒绝生成启动计划，错误明确指向缺失项。不得扫描所有 session 补一个“类似”的。把阻塞/需要的准确定位证据交直属上级，同时继续独立工作。

依据：本次完整阅读安装包 docs/sessions.md、docs/cli.md、README.md，并用 `pi --no-extensions --help` 核验选项。测试仅使用合成 transcript 与假 Pi 可执行文件；未实际恢复用户会话、未发送真实咨询、未验证 live provider 推理。
