# Native session handoff UI

The frontend model-picker integration requires `core.js`, `target-model.js`, `panel.js`, `bootstrap.js`, in that order, in **one separate same-origin namespace script**. The backend feature's concat and `target` route allowlist must be joined with this frontend before deployment; this frontend checkpoint alone does not wire that Python boundary. Do not combine it with owner-groups: bootstrap captures `document.currentScript.src` to locate `__session_handoff__`, including a configured URL prefix.

- Core validates public operations and serializes actions **per opaque source session ID**. Different sources can start concurrently; busy/error/unknown-result locks and late replies stay with their source. Only the currently selected source renders or receives timed status polls. Start reads native `#session` when opening confirmation and again on submit; changed sources require another confirmation and never inherit another source's note. Target links use `location.replace` so native hashchange owns navigation.
- Native selection also uses `history.replaceState`, which emits no event. Passive click/keyboard/hash/popstate listeners re-read the URL after native handlers; a visible-only 250ms URL comparison covers asynchronous native transcript/deeplink changes. Every contextual action captures its displayed source/reference/operation before URL and storage synchronization: if identity changed, the first click only repaints or reconfirms, and another deliberate click is required. It pauses while hidden and is removed on unload/logout. No native history patch, card-name/DOM identity inference, or transcript observer is used.
- Panel attaches one icon button immediately after `.topActions > #diagBtn`. The static Opus SVG depicts context flowing from an old node into a fresh node; `title` and `aria-label` retain “会话接力” without a visible text label. It inherits native `.icon-btn` dimensions, hit area, colors, hover, disabled and keyboard-focus styles; only the SVG shrinks from 20px to 18px at widths up to 420px. Native handlers stay untouched. Observers watch direct shell/main replacements and only the small topbar subtree; never the transcript or whole document tree.
- Bootstrap handles native login/logout without requiring a page reload. Logout disposes session requests, timers, listeners and observers. A fresh root-only login sentinel (no operation data or network requests) waits for the next authenticated shell; pagehide removes that sentinel too.

## Request recovery

Each source has its own local storage key, `codoxear:session-handoff:<namespace path>:source:<encodeURIComponent(source ID)>`. Separate keys avoid whole-map lost updates between tabs operating on different sources. Only minimal source-associated identities are stored:

- confirmed operation: `{operation_id,source_session_id}`;
- pending start: `{request_id,source_session_id,target?}`, where an explicit `target` is the frozen complete `{model_provider,model,reasoning_effort}` tuple; absent target preserves legacy inheritance;
- uncertain explicit continue: `{operation_id,source_session_id,pending_action:"continue",pending_id}`; the UUID `pending_id` distinguishes successive explicit intents, while older records without it remain readable.

The former namespace-wide base key is migrated only after the new source key is written. A legacy pending start already identifies its source. A legacy operation without a source is queried read-only and assigned using the **returned** source, never the selected session. Failed lookups/writes or conflicting references preserve the original record, show a recovery warning, and do not lock unrelated sessions. Mount never replays pending starts or continuations. Status responses must match both source ID and operation ID.

An explicit tuple is validated, copied and saved **before** the first POST. Same-UUID recovery after reload carries exactly that tuple; it never drops back to inheritance. Malformed saved targets retain an error/lock and the original record without sending a request. Cross-tab ownership includes the target, not only the UUID.

Notes, status payloads, transcripts and credentials are never persisted. A pending start is not replayed on reload. The user can reconcile it with the same UUID; a note lost on refresh is explicitly disclosed. A pending continue is queried but never resent while unreviewed or explicitly unknown. Only its correlated response or a status observation begun with the same persisted intent can resolve the lock upon state advancement. Request completions, including rejections, compare current stored source-reference ownership before writing; replaced/deleted references discard old results and synchronize the view. These checks cover stale observations, not atomic cross-tab compare-and-swap. Storage failure before mutation prevents the request.

Chrome can repeat a POST internally when a keepalive connection resets, even without JavaScript retry code. Therefore backend start idempotency and continue's durable side-effect guard are required independently of UI single-click protection. A successful HTTP response alone never produces `ready`; the panel displays the server's public state.

