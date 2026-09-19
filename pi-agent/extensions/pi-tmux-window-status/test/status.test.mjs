import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { HEARTBEAT_MS, LEASE_TTL_MS, acquireAnimatorLock, aggregateLogicalState, leasePath, projectAsyncStatus, publishLease, readLease, releaseAnimatorLock, seedAttentionWatermarksFromSnapshot, windowLeaseStates } from "../state.mjs";
import { CANCEL_SENTINEL, RECOVERY_ARMED_OPTION, animatorSpawnNeeded, childFromStartedPayload, childStillRunning, classifyModelUnavailable, defaultAsyncRoot, isManualEscapePress, isRetryExhaustedAbort, mergedChildSnapshot, monitorNeeded, nestedProjectionFromChildren, nestedPublisherDisabled, nextLastAssistantErrorMatched, nextModelErrorState, readNestedRegistryProjection, recoveryTerminalInput, restoredChildren, sessionIdOf, validateNestedRoute } from "../index.ts";
import { ANIMATOR_URGENT_SIGNAL, ERROR_BG, ERROR_FG, ERROR_MS, FRAME_BLUE, FRAME_COUNT, FRAME_MS, FRAMES, GRAY_RANGE, MAX_CONSECUTIVE_FAILURES, PERIOD_MS, activeWindows, frameAt, isDirectExecution, leaseWindowStates, listClients, listRecoveryArmedPanes, paneRecoveryOptionArgs, requestAnimatorTick, runAnimator, sweepWindows, windowOptionArgs } from "../animator.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../../../..");
const trash = [];
const temp = () => { const d = mkdtempSync(join(tmpdir(), "pi-tmux-status-")); trash.push(d); return d; };
afterEach(() => { while (trash.length) rmSync(trash.pop(), { recursive: true, force: true }); });
const withoutTmuxEnvironment = (source = process.env) => Object.fromEntries(Object.entries(source).filter(([key]) => key !== "TMUX" && !key.startsWith("TMUX_")));
const isolatedTmux = (args, options = {}) => { const { env = process.env, ...rest } = options; return execFileSync("tmux", args, { ...rest, env: withoutTmuxEnvironment(env) }); };
const inertUrgent = () => () => {};
const styledCells = (value) => {
  const line = String(value).split("\n")[0];
  const cells = [], style = { fg: undefined, bg: undefined };
  for (let offset = 0; offset < line.length;) {
    if (line.startsWith("#[", offset)) {
      const end = line.indexOf("]", offset + 2);
      assert.notEqual(end, -1, `unterminated tmux style in ${line}`);
      for (const field of line.slice(offset + 2, end).split(",")) {
        if (field === "default") { style.fg = undefined; style.bg = undefined; }
        else if (field.startsWith("fg=")) style.fg = field.slice(3);
        else if (field.startsWith("bg=")) style.bg = field.slice(3);
      }
      offset = end + 1;
      continue;
    }
    const char = String.fromCodePoint(line.codePointAt(offset));
    cells.push({ char, fg: style.fg, bg: style.bg });
    offset += char.length;
  }
  return cells;
};
const ident = (socket = "/tmp/tmux-status") => ({ socketPath: socket, windowId: "@8", paneId: "%9" });
const real = (runId = "root", lastActivityAt = 100, state = "running", steps = []) => ({ runId, state, lastUpdate: lastActivityAt, steps });
const waitFor = async (read, expected, timeoutMs = 1_500) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (read() === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.equal(read(), expected);
};
let harnessSequence = 0;
async function statusHarness(label) {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock");
  const tmux = (args, options = {}) => isolatedTmux(args, options);
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", label]);
  const paneId = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const handlers = {}, sent = [];
  let terminalInput, terminalInputUnsubscribed = 0, currentSignal;
  const ctx = {
    mode: "tui",
    ui: { onTerminalInput: (handler) => { terminalInput = handler; return () => { terminalInputUnsubscribed += 1; }; } },
    get signal() { return currentSignal; },
    isIdle: () => true,
    hasPendingMessages: () => false,
    sessionManager: { getSessionFile: () => `/home/tester/.pi/agent/sessions/sanitized/${label}.jsonl` },
  };
  const pi = {
    on: (name, fn) => { (handlers[name] ||= []).push(fn); },
    events: { on: () => () => {} },
    sendUserMessage: (content) => sent.push(content),
  };
  const fire = (name, ...args) => (handlers[name] || []).map((fn) => fn(...args));
  const env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime, TMUX_PANE: paneId, TMUX: `${socket},0,0` };
  const origEnv = { ...process.env };
  Object.assign(process.env, env);
  try {
    const mod = await import(`../index.ts?status-harness-${label}-${++harnessSequence}`);
    mod.default(pi);
    fire("session_start", {}, ctx);
  } catch (error) {
    Object.keys(process.env).forEach((key) => { if (!(key in origEnv)) delete process.env[key]; });
    Object.assign(process.env, origEnv);
    tmux(["-S", socket, "kill-server"]);
    throw error;
  }
  return {
    handlers, sent, ctx, fire,
    get terminalInput() { return terminalInput; },
    setSignal(signal) { currentSignal = signal; },
    errorOption: () => tmux(["-S", socket, "show-options", "-wqv", "@quick_deploy_pi_error"], { encoding: "utf8" }).trim(),
    paneArmed: () => tmux(["-S", socket, "show-options", "-pqv", "-t", paneId, RECOVERY_ARMED_OPTION], { encoding: "utf8" }).trim(),
    async cleanup() {
      fire("session_shutdown", {}, ctx);
      assert.equal(terminalInputUnsubscribed, 1, "session shutdown releases the raw input listener");
      Object.keys(process.env).forEach((key) => { if (!(key in origEnv)) delete process.env[key]; });
      Object.assign(process.env, origEnv);
      try { tmux(["-S", socket, "kill-server"]); } catch {}
    },
  };
}

test("24-frame breathing palette starts at idle baseline and hits symmetric perceptual ranges", () => {
  assert.equal(FRAMES.length, FRAME_COUNT);
  assert.equal(FRAME_MS, 42);
  assert.equal(PERIOD_MS, 1008);
  assert.equal(FRAMES[0], GRAY_RANGE.idle);
  assert.equal(FRAMES[6], GRAY_RANGE.bright);
  assert.equal(FRAMES[12], GRAY_RANGE.idle);
  assert.equal(FRAMES[18], GRAY_RANGE.dark);
  assert.equal(FRAME_BLUE, "#0077aa", "selection frame color stays fixed and never breathes");
});

test("isolated tmux environment removes every ambient tmux routing variable", () => {
  const clean = withoutTmuxEnvironment({
    HOME: "/home/tester",
    PATH: "/bin",
    TERM: "xterm-256color",
    TMUX: "/tmp/outer,1,0",
    TMUX_PANE: "%9",
    TMUX_SOCKET: "/tmp/outer",
    TMUX_CONF: "/home/tester/.tmux.conf",
    TMUX_CONF_LOCAL: "/home/tester/.tmux.conf.local",
    TMUX_PROGRAM: "/usr/bin/tmux",
  });
  assert.deepEqual(clean, { HOME: "/home/tester", PATH: "/bin", TERM: "xterm-256color" });
});

test("monotonic frame phase wraps and skips delayed callbacks", () => {
  assert.equal(frameAt(0), 0);
  assert.equal(frameAt(41), 0);
  assert.equal(frameAt(42), 1);
  assert.equal(frameAt(42 * 10 + 17), 10);
  assert.equal(frameAt(PERIOD_MS + 42 * 3 + 1), 3);
  assert.equal(frameAt(-1), 23);
});

test("nested child publishers are disabled while root publishers remain enabled", () => {
  assert.equal(nestedPublisherDisabled({ PI_SUBAGENT_DEPTH: "1" }), true);
  assert.equal(nestedPublisherDisabled({ PI_SUBAGENT_DEPTH: "0" }), false);
  assert.equal(nestedPublisherDisabled({}), false);
});

test("manual Esc detection accepts terminal press encodings without matching releases or modifiers", () => {
  for (const data of ["\x1b", "\x1b[27u", "\x1b[27;1u", "\x1b[27;1:1u", "\x1b[27;1:2u", "\x1b[27;65u", "\x1b[27;1;27~"]) {
    assert.equal(isManualEscapePress(data), true, JSON.stringify(data));
  }
  for (const data of ["\x1b[27;1:3u", "\x1b[27;3u", "\x1b[27;3;27~", "\x1b[A", "x"]) {
    assert.equal(isManualEscapePress(data), false, JSON.stringify(data));
  }
});

test("terminal cancellation consumes only the sentinel and leaves raw Esc as passthrough fallback", () => {
  let cancellations = 0;
  assert.deepEqual(recoveryTerminalInput(CANCEL_SENTINEL, () => { cancellations += 1; }), { consume: true });
  assert.equal(recoveryTerminalInput("\x1b", () => { cancellations += 1; }), undefined);
  assert.equal(recoveryTerminalInput("x", () => { cancellations += 1; }), undefined);
  assert.equal(cancellations, 2);
});

test("installed fullscreen search consumes real Esc only after the private sentinel cancels recovery", async () => {
  const npmRoot = execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
  const piPackage = join(npmRoot, "@earendil-works/pi-coding-agent");
  const tuiRoot = join(piPackage, "node_modules/@earendil-works/pi-tui/dist");
  const [{ TuiAltScreen }, { StdinBuffer }] = await Promise.all([
    import(pathToFileURL(join(tuiRoot, "tui-alt-screen.js")).href),
    import(pathToFileURL(join(tuiRoot, "stdin-buffer.js")).href),
  ]);
  const terminal = { columns: 80, rows: 24, write() {}, hideCursor() {}, showCursor() {}, start() {}, stop() {}, setTitle() {}, setProgress() {} };
  const tui = new TuiAltScreen(terminal, false);
  tui.requestRender = () => {};
  const seen = [];
  let cancellations = 0;
  tui.addInputListener((data) => { seen.push(data); return recoveryTerminalInput(data, () => { cancellations += 1; }); });
  tui.toggleSearch();
  assert.equal(tui.activeSearch?.overlay?.isFocused(), true, "installed fullscreen search owns Esc before extension listeners");

  const buffer = new StdinBuffer({ escapeTimeout: 1 });
  buffer.on("data", (data) => tui.handleTerminalInput(data));
  buffer.process(CANCEL_SENTINEL + "\x1b");
  await new Promise((resolve) => setTimeout(resolve, 10));
  buffer.destroy();

  assert.equal(cancellations, 1, "sentinel reaches and cancels through the actual installed listener chain");
  assert.deepEqual(seen, [CANCEL_SENTINEL], "the earlier viewport listener consumes the real search-closing Esc");
  assert.equal(tui.activeSearch, undefined, "the same first real Esc still closes fullscreen search");
});

test("model/provider unavailable classifier accepts provider failures and rejects non-availability errors", () => {
  const assistantError = (errorMessage) => ({ role: "assistant", stopReason: "error", errorMessage });
  for (const message of [
    "OpenAI API error (403): insufficient_user_quota: available balance is 0",
    "429: No deployments available for selected model",
    "OpenAI API error (502): bad gateway",
    "provider returned HTTP 524 timeout",
    "401 unauthorized invalid API key",
    "invalid model name 'gpt-x'",
    "model not found: gpt-x",
    "model does not exist: gpt-x",
    "model unavailable; 503 upstream",
    "408 request timeout from provider",
    "stream ended before completion",
    "fetch failed: connection reset by peer during streaming transport",
    "stream_read_error",
    "service unavailable: model overloaded",
    "OpenAI API error (520)",
    "LiteLLMCompletionStreamingIterator missing completed_response adapter signature",
    "permission_error: concurrent request limit reached",
    "OpenAI API error (402): your 余额 is 不足; please top up",
    "OpenAI API error (400): invalid model id 'gpt-x'",
  ]) assert.equal(classifyModelUnavailable(assistantError(message)), true, message);
  for (const message of [
    assistantError("aborted by user"),
    assistantError("context length exceeded max tokens"),
    assistantError("content policy safety refusal"),
    assistantError("400 bad request schema validation failed"),
    assistantError("stream parser failed unexpectedly"),
    assistantError("OpenAI API error (409): conflict on resource"),
    { role: "tool", stopReason: "error", errorMessage: "OpenAI API error (502)" },
    { role: "tool", stopReason: "error", errorMessage: "stream_read_error" },
    { role: "assistant", stopReason: "stop", errorMessage: "OpenAI API error (502)" },
  ]) assert.equal(classifyModelUnavailable(message), false, JSON.stringify(message));
  assert.equal(classifyModelUnavailable(assistantError("400 bad request: insufficient_user_quota")), true, "strong quota phrase wins over ordinary 400 wording");
  assert.equal(classifyModelUnavailable(assistantError("OpenAI API error (409): too many concurrent requests")), true, "409 becomes positive only with rate/overload/capacity/concurrency phrasing");
  assert.equal(classifyModelUnavailable(assistantError("model field missing validation")), false, "schema 'missing' phrasing does not match model unavailable positives");
});

