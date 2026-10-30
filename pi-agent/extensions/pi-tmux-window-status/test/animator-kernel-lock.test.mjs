import assert from "node:assert/strict";
import { once } from "node:events";
import { afterEach, test } from "node:test";
import net from "node:net";
import { fork, spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";
import { acquireAnimatorKernelLock, animatorKernelName, publishLease, releaseAnimatorKernelLock } from "../state.mjs";
import { runAnimator } from "../animator.mjs";

const HERE = dirname(fileURLToPath(import.meta.url)), SRC = join(HERE, ".."), WORKER = join(HERE, "animator-lock-worker.mjs");
const trash = [], pids = new Set(), children = new Set();
const temp = () => { const d = mkdtempSync(join(tmpdir(), "pi-animator-kernel-")); trash.push(d); return d; };
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch (error) { return error.code === "EPERM"; } };
const servers = () => process._getActiveHandles().filter((handle) => handle instanceof net.Server && handle._handle).length;
const inertUrgent = () => () => {};
afterEach(async () => {
  for (const child of children) try { child.kill("SIGKILL"); } catch {}
  for (const pid of pids) try { process.kill(pid, "SIGKILL"); } catch {}
  for (const child of children) if (child.exitCode === null && child.signalCode === null) await once(child, "exit");
  // SIGKILLed orphans are reaped asynchronously by init; give that a bounded moment before declaring a leak.
  for (const deadline = Date.now() + 2_000; [...pids].some(alive) && Date.now() < deadline;) await new Promise((done) => setTimeout(done, 10));
  const leaked = [...pids].filter(alive); children.clear(); pids.clear();
  while (trash.length) rmSync(trash.pop(), { recursive: true, force: true });
  assert.deepEqual(leaked, [], "no owned fixture pid survives its test");
});

function arena() {
  const dir = temp(), socketPath = join(dir, `${randomUUID()}.sock`), lockPath = join(dir, "animator.lock");
  const next = (child) => once(child, "message", { signal: AbortSignal.timeout(8_000) }).then(([message]) => message);
  const worker = async (round = "go") => {
    const child = fork(WORKER, [], { env: { ...process.env, CFG: JSON.stringify({ socketPath, lockPath, go: join(dir, round) }) } });
    children.add(child); pids.add(child.pid); await next(child); return child;
  };
  const claim = async (workers, round = "go") => {
    const replies = workers.map(next);
    workers.forEach((child, index) => child.send({ cmd: "claim", token: `t${index}-${randomUUID()}` }));
    writeFileSync(join(dir, round), "");
    return Promise.all(replies);
  };
  const meta = () => JSON.parse(readFileSync(lockPath, "utf8"));
  return { dir, socketPath, lockPath, next, worker, claim, meta };
}

for (const [label, seed] of [["corrupt", "{garbage"], ["dead legacy pid", JSON.stringify({ pid: 2_147_483_646, token: "dead" })], ["dead v2 pid", JSON.stringify({ v: 2, pid: 2_147_483_646, token: "dead" })]]) test(`four barrier contenders over ${label} metadata admit exactly one owner`, async () => {
  const a = arena(); writeFileSync(a.lockPath, seed);
  const workers = await Promise.all([1, 2, 3, 4].map(() => a.worker()));
  const replies = await a.claim(workers);
  const owners = replies.filter((reply) => reply.owner);
  assert.equal(owners.length, 1);
  assert.deepEqual(replies.filter((reply) => !reply.owner).map((reply) => reply.reason), ["held", "held", "held"]);
  assert.deepEqual({ ...a.meta() }, { v: 2, pid: owners[0].pid, token: owners[0].token });
});

