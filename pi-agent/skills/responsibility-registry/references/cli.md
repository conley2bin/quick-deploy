# CLI / 数据契约

源码在 quick-deploy 的 `pi-agent/skills/responsibility-registry/`。从仓库运行 `bash pi-agent/install.sh --skills-only` 并在编号菜单只选 `responsibility-registry`；复用现有复制机制，不安装扩展、不写 fullscreen 或无关配置。安装副本在 `~/.pi/agent/skills/responsibility-registry/`，更新源码后重新选择安装并在 Pi `/reload`，不要在安装副本开发。测试可用临时 HOME 和 `PI_AGENT_SKILLS_DIR` 隔离安装目标。

所有命令用 Python 3 标准库。以下 `RR` 指向已安装技能脚本；除明确临时演练外，不传 `--root`，所有项目共享默认登记簿。

```bash
RR="$HOME/.pi/agent/skills/responsibility-registry/scripts/registry.py"
python3 "$RR" init
python3 "$RR" query
python3 "$RR" query --project 'path:/absolute/project' --active
python3 "$RR" query --owner 'stable-owner-id'
python3 "$RR" query --task '发布'
python3 "$RR" query --repo 'repo:/canonical/repo/.git' --active
python3 "$RR" rebuild
```

`init` 只在无任何 legacy/control 证据的新安装创建空模板并记初始化标记；现有 live 文件字节/inode不动，曾初始化后的缺失不自动补齐。旧 JSON 必须明确迁移，见 [migration.md](migration.md)。`rebuild` 只向 stdout 或显式 `--output FILE` 输出含 era/revision 的非权威即时报告，不写 dashboard.md，不改 live。query 只读，缺失/损坏报错且保留字节，不修复报告。旧 dashboard.md 已由迁移封存并替换为无关系的指路 tombstone。`query` 返回责任、owner、当前归属 current_owners、语义 history、上下级 hierarchy、依赖 relations、写入协调 coordination、当前 revision；项目/repo 过滤为精确 ID 或规范绝对路径，任务过滤为 task/objective 子串；缺省包括已结束记录。完整当前目录读安装根的 `RESPONSIBILITIES.md`；query 同时返回 era、revision、document_token；把 era 与 owner ID 一起保存，只有当前 MD 行可证明当前登记授权。同项目同 repo 的不同方向可以有不同 owner，不是项目级排他锁。

以下仅是用户给出的领域示例，**不构成真实任命，不创建登记记录**：例如同一项目下，可以同时有office场景评测负责人,kitchen场景评测负责人等,又或者同一个Dexathlon_task-proposals仓库,可以同时有发布web管理负责人,也可以同时有office场景采集任务发布负责人,也可以同时有kitchen场景采集任务发布负责人,各自有明确的目标和上级。

## 输入字段

