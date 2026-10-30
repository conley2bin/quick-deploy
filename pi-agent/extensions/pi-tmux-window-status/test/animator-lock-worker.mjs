// Child-process fixture for animator-kernel-lock.test.mjs. Private socket key and metadata dir arrive via CFG; commands arrive over IPC.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { acquireAnimatorKernelLock, releaseAnimatorKernelLock } from "../state.mjs";

const { socketPath, lockPath, go } = JSON.parse(process.env.CFG);
let held;
process.on("message", async (message) => {
  if (message.cmd === "claim") {
    // Spin on a barrier file so every contender enters the kernel bind together.
    const deadline = Date.now() + 5_000;
    while (!existsSync(go) && Date.now() < deadline);
    held = await acquireAnimatorKernelLock({ socketPath, lockPath, token: message.token });
    process.send({ owner: held.owner, reason: held.reason, pid: process.pid, token: message.token });
  } else if (message.cmd === "spawnDescendant") {
    // An exec'd descendant that outlives a SIGKILLed owner; it must not pin the guard.
    const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)", message.marker], { detached: true, stdio: "ignore" });
    child.unref();
    process.send({ descendant: child.pid });
  } else if (message.cmd === "release") {
    await releaseAnimatorKernelLock(held);
    process.send({ released: true });
  }
});
process.send({ ready: true });
