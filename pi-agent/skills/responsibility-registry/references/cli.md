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

`init` 只创建空状态或验证已有状态，不清空。输出 dashboard 的实际路径。`query` 返回责任、owner、当前归属 current_owners、语义 history、上下级 hierarchy、依赖 relations、写入协调 coordination、当前 revision；项目/repo 过滤为精确 ID 或规范绝对路径，任务过滤为 task/objective 子串；缺省包括已结束记录。完整目录读 `dashboard.md`。同项目同 repo 的不同方向可以有不同 owner，不是项目级排他锁。

以下仅是用户给出的领域示例，**不构成真实任命，不创建登记记录**：例如同一项目下，可以同时有office场景评测负责人,kitchen场景评测负责人等,又或者同一个Dexathlon_task-proposals仓库,可以同时有发布web管理负责人,也可以同时有office场景采集任务发布负责人,也可以同时有kitchen场景采集任务发布负责人,各自有明确的目标和上级。

## 输入字段

- `register --actor <owner-ID|user> --input FILE`，FILE 缺省 `-` 从 stdin 读 JSON。顶层 `owner, appointment, capacity, goal_source` 必需，`assignment` 可选（交接前仅登记接任 owner）。`capacity` 必须是 `accountable-owner`，临时 helper 拒绝登记。appointment 保存真实任命依据，不能只因需要占位编造任务。
- owner 输入：`id, name, role, parent, identity`。parent 是 `user` 或 `owner:<id>`；未登记上级显示待解析并澄清，不提升为顶层；自环/间接环拒绝。仅顶层向用户报告，其他层向直属上级；用户任命也不解除已有链路。一个 owner 一条汇报链，peer 协作不改 parent。
- `goal_source: {"issuer":"user 或 owner:<ID>","source":"真实目标/任命依据"}` 记录签发者和来源。顶层由用户下达；授权直属上级可在范围内任命下级，issuer 应匹配 parent；register 的 actor 可为本人、用户或与该 issuer 相符的直属上级。owner 保存初次 `appointment_source`；责任保存当前 `goal_source`。目标/验收变更时在 update 中保留/更新真实来源，不伪造用户决策。历史缺失来源只显示待确认，不补造。
- identity：`pi_session_id, intercom_id, source, unknown_reason`。source 是 `current-session`（已验证真实 Pi ID；联系地址可缺失）、`intercom-status`（只能证明联系 ID，Pi ID 必须 null）、`unavailable`（两 ID 必须 null）。任一 ID 缺失须写原因。字段不执行身份认证；不得把未核验值标为 verified。已知真实 Pi ID 与联系 ID 可以不同。
- assignment：`id, project, repo, task, objective, acceptance`，可选 `depends_on` 数组；register 可从顶层继承 goal_source，update 必须带完整 goal_source。repo 是 `{path, id?}`，path 为已核验的**规范 Git common dir**，所有 worktree 使用同一个 repo ID（缺省 `repo:<规范路径>`）。在对应仓库只读执行 `git rev-parse --path-format=absolute --git-common-dir` 取得路径再规范化，不能用每个 worktree cwd 当成不同 repo。项目和 repo 身份独立，多个项目/方向可共享 repo。project 有 `name, path`，可选 `id`；path 解析为规范绝对路径，缺省项目 ID 为 `path:<规范路径>`，不按 basename 聚合。逻辑项目已有稳定 ID 时复用以跨 worktree/迁移关联，并复用已登记规范 name/path；工作 worktree 放目标/队列里而不是改变该项目事实。跨机器不共享此本地数据库。
- task 是项目内稳定标识；objective 是边界和目标，acceptance 是验收条件。重复登记须复用 ID、任命依据及事实；相同请求无新历史。不同 active 责任 ID 即使同 owner 也不允许占同一 project+task；交接时使用 `handover`。语义上近似但 ID 不同的重叠须 owner 事先检查，脚本不做语义匹配。
- depends_on 的每项为 `{"kind":"project","project":"项目ID"}` 或 `{"kind":"task","project":"项目ID","task":"任务ID"}`，指向前置，独立于 owner.parent。目标未登记也显示。任务依赖环不被脚本当作汇报环拒绝；owner 应在队列中处理工作依赖死锁。

更新必须先 query，使用拿到的全局 `--expected-revision N` 和 `--reason`，并声明 `--actor`。全局 revision 变化说明别的 owner 已改过，重新读后再决定；不循环盲重试覆盖。actor 是本机协作的授权声明，不能抵御恶意同用户进程，文件权限是账户级边界。