test("semanticAssistantOutput recognises text, ThinkingContent.thinking, and toolCall content shapes", () => {
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "text", text: "hello" }] }), false);
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "thinking", thinking: "reasoning" }] }), false);
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "toolCall", name: "bash" }] }), false);
  assert.equal(nextModelErrorState(true, "message_update", { role: "tool", content: [{ type: "text", text: "out" }] }), true, "tool-role delta does not clear");
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "text_start" }, { type: "text", text: "" }] }), true, "empty text start/end without payload keeps the latch");
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "thinking_start" }, { type: "thinking", thinking: "   " }] }), true, "whitespace-only thinking keeps the latch");
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "text" }, { type: "thinking" }] }), true, "type-only text/thinking without payload keeps the latch");
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", content: [{ type: "text_start" }, { type: "thinking_start" }, { type: "text", text: "ok" }] }), false, "later nonempty text clears after start markers");
});

test("model error state latches and clears only on semantic assistant/model success paths", () => {
  const unavailable = { role: "assistant", stopReason: "error", errorMessage: "429 No deployments available for selected model" };
  assert.equal(nextModelErrorState(false, "message_update", unavailable), true);
  assert.equal(nextModelErrorState(true, "agent_start"), true, "retry agent_start does not clear");
  assert.equal(nextModelErrorState(true, "agent_settled"), true);
  assert.equal(nextModelErrorState(true, "message_update", { role: "assistant", delta: { content: [{ type: "text", text: "ok" }] } }), false);
  assert.equal(nextModelErrorState(true, "message_end", { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "done" }] }), false);
  assert.equal(nextModelErrorState(true, "model_select"), false);
  assert.equal(nextModelErrorState(false, "message_end", { role: "assistant", stopReason: "error", errorMessage: "context length exceeded" }), false);
  assert.equal(nextModelErrorState(true, "message_end", { role: "assistant", stopReason: "error", errorMessage: "context length exceeded" }), true, "nonmatching error does not clear existing latch");
  assert.equal(nextModelErrorState(true, "message_end", { role: "assistant", stopReason: "aborted", errorMessage: "aborted" }), true);
});

test("auto-continue flag survives pi retry-exhaustion abort tail but not user abort or normal output", () => {
  const terminated = { role: "assistant", stopReason: "error", errorMessage: "terminated" };
  const retryAbort = (n) => ({ role: "assistant", stopReason: "aborted", errorMessage: `Aborted after ${n} retry attempt${n > 1 ? "s" : ""}` });
  const userAbort = { role: "assistant", stopReason: "aborted", errorMessage: "Operation aborted" };
  assert.equal(isRetryExhaustedAbort(retryAbort(1)), true);
  assert.equal(isRetryExhaustedAbort(retryAbort(5)), true);
  assert.equal(isRetryExhaustedAbort(userAbort), false);
  assert.equal(isRetryExhaustedAbort(terminated), false);
  assert.equal(isRetryExhaustedAbort({ role: "tool", stopReason: "aborted", errorMessage: "Aborted after 3 retry attempts" }), false);
  // 重现会话日志中的失败链：terminated →（pi 内部重试）→ "Aborted after N retry attempts" → settle
  let flag = false;
  flag = nextLastAssistantErrorMatched(flag, terminated);
  assert.equal(flag, true, "classified provider error arms the continue gate");
  flag = nextLastAssistantErrorMatched(flag, retryAbort(1));
  assert.equal(flag, true, "retry-exhaustion abort tail preserves the gate so agent_settled can send continue");
  assert.equal(nextLastAssistantErrorMatched(false, retryAbort(3)), false, "abort tail alone does not arm without a prior classified error");
  assert.equal(nextLastAssistantErrorMatched(true, userAbort), false, "user Esc abort clears the gate");
  assert.equal(nextLastAssistantErrorMatched(true, { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "ok" }] }), false, "normal assistant completion clears the gate");
  assert.equal(nextLastAssistantErrorMatched(true, { role: "assistant", stopReason: "error", errorMessage: "context length exceeded" }), false, "non-availability error clears the gate");
});

test("0.56 async-started ownership filters on root Pi session file identity", () => {
  const sessionFile = "/home/tester/.pi/agent/sessions/sanitized/session.jsonl", uuid = "01a03c1f-ae52-7704-9158-f7e095fb736d";
  assert.equal(sessionIdOf({ getSessionFile: () => sessionFile, getSessionId: () => uuid }), sessionFile, "installed pi-subagents 0.56 uses session file before UUID");
  assert.equal(sessionIdOf({ getSessionFile: () => "", getSessionId: () => uuid }), uuid);
  assert.deepEqual(childFromStartedPayload({ id: "r1", asyncDir: "/tmp/a", sessionId: sessionFile }, sessionFile), { id: "r1", asyncDir: "/tmp/a" });
  const routed = childFromStartedPayload({ id: "r1", asyncDir: "/tmp/a", sessionId: sessionFile, nestedRoute: { rootRunId: "r1", eventSink: "/tmp/sidecar/events.jsonl", controlInbox: "/tmp/sidecar/control.jsonl", capabilityToken: "cap" } }, sessionFile);
  assert.equal(routed?.nestedRoute?.registryPath, "/tmp/sidecar/registry.json");
  assert.equal(childFromStartedPayload({ id: "r1", asyncDir: "/tmp/a", sessionId: sessionFile, nestedRoute: { rootRunId: "other", eventSink: "/tmp/sidecar/events.jsonl", controlInbox: "/tmp/sidecar/control.jsonl", capabilityToken: "cap" } }, sessionFile)?.nestedRoute, undefined);
  assert.equal(childFromStartedPayload({ id: "r1", asyncDir: "/tmp/a", sessionId: uuid }, sessionFile), undefined, "UUID event is foreign when real status uses session file path");
  assert.equal(childFromStartedPayload({ id: "r1", asyncDir: "/tmp/a" }, sessionFile), undefined);
});

test("animator direct-execution predicate is nonthrowing and symlink execution is recognized", () => {
  const d = temp(), link = join(d, "animator.mjs"), missing = join(d, "missing.mjs"), target = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/animator.mjs");
  assert.equal(isDirectExecution(import.meta.url, missing), false, "nonexistent argv path is treated as import/non-direct without throwing");
  assert.equal(isDirectExecution(import.meta.url, "-"), false, "stdin argv marker is treated as import/non-direct without throwing");
  execFileSync("ln", ["-s", target, link]);
  const result = spawnSync(process.execPath, [link], { encoding: "utf8" });
  assert.equal(result.status, 2, "symlink invocation reaches canonical direct-execution guard and reports missing socket");
  assert.match(result.stderr, /missing socket/);
});

test("active-index restoration uses configured root and filters real session file identity", () => {
  const d = temp(), root = join(d, "async-subagent-runs"), active = join(root, ".active-runs"), sessionFile = "/home/tester/.pi/agent/sessions/sanitized/session.jsonl", uuid = "01a03c1f-ae52-7704-9158-f7e095fb736d";
  mkdirSync(active, { recursive: true });
  const route = { rootRunId: "same", eventSink: join(d, "sidecar", "events.jsonl"), controlInbox: join(d, "sidecar", "control.jsonl"), capabilityToken: "cap" };
  const malformedRoute = { rootRunId: "bad-route", eventSink: join(d, "sidecar", "bad-events.jsonl"), controlInbox: "", capabilityToken: "cap" };
  for (const [id, session, nestedRoute] of [["same", sessionFile, route], ["uuid-only", uuid, undefined], ["foreign", "/home/tester/.pi/agent/sessions/foreign/session.jsonl", undefined], ["bad-route", sessionFile, malformedRoute], ["done", sessionFile, undefined]]) {
    const dir = join(root, id);
    mkdirSync(dir);
    writeFileSync(join(dir, "status.json"), JSON.stringify({ runId: id, sessionId: session, state: id === "done" ? "complete" : "running", lastUpdate: 10, ...(nestedRoute ? { nestedRoute } : {}) }));
    writeFileSync(join(active, id), "");
  }
  assert.equal(defaultAsyncRoot({ PI_SUBAGENTS_TEMP_ROOT: d }), root);
  const restored = restoredChildren(root, sessionFile);
  assert.deepEqual(restored.map((x) => x.id).sort(), ["bad-route", "same"]);
  const same = restored.find((x) => x.id === "same");
  const bad = restored.find((x) => x.id === "bad-route");
  assert.equal(same?.nestedRoute?.registryPath, join(d, "sidecar", "registry.json"));
  assert.equal(bad?.nestedRoute, undefined, "foreign/malformed restored nested routes are ignored");
  mkdirSync(same.nestedRoute.registryDir, { recursive: true });
  writeFileSync(same.nestedRoute.registryPath, JSON.stringify({ rootRunId: "same", children: [{ id: "restored-live", state: "running", lastActivityAt: 10 }] }));
  const projection = readNestedRegistryProjection(same.nestedRoute).projection;
  const state = aggregateLogicalState({ mainActive: false, roots: new Set([same.id]), snapshots: new Map([[same.id, projection]]), attentionWatermarks: new Map() });
  assert.equal(state.active, true, "restored valid nestedRoute keeps ownership through terminal registry live descendant");
  assert.deepEqual([...state.activeIds], ["restored-live"]);
});

test("pi-subagents 0.56 snapshots project activity, attention recovery, and siblings", () => {
  const waiting = { id: "wait", kind: "subagent", label: "child", state: "running", updatedAt: 100, activity: { state: "needs_attention", lastActivityAt: 100 } };
  const executing = { id: "go", kind: "subagent", label: "child", state: "running", updatedAt: 101, activity: { state: "active_long_running", lastActivityAt: 101, currentToolStartedAt: 101 } };
  const snapshot = { kind: "pi-subagents.async-status-snapshot", version: 1, generatedAt: 102, caps: {}, omitted: {}, runs: [{ id: "root", kind: "workflow", label: "wf", state: "running", children: [waiting, executing] }] };
  let p = projectAsyncStatus(snapshot, new Map([["wait", 100]]));
  assert.deepEqual([...p.activeIds], ["go"]);
  assert.equal(aggregateLogicalState({ mainActive: false, roots: new Set(["root"]), snapshots: new Map([["root", snapshot]]), attentionWatermarks: new Map([["wait", 100]]) }).active, true);
  waiting.activity.lastActivityAt = 102;
  waiting.updatedAt = 102;
  p = projectAsyncStatus(snapshot, new Map([["wait", 100]]));
  assert.ok(p.activeIds.has("wait"), "same node activity after attention recovers");
  executing.state = "complete";
  waiting.state = "paused";
  assert.equal(projectAsyncStatus(snapshot).activeIds.size, 0);
});

