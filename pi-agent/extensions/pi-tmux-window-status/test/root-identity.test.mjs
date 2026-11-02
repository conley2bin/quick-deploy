import assert from "node:assert/strict";
import { test } from "node:test";
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createInterface } from "node:readline";
import { nestedPublisherDisabled } from "../index.ts";
import * as recovery from "../recovery.mjs";

// Root-only identity. A child host runs async children in runner processes:
// the exec environment inherits TMUX/TMUX_PANE and carries PARENT_SESSION, and
// subagent-runner.js sets PI_SUBAGENT_CHILD=1 at module load, after exec and
// before any child session loads ambient extensions. Without that marker in the
// identity check, every runner child published leases into its parent's window.
const IDENTITY = ["PI_SUBAGENT_CHILD", "PI_SUBAGENT_DEPTH", "PI_SUBAGENT_PARENT_SESSION", "PI_SUBAGENT_MAX_DEPTH"];
const RUNNER_EXEC = { PI_SUBAGENT_PARENT_SESSION: "/home/tester/.pi/agent/sessions/root.jsonl", PI_ASYNC_NATIVE_RUNNER: "1" };
const RUNNER_CHILD_HOST = { ...RUNNER_EXEC, PI_SUBAGENT_CHILD: "1" };
const INDEX_URL = new URL("../index.ts", import.meta.url).href;
const REAL_TMUX = execFileSync("sh", ["-c", "command -v tmux"], { encoding: "utf8" }).trim();
const failure = (errorMessage = "429 Too Many Requests") => ({ role: "assistant", content: [], stopReason: "error", errorMessage });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const waitFor = async (read, expected, timeoutMs = 3_000) => {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) { if (read() === expected) return; await sleep(20); }
  assert.equal(read(), expected);
};
const rootEnv = (source = process.env) => Object.fromEntries(Object.entries(source).filter(([key]) => key !== "TMUX" && !key.startsWith("TMUX_") && !IDENTITY.includes(key)));

function fakePi(sessionFile) {
  const handlers = new Map(), channels = new Map(), inputs = new Set(), registrations = [], sent = [], sentAt = [];
  let signal;
  const ctx = {
    mode: "tui",
    get signal() { return signal; },
    isIdle: () => true,
    hasPendingMessages: () => false,
    sessionManager: { getSessionFile: () => sessionFile },
    ui: { onTerminalInput(fn) { inputs.add(fn); return () => inputs.delete(fn); } },
  };
  const pi = {
    on(name, fn) { registrations.push(`on:${name}`); if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); },
    events: {
      on(name, fn) { registrations.push(`events:${name}`); if (!channels.has(name)) channels.set(name, new Set()); channels.get(name).add(fn); return () => channels.get(name)?.delete(fn); },
      emit(name, data) { for (const fn of channels.get(name) || []) fn(data); },
    },
    sendUserMessage(text) { sent.push(text); sentAt.push(Date.now()); },
  };
  const fire = (name, event = {}) => { for (const fn of handlers.get(name) || []) fn(event, ctx); };
  return {
    pi, ctx, inputs, registrations, sent, sentAt, fire,
    start() { signal = new AbortController().signal; fire("agent_start"); },
    error(message = failure()) { fire("message_end", { message }); },
    say(text) { fire("message_update", { message: { role: "assistant", content: [{ type: "text", text }] } }); },
    settle(aborted = false) { fire("agent_settled", { aborted }); },
  };
}