test("metadata unlink or corruption while the owner is live admits zero second owners", async () => {
  const a = arena();
  const [owner] = await Promise.all([a.worker()]);
  assert.equal((await a.claim([owner]))[0].owner, true);
  for (const tamper of [() => rmSync(a.lockPath), () => writeFileSync(a.lockPath, "{garbage"), () => writeFileSync(a.lockPath, JSON.stringify({ v: 2, pid: 2_147_483_646, token: "forged" }))]) {
    tamper();
    const challenger = await a.worker("go-" + randomUUID());
    const [reply] = await a.claim([challenger], "go-" + randomUUID());
    assert.equal(reply.owner, false, "kernel guard, not metadata, decides ownership");
  }
});

test("SIGKILL of the owner with a surviving exec descendant releases the guard for exactly one of three successors", async () => {
  const a = arena();
  const [owner] = await Promise.all([a.worker()]);
  assert.equal((await a.claim([owner]))[0].owner, true);
  const marker = `pi-animator-descendant-${randomUUID()}`;
  owner.send({ cmd: "spawnDescendant", marker });
  const { descendant } = await a.next(owner); pids.add(descendant);
  owner.kill("SIGKILL"); await once(owner, "exit");
  assert.equal(alive(descendant), true, "descendant outlives its owner");
  assert.equal(existsSync(a.lockPath), true, "stale metadata remains after abrupt death");
  const successors = await Promise.all([1, 2, 3].map(() => a.worker("go2")));
  const replies = await a.claim(successors, "go2");
  assert.equal(replies.filter((reply) => reply.owner).length, 1);
  assert.equal(alive(descendant), true, "descendant was never what held the guard");
});

test("release removes only the owner's metadata, keeps the guard through that cleanup, and closes even when cleanup fails", async () => {
  const d = temp(), socketPath = join(d, "s.sock"), lockPath = join(d, "animator.lock"), before = servers();
  const lock = await acquireAnimatorKernelLock({ socketPath, lockPath });
  assert.equal(lock.owner, true);
  assert.equal(servers(), before + 1);
  let heldDuringCleanup;
  const failing = () => { heldDuringCleanup = lock.server.listening; throw Object.assign(new Error("EIO"), { code: "EIO" }); };
  await assert.rejects(releaseAnimatorKernelLock(lock, readFileSync, failing), /EIO/);
  assert.equal(heldDuringCleanup, true, "guard is still bound while metadata is removed");
  assert.equal(servers(), before, "guard closed in finally after the IO error");
  assert.equal(releaseAnimatorKernelLock(lock), lock.closing, "release is idempotent: later calls share the one outcome");
  await assert.rejects(lock.closing, /EIO/);
  const successor = await acquireAnimatorKernelLock({ socketPath, lockPath });
  assert.equal(successor.owner, true, "failed cleanup does not strand the guard");
  writeFileSync(lockPath, JSON.stringify({ v: 2, pid: process.pid, token: "someone-else" }));
  await releaseAnimatorKernelLock(successor);
  assert.equal(JSON.parse(readFileSync(lockPath, "utf8")).token, "someone-else", "foreign metadata is not unlinked");
  assert.equal(servers(), before);
  const normal = await acquireAnimatorKernelLock({ socketPath, lockPath });
  assert.equal(JSON.parse(readFileSync(lockPath, "utf8")).token, normal.token);
  await releaseAnimatorKernelLock(normal);
  assert.equal(existsSync(lockPath), false, "normal release unlinks its own metadata");
  assert.deepEqual(readdirSync(d).filter((name) => name.endsWith(".tmp")), []);
});

test("a genuinely live legacy metadata pid is deferred to without signalling it and without keeping the guard", async () => {
  const d = temp(), socketPath = join(d, "s.sock"), lockPath = join(d, "animator.lock"), before = servers();
  const legacy = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)", `legacy-${randomUUID()}`], { stdio: "ignore" });
  children.add(legacy); pids.add(legacy.pid);
  const seed = JSON.stringify({ pid: legacy.pid, token: "old" });
  writeFileSync(lockPath, seed);
  const lost = await acquireAnimatorKernelLock({ socketPath, lockPath });
  assert.deepEqual({ owner: lost.owner, reason: lost.reason, pid: lost.pid }, { owner: false, reason: "legacy-live", pid: legacy.pid });
  assert.equal(legacy.exitCode, null); assert.equal(legacy.signalCode, null); assert.equal(alive(legacy.pid), true);
  assert.equal(readFileSync(lockPath, "utf8"), seed, "legacy metadata untouched");
  assert.equal(servers(), before, "deferral closes its guard handle");
  legacy.kill("SIGKILL"); await once(legacy, "exit");
  const won = await acquireAnimatorKernelLock({ socketPath, lockPath });
  assert.equal(won.owner, true, "claimable once the legacy pid is gone");
  await releaseAnimatorKernelLock(won);
});