- `register --actor <owner-ID|user> --expected-document TOKEN --input FILE`，FILE 缺省 `-` 从 stdin 读 JSON。顶层 `owner, appointment, capacity, goal_source` 必需，`assignment` 可选（交接前仅登记接任 owner）。`capacity` 必须是 `accountable-owner`，临时 helper 拒绝登记。appointment 保存真实任命依据，不能只因需要占位编造任务。
- owner 输入：`id, name, role, parent, identity`，可带 `recovery`（已核验 session 文件、原 cwd/repo/worktree、交接摘要/决策/问题/指针与缺失原因，格式见 [前任恢复](predecessor.md)）。parent 是 `user` 或 `owner:<id>`；未登记上级显示待解析并澄清，不提升为顶层；自环/间接环拒绝。仅顶层向用户报告，其他层向直属上级；用户任命也不解除已有链路。一个 owner 一条汇报链，peer 协作不改 parent。
- `goal_source: {"issuer":"user 或 owner:<ID>","source":"真实目标/任命依据"}` 记录签发者和来源。顶层由用户下达；授权直属上级可在范围内任命下级，issuer 应匹配 parent；register 的 actor 可为本人、用户或与该 issuer 相符的直属上级。owner 保存初次 `appointment_source`；责任保存当前 `goal_source`。目标/验收变更时在 update 中保留/更新真实来源，不伪造用户决策。历史缺失来源只显示待确认，不补造。
- identity：`pi_session_id, intercom_id, source, unknown_reason`。source 是 `current-session`（已验证真实 Pi ID；联系地址可缺失）、`intercom-status`（只能证明联系 ID，Pi ID 必须 null）、`unavailable`（两 ID 必须 null）。任一 ID 缺失须写原因。字段不执行身份认证；不得把未核验值标为 verified。已知真实 Pi ID 与联系 ID 可以不同。
- assignment：`id, project, repo, task, objective, acceptance`，可选 `depends_on` 数组；register 可从顶层继承 goal_source，update 必须带完整 goal_source。repo 是 `{path, id?}`，path 为已核验的**规范 Git common dir**，所有 worktree 使用同一个 repo ID（缺省 `repo:<规范路径>`）。在对应仓库只读执行 `git rev-parse --path-format=absolute --git-common-dir` 取得路径再规范化，不能用每个 worktree cwd 当成不同 repo。项目和 repo 身份独立，多个项目/方向可共享 repo。project 有 `name, path`，可选 `id`；path 解析为规范绝对路径，缺省项目 ID 为 `path:<规范路径>`，不按 basename 聚合。逻辑项目已有稳定 ID 时复用以跨 worktree/迁移关联，并复用已登记规范 name/path；工作 worktree 放目标/队列里而不是改变该项目事实。跨机器不共享此本地数据库。
- task 是项目内稳定标识；objective 是边界和目标，acceptance 是验收条件。重复登记须复用 ID、任命依据及事实；相同请求无新历史。不同 active 责任 ID 即使同 owner 也不允许占同一 project+task；交接时使用 `handover`。语义上近似但 ID 不同的重叠须 owner 事先检查，脚本不做语义匹配。
- depends_on 的每项为 `{"kind":"project","project":"项目ID"}` 或 `{"kind":"task","project":"项目ID","task":"任务ID"}`，指向前置，独立于 owner.parent。目标未登记也显示。任务依赖环不被脚本当作汇报环拒绝；owner 应在队列中处理工作依赖死锁。

每次变更（包括 register、幂等重复请求）必须先 query，核验当前职责和任命，使用 `--expected-document TOKEN`。token 绑定 era、原始字节 SHA256 与文件 dev/inode/size/mtime_ns/ctime_ns，手工编辑未改 Revision 或同内容重新复制也会令旧 token 过期。除 register 外还需 `--expected-revision N`、`--reason`；声明 `--actor`。全局 revision 变化说明别的 owner 已改过，重新读后再决定；不循环盲重试覆盖。actor 是本机协作的授权声明，不能抵御恶意同用户进程，文件权限是账户级边界。

```bash
# 每条写命令之前重新 query，核对事实后设置 TOKEN（不要把旧 token 自动重试）。
TOKEN=$(python3 "$RR" query | python3 -c 'import json,sys; print(json.load(sys.stdin)["document_token"])')
# owner.json 是完整 owner 输入（含同一 id；去掉输出中的 timestamps/appointment_source）；可改身份、名字、角色、上级。
python3 "$RR" update-owner --expected-document "$TOKEN" --actor 'stable-owner-id' --expected-revision 7 \
  --reason '当前会话已重新核验' --input owner.json
# scope.json 是完整 assignment 输入（含 repo、goal_source）；仅改目标、验收、依赖、来源，项目/repo/任务/责任 ID 不变。
python3 "$RR" update --expected-document "$TOKEN" --actor 'stable-owner-id' --assignment 'scope-id' \
  --expected-revision 8 --reason '范围内验收细化' --input scope.json
python3 "$RR" close --expected-document "$TOKEN" --actor 'stable-owner-id' --assignment 'scope-id' \
  --expected-revision 9 --reason '目标已获验收'
# 接任 owner 已登记；原记录 handed-over，新责任 active；保存原负责人和历史。
python3 "$RR" handover --expected-document "$TOKEN" --actor 'stable-owner-id' --assignment 'scope-id' \
  --to-owner 'successor-id' --new-assignment 'new-scope-id' \
  --expected-revision 9 --reason '有权上级明确批准接任，未完验收目标不变' \
  --pending-work '已核验无运行 child；未完材料随目标交接' \
  --authority '真实授权记录/消息依据'
# 若继承活动或历史待办协调，另加 --coordination-plan '由谁重新确认哪些边界/接口/顺序'。
# 接任者上级不同时，另加 --reporting-change '明确批准的新汇报对象与依据'，不静默改 parent。
```

