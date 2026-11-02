# Native session handoff UI

The feature concatenates `core.js`, `panel.js`, `bootstrap.js`, in that order, into **one separate same-origin namespace script**. Do not combine it with owner-groups: bootstrap captures `document.currentScript.src` to locate `__session_handoff__`, including a configured URL prefix.

- Core validates the public operation, parses native `#session`, and serializes user actions. Start reads the hash when opening confirmation and again on submit. A changed source needs another confirmation. Target links use `location.replace` so native hashchange owns selection.
- Panel attaches one icon button immediately after `.topActions > #diagBtn`. The static Opus SVG depicts context flowing from an old node into a fresh node; `title` and `aria-label` retain “会话接力” without a visible text label. It inherits native `.icon-btn` dimensions, hit area, colors, hover, disabled and keyboard-focus styles; only the SVG shrinks from 20px to 18px at widths up to 420px. Native handlers stay untouched. Observers watch direct shell/main replacements and only the small topbar subtree; never the transcript or whole document tree.
- Bootstrap handles native login/logout without requiring a page reload. Logout disposes session requests, timers, listeners and observers. A fresh root-only login sentinel (no operation data or network requests) waits for the next authenticated shell; pagehide removes that sentinel too.

## Request recovery

Local storage key is `codoxear:session-handoff:<namespace path>`; only the operation reference or pending request identity is stored:

- confirmed operation: `{operation_id}`;
- pending start: `{request_id,source_session_id}`;
- uncertain explicit continue: `{operation_id,pending_action:"continue"}`.

Notes, status payloads, transcripts and credentials are never persisted. A pending start is not replayed on reload. The user can reconcile it with the same UUID; a note lost on refresh is explicitly disclosed. A pending continue is queried but never resent while still unreviewed. Storage failure before mutation prevents the request.

Chrome can repeat a POST internally when a keepalive connection resets, even without JavaScript retry code. Therefore backend start idempotency and continue's durable side-effect guard are required independently of UI single-click protection. A successful HTTP response alone never produces `ready`; the panel displays the server's public state.

Status POSTs run only while the document is visible. Terminal states stop timed polling; explicit status refresh remains available. Unknown mutation states cannot be dismissed into an accidental new request. Authentication loss clears local feature references and shows an explicit login/permission message.

## Isolated verification

```sh
node --test codoxear/session-handoff/tests/core.test.cjs
# Run this persistent fixture in a task-owned tmux pane:
CODOXEAR_STATIC_DIR=/absolute/read-only/codoxear/static \
  node codoxear/session-handoff/tests/frontend-fixture.cjs
# Run the Chrome suite as a resource-bounded job on the test machine:
AGENT_BROWSER_BIN=/absolute/path/to/agent-browser \
  node codoxear/session-handoff/tests/browser.test.cjs
```

The fixture binds only loopback (`PORT`, default 8896), serves actual upstream assets read-only, injects the two extension scripts and provides synthetic APIs. UI and data are marked **DEMO**. It never starts agents, writes registry data, or contacts production APIs. The browser test accepts only a loopback `/demo/` URL, uses named ephemeral Chrome sessions and closes its own sessions. `HANDOFF_SCREENSHOTS` selects the output directory. Desktop includes native edit/cancel, delete/cancel and synthetic duplicate; mobile checks 390px layout and target navigation. Both viewport tests assert the exact SVG, accessible name, keyboard open/Escape cancellation, visible focus, native-sized unoccluded toolbar hit areas and no overflow. Remount and icon-child mutation checks guard against duplicate buttons and observer feedback.

These tests establish frontend behavior against the supplied native asset version. Actual Pi reconstruction, review and continuation require the separate integrated workflow acceptance test.
