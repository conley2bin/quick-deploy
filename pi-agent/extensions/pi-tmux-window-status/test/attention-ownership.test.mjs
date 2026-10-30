import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// Exercise the live factory closure with real status/registry files. Only the
// external tmux/process/watch boundaries are inert; no production debug API.
async function harness(t) {
  const work = mkdtempSync(join(tmpdir(), "pi-attn-"));
  const previous = { ...process.env };
  delete process.env.PI_SUBAGENT_DEPTH;
  process.env.QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME = join(work, "rt");
  process.env.PI_SUBAGENTS_TEMP_ROOT = join(work, "async");
  const moduleRoot = new URL("../", import.meta.url);
  let source = readFileSync(new URL("index.ts", moduleRoot), "utf8");
  const replace = (from, to) => { assert.ok(source.includes(from), `instrumentation boundary: ${from}`); source = source.replace(from, to); };
  for (const file of ["state.mjs", "recovery.mjs", "animator.mjs"]) source = source.replaceAll(`"./${file}"`, JSON.stringify(new URL(file, moduleRoot).href));
  replace('import { execFileSync, spawn } from "node:child_process";', 'const spawn = () => ({ exitCode: null, signalCode: null, unref() {} });');
  replace('readFileSync, readdirSync, watch, type FSWatcher', 'readFileSync, readdirSync, type FSWatcher');
  source = 'const watch = (_path: string, callback: unknown) => ({ callback, unref() {}, close() {} });\n' + source;
  source = source.replace(/import \{ requestAnimatorTick \} from [^;]+;/, 'const requestAnimatorTick = () => false;');
  source = source.replace(/function identity\(\): Identity \{[^\n]+\}/, 'function identity(): Identity { return { socketPath: "/inert-attention.sock", windowId: "@0", paneId: "%0" }; }');
  assert.ok(source.endsWith("\n}\n"));
  source = source.slice(0, -3) + '\n  return { children, watchers, routeWatchers, attention, attentionOwners: typeof attentionOwners === "undefined" ? new Map() : attentionOwners, logical, sync, get monitor() { return Boolean(beat); } };\n}\n';
  const copy = join(work, "index.ts");
  writeFileSync(copy, source);
  const handlers = new Map(), channels = new Map(), diagnostics = [];
  t.mock.method(console, "error", (...args) => diagnostics.push(args.join(" ")));
  const pi = {
    on(name, fn) { if (!handlers.has(name)) handlers.set(name, []); handlers.get(name).push(fn); },
    events: {
      on(name, fn) { if (!channels.has(name)) channels.set(name, new Set()); channels.get(name).add(fn); return () => channels.get(name).delete(fn); },
      emit(name, event) { for (const fn of channels.get(name) || []) fn(event); },
    },
    sendUserMessage() { throw new Error("unexpected recovery send"); },
  };
  const sessionId = "/attention/session.jsonl";
  const ctx = { mode: "rpc", sessionManager: { getSessionFile: () => sessionId }, isIdle: () => true, hasPendingMessages: () => false };
  const fire = (name) => { for (const fn of handlers.get(name) || []) fn({}, ctx); };
  const { default: entry } = await import(pathToFileURL(copy).href);
  const state = entry(pi);
  fire("session_start");
  t.after(() => {
    // Restore any deliberately faulted watcher before ordinary fixture cleanup.
    for (const watcher of [...state.watchers.values(), ...state.routeWatchers.values()]) watcher.close = () => {};
    fire("session_shutdown");
    for (const key of Object.keys(process.env)) if (!(key in previous)) delete process.env[key];
    Object.assign(process.env, previous);
    rmSync(work, { recursive: true, force: true });
  });
  const roots = join(process.env.PI_SUBAGENTS_TEMP_ROOT, "async-subagent-runs");
  const root = (id, nodes, { indexed = false, routed = true } = {}) => {
    const dir = join(roots, id), registryDir = join(dir, "nested");
    mkdirSync(registryDir, { recursive: true });
    const route = { rootRunId: id, eventSink: join(registryDir, "events.jsonl"), controlInbox: join(registryDir, "control.jsonl"), capabilityToken: "inert-fixture-token" };
    const status = (children, state = "running") => writeFileSync(join(dir, "status.json"), JSON.stringify({ runId: id, state, sessionId, ...(routed ? { nestedRoute: route } : {}), children }));
    const registry = (children, rootRunId = id) => writeFileSync(join(registryDir, "registry.json"), JSON.stringify({ rootRunId, children }));
    status(nodes); registry(nodes);
    if (indexed) { mkdirSync(join(roots, ".active-runs"), { recursive: true }); writeFileSync(join(roots, ".active-runs", id), ""); }
    pi.events.emit("subagent:async-started", { id, asyncDir: dir, sessionId, ...(routed ? { nestedRoute: route } : {}) });
    return {
      id, dir, route, status, registry,
      complete() { status([], "complete"); pi.events.emit("subagent:async-complete", { id, sessionId }); },
      refresh() { state.routeWatchers.get(id)?.callback("change", "registry.json"); },
      retire() { registry([]); this.refresh(); },
    };
  };
  const counts = () => ({ attention: state.attention.size, owners: state.attentionOwners.size, ownedIds: [...state.attentionOwners.values()].reduce((n, ids) => n + ids.size, 0) });
  const control = (runId) => pi.events.emit("subagent:control-event", { event: { type: "needs_attention", runId } });
  return { state, root, fire, counts, control, channels, diagnostics, pi };
}
const node = (id, attention = true, mark = 100) => ({ id, state: "running", lastActivityAt: mark, ...(attention ? { activityState: "needs_attention" } : {}) });
const zero = { attention: 0, owners: 0, ownedIds: 0 };