更新不能改责任的 owner 或任务身份，结束 ID 不复用；权限范围之外的转移不能用 update 绕过。`--actor user` 只在用户确实授权该次变更时使用。同一任命重复 register、相同内容 update/update-owner 不增加历史。close 重复请求拒绝且不追加事件；handover 原样重复请求（原 expected-revision 可保留，但必须重新 query 取得当前 document_token）返回 unchanged，不制造假历史。不同参数不能冒充同一交接，先 query 核实当前归属。

## 长期历史、交接与发生时间

交接还保存 immutable `predecessor_context`（前任实际 Pi ID/contact、recovery、原目标/验收/上级和交接摘要），使旧 owner 后来换会话也不覆盖该次来源。确切离线咨询命令、受治理 child 与 parent 的启动分工、错误处理和咨询记录见 [predecessor.md](predecessor.md)。

当前 era 内 CLI 不删除 owner、旧任命、旧责任；用户手工删行或模板重置明确改变当前事实。会话离线不自动交接；有变化时现任 owner/授权直属上级主动更新文档，不等用户提醒。handover 必须给 `--pending-work`：逐个写活动 child 的 run ID、实际托管 owner、结果回报/验收路径；没有则核验后明确写无。运行 child 不会因登记自动更换 supervisor。若上级不同必须给 `--reporting-change` 明确依据；目标、验收、依赖由旧责任继承，不能偷偷改变。

有活动写入协议或继承协调时，`--coordination-plan` 必需。原子交接会把受影响旧协议 released，并把协议 ID、前次/当前 child 安排、再确认计划、汇报安排写进新责任 `handover_obligations`。旧 owner 的 ack 不迁给新 owner，新 owner 在重叠 mutation 前重新协商；原协议/owner/责任和历史仍留存。`query --task ... --active` / `current_owners` 查现任，当前 era 的默认查询和 live MD 审计历史保留历任。

同一 live Markdown 原子提交同时保存责任变化和追加历史。每个事件有 `event-00000001` 式稳定 ID、revision 顺序、`at`（登记时间，ISO8601 秒+时区）、`occurred`（发生时间及精度）、old/new owner 的 ID/名称/角色快照、项目/任务、操作者、授权/原因和 before/after 快照；同一秒的 A→B→C 按事件顺序区分。历史在**同一 live Markdown** 的 audit-only JSON fence 保存；显式即时报告可渲染语义事件。历史快照永不重建当前行。姓名/角色是登记该事件时已知的事实，不猜过去未知姓名。

所有变更命令支持 `--authority '已知授权证据'`，未提供就保留未知；实时事件缺省记录当前发生秒。**补录过去事件不可用当前秒冒充发生时间**：

```bash
# 时间精确且已知：保留明确时区秒；登记时间仍是现在。
# 将这些选项加到实际 register/update/close/handover 等命令：
# --occurred-at '2025-05-06T07:08:09+08:00' --occurrence-precision exact
# 不知道过去具体时间：不填 occurred-at，也不制造秒。
# --occurrence-precision unknown
# 只知道范围：原样存约略描述，明确不保证秒。
# --occurred-at '约2025年5月，具体秒未知' --occurrence-precision approximate
```

责任的 start_time/end_time 单列发生时间/精度，created_at/updated_at 是建立/更新记录的时间，不混为一谈。旧 schema 缺失的历史发生时间展示未知，不反推或补造。

