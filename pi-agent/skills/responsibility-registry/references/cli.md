# CLI / data contract

The source is in quick-deploy at `pi-agent/skills/responsibility-registry/`. From the repository, run `bash pi-agent/install.sh --skills-only` and select only `responsibility-registry` in the numbered menu. This reuses the existing copy mechanism without installing extensions or writing fullscreen or unrelated configuration. The installed copy is at `~/.pi/agent/skills/responsibility-registry/`. After updating the source, select it for installation again and run Pi `/reload`; do not develop in the installed copy. Tests can isolate the installation target with a temporary HOME and `PI_AGENT_SKILLS_DIR`.

All commands use the Python 3 standard library. Below, `RR` points to the installed skill's script. Do not pass `--root` except for an explicitly temporary walkthrough; all projects share the default registry.

```bash
RR="$HOME/.pi/agent/skills/responsibility-registry/scripts/registry.py"
python3 "$RR" init
python3 "$RR" query
python3 "$RR" query --project 'path:/absolute/project' --active
python3 "$RR" query --owner 'stable-owner-id'
python3 "$RR" query --task 'release'
python3 "$RR" query --repo 'repo:/canonical/repo/.git' --active
python3 "$RR" rebuild
```

`init` creates an empty template and an initialization marker only for a fresh installation with no legacy/control evidence. It leaves existing live bytes/inode untouched and does not refill a file that goes missing after initialization. Legacy JSON requires explicit migration; see [migration.md](migration.md). `rebuild` emits a non-authoritative, on-demand report containing era/revision only to stdout or an explicit `--output FILE`; it neither writes dashboard.md nor changes live data. `query` is read-only: missing/corrupt documents produce errors with bytes preserved; it reports instead of repairing. Migration archives the old dashboard.md and replaces it with a relation-free pointer tombstone. `query` returns assignments, owners, `current_owners`, semantic `history`, `hierarchy`, dependency `relations`, write `coordination`, and the current revision. Project/repo filters use exact IDs or canonical absolute paths; the task filter matches substrings of task/objective. Ended records are included by default. Read `RESPONSIBILITIES.md` in the installation root for the complete current directory. `query` also returns era, revision, and document_token. Save the era together with the owner ID; only current MD rows prove current registry authority. Different directions in the same project and repo can have different owners; this is not a project-wide exclusive lock.

The following are only domain examples supplied by the user; **they are not actual appointments and create no registry records**. For example, one project may have both an office-scenario evaluation owner and a kitchen-scenario evaluation owner. Likewise, the same Dexathlon_task-proposals repository may have an owner for web publishing management, an owner for publishing office-scenario data-collection tasks, and an owner for publishing kitchen-scenario data-collection tasks, each with clear goals and a superior.

## Input fields

