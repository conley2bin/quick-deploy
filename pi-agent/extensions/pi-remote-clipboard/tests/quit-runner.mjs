// Isolated "real quit" process for lifecycle.test.mjs: awaits the actual session_shutdown handler
// and then exits the process root, exactly as Pi does after shutdown. Synthetic helper only.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join } from "node:path";
import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { mock } from "node:test";

let coreRoot = dirname(realpathSync(execFileSync("sh", ["-c", "command -v pi"], { encoding: "utf8" }).trim()));
while (coreRoot !== dirname(coreRoot)) {
  const pkg = join(coreRoot, "package.json");
  if (existsSync(pkg) && JSON.parse(readFileSync(pkg, "utf8")).name === "@earendil-works/pi-coding-agent") break;
  coreRoot = dirname(coreRoot);
}
const coreUrl = pathToFileURL(join(coreRoot, "dist/index.js")).href;
const tuiUrl = pathToFileURL(join(coreRoot, "node_modules/@earendil-works/pi-tui/dist/index.js")).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@earendil-works/pi-coding-agent") return { url: coreUrl, shortCircuit: true };
    if (specifier === "@earendil-works/pi-tui") return { url: tuiUrl, shortCircuit: true };
    return nextResolve(specifier, context);
  },
});

const temp = process.env.RUNNER_TEMP;
const mode = process.env.RUNNER_MODE;
process.env.PI_REMOTE_CLIPBOARD_DIR = temp;
process.env.SSH_CONNECTION = "fixture";
const { default: piRemoteClipboard } = await import("../pi-extension/index.ts");
const shortcuts = new Map();
const events = new Map();
piRemoteClipboard({
  registerShortcut(key, options) { shortcuts.set(key, options); },
  registerCommand() {},
  on(event, handler) { events.set(event, handler); },
});
const ctx = { mode: "print", ui: { notify() {}, pasteToEditor() {} } };
events.get("session_start")({ type: "session_start" }, ctx);

const started = () => readdirSync(temp).some((name) => name.startsWith("started-"));
const turn = () => new Promise((resolve) => setImmediate(resolve));
async function untilStarted() {
  while (!started()) await turn();
}

const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
if (mode === "timedout-exit" || mode === "exited-timedout-exit") mock.timers.enable({ apis: ["setTimeout"] });
const operation = shortcuts.get("ctrl+alt+v").handler(ctx);
await untilStarted();
if (mode === "exited-quit" || mode === "exited-timedout-exit") {
  // The leader has already exited 0; a TERM-ignoring descendant still holds the pipe.
  const leader = Number(readdirSync(temp).find((name) => name.startsWith("started-")).slice(8));
  while (alive(leader)) await turn();
  if (mode === "exited-timedout-exit") {
    mock.timers.tick(8000); // deadline rejects the operation; the leader is already gone
    await operation;
  }
  const shutdown = events.get("session_shutdown")({ type: "session_shutdown" }, ctx);
  let done = false;
  void shutdown.then(() => { done = true; });
  // Grace time advances only while shutdown is still pending: a shutdown that already
  // returned must not rely on a timer that process.exit would destroy.
  for (let turns = 0; turns < 50 && !done; turns += 1) {
    await turn();
    if (!done && mode === "exited-timedout-exit") mock.timers.tick(1000);
  }
  await shutdown;
  process.exit(0);
} else if (mode === "timedout-exit") {
  mock.timers.tick(8000); // the operation promise has already settled (timed out) before quitting
  await operation;
  const shutdown = events.get("session_shutdown")({ type: "session_shutdown" }, ctx);
  mock.timers.tick(1000);
  await shutdown;
  process.exit(0);
} else {
  const shutdown = events.get("session_shutdown")({ type: "session_shutdown" }, ctx);
  await shutdown;
  await operation;
  if (mode === "inflight-exit") process.exit(0);
}
