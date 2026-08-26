import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { HEARTBEAT_MS, LEASE_TTL_MS, acquireAnimatorLock, aggregateLogicalState, leasePath, projectAsyncStatus, publishLease, readLease, releaseAnimatorLock, seedAttentionWatermarksFromSnapshot, windowLeaseStates } from "../state.mjs";
import { animatorSpawnNeeded, childFromStartedPayload, childStillRunning, classifyModelUnavailable, defaultAsyncRoot, mergedChildSnapshot, monitorNeeded, nestedProjectionFromChildren, nestedPublisherDisabled, nextModelErrorState, readNestedRegistryProjection, restoredChildren, sessionIdOf, validateNestedRoute } from "../index.ts";
import { BLUE_RANGE, CURRENT_FRAMES, ERROR_BG, ERROR_FG, ERROR_MS, FRAME_COUNT, FRAME_MS, FRAMES, GRAY_RANGE, PERIOD_MS, activeWindows, frameAt, isDirectExecution, leaseWindowStates, listClients, runAnimator, sweepWindows, windowOptionArgs } from "../animator.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../../../..");
const trash = [];
const temp = () => { const d = mkdtempSync(join(tmpdir(), "pi-tmux-status-")); trash.push(d); return d; };
afterEach(() => { while (trash.length) rmSync(trash.pop(), { recursive: true, force: true }); });
const ident = (socket = "/tmp/tmux-status") => ({ socketPath: socket, windowId: "@8", paneId: "%9" });
const real = (runId = "root", lastActivityAt = 100, state = "running", steps = []) => ({ runId, state, lastUpdate: lastActivityAt, steps });

test("24-frame palettes start at idle baseline and hit symmetric perceptual ranges", () => {
  assert.equal(FRAMES.length, FRAME_COUNT);
  assert.equal(CURRENT_FRAMES.length, FRAME_COUNT);
  assert.equal(FRAME_MS, 60);
  assert.equal(PERIOD_MS, 1440);
  assert.equal(FRAMES[0], GRAY_RANGE.idle);
  assert.equal(FRAMES[6], GRAY_RANGE.bright);
  assert.equal(FRAMES[12], GRAY_RANGE.idle);
  assert.equal(FRAMES[18], GRAY_RANGE.dark);
  assert.equal(CURRENT_FRAMES[0], BLUE_RANGE.idle);
  assert.equal(CURRENT_FRAMES[6], BLUE_RANGE.bright);
  assert.equal(CURRENT_FRAMES[12], BLUE_RANGE.idle);
  assert.equal(CURRENT_FRAMES[18], BLUE_RANGE.dark);
});

test("monotonic frame phase wraps and skips delayed callbacks", () => {
  assert.equal(frameAt(0), 0);
  assert.equal(frameAt(59), 0);
  assert.equal(frameAt(60), 1);
  assert.equal(frameAt(60 * 10 + 17), 10);
  assert.equal(frameAt(PERIOD_MS + 60 * 3 + 1), 3);
  assert.equal(frameAt(-1), 23);
});

test("nested child publishers are disabled while root publishers remain enabled", () => {
  assert.equal(nestedPublisherDisabled({ PI_SUBAGENT_DEPTH: "1" }), true);
  assert.equal(nestedPublisherDisabled({ PI_SUBAGENT_DEPTH: "0" }), false);
  assert.equal(nestedPublisherDisabled({}), false);
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
  const d = temp(), link = join(d, "animator.mjs"), missing = join(d, "missing.mjs"), target = join(ROOT, "pi-agent/extensions/quick-deploy-tmux-status/animator.mjs");
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
  const runtime = temp(), i = ident(), env = { QUICK_DEPLOY_TMUX_STATUS_RUNTIME: runtime }, file = leasePath(i, "owner", env);
  publishLease(i, "owner", true, 1, env, { parentSessionId: "session-z", activeRunIds: ["b", "a", "a"], activeNodeIds: ["n2", "n1"] });
  const lease = JSON.parse(readFileSync(file, "utf8"));
  assert.deepEqual(lease.activeRunIds, ["a", "b"]);
  assert.deepEqual(lease.activeNodeIds, ["n1", "n2"]);
  assert.equal(lease.parentSessionId, "session-z");
  for (const forbidden of ["prompt", "history", "cwd", "name", "windowName"]) assert.equal(Object.hasOwn(lease, forbidden), false);
});