- `register --actor <owner-ID|user> --expected-document TOKEN --input FILE`: FILE defaults to `-`, reading JSON from stdin. Top-level `owner, appointment, capacity, goal_source` are required; `assignment` is optional (registering only the successor owner before handover). `capacity` must be `accountable-owner`; temporary helpers cannot register. `appointment` preserves the actual appointment evidence; do not invent a task merely to occupy a slot.
- Owner input: `id, name, role, parent, identity`, with optional `recovery` (verified session file, original cwd/repo/worktree, handover summary/decisions/questions/pointers, and reasons for missing information; see [predecessor recovery](predecessor.md) for the format). `parent` is `user` or `owner:<id>`. An unregistered superior is shown as unresolved and requires clarification, not promotion to the top level. Self-cycles and indirect cycles are rejected. Only the top level reports to the user; other levels report to their direct superior. A user appointment does not remove an existing chain. Each owner has one reporting chain; peer collaboration does not change parent.
- `goal_source: {"issuer":"user or owner:<ID>","source":"actual goal/appointment evidence"}` records the issuer and source. Top-level goals come from the user; an authorized direct superior may appoint subordinates within scope, and issuer should match parent. The register actor may be the owner themself, the user, or the direct superior matching that issuer. The owner retains the initial `appointment_source`; the assignment retains the current `goal_source`. When goals/acceptance change, preserve/update the actual source in update; do not fabricate user decisions. Missing historical sources are shown as awaiting confirmation, not invented.
- Identity: `pi_session_id, intercom_id, source, unknown_reason`. Source is `current-session` (verified actual Pi ID; contact address may be missing), `intercom-status` (proves only the contact ID; Pi ID must be null), or `unavailable` (both IDs must be null). Give a reason for any missing ID. These fields do not authenticate identity; never label unverified values as verified. A known actual Pi ID may differ from the contact ID.
- Assignment: `id, project, repo, task, objective, acceptance`, with an optional `depends_on` array. Register may inherit goal_source from the top level; update must include a complete goal_source. Repo is `{path, id?}`; path is the verified **canonical Git common dir**. All worktrees use the same repo ID (default `repo:<canonical-path>`). Run the read-only `git rev-parse --path-format=absolute --git-common-dir` in that repository, then canonicalize the path; do not treat each worktree cwd as a different repo. Project and repo identities are independent; multiple projects/directions may share a repo. Project has `name, path` and optional `id`. Path resolves to a canonical absolute path; the default project ID is `path:<canonical-path>`, with no basename grouping. Reuse an existing stable logical project ID across worktrees/migrations, along with its registered canonical name/path. Put the working worktree in the goal/queue rather than changing those project facts. This local database is not shared across machines.
- Task is a stable identifier within the project; objective defines the boundary and goal, and acceptance defines the acceptance criteria. Repeated registration must reuse the ID, appointment evidence, and facts; an identical request adds no history. Different active assignment IDs cannot occupy the same project+task, even for the same owner; use `handover`. Owners must check semantic overlap between different IDs in advance; the script does not perform semantic matching.
- Each depends_on item is `{"kind":"project","project":"project-ID"}` or `{"kind":"task","project":"project-ID","task":"task-ID"}`, pointing to a prerequisite independently of owner.parent. Unregistered targets are still shown. The script does not reject task dependency cycles as reporting cycles; owners should resolve work-dependency deadlocks in their queues.

Before every mutation (including register and idempotent repeated requests), query first, verify the current responsibilities and appointment, and use `--expected-document TOKEN`. The token binds era, raw-byte SHA256, and file dev/inode/size/mtime_ns/ctime_ns. A manual edit without a Revision change, or even a same-content copy, invalidates an old token. Commands other than register also require `--expected-revision N` and `--reason`; declare `--actor`. A global revision change means another owner has made a change: reread before deciding, rather than blindly retrying in a loop and overwriting. Actor is a local collaboration authorization claim, not protection against a malicious process under the same user; file permissions are the account-level boundary.

```bash
# Query again before each write command; check the facts before setting TOKEN (never automatically retry an old token).
TOKEN=$(python3 "$RR" query | python3 -c 'import json,sys; print(json.load(sys.stdin)["document_token"])')
# owner.json is the complete owner input (with the same id; remove output timestamps/appointment_source); identity, name, role, and parent may change.
python3 "$RR" update-owner --expected-document "$TOKEN" --actor 'stable-owner-id' --expected-revision 7 \
  --reason 'Current session reverified' --input owner.json
# scope.json is the complete assignment input (including repo and goal_source); change only goal, acceptance, dependencies, and source, not project/repo/task/assignment IDs.
python3 "$RR" update --expected-document "$TOKEN" --actor 'stable-owner-id' --assignment 'scope-id' \
  --expected-revision 8 --reason 'Refine acceptance within scope' --input scope.json
python3 "$RR" close --expected-document "$TOKEN" --actor 'stable-owner-id' --assignment 'scope-id' \
  --expected-revision 9 --reason 'Goal accepted'
# The successor owner is already registered; the original record becomes handed-over and the new assignment active, preserving the original owner and history.
python3 "$RR" handover --expected-document "$TOKEN" --actor 'stable-owner-id' --assignment 'scope-id' \
  --to-owner 'successor-id' --new-assignment 'new-scope-id' \
  --expected-revision 9 --reason 'Authorized superior explicitly approved succession; outstanding acceptance goals unchanged' \
  --pending-work 'Verified no running children; outstanding materials follow the goal handover' \
  --authority 'Actual authorization record/message evidence'
# When inheriting active or historical pending coordination, also add --coordination-plan 'Who will reconfirm which boundaries/interfaces/order'.
# If the successor has a different superior, also add --reporting-change 'Explicitly approved new reporting recipient and evidence'; never silently change parent.
```