## 同仓库写入协议

派发 mutation child 前先 `query --repo ...` 找全部已有方向 owner，核对谁会受影响；共同约定边界而非单方宣告。coord-propose 输入见下面演练的 agreement.json：scopes 将活动责任 ID 映射为允许写入的文件/模块列表；其他字段明确共享文件/接口、branch/worktree、顺序、集成 owner、验收。工具从责任推导规范 repo、参与 owner、范围版本及完整 assignment 的 SHA256 digest。人工编辑范围即便不改 updated_at 也会让旧确认失效。只列受影响方；未列入的方向仍需确认其不受影响，不能借缺席把重叠变更当作 ready。

```bash
python3 "$RR" coord-propose --expected-document "$TOKEN" --actor 'owner-a' --expected-revision 10 \
  --reason '形成提案，尚待各方明确同意' --input agreement.json
python3 "$RR" coord-ack --expected-document "$TOKEN" --actor 'owner-a' --coordination 'agreement-id' \
  --expected-revision 11 --reason '本人明确同意全部条款，消息/记录依据…'
python3 "$RR" coord-ack --expected-document "$TOKEN" --actor 'owner-b' --coordination 'agreement-id' \
  --expected-revision 12 --reason '本人明确同意边界、共享接口、隔离与集成顺序，依据…'
python3 "$RR" query --repo 'repo:/canonical/repo/.git'
python3 "$RR" coord-release --expected-document "$TOKEN" --actor 'owner-a' --coordination 'agreement-id' \
  --expected-revision 13 --reason '边界改变，旧协议释放，另建新 ID 重新协商'
```

发送/送达不是确认；不能伪填 peer 的 actor/ack。只有真实参与方明确确认才写 ack 证据。状态 proposed → 所有参与 owner ack 后 confirmed；released 不再可用。范围目标/验收/owner/状态改变后旧确认失效，query 的 `agreement_ready` 为 false（即便历史 status 是 confirmed）；须 release 并新建 ID 协商，不复用旧 ack。确认记录在同一 live Markdown/query 可查并有历史；人工改协议条款也会使 terms_digest 不匹配而失效。

`agreement_ready` **只覆盖所列协议**，不是全仓调度许可/活动 writer 锁。共享 cwd 一次仅一个 writer；隔离 worktree 可并行但不能忽略重叠文件/接口和集成顺序。未知/不可达 peer 或未确认重叠只阻塞相关 mutation，继续规划、只读和有依据的不冲突工作。禁止越过既有边界、覆盖或单独 stage/commit 他人变更。脚本不做路径 glob 冲突求解，也不替代真实协商。

## 可直接运行的临时演练

此块是合成数据，**只写 mktemp 目录**，不是对真实会话的任命；不应复制到默认登记簿。包含上级任命、同 repo 两方向 owner、上下级、依赖、查找、验收细化、显式协议确认、交接、结束、汇报链变更和重建。项目路径是演示用规范路径，不要求项目存在。

