import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

function installedPiRoot(): string {
  const cli = execFileSync("sh", ["-lc", "realpath \"$(command -v pi)\""], { encoding: "utf8" }).trim();
  return dirname(dirname(dirname(cli)));
}

test("cancelled session switch preserves prepared image state through the installed host runner", async () => {
  const temporary = mkdtempSync(resolve(tmpdir(), "pi-inline-lifecycle-"));
  mkdirSync(resolve(temporary, "agent"));
  const prior = { ...process.env };
  delete process.env.TMUX;
  delete process.env.KITTY_WINDOW_ID;
  delete process.env.GHOSTTY_RESOURCES_DIR;
  delete process.env.WEZTERM_PANE;
  process.env.TERM = "xterm";
  process.env.TERM_PROGRAM = "test";
  process.env.PI_CODING_AGENT_DIR = resolve(temporary, "agent");
  process.env.PI_OFFLINE = "1";
  try {
    const core = installedPiRoot();
    const { loadExtensions, loadExtensionFromFactory } = await import(pathToFileURL(resolve(core, "dist/core/extensions/loader.js")).href);
    const { createEventBus } = await import(pathToFileURL(resolve(core, "dist/core/event-bus.js")).href);
    const { ExtensionRunner } = await import(pathToFileURL(resolve(core, "dist/core/extensions/runner.js")).href);
    const { AgentSessionRuntime } = await import(pathToFileURL(resolve(core, "dist/core/agent-session-runtime.js")).href);
    const bus = createEventBus();
    const loaded = await loadExtensions([resolve("index.ts")], process.cwd(), bus);
    assert.deepEqual(loaded.errors, []);
    const extension = loaded.extensions[0];
    const cancel = await loadExtensionFromFactory((pi: { on(name: string, handler: () => unknown): void }) => {
      pi.on("session_before_switch", () => ({ cancel: true }));
    }, process.cwd(), bus, loaded.runtime, "<cancel-test>");
    const runner = new ExtensionRunner([extension, cancel], loaded.runtime, process.cwd(), { getBranch: () => [] }, {});
    runner.mode = "tui";
    const raw = `Before\n\n![fixture](${resolve("test/fixtures/color-block.png")})\n\nAfter`;
    const message = { role: "assistant", content: [{ type: "text", text: raw }], stopReason: "stop" };
    const serializedBefore = JSON.stringify(message);
    await runner.emitMessageEnd({ type: "message_end", message });
    assert.equal(JSON.stringify(message), serializedBefore, "async preparation does not mutate native message bytes");
    const context = { messageType: "assistant", isStreaming: false, availableWidth: 40 };
    const before = extension.markdownTransformer(raw, context);
    const runtime = new AgentSessionRuntime({ extensionRunner: runner }, { cwd: process.cwd() }, () => { throw new Error("cancelled switch must not replace session"); });
    const result = await runtime.newSession();
    const after = extension.markdownTransformer(raw, context);
    assert.deepEqual(result, { cancelled: true });
    assert.notEqual(before, raw);
    assert.equal(after, before, "cancellable before-switch phase does not destroy active image state");
  } finally {
    for (const key of Object.keys(process.env)) if (!(key in prior)) delete process.env[key];
    Object.assign(process.env, prior);
    rmSync(temporary, { recursive: true, force: true });
  }
});