test("invalid lease residue is removed while legacy missing-state leases stay active", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_TMUX_STATUS_RUNTIME: runtime }, i = ident(), dir = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"), "leases");
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
  const runtime = temp(), i = ident(), env = { QUICK_DEPLOY_TMUX_STATUS_RUNTIME: runtime }, file = leasePath(i, "owner", env);
  for (let t = 0; t <= LEASE_TTL_MS * 3; t += HEARTBEAT_MS) {
    publishLease(i, "owner", true, t, env);
    assert.ok(readLease(file, t + HEARTBEAT_MS - 1));
  }
  publishLease(i, "owner", false, LEASE_TTL_MS * 3 + 1, env);
  assert.equal(readLease(file, LEASE_TTL_MS * 3 + 2), undefined);
});

test("multiple owners aggregate active/error leases with backcompat and expiry", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_TMUX_STATUS_RUNTIME: runtime }, i = ident();
  publishLease(i, "legacy-active", true, 1, env);
  const legacyPath = leasePath(i, "legacy-missing-state", env);
  writeFileSync(legacyPath, JSON.stringify({ version: 1, ownerId: "legacy-missing-state", socketPath: i.socketPath, windowId: "@10", paneId: "%11", heartbeatAt: 1 }));
  publishLease({ ...i, paneId: "%10" }, "error", "error", 1, env, { activeRunIds: ["r"], activeNodeIds: ["n"] });
  const root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  const states = windowLeaseStates(root, 2);
  assert.deepEqual([...states.error], ["@8"], "error wins over active owners in the same window");
  assert.deepEqual([...states.active], ["@10"], "legacy lease without state remains active for rolling compatibility");
  assert.deepEqual([...activeWindows(root, 2)], ["@10"]);
  const lease = readLease(leasePath(i, "error", env), 2);
  assert.equal(lease.state, "error");
  assert.equal(JSON.stringify(lease).includes("No deployments"), false, "raw provider text is not stored in error lease");
  publishLease({ ...i, windowId: "@9" }, "active", "active", 2, env);
  const mixed = leaseWindowStates(root, 3);
  assert.deepEqual([...mixed.error], ["@8"]);
  assert.deepEqual([...mixed.active], ["@9", "@10"]);
  assert.equal(windowLeaseStates(root, LEASE_TTL_MS + 2).error.size, 0);
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
  const held = acquireAnimatorLock(lock, "live", 111);
  assert.ok(held.owner);
  const live = JSON.parse(readFileSync(lock));
  assert.equal(live.token, "live", "real lock file is complete JSON");
  assert.equal(live.pid, 111);
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

test("animator batches active/error transitions, cadences, resets, and cached clients", () => {
  const runtime = temp(), env = { QUICK_DEPLOY_TMUX_STATUS_RUNTIME: runtime }, i = ident("/tmp/socket"), root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(i.socketPath).toString("base64url"));
  publishLease(i, "a", "active", 100, env);
  const calls = [], delays = [];
  let mono = 0, wall = 100, listCount = 0;
  const exec = (_tmux, args) => { calls.push(args); if (args.includes("list-clients")) { listCount++; if (listCount === 2) { const e = new Error("stale"); e.status = 1; throw e; } return "c1\nc2\n"; } if (args.includes("list-windows")) return "@8\n@9\n"; return ""; };
  const a = runAnimator({ socketPath: i.socketPath, root, now: () => mono, wallNow: () => wall, intervalMs: FRAME_MS, errorIntervalMs: ERROR_MS, exec, schedule: (_fn, delay) => { delays.push(delay); return { unref() {} }; }, cancel: () => {} });
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
  wall = 1300; a.tick();
  assert.ok(calls.some((args) => args.includes("-uw") && args.includes("@quick_deploy_pi_error")));
  assert.equal(acquireAnimatorLock(join(root, "animator.lock")).owner, true);
});