Status POSTs run only while the document is visible. Terminal states stop timed polling; explicit status refresh remains available. Unknown mutation states cannot be dismissed into an accidental new request. Authentication loss clears local feature references and shows an explicit login/permission message.

## Native target-model reuse

`target-model.js` lazily imports the served native `createNewSessionController`, `iconSvg`, and `createDialogMenuController`. The existing authenticated same-origin `GET api/sessions` is the sole catalog source (`cache:no-store`, bypassing native service-worker stale fallback). Native controller methods own grouping, Recent, ranked search, provider/model display/parsing, and reasoning capabilities/resolution. The handoff owns only a minimal DOM shell and source-local immutable draft choices. It never instantiates the native New-session dialog, invokes native Start/create, reconstructs missing provider/model pairs, or calls native preference-writing methods (`setNewSessionProvider`, `selectNewSessionModel`, `syncNewSessionProviderFromModelInput`, `applyNewSessionLaunchPreset`). No native `codoxear.*` storage is written.

Initial inheritance is labelled `沿用原会话设置`, without claiming the native row is runtime-current. Explicit selection requires a committed real native pair, never a synthetic or case-insensitive `default` model. A missing/incompatible module, unavailable/empty catalog, or **any** `warnings.pi` key disables explicit selection; inheritance remains available. Dirty queries or a no-longer-available selected pair/effort block confirmation rather than silently changing the choice. Native effort adjustments are shown in the confirmation. Native may omit duplicate IDs under a second provider or Pi may clamp advertised effort: this bridge follows native authority, not a second catalog.

Optional server `target_settings` is frozen creation configuration, not runtime proof. Inherited echoes say `沿用原会话列表设置`. Optional `target_check` reports asymmetric observations: model mismatch is an alert, effort mismatch a visible note, and equality is only `not_contradicted`, never a verification claim. Observations do not retarget operations, change locks or trigger retries.

## Isolated verification

```sh
CODOXEAR_STATIC_DIR=/absolute/read-only/codoxear/static \
  node --test codoxear/session-handoff/tests/{core,panel,target-model}.test.cjs
# Run this persistent fixture in a task-owned tmux pane:
CODOXEAR_STATIC_DIR=/absolute/read-only/codoxear/static \
  node codoxear/session-handoff/tests/frontend-fixture.cjs
# Run the Chrome suite as a resource-bounded job on the test machine:
AGENT_BROWSER_BIN=/absolute/path/to/agent-browser \
  node codoxear/session-handoff/tests/browser.test.cjs
```

The fixture binds only loopback (`PORT`, default 8896), serves actual upstream assets read-only, injects the two extension scripts and provides synthetic APIs. UI and data are marked **DEMO**. It never starts agents, writes registry data, or contacts production APIs. The browser test accepts only a loopback `/demo/` URL, uses named ephemeral Chrome sessions and closes its own sessions. `HANDOFF_SCREENSHOTS` selects the output directory. Desktop includes native edit/cancel, delete/cancel and synthetic duplicate; mobile checks 390px layout and target navigation. Both viewport tests assert the exact SVG, accessible name, keyboard open/Escape cancellation, visible focus, native-sized unoccluded toolbar hit areas and no overflow. Remount and icon-child mutation checks guard against duplicate buttons and observer feedback.

The concurrency regression holds A's start response open, selects B through the real native sidebar, starts B before A completes, then releases late A and checks both source views and refresh recovery. Both desktop and mobile cover unsourced legacy migration while B is selected and selected-only terminal forgetting. Separate tabs exercise independent source writes; storage refusal, auth loss, hidden polling, and unload cleanup are also checked. Controlled promises cover old recovering/ready responses versus new Continue intents and replacement/deleted references. Controlled panel timers and real desktop/mobile single-task `replaceState`→click probes exercise the pre-monitor gap: a displayed A action cannot act on B, and a deliberate second B click works. Browser condition waits are bounded to avoid hanging on unrelated native fixture failures. A `GAP mobile native Duplicate/drawer fixture` line explicitly reports incomplete coverage of that existing native action (the drawer can close after Delete cancellation); desktop Duplicate and all handoff assertions remain mandatory.

These tests establish frontend behavior against the supplied native asset version. Actual Pi reconstruction, review and continuation require the separate integrated workflow acceptance test.