test("50 live-parent cycles release former descendant watermarks and ownership after authoritative retirement", async (t) => {
  const h = await harness(t), observations = [];
  for (let i = 0; i < 50; i++) {
    const leaf = `leaf-${i}`, seeded = i % 2 === 0;
    const root = h.root(`root-${i}`, [node(leaf, seeded)]);
    if (!seeded) h.control(leaf);
    root.complete();
    assert.equal(h.state.attention.has(leaf), true);
    root.retire(); // refresh replaces the projection BEFORE closeChild.
    assert.equal(h.state.children.size, 0);
    assert.equal(h.state.watchers.size + h.state.routeWatchers.size, 0);
    assert.equal(h.state.monitor, false);
    observations.push(h.counts());
  }
  t.diagnostic(`50 cycles; final ${JSON.stringify(h.counts())}; children/watchers/monitor=0/0/false`);
  assert.deepEqual(observations, Array.from({ length: 50 }, () => zero), "every live-parent cycle retains no retired IDs");
});

test("projection omission while resumable preserves acknowledgement and later activity recovery", async (t) => {
  const h = await harness(t), root = h.root("resumable", [node("waiting")]);
  root.complete();
  assert.equal(h.state.logical().active, false);
  root.registry([node("sibling", false)]); root.refresh();
  assert.equal(h.state.children.size, 1);
  assert.equal(h.state.attention.get("waiting"), 100);
  root.registry([node("waiting")]); root.refresh();
  assert.equal(h.state.logical().active, false, "returning stale marker remains acknowledged");
  root.registry([node("waiting", true, 101)]); root.refresh();
  assert.equal(h.state.logical().active, true, "new activity reactivates despite the old marker");
  root.retire();
  assert.deepEqual(h.counts(), zero);
});

for (const mode of ["snapshot", "control"]) test(`shared ID ${mode} ownership survives first root retirement and ends at last owner`, async (t) => {
  const h = await harness(t);
  const first = h.root("first", [node("shared", false)]);
  const last = h.root("last", [node("shared", mode === "snapshot")]);
  if (mode === "control") h.control("shared");
  assert.deepEqual(h.counts(), { attention: 1, owners: 2, ownedIds: 2 }, "later seeding associates earlier roots too");
  first.complete(); first.retire();
  assert.equal(h.state.attention.has("shared"), true);
  assert.deepEqual(h.counts(), { attention: 1, owners: 1, ownedIds: 1 });
  last.complete(); last.retire();
  assert.deepEqual(h.counts(), zero);
});

