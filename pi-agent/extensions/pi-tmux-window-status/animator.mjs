#!/usr/bin/env node
import { existsSync, readdirSync, realpathSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { acquireAnimatorLock, readLease, releaseAnimatorLock, runtimeRoot, safeId, windowLeaseStates } from "./state.mjs";

export const FRAME_COUNT = 24;
export const FRAME_MS = 42;
export const ERROR_MS = 1_000;
export const PERIOD_MS = FRAME_COUNT * FRAME_MS;
export const ERROR_BG = "#d70000";
export const ERROR_FG = "#ffffff";
export const GRAY_RANGE = { idle: "#bcbcbc", bright: "#f5f5f5", dark: "#808080" };
export const BLUE_RANGE = { idle: "#00afff", bright: "#7be0ff", dark: "#0087af" };

const srgb = (byte) => { const value = byte / 255; return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4; };
const encoded = (value) => Math.round(255 * (value <= 0.0031308 ? value * 12.92 : 1.055 * value ** (1 / 2.4) - 0.055));
const rgb = (hex) => [1, 3, 5].map((offset) => srgb(Number.parseInt(hex.slice(offset, offset + 2), 16)));
function interpolate(from, to, amount) { const a = rgb(from), b = rgb(to); return `#${a.map((v, i) => encoded(v + (b[i] - v) * amount).toString(16).padStart(2, "0")).join("")}`; }
export function createPalette({ idle, bright, dark }) { return Array.from({ length: FRAME_COUNT }, (_, frame) => { const wave = Math.sin((Math.PI * 2 * frame) / FRAME_COUNT); return wave >= 0 ? interpolate(idle, bright, wave) : interpolate(idle, dark, -wave); }); }
export const FRAMES = createPalette(GRAY_RANGE);
export const CURRENT_FRAMES = createPalette(BLUE_RANGE);
export function frameAt(elapsedMs) { return Math.floor(((elapsedMs % PERIOD_MS) + PERIOD_MS) % PERIOD_MS / FRAME_MS); }

export function activeWindows(root, now = Date.now()) { return windowLeaseStates(root, now).active; }
export function leaseWindowStates(root, now = Date.now()) { return windowLeaseStates(root, now); }

export function windowOptionArgs(active, frame, reset = new Set(), error = new Set()) {
  const args = [];
  for (const id of [...reset].sort()) args.push("set-option", "-uw", "-t", id, "@quick_deploy_pi_active", ";", "set-option", "-uw", "-t", id, "@quick_deploy_pi_bg", ";", "set-option", "-uw", "-t", id, "@quick_deploy_pi_current_bg", ";", "set-option", "-uw", "-t", id, "@quick_deploy_pi_error", ";");
  for (const id of [...error].sort()) args.push("set-option", "-uw", "-t", id, "@quick_deploy_pi_active", ";", "set-option", "-uw", "-t", id, "@quick_deploy_pi_bg", ";", "set-option", "-uw", "-t", id, "@quick_deploy_pi_current_bg", ";", "set-option", "-w", "-t", id, "@quick_deploy_pi_error", "1", ";");
  for (const id of [...active].sort()) args.push("set-option", "-uw", "-t", id, "@quick_deploy_pi_error", ";", "set-option", "-w", "-t", id, "@quick_deploy_pi_active", "1", ";", "set-option", "-w", "-t", id, "@quick_deploy_pi_bg", FRAMES[frame], ";", "set-option", "-w", "-t", id, "@quick_deploy_pi_current_bg", CURRENT_FRAMES[frame], ";");
  return args;
}
export function clientRefreshArgs(clients) { return clients.flatMap((client) => ["refresh-client", "-S", "-t", client, ";"]); }
export function listClients(tmux, socketPath, exec = execFileSync) { try { const output = exec(tmux, ["-S", socketPath, "list-clients", "-F", "#{client_name}"], { encoding: "utf8" }); return String(output || "").trim().split("\n").filter(Boolean).sort(); } catch (error) { if (error?.status === 1) return []; throw error; } }
export function sweepWindows(tmux, socketPath, states = { active: new Set(), error: new Set() }, exec = execFileSync) {
  try { const ids = exec(tmux, ["-S", socketPath, "list-windows", "-a", "-F", "#{window_id}"], { encoding: "utf8" }); const active = states instanceof Set ? states : states.active; const error = states instanceof Set ? new Set() : states.error; const live = new Set([...active, ...error]); const stale = String(ids || "").trim().split("\n").filter((id) => id && !live.has(id)); const args = windowOptionArgs(new Set(), 0, new Set(stale), new Set()); if (args.length) exec(tmux, ["-S", socketPath, ...args], { stdio: "ignore" }); return stale; } catch (error) { console.error("pi-tmux-window-status startup sweep failed:", error); return []; }
}

export function runAnimator({ socketPath, root = join(runtimeRoot(), safeId(socketPath)), tmux = "tmux", intervalMs = FRAME_MS, errorIntervalMs = ERROR_MS, now = () => performance.now(), wallNow = () => Date.now(), exec = execFileSync, clientTtlMs = 1_000, schedule = setTimeout, cancel = clearTimeout }) {
  const lockPath = join(root, "animator.lock"); const lock = acquireAnimatorLock(lockPath); if (!lock.owner) return { started: false };
  let previous = new Set(), stopped = false, timer, clients = [], clientsAt = -Infinity; const startedAt = now();
  const scheduleNext = (delay) => { timer = schedule(tick, delay); };
  const refreshCache = () => { if (wallNow() - clientsAt >= clientTtlMs) { clients = listClients(tmux, socketPath, exec); clientsAt = wallNow(); } };
  const render = (states, reset, refreshClients = clients) => { const frame = frameAt(now() - startedAt); const args = [...windowOptionArgs(states.active, frame, reset, states.error), ...clientRefreshArgs(refreshClients)]; if (args.length) exec(tmux, ["-S", socketPath, ...args], { stdio: "ignore" }); };
  const stop = () => { if (stopped) return; stopped = true; cancel(timer); try { refreshCache(); render({ active: new Set(), error: new Set() }, previous); } catch { try { render({ active: new Set(), error: new Set() }, previous, []); } catch (retry) { console.error("pi-tmux-window-status cleanup failed:", retry); } } releaseAnimatorLock(lockPath, lock.token); };
  const tick = () => { try { const current = windowLeaseStates(root, wallNow()); refreshCache(); const currentAll = new Set([...current.active, ...current.error]); const reset = new Set([...previous].filter((id) => !currentAll.has(id))); render(current, reset); previous = currentAll; if (!currentAll.size) return stop(); scheduleNext(current.active.size ? intervalMs : errorIntervalMs); } catch (error) { console.error("pi-tmux-window-status frame failed; retrying:", error); clientsAt = -Infinity; scheduleNext(errorIntervalMs); } };
  const initial = windowLeaseStates(root, wallNow()); sweepWindows(tmux, socketPath, initial, exec); tick(); return { started: true, stop, tick };
}
export function isDirectExecution(metaUrl = import.meta.url, argv1 = process.argv[1]) { if (!argv1 || !existsSync(argv1)) return false; try { return realpathSync(fileURLToPath(metaUrl)) === realpathSync(argv1); } catch { return false; } }
if (isDirectExecution()) { const socket = process.argv[2]; if (!socket) { console.error("pi-tmux-window-status: missing socket"); process.exitCode = 2; } else runAnimator({ socketPath: socket }); }