Update cannot change an assignment's owner or task identity, and ended IDs are not reused. It cannot bypass authorization for an out-of-scope transfer. Use `--actor user` only when the user actually authorized that mutation. Repeated register for the same appointment and unchanged update/update-owner add no history. Repeated close is rejected without appending events. An identical handover retry (the original expected-revision may remain, but query again for the current document_token) returns unchanged without creating false history. Different parameters cannot masquerade as the same handover; query first to verify current ownership.

## Long-term history, handover, and occurrence time

Handover also saves immutable `predecessor_context` (the predecessor's actual Pi ID/contact, recovery, original goal/acceptance/superior, and handover summary), so a later session change by that predecessor cannot overwrite the source for this handover. See [predecessor.md](predecessor.md) for exact offline consultation commands, the launch responsibilities of governed children and their parent, error handling, and consultation records.

Within the current era, the CLI does not delete owners, old appointments, or old assignments. User-deleted rows or a template reset explicitly change current facts. An offline session does not trigger automatic handover. When facts change, the current owner/authorized direct superior proactively updates the document without waiting for a user reminder. Handover requires `--pending-work`: list each active child's run ID, actual custodial owner, and result reporting/acceptance path; if there are none, verify and explicitly state that. Registration does not automatically change a running child's supervisor. A different superior requires explicit evidence in `--reporting-change`. Goal, acceptance, and dependencies are inherited from the old assignment and cannot be silently changed.

When an active write agreement or inherited coordination exists, `--coordination-plan` is required. Atomic handover sets affected old agreements to released and writes their IDs, previous/current child arrangements, reconfirmation plan, and reporting arrangements into the new assignment's `handover_obligations`. The old owner's ack does not transfer to the new owner, who must renegotiate before overlapping mutation. Original agreements/owners/assignments and history remain. Use `query --task ... --active` / `current_owners` to find the current owner; default queries in the current era and live MD audit history retain all predecessors.

One atomic live Markdown commit saves both assignment changes and appended history. Each event has a stable ID such as `event-00000001`, revision order, `at` (registration time, ISO8601 seconds with timezone), `occurred` (occurrence time and precision), snapshots of old/new owner ID/name/role, project/task, actor, authorization/reason, and before/after snapshots. A→B→C within the same second is distinguished by event order. History is stored in an audit-only JSON fence in **the same live Markdown**; explicit on-demand reports can render semantic events. History snapshots never reconstruct current rows. Names/roles are facts known when the event was registered; do not guess unknown past names.

All mutation commands support `--authority 'Known authorization evidence'`; if omitted, it remains unknown. Live events default to the current occurrence second. **When backfilling past events, never pass the current second off as the occurrence time**:

```bash
# Exact known time: preserve explicit timezone seconds; registration time is still now.
# Add these options to the actual register/update/close/handover command:
# --occurred-at '2025-05-06T07:08:09+08:00' --occurrence-precision exact
# Unknown exact past time: omit occurred-at; do not fabricate seconds.
# --occurrence-precision unknown
# Only a range is known: store the approximate description as given, explicitly without second-level certainty.
# --occurred-at 'Around May 2025; exact second unknown' --occurrence-precision approximate
```

An assignment's start_time/end_time separately store occurrence time/precision. created_at/updated_at are record creation/update times; do not conflate them. Historical occurrence times absent from old schemas are shown as unknown, not inferred or invented.

## Same-repository write agreements

Before dispatching a mutation child, use `query --repo ...` to find all existing direction owners and check who is affected. Agree on boundaries jointly rather than declaring them unilaterally. For coord-propose input, see agreement.json in the walkthrough below. Scopes map active assignment IDs to lists of writable files/modules; other fields specify shared files/interfaces, branch/worktree, sequencing, integration owner, and acceptance. The tool derives the canonical repo, participating owners, scope versions, and complete-assignment SHA256 digests from assignments. A manual scope edit invalidates old confirmations even without changing updated_at. List only affected parties; still verify that omitted directions are unaffected, rather than treating overlapping work as ready merely because someone is absent.

```bash
python3 "$RR" coord-propose --expected-document "$TOKEN" --actor 'owner-a' --expected-revision 10 \
  --reason 'Proposal formed; explicit agreement from all parties still pending' --input agreement.json
python3 "$RR" coord-ack --expected-document "$TOKEN" --actor 'owner-a' --coordination 'agreement-id' \
  --expected-revision 11 --reason 'I explicitly agree to all terms; message/record evidence…'
python3 "$RR" coord-ack --expected-document "$TOKEN" --actor 'owner-b' --coordination 'agreement-id' \
  --expected-revision 12 --reason 'I explicitly agree to boundaries, shared interfaces, isolation, and integration order; evidence…'
python3 "$RR" query --repo 'repo:/canonical/repo/.git'
python3 "$RR" coord-release --expected-document "$TOKEN" --actor 'owner-a' --coordination 'agreement-id' \
  --expected-revision 13 --reason 'Boundaries changed; release old agreement and negotiate under a new ID'
```

Sending/delivery is not confirmation; never fabricate a peer's actor/ack. Record ack evidence only for an actual participant's explicit agreement. Status moves from proposed to confirmed after all participating owners ack; released agreements are no longer usable. Changes to scope goals/acceptance/owner/status invalidate old confirmations: query returns `agreement_ready` false even if the historical status is confirmed. Release and negotiate under a new ID rather than reusing old acks. Confirmations are visible in the same live Markdown/query and have history. Manual edits to agreement terms also invalidate readiness through a terms_digest mismatch.

`agreement_ready` **covers only the listed agreement**; it is neither repository-wide scheduling permission nor an active-writer lock. Only one writer at a time may use a shared cwd. Isolated worktrees may run in parallel but cannot ignore overlapping files/interfaces or integration order. Unknown/unreachable peers or unconfirmed overlap block only related mutation; continue planning, read-only work, and demonstrably non-conflicting work. Do not cross existing boundaries, overwrite others' work, or independently stage/commit their changes. The script neither solves path-glob conflicts nor replaces real negotiation.

## Runnable temporary walkthrough

This block uses synthetic data and **writes only to a mktemp directory**. It does not appoint real sessions and must not be copied into the default registry. It covers superior-issued appointments, two direction owners in one repo, hierarchy, dependencies, lookup, acceptance refinement, explicit agreement acknowledgements, handover, closure, reporting-chain changes, and report rebuilding. Project paths are canonical demonstration paths; the projects need not exist.

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
  cat "$R/current-query.json" # Synthetic data only here; in real use, check current owners/superiors/appointments before deciding.
  TOKEN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["document_token"])' "$R/current-query.json")
}
rr init
cat > "$R/appointment.json" <<'JSON'
{
  "capacity": "accountable-owner",
  "appointment": "Walkthrough: I appoint you as the example project's release owner",
  "goal_source": {"issuer": "user", "source": "Walkthrough: user supplied the release goal"},
  "owner": {
    "id": "demo-alpha", "name": "Example Alpha", "role": "Release owner", "parent": "user",
    "identity": {"pi_session_id": null, "intercom_id": null, "source": "unavailable", "unknown_reason": "Temporary synthetic walkthrough; no real session"}
  },
  "assignment": {
    "id": "demo-release-v1", "project": {"id": "demo:alpha", "name": "Example project", "path": "/example/project-alpha"},
    "repo": {"path": "/example/project-alpha/.git"},
    "goal_source": {"issuer": "user", "source": "Walkthrough: user supplied the release goal"},
    "task": "release", "objective": "Organize the release and acceptance", "acceptance": "All acceptance goals pass and the user accepts",
    "depends_on": [{"kind": "project", "project": "demo:upstream"}]
  }
}
JSON
inspect_current
rr register --expected-document "$TOKEN" --actor demo-alpha --input "$R/appointment.json"
inspect_current
rr register --expected-document "$TOKEN" --actor demo-alpha --input "$R/appointment.json" # Still revision 1
python3 - "$R" <<'PY'
import json, pathlib, sys
r = pathlib.Path(sys.argv[1])
p = json.loads((r / 'appointment.json').read_text())
p['owner'].update(id='demo-beta', name='Example Beta', role='Build direction owner', parent='owner:demo-alpha')
p['appointment'] = 'Walkthrough: Alpha appoints Beta within authorized scope as an ongoing subordinate to own the build and prepare for succession'
p['goal_source'] = {'issuer': 'owner:demo-alpha', 'source': 'Walkthrough: Alpha explicitly issued the build goal'}
p['assignment'].update(id='demo-build', task='build', objective='Organize the build direction and deliver the release prerequisite', goal_source=p['goal_source'])
(r / 'successor.json').write_text(json.dumps(p, ensure_ascii=False))
a = json.loads((r / 'appointment.json').read_text())['assignment']
a['acceptance'] = 'Subagents deliver validation evidence, the owner reviews, and the user accepts'
(r / 'scope.json').write_text(json.dumps(a, ensure_ascii=False))
o = p['owner']
o['parent'] = 'user'
(r / 'owner.json').write_text(json.dumps(o, ensure_ascii=False))
PY
inspect_current
rr register --expected-document "$TOKEN" --actor demo-alpha --input "$R/successor.json"
rr query --owner demo-alpha
rr query --project demo:alpha --task 'release'
inspect_current
rr update --expected-document "$TOKEN" --actor demo-alpha --assignment demo-release-v1 \
  --expected-revision 2 --reason 'Walkthrough: refine acceptance' --input "$R/scope.json"