```bash
set -eu
RR="$HOME/.pi/agent/skills/responsibility-registry/scripts/registry.py"
R=$(mktemp -d /tmp/responsibility-walkthrough.XXXXXX)
R_ID=$(python3 -c 'import os,sys; s=os.stat(sys.argv[1]); print(f"{s.st_dev}:{s.st_ino}")' "$R")
cleanup() {
  python3 - "$R" "$R_ID" <<'PY'
from pathlib import Path
import shutil, sys
root = Path(sys.argv[1])
assert root.parent == Path('/tmp') and root.name.startswith('responsibility-walkthrough.')
assert not root.is_symlink() and root.resolve() == root
s = root.stat()
assert f'{s.st_dev}:{s.st_ino}' == sys.argv[2], 'temporary root changed; retain rather than guess cleanup target'
shutil.rmtree(root)
PY
}
trap cleanup EXIT
rr() { python3 "$RR" --root "$R" --document "$R/RESPONSIBILITIES.md" "$@"; }
inspect_current() {
  rr query > "$R/current-query.json"
  cat "$R/current-query.json" # 此块只演示合成数据；真实调用须核对现任/上级/任命再决定。
  TOKEN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["document_token"])' "$R/current-query.json")
}
rr init
cat > "$R/appointment.json" <<'JSON'
{
  "capacity": "accountable-owner",
  "appointment": "演练：任命你为示例项目发布负责人",
  "goal_source": {"issuer": "user", "source": "演练：用户给出发布目标"},
  "owner": {
    "id": "demo-alpha", "name": "示例甲", "role": "发布负责人", "parent": "user",
    "identity": {"pi_session_id": null, "intercom_id": null, "source": "unavailable", "unknown_reason": "临时合成演练，无真实会话"}
  },
  "assignment": {
    "id": "demo-release-v1", "project": {"id": "demo:alpha", "name": "示例项目", "path": "/example/project-alpha"},
    "repo": {"path": "/example/project-alpha/.git"},
    "goal_source": {"issuer": "user", "source": "演练：用户给出发布目标"},
    "task": "发布", "objective": "组织发布并验收", "acceptance": "所有验收目标通过且用户接受",
    "depends_on": [{"kind": "project", "project": "demo:upstream"}]
  }
}
JSON
inspect_current
rr register --expected-document "$TOKEN" --actor demo-alpha --input "$R/appointment.json"
inspect_current
rr register --expected-document "$TOKEN" --actor demo-alpha --input "$R/appointment.json" # 仍修订 1
python3 - "$R" <<'PY'
import json, pathlib, sys
r = pathlib.Path(sys.argv[1])
p = json.loads((r / 'appointment.json').read_text())
p['owner'].update(id='demo-beta', name='示例乙', role='构建方向负责人', parent='owner:demo-alpha')
p['appointment'] = '演练：甲在授权范围内任命乙为持续下级，负责构建并准备接任'
p['goal_source'] = {'issuer': 'owner:demo-alpha', 'source': '演练：甲明确下达构建目标'}
p['assignment'].update(id='demo-build', task='构建', objective='组织构建方向并交付发布前置', goal_source=p['goal_source'])
(r / 'successor.json').write_text(json.dumps(p, ensure_ascii=False))
a = json.loads((r / 'appointment.json').read_text())['assignment']
a['acceptance'] = '子代理交付验证证据，负责人审阅，用户接受'
(r / 'scope.json').write_text(json.dumps(a, ensure_ascii=False))
o = p['owner']
o['parent'] = 'user'
(r / 'owner.json').write_text(json.dumps(o, ensure_ascii=False))
PY
inspect_current
rr register --expected-document "$TOKEN" --actor demo-alpha --input "$R/successor.json"
rr query --owner demo-alpha
rr query --project demo:alpha --task '发布'
inspect_current
rr update --expected-document "$TOKEN" --actor demo-alpha --assignment demo-release-v1 \
  --expected-revision 2 --reason '演练：细化验收' --input "$R/scope.json"
cat > "$R/agreement.json" <<'JSON'
{
  "id": "demo-agreement",
  "scopes": {"demo-release-v1": ["release/**", "shared/config.json"], "demo-build": ["build/**", "shared/config.json"]},
  "shared_interfaces": "共享 config v1；甲先更新共享文件，乙基于确认版本，不覆盖",
  "workspace": {"mode": "isolated-worktrees", "plan": "甲 release 分支/worktree，乙 build 分支/worktree"},
  "sequencing": "甲共享接口先验收，乙构建后由甲串行集成",
  "integration_owner": "demo-alpha", "acceptance": "双方确认接口，甲验收集成并向用户报告；乙向甲报告"
}
JSON
inspect_current
rr coord-propose --expected-document "$TOKEN" --actor demo-alpha --expected-revision 3 \
  --reason '演练：提案发出，不等于同意' --input "$R/agreement.json"
rr query --repo 'repo:/example/project-alpha/.git' # agreement_ready=false
inspect_current
rr coord-ack --expected-document "$TOKEN" --actor demo-alpha --coordination demo-agreement \
  --expected-revision 4 --reason '演练：甲明确确认全部条款'
inspect_current
rr coord-ack --expected-document "$TOKEN" --actor demo-beta --coordination demo-agreement \
  --expected-revision 5 --reason '演练：乙明确确认全部条款，非送达回执'
rr query --repo 'repo:/example/project-alpha/.git' # agreement_ready=true
inspect_current
rr handover --expected-document "$TOKEN" --actor demo-alpha --assignment demo-release-v1 \
  --to-owner demo-beta --new-assignment demo-release-v2 --expected-revision 6 \
  --reason '演练：用户批准交接；甲向乙下达接任目标' \
  --pending-work '演练：已核验无运行 child，甲把现有验收材料交乙' \
  --reporting-change '演练：用户明确乙接任后仍向甲报告' \
  --coordination-plan '演练：释放旧协议，乙接任后与构建方向重新确认共享配置和集成顺序'
rr query --owner demo-beta --active
inspect_current
rr close --expected-document "$TOKEN" --actor demo-beta --assignment demo-release-v2 \
  --expected-revision 7 --reason '演练：直属上级甲已接受目标'
inspect_current
rr update-owner --expected-document "$TOKEN" --actor demo-beta --expected-revision 8 \
  --reason '演练：用户明确调整为直报' --input "$R/owner.json"
rr rebuild --output "$R/report.md"
cat "$R/report.md"
```

