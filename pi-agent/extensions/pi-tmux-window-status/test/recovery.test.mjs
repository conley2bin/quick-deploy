import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync } from "node:child_process";
import { cpSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import extension from "../index.ts";
import {
  AUTO_CONTINUE_MAX,
  AUTO_CONTINUE_MIN_INTERVAL_MS,
  CANCEL_SENTINEL,
  ERROR_STALL_MS,
  RECOVERY_CANCEL_EVENT,
} from "../recovery.mjs";

const PERMISSION_ERROR = JSON.stringify({ type: "error", error: { type: "permission_error", message: "You've reached your concurrent request limit. Please wait for your ongoing requests to finish and try again." } });
const failure = (errorMessage) => ({ role: "assistant", content: [], stopReason: "error", ...(errorMessage === undefined ? {} : { errorMessage }) });

function harness(t, { mode = "tui", depth, send, entry = extension } = {}) {
  const previous = { ...process.env };
  for (const key of Object.keys(process.env)) if (key === "TMUX" || key.startsWith("TMUX_") || key === "PI_SUBAGENT_DEPTH") delete process.env[key];
  if (depth !== undefined) process.env.PI_SUBAGENT_DEPTH = String(depth);
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  const handlers = new Map(), channels = new Map(), inputs = new Set(), sent = [], sentAt = [], diagnostics = [];
  t.mock.method(console, "error", (...args) => diagnostics.push(args));
  let signal, idle = true, pending = false;
  const ctx = {
    mode,
    get signal() { return signal; },
    isIdle: () => idle,
    hasPendingMessages: () => pending,
    ui: { onTerminalInput(fn) { inputs.add(fn); return () => inputs.delete(fn); } },
  };
  const events = {
    on(name, fn) { if (!channels.has(name)) channels.set(name, new Set()); channels.get(name).add(fn); return () => channels.get(name).delete(fn); },
    emit(name, data) { for (const fn of channels.get(name) || []) fn(data); },
  };
  const pi = {
    on(name, fn) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); },
    events,
    sendUserMessage(text) { sent.push(text); sentAt.push(Date.now()); send?.(text); },
  };
  const fire = (name, event = {}, context = ctx) => { for (const fn of handlers.get(name) || []) fn(event, context); };
  entry(pi);
  fire("session_start");
  const h = {
    ctx, pi, inputs, channels, sent, sentAt, diagnostics, handlers, fire,
    tick: (ms) => t.mock.timers.tick(ms),
    start() { signal = new AbortController().signal; idle = false; fire("agent_start"); return signal; },
    error(message = failure(PERMISSION_ERROR)) { fire("message_end", { message }); },
    settle(aborted = false) { idle = true; fire("agent_settled", { aborted }); },
    terminal(data) { for (const fn of inputs) { const result = fn(data); if (result?.consume) return result; } },
    set idle(value) { idle = value; },
    set pending(value) { pending = value; },
  };
  t.after(() => {
    fire("session_shutdown");
    t.mock.timers.reset();
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
  });
  return h;
}

test("user policy is ten minutes since error, at least ten minutes per send, at most three", () => {
  assert.equal(ERROR_STALL_MS, 600_000);
  assert.equal(AUTO_CONTINUE_MIN_INTERVAL_MS, 600_000);
  assert.equal(AUTO_CONTINUE_MAX, 3);
});

test("standalone module install recovers without any suspend-guard source or installation", async (t) => {
  const work = mkdtempSync(join(tmpdir(), "pi-status-recovery-install-"));
  t.after(() => rmSync(work, { recursive: true, force: true }));
  const source = join(work, "pi-agent", "extensions", "pi-tmux-window-status");
  const piHome = join(work, "pi-home");
  cpSync(new URL("..", import.meta.url), source, { recursive: true });
  execFileSync("bash", [join(source, "install.sh")], { env: { ...process.env, PI_CODING_AGENT_DIR: piHome } });
  assert.equal(existsSync(join(work, "pi-agent", "extensions", "pi-suspend-guard")), false);
  assert.equal(existsSync(join(piHome, "extensions", "pi-suspend-guard")), false);
  const { default: entry } = await import(pathToFileURL(join(piHome, "extensions", "pi-tmux-window-status", "index.ts")).href);
  const h = harness(t, { entry });
  h.start(); h.error(); h.settle();
  h.tick(599_999); assert.deepEqual(h.sent, []);
  h.tick(1); assert.deepEqual(h.sent, ["continue"]);
});