test("attention watermark seeding supports post-reload idle then stale-marker recovery", () => {
  const raw = { runId: "root", state: "running", steps: [{ runId: "wait", status: "running", activityState: "needs_attention", lastActivityAt: 100, currentToolStartedAt: 95, currentTool: "bash" }] };
  const watermarks = new Map();
  seedAttentionWatermarksFromSnapshot(raw, watermarks);
  assert.equal(watermarks.get("wait"), 100);
  assert.deepEqual([...projectAsyncStatus(raw, watermarks).activeIds], [], "first post-reload needs_attention snapshot seeds and stays idle");
  const advanced = { runId: "root", state: "running", steps: [{ runId: "wait", status: "running", activityState: "needs_attention", lastActivityAt: 101, currentToolStartedAt: 101, currentTool: "bash" }] };
  seedAttentionWatermarksFromSnapshot(advanced, watermarks);
  assert.equal(watermarks.get("wait"), 100, "existing explicit/seeded watermark is not overwritten on monitor tick");
  assert.deepEqual([...projectAsyncStatus(advanced, watermarks).activeIds], ["wait"], "advanced stale marker recovers using the same map");
  const explicit = new Map([["wait", 99]]);
  seedAttentionWatermarksFromSnapshot(raw, explicit);
  assert.equal(explicit.get("wait"), 99, "explicit control watermark is never overwritten");
});

test("legacy real status projects steps, terminal values, and malformed conservation", () => {
  const waiting = { runId: "wait", status: "running", lastActivityAt: 100, activityState: "needs_attention" };
  const executing = { runId: "go", status: "running", lastActivityAt: 101, currentToolStartedAt: 101 };
  const s = real("root", 99, "running", [waiting, executing]);
  assert.deepEqual([...projectAsyncStatus(s, new Map([["wait", 100]])).activeIds], ["go"]);
  waiting.lastActivityAt = 102;
  assert.ok(projectAsyncStatus(s, new Map([["wait", 100]])).activeIds.has("wait"));
  assert.equal(projectAsyncStatus({ runId: "x", state: "running", lastUpdate: 1 }).malformed, false);
  assert.equal(projectAsyncStatus({ bad: true }).malformed, true);
  assert.equal(aggregateLogicalState({ mainActive: false, roots: new Set(["unknown"]), snapshots: new Map(), attentionWatermarks: new Map() }).active, true, "unknown child state remains conservatively active until complete");
  assert.equal(aggregateLogicalState({ mainActive: true, roots: new Set(), snapshots: new Map(), attentionWatermarks: new Map() }).active, true);
  assert.equal(aggregateLogicalState({ mainActive: false, roots: new Set(), snapshots: new Map(), attentionWatermarks: new Map() }).active, false);
});

test("terminal root with unresolved nested route stays conservatively active", () => {
  const d = temp(), route = validateNestedRoute({ rootRunId: "root", eventSink: join(d, "events", "events.jsonl"), controlInbox: join(d, "events", "control.jsonl"), capabilityToken: "cap" }, "root");
  assert.ok(route);
  const child = { id: "root", asyncDir: join(d, "root"), nestedRoute: route, rootTerminal: true };
  assert.equal(mergedChildSnapshot(child, { runId: "root", state: "complete" }), undefined);
  const state = aggregateLogicalState({ mainActive: false, roots: new Set(["root"]), snapshots: new Map([["root", mergedChildSnapshot(child, { runId: "root", state: "complete" })]]), attentionWatermarks: new Map() });
  assert.equal(state.active, true);
  assert.deepEqual([...state.activeRoots], ["root"]);
});

test("completion nested seed keeps terminal root active and exposes descendant ids", () => {
  const projection = nestedProjectionFromChildren("root", [{ id: "child-live", state: "running", children: [{ id: "grand-terminal", state: "complete" }] }]);
  const child = { id: "root", rootTerminal: true, nestedProjection: projection };
  const state = aggregateLogicalState({ mainActive: false, roots: new Set(["root"]), snapshots: new Map([["root", mergedChildSnapshot(child, { runId: "root", state: "complete" })]]), attentionWatermarks: new Map() });
  assert.equal(state.active, true);
  assert.deepEqual([...state.activeRoots], ["root"]);
  assert.deepEqual([...state.activeIds], ["child-live"]);
});

test("nested registry projection is read-only, recursive, and failure-closed", () => {
  const d = temp(), route = validateNestedRoute({ rootRunId: "root", eventSink: join(d, "sidecar", "events.jsonl"), controlInbox: join(d, "sidecar", "control.jsonl"), capabilityToken: "cap" }, "root");
  assert.ok(route);
  mkdirSync(route.registryDir, { recursive: true });
  assert.equal(readNestedRegistryProjection(route).resolved, false, "missing registry is active/unresolved");
  writeFileSync(route.registryPath, "not json");
  assert.equal(readNestedRegistryProjection(route).resolved, false, "malformed registry is active/unresolved");
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "foreign", children: [] }));
  assert.equal(readNestedRegistryProjection(route).resolved, false, "mismatched root is active/unresolved");
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "child", state: "running", childStatus: "complete" }] }));
  let result = readNestedRegistryProjection(route);
  assert.equal(result.resolved, true);
  assert.equal(result.live, true);
  assert.deepEqual([...projectAsyncStatus(result.projection).activeIds], ["child"], "registry state, not child-status side files/fields, determines liveness");
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "attention", state: "running", activityState: "needs_attention", lastActivityAt: 100, currentToolStartedAt: 90, currentTool: "bash" }] }));
  result = readNestedRegistryProjection(route);
  assert.equal(result.live, true);
  const registryWatermarks = new Map();
  seedAttentionWatermarksFromSnapshot(result.projection, registryWatermarks);
  assert.equal(registryWatermarks.get("attention"), 100);
  assert.deepEqual([...projectAsyncStatus(result.projection, registryWatermarks).activeIds], [], "registry needs_attention seeds and is idle after reload");
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "attention", state: "running", activityState: "needs_attention", lastActivityAt: 101, currentToolStartedAt: 101, currentTool: "bash" }] }));
  result = readNestedRegistryProjection(route);
  seedAttentionWatermarksFromSnapshot(result.projection, registryWatermarks);
  assert.equal(registryWatermarks.get("attention"), 100);
  assert.deepEqual([...projectAsyncStatus(result.projection, registryWatermarks).activeIds], ["attention"], "later activity beyond watermark recovers");
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "activity-object", state: "running", activity: { state: "needs_attention", lastActivityAt: 200, currentToolStartedAt: 199, currentTool: "read" } }] }));
  result = readNestedRegistryProjection(route);
  assert.deepEqual([...projectAsyncStatus(result.projection).activeIds], [], "normalized activity.state is preserved for idle classification");
  assert.deepEqual([...projectAsyncStatus(result.projection, new Map([["activity-object", 199]])).activeIds], ["activity-object"]);
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "late", state: "queued" }] }));
  result = readNestedRegistryProjection(route);
  assert.equal(result.live, true, "late descendant appears and keeps root active");
  assert.deepEqual([...projectAsyncStatus(result.projection).activeIds], ["late"]);
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "a", state: "complete", children: [{ id: "b", state: "failed" }], steps: [{ children: [{ id: "c", state: "paused" }, { id: "d", state: "stopped" }] }] }] }));
  result = readNestedRegistryProjection(route);
  assert.equal(result.resolved, true);
  assert.equal(result.live, false, "all recursive child and step-child terminal clears");
  assert.equal(projectAsyncStatus(result.projection).activeIds.size, 0);
});

test("terminal root without nested route or live seed removes normally by projection", () => {
  assert.equal(nestedProjectionFromChildren("root", [{ id: "done", state: "complete" }]), undefined);
  const child = { id: "root", rootTerminal: true };
  const snapshot = mergedChildSnapshot(child, { runId: "root", state: "complete" });
  assert.equal(projectAsyncStatus(snapshot).activeIds.size, 0);
  assert.equal(aggregateLogicalState({ mainActive: false, roots: new Set(["root"]), snapshots: new Map([["root", snapshot]]), attentionWatermarks: new Map() }).active, false);
});

test("repair monitor condition remains while tracked attention-idle children have no active lease", () => {
  assert.equal(monitorNeeded(false, 0), false);
  assert.equal(monitorNeeded(true, 0), true);
  assert.equal(monitorNeeded(false, 1), true);
  const projection = nestedProjectionFromChildren("root", [{ id: "attention", state: "running", activityState: "needs_attention", lastActivityAt: 100 }]);
  const state = aggregateLogicalState({ mainActive: false, roots: new Set(["root"]), snapshots: new Map([["root", projection]]), attentionWatermarks: new Map() });
  assert.equal(state.active, false, "attention-idle child does not make the window breathe");
  assert.equal(monitorNeeded(false, 1), true, "tracked idle child still keeps repair monitor alive");
});

test("lease heartbeat diagnostics are sorted and contain no history-bearing fields", () => {
  const runtime = temp(), i = ident(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, file = leasePath(i, "owner", env);
  publishLease(i, "owner", true, 1, env, { parentSessionId: "session-z", activeRunIds: ["b", "a", "a"], activeNodeIds: ["n2", "n1"] });
  const lease = JSON.parse(readFileSync(file, "utf8"));
  assert.deepEqual(lease.activeRunIds, ["a", "b"]);
  assert.deepEqual(lease.activeNodeIds, ["n1", "n2"]);
  assert.equal(lease.parentSessionId, "session-z");
  for (const forbidden of ["prompt", "history", "cwd", "name", "windowName"]) assert.equal(Object.hasOwn(lease, forbidden), false);
});

test("invalid lease residue is removed while legacy missing-state leases stay active", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, i = ident(), dir = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"), "leases");
  mkdirSync(dir, { recursive: true });
  const bad = leasePath(i, "garbage", env);
  writeFileSync(bad, "not json");
  const corruptSchema = leasePath(i, "schema", env);
  writeFileSync(corruptSchema, JSON.stringify({ bogus: true }));
  const legacy = leasePath(i, "legacy-missing-state", env);
  writeFileSync(legacy, JSON.stringify({ version: 1, ownerId: "legacy-missing-state", socketPath: i.socketPath, windowId: i.windowId, paneId: i.paneId, heartbeatAt: 1 }));
  assert.equal(readLease(bad, 2), undefined);
  assert.equal(readLease(corruptSchema, 2), undefined);
  assert.ok(!existsSync(bad));
  assert.ok(!existsSync(corruptSchema));
  assert.equal(readLease(legacy, 2)?.state, "active");
});

test("lease heartbeat remains fresh beyond two TTL windows then clears immediately", () => {
  const runtime = temp(), i = ident(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, file = leasePath(i, "owner", env);
  for (let t = 0; t <= LEASE_TTL_MS * 3; t += HEARTBEAT_MS) {
    publishLease(i, "owner", true, t, env);
    assert.ok(readLease(file, t + HEARTBEAT_MS - 1));
  }
  publishLease(i, "owner", false, LEASE_TTL_MS * 3 + 1, env);
  assert.equal(readLease(file, LEASE_TTL_MS * 3 + 2), undefined);
});

test("multiple owners aggregate active/error leases with backcompat and expiry", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, i = ident();
  publishLease(i, "legacy-active", true, 1, env);
  const legacyPath = leasePath(i, "legacy-missing-state", env);
  writeFileSync(legacyPath, JSON.stringify({ version: 1, ownerId: "legacy-missing-state", socketPath: i.socketPath, windowId: "@10", paneId: "%11", heartbeatAt: 1 }));
  publishLease({ ...i, paneId: "%10" }, "error", "error", 1, env, { activeRunIds: ["r"], activeNodeIds: ["n"] });
  const root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  const states = windowLeaseStates(root, 2);
  assert.deepEqual([...states.error], ["@8"], "error wins over active owners in the same window");
  assert.deepEqual([...states.active], ["@10"], "legacy lease without state remains active for rolling compatibility");
  assert.deepEqual([...states.errorPanes], ["%10"], "pane routing ownership comes only from live error leases");
  assert.deepEqual([...activeWindows(root, 2)], ["@10"]);
  const lease = readLease(leasePath(i, "error", env), 2);
  assert.equal(lease.state, "error");
  assert.equal(JSON.stringify(lease).includes("No deployments"), false, "raw provider text is not stored in error lease");
  publishLease({ ...i, windowId: "@9" }, "active", "active", 2, env);
  const mixed = leaseWindowStates(root, 3);
  assert.deepEqual([...mixed.error], ["@8"]);
  assert.deepEqual([...mixed.active], ["@9", "@10"]);
  assert.deepEqual([...mixed.errorPanes], ["%10"]);
  const expired = windowLeaseStates(root, LEASE_TTL_MS + 2);
  assert.equal(expired.error.size, 0);
  assert.equal(expired.errorPanes.size, 0, "expired publisher cannot retain pane authorization");
});