let sequence = 0;
// One isolated tmux server and private runtime per case. The extension and its
// animator reach tmux through a PATH wrapper that records the caller's pid;
// fixture reads call the real binary directly.
async function paneFixture(t, label, identityEnv = {}) {
  // Short paths: atomicWrite names its temp file after the base64url lease path (255-byte name limit).
  const work = mkdtempSync(join(tmpdir(), "pri-"));
  const socket = join(work, "t.sock"), runtime = join(work, "rt"), asyncTemp = join(work, "a"), bin = join(work, "bin"), callLog = join(work, "calls.log");
  mkdirSync(bin);
  writeFileSync(join(bin, "tmux"), `#!/bin/sh\necho "$PPID $*" >> '${callLog}'\nexec '${REAL_TMUX}' "$@"\n`, { mode: 0o755 });
  const base = rootEnv();
  const tmux = (...args) => execFileSync(REAL_TMUX, ["-S", socket, ...args], { encoding: "utf8", env: base }).trim();
  tmux("-f", "/dev/null", "new-session", "-d", "-s", label, "-x", "80", "-y", "24");
  const [windowId, paneId] = tmux("display-message", "-p", "#{window_id}|#{pane_id}").split("|");
  const paneEnv = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime, PI_SUBAGENTS_TEMP_ROOT: asyncTemp, TMUX: `${socket},0,0`, TMUX_PANE: paneId, PATH: `${bin}:${base.PATH}` };
  const previous = { ...process.env };
  for (const key of Object.keys(process.env)) if (!(key in base)) delete process.env[key];
  Object.assign(process.env, paneEnv, identityEnv);
  const sessionFile = join(work, `${label}-root-session.jsonl`);
  const root = fakePi(sessionFile);
  const { default: entry } = await import(`../index.ts?root-identity-${label}-${++sequence}`);
  entry(root.pi);
  root.fire("session_start");
  const windowOption = (name) => tmux("show-options", "-wqv", "-t", windowId, name);
  const animatorAlive = () => { try { return execFileSync("pgrep", ["-f", `animator.mjs ${socket}`], { encoding: "utf8" }).trim() !== ""; } catch { return false; } };
  t.after(async () => {
    t.mock.timers.reset();
    root.fire("session_shutdown");
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    // Let the animator retire the window and exit before its server and runtime
    // disappear; a shutdown kick would otherwise recreate the runtime directory.
    try { await waitFor(() => windowOption("@quick_deploy_pi_error") + windowOption("@quick_deploy_pi_active"), "", 2_000); } catch {}
    try { await waitFor(animatorAlive, false, 5_000); } catch {}
    try { tmux("kill-server"); } catch {}
    rmSync(work, { recursive: true, force: true });
  });
  const runStatus = (runId, state) => { const dir = join(asyncTemp, "async-subagent-runs", runId); mkdirSync(dir, { recursive: true }); writeFileSync(join(dir, "status.json"), JSON.stringify({ runId, state, sessionId: sessionFile, lastUpdate: Date.now() })); return dir; };
  return {
    ...root, work, socket, windowId, paneId, sessionFile, base, paneEnv, windowOption,
    paneArmed: () => tmux("show-options", "-pqv", "-t", paneId, "@quick_deploy_pi_recovery_armed"),
    leases() {
      const dir = join(runtime, "quick-deploy", "pi-tmux-status"), found = [];
      let servers = []; try { servers = readdirSync(dir); } catch {}
      for (const server of servers) {
        let files = []; try { files = readdirSync(join(dir, server, "leases")); } catch {}
        // A lease can be retired between listing and reading; atomic renames never expose partial JSON.
        for (const file of files) if (file.endsWith(".json")) { try { found.push(JSON.parse(readFileSync(join(dir, server, "leases", file), "utf8"))); } catch (error) { if (error?.code !== "ENOENT") throw error; } }
      }
      return found;
    },
    tmuxCallsFrom(pid) { let lines = []; try { lines = readFileSync(callLog, "utf8").split("\n").filter(Boolean); } catch {} return lines.filter((line) => line.split(" ", 1)[0] === String(pid)); },
    // How pi-subagents reports a detached run to its parent root session.
    asyncStarted(runId) { root.pi.events.emit("subagent:async-started", { id: runId, asyncDir: runStatus(runId, "running"), sessionId: sessionFile }); },
    asyncComplete(runId) { runStatus(runId, "complete"); root.pi.events.emit("subagent:async-complete", { id: runId, sessionId: sessionFile }); },
  };
}