for (const [label, errorMessage] of [
  ["exact concurrent permission_error", PERMISSION_ERROR],
  ["arbitrary", "unfamiliar provider failure"],
  ["empty", ""], ["missing", undefined], ["whitespace", "   "],
  ["null", null], ["non-string", {}],
  ["auth", "401 unauthorized"], ["quota", "quota exceeded"],
]) test(`without tmux, terminal assistant error waits until the exact ten-minute boundary: ${label}`, (t) => {
  const h = harness(t);
  assert.equal(process.env.TMUX, undefined);
  assert.equal(process.env.TMUX_PANE, undefined);
  assert.equal(h.handlers.get("message_end").length, 1, "default extension installs exactly one sender without tmux");
  assert.equal(h.inputs.size, 1, "only the recovery listener is installed without presentation");
  assert.match(h.diagnostics[0][0], /tmux presentation disabled; error recovery remains enabled/);
  h.start(); h.error(failure(errorMessage)); h.settle();
  h.tick(59_999); assert.deepEqual(h.sent, [], "no send at 59,999 ms");
  h.tick(1); assert.deepEqual(h.sent, [], "the former 60,000 ms deadline no longer sends");
  h.tick(539_999); assert.deepEqual(h.sent, [], "no send at 599,999 ms");
  h.tick(1);
  assert.deepEqual(h.sent, ["continue"]);
  assert.equal(h.sentAt[0], 1_600_000);
});

for (const mode of ["rpc", "json", "print"]) test(`recovery is independent of rendering mode: ${mode}`, (t) => {
  const h = harness(t, { mode });
  assert.equal(h.inputs.size, 0, "terminal listeners belong only to TUI");
  h.start(); h.error(); h.settle(); h.tick(600_000);
  assert.deepEqual(h.sent, ["continue"]);
});

test("child failures remain owned by the supervisor", (t) => {
  const h = harness(t, { depth: 1 });
  h.start(); h.error(); h.settle(); h.tick(6_000_000);
  assert.deepEqual(h.sent, []);
  assert.equal(h.inputs.size, 0);
});

test("loading/reloading ignores historical errors until a new terminal error is observed", (t) => {
  const h = harness(t);
  h.ctx.sessionManager = { getBranch: () => [{ type: "message", message: failure(PERMISSION_ERROR) }] };
  h.fire("session_start"); h.settle(); h.tick(6_000_000);
  assert.deepEqual(h.sent, [], "session history never arms a trigger");
  h.start(); h.error(); h.settle(); h.tick(600_000);
  assert.deepEqual(h.sent, ["continue"]);
});

test("agent_end and an expired error deadline cannot interrupt native retries", (t) => {
  const h = harness(t);
  h.start(); h.error();
  h.fire("agent_end");
  h.tick(1_200_000);
  assert.deepEqual(h.sent, [], "even idle-looking low-level end is not final settlement");
  h.settle(); h.tick(0);
  assert.deepEqual(h.sent, ["continue"], "settlement after the threshold needs no extra stall window");
});

test("native retry's latest terminal error owns the deadline", (t) => {
  const h = harness(t);
  h.start(); h.error(); h.tick(450_000);
  h.start(); h.error(failure("second failure")); h.settle();
  h.tick(599_999); assert.deepEqual(h.sent, []);
  h.tick(1); assert.deepEqual(h.sentAt, [2_050_000]);
});