cat > "$R/agreement.json" <<'JSON'
{
  "id": "demo-agreement",
  "scopes": {"demo-release-v1": ["release/**", "shared/config.json"], "demo-build": ["build/**", "shared/config.json"]},
  "shared_interfaces": "Shared config v1; Alpha updates the shared file first, and Beta uses the confirmed version without overwriting",
  "workspace": {"mode": "isolated-worktrees", "plan": "Alpha uses the release branch/worktree; Beta uses the build branch/worktree"},
  "sequencing": "Accept Alpha's shared interface first; after Beta builds, Alpha integrates serially",
  "integration_owner": "demo-alpha", "acceptance": "Both parties confirm the interface; Alpha accepts integration and reports to the user; Beta reports to Alpha"
}
JSON
inspect_current
rr coord-propose --expected-document "$TOKEN" --actor demo-alpha --expected-revision 3 \
  --reason 'Walkthrough: sending a proposal does not mean agreement' --input "$R/agreement.json"
rr query --repo 'repo:/example/project-alpha/.git' # agreement_ready=false
inspect_current
rr coord-ack --expected-document "$TOKEN" --actor demo-alpha --coordination demo-agreement \
  --expected-revision 4 --reason 'Walkthrough: Alpha explicitly confirms all terms'
inspect_current
rr coord-ack --expected-document "$TOKEN" --actor demo-beta --coordination demo-agreement \
  --expected-revision 5 --reason 'Walkthrough: Beta explicitly confirms all terms, not a delivery receipt'
