# Pi → tmux breathing status

This Pi 0.84.3 extension exposes **logical Pi work** and root model/provider availability failures, not terminal-process activity, in the tmux window status line.

- `agent_start` marks the local root Pi turn active; only `agent_settled` returns it idle.
- Only the root Pi process publishes leases. Nested child Pi processes (`PI_SUBAGENT_DEPTH > 0`) are ignored so a child paused for attention cannot keep the parent window active.
- pi-subagents 0.56 `subagent:async-started` / `subagent:async-complete` events add and remove detached child work only when their `sessionId` matches the root Pi session. If a terminal top-level async root has a trusted `nestedRoute`, the extension retains it until the read-only nested registry sidecar shows all descendants terminal. If completion includes live nested children but no trusted sidecar, the extension logs that the root is retained failure-closed until session shutdown because no authoritative source can prove terminal.
- `subagent:control-event` with `needs_attention` makes that child/node idle immediately. Its atomically written `status.json` and, for terminal roots, `dirname(nestedRoute.eventSink)/registry.json` are watched; newer projected activity reactivates it. Missing, malformed, or mismatched nested registries are failure-closed active and retried by the heartbeat. A waiting child therefore cannot hide a concurrently running sibling.
- A private atomic lease is written while work is active (`state:"active"`) or a model/provider availability error is latched (`state:"error"`). It expires after 6 seconds, repairing crash leftovers. Leases never store raw provider error text.
- A 2-second repair monitor stays alive while the root turn or any tracked child exists. It refreshes retained terminal-root registries and publishes an active lease only when logical work is active; attention-idle children keep repair running but do not start breathing. The lease diagnostics contain the parent Pi session ID plus sorted active top-level run IDs and active node IDs. They deliberately exclude prompts, history, cwd, and names.
- A conservative classifier latches ERROR only for assistant `stopReason:"error"` model/provider availability failures such as quota/billing (including weekly/period usage limits and CJK phrasing), auth/API key, model not found, exact `terminated`, timeout, transport, rate-limit/overload, no deployment, and 5xx/provider unavailable errors. It excludes user aborts, context overflow, policy/refusal, ordinary 400/schema failures, and tool-result errors. The latch survives retry/agent start/settled and clears on semantic assistant output, successful assistant completion, or model selection. When the agent settles idle on such an error with no queued user input, the extension sends `pi.sendUserMessage("continue")` after a 250 ms debounce (cancelled by user `input` or `agent_start`), throttled to one send per 30 s, at most 10 times per failure streak; the streak resets on any normal assistant output. At the cap the window stays red and no further automatic message is sent. Auto-continue never clears the red latch itself. Pi's own auto-retry turns each failed attempt into `stopReason:"error"` and, when retries are exhausted, rewrites the message to `stopReason:"aborted"` with `Aborted after N retry attempts`; that retry-exhaustion tail preserves the armed continue gate (it is the classified error's consequence, not new information), while a user-initiated `Operation aborted` clears it.
- One detached animator per tmux socket uses a PID/token-owned exclusive lock. ACTIVE updates tmux window user options at 24 frames × 42 ms/frame (~24 FPS), a ~1.0 s monotonic-time cycle. ERROR is steady `#d70000` with `#ffffff` text and reconciles at a slow 1 s cadence when no active window exists. Delayed callbacks skip to the correct phase instead of stretching the animation.
- The unselected active palette breathes symmetrically through `#bcbcbc → #f5f5f5 → #bcbcbc → #808080 → #bcbcbc`; the selected active palette uses `#00afff → #7be0ff → #00afff → #0087af → #00afff`.
- The animator caches attached tmux clients for roughly one second. Each rendered frame is one semicolon-batched tmux update containing all active/reset window-option commands and all cached-client `refresh-client -S` commands. Zero or temporarily stale clients do not stop the helper permanently.
- On startup the animator clears stale window options with no live lease; it exits—and clears its active/error window options—when no live lease remains. Window priority is ERROR > ACTIVE > IDLE.

## Deployment

Run the independent installer:

```bash
bash fresh-install/modules/tmux/install-pi-tmux-window-status.sh
```

It creates exactly one managed link:

```text
~/.pi/agent/extensions/pi-tmux-window-status
  → <checkout>/pi-agent/extensions/pi-tmux-window-status
```

An exact link is skipped. A stale symlink at the new path whose target suffix is recognizably `pi-agent/extensions/pi-tmux-window-status` (for example after a checkout move) is backed up and replaced. The legacy name `~/.pi/agent/extensions/quick-deploy-tmux-status` is still recognized: when it is a known managed link to this repo's old extension path, it is treated as a legacy install and migrated — backed up with a timestamp and then replaced by the new link. Unknown files, directories, and foreign symlinks at either path are left untouched and cause a failure; if both old and new paths exist, the installer proceeds only when both are known managed links and otherwise fails without mutating anything. The installer accepts `QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_SOURCE`, `QUICK_DEPLOY_PI_HOME`, `QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_TARGET`, and `QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_LEGACY_TARGET` for isolated tests.

**Runtime contract is intentionally unchanged by this rename.** tmux window options remain `@quick_deploy_pi_*` and the private lease directory remains `quick-deploy/pi-tmux-status`; only the managed symlink name changed. This keeps already-running Pi processes and outstanding leases working and creates no duplicate runtime state.

On startup/reload, restoration reads only pi-subagents 0.56's active-run index under `ASYNC_DIR/.active-runs` beneath `PI_SUBAGENTS_TEMP_ROOT` (or its default scoped temp root), and adopts only status files whose `sessionId` matches the current Pi session. It does not scrape arbitrary directories.

## Pi auto-discovery

Pi does **not** scan this repository. At startup Pi loads extension directories it finds under `~/.pi/agent/extensions/`, which is exactly what the managed symlink above provides; the tracked extension under `pi-agent/extensions/pi-tmux-window-status/` is inert until that link exists. After installing or updating the extension, **restart Pi or run `/reload`** so the new code is loaded. Reloading tmux only changes the visual format; it does not load the Pi extension. The current Pi process is not reloaded by the installer.

The status format deliberately uses absolute styles so activity color always wins. `last` and `activity` overlays therefore retain their label semantics but do not change background; bell preserves yellow foreground and `!`, and zoom preserves `Z`, while the active/idle background remains authoritative. The dark edge cell still separates adjacent tabs.

Runtime state is private under `${XDG_RUNTIME_DIR}/quick-deploy/pi-tmux-status` (or `${XDG_STATE_HOME:-~/.local/state}/quick-deploy-runtime/quick-deploy/pi-tmux-status`) and must never be committed.

## Diagnostics

When Pi is outside tmux, tmux identity resolution fails, or an async payload is malformed, the extension writes an explicit diagnostic to Pi's stderr and does not fabricate activity. This needs the `pi-subagents` extension in the same root Pi process to receive its async event bus channels.