test("identity: the pi-subagents child-host marker or legacy depth marks a nested process; routing and limit config never do", () => {
  const nested = [RUNNER_CHILD_HOST, { PI_SUBAGENT_CHILD: "1" }, { PI_SUBAGENT_DEPTH: "1" }, { PI_SUBAGENT_DEPTH: "2", PI_SUBAGENT_MAX_DEPTH: "3" }];
  const roots = [{}, RUNNER_EXEC, { PI_SUBAGENT_PARENT_SESSION: "/inherited.jsonl" }, { PI_SUBAGENT_CHILD: "0" }, { PI_SUBAGENT_CHILD: "true" }, { PI_SUBAGENT_MAX_DEPTH: "3" }, { PI_SUBAGENT_DEPTH: "0" }, { PI_SUBAGENT_DEPTH: "" }, { PI_SUBAGENT_DEPTH: "deep" }, { PI_SUBAGENT_DEPTH: "Infinity" }];
  for (const env of nested) {
    assert.equal(nestedPublisherDisabled(env), true, `presentation: ${JSON.stringify(env)}`);
    assert.equal(recovery.isNestedSubagentProcess?.(env), true, `recovery: ${JSON.stringify(env)}`);
  }
  for (const env of roots) {
    assert.equal(nestedPublisherDisabled(env), false, `presentation: ${JSON.stringify(env)}`);
    assert.equal(recovery.isNestedSubagentProcess?.(env), false, `recovery: ${JSON.stringify(env)}`);
  }
});