rr query --repo 'repo:/example/project-alpha/.git' # agreement_ready=true
inspect_current
rr handover --expected-document "$TOKEN" --actor demo-alpha --assignment demo-release-v1 \
  --to-owner demo-beta --new-assignment demo-release-v2 --expected-revision 6 \
  --reason 'Walkthrough: user approved handover; Alpha issues the succession goal to Beta' \
  --pending-work 'Walkthrough: verified no running children; Alpha hands existing acceptance materials to Beta' \
  --reporting-change 'Walkthrough: user explicitly states that Beta still reports to Alpha after succession' \
  --coordination-plan 'Walkthrough: release the old agreement; after succession, Beta reconfirms shared config and integration order with the build direction'
rr query --owner demo-beta --active
inspect_current
rr close --expected-document "$TOKEN" --actor demo-beta --assignment demo-release-v2 \
  --expected-revision 7 --reason 'Walkthrough: direct superior Alpha accepted the goal'
inspect_current
rr update-owner --expected-document "$TOKEN" --actor demo-beta --expected-revision 8 \
  --reason 'Walkthrough: user explicitly has Beta report directly to the user' --input "$R/owner.json"
rr rebuild --output "$R/report.md"
cat "$R/report.md"
```

## Markdown format, reset, and restore

`RESPONSIBILITIES.md` in the installation root is the sole current graph. `RESPONSIBILITIES.template.md` contains no example rows, identities, or history. The default path is not derived from the running script's source location. For temporary/custom paths, pass both `--root PRIVATE --document LIVE.md`; overriding root alone is rejected to prevent tests from touching production.

The strict document format is `responsibility-registry/v2`: fixed Format/Era/Revision, three visible tables (Owners, Assignments, Coordination), and one audit-only History JSON fence. Rows determine existence; relationship fields appear only in columns and must not be duplicated in Details JSON. Unknown fields, duplicate JSON keys, duplicate row IDs, extra structure, incorrect column counts, invalid references/cycles/lead conflicts all produce errors. The template era=template permits only Revision0 with everything empty; reads do not generate eras/events. Only the first genuine register allocates a UUID era.

- Owners columns: ID / Name / Role / Parent / Details. Details: identity, created_at, updated_at, with optional appointment_source and recovery.
- Assignments columns: ID / Owner / Status / Project ID / Task / Objective / Acceptance / Details. The project in Details has only name/path; remaining fields retain appointment, depends_on, created_at, updated_at, end_reason, successor, and existing repo/goal_source/start_time/end_time/handover_obligations/handover_request.
- Coordination columns: ID / Status / Integration owner / Details. Details retains repo, scopes, shared_interfaces, workspace, sequencing, acceptance, acknowledgements, created_at, updated_at. Scopes include assignment_digest; never assume manually edited scopes remain confirmed.

Tables split columns on literal `|`. Encode data pipes as `&#124;`, **not** `\|`. Entities are decoded once; `<br>` denotes a newline. Write literal `<br>` as `&lt;br&gt;` and literal `&#124;` as `&amp;#124;`. Details is strict JSON before cell encoding. The encoder preserves Chinese and escapes &/angle brackets/quotes/pipes/backticks/Markdown formatting; CR=`&#13;`, tab=`&#9;`, leading/trailing spaces=`&#32;`. Raw HTML/unknown entities are rejected, with no CommonMark/backslash reinterpretation. Manual edits to visible columns take effect directly without automatically fabricating actors or events.