test("animator respawn guard treats signal-killed child as exited and spawns a replacement", () => {
  const running = { exitCode: null, signalCode: null };
  const killed = { exitCode: null, signalCode: "SIGKILL" };
  assert.equal(childStillRunning(running), true);
  assert.equal(childStillRunning(killed), false, "signal-killed child is not running");
  assert.equal(childStillRunning({ exitCode: 0, signalCode: null }), false, "exited child is not running");
  assert.equal(childStillRunning({ exitCode: 1, signalCode: null }), false, "non-zero exit child is not running");
  assert.equal(childStillRunning(null), false);
  assert.equal(childStillRunning(undefined), false);
  assert.equal(animatorSpawnNeeded(running, true), false, "live child remains singleton");
  assert.equal(animatorSpawnNeeded(killed, true), true, "signal-killed animator is replaced while a lease is desired");
  assert.equal(animatorSpawnNeeded(killed, false), false, "idle state does not spawn a replacement");
  assert.equal(animatorSpawnNeeded(killed, false, true), true, "forced cleanup reconciliation may replace a killed animator");
});

test("nested registry projection treats rejected descendant as terminal and clears ownership", () => {
  const d = temp(), route = validateNestedRoute({ rootRunId: "root", eventSink: join(d, "sidecar", "events.jsonl"), controlInbox: join(d, "sidecar", "control.jsonl"), capabilityToken: "cap" }, "root");
  assert.ok(route);
  mkdirSync(route.registryDir, { recursive: true });
  writeFileSync(route.registryPath, JSON.stringify({ rootRunId: "root", children: [{ id: "denied", state: "rejected", lastActivityAt: 1 }] }));
  const result = readNestedRegistryProjection(route);
  assert.equal(result.resolved, true);
  assert.equal(result.live, false, "rejected descendant does not retain ownership");
  const state = aggregateLogicalState({ mainActive: false, roots: new Set(["root"]), snapshots: new Map([["root", result.projection]]), attentionWatermarks: new Map() });
  assert.equal(state.active, false, "rejected-only registry closes the root");
});

test("process-owned animator lock uses atomic hard-link, cleans up temp, and rejects concurrent claims", () => {
  const d = temp(), lock = join(d, "animator.lock");
  const held = acquireAnimatorLock(lock, "live", process.pid);
  assert.ok(held.owner);
  const live = JSON.parse(readFileSync(lock));
  assert.equal(live.token, "live", "real lock file is complete JSON");
  assert.equal(live.pid, process.pid, "lock records the claiming pid; using the test process pid keeps the liveness check deterministic (an arbitrary pid like 111 may not exist on the host)");
  const dirEntries = readdirSync(d);
  assert.equal(dirEntries.includes("animator.lock"), true, "lock file is published");
  assert.equal(dirEntries.some((entry) => entry.endsWith(".tmp")), false, "no temp hard-link is left in the directory after successful claim");
  assert.equal(acquireAnimatorLock(lock, "other", 222).owner, false, "second claim against an existing live lock is rejected");
  releaseAnimatorLock(lock, "live");
  assert.equal(existsSync(lock), false, "release removes lock file");
  writeFileSync(lock, "");
  const emptyHeld = acquireAnimatorLock(lock, "empty", 333);
  assert.ok(emptyHeld.owner, "empty real lock is reclaimed");
  assert.equal(readdirSync(d).some((entry) => entry.endsWith(".tmp")), false, "no temp hard-link left after reclaiming empty lock");
  const reclaimed = JSON.parse(readFileSync(lock));
  assert.equal(reclaimed.token, "empty");
  assert.equal(reclaimed.pid, 333);
  releaseAnimatorLock(lock, "empty");
  writeFileSync(lock, "not json");
  const corruptHeld = acquireAnimatorLock(lock, "corrupt", 444);
  assert.ok(corruptHeld.owner, "unparseable real lock is reclaimed");
  assert.equal(readdirSync(d).some((entry) => entry.endsWith(".tmp")), false, "no temp hard-link left after reclaiming corrupt lock");
  const replaced = JSON.parse(readFileSync(lock));
  assert.equal(replaced.token, "corrupt");
  assert.equal(replaced.pid, 444);
  releaseAnimatorLock(lock, "corrupt");
  const eexDir = temp();
  const eexLock = join(eexDir, "race.lock");
  let linkCalls = 0;
  const racingLink = () => { linkCalls++; throw Object.assign(new Error("link refused"), { code: "EEXIST" }); };
  const eexAttempt = acquireAnimatorLock(eexLock, "racer", 999, undefined, racingLink, () => {}, () => {}, () => false);
  assert.equal(eexAttempt.owner, false, "atomic EEXIST returns owner false without publishing partial lock");
  assert.ok(linkCalls >= 1, "atomic EEXIST path invokes link at least once");
  assert.equal(existsSync(eexLock), false, "no partial lock file is published when link fails");
  const winner = acquireAnimatorLock(eexLock, "winner", 555);
  assert.ok(winner.owner, "real subsequent claim wins after the racing EEXIST injection");
  assert.equal(readdirSync(eexDir).some((entry) => entry.endsWith(".tmp")), false, "no temp hard-link left after successful real claim");
  releaseAnimatorLock(eexLock, "winner");
});

test("urgent animator request targets the validated lock owner with safe SIGWINCH", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, i = ident("/tmp/urgent-socket"), root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  mkdirSync(root, { recursive: true });
  writeFileSync(join(root, "animator.lock"), JSON.stringify({ pid: 4321, token: "owner" }));
  const calls = [];
  assert.equal(requestAnimatorTick(i, env, (pid, signal) => { calls.push({ pid, signal }); }), true);
  assert.deepEqual(calls, [{ pid: 4321, signal: ANIMATOR_URGENT_SIGNAL }]);
  writeFileSync(join(root, "animator.lock"), JSON.stringify({ pid: 0, token: "bad" }));
  assert.equal(requestAnimatorTick(i, env, () => { throw new Error("must not signal"); }), false);
  assert.equal(ANIMATOR_URGENT_SIGNAL, "SIGWINCH", "older animators ignore the urgent signal instead of being terminated");
});

test("animator gives up after consecutive failed frames and releases the lock", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, i = ident("/tmp/socket"), root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  publishLease(i, "a", "active", 100, env);
  const exec = () => { const e = new Error("no server running"); e.status = 1; throw e; };
  const delays = [];
  const a = runAnimator({ socketPath: i.socketPath, root, now: () => 0, wallNow: () => 100, intervalMs: FRAME_MS, errorIntervalMs: ERROR_MS, exec, schedule: (_fn, delay) => { delays.push(delay); return { unref() {} }; }, cancel: () => {}, subscribeUrgent: inertUrgent });
  assert.ok(a.started);
  assert.equal(acquireAnimatorLock(join(root, "animator.lock")).owner, false, "lock is held while retrying");
  assert.equal(delays.at(-1), ERROR_MS, "failed frame retries at the slow reconciliation cadence");
  for (let n = 1; n < MAX_CONSECUTIVE_FAILURES; n++) a.tick();
  assert.equal(existsSync(join(root, "animator.lock")), false, "lock file is removed after MAX_CONSECUTIVE_FAILURES failed frames");
  const recovered = runAnimator({ socketPath: i.socketPath, root, now: () => 0, wallNow: () => 100, intervalMs: FRAME_MS, errorIntervalMs: ERROR_MS, exec, schedule: (_fn, delay) => { delays.push(delay); return { unref() {} }; }, cancel: () => {}, subscribeUrgent: inertUrgent });
  assert.ok(recovered.started, "a fresh animator can acquire the lock and retry right away");
});

test("animator counts only consecutive failures: one success resets the give-up counter", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, i = ident("/tmp/socket"), root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  publishLease(i, "a", "active", 100, env);
  let failing = true;
  const exec = () => { if (failing) { const e = new Error("no server running"); e.status = 1; throw e; } return ""; };
  const a = runAnimator({ socketPath: i.socketPath, root, now: () => 0, wallNow: () => 100, intervalMs: FRAME_MS, errorIntervalMs: ERROR_MS, exec, schedule: () => ({ unref() {} }), cancel: () => {}, subscribeUrgent: inertUrgent });
  assert.ok(a.started);
  failing = false; a.tick();
  failing = true;
  for (let n = 0; n < MAX_CONSECUTIVE_FAILURES - 1; n++) a.tick();
  assert.equal(existsSync(join(root, "animator.lock")), true, "a success between failures resets the counter");
  a.tick();
  assert.equal(existsSync(join(root, "animator.lock")), false, "the give-up threshold applies to consecutive failures only");
});

test("animator batches active/error transitions, cadences, resets, and cached clients", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, i = ident("/tmp/socket"), root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  publishLease(i, "a", "active", 100, env);
  const calls = [], delays = [];
  let mono = 0, wall = 100, listCount = 0, urgentHandler, urgentUnsubscribed = 0;
  const exec = (_tmux, args) => { calls.push(args); if (args.includes("list-clients")) { listCount++; if (listCount === 2) { const e = new Error("stale"); e.status = 1; throw e; } return "c1\nc2\n"; } if (args.includes("list-windows")) return "@8\n@9\n"; return ""; };
  const a = runAnimator({ socketPath: i.socketPath, root, now: () => mono, wallNow: () => wall, intervalMs: FRAME_MS, errorIntervalMs: ERROR_MS, exec, schedule: (_fn, delay) => { delays.push(delay); return { unref() {} }; }, cancel: () => {}, subscribeUrgent: (handler) => { urgentHandler = handler; return () => { urgentUnsubscribed += 1; }; } });
  assert.ok(a.started);
  let frameBatches = calls.filter((args) => args.includes("set-option"));
  assert.ok(frameBatches.at(-1).includes(FRAMES[0]));
  assert.ok(frameBatches.at(-1).includes("refresh-client"));
  assert.equal(delays.at(-1), FRAME_MS);
  assert.equal(listCount, 1);
  mono = 60 * 5 + 3; wall = 500; a.tick();
  assert.equal(listCount, 1, "client list is cached for roughly one second");
  assert.ok(calls.at(-1).includes(FRAMES[5]));
  publishLease(i, "a", "error", 600, env);
  wall = 600; a.tick();
  assert.equal(delays.at(-1), ERROR_MS, "error-only uses slow reconciliation cadence");
  assert.ok(calls.at(-1).includes("@quick_deploy_pi_error"));
  assert.ok(calls.at(-1).includes(RECOVERY_ARMED_OPTION));
  assert.ok(calls.at(-1).includes("-pq"), "error frame atomically publishes pane ownership");
  assert.ok(calls.at(-1).includes("1"));
  publishLease({ ...i, windowId: "@9" }, "b", "active", 700, env);
  wall = 700; a.tick();
  assert.equal(delays.at(-1), FRAME_MS, "mixed active+error uses active frame cadence");
  assert.ok(calls.at(-1).includes("@quick_deploy_pi_error"));
  assert.ok(calls.at(-1).includes("@quick_deploy_pi_bg"));
  wall = 1200; a.tick();
  assert.equal(listCount, 2, "stale zero-client refresh is tolerated for one update");
  publishLease(i, "a", false, 1300, env);
  publishLease({ ...i, windowId: "@9" }, "b", false, 1300, env);
  wall = 1300; urgentHandler();
  assert.ok(calls.some((args) => args.includes("-uw") && args.includes("@quick_deploy_pi_error")));
  assert.ok(calls.some((args) => args.includes("-upq") && args.includes(RECOVERY_ARMED_OPTION)), "same animator clears stale pane ownership");
  assert.equal(urgentUnsubscribed, 1, "stop removes the urgent signal listener");
  assert.equal(acquireAnimatorLock(join(root, "animator.lock")).owner, true);
});