test("AC1: a live child host (PI_SUBAGENT_CHILD=1) holding a terminal 429 in the parent's pane is silent, so the healthy root's window never turns red", async (t) => {
  const f = await paneFixture(t, "runner-429");
  assert.equal(f.registrations.filter((name) => name === "on:message_end").length, 2, "root installs recovery and presentation");
  assert.ok(f.tmuxCallsFrom(process.pid).length > 0, "root resolves its tmux identity");
  const runId = "runner-429-run";
  f.asyncStarted(runId);
  f.start(); f.say("working");
  await waitFor(() => f.windowOption("@quick_deploy_pi_active"), "1");
  // Runner child host: exec env exactly as /proc/<runner>/environ shows it; the
  // script imports the extension, then sets the marker as subagent-runner.js does.
  const script = join(f.work, "runner-child-host.mjs");
  writeFileSync(script, `import extension from ${JSON.stringify(INDEX_URL)};
import { mock } from "node:test";
const execHadChildMarker = process.env.PI_SUBAGENT_CHILD !== undefined;
process.env.PI_SUBAGENT_CHILD = "1";
mock.timers.enable({ apis: ["setTimeout", "Date"], now: Date.now() });
const handlers = new Map(), registrations = [], sent = [];
const ctx = { mode: "print", signal: new AbortController().signal, isIdle: () => true, hasPendingMessages: () => false, sessionManager: { getSessionFile: () => process.argv[2] } };
const pi = { on(name, fn) { registrations.push("on:" + name); if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); }, events: { on(name) { registrations.push("events:" + name); return () => {}; }, emit() {} }, sendUserMessage(text) { sent.push(text); } };
const fire = (name, event = {}) => { for (const fn of handlers.get(name) || []) fn(event, ctx); };
const error = { role: "assistant", content: [], stopReason: "error", errorMessage: "429 Too Many Requests" };
extension(pi); fire("session_start");
fire("agent_start"); fire("message_update", { message: error }); fire("message_end", { message: error }); fire("agent_settled", { aborted: false });
process.stdout.write(JSON.stringify({ pid: process.pid, execHadChildMarker, registrations }) + "\\n");
await new Promise((resolve) => { const hold = setInterval(() => { clearInterval(hold); resolve(); }, Number(process.argv[3])); });
mock.timers.tick(600_000); mock.timers.tick(28_800_000); mock.timers.reset();
fire("session_shutdown");
process.stdout.write(JSON.stringify({ sent }) + "\\n", () => process.exit(0));
`);
  const child = spawn(process.execPath, ["--experimental-strip-types", script, join(f.work, "run-0", "session.jsonl"), "1200"], {
    env: { ...f.base, ...f.paneEnv, ...RUNNER_EXEC, PI_SUBAGENT_PARENT_SESSION: f.sessionFile },
    stdio: ["ignore", "pipe", "inherit"],
  });
  const exited = new Promise((resolve) => child.once("exit", resolve));
  const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  const ready = JSON.parse((await lines.next()).value);
  const redSeen = new Set(), owners = new Set();
  for (const until = Date.now() + 900; Date.now() < until; await sleep(25)) {
    redSeen.add(f.windowOption("@quick_deploy_pi_error"));
    for (const lease of f.leases()) owners.add(`${lease.ownerId}:${lease.state}`);
  }
  const done = JSON.parse((await lines.next()).value);
  t.diagnostic(`child host: registrations=${ready.registrations.length} tmuxCalls=${f.tmuxCallsFrom(ready.pid).length} sent=${JSON.stringify(done.sent)} windowError=${JSON.stringify([...redSeen])} leases=${JSON.stringify([...owners].map((owner) => owner.replace(String(ready.pid), "<child>").replace(String(process.pid), "<root>")))}`);
  assert.equal(await exited, 0);
  assert.equal(ready.execHadChildMarker, false, "the marker is set after exec, as in the real runner");
  assert.deepEqual(ready.registrations, [], "the child host registers neither recovery nor presentation");
  assert.deepEqual(f.tmuxCallsFrom(ready.pid), [], "the child host never resolves tmux identity");
  assert.deepEqual(done.sent, [], "the child host never sends continue, even after ten minutes and eight hours");
  assert.deepEqual([...redSeen], [""], "the parent's window never turns red for the child's 429");
  assert.deepEqual([...owners], [`${process.pid}:${f.paneId}:active`], "only the root publishes, and it is active");
  const [lease] = f.leases();
  assert.deepEqual(lease.activeRunIds, [runId], "the root carries its child's activity");
  assert.equal(f.windowOption("@quick_deploy_pi_active"), "1");
  assert.equal(f.paneArmed(), "");
});

test("AC1: recovery imported before a child-host marker is set registers nothing and never sends; legacy depth stays silent", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  const previous = { ...process.env };
  t.after(() => { for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key]; Object.assign(process.env, previous); });
  for (const key of IDENTITY) delete process.env[key];
  for (const identityEnv of [RUNNER_CHILD_HOST, { PI_SUBAGENT_DEPTH: "1" }]) {
    Object.assign(process.env, identityEnv); // after this module (and recovery.mjs) was imported
    const child = fakePi("/child/run-0/session.jsonl");
    recovery.installErrorRecovery(child.pi);
    assert.deepEqual(child.registrations, [], JSON.stringify(identityEnv));
    child.fire("session_start"); child.start(); child.error(); child.settle();
    t.mock.timers.tick(600_000); t.mock.timers.tick(28_800_000);
    assert.deepEqual(child.sent, [], JSON.stringify(identityEnv));
    for (const key of IDENTITY) delete process.env[key];
  }
  // Positive control on the same imported module: cleared before registration, it is a root again.
  Object.assign(process.env, { PI_SUBAGENT_PARENT_SESSION: "/inherited.jsonl", PI_SUBAGENT_MAX_DEPTH: "3" });
  const root = fakePi("/root/session.jsonl");
  recovery.installErrorRecovery(root.pi);
  assert.ok(root.registrations.includes("on:message_end"));
  root.fire("session_start"); root.start(); root.error(); root.settle();
  const settledAt = Date.now();
  t.mock.timers.tick(599_999); assert.deepEqual(root.sent, []);
  t.mock.timers.tick(1); assert.deepEqual(root.sent, ["continue"]);
  assert.equal(root.sentAt[0], settledAt + 600_000);
  root.fire("session_shutdown");
});