This is only a format example, not an appointment; **never put it in the template**:

```markdown
| example-owner | Example owner | Direction owner | user | {&quot;identity&quot;:{&quot;pi_session_id&quot;:null,&quot;intercom_id&quot;:null,&quot;source&quot;:&quot;unavailable&quot;,&quot;unknown_reason&quot;:&quot;Example; not verified&quot;},&quot;created_at&quot;:&quot;2026-10-24T08:00:00+00:00&quot;,&quot;updated_at&quot;:&quot;2026-10-24T08:00:00+00:00&quot;} |
```

When the user chooses to make appointments afresh, first pause registry CLI writers, then run only:

```bash
LIVE="$HOME/.pi/agent/skills/responsibility-registry"
rm -- "$LIVE/RESPONSIBILITIES.md"
cp -- "$LIVE/RESPONSIBILITIES.template.md" "$LIVE/RESPONSIBILITIES.md"
# query -> zero current graph; after reverifying actual appointments, query/token/register. No JSON deletion or service restart.
```

During the delete/copy gap, or for a corrupt document, only diagnostics are returned and existing bytes are preserved. The web shows the native list without a last-successful-graph fallback. Manual rm really deletes that file's history for that era, including events added after migration; unobserved manual edits are not automatically archived. Prefer agent clear for a complete backup, or make a private dated copy before rm. Backups never grant authority. Reset does not stop agents/children; never reconstruct your responsibilities from old memory. query/rebuild/init/reinstall never import old JSON. Under the same `registry.lock`, the CLI validates the token and current authority, serializes/reads back, then rechecks original bytes/file identity after fsync of the temporary file and before atomic replace. It does not blindly retry. A non-cooperating editor can write between the final check and rename; standard POSIX has no compare-and-swap rename. Pausing registry writes during reset is recommended. Do not delete/replace the lock inode. Only same-machine local POSIX is supported; no NFS/cross-machine transactions are promised.

