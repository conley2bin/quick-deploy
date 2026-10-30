// Child-process fixture for animator-kernel-lock.test.mjs: runs a real runAnimator with one injected fault and, unlike an OS exit, stays alive over private IPC afterwards.
import net from "node:net";
import { join } from "node:path";
import { runAnimator } from "../animator.mjs";
import { publishLease } from "../state.mjs";

const { socketPath, runtime, fault } = JSON.parse(process.env.CFG);
const env = { QUICK_DEPLOY_PI_TMUX_WINDOW_STATUS_RUNTIME: runtime };
const root = join(runtime, "quick-deploy", "pi-tmux-status", Buffer.from(socketPath).toString("base64url"));
const calls = { cancel: 0, unsubscribe: 0, resets: 0 };
const boom = (label) => () => { throw new Error(`injected ${label}`); };
const exec = (_tmux, args) => { if (args.includes("-uw")) calls.resets += 1; return ""; };
const options = {
  socketPath, root, exec, schedule: () => ({ unref() {} }),
  cancel: fault === "cancel" ? () => { calls.cancel += 1; throw new Error("injected cancel"); } : () => { calls.cancel += 1; },
  subscribeUrgent: fault === "subscribe" ? boom("subscribe") : () => () => { calls.unsubscribe += 1; if (fault === "unsubscribe") throw new Error("injected unsubscribe"); },
  ...(fault === "now" ? { now: boom("now") } : {}),
};
let animator;
process.on("message", async (message) => {
  if (message.cmd === "run") {
    publishLease({ socketPath, windowId: "@1", paneId: "%1" }, "owner", "active", Date.now(), env);
    try { animator = await runAnimator(options); process.send({ started: animator.started }); }
    catch (error) { process.send({ rejected: error.message, servers: process._getActiveHandles().filter((h) => h instanceof net.Server && h._handle).length }); }
  } else if (message.cmd === "stop") {
    await animator.stop();
    process.send({ stopped: true, calls, servers: process._getActiveHandles().filter((h) => h instanceof net.Server && h._handle).length });
  }
});
process.send({ ready: true });