## Markdown 格式、重置与恢复

安装根的 `RESPONSIBILITIES.md` 是唯一当前图，`RESPONSIBILITIES.template.md` 无示例行/身份/历史。默认路径不从运行脚本的源码位置推导。临时/自定义路径同时传 `--root PRIVATE --document LIVE.md`；只覆盖 root 会拒绝，防止测试误碰生产。

文档严格格式 `responsibility-registry/v2`：固定 Format/Era/Revision、Owners、Assignments、Coordination 三张可见表与一个 audit-only History JSON fence。行决定是否存在；关系字段只在列内，Details JSON 不准重复它们。未知字段、重复 JSON key、重复行 ID、额外结构、错误列数、非法引用/环/lead 冲突均报错。模板 era=template 仅允许 Revision0 且全部为空；读操作不生成 era/事件。首次真实 register 才分配 UUID era。

- Owners 列：ID / Name / Role / Parent / Details。Details：identity、created_at、updated_at，可选 appointment_source、recovery。
- Assignments 列：ID / Owner / Status / Project ID / Task / Objective / Acceptance / Details。Details 的 project 只有 name/path，其余保留 appointment、depends_on、created_at、updated_at、end_reason、successor 及已有 repo/goal_source/start_time/end_time/handover_obligations/handover_request。
- Coordination 列：ID / Status / Integration owner / Details。Details 保留 repo、scopes、shared_interfaces、workspace、sequencing、acceptance、acknowledgements、created_at、updated_at。scopes 含 assignment_digest；不能把手工改过的范围默认为已确认。

表格以字面 `|` 分列；数据 pipe 用 `&#124;`，**不是** `\|`。实体只解码一次，`<br>` 是换行；字面 `<br>` 写 `&lt;br&gt;`，字面 `&#124;` 写 `&amp;#124;`。Details 先严格 JSON 再编码为单元格。编码器保留中文，转义 &/尖括号/引号/pipe/backtick/Markdown 装饰，CR=`&#13;`、tab=`&#9;`、首尾空格=`&#32;`。拒绝原始 HTML/未知实体，不做 CommonMark/backslash 再解释。可见列手改直接生效，不自动伪造操作者或事件。

仅为格式示例，不构成任命，**不得放进模板**：