test("unsupported platforms fail explicitly with no guard, metadata, or file fallback", async () => {
  const d = temp(), before = servers();
  await assert.rejects(acquireAnimatorKernelLock({ socketPath: join(d, "s.sock"), lockPath: join(d, "animator.lock"), platform: "darwin" }), /Linux abstract unix sockets.*darwin.*unsupported/);
  assert.equal(readdirSync(d).length, 0); assert.equal(servers(), before);
  assert.equal(animatorKernelName("/x", 7).startsWith("\0pi-tmux-window-status:v2:7:"), true);
  assert.match(animatorKernelName("/x", 7), /:[0-9a-f]{32}$/);
});

test("runAnimator: concurrent starts yield one owner; stop is idempotent, releases last, and leaks no listener", async () => {
  const d = temp(), socketPath = join(d, "s.sock"), root = join(d, "root"), before = servers(), env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: d };
  publishLease({ socketPath, windowId: "@1", paneId: "%1" }, "o", "active", Date.now(), env);
  const exec = () => "";
  const open = () => runAnimator({ socketPath, root: join(d, "quick-deploy", "pi-tmux-status", Buffer.from(socketPath).toString("base64url")), exec, schedule: () => ({ unref() {} }), cancel: () => {}, subscribeUrgent: inertUrgent });
  const runs = await Promise.all([open(), open(), open()]);
  const started = runs.filter((run) => run.started);
  assert.equal(started.length, 1);
  const first = started[0].stop(), second = started[0].stop();
  assert.equal(first, second, "stop returns the single in-flight release");
  await second;
  assert.equal(servers(), before, "a joined waiter resumes only after metadata release and guard close");
  assert.equal(existsSync(join(d, "quick-deploy", "pi-tmux-status", Buffer.from(socketPath).toString("base64url"), "animator.lock")), false);
  await started[0].stop();
  const again = await open(); assert.equal(again.started, true); await again.stop();
});

test("startup failure releases the guard and rejects instead of orphaning a bound server", async () => {
  const d = temp(), socketPath = join(d, "s.sock"), before = servers();
  const exec = () => { throw new Error("tmux exploded"); };
  await assert.rejects(runAnimator({ socketPath, root: join(d, "root"), exec, schedule: () => ({ unref() {} }), cancel: () => {}, subscribeUrgent: inertUrgent }), /tmux exploded/);
  assert.equal(servers(), before);
  const lock = await acquireAnimatorKernelLock({ socketPath, lockPath: join(d, "root", "animator.lock") });
  assert.equal(lock.owner, true); await releaseAnimatorKernelLock(lock);
});

