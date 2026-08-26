import { chmodSync, closeSync, mkdirSync, openSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { linkSync } from "node:fs";
import { dirname, join } from "node:path";
import { randomUUID } from "node:crypto";

export const LEASE_TTL_MS = 6_000;
export const HEARTBEAT_MS = 2_000;
const TERMINAL = new Set(["complete", "completed", "failed", "paused", "stopped", "rejected"]);

/** Runtime contract: tmux window options stay @quick_deploy_pi_* and the private lease directory stays quick-deploy/pi-tmux-status (deliberately unchanged by the extension rename) so no duplicate runtime state is created. */
export function runtimeRoot(env = process.env) {
  const base = env.QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME || env.XDG_RUNTIME_DIR || join(env.XDG_STATE_HOME || join(env.HOME || ".", ".local", "state"), "quick-deploy-runtime");
  const root = join(base, "quick-deploy", "pi-tmux-status");
  mkdirSync(root, { recursive: true, mode: 0o700 });
  try { chmodSync(root, 0o700); } catch {}
  return root;
}
export const safeId = (value) => Buffer.from(String(value), "utf8").toString("base64url");
export const serverRoot = (identity, env = process.env) => join(runtimeRoot(env), safeId(identity.socketPath));
export const leasePath = (identity, ownerId, env = process.env) => join(serverRoot(identity, env), "leases", `${safeId(ownerId)}.json`);

export function atomicWrite(file, value) {
  mkdirSync(dirname(file), { recursive: true, mode: 0o700 });
  const temp = join(dirname(file), `.${safeId(file)}.${process.pid}.${randomUUID()}.tmp`);
  try {
    writeFileSync(temp, `${JSON.stringify(value)}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
    renameSync(temp, file);
  } finally {
    rmSync(temp, { force: true });
  }
}

/** Runtime-only ownership diagnostics deliberately contain IDs only, never prompts/history/cwd/names. */
export function normalizeLeaseState(state) {
  if (state === false || state === undefined || state === null) return false;
  if (state === true || state === "active") return "active";
  if (state === "error") return "error";
  throw new Error(`invalid pi-tmux-window-status lease state: ${String(state)}`);
}

export function publishLease(identity, ownerId, state, now = Date.now(), env = process.env, diagnostic = {}) {
  const file = leasePath(identity, ownerId, env);
  const normalized = normalizeLeaseState(state);
  if (!normalized) { rmSync(file, { force: true }); return file; }
  const parentSessionId = typeof diagnostic.parentSessionId === "string" ? diagnostic.parentSessionId : undefined;
  const activeRunIds = [...new Set(Array.isArray(diagnostic.activeRunIds) ? diagnostic.activeRunIds.filter((id) => typeof id === "string") : [])].sort();
  const activeNodeIds = [...new Set(Array.isArray(diagnostic.activeNodeIds) ? diagnostic.activeNodeIds.filter((id) => typeof id === "string") : [])].sort();
  atomicWrite(file, {
    version: 1,
    ownerId,
    state: normalized,
    socketPath: identity.socketPath,
    windowId: identity.windowId,
    paneId: identity.paneId,
    heartbeatAt: now,
    ...(parentSessionId ? { parentSessionId } : {}),
    ...(activeRunIds.length ? { activeRunIds } : {}),
    ...(activeNodeIds.length ? { activeNodeIds } : {}),
  });
  return file;
}

export function readLease(file, now = Date.now(), ttl = LEASE_TTL_MS, remove = rmSync) {
  let v;
  try { v = JSON.parse(readFileSync(file, "utf8")); } catch { try { remove(file, { force: true }); } catch {} return undefined; }
  if (!v || v.version !== 1 || typeof v.socketPath !== "string" || typeof v.windowId !== "string" || typeof v.paneId !== "string" || !Number.isFinite(v.heartbeatAt)) { try { remove(file, { force: true }); } catch {} return undefined; }
  if (now - v.heartbeatAt > ttl) { try { remove(file, { force: true }); } catch {} return undefined; }
  const state = v.state === "error" ? "error" : "active";
  return { ...v, state };
}

function numericMax(values) {
  const numbers = values.filter(Number.isFinite);
  return numbers.length ? Math.max(...numbers) : 0;
}

function nodeId(node, fallbackId) {
  return typeof node.runId === "string" ? node.runId : typeof node.id === "string" ? node.id : fallbackId;
}

function nodeState(node) {
  return typeof node.status === "string" ? node.status : typeof node.state === "string" ? node.state : undefined;
}

function nodeMark(node) {
  const activity = node && typeof node.activity === "object" ? node.activity : undefined;
  return numericMax([
    node?.lastActivityAt,
    node?.currentToolStartedAt,
    node?.lastUpdate,
    node?.updatedAt,
    node?.startedAt,
    activity?.lastActivityAt,
    activity?.currentToolStartedAt,
  ]);
}

function nodeActivityState(node) {
  const activity = node && typeof node.activity === "object" ? node.activity : undefined;
  return typeof node.activityState === "string" ? node.activityState : typeof activity?.state === "string" ? activity.state : undefined;
}

function hasOwnActivity(node) {
  const activity = node && typeof node.activity === "object" ? node.activity : undefined;
  return Boolean(
    Number.isFinite(node?.lastActivityAt) ||
    Number.isFinite(node?.currentToolStartedAt) ||
    typeof node?.currentTool === "string" ||
    Number.isFinite(activity?.lastActivityAt) ||
    Number.isFinite(activity?.currentToolStartedAt) ||
    typeof activity?.currentTool === "string"
  );
}

function record(node, fallbackId, out) {
  if (!node || typeof node !== "object") return;
  const id = nodeId(node, fallbackId);
  const state = nodeState(node);
  if (!id || !state) return;
  const container = (Array.isArray(node.steps) && node.steps.length > 0) || (Array.isArray(node.children) && node.children.length > 0);
  out.set(id, { id, state, mark: nodeMark(node), activityState: nodeActivityState(node), container, ownActivity: hasOwnActivity(node) });
  if (Array.isArray(node.steps)) node.steps.forEach((step, i) => record(step, `${id}:step:${i}`, out));
  if (Array.isArray(node.children)) node.children.forEach((child, i) => record(child, `${id}:child:${i}`, out));
}

function nodeIsActive(node, attentionWatermarks) {
  if (TERMINAL.has(node.state)) return false;
  const watermark = attentionWatermarks.get(node.id);
  if (node.activityState === "needs_attention" && (watermark === undefined || node.mark <= watermark)) return false;
  if (watermark !== undefined && node.mark <= watermark) return false;
  if (node.container && !node.ownActivity) return false;
  return true;
}

/** Projects pi-subagents 0.56 AsyncStatus roots, snapshot nodes, and nested workflow steps. */
export function seedAttentionWatermarksFromSnapshot(status, attentionWatermarks) {
  const nodes = new Map();
  if (!status || typeof status !== "object") return attentionWatermarks;
  if (Array.isArray(status.runs)) status.runs.forEach((run, i) => record(run, `run:${i}`, nodes));
  else record(status, undefined, nodes);
  for (const node of nodes.values()) if (node.activityState === "needs_attention" && !attentionWatermarks.has(node.id)) attentionWatermarks.set(node.id, node.mark);
  return attentionWatermarks;
}

export function projectAsyncStatus(status, attentionWatermarks = new Map()) {
  const nodes = new Map();
  if (!status || typeof status !== "object") return { nodes, activeIds: new Set(), malformed: true };
  if (Array.isArray(status.runs)) status.runs.forEach((run, i) => record(run, `run:${i}`, nodes));
  else record(status, undefined, nodes);
  if (!nodes.size) return { nodes, activeIds: new Set(), malformed: true };
  const activeIds = new Set();
  for (const node of nodes.values()) if (nodeIsActive(node, attentionWatermarks)) activeIds.add(node.id);
  return { nodes, activeIds, malformed: false };
}

/** The window lease is active for root work or any non-attention descendant. */
export function aggregateLogicalState({ mainActive, roots, snapshots, attentionWatermarks }) {
  const activeRoots = new Set(), activeIds = new Set();
  let malformed = false;
  for (const root of roots) {
    const p = projectAsyncStatus(snapshots.get(root), attentionWatermarks);
    malformed ||= p.malformed;
    if (p.malformed || p.activeIds.size) activeRoots.add(root);
    for (const id of p.activeIds) activeIds.add(id);
  }
  return { active: Boolean(mainActive || activeRoots.size), activeRoots, activeIds, malformed };
}

function processAlive(pid, killFn = process.kill) {
  try { killFn(pid, 0); return true; } catch (error) { return error?.code === "EPERM"; }
}

export function acquireAnimatorLock(lockPath, token = randomUUID(), pid = process.pid, read = readFileSync, link = linkSync, writeFile = writeFileSync, unlink = rmSync, alive = processAlive) {
  mkdirSync(dirname(lockPath), { recursive: true, mode: 0o700 });
  const tryClaim = () => {
    const temp = join(dirname(lockPath), `.${safeId(lockPath)}.${pid}.${randomUUID()}.tmp`);
    let outcome;
    try {
      writeFile(temp, `${JSON.stringify({ pid, token })}\n`, { encoding: "utf8", mode: 0o600, flag: "wx" });
      try { link(temp, lockPath); outcome = { token, owner: true }; }
      catch (error) { outcome = error; }
    } finally {
      if (temp !== lockPath) { try { unlink(temp, { force: true }); } catch {} }
    }
    return outcome;
  };
  const attempt = tryClaim();
  if (!(attempt instanceof Error)) return attempt;
  if (attempt.code !== "EEXIST") throw attempt;
  const reclaim = () => { try { unlink(lockPath, { force: true }); } catch {} const next = tryClaim(); if (!(next instanceof Error)) return next; if (next.code === "EEXIST") return { owner: false }; throw next; };
  let held; try { held = JSON.parse(read(lockPath, "utf8")); } catch { return reclaim(); }
  if (!held || typeof held.token !== "string" || !Number.isInteger(held.pid)) return reclaim();
  if (alive(held.pid)) return { owner: false };
  return reclaim();
}

export function windowLeaseStates(root, now = Date.now()) {
  const active = new Set(), error = new Set();
  const dir = join(root, "leases");
  try {
    for (const file of readdirSync(dir)) {
      if (!file.endsWith(".json")) continue;
      const lease = readLease(join(dir, file), now);
      if (!lease) continue;
      if (lease.state === "error") error.add(lease.windowId);
      else active.add(lease.windowId);
    }
  } catch {}
  for (const id of error) active.delete(id);
  return { active, error };
}

export function releaseAnimatorLock(lockPath, token, read = readFileSync, remove = rmSync) {
  try {
    const held = JSON.parse(read(lockPath, "utf8"));
    if (held.token === token) remove(lockPath, { force: true });
  } catch {}
}