```markdown
| example-owner | 示例负责人 | 方向负责人 | user | {&quot;identity&quot;:{&quot;pi_session_id&quot;:null,&quot;intercom_id&quot;:null,&quot;source&quot;:&quot;unavailable&quot;,&quot;unknown_reason&quot;:&quot;示例，未核验&quot;},&quot;created_at&quot;:&quot;2026-10-24T08:00:00+00:00&quot;,&quot;updated_at&quot;:&quot;2026-10-24T08:00:00+00:00&quot;} |
```

用户选择重新任命时，先让 registry CLI writers 暂停写入，然后仅运行：

```bash
LIVE="$HOME/.pi/agent/skills/responsibility-registry"
rm -- "$LIVE/RESPONSIBILITIES.md"
cp -- "$LIVE/RESPONSIBILITIES.template.md" "$LIVE/RESPONSIBILITIES.md"
# query -> 零当前图；重新核验真实任命后 query/token/register。不要删 JSON，不需服务重启。
```

删除/复制间隙及损坏文档只报诊断、保留字节；网页显示原生列表，无最后成功图兜底。手工 rm 会真正删除该文件内该 era 的历史，包括迁移之后新增事件；不会自动归档未观察到的人工编辑。完整备份优先用 agent clear，或在 rm 前自行复制到私有 dated 文件；备份不会供给授权。重置不停止 agent/child；不得从旧记忆重建自己职责。query/rebuild/init/reinstall 均不导入旧 JSON。CLI 在同一 `registry.lock` 下校验 token、当前授权、序列化/readback，并在 fsync 临时文件后、atomic replace 前重检原字节/文件身份；不盲重试。非协作 editor 可在最终重检与 rename 之间抢写，标准 POSIX 无比较交换 rename；建议重置时暂停登记写入。不要删除/更换锁 inode。只支持同机本地 POSIX，不承诺 NFS/跨机事务。

安装/迁移/崩溃恢复和不复活旧数据的代码回退流程见 [migration.md](migration.md)。历史可长期增大，网页上限 8 MiB，超限明确停用分组，不静默截断。

## 用户授权的带备份 clear / restore

自然语言“取消所有的负责人关系”“重建负责人关系”都用 clear；无需后续任命。若用户明确附带新任命，clear 后重新 query，再只登记该任命。全局操作只接受实际用户授权的 `--actor user --reason --authority`，字段是自我声明，不是身份认证边界；peer/角色权限不能清空全局。

```bash
python3 "$RR" query > /absolute/private/current-query.json
cat /absolute/private/current-query.json # 核对当前图、用户请求与上级；不是自动重试
TOKEN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["document_token"])' /absolute/private/current-query.json)
python3 "$RR" clear --expected-document "$TOKEN" --actor user \
  --reason '用户明确取消全部登记关系' --authority '实际用户消息/时间依据'
python3 "$RR" backup-list
# 恢复前再次独立 query、核验目标与当前图，重新提取 TOKEN：
python3 "$RR" restore --target previous --expected-document "$TOKEN" --actor user \
  --reason '用户明确恢复上一次关系' --authority '实际用户消息依据'
# 也可 --target 精确ID/文件名/明确时区的ISO分钟或秒（T或空格均可）；歧义会列候选，需向用户问清。
# 失败后先 query 看当前图，重新取 TOKEN，并经用户授权：
python3 "$RR" recover-switch --expected-document "$TOKEN" --actor user \
  --reason '核对未完成关系切换' --authority '用户授权恢复索引'
```

`backups/` 中的 exact-byte MD 是不可变 pre-switch 快照；`BACKUPS.md` 显示秒+时区时间、根姓名/parent→child角色、计数、理由/授权、snapshot/hash/status。自动 previous/时间选择使用 completed；superseded-manual 可由用户明确选择完整 ID/文件名，经相同 hash/严格 v2 校验后恢复。若存在序号比最近 completed 更新的 superseded-manual，previous 必须拒绝并列双方UTC/本机时间，不静默跳到更旧状态；query/projector 从不读索引/备份作为当前状态，JSON/dashboard永不候选。previous 在创建新备份**之前**解析：A→clear(s1=A)→restore previous(A,s2=empty)→restore previous(empty,s3=A)。目标选定后先备份 CURRENT，再用 fresh UUID era 恢复目标原 revision/history；不复用快照 era，不新增伪历史，不重启任务/进程。旧 terms_digest 缺失的确认保持 not-ready 并报告 readiness_changes，不暗中重签。