test("partial tmux batch failure retains attempted window and pane cleanup responsibility", async () => {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock"), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };
  const tmuxEnv = withoutTmuxEnvironment({ ...process.env, ...env });
  const tmux = (args, options = {}) => execFileSync("tmux", args, { env: tmuxEnv, ...options });
  tmux(["-S", socket, "-f", "/dev/null", "new-session", "-d", "-s", "partial-batch"]);
  const paneB = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const windowB = tmux(["-S", socket, "display-message", "-p", "#{window_id}"], { encoding: "utf8" }).trim();
  const [paneA, windowA] = tmux(["-S", socket, "new-window", "-dP", "-F", "#{pane_id}|#{window_id}", "-t", "partial-batch:"], { encoding: "utf8" }).trim().split("|");
  const first = { socketPath: socket, windowId: windowA, paneId: paneA }, second = { socketPath: socket, windowId: windowB, paneId: paneB };
  let wall = Date.now(), animator, client;
  publishLease(second, "root-b", "error", wall, env);
  const marker = (pane) => tmux(["-S", socket, "show-options", "-pqv", "-t", pane, RECOVERY_ARMED_OPTION], { encoding: "utf8" }).trim();
  const red = (window) => tmux(["-S", socket, "show-options", "-wqv", "-t", window, "@quick_deploy_pi_error"], { encoding: "utf8" }).trim();
  try {
    client = spawn("tmux", ["-C", "-S", socket, "attach-session", "-t", "partial-batch"], { env: tmuxEnv, stdio: ["pipe", "ignore", "ignore"] });
    await waitFor(() => { try { return tmux(["-S", socket, "list-clients", "-F", "#{client_name}"], { encoding: "utf8" }).trim() ? "attached" : ""; } catch { return ""; } }, "attached");
    const root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(socket).toString("base64url"));
    animator = runAnimator({ socketPath: socket, root, wallNow: () => wall, schedule: () => ({ unref() {} }), cancel: () => {}, subscribeUrgent: inertUrgent });
    assert.equal(animator.started, true);
    assert.equal(marker(paneB), "1");

    publishLease(first, "root-a", "error", wall, env);
    const clientExited = new Promise((resolve) => client.once("exit", resolve));
    client.kill("SIGTERM");
    await clientExited;
    const originalError = console.error; console.error = () => {};
    try { animator.tick(); } finally { console.error = originalError; }
    assert.equal(marker(paneA), "1", "marker command applied before trailing stale-client refresh failed");
    assert.equal(red(windowA), "1", "new window target was also partially applied");
    assert.equal(marker(paneB), "1");

    publishLease(first, "root-a", false, wall, env);
    wall += 1_100;
    animator.tick();
    assert.equal(marker(paneA), "", "successful retry clears attempted A pane after its lease removal");
    assert.equal(red(windowA), "", "successful retry clears attempted A window");
    assert.equal(marker(paneB), "1", "live B ownership survives cleanup");
    assert.equal(red(windowB), "1");
    animator.tick();
    assert.equal(marker(paneA), "", "repeated reconciliation cannot strand A");
    animator.stop();
    assert.equal(marker(paneA), "");
    assert.equal(marker(paneB), "", "stop clears the remaining pane responsibility set");
    assert.equal(red(windowA), "");
    assert.equal(red(windowB), "", "stop clears the remaining window responsibility set");
  } finally {
    if (client?.exitCode === null) client.kill("SIGKILL");
    animator?.stop?.();
    publishLease(first, "root-a", false, Date.now(), env);
    publishLease(second, "root-b", false, Date.now(), env);
    try { tmux(["-S", socket, "kill-server"]); } catch {}
  }
});

test("auto-continue classifier additions cover terminated, weekly quota, mixed and cloudflare errors", () => {
  const assistantError = (errorMessage) => ({ role: "assistant", stopReason: "error", errorMessage });
  for (const message of [
    "Error: terminated",
    "terminated",
    "{\"error\":{\"type\":\"new_api_error\",\"message\":\"用户额度不足：模型 claude-opus-5\"}} ... {\"error\":{\"type\":\"permission_error\",\"message\":\"You've reached your weekly (7-day) usage limit. Your quota will reset when the current 7-day window ends. To continue now, purchase extra usage or upgrade your plan\"}}",
    "OpenAI API error (403): 用户额度不足：模型 gpt-5.6-sol ... Error doing the fallback: litellm.BadRequestError: {\"error\":{\"message\":\"An assistant message with 'tool_calls' must be followed by tool messages\",\"type\":\"invalid_request_error\"}} LiteLLM Retried: 3 times",
    "Error: 502 {\"status\":502,\"error_code\":502,\"retryable\":true,\"retry_after\":60,\"cloudflare_error\":true}",
  ]) assert.equal(classifyModelUnavailable(assistantError(message)), true, message);
  assert.equal(classifyModelUnavailable(assistantError("the task terminated normally")), false);
});

test("sentinel cancellation invalidates delayed events from one run while a distinct run auto-continues", async () => {
  const h = await statusHarness("delayed-cancel");
  const firstRun = new AbortController(), secondRun = new AbortController(), thirdRun = new AbortController();
  const errMsg = { role: "assistant", stopReason: "error", errorMessage: "stream_read_error" };
  try {
    h.setSignal(firstRun.signal);
    h.fire("agent_start", {}, h.ctx);
    h.fire("message_update", { message: errMsg }, h.ctx);
    await waitFor(h.paneArmed, "1");
    await waitFor(h.errorOption, "1");

    let releaseDelayed;
    const delayedGate = new Promise((resolve) => { releaseDelayed = resolve; });
    const delayedDispatch = (async () => {
      await delayedGate;
      h.handlers.message_update[0]({ message: errMsg }, h.ctx);
      return h.handlers.message_end[0]({ message: errMsg }, h.ctx);
    })();

    assert.deepEqual(h.terminalInput(CANCEL_SENTINEL), { consume: true }, "only the private sentinel is consumed");
    firstRun.abort();
    await waitFor(h.paneArmed, "");
    releaseDelayed();
    await delayedDispatch;
    h.fire("agent_settled", {}, h.ctx);
    await new Promise((resolve) => setTimeout(resolve, 350));
    assert.deepEqual(h.sent, [], "same-signal delayed message_end cannot re-arm or send");
    assert.equal(h.paneArmed(), "");
    await waitFor(h.errorOption, "");

    h.setSignal(secondRun.signal);
    h.fire("agent_start", {}, h.ctx);
    h.fire("message_end", { message: errMsg }, h.ctx);
    await waitFor(h.paneArmed, "1");
    h.fire("agent_settled", {}, h.ctx);
    await new Promise((resolve) => setTimeout(resolve, 350));
    assert.deepEqual(h.sent, ["continue"], "distinct uncancelled run follows normal auto-continue");
    await waitFor(h.errorOption, "1");

    assert.deepEqual(h.terminalInput(CANCEL_SENTINEL), { consume: true });
    h.setSignal(thirdRun.signal);
    h.fire("agent_start", {}, h.ctx);
    h.fire("message_end", { message: errMsg }, h.ctx);
    h.fire("agent_settled", {}, h.ctx);
    await new Promise((resolve) => setTimeout(resolve, 350));
    assert.deepEqual(h.sent, ["continue"], "Esc does not reset the existing 30 s send throttle");
  } finally {
    await h.cleanup();
  }
});

test("input and agent_start cancel pending continue while success clears error ownership", async () => {
  const h = await statusHarness("existing-cancellations");
  const run1 = new AbortController(), run2 = new AbortController(), run3 = new AbortController();
  const errMsg = { role: "assistant", stopReason: "error", errorMessage: "stream_read_error" };
  try {
    h.setSignal(run1.signal);
    h.fire("agent_start", {}, h.ctx);
    h.fire("message_end", { message: errMsg }, h.ctx);
    h.fire("agent_settled", {}, h.ctx);
    h.fire("input", { text: "user typed" }, h.ctx);
    await new Promise((resolve) => setTimeout(resolve, 350));
    assert.deepEqual(h.sent, [], "submitted input cancels the debounce before any send");
    assert.equal(h.paneArmed(), "1", "ordinary input cancellation preserves the existing red latch");

    h.setSignal(run2.signal);
    h.fire("agent_start", {}, h.ctx);
    h.fire("message_end", { message: errMsg }, h.ctx);
    h.fire("agent_settled", {}, h.ctx);
    h.setSignal(run3.signal);
    h.fire("agent_start", {}, h.ctx);
    h.fire("message_end", { message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "ok" }] } }, h.ctx);
    h.fire("agent_settled", {}, h.ctx);
    await new Promise((resolve) => setTimeout(resolve, 350));
    assert.deepEqual(h.sent, [], "agent_start cancels the old timer even after the new run succeeds and settles before its deadline");
    assert.equal(h.paneArmed(), "", "new-run success clears pane recovery ownership");
    await waitFor(h.errorOption, "");
  } finally {
    await h.cleanup();
  }
});