for (const [label, identityEnv] of [
  ["inherited-parent-session", { PI_SUBAGENT_PARENT_SESSION: "/inherited/grandparent.jsonl" }],
  ["child-marker-zero", { PI_SUBAGENT_CHILD: "0" }],
  ["max-depth-only", { PI_SUBAGENT_MAX_DEPTH: "3" }],
]) test(`AC2: a root with ${label} keeps its error lease and sends exactly at ten minutes`, async (t) => {
  const f = await paneFixture(t, label, identityEnv);
  assert.equal(f.registrations.filter((name) => name === "on:message_end").length, 2);
  // The ten-minute stall is anchored at the terminal error, so the clock is virtual before it.
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: Date.now() });
  const erroredAt = Date.now();
  f.start(); f.error();
  assert.deepEqual(f.leases().map((lease) => `${lease.ownerId}:${lease.state}`), [`${process.pid}:${f.paneId}:error`]);
  f.settle();
  t.mock.timers.tick(599_999); assert.deepEqual(f.sent, []);
  t.mock.timers.tick(1); assert.deepEqual(f.sent, ["continue"]);
  assert.equal(f.sentAt[0], erroredAt + 600_000);
  assert.equal(f.leases()[0].state, "error", "sending continue keeps the root's red");
  t.mock.timers.reset();
});

test("AC2: a root's own terminal error stays red while its async child is active, and clears only on the root's semantic output", async (t) => {
  const f = await paneFixture(t, "root-error-active-child", { PI_SUBAGENT_PARENT_SESSION: "/inherited/grandparent.jsonl" });
  const runId = "healthy-child-run";
  f.asyncStarted(runId);
  assert.deepEqual(f.leases().map((lease) => [lease.state, lease.activeRunIds]), [["active", [runId]]]);
  await waitFor(() => f.windowOption("@quick_deploy_pi_active"), "1");
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: Date.now() });
  const erroredAt = Date.now();
  f.start(); f.error();
  assert.deepEqual(f.leases().map((lease) => [lease.state, lease.activeRunIds]), [["error", [runId]]], "error outranks the child's activity in the root's own lease");
  f.settle();
  t.mock.timers.tick(599_999); assert.deepEqual(f.sent, []);
  t.mock.timers.tick(1); assert.deepEqual(f.sent, ["continue"]);
  assert.equal(f.sentAt[0], erroredAt + 600_000);
  t.mock.timers.reset();
  await waitFor(() => f.windowOption("@quick_deploy_pi_error"), "1");
  await waitFor(f.paneArmed, "1");
  await sleep(300);
  assert.deepEqual(f.leases().map((lease) => [lease.state, lease.activeRunIds]), [["error", [runId]]], "an idle root with an active child keeps its latched error after sending");
  assert.equal(f.windowOption("@quick_deploy_pi_error"), "1");
  f.start(); f.say("resumed");
  assert.deepEqual(f.leases().map((lease) => [lease.state, lease.activeRunIds]), [["active", [runId]]]);
  await waitFor(() => f.windowOption("@quick_deploy_pi_error"), "");
  await waitFor(() => f.windowOption("@quick_deploy_pi_active"), "1");
  await waitFor(f.paneArmed, "");
  f.fire("message_end", { message: { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "done" }] } }); f.settle();
  assert.deepEqual(f.leases().map((lease) => [lease.state, lease.activeRunIds]), [["active", [runId]]], "an idle root still shows its running child");
  f.asyncComplete(runId);
  assert.deepEqual(f.leases(), []);
  await waitFor(() => f.windowOption("@quick_deploy_pi_active"), "");
});