test("active-index restoration replacing Child objects retains historical ownership", async (t) => {
  const h = await harness(t), root = h.root("restored", [node("history", false)], { indexed: true });
  h.control("history");
  const before = h.state.children.get(root.id);
  root.status([node("replacement", false)]);
  h.fire("agent_start");
  assert.notEqual(h.state.children.get(root.id), before, "real restore replaced the Child object");
  assert.equal(h.state.attention.has("history"), true);
  root.complete(); root.retire(); h.fire("agent_settled");
  assert.deepEqual(h.counts(), zero);
});

test("successful-node churn and foreign or unattributable events grow no ownership metadata", async (t) => {
  const h = await harness(t), root = h.root("quiet", [node("success-0", false)]);
  for (let i = 1; i <= 50; i++) {
    root.status([node(`success-${i}`, false)]); h.state.sync();
    h.control(`unknown-${i}`);
    h.pi.events.emit("subagent:async-started", { id: `foreign-${i}`, asyncDir: root.dir, sessionId: "/other/session" });
    h.pi.events.emit("subagent:async-complete", { id: root.id, sessionId: "/other/session" });
    assert.deepEqual(h.counts(), zero);
  }
  assert.equal(h.state.children.size, 1);
  root.complete(); root.retire();
  assert.equal(h.state.children.size, 0);
});

test("untrusted or unreadable registry retains acknowledgement failure-closed until shutdown", async (t) => {
  const h = await harness(t), root = h.root("unresolved", [node("held")]);
  root.complete();
  for (const contents of ["{", JSON.stringify({ rootRunId: "foreign", children: [] })]) {
    writeFileSync(join(root.dir, "nested", "registry.json"), contents); root.refresh();
    assert.equal(h.state.children.size, 1);
    assert.equal(h.state.attention.has("held"), true);
  }
  const queuedCallback = h.state.routeWatchers.get(root.id).callback;
  h.fire("session_shutdown");
  assert.deepEqual(h.counts(), zero);
  assert.equal(h.state.children.size + h.state.watchers.size + h.state.routeWatchers.size, 0);
  assert.equal([...h.channels.values()].reduce((n, callbacks) => n + callbacks.size, 0), 0);
  queuedCallback("change", "registry.json"); // queued callbacks cannot recreate children.
  h.control("held");
  assert.deepEqual(h.counts(), zero);
});

test("real retirement releases attention before a watcher close fault", async (t) => {
  const h = await harness(t), root = h.root("fault", [node("retired-before-close")]);
  root.complete();
  h.state.watchers.get(root.id).close = () => { throw new Error("fixture watcher fault"); };
  assert.throws(() => root.retire(), /fixture watcher fault/);
  assert.equal(h.state.children.size, 0);
  assert.deepEqual(h.counts(), zero);
});

test("shutdown clears watermark ownership even when watcher cleanup throws", async (t) => {
  const h = await harness(t), root = h.root("shutdown-fault", [node("shutdown-held")]);
  assert.deepEqual(h.counts(), { attention: 1, owners: 1, ownedIds: 1 });
  h.state.watchers.get(root.id).close = () => { throw new Error("fixture shutdown close fault"); };
  assert.throws(() => h.fire("session_shutdown"), /fixture shutdown close fault/);
  assert.deepEqual(h.counts(), zero);
  assert.equal(h.state.children.size, 0);
});

test("completion without a trusted sidecar keeps resumable descendants until shutdown", async (t) => {
  const h = await harness(t), root = h.root("no-sidecar", [node("sidecar-held")], { routed: false });
  // Completion's nestedChildren fallback cannot authoritatively retire descendants.
  root.status([], "complete");
  h.pi.events.emit("subagent:async-complete", { id: root.id, sessionId: "/attention/session.jsonl", nestedChildren: [node("sidecar-held")] });
  assert.equal(h.state.children.size, 1);
  assert.equal(h.state.attention.get("sidecar-held"), 100);
  assert.equal(h.state.logical().active, false);
  assert.ok(h.diagnostics.some((line) => line.includes("no authoritative sidecar")));
  h.fire("session_shutdown");
  assert.deepEqual(h.counts(), zero);
});