test("repeated settlement only schedules one deadline and one send", (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle(); h.tick(300_000);
  h.settle(); h.settle(); h.tick(299_999); assert.deepEqual(h.sent, []);
  h.tick(1); assert.deepEqual(h.sent, ["continue"]);
  h.settle(); h.settle(); h.tick(0); assert.deepEqual(h.sent, ["continue"]);
  h.tick(599_999); assert.equal(h.sent.length, 1);
  h.tick(1); assert.equal(h.sent.length, 2);
});

test("only terminal assistant errors arm; unrelated tools never disarm a failure", (t) => {
  const h = harness(t);
  for (const message of [
    { role: "toolResult", isError: true, content: [{ type: "text", text: "Error: failed" }] },
    { role: "tool", stopReason: "error" },
    { role: "user", stopReason: "error" },
    { role: "assistant", stopReason: "aborted", errorMessage: "Operation aborted" },
    { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "Error: failed" }] },
  ]) {
    h.start(); h.error(message); h.settle(); h.tick(1_200_000);
    assert.deepEqual(h.sent, []);
  }
  h.start(); h.fire("message_update", { message: failure() }); h.settle(); h.tick(600_000);
  assert.deepEqual(h.sent, [], "only finalized errors start the clock");
  h.error();
  h.fire("message_end", { message: { role: "toolResult", isError: true } });
  h.fire("message_update", { message: { role: "tool", content: [{ type: "text", text: "ok" }] } });
  h.tick(600_000); assert.deepEqual(h.sent, ["continue"]);
});

test("busy and pending gates are checked both at settlement and deadline", (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle();
  h.idle = false; h.tick(600_000); assert.deepEqual(h.sent, []);
  h.idle = true; h.pending = true; h.tick(600_000); assert.deepEqual(h.sent, []);
  h.pending = false; h.settle(); h.tick(0); assert.deepEqual(h.sent, ["continue"]);
  h.start(); h.error(); h.settle(); h.pending = true;
  h.tick(600_000); assert.equal(h.sent.length, 1);
  h.pending = false; h.settle(); h.tick(0); assert.equal(h.sent.length, 2);
});

for (const gate of ["busy", "pending"]) test(`${gate} watchdog rechecks only after another full ten-minute stall`, (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle();
  if (gate === "busy") h.idle = false;
  else h.pending = true;
  h.tick(600_000); assert.deepEqual(h.sent, []);
  h.idle = true; h.pending = false;
  h.tick(599_999); assert.deepEqual(h.sent, [], "becoming available does not shorten the watchdog window");
  h.tick(1); assert.deepEqual(h.sentAt, [2_200_000]);
});

for (const progress of [
  { content: [{ type: "text", text: "recovered" }] },
  { content: [{ type: "thinking", thinking: "reasoning" }] },
  { content: [{ type: "toolCall", name: "bash" }] },
  { delta: { content: [{ type: "text", text: "ok" }] } },
]) test(`semantic assistant progress invalidates the stale error deadline: ${JSON.stringify(progress)}`, (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle(); h.tick(599_999);
  h.fire("message_update", { message: { role: "assistant", ...progress } });
  h.settle(); h.tick(6_000_000);
  assert.deepEqual(h.sent, []);
});

test("new native run cancels the timer; successful recovery invalidates the old error", (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle(); h.tick(599_999);
  h.start(); h.tick(600_000); assert.deepEqual(h.sent, []);
  h.fire("message_end", { message: { role: "assistant", stopReason: "toolUse", content: [] } });
  h.settle(); h.tick(6_000_000); assert.deepEqual(h.sent, []);
});

test("submitted input has priority but a swallowed input does not lose recovery", (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle(); h.tick(599_999);
  h.fire("input", { text: "user input", source: "interactive" });
  h.tick(59_999); assert.deepEqual(h.sent, []);
  h.tick(1); assert.deepEqual(h.sent, [], "submitted input keeps priority beyond the former one-minute window");
  h.tick(539_999); assert.deepEqual(h.sent, []);
  h.tick(1); assert.deepEqual(h.sentAt, [2_199_999], "swallowed input receives its own full ten-minute priority window");
  h.fire("input", { text: "next prompt", source: "extension" });
  h.start(); h.tick(1_200_000); assert.equal(h.sent.length, 1, "a real new run owns processing");
});