test("native search prompt lifetime cancels only on armed close and preserves unarmed behavior", async () => {
  const npmRoot = execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
  const stdinBufferUrl = pathToFileURL(join(npmRoot, "@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-tui/dist/stdin-buffer.js")).href;
  const extensionUrl = pathToFileURL(join(ROOT, "pi-agent/extensions/pi-tmux-window-status/index.ts")).href;
  const cases = [
    { label: "vi-forward", mode: "vi", keyHex: "2f", scenario: "armed-close" },
    { label: "vi-backward", mode: "vi", keyHex: "3f", scenario: "armed-close" },
    { label: "emacs-forward", mode: "emacs", keyHex: "13", scenario: "armed-close" },
    { label: "emacs-backward", mode: "emacs", keyHex: "12", scenario: "armed-close" },
    { label: "emacs-forward-enter", mode: "emacs", keyHex: "13", scenario: "armed-enter" },
    { label: "emacs-backward-enter", mode: "emacs", keyHex: "12", scenario: "armed-enter" },
    { label: "emacs-forward-no-close", mode: "emacs", keyHex: "13", scenario: "no-close" },
    { label: "emacs-backward-no-close", mode: "emacs", keyHex: "12", scenario: "no-close" },
    { label: "emacs-forward-arm-open", mode: "emacs", keyHex: "13", scenario: "arm-while-open" },
    { label: "emacs-backward-arm-open", mode: "emacs", keyHex: "12", scenario: "arm-while-open" },
  ];
  for (const item of cases) {
    const runtime = temp(), work = temp(), socket = join(work, "server.sock"), resultPath = join(work, "result.json");
    const fixture = join(work, "extension-fixture.mjs"), injector = join(work, "prompt-inject.py");
    writeFileSync(fixture, `import extension from ${JSON.stringify(extensionUrl)};\nimport { StdinBuffer } from ${JSON.stringify(stdinBufferUrl)};\nimport { execFileSync } from "node:child_process";\nimport { writeFileSync } from "node:fs";\nconst resultPath = process.argv[2];\nconst handlers = {}, sent = [], unarmedInputs = []; let terminalInput; const run = new AbortController();\nconst ctx = { mode: "tui", ui: { onTerminalInput(fn) { terminalInput = fn; return () => {}; } }, get signal() { return run.signal; }, isIdle: () => true, hasPendingMessages: () => false, sessionManager: { getSessionFile: () => "/tmp/search-prompt-session.jsonl" } };\nconst pi = { on(name, fn) { (handlers[name] ||= []).push(fn); }, events: { on: () => () => {} }, sendUserMessage(value) { sent.push(value); } };\nconst fire = (name, ...args) => { for (const fn of handlers[name] || []) fn(...args); };\nextension(pi); fire("session_start", {}, ctx);\nlet armed = false; const buffer = new StdinBuffer({ escapeTimeout: 1 });\nconst report = () => { const state = execFileSync("tmux", ["display-message", "-p", "-t", process.env.TMUX_PANE, "#{@quick_deploy_pi_recovery_armed}|#{@quick_deploy_pi_error}"], { encoding: "utf8" }).trim(); writeFileSync(resultPath, JSON.stringify({ sent, state, unarmedInputs })); fire("session_shutdown", {}, ctx); setTimeout(() => process.exit(0), 20); };\nbuffer.on("data", (data) => { if (!armed && data === "A") { armed = true; fire("agent_start", {}, ctx); fire("message_end", { message: { role: "assistant", stopReason: "error", errorMessage: "stream_read_error" } }, ctx); fire("agent_settled", {}, ctx); setTimeout(report, 600); return; } if (!armed) { unarmedInputs.push(Buffer.from(data).toString("hex")); return; } terminalInput?.(data); });\nprocess.stdin.setRawMode(true); process.stdin.resume(); process.stdin.on("data", (chunk) => buffer.process(chunk)); process.stdout.write("WAIT\\r\\n");\n`);
    writeFileSync(injector, `import fcntl, json, os, pty, select, struct, subprocess, sys, termios, time\nsocket, session, pane, mode, key_hex, scenario, result_path = sys.argv[1:]\nenv = os.environ.copy(); env.pop("TMUX", None); env.pop("TMUX_PANE", None)\nbase = ["tmux", "-S", socket]\ndef tx(*args, check=True): return subprocess.run(base + list(args), check=check, capture_output=True, text=True, env=env)\ndef wait_armed():\n    deadline = time.time() + 0.24\n    while time.time() < deadline:\n        state = tx("display-message", "-p", "-t", pane, "#{@quick_deploy_pi_recovery_armed}|#{@quick_deploy_pi_error}").stdout.strip()\n        if state == "1|1": return state\n        time.sleep(0.005)\n    raise AssertionError("recovery did not arm before debounce")\npid, fd = pty.fork()\nif pid == 0:\n    os.environ.clear(); os.environ.update(env); os.environ["TERM"] = "xterm-256color"\n    os.execvp("tmux", base + ["attach-session", "-t", session])\ntry:\n    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))\n    deadline = time.time() + 3\n    while time.time() < deadline and not tx("list-clients", "-t", session, check=False).stdout.strip(): time.sleep(0.02)\n    tx("set-option", "-w", "-t", pane, "mode-keys", mode); tx("select-pane", "-t", pane)\n    unarmed_mode = before_mode = prompt_mode = pane_in_mode = pre_close_state = ""\n    close_key = b"\\r" if scenario == "armed-enter" else b"\\x1b"\n    if scenario in ("armed-close", "armed-enter"):\n        tx("copy-mode", "-t", pane); time.sleep(0.02); os.write(fd, bytes.fromhex(key_hex)); time.sleep(0.05); os.write(fd, close_key); time.sleep(0.05)\n        unarmed_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip(); tx("send-keys", "-X", "cancel", "-t", pane)\n        tx("send-keys", "-l", "-t", pane, "A"); wait_armed()\n        tx("copy-mode", "-t", pane); time.sleep(0.02); before_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n        os.write(fd, bytes.fromhex(key_hex)); time.sleep(0.10); prompt_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n        pre_close_state = tx("display-message", "-p", "-t", pane, "#{@quick_deploy_pi_recovery_armed}|#{@quick_deploy_pi_error}").stdout.strip()\n        os.write(fd, close_key); time.sleep(0.05); pane_in_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n    elif scenario == "no-close":\n        tx("send-keys", "-l", "-t", pane, "A"); wait_armed()\n        tx("copy-mode", "-t", pane); time.sleep(0.02); before_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n        os.write(fd, bytes.fromhex(key_hex)); time.sleep(0.10); prompt_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n        pre_close_state = tx("display-message", "-p", "-t", pane, "#{@quick_deploy_pi_recovery_armed}|#{@quick_deploy_pi_error}").stdout.strip(); pane_in_mode = prompt_mode\n    elif scenario == "arm-while-open":\n        tx("copy-mode", "-t", pane); time.sleep(0.02); before_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n        os.write(fd, bytes.fromhex(key_hex)); time.sleep(0.05); prompt_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n        tx("set-buffer", "-b", "__test_arm_trigger", "A"); tx("paste-buffer", "-d", "-b", "__test_arm_trigger", "-t", pane); wait_armed()\n        pre_close_state = tx("display-message", "-p", "-t", pane, "#{@quick_deploy_pi_recovery_armed}|#{@quick_deploy_pi_error}").stdout.strip()\n        os.write(fd, b"\\x1b"); time.sleep(0.05); pane_in_mode = tx("display-message", "-p", "-t", pane, "#{pane_in_mode}").stdout.strip()\n    else: raise AssertionError("unknown scenario")\n    deadline = time.time() + 3\n    while time.time() < deadline and not os.path.exists(result_path): time.sleep(0.02)\n    if not os.path.exists(result_path): raise AssertionError("extension result missing")\n    result = json.load(open(result_path, encoding="utf8")); result.update({"scenario": scenario, "unarmedMode": unarmed_mode, "beforeMode": before_mode, "promptMode": prompt_mode, "preCloseState": pre_close_state, "paneInMode": pane_in_mode}); print(json.dumps(result))\nfinally:\n    tx("detach-client", "-s", session, check=False)\n    try: os.waitpid(pid, 0)\n    except ChildProcessError: pass\n`);
    const tmux = (args, options = {}) => isolatedTmux(args, options);
    const env = { ...process.env, QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };
    tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", item.label, `${process.execPath} --experimental-strip-types '${fixture}' '${resultPath}'`], { env });
    tmux(["-S", socket, "set-option", "-w", "-t", item.label, "remain-on-exit", "on"], { env });
    const paneId = tmux(["-S", socket, "display-message", "-p", "-t", item.label, "#{pane_id}"], { encoding: "utf8", env }).trim();
    try {
      const output = execFileSync("python3", [injector, socket, item.label, paneId, item.mode, item.keyHex, item.scenario, resultPath], { encoding: "utf8", env: withoutTmuxEnvironment(env), timeout: 10_000 });
      const result = JSON.parse(output.trim());
      if (["armed-close", "armed-enter"].includes(item.scenario)) assert.equal(result.unarmedMode, "1", `${item.label}: unarmed prompt close preserves copy-mode`);
      assert.deepEqual(result.unarmedInputs, [], `${item.label}: unarmed prompt emits no pane bytes`);
      assert.equal(result.beforeMode, "1", `${item.label}: test entered copy-mode`);
      assert.equal(result.promptMode, "1", `${item.label}: native search prompt stays open in copy-mode`);
      assert.equal(result.preCloseState, "1|1", `${item.label}: recovery is still armed immediately before any close key`);
      assert.equal(result.paneInMode, "1", `${item.label}: prompt lifecycle preserves copy-mode: ${JSON.stringify(result)}`);
      if (item.scenario === "no-close") {
        assert.deepEqual(result.sent, ["continue"], `${item.label}: opening alone does not cancel the real timer`);
        assert.equal(result.state, "1|1", `${item.label}: no-close leaves recovery armed`);
      } else {
        assert.deepEqual(result.sent, [], `${item.label}: first prompt-closing Esc cancels the real 250 ms timer`);
        assert.equal(result.state, "|", `${item.label}: prompt close clears extension error and animator marker`);
      }
      const buffers = spawnSync("tmux", ["-S", socket, "list-buffers", "-F", "#{buffer_name}"], { encoding: "utf8", env: withoutTmuxEnvironment(env) }).stdout || "";
      assert.doesNotMatch(buffers, /__quick_deploy_pi_cancel_/, `${item.label}: transient sentinel buffer is deleted`);
    } finally {
      await new Promise((resolve) => setTimeout(resolve, 50));
      try { tmux(["-S", socket, "kill-server"], { env }); } catch {}
    }
  }
});

test("detached animator process remains alive for later 42ms frames and exits after lease removal", async () => {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock");
  const tmux = (args, options = {}) => isolatedTmux(args, options);
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", "animator-process"]);
  const windowId = tmux(["-S", socket, "display-message", "-p", "#{window_id}"], { encoding: "utf8" }).trim();
  const paneId = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const i = { socketPath: socket, windowId, paneId }, env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };
  publishLease(i, "process", "active", Date.now(), env);
  const animator = spawn(process.execPath, [join(ROOT, "pi-agent/extensions/pi-tmux-window-status/animator.mjs"), socket], { env: { ...withoutTmuxEnvironment(), ...env }, stdio: "ignore" });
  try {
    await new Promise((resolve) => setTimeout(resolve, FRAME_MS * 3 + 30));
    assert.equal(animator.exitCode, null, "referenced frame timer keeps the detached helper alive");
    const color = tmux(["-S", socket, "show-options", "-wv", "@quick_deploy_pi_bg"], { encoding: "utf8" }).trim();
    assert.notEqual(color, FRAMES[0], "helper advances beyond the initial frame");
    publishLease(i, "process", false, Date.now(), env);
    const exited = await Promise.race([
      new Promise((resolve) => animator.once("exit", () => resolve(true))),
      new Promise((resolve) => setTimeout(() => resolve(false), 1_000)),
    ]);
    assert.equal(exited, true, "helper exits after the active lease is removed");
    assert.equal(tmux(["-S", socket, "show-options", "-wqv", "@quick_deploy_pi_active"], { encoding: "utf8" }).trim(), "");
  } finally {
    if (animator.exitCode === null) animator.kill("SIGKILL");
    tmux(["-S", socket, "kill-server"]);
  }
});

test("urgent animator tick clears the last error without repaint and preserves another root error", async () => {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock");
  const tmux = (args, options = {}) => isolatedTmux(args, options);
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", "urgent-error"]);
  const windowId = tmux(["-S", socket, "display-message", "-p", "#{window_id}"], { encoding: "utf8" }).trim();
  const paneId = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const first = { socketPath: socket, windowId, paneId }, second = { ...first, paneId: "%other" };
  const env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };
  publishLease(first, "root-a", "error", Date.now(), env);
  publishLease(second, "root-b", "error", Date.now(), env);
  const animator = spawn(process.execPath, [join(ROOT, "pi-agent/extensions/pi-tmux-window-status/animator.mjs"), socket], { env: { ...withoutTmuxEnvironment(), ...env }, stdio: "ignore" });
  const errorOption = () => tmux(["-S", socket, "show-options", "-wqv", "@quick_deploy_pi_error"], { encoding: "utf8" }).trim();
  const paneArmed = () => tmux(["-S", socket, "show-options", "-pqv", "-t", paneId, RECOVERY_ARMED_OPTION], { encoding: "utf8" }).trim();
  try {
    await waitFor(errorOption, "1");
    await waitFor(paneArmed, "1");
    publishLease(first, "root-a", false, Date.now(), env);
    assert.equal(requestAnimatorTick(first, env), true, "request reaches the shared lock owner");
    await waitFor(paneArmed, "");
    assert.equal(errorOption(), "1", "another root error preserves aggregate red while this pane loses authorization");

    publishLease(second, "root-b", false, Date.now(), env);
    assert.equal(requestAnimatorTick(second, env), true);
    await waitFor(errorOption, "", 500);
    await new Promise((resolve) => setTimeout(resolve, ERROR_MS + 150));
    assert.equal(errorOption(), "", "no stale frame repaints red after more than one error interval");
    const exited = await Promise.race([
      animator.exitCode !== null ? Promise.resolve(true) : new Promise((resolve) => animator.once("exit", () => resolve(true))),
      new Promise((resolve) => setTimeout(() => resolve(false), 500)),
    ]);
    assert.equal(exited, true, "animator exits after urgent reconciliation removes the last lease");
  } finally {
    if (animator.exitCode === null) animator.kill("SIGKILL");
    tmux(["-S", socket, "kill-server"]);
  }
});