```bash
# owner.json 是完整 owner 输入（含同一 id；去掉输出中的 timestamps/appointment_source）；可改身份、名字、角色、上级。
python3 "$RR" update-owner --actor 'stable-owner-id' --expected-revision 7 \
  --reason '当前会话已重新核验' --input owner.json
# scope.json 是完整 assignment 输入（含 repo、goal_source）；仅改目标、验收、依赖、来源，项目/repo/任务/责任 ID 不变。
python3 "$RR" update --actor 'stable-owner-id' --assignment 'scope-id' \
  --expected-revision 8 --reason '范围内验收细化' --input scope.json
python3 "$RR" close --actor 'stable-owner-id' --assignment 'scope-id' \
  --expected-revision 9 --reason '目标已获验收'
# 接任 owner 已登记；原记录 handed-over，新责任 active；保存原负责人和历史。
python3 "$RR" handover --actor 'stable-owner-id' --assignment 'scope-id' \
  --to-owner 'successor-id' --new-assignment 'new-scope-id' \
  --expected-revision 9 --reason '有权上级明确批准接任，未完验收目标不变' \
  --pending-work '已核验无运行 child；未完材料随目标交接' \
  --authority '真实授权记录/消息依据'
# 若继承活动或历史待办协调，另加 --coordination-plan '由谁重新确认哪些边界/接口/顺序'。
# 接任者上级不同时，另加 --reporting-change '明确批准的新汇报对象与依据'，不静默改 parent。
```

更新不能改责任的 owner 或任务身份，结束 ID 不复用；权限范围之外的转移不能用 update 绕过。`--actor user` 只在用户确实授权该次变更时使用。同一任命重复 register、相同内容 update/update-owner 不增加历史。close 重复请求拒绝且不追加事件；handover 原样重复请求（包括原 expected-revision）返回 unchanged，不制造假历史。不同参数不能冒充同一交接，先 query 核实当前归属。

## 长期历史、交接与发生时间

owner、旧任命、旧责任不删除。会话离线不自动交接；有变化时现任 owner/授权直属上级主动更新文档，不等用户提醒。handover 必须给 `--pending-work`：逐个写活动 child 的 run ID、实际托管 owner、结果回报/验收路径；没有则核验后明确写无。运行 child 不会因登记自动更换 supervisor。若上级不同必须给 `--reporting-change` 明确依据；目标、验收、依赖由旧责任继承，不能偷偷改变。

有活动写入协议或继承协调时，`--coordination-plan` 必需。原子交接会把受影响旧协议 released，并把协议 ID、前次/当前 child 安排、再确认计划、汇报安排写进新责任 `handover_obligations`。旧 owner 的 ack 不迁给新 owner，新 owner 在重叠 mutation 前重新协商；原协议/owner/责任和历史仍留存。`query --task ... --active` / `current_owners` 查现任，默认查询和 dashboard 保留历任。

同一 JSON 原子提交同时保存责任变化和追加历史。每个事件有 `event-00000001` 式稳定 ID、revision 顺序、`at`（登记时间，ISO8601 秒+时区）、`occurred`（发生时间及精度）、old/new owner 的 ID/名称/角色快照、项目/任务、操作者、授权/原因和 before/after 快照；同一秒的 A→B→C 按事件顺序区分。历史在**同一 dashboard** 渲染，不仅给 JSON 链接。姓名/角色是登记该事件时已知的事实，不猜过去未知姓名。

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

派发 mutation child 前先 `query --repo ...` 找全部已有方向 owner，核对谁会受影响；共同约定边界而非单方宣告。coord-propose 输入见下面演练的 agreement.json：scopes 将活动责任 ID 映射为允许写入的文件/模块列表；其他字段明确共享文件/接口、branch/worktree、顺序、集成 owner、验收。工具从责任推导规范 repo、参与 owner 和范围版本。只列受影响方；未列入的方向仍需确认其不受影响，不能借缺席把重叠变更当作 ready。

```bash
python3 "$RR" coord-propose --actor 'owner-a' --expected-revision 10 \
  --reason '形成提案，尚待各方明确同意' --input agreement.json
python3 "$RR" coord-ack --actor 'owner-a' --coordination 'agreement-id' \
  --expected-revision 11 --reason '本人明确同意全部条款，消息/记录依据…'
python3 "$RR" coord-ack --actor 'owner-b' --coordination 'agreement-id' \
  --expected-revision 12 --reason '本人明确同意边界、共享接口、隔离与集成顺序，依据…'
python3 "$RR" query --repo 'repo:/canonical/repo/.git'
python3 "$RR" coord-release --actor 'owner-a' --coordination 'agreement-id' \
  --expected-revision 13 --reason '边界改变，旧协议释放，另建新 ID 重新协商'
```