for (const abort of ["message", "settlement", "raw-escape", "sentinel"]) test(`intentional ${abort} cancels the timer and same-run late errors`, (t) => {
  const h = harness(t);
  const signal = h.start(); h.error(); h.settle(); h.tick(599_999);
  if (abort === "message") h.error({ role: "assistant", stopReason: "aborted", errorMessage: "Operation aborted" });
  else if (abort === "settlement") h.settle(true);
  else assert.deepEqual(h.terminal(abort === "sentinel" ? CANCEL_SENTINEL : "\x1b"), abort === "sentinel" ? { consume: true } : undefined);
  h.error(); h.settle(false); h.tick(6_000_000);
  assert.deepEqual(h.sent, [], "also covers Pi 1.4 retry Esc that settles aborted:false");
  assert.ok(signal);
  h.start(); h.error(); h.settle(); h.tick(600_000);
  assert.deepEqual(h.sent, ["continue"], "a distinct uncancelled run may recover");
});

test("late errors from a cancelled run cannot arm after a different run has started", (t) => {
  const h = harness(t);
  const signal = h.start(), cancelledCtx = { ...h.ctx, signal };
  h.error(); h.terminal(CANCEL_SENTINEL);
  h.start();
  h.fire("message_update", { message: failure() }, cancelledCtx);
  h.fire("message_end", { message: failure() }, cancelledCtx);
  h.settle(); h.tick(6_000_000); assert.deepEqual(h.sent, []);
  h.error(); h.settle(); h.tick(600_000);
  assert.deepEqual(h.sent, ["continue"], "only a genuine error from the new run may renew recovery");
});

test("shared pane cancellation carries the run signal even if another listener consumed sentinel", (t) => {
  const h = harness(t);
  const signal = h.start(); h.error(); h.settle();
  h.pi.events.emit(RECOVERY_CANCEL_EVENT, { signal });
  h.error(); h.settle(); h.tick(6_000_000); assert.deepEqual(h.sent, []);
});

test("legacy retry-exhausted aborted tails preserve an existing error, never arm alone", (t) => {
  const h = harness(t);
  const tail = { role: "assistant", stopReason: "aborted", errorMessage: "Aborted after 5 retry attempts" };
  h.start(); h.error(tail); h.settle(); h.tick(600_000); assert.deepEqual(h.sent, []);
  h.start(); h.error(); h.tick(200_000); h.error(tail); h.settle();
  h.tick(399_999); assert.deepEqual(h.sent, []);
  h.tick(1); assert.deepEqual(h.sent, ["continue"]);
  h.start(); h.error(); h.error(tail); h.settle(true); h.tick(6_000_000);
  assert.equal(h.sent.length, 1, "explicit aborted settlement still wins");
});

test("every new terminal error renews three attempts, including after an automatic continue", (t) => {
  const h = harness(t);
  for (let n = 1; n <= 7; n++) {
    h.start(); h.error(); h.settle();
    h.tick(599_999); assert.equal(h.sent.length, n - 1);
    h.tick(1); assert.equal(h.sent.length, n, "new errors intentionally allow more than three sends in total");
    if (n > 1) assert.equal(h.sentAt[n - 1] - h.sentAt[n - 2], 600_000);
  }
  // No new error: only the remaining two attempts belong to this trigger.
  for (let n = 0; n < 2; n++) h.tick(600_000);
  assert.equal(h.sent.length, 9);
  h.tick(6_000_000); assert.equal(h.sent.length, 9, "the latest trigger still has a three-send cap");
  h.terminal(CANCEL_SENTINEL);
  h.start(); h.error(); h.settle(); h.tick(600_000);
  assert.equal(h.sent.length, 10, "a genuinely new error starts a new trigger after cancellation too");
});