test("animator startup sweep clears stale pane markers and derives current owner from leases", () => {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock"), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };
  const tmux = (args, options = {}) => isolatedTmux(args, options);
  tmux(["-S", socket, "-f", "/dev/null", "new-session", "-d", "-s", "pane-sweep"]);
  const ownerPane = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const stalePane = tmux(["-S", socket, "split-window", "-dP", "-F", "#{pane_id}", "-t", "pane-sweep"], { encoding: "utf8" }).trim();
  const windowId = tmux(["-S", socket, "display-message", "-p", "#{window_id}"], { encoding: "utf8" }).trim();
  const i = { socketPath: socket, windowId, paneId: ownerPane };
  const marker = (pane) => tmux(["-S", socket, "show-options", "-pqv", "-t", pane, RECOVERY_ARMED_OPTION], { encoding: "utf8" }).trim();
  const root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(socket).toString("base64url"));
  tmux(["-S", socket, "set-option", "-p", "-t", ownerPane, RECOVERY_ARMED_OPTION, "1"]);
  tmux(["-S", socket, "set-option", "-p", "-t", stalePane, RECOVERY_ARMED_OPTION, "1"]);
  publishLease(i, "live-owner", "error", Date.now(), env);
  let animator;
  try {
    assert.deepEqual([...listRecoveryArmedPanes("tmux", socket)].sort(), [ownerPane, stalePane].sort());
    animator = runAnimator({ socketPath: socket, root, schedule: () => ({ unref() {} }), cancel: () => {}, subscribeUrgent: inertUrgent });
    assert.equal(animator.started, true);
    assert.equal(marker(ownerPane), "1", "live error lease retains marker");
    assert.equal(marker(stalePane), "", "startup sweep clears marker without a live error lease");
    publishLease(i, "live-owner", false, Date.now(), env);
    animator.tick();
    assert.equal(marker(ownerPane), "", "lease removal clears prior owner marker");
  } finally {
    animator?.stop?.();
    tmux(["-S", socket, "kill-server"]);
  }
});

test("crashed publisher lease expiry clears reused pane marker while sibling keeps window red", async () => {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock"), session = "ttl-reuse";
  const tmux = (args, options = {}) => isolatedTmux(args, options);
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", session]);
  const paneA = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const paneB = tmux(["-S", socket, "split-window", "-dP", "-F", "#{pane_id}", "-t", session], { encoding: "utf8" }).trim();
  const windowId = tmux(["-S", socket, "display-message", "-p", "#{window_id}"], { encoding: "utf8" }).trim();
  const stateUrl = pathToFileURL(join(ROOT, "pi-agent/extensions/pi-tmux-window-status/state.mjs")).href;
  const publisher = join(work, "publisher.mjs"), receiver = join(work, "read-burst.py"), injector = join(work, "inject-root.py");
  writeFileSync(publisher, `import { publishLease } from ${JSON.stringify(stateUrl)};\nconst [socketPath, windowId, paneId, ownerId, runtime] = process.argv.slice(2);\nconst identity = { socketPath, windowId, paneId };\nconst env = { ...process.env, QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };\nconst beat = () => publishLease(identity, ownerId, "error", Date.now(), env);\nbeat(); setInterval(beat, 1000);\n`);
  writeFileSync(receiver, `import os, select, sys, time, tty\ntty.setraw(sys.stdin.fileno())\ndata = b""\ndeadline = time.time() + 15\nwhile time.time() < deadline:\n    ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.05 if data else 0.2)\n    if not ready:\n        if data: break\n        continue\n    chunk = os.read(sys.stdin.fileno(), 65536)\n    if not chunk: break\n    data += chunk\nsys.stdout.write("INPUT=" + data.hex() + "\\r\\n"); sys.stdout.flush(); time.sleep(0.5)\n`);
  writeFileSync(injector, `import fcntl, os, pty, select, struct, subprocess, sys, termios, time\nsocket, session, pane = sys.argv[1:]\nenv = os.environ.copy(); env.pop("TMUX", None); env.pop("TMUX_PANE", None)\nbase = ["tmux", "-S", socket]\ndef tx(*args, check=True): return subprocess.run(base + list(args), check=check, capture_output=True, text=True, env=env)\npid, fd = pty.fork()\nif pid == 0:\n    os.environ.clear(); os.environ.update(env); os.environ["TERM"] = "xterm-256color"\n    os.execvp("tmux", base + ["attach-session", "-t", session])\ntry:\n    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))\n    deadline = time.time() + 3\n    while time.time() < deadline and not tx("list-clients", "-t", session, check=False).stdout.strip(): time.sleep(0.05)\n    tx("select-pane", "-t", pane); time.sleep(0.1)\n    while select.select([fd], [], [], 0)[0]:\n        try: os.read(fd, 65536)\n        except OSError: break\n    os.write(fd, b"\\x1b")\n    deadline = time.time() + 2\n    while time.time() < deadline:\n        out = tx("capture-pane", "-p", "-t", pane).stdout.replace("\\r", "")\n        found = [line for line in out.splitlines() if line.startswith("INPUT=")]\n        if found: print(found[0]); break\n        time.sleep(0.02)\nfinally:\n    tx("detach-client", "-s", session, check=False)\n    try: os.waitpid(pid, 0)\n    except ChildProcessError: pass\n`);
  const publisherCommand = (pane, owner) => `${process.execPath} '${publisher}' '${socket}' '${windowId}' '${pane}' '${owner}' '${runtime}'`;
  tmux(["-S", socket, "respawn-pane", "-k", "-t", paneA, publisherCommand(paneA, "publisher-a")]);
  tmux(["-S", socket, "respawn-pane", "-k", "-t", paneB, publisherCommand(paneB, "publisher-b")]);
  const animator = spawn(process.execPath, [join(ROOT, "pi-agent/extensions/pi-tmux-window-status/animator.mjs"), socket], { env: { ...withoutTmuxEnvironment(), QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, stdio: "ignore" });
  const marker = (pane) => tmux(["-S", socket, "show-options", "-pqv", "-t", pane, RECOVERY_ARMED_OPTION], { encoding: "utf8" }).trim();
  const red = () => tmux(["-S", socket, "show-options", "-wqv", "@quick_deploy_pi_error"], { encoding: "utf8" }).trim();
  try {
    await waitFor(() => marker(paneA), "1");
    await waitFor(() => marker(paneB), "1");
    await waitFor(red, "1");
    tmux(["-S", socket, "respawn-pane", "-k", "-t", paneA, `python3 '${receiver}'`]);
    await new Promise((resolve) => setTimeout(resolve, LEASE_TTL_MS + 1_200));
    await waitFor(() => marker(paneA), "", 2_000);
    assert.equal(marker(paneB), "1", "live sibling retains its derived marker");
    assert.equal(red(), "1", "live sibling keeps aggregate window red");
    const routed = execFileSync("python3", [injector, socket, session, paneA], { encoding: "utf8", env: withoutTmuxEnvironment() }).trim();
    assert.equal(routed, "INPUT=1b", "reused expired pane receives ordinary Esc without stale sentinel");
  } finally {
    if (animator.exitCode === null) animator.kill("SIGKILL");
    try { tmux(["-S", socket, "kill-server"]); } catch {}
  }
});

test("client listing handles zero clients and startup sweep resets stale active/error windows", () => {
  assert.deepEqual(listClients("tmux", "/x", () => { const e = new Error("none"); e.status = 1; throw e; }), []);
  const calls = [];
  assert.deepEqual(listClients("tmux", "/x", (_t, args) => { calls.push(args); return "c2\nc1\n"; }), ["c1", "c2"]);
  assert.equal(calls.length, 1);
  const sweep = [];
  assert.deepEqual(sweepWindows("tmux", "/x", { active: new Set(["@1"]), error: new Set() }, (_t, args) => { sweep.push(args); return args.includes("list-windows") ? "@1\n@2\n" : ""; }), ["@2"]);
  assert.ok(sweep[1].includes("@2"));
  assert.ok(sweep[1].includes("@quick_deploy_pi_error"));
  const args = windowOptionArgs(new Set(["@1"]), 6, new Set(["@2"]), new Set(["@3"]));
  assert.ok(args.includes(FRAMES[6]), "active windows breathe through the gray palette");
  assert.equal(args.filter((value) => value === "@quick_deploy_pi_current_bg").length, 1, "only the reset branch unsets the legacy current-bg option");
  assert.ok(args.includes("@quick_deploy_pi_error"));
});

test("installer is executable, idempotent, exact-link skip, and preserves foreign new path", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh"), env = { ...process.env, PI_TMUX_WINDOW_STATUS_SOURCE: source, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  assert.equal(spawnSync("bash", [installer], { env }).status, 0);
  assert.equal(spawnSync("bash", [installer], { env }).status, 0);
  const target = join(env.PI_CODING_AGENT_DIR, "extensions", "pi-tmux-window-status");
  assert.equal(readlinkSync(target), source, "second run leaves the exact managed link in place");
  rmSync(target);
  mkdirSync(target);
  writeFileSync(join(target, "keep"), "yes");
  assert.notEqual(spawnSync("bash", [installer], { env }).status, 0);
  assert.equal(readFileSync(join(target, "keep"), "utf8"), "yes");
});

test("installer migrates a known legacy managed old link to the new managed link", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const env = { ...process.env, PI_TMUX_WINDOW_STATUS_SOURCE: source, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh");
  const extensions = join(env.PI_CODING_AGENT_DIR, "extensions"), legacy = join(extensions, "quick-deploy-tmux-status"), target = join(extensions, "pi-tmux-window-status");
  const oldCheckout = join(d, "old-checkout", "pi-agent", "extensions", "quick-deploy-tmux-status");
  mkdirSync(oldCheckout, { recursive: true });
  mkdirSync(extensions, { recursive: true });
  symlinkSync(oldCheckout, legacy);
  assert.equal(spawnSync("bash", [installer], { env }).status, 0);
  assert.equal(readlinkSync(target), source, "new managed link points at the new source");
  assert.equal(existsSync(legacy), false, "legacy link was removed");
  const backups = readdirSync(extensions).filter((entry) => entry.startsWith("quick-deploy-tmux-status.bak."));
  assert.equal(backups.length, 1, "legacy link is backed up, never deleted");
  assert.equal(readlinkSync(join(extensions, backups[0])), oldCheckout, "backup preserves the original raw target");
});

test("installer refuses a foreign legacy path without mutation", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const env = { ...process.env, PI_TMUX_WINDOW_STATUS_SOURCE: source, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh");
  const legacy = join(env.PI_CODING_AGENT_DIR, "extensions", "quick-deploy-tmux-status");
  mkdirSync(legacy, { recursive: true });
  writeFileSync(join(legacy, "keep"), "yes");
  assert.notEqual(spawnSync("bash", [installer], { env }).status, 0, "foreign legacy path fails");
  assert.equal(readFileSync(join(legacy, "keep"), "utf8"), "yes", "foreign legacy content untouched");
  assert.equal(existsSync(join(env.PI_CODING_AGENT_DIR, "extensions", "pi-tmux-window-status")), false, "no new link is created on foreign conflict");
});

test("installer fails without mutation when both old and new exist and one side is foreign", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const env = { ...process.env, PI_TMUX_WINDOW_STATUS_SOURCE: source, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh");
  const extensions = join(env.PI_CODING_AGENT_DIR, "extensions"), legacy = join(extensions, "quick-deploy-tmux-status"), target = join(extensions, "pi-tmux-window-status");
  mkdirSync(extensions, { recursive: true });
  symlinkSync(source, target);
  mkdirSync(legacy, { recursive: true });
  writeFileSync(join(legacy, "keep"), "yes");
  assert.notEqual(spawnSync("bash", [installer], { env }).status, 0, "both-present with a foreign side fails");
  assert.equal(readlinkSync(target), source, "exact new link is untouched");
  assert.equal(readFileSync(join(legacy, "keep"), "utf8"), "yes", "foreign legacy dir is untouched");
});

