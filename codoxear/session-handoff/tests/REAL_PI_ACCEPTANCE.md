# Isolated native Pi acceptance

The procedure below is the approved isolated plan. A bounded execution outcome
is recorded at the end; it does not imply production deployment.
`test_integration_fixture.py` checks the evidence helpers without spawning Pi.
The normal unit suite must never start agents, copy credentials, or contact a
model provider. A parent-approved dedicated run is required for the steps below.

## Runtime boundary

Create a fresh `mkdtemp` root named `codoxear-handoff-acceptance-*` under the
system temporary directory. Record its absolute path privately before launch.
Do not put this root under the live project or reuse an earlier fixture.

| Purpose | Location within the new root |
|---|---|
| Fake task and stale memory | `project/` (from `prepare_fixture`) |
| Synthetic predecessor history | `home/.pi/agent/sessions/demo/<unrelated-uuid>.jsonl` |
| Independent expected outcome | `ground-truth.json` |
| Home directory | `home/` |
| Pi agent directory and sessions | `home/.pi/agent/` |
| Native app state/sockets/HMAC | `home/.local/share/codoxear/` |
| Handoff operation state | `state/` |
| Isolated auth copy | `home/.pi/agent/auth.json`, mode 0600 |
| Task-specific executable shims | `bin/` |
| Private tmux socket | `tmux.sock` |
| Private evidence, timestamps, ownership | `evidence/` |

Use an environment **allowlist**, not `{**os.environ, ...}`: explicit PATH,
HOME, USER, LANG, TERM, SHELL, PI_HOME, PI_CODING_AGENT_DIR,
XDG_CONFIG_HOME/XDG_CACHE_HOME/XDG_STATE_HOME, PYTHONDONTWRITEBYTECODE,
PI_OFFLINE, and test-only CODEX_WEB configuration. No inherited intercom,
subagent, registry, SSH agent, cloud credentials, TMUX, or production Web env.
Use a new random local Web password and a loopback ephemeral port; retain its
cookie only in the test client memory. Never output env/auth/header dumps.

The approved model is exactly `openai/gpt-6-astra`. Read the installed model
catalog without changing it. If isolated credentials cannot authenticate this
route, stop and report that failure; no alternate provider/model. Only after
approval, construct an **access-only openai entry** with the existing valid
access token, original real expiry and required account/client metadata. The
installed schema requires a string `refresh`; use an explicitly invalid test
sentinel, never the real refresh secret. Do not print credentials, create a
symlink, or extend expiry. Require remaining TTL to cover the bounded run plus
SDK refresh margin before spawning. If insufficient, only the parent's
separately approved native ModelRuntime.getAuth coordinated refresh may update
canonical auth; a failed refresh stops this attempt. The test copy must never
rotate account credentials. Do not copy settings, prompts, extensions,
packages, skills, registry, MCP configuration, or other providers. Bound provider
retries and total runtime in the private settings.

Use installed Pi and installed native Python read-only, never `run_server.sh`
or setup/update wrappers. No shared venv install, upstream fetch or global
config/alias edits. A test-only PI_BIN shim may append resource-isolation flags
(`--no-extensions`, `--no-skills`, `--no-prompt-templates`, `--no-themes`,
`--no-mcp`, `--no-approve`, `--offline`) while retaining the broker-supplied
argv exactly. Keep the native active-session bridge if broker passes it
explicitly; its role is session identity, not owner registration. Fixture
AGENTS.md remains visible. No custom system prompt supplies the expected answer.

### Why a private tmux route is required

Inspected upstream `session_launcher._launch_tmux_broker` calls `tmux` without
`-S`. `AgentBackend.build_tmux_inline_env` does not forward HOME or
PI_CODING_AGENT_DIR. A clean Web process environment alone therefore does not
isolate a broker created on a pre-existing tmux server.

For this test only, obtain parent approval for a private socket. A PATH-local
`tmux` shim must execute the installed tmux with an explicit `-S <root>/tmux.sock`
and `-f /dev/null`; it must never silently fall back to default or managed user
sockets. Initialize that server from the allowlisted environment. All server,
source, and successor windows belong to this single test. Record exact pane,
window, broker PID/start ticks and agent PID/start ticks before interacting or
cleaning up. No input to a broker pane via tmux: use native HTTP `/send`.
Stop only these verified task-owned processes/windows. Do not kill any existing
tmux server/session or signal production brokers/agents.

## Actual experiment

1. Generate the fixture with `prepare_fixture`; initialize a temporary Git
   repository in `project/` if required for native cwd validation. Record
   `project_fingerprint` before launch. The fixture has goal X → Y, off-branch
   Z, rejected vendor delegation, stale memory X, an absent claimed edit, and
   an unverified worker claim. The latest long user message places goal Y and
   the vendor constraint at its tail, beyond the old prefix-only excerpt.
   Only `main.py` may change. Keep the seed log inside the private native Pi
   sessions namespace; never weaken native resume validation.
2. Start installed native Web under the clean home and private tmux route,
   plus the integrated shared host with a test state root. Use both actual
   features, without reading a real owner registry. Empty/missing test registry
   is acceptable for this semantic run; grouping is a separate browser fixture.
3. Create the predecessor via native API with the synthetic history as its
   explicitly selected session and a fixture-only instruction to answer READY
   and remain idle without changing task files. Scope that instruction to this
   predecessor only: the successor retains authorization to do Y after review.
   The predecessor must not supply an answer-key summary. Bind it by returned
   launch ID/broker PID, then real header ID. Preserve the synthetic origin of
   historical records in the evidence; this is not a production session.