See [migration.md](migration.md) for installation/migration/crash recovery and code rollback without resurrecting old data. History can grow over time; the web limit is 8 MiB, beyond which grouping is explicitly disabled, not silently truncated.

## User-authorized clear / restore with backups

Natural-language requests to "Cancel all owner relationships" or "Rebuild owner relationships" both use clear; no subsequent appointment is required. If the user explicitly includes a new appointment, query again after clear and register only that appointment. Global operations accept only actual user authorization with `--actor user --reason --authority`. These fields are self-attestation, not an authentication boundary; peer/role permissions cannot clear the global registry.

```bash
python3 "$RR" query > /absolute/private/current-query.json
cat /absolute/private/current-query.json # Check the current graph, user request, and superior; no automatic retry.
TOKEN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["document_token"])' /absolute/private/current-query.json)
python3 "$RR" clear --expected-document "$TOKEN" --actor user \
  --reason 'User explicitly canceled all registered relationships' --authority 'Actual user message/time evidence'
python3 "$RR" backup-list
# Before restore, independently query again, verify the target and current graph, and extract a fresh TOKEN:
python3 "$RR" restore --target previous --expected-document "$TOKEN" --actor user \
  --reason 'User explicitly requested the previous relationships' --authority 'Actual user message evidence'
# Alternatively, --target accepts an exact ID/filename/ISO minute or second with explicit timezone (T or space); ambiguity lists candidates and requires user clarification.
# After failure, query the current graph first, extract a fresh TOKEN, and obtain user authorization:
python3 "$RR" recover-switch --expected-document "$TOKEN" --actor user \
  --reason 'Reconcile an unfinished relationship switch' --authority 'User authorized index recovery'
```

Exact-byte MD files in `backups/` are immutable pre-switch snapshots. `BACKUPS.md` shows second-level timestamps with timezone, root names/parent→child roles, counts, reason/authority, snapshot/hash/status. Automatic previous/time selection uses completed records. The user may explicitly select a superseded-manual snapshot by full ID/filename; restoration requires the same hash/strict-v2 validation. If a superseded-manual record has a sequence newer than the latest completed record, previous must refuse and list both in UTC/local time rather than silently choosing an older state. query/projector never read the index/backups as current state; JSON/dashboard are never candidates. previous is resolved **before** creating a new backup: A→clear(s1=A)→restore previous(A,s2=empty)→restore previous(empty,s3=A). Once the target is selected, back up CURRENT first, then restore the target's original revision/history with a fresh UUID era. Do not reuse the snapshot era, add false history, or restart tasks/processes. Older confirmations missing terms_digest remain not-ready and are reported in readiness_changes, never silently re-signed.