test("installer proceeds when both old and new are known managed links", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const env = { ...process.env, PI_TMUX_WINDOW_STATUS_SOURCE: source, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh");
  const extensions = join(env.PI_CODING_AGENT_DIR, "extensions"), legacy = join(extensions, "quick-deploy-tmux-status"), target = join(extensions, "pi-tmux-window-status");
  const oldCheckout = join(d, "old-checkout", "pi-agent", "extensions", "quick-deploy-tmux-status");
  mkdirSync(oldCheckout, { recursive: true });
  mkdirSync(extensions, { recursive: true });
  symlinkSync(oldCheckout, legacy);
  const otherCheckout = join(d, "other-checkout", "pi-agent", "extensions", "pi-tmux-window-status");
  mkdirSync(otherCheckout, { recursive: true });
  symlinkSync(otherCheckout, target);
  assert.equal(spawnSync("bash", [installer], { env }).status, 0, "both managed links are reconciled");
  assert.equal(readlinkSync(target), source, "new target is reinstalled to the real source");
  assert.equal(existsSync(legacy), false);
  const backups = readdirSync(extensions).filter((entry) => /^(quick-deploy-tmux-status|pi-tmux-window-status)\.bak\./.test(entry));
  assert.equal(backups.length, 2, "both managed links are backed up before replacement");
});

test("installer repairs a stale new-link target left by a checkout move", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const env = { ...process.env, PI_TMUX_WINDOW_STATUS_SOURCE: source, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh");
  const extensions = join(env.PI_CODING_AGENT_DIR, "extensions"), target = join(extensions, "pi-tmux-window-status");
  const movedCheckout = join(d, "moved-checkout", "pi-agent", "extensions", "pi-tmux-window-status");
  mkdirSync(extensions, { recursive: true });
  symlinkSync(movedCheckout, target);
  assert.equal(existsSync(target), false, "precondition: link is dangling because the checkout moved");
  assert.equal(spawnSync("bash", [installer], { env }).status, 0);
  assert.equal(readlinkSync(target), source, "dangling managed link is replaced with the real source");
  const backups = readdirSync(extensions).filter((entry) => entry.startsWith("pi-tmux-window-status.bak."));
  assert.equal(backups.length, 1, "dangling managed link is backed up");
});

test("installer resolves the tracked source independently of the working directory", () => {
  const d = temp(), home = join(d, "home");
  const env = { ...process.env, PI_CODING_AGENT_DIR: join(home, ".pi", "agent") };
  const installer = join(ROOT, "pi-agent/extensions/pi-tmux-window-status/install.sh");
  const target = join(env.PI_CODING_AGENT_DIR, "extensions", "pi-tmux-window-status");
  const repoSource = join(ROOT, "pi-agent/extensions/pi-tmux-window-status");
  assert.equal(spawnSync("bash", [installer], { env, cwd: d }).status, 0, "default source resolution must not depend on the caller's cwd");
  assert.equal(resolve(readlinkSync(target)), repoSource, "default source resolves to the tracked extension");
});

test("full gpakosz load cannot follow polluted outer tmux routing", () => {
  const work = temp(), outer = join(work, "outer.sock"), inner = join(work, "inner.sock");
  const clean = withoutTmuxEnvironment();
  const rawTmux = (args, options = {}) => execFileSync("tmux", args, { cwd: work, env: clean, ...options });
  rawTmux(["-S", outer, "-f", "/dev/null", "new-session", "-d", "-s", "outer"]);
  try {
    rawTmux(["-S", outer, "set-option", "-g", "window-status-current-format", "OUTER_SENTINEL"]);
    const outerPid = rawTmux(["-S", outer, "display-message", "-p", "#{pid}"], { encoding: "utf8" }).trim();
    const polluted = {
      ...process.env,
      TMUX: `${outer},${outerPid},0`,
      TMUX_PANE: "%0",
      TMUX_SOCKET: outer,
      TMUX_CONF: join(process.env.HOME, ".tmux.conf"),
      TMUX_CONF_LOCAL: join(process.env.HOME, ".tmux.conf.local"),
      TMUX_PROGRAM: "/usr/bin/tmux",
    };
    isolatedTmux(["-S", inner, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", "inner"], { cwd: work, env: polluted });
    execFileSync("sleep", ["1"]);
    const outerFormat = rawTmux(["-S", outer, "show-options", "-gqv", "window-status-current-format"], { encoding: "utf8" }).trim();
    assert.equal(outerFormat, "OUTER_SENTINEL", "secondary gpakosz commands stay on the explicit isolated socket");
    const innerFormat = rawTmux(["-S", inner, "show-options", "-gqv", "window-status-current-format"], { encoding: "utf8" });
    assert.match(innerFormat, new RegExp(FRAME_BLUE));
  } finally {
    for (const socket of [inner, outer]) {
      try { rawTmux(["-S", socket, "kill-server"]); } catch {}
    }
  }
});

test("gpakosz generated reload stage keeps blue rails and a gray dynamic center", () => {
  const work = temp(), home = join(work, "home"), socket = join(work, "server.sock");
  mkdirSync(home, { recursive: true });
  symlinkSync(join(process.env.HOME, ".tmux", ".tmux.conf"), join(home, ".tmux.conf"));
  const localSource = join(ROOT, "fresh-install/modules/tmux/tmux.conf.local");
  const localText = readFileSync(localSource, "utf8");
  const directMatch = localText.match(/^setw -g window-status-current-format '(.*)' #!important$/m);
  assert.ok(directMatch, "direct important current format is present");
  const direct = directMatch[1];
  const generatedOnly = localText
    .split("\n")
    .filter((line) => !line.startsWith("setw -g window-status-current-format "))
    .join("\n");
  writeFileSync(join(home, ".tmux.conf.local"), generatedOnly);
  const env = { ...process.env, HOME: home, TERM: "xterm-256color" };
  const tmux = (args, options = {}) => isolatedTmux(args, { cwd: work, env, ...options });
  tmux(["-S", socket, "-f", join(home, ".tmux.conf"), "new-session", "-d", "-s", "q"]);
  try {
    execFileSync("sleep", ["1"]);
    const current = tmux(["-S", socket, "show-options", "-gqv", "window-status-current-format"], { encoding: "utf8" });
    const evalf = (format = current) => tmux(["-S", socket, "display-message", "-p", format], { encoding: "utf8" });
    const assertMatchesFinalCellStyles = (label) => {
      const generatedCells = styledCells(evalf(current));
      const finalCells = styledCells(evalf(direct));
      assert.equal(generatedCells.length, finalCells.length, `${label}: generated and final widths match`);
      assert.deepEqual(
        generatedCells.map(({ fg, bg }) => ({ fg, bg })),
        finalCells.map(({ fg, bg }) => ({ fg, bg })),
        `${label}: every generated cell has the final foreground/background`,
      );
      assert.equal(
        generatedCells.slice(2, -2).map(({ char }) => char).join(""),
        finalCells.slice(2, -2).map(({ char }) => char).join(""),
        `${label}: center text matches the final format`,
      );
      assert.deepEqual(generatedCells.slice(0, 2).map(({ bg }) => bg), [FRAME_BLUE, FRAME_BLUE], `${label}: left rail is two blue cells`);
      assert.deepEqual(generatedCells.slice(-2).map(({ bg }) => bg), [FRAME_BLUE, FRAME_BLUE], `${label}: right rail is two blue cells`);
    };
    assert.doesNotMatch(current, /#00afff/, "upstream theme generation has no whole-blue fallback");
    assert.match(current, new RegExp(FRAME_BLUE));
    assert.match(current, /@quick_deploy_pi_bg/);
    assert.match(evalf(), /#bcbcbc/, "generated idle center is gray-white");
    assertMatchesFinalCellStyles("idle");
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_active", "1"]);
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_bg", FRAMES[6]]);
    assert.match(evalf(), new RegExp(FRAMES[6]), "generated active center follows the gray animator frame");
    assertMatchesFinalCellStyles("active");
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_error", "1"]);
    assert.match(evalf(), new RegExp(ERROR_BG), "generated error center stays red");
    assert.match(evalf(), new RegExp(FRAME_BLUE), "generated error state keeps blue rails");
    assertMatchesFinalCellStyles("error");
  } finally {
    tmux(["-S", socket, "kill-server"]);
  }
});

test("isolated gpakosz load evaluates actual deployed formats across idle and two active frames", () => {
  const work = temp(), socket = join(work, "server.sock");
  const tmux = (args, options = {}) => isolatedTmux(args, { cwd: work, ...options });
  const stripStyles = (s) => String(s).replace(/#\[[^\]]*\]/g, "");
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", "q"]);
  try {
    for (const table of ["root", "copy-mode", "copy-mode-vi"]) {
      const binding = tmux(["-S", socket, "list-keys", "-T", table, "Escape"], { encoding: "utf8" });
      assert.match(binding, /@quick_deploy_pi_recovery_armed.*@quick_deploy_pi_error/, `${table} retains pane ownership plus aggregate red gating`);
      assert.match(binding, /send-keys -H 1b 5b 39 39 37 3b 31 7e/, `${table} prepends the private cancellation sentinel`);
      if (table !== "root") assert.match(binding, /send-keys -X cancel/, `${table} exits copy-mode before routing`);
    }
    for (const [table, key, search] of [["copy-mode", "C-r", "search-backward"], ["copy-mode", "C-s", "search-forward"], ["copy-mode-vi", "/", "search-forward"], ["copy-mode-vi", "?", "search-backward"]]) {
      const binding = tmux(["-S", socket, "list-keys", "-T", table, key], { encoding: "utf8" });
      assert.match(binding, new RegExp(`command-prompt.*send-keys -X ${search}`), `${table} ${key} preserves search direction`);
      if (table === "copy-mode") { assert.doesNotMatch(binding, /command-prompt -i/, `${key} deliberately uses a blocking submit-on-close prompt`); assert.match(binding, /-I "#{pane_search_string}"/, `${key} preserves the previous search as initial input`); }
      assert.match(binding, /paste-buffer -d -b .*quick_deploy_pi_cancel/, `${table} ${key} emits sentinel only after prompt closure`);
    }
    const idle = tmux(["-S", socket, "show-options", "-gqv", "window-status-format"], { encoding: "utf8" });
    const current = tmux(["-S", socket, "show-options", "-gqv", "window-status-current-format"], { encoding: "utf8" });
    const evalf = (f) => tmux(["-S", socket, "display-message", "-p", f], { encoding: "utf8" });
    assert.match(evalf(idle), /#bcbcbc/);
    assert.match(evalf(current), /#bcbcbc/, "selected idle keeps the same gray-white background");
    assert.match(evalf(current), new RegExp(FRAME_BLUE), "selected idle shows the deep-blue side rails");
    assert.doesNotMatch(evalf(current), /#00afff/, "selected no longer uses a blue background block");
    const visible = stripStyles(evalf(current));
    assert.match(visible, /██.*██/, "selected draws two-cell full-block blue bars at both ends");
    assert.match(evalf(idle), /#080808/);
    assert.doesNotMatch(idle + current, /#\(/);
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_error", "1"]);
    assert.match(evalf(idle), new RegExp(ERROR_BG));
    assert.match(evalf(idle), new RegExp(ERROR_FG));
    assert.match(evalf(current), new RegExp(ERROR_BG));
    assert.match(evalf(current), new RegExp(ERROR_FG));
    assert.match(evalf(current), new RegExp(FRAME_BLUE), "selected with error keeps the blue frame on the red block");
    assert.match(idle, /window_bell_flag,#ffff00/);
    assert.match(idle, /window_bell_flag,!,/);
    assert.match(evalf(idle), new RegExp(ERROR_FG), "error foreground wins over bell yellow path while preserving bell marker conditional");
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_active", "1"]);
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_bg", FRAMES[6]]);
    assert.match(evalf(idle), new RegExp(ERROR_BG), "error+active resolves red");
    tmux(["-S", socket, "set-option", "-uw", "@quick_deploy_pi_error"]);
    for (const color of [FRAMES[6], FRAMES[18]]) {
      tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_active", "1"]);
      tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_bg", color]);
      assert.match(evalf(idle), new RegExp(color));
      assert.match(evalf(current), new RegExp(color), "selected and unselected breathe the same gray palette");
      assert.match(evalf(current), new RegExp(FRAME_BLUE), "the frame stays visible while the block breathes");
    }
    tmux(["-S", socket, "set-option", "-uw", "@quick_deploy_pi_active"]);
    tmux(["-S", socket, "set-option", "-uw", "@quick_deploy_pi_bg"]);
    assert.match(evalf(idle), /#bcbcbc/);
  } finally {
    tmux(["-S", socket, "kill-session", "-t", "q"]);
  }
});
