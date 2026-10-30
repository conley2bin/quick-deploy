import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

import { installedPiRoot } from "./pi-root.ts";

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


test("real SDK load-only invalidation and failed factories leave no stdout or bridge roots", async () => {
  const temporary = mkdtempSync(resolve(tmpdir(), "pi-inline-factory-"));
  const core = installedPiRoot();
  const { loadExtensions } = await import(pathToFileURL(resolve(core, "dist/core/extensions/loader.js")).href);
  const { createEventBus } = await import(pathToFileURL(resolve(core, "dist/core/event-bus.js")).href);
  const { ExtensionRunner } = await import(pathToFileURL(resolve(core, "dist/core/extensions/runner.js")).href);
  const request = "pi-inline-images:graphics-owner:request";
  const reply = "pi-inline-images:graphics-owner:reply";
  const eventBus = createEventBus();
  // Observe exact tracked callback lifetimes, without retaining retired handles.
  const callbacks = new Map<string, Set<unknown>>();
  const bus = {
    emit: eventBus.emit,
    on(channel: string, callback: (value: unknown) => void) {
      const set = callbacks.get(channel) ?? new Set();
      callbacks.set(channel, set);
      set.add(callback);
      const remove = eventBus.on(channel, callback);
      return () => { set.delete(callback); remove(); };
    },
  };
  const baseline = [process.stdout.listenerCount("error"), process.stdout.listenerCount("close")];
  let replies = 0;
  const removeReply = bus.on(reply, () => { replies++; });
  const failingPath = resolve(temporary, "failed.ts");
  writeFileSync(failingPath, `import factory from ${JSON.stringify(resolve("index.ts"))};\nexport default (pi) => { factory(pi); throw new Error("after bridge registration"); };\n`);
  try {
    for (const kind of ["invalidate", "failed", "shutdown"]) {
      for (let cycle = 0; cycle < 8; cycle++) {
        const loaded = await loadExtensions([kind === "failed" ? failingPath : resolve("index.ts")], process.cwd(), bus);
        try {
          if (kind === "failed") {
            assert.match(loaded.errors[0]?.error ?? "", /after bridge registration/u);
            assert.equal(callbacks.get(request)?.size ?? 0, 0, "loader discard removes failed-factory bridge callback");
          } else {
            assert.deepEqual(loaded.errors, []);
            assert.equal(callbacks.get(request)?.size, 1, "factory publishes one current backend before session_start");
            const before = replies;
            for (const owner of ["inline", "read"]) bus.emit(request, { version: 1, owner, requestId: `${kind}-${cycle}-${owner}` });
            assert.equal(replies, before + 2, "both owner handles are available during load-only lifetime");
            if (kind === "shutdown") {
              const runner = new ExtensionRunner(loaded.extensions, loaded.runtime, process.cwd(), { getBranch: () => [] }, {});
              await runner.emit({ type: "session_shutdown" });
            }
          }
          assert.deepEqual([process.stdout.listenerCount("error"), process.stdout.listenerCount("close")], baseline,
            `${kind} cycle ${cycle}: passive factory owns no stdout pair`);
        } finally { loaded.runtime.invalidate(); }
        assert.equal([...callbacks.entries()].filter(([channel]) => channel !== reply).reduce((sum, [, set]) => sum + set.size, 0), 0,
          "runtime invalidation removes every extension event-bus root, including bridge handle closures");
        const before = replies;
        bus.emit(request, { version: 1, owner: "read", requestId: "retired" });
        assert.equal(replies, before, "retired backend cannot reply");
      }
    }
  } finally {
    removeReply();
    rmSync(temporary, { recursive: true, force: true });
  }
});