Snapshot directories have mode 0700; snapshots/index have mode 0600. Time+sequence+UUID and O_EXCL/fsync prevent same-second collisions. If snapshot or pre-switch index writing fails, live bytes/inode remain unchanged; pending evidence requires explicit recovery. A post-switch index failure returns nonzero `switched; index incomplete`; do not assume no switch occurred. `recover-switch` compares journal before/after hashes with the actual current MD: matching after yields completed; matching before with no publication evidence yields aborted. If the user has changed it to another **valid** MD (including manual rm+cp), recovery records only the non-automatic terminal status `superseded-manual`, saving the observed hash/original journal phase and preserving the original snapshot. It removes the journal after durably writing the index, without changing live bytes/inode. Even if the user changes published data back to the before bytes, recovery must not claim it was never published. Known unpublished failures make a best-effort attempt to finalize the aborted index/cleanup automatically. Any index cleanup failure still explicitly reports recovery required rather than silently unblocking. superseded-manual is not automatically selected by previous, and newer superseded evidence prevents previous from guessing. pending/aborted/incomplete records cannot be restored; an unfinished journal blocks subsequent mutation.

Time selection uses instant intervals, not UTC string prefixes: `2026-10-24T16:13+08:00` selects the minute UTC08:13; `2026-10-24 16:13:05+08:00` selects the second UTC08:13:05. Intervals include the start and exclude the next minute/second. `2026-10-24T16:13Z` is a different instant interval. A timezone-free `2026-10-24T16:13`, a date alone, or unsupported precision is rejected without creating a CURRENT backup or switching. Candidates show UTC and local offset times; ask the user to clarify. Multiple matches also list candidates rather than guessing. Full IDs/filenames and previous are unchanged; after two clears, use a specific ID from backup-list to find older state. Visible BACKUPS.md entries also show the restored-from target and new era.

When live is missing or malformed, query/clear/restore/recover first return accurate diagnostics and preserve existing bytes. Agents must not bypass errors by copying the template themselves; let the user decide on repair or manual replacement. **Before the user chooses replacement**, save existing corrupt raw bytes to a private mode-0600 dated copy and verify it. If the file is already missing, state that there are no existing bytes to back up; do not claim a successful backup. A corrupt raw copy is evidence, not a validated restore candidate.

OS-level rm cannot be intercepted, so the manual path has no guaranteed backup. To preserve all history/unobserved manual edits in this era, use clear. If manual replacement is necessary, you can first make a private dated copy and perform it under the same CLI lock:

```bash
PRIV="$HOME/.pi/agent/.memory/local/responsibility-registry"
L="$HOME/.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.md"
T="$HOME/.pi/agent/skills/responsibility-registry/RESPONSIBILITIES.template.md"
flock "$PRIV/registry.lock" sh -c 'rm -- "$1" && cp -- "$2" "$1"' sh "$L" "$T"
```

This is only an optional cooperative serialization method. The user's original `rm`+`cp` remains valid without JSON deletion, hidden reset steps, or service restarts. After any reset/restore, reread era and current rows; do not register missing rows again on the basis of old appointments/memory.

## Maintenance validation

Run from the quick-deploy source root. Cross-consumer tests also use the external projector in the repository; the installed copy is not a complete test workspace. All state writes go to temporary isolated directories:

```bash
python3 -m unittest discover -s pi-agent/skills/responsibility-registry/tests -v
```

Pi documentation basis: installed-package `docs/skills.md` (discovery, model loading, /reload), `docs/sessions.md` and `docs/session-format.md` (/session and actual IDs); the pi-intercom README's status/list and current source implementation (contact IDs can be independently configured); pi-subagents `docs/tool-reference.md` (capability discovery, async, status, notification/waiting). At runtime, follow the actual installed version and available tools; do not copy installation paths or modify packages.