test("message_update and duplicate message_end cannot renew an exhausted trigger", (t) => {
  const h = harness(t);
  const message = failure();
  h.start(); h.error(message); h.settle();
  for (let n = 0; n < 3; n++) h.tick(600_000);
  assert.equal(h.sent.length, 3);
  h.fire("message_update", { message }); h.error(message); h.settle();
  h.tick(6_000_000); assert.equal(h.sent.length, 3);
  h.error(failure()); h.settle();
  for (let n = 1; n <= 3; n++) {
    h.tick(599_999); assert.equal(h.sent.length, 3 + n - 1);
    h.tick(1); assert.equal(h.sent.length, 3 + n, "a new terminal message, even with identical text, renews all three attempts");
  }
  h.tick(6_000_000); assert.equal(h.sent.length, 6, "the renewed trigger also stops before a fourth send");
});

for (const invalidation of ["normal completion", "model selection"]) test(`${invalidation} never bypasses the ten-minute send throttle`, (t) => {
  const h = harness(t);
  h.start(); h.error(); h.settle(); h.tick(600_000);
  if (invalidation === "model selection") h.fire("model_select");
  else h.error({ role: "assistant", stopReason: "stop", content: [] });
  h.start(); h.error(); h.tick(600_000); // Deadline expires while native run is busy.
  h.settle(); h.tick(0); assert.equal(h.sent.length, 2);
  h.fire("agent_settled", { aborted: false }); h.tick(599_999); assert.equal(h.sent.length, 2);
  h.tick(1); assert.equal(h.sent.length, 3);
  assert.deepEqual(h.sentAt, [1_600_000, 2_200_000, 2_800_000]);
});

test("a send consumed without a run is retried by one bounded watchdog, not a tight loop", (t) => {
  let h;
  h = harness(t, { send: () => h.fire("input", { text: "continue", source: "extension" }) });
  h.start(); h.error(); h.settle();
  for (let n = 1; n <= 3; n++) { h.tick(599_999); assert.equal(h.sent.length, n - 1); h.tick(1); assert.equal(h.sent.length, n); }
  h.tick(600_000); assert.equal(h.sent.length, 3, "no fourth send at the next deadline");
  h.tick(6_000_000); assert.equal(h.sent.length, 3);
  assert.deepEqual(h.sentAt, [1_600_000, 2_200_000, 2_800_000]);
});

test("a synchronous send failure is surfaced and remains capped/throttled", (t) => {
  const h = harness(t, { send: () => { throw new Error("delivery rejected"); } });
  const errors = h.diagnostics;
  errors.length = 0; // Ignore the expected non-tmux presentation diagnostic.
  h.start(); h.error(); h.settle();
  for (let n = 1; n <= 3; n++) h.tick(600_000);
  h.tick(6_000_000);
  assert.equal(h.sent.length, 3);
  assert.equal(errors.length, 3);
  assert.match(errors[0][0], /could not send continue/);
});

test("shutdown/restart disposes listeners and deadlines; expired contexts are surfaced", (t) => {
  const h = harness(t);
  const errors = h.diagnostics;
  errors.length = 0;
  h.start(); h.error(); h.settle(); h.fire("session_shutdown");
  assert.equal(h.inputs.size, 0);
  assert.equal(h.channels.get(RECOVERY_CANCEL_EVENT).size, 0);
  h.ctx.isIdle = () => { throw new Error("stale extension context"); };
  h.tick(6_000_000); assert.deepEqual(h.sent, []); assert.deepEqual(errors, []);
  h.fire("session_start"); h.start(); h.error(); h.settle(); h.tick(600_000);
  assert.deepEqual(h.sent, []);
  assert.equal(errors.length, 1);
  assert.match(errors[0][0], /context expired/);
  h.tick(6_000_000); assert.equal(errors.length, 1, "stale context cancels instead of repeatedly failing");
});