4. POST handoff start for that opaque source route. Record the exact native
   successor create payload through a test observer: known source cwd,
   `agent_backend=pi`, `model_provider=openai`, `model=gpt-6-astra`,
   `create_in_tmux=true`, and `args=[actual_recovery_prompt]`. No resume argument,
   no standalone `--`, no manual Enter, no initial `/send`. Do not wait for a
   target transcript before native create.
5. When the actual target log materializes, use `assert_startup_submitted` to
   compare its first persisted user message with the actual argv prompt and
   check a new header identity. Capture this evidence **before any external
   send to that successor**. A terminal screenshot/prefill or HTTP 200 does not
   establish submission.
6. At understanding submission, record a project fingerprint and prove no
   project files changed. Inspect the actual report against the independent
   truth; do not score it solely by keyword presence. The old agent receives
   only the real correlated review prompt through normal native `/send`.
7. Capture old-agent tool calls and a fingerprint at review submission. Verify
   it only reviewed/wrote operation-local drafts and did not advance the task.
   Native 200 is merely accepted delivery. The matching review artifact proves
   the review protocol ran. If it finds no defect, retain `clear` honestly.
8. Deliver one actual continuation to the bound successor. Capture resolution
   publication **before** the first task-file write (an event/file watcher with
   monotonic timestamps plus transcript tool-result order). Validate the exact
   understanding/review/resolution chain with `read_correlated_chain`.
9. Inspect resolution semantics and `assert_intended_action`: self-contained
   `main.py result()` returns literal Y; `vendor.py`, memory, and all other task
   files remain byte-identical. No invented worker, no delegation, no repeated
   approval loop. A ready receipt without this artifact is not toy-task success.
10. In a separately labelled deterministic actor test, deliberately omit the
    vendor constraint from understanding, require concrete predecessor
    correction, and require successor acknowledgment and the correct action.
    Never alter a real model report to manufacture a correction. If the real
    run itself needs correction, preserve and inspect the actual evidence.

Run the bounded acceptance driver through the resource-limited `job` service
with a completion notification. Its native Web child runs in the test socket's
`acceptance` tmux session; the runner records its pane, PID and start ticks and
terminates only that verified Web PID. The operator still owns exact cleanup
of the dedicated predecessor/successor broker windows and anchor.

Use finite stage deadlines and event-driven local file/process notifications.
Transient native/Pi lock directories may vanish before inotify registration;
ignore only ENOENT there so later receipt events in the same buffer are retained.
No sleep/poll loops and no automatic resend/recreate after ambiguous mutations.
Record failure at the original stage rather than making a retry look like the
same run. The native application may use its own bounded readiness mechanisms.

The current runner requires exactly four handoff mutations in order: successor
create, identity-bound successor rename to the frozen `_NN` name, predecessor
review send, successor continuation send. Alias readback verifies display state;
actual tmux convergence is separately checked by the private fake-Pi naming
probe (`test_native_naming.py`), not inferred from HTTP 200. The historical run
recorded below predates naming and retains its original three-mutation evidence.

## Evidence and reporting

Private evidence includes exact launch argv (no secrets), real transcript IDs,
first persisted startup user entry, report chain hashes, milestone file
fingerprints, ordered mutation observations, and manually annotated reports.
Sanitize published summaries; do not commit raw transcripts or auth state.

Report separately: fixture/helper tests, deterministic workflow actors, native
Pi startup and report chain, actual toy action, browser coexistence, and live
rollout. None implies another. This plan authorizes no production deployment.

## Recorded isolated execution — 2026-11-02

The integrated shared host and installed native runtime completed one real
`openai/gpt-6-astra` run with dedicated predecessor and successor actors. The
resource-bounded driver exited 0 after about four minutes. No canonical auth
refresh was needed; the private credential used its true expiry and an invalid
refresh sentinel, and was removed after cleanup.

Manual inspection of all three report bodies against the independent fixture
confirmed the following:

- The successor selected Y from the long latest-user tail despite repeated X
  history and stale X memory. It inspected actual files and correctly identified
  the historical claimed edit as absent and the worker claim as unestablished.
- It retained the self-contained implementation constraint and main-only edit
  scope. Its first persisted user entry exactly matched native creation argv;
  the successor had a new Pi header identity. No manual initial send occurred.
- Project fingerprints were unchanged before both review and continuation
  delivery. The predecessor only read evidence and wrote its operation-local
  review; its actual verdict was `clear`, without manufactured corrections.
- One successor resolution accepted the evidence-backed review and was
  successfully submitted before the first `main.py` edit. The transcript's
  successful submit result precedes the edit call and result. The actor then
  actually invoked Python with `-B` and verified `main.result() == "Y"`.
  `vendor.py`, memory and AGENTS remained byte-identical to their baseline.
- The observed handoff mutations were exactly one successor create, one source
  review send and one successor continuation send. No second approval loop,
  delegation or production operation occurred. All owned actors, broker/Web
  processes and dedicated tmux panes were cleaned up.

Evidence limitation: the running observer logged transient-directory ENOENT
callbacks during early native lock-file activity. Its event list and complete
transcript still establish resolution-before-edit for this run; a later harness
fix retains subsequent events after that race and has a regression test, but
that observer fix has not had a second real-agent execution. The real run covered
clear review; deliberately incomplete understanding/correction remains a
separately labelled deterministic test. Browser coexistence was independently
validated with synthetic APIs, not inferred from this native run. Private raw
transcripts, credentials and environment records are never committed.