// Exception unwind: the child stays alive after the fault, so only explicit cleanup (not OS exit) can have released the guard.
for (const [fault, expected] of [["now", /injected now/], ["subscribe", /injected subscribe/], ["unsubscribe", undefined], ["cancel", undefined]]) test(`injected ${fault} fault leaves no leaked kernel guard while the animator process stays alive`, async () => {
  const d = temp(), socketPath = join(d, "s.sock");
  const child = fork(join(HERE, "animator-fault-worker.mjs"), [], { env: { ...process.env, CFG: JSON.stringify({ socketPath, runtime: d, fault }) } });
  children.add(child); pids.add(child.pid);
  const next = () => once(child, "message", { signal: AbortSignal.timeout(8_000) }).then(([message]) => message);
  await next();
  child.send({ cmd: "run" });
  const startup = await next();
  if (expected) {
    assert.match(startup.rejected, expected, "original startup error is preserved");
    assert.equal(startup.servers, 0, "failed startup closed its bound Server");
  } else {
    assert.equal(startup.started, true);
    child.send({ cmd: "stop" });
    const stopped = await next();
    assert.equal(stopped.calls.resets >= 1, true, "style reset was still attempted");
    assert.equal(stopped.calls.cancel >= 1 && stopped.calls.unsubscribe >= 1, true, "every cleanup hook ran despite the throwing one");
    assert.equal(stopped.servers, 0);
  }
  assert.equal(child.exitCode, null, "fixture is still alive: release was cleanup, not process exit");
  const claim = await acquireAnimatorKernelLock({ socketPath, lockPath: join(d, "other", "animator.lock") });
  assert.equal(claim.owner, true, "same-key kernel claim succeeds while the faulted child lives");
  await releaseAnimatorKernelLock(claim);
});

// Mixed-generation module graphs must fail at ESM linking, before any lock, socket, metadata or tmux frame exists.
function mixedGeneration(label, build) {
  test(`mixed-generation ${label} fails at linking with zero side effects`, () => {
    const d = temp(), code = join(d, "code"), runtime = join(d, "runtime"), bin = join(d, "bin"), marker = join(d, "tmux-ran"), socket = join(d, "s.sock");
    for (const dir of [code, bin]) spawnSync("mkdir", ["-p", dir]);
    writeFileSync(join(bin, "tmux"), `#!/bin/sh\ntouch ${marker}\n`); chmodSync(join(bin, "tmux"), 0o755);
    build(code);
    const run = spawnSync(process.execPath, [join(code, "animator.mjs"), socket], { env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime }, encoding: "utf8", timeout: 10_000 });
    assert.notEqual(run.status, 0);
    assert.match(run.stderr, /does not provide an export named '(acquireAnimator|releaseAnimator)/);
    assert.equal(existsSync(runtime), false, "no runtime dir, lock directory or metadata was created");
    assert.equal(existsSync(marker), false, "no tmux frame command ran");
  });
}
const sourceOf = (name) => readFileSync(join(SRC, name), "utf8");
const renameExports = (text) => text.replaceAll("acquireAnimatorKernelLock", "acquireAnimatorLock").replaceAll("releaseAnimatorKernelLock", "releaseAnimatorLock");
mixedGeneration("old animator importing the unsafe sync names against new state", (dir) => {
  const old = renameExports(sourceOf("animator.mjs")); assert.notEqual(old, sourceOf("animator.mjs"));
  writeFileSync(join(dir, "animator.mjs"), old); writeFileSync(join(dir, "state.mjs"), sourceOf("state.mjs"));
});
mixedGeneration("new animator against old state exporting only the sync names", (dir) => {
  const old = renameExports(sourceOf("state.mjs")); assert.notEqual(old, sourceOf("state.mjs"));
  writeFileSync(join(dir, "animator.mjs"), sourceOf("animator.mjs")); writeFileSync(join(dir, "state.mjs"), old);
});

test("state.mjs no longer exports the unsafe sync lock names while keeping unrelated exports", async () => {
  const state = await import("../state.mjs");
  assert.equal("acquireAnimatorLock" in state, false); assert.equal("releaseAnimatorLock" in state, false);
  for (const name of ["acquireAnimatorKernelLock", "releaseAnimatorKernelLock", "publishLease", "readLease", "windowLeaseStates", "runtimeRoot", "safeId", "serverRoot", "leasePath", "aggregateLogicalState", "projectAsyncStatus", "seedAttentionWatermarksFromSnapshot", "HEARTBEAT_MS", "LEASE_TTL_MS", "RECOVERY_ARMED_OPTION", "atomicWrite"]) assert.ok(name in state, name);
});
