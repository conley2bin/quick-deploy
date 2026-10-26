# Predecessor consultation / offline recovery of old context

The user has explicitly authorized a new owner with responsibility-related questions to use a subagent and launch a consultation through Pi commands at the corresponding path, even when the old session is closed. This is bounded knowledge retrieval, not a CLI fallback for failed infrastructure or restoration of the old owner's project authority. Use it only for questions relevant to current responsibilities.

## Preserve recoverable evidence first

Store the verified actual Pi ID in `owner.identity.pi_session_id` (not an intercom alias). An owner may include `recovery`, maintained together with identity through register/update-owner. Use null/an explicit reason for unknowns. Do not construct file paths from IDs or pick the newest/first session with the same cwd.

```json
{
  "session_file": null,
  "cwd": null,
  "repo": null,
  "worktree": null,
  "summary": "This owner's handover summary and original goal/responsibility description",
  "decisions": null,
  "pending_issues": "Questions the successor needs to clarify",
  "context_refs": [],
  "source": "Verified current-session host metadata or /session evidence; never fabricate it",
  "unknown_reason": "This example provides no actual session file/cwd/repo/worktree/key decisions; they remain unknown"
}
```

Fill known items with actual absolute paths. session_file comes from metadata for **the confirmed current session** or `/session`; cwd is the original working directory in its header, repo is the canonical Git common dir, and worktree is the original checkout root. summary/decisions/pending_issues may be actual descriptions or null. context_refs lists only known handover/decision/task documents or message pointers. Registration validates the format; files may later disappear, and unreadability is not a reason to delete history. When preparing consultation, reverify the actual file's header.id against the stored Pi ID and header.cwd against the original cwd.

Handover already saves goal/acceptance/dependencies and reporting/child/coordination arrangements. It now also copies the predecessor ID, contact, recovery, and handover summary at that moment into the new assignment's `handover_obligations.predecessor_context`. Later owner session changes leave the old snapshot unchanged; before/after in live Markdown audit history retain it too. Find earlier predecessors through the explicit predecessor assignment chain, without traversing unrelated session directories.

## 1. Prefer exact online consultation

1. Read the specified handover snapshot. First use fresh `intercom status` to verify yourself, then `list` to check whether the **full original contact ID** is online. Do not substitute name/cwd for identity.
2. If the contact address is an alias or may be reused across restarts, first ask that one exact contact to confirm the actual current Pi session ID/corresponding handover. Call it the "original online session" only if it matches the historical ID. Otherwise label it "new advisor/new session", never the predecessor themself.
3. When online with matching identity, use `intercom ask` for bounded questions and record the actual response and evidence. Old instructions are not new authorization after succession. This is already-authorized lateral predecessor consultation; decisions and progress still go to the current owner's direct superior, and only the top level reports to the user.
4. If the contact is invalid, disconnected, or identity cannot be confirmed, preserve the observation and take the offline path. Do not broadcast to find someone or guess other sessions. Do not send these messages when implementing/testing this skill.

## 2. Offline: governed preparation → parent schedules restricted CLI → governed interpretation

**Native subagents use async notifications; never block on bg_wait for a child.** The owner uses existing capability discovery to select a subagent with the relevant read/private-artifact permissions. The task must specify: current question, exact successor scope, predecessor evidence, files it may read, output directory, no repository changes/no new appointments, reporting recipient, and timeout/uncertainty.

When a leaf is forbidden to launch further agents, it only prepares/reconstructs material and **must not run Pi CLI or fan out again**. After receiving the preparation-complete notification, the authorized owning parent reviews the static plan and personally schedules the one user-authorized read-only consultation process below. This is the owner's knowledge-coordination action, not a switch to implementing business work personally. A governed readonly subagent then checks the answer/evidence. Do not create a profile, change global settings, switch execution modes on failure/retry, or let a leaf secretly launch an unrestricted agent. If existing governance forbids even this explicit parent CLI consultation, report the exact permission gap to the direct superior; do not claim completion.

The preparation subtask runs (after substituting the verified owner/scope, actual question file, new private directory, and approved exact provider/model):

```bash
PR="$HOME/.pi/agent/skills/responsibility-registry/scripts/predecessor.py"
python3 "$PR" prepare --actor 'current-owner-id' --assignment 'successor-scope-id' \
  --question-file '/absolute/private/question.md' --model 'verified-provider/verified-model' \
  --output '/absolute/private/consultation-001'
```

Prepare selects the current actor/assignment from live MD through the same Store and saves era/document_token in the plan. For custom paths, pass both `--root PRIVATE --document LIVE.md`. After a template reset, old actors/assignments are no longer selectable. Copied plans/answers are only old-era advice; record only appends a private consultation log and does not restore identity or responsibilities. A plan prepared before migration without era/token is recorded with null (unknown historical source), not assumed to belong to the current era.

Prepare **does not launch a model/Pi**. It only reads the specified assignment snapshot and this one known transcript, checks ID/cwd and file/directory existence, copies it to private `predecessor.jsonl` (0400), saves its hash, and writes plan.json, question.md, advisor.txt, launch.sh, and consultation.jsonl. Output must be a new directory outside the original cwd/worktree; overwriting is rejected. The original transcript is never written. CLI forks again from the copy to avoid concurrent writers to the old session's file.

The generated launch.sh is equivalent to the following invocation, **confirmed as supported by the installed documentation**, run from the verified original cwd. Actual file values come from the plan, not path guesses:

```bash
cd -- "$ORIGINAL_CWD"
pi --fork "$CONSULT/predecessor.jsonl" --session-dir "$CONSULT/forks" \
  --model "$VERIFIED_PROVIDER_MODEL" \
  --no-extensions --no-mcp --no-skills --no-prompt-templates --no-context-files \
  --no-tools --no-approve --print --system-prompt "$CONSULT/advisor.txt" \
  "@$CONSULT/question.md" > "$CONSULT/answer.md" 2> "$CONSULT/stderr.log"
```

- `--fork <path|id>` is a separate option taking an argument; **do not** combine it with `--session`, `--continue`, `--resume`, or `--no-session`. Do not use the argument-free `--resume` picker / `--continue` to touch the "latest" session. CLI has no `--cwd` suitable here, so use shell `cd`.
- `--print` exits after one answer. All tools/extensions/MCP/skills/project context are disabled. The temporary advisory system prompt explicitly states: historical context is not current authorization; do not take tasks, edit, delegate, or contact others. CLI saves a new fork in a separate directory without restoring the old owner registration.
- The selected exact provider/model must be available in the **restricted invocation**. If it is registered only through a disabled extension, or credentials/identity/protocol fail, the command may be unavailable. Preserve stderr and the exact gap for the direct superior to select an allowed path; never automatically re-enable all extensions/tools or switch models. This verifies installed-version syntax, not successful live execution of any provider.

For long tasks, the owning parent first reads the tmux and pi-notify skills. After verifying its own project session/new free slot, run in **its own new window** (do not touch other existing panes; native subagents are not wrapped in tmux):

```bash
# CONSULT points to the new private directory above; OWNER_CONTACT comes from this owner's fresh intercom status.
# Verify SESSION/FREE_SLOT ownership and availability under the tmux skill; never infer someone else's pane from cwd.
NOTIFY=$(type -P pi-notify || true) # Find the actual executable on PATH first, not an alias/function.
[[ -n "$NOTIFY" ]] || NOTIFY="$HOME/.pi/agent/scripts/pi-notify" # Direct entry point provided by the pi-notify skill.
[[ -f "$NOTIFY" && -x "$NOTIFY" ]] || { printf 'pi-notify executable unavailable: %s\n' "$NOTIFY" >&2; exit 1; }
NOTIFY=$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve(strict=True))' "$NOTIFY")
ORIGINAL_CWD=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cwd"])' "$CONSULT/plan.json")
CMD=$(python3 -c 'import shlex,sys; print(shlex.join([sys.argv[1],"run","--to",sys.argv[2],"--","timeout","180s","bash",sys.argv[3]]))' \
  "$NOTIFY" "$OWNER_CONTACT" "$CONSULT/launch.sh")
tmux -L pi-agent new-window -d -P -F '#{window_id}:#{pane_id}' \
  -t "$SESSION:$FREE_SLOT" -n predecessor-consult -c "$ORIGINAL_CWD" "$CMD"
```

The resolution above creates no global links. If PATH lacks the entry point, use `~/.pi/agent/scripts/pi-notify` already provided by the skill. If neither is executable, exit explicitly before creating a tmux window. Also verify the chosen notification entry point's broker and GNU timeout availability. Notify only the current owner. Retain the exact returned window/pane ID and task directory. The owner yields the turn and handles the completion notification when it arrives. Do not sleep/status-poll or block on subagents for a same-turn answer. If notification infrastructure is unavailable, explicitly record the notification/execution gap and arrange an available governed completion notification rather than silently dropping the task.

## 3. Interpret, label, and record

- Answer from the exact old online session: label live predecessor and retain message/identity evidence.
- CLI fork answer: label **predecessor-context advice**. It is an independent consultation instance using old material, not the closed old process itself, and restores no project ownership.
- Answer from a native readonly subagent merely reading the same old transcript/handover documents: label **reconstruction**. It may resolve questions when the material is sufficient, but cannot impersonate the actual old session/CLI execution. If the fork consultation above is needed, follow the allowed path or report the exact gap, never silently substitute.

The governed interpretation subtask cites the question, source session ID/file/hash, and relevant messages/handover decisions, distinguishing known facts, interpretations, and uncertainty. It must not modify the project, take over tasks, or change the reporting chain. The owner decides the next action from this evidence; goal/priority conflicts still escalate level by level.

```bash
python3 "$PR" record --directory "$CONSULT" --kind predecessor-context \
  --answer-file "$CONSULT/answer.md" --evidence 'Exact fork/message/handover evidence; verified by the interpretation child' \
  --uncertainty 'Matters still unexplained by current material; never label unknowns as risk-free'
# For reconstruction by a readonly child only, use --kind reconstruction and its delivered answer file.
```

Private consultation.jsonl appends the question, predecessor/source, answer, evidence, unresolved uncertainty, increasing sequence, and second-level time with timezone. Prepare first writes prepared; that does not count as receiving an answer. `record --kind` is the caller's evidence-based label, not machine-verified proof of successful inference. A failed Pi run may also leave fork files; file existence does not prove an answer exists. Record online answers with the same fields in private task records (no fabricated offline prepare just to log them). The current owner updates any resulting formal responsibility/goal changes through the registry workflow; consultation itself creates no owner.

If the actual Pi ID is missing, the file is unreadable, the header mismatches, or the original cwd/worktree has disappeared, the helper refuses to generate a launch plan and reports the exact missing item. Do not scan all sessions to fill in a "similar" one. Report the blocker and the exact session-location evidence needed to the direct superior while continuing independent work.

Basis: the installed package's docs/sessions.md, docs/cli.md, and README.md were read in full for this procedure, and options were verified with `pi --no-extensions --help`. Tests use only synthetic transcripts and a fake Pi executable. No user session was actually restored, no real consultation was sent, and no live provider inference was verified.