发送/送达不是确认；不能伪填 peer 的 actor/ack。只有真实参与方明确确认才写 ack 证据。状态 proposed → 所有参与 owner ack 后 confirmed；released 不再可用。范围目标/验收/owner/状态改变后旧确认失效，query 的 `agreement_ready` 为 false（即便历史 status 是 confirmed）；须 release 并新建 ID 协商，不复用旧 ack。确认记录在同一 dashboard 可查并有历史。

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
python3 "$RR" --root "$R" init
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
python3 "$RR" --root "$R" register --actor demo-alpha --input "$R/appointment.json"
python3 "$RR" --root "$R" register --actor demo-alpha --input "$R/appointment.json" # 仍修订 1
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
python3 "$RR" --root "$R" register --actor demo-alpha --input "$R/successor.json"
python3 "$RR" --root "$R" query --owner demo-alpha
python3 "$RR" --root "$R" query --project demo:alpha --task '发布'
python3 "$RR" --root "$R" update --actor demo-alpha --assignment demo-release-v1 \
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
python3 "$RR" --root "$R" coord-propose --actor demo-alpha --expected-revision 3 \
  --reason '演练：提案发出，不等于同意' --input "$R/agreement.json"
python3 "$RR" --root "$R" query --repo 'repo:/example/project-alpha/.git' # agreement_ready=false
python3 "$RR" --root "$R" coord-ack --actor demo-alpha --coordination demo-agreement \
  --expected-revision 4 --reason '演练：甲明确确认全部条款'
python3 "$RR" --root "$R" coord-ack --actor demo-beta --coordination demo-agreement \
  --expected-revision 5 --reason '演练：乙明确确认全部条款，非送达回执'
python3 "$RR" --root "$R" query --repo 'repo:/example/project-alpha/.git' # agreement_ready=true
python3 "$RR" --root "$R" handover --actor demo-alpha --assignment demo-release-v1 \
  --to-owner demo-beta --new-assignment demo-release-v2 --expected-revision 6 \
  --reason '演练：用户批准交接；甲向乙下达接任目标' \
  --pending-work '演练：已核验无运行 child，甲把现有验收材料交乙' \
  --reporting-change '演练：用户明确乙接任后仍向甲报告' \
  --coordination-plan '演练：释放旧协议，乙接任后与构建方向重新确认共享配置和集成顺序'
python3 "$RR" --root "$R" query --owner demo-beta --active
python3 "$RR" --root "$R" close --actor demo-beta --assignment demo-release-v2 \
  --expected-revision 7 --reason '演练：直属上级甲已接受目标'
python3 "$RR" --root "$R" update-owner --actor demo-beta --expected-revision 8 \
  --reason '演练：用户明确调整为直报' --input "$R/owner.json"
python3 "$RR" --root "$R" rebuild
cat "$R/dashboard.md"
```

## 存储与恢复

`registry.json` 版本 1 是唯一事实源，保存 owners、assignments、coordination、递增 revision 和每次变更的 before/after 历史快照。兼容旧版 v1 缺失的 coordination/repo/goal_source 字段：缺失按未记录展示，不补造；新登记/更新要求明确事实。history 新语义字段也向后兼容；旧事件的原始快照保留，未知发生时间不猜造。`registry.lock` 使用 flock，读/校验/修改/替换/投影都在同一锁内；锁文件不得删除或更换 inode，所有写入必须经过 CLI。文件 0600、新目录 0700。JSON 与 Markdown 各自临时文件 + fsync + replace，不能把两文件当一次原子事务。

- CLI 成功后 dashboard 对应同一 revision。进程在两次替换之间中断会留下旧投影；运行 `rebuild`（query 也重建）。若报“canonical revision … retained; dashboard failed”，变更可能已提交，先 query，不要盲目重做交接。
- JSON 格式/结构/关系损坏直接退出 2，保留原始字节；不回退空库，不从 Markdown 猜造事实。有 dashboard 却缺 JSON 时 init 也拒绝。先保存损坏副本并从已知备份人工恢复；没有备份必须让用户明确处理丢失的事实，不能冒充成功。
- 仅缺少 dashboard 可直接 rebuild。默认目录初次 init 是空目录，不自动注册任何人。不要手改活跃 registry、锁文件或历史。此实现不提供跨主机/NFS 锁保证、自动心跳、调度器或会话恢复。
- 文档反映最近已登记事实；session 退出后记录仍在。联系前再核验当前 roster；持久 owner 会话变化用 update-owner，保留历史依据。

## 维护验证

```bash
python3 -m unittest discover -s "$HOME/.pi/agent/skills/responsibility-registry/tests" -v
```

Pi 文档依据：安装包 `docs/skills.md`（发现、模型加载、/reload）、`docs/sessions.md` 和 `docs/session-format.md`（/session 与真实 ID）；pi-intercom README 的 status/list 与当前源实现（联系 ID 可独立配置）；pi-subagents `docs/tool-reference.md`（能力发现、async、status、通知/等待）。运行时以实际安装版本和可用工具为准，不复制安装路径或修改包。