test("detached animator process remains alive for later 60ms frames and exits after lease removal", async () => {
  const runtime = temp(), work = temp(), socket = join(work, "server.sock");
  const tmux = (args, options = {}) => execFileSync("tmux", args, { ...options });
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", "animator-process"]);
  const windowId = tmux(["-S", socket, "display-message", "-p", "#{window_id}"], { encoding: "utf8" }).trim();
  const paneId = tmux(["-S", socket, "display-message", "-p", "#{pane_id}"], { encoding: "utf8" }).trim();
  const i = { socketPath: socket, windowId, paneId }, env = { QUICK_DEPLOY_TMUX_STATUS_RUNTIME: runtime };
  publishLease(i, "process", "active", Date.now(), env);
  const animator = spawn(process.execPath, [join(ROOT, "pi-agent/extensions/quick-deploy-tmux-status/animator.mjs"), socket], { env: { ...process.env, ...env }, stdio: "ignore" });
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
  assert.ok(args.includes(CURRENT_FRAMES[6]));
  assert.ok(args.includes("@quick_deploy_pi_error"));
});

test("installer is executable, idempotent, and preserves foreign paths", () => {
  const d = temp(), source = join(d, "source"), home = join(d, "home");
  mkdirSync(source, { recursive: true });
  writeFileSync(join(source, "index.ts"), "");
  const installer = join(ROOT, "fresh-install/modules/tmux/install-pi-tmux-status.sh"), env = { ...process.env, QUICK_DEPLOY_PI_TMUX_STATUS_SOURCE: source, QUICK_DEPLOY_PI_HOME: join(home, ".pi", "agent") };
  assert.equal(spawnSync("bash", [installer], { env }).status, 0);
  assert.equal(spawnSync("bash", [installer], { env }).status, 0);
  const target = join(env.QUICK_DEPLOY_PI_HOME, "extensions", "quick-deploy-tmux-status");
  rmSync(target);
  mkdirSync(target);
  writeFileSync(join(target, "keep"), "yes");
  assert.notEqual(spawnSync("bash", [installer], { env }).status, 0);
  assert.equal(readFileSync(join(target, "keep"), "utf8"), "yes");
});

test("isolated gpakosz load evaluates actual deployed formats across idle and two active frames", () => {
  const work = temp(), socket = join(work, "server.sock");
  const tmux = (args, options = {}) => execFileSync("tmux", args, { cwd: work, ...options });
  tmux(["-S", socket, "-f", join(process.env.HOME, ".tmux.conf"), "new-session", "-d", "-s", "q"]);
  try {
    const idle = tmux(["-S", socket, "show-options", "-gqv", "window-status-format"], { encoding: "utf8" });
    const current = tmux(["-S", socket, "show-options", "-gqv", "window-status-current-format"], { encoding: "utf8" });
    const evalf = (f) => tmux(["-S", socket, "display-message", "-p", f], { encoding: "utf8" });
    assert.match(evalf(idle), /#bcbcbc/);
    assert.match(evalf(current), /#00afff/);
    assert.match(evalf(idle), /#080808/);
    assert.doesNotMatch(idle + current, /#\(/);
    tmux(["-S", socket, "set-option", "-w", "@quick_deploy_pi_error", "1"]);
    assert.match(evalf(idle), new RegExp(ERROR_BG));
    assert.match(evalf(idle), new RegExp(ERROR_FG));
    assert.match(evalf(current), new RegExp(ERROR_BG));
    assert.match(evalf(current), new RegExp(ERROR_FG));
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
    }
    tmux(["-S", socket, "set-option", "-uw", "@quick_deploy_pi_active"]);
    tmux(["-S", socket, "set-option", "-uw", "@quick_deploy_pi_bg"]);
    assert.match(evalf(idle), /#bcbcbc/);
  } finally {
    tmux(["-S", socket, "kill-session", "-t", "q"]);
  }
});