快照目录0700，快照/索引0600，时间+序号+UUID且 O_EXCL/fsync 保证同秒不碰撞。快照或 pre-switch 索引失败，live字节/inode不变；pending证据须显式 recover。切换后索引失败会非零报 `switched; index incomplete`；不要认为未切换。`recover-switch` 比较 journal 的 before/after hash与当前真实MD：匹配 after 则completed；匹配 before 且无已发布证据则aborted。若用户已改成另一个**有效** MD（包括手工 rm+cp），恢复只记不自动入选的终态 `superseded-manual`，保存观察到的 hash/原 journal phase，保留原快照，持久化索引后删除 journal；不改 live 的字节/inode。即使已发布后被用户改回 before 字节，也不冒称从未发布。已知未发布的失败会尽力自动完成 aborted 索引/清理；任何索引清理失败仍显式报恢复需要，不静默解锁。superseded-manual不自动入 previous，更新的 superseded 证据会阻止 previous 猜测；pending/aborted/incomplete 不能恢复，未完成 journal 会阻止后续mutation。

时间选择按瞬时区间，不是UTC字符串前缀：`2026-10-24T16:13+08:00` 选择 UTC08:13这一分钟，`2026-10-24 16:13:05+08:00` 选择 UTC08:13:05这一秒；区间含起点、不含下一分钟/秒。`2026-10-24T16:13Z` 是另一个瞬时区间。无时区的 `2026-10-24T16:13`、仅日期或不支持的精度会拒绝，不创建 CURRENT备份或切换；候选列UTC与本机带偏移时间，请向用户问清。多个匹配同样列候选，不猜。完整ID/文件名和previous不变，若两次clear后要找更旧状态应使用backup-list中的具体ID。BACKUPS.md可见条目同时显示 restored-from目标与新era。

live 缺失或格式损坏时，query/clear/restore/recover 先报准确诊断，保留现存字节。agent不得自行用模板cp绕过错误；应让用户决定修复或手工替换。在用户选择替换**之前**，把现存损坏原始字节保存到私有0600 dated copy并核对；已缺失则说明没有现存字节可备份，不伪称备份成功。损坏原始副本只是证据，不能当作已校验的restore候选。

OS级 rm 无法拦截，故手工路径没有 guaranteed backup。需要保留本era全部历史/未观察人工编辑时，用 clear；如果必须手工，可先做私有 dated copy，并在同一 CLI lock 内执行：

```bash
PRIV="$HOME/.pi/agent/.memory/local/responsibility-registry"
L="$HOME/.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md"
T="$HOME/.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.template.md"
flock "$PRIV/registry.lock" sh -c 'rm -- "$1" && cp -- "$2" "$1"' sh "$L" "$T"
```

这只是可选协作串行方式；用户原始 `rm`+`cp` 仍有效，不需JSON删除/隐藏reset步骤/服务重启。任何reset/restore后重新读era和当前行；缺失行不能凭旧任命/旧记忆重新登记。

## 维护验证

在 quick-deploy 源码根运行；跨消费者测试同时使用仓库内的外部 projector，不能把安装副本当作完整测试工作区。所有状态写入临时隔离目录：

```bash
python3 -m unittest discover -s pi-agent/skills/responsibility-registry/tests -v
```

Pi 文档依据：安装包 `docs/skills.md`（发现、模型加载、/reload）、`docs/sessions.md` 和 `docs/session-format.md`（/session 与真实 ID）；pi-intercom README 的 status/list 与当前源实现（联系 ID 可独立配置）；pi-subagents `docs/tool-reference.md`（能力发现、async、status、通知/等待）。运行时以实际安装版本和可用工具为准，不复制安装路径或修改包。
