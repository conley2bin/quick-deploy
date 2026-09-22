import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { VERSION } from "@earendil-works/pi-coding-agent";

test("Pi 0.86.1 rejects an unavailable displayed-session clipboard without OSC52 fallback", (t) => {
  if (VERSION !== "0.86.1" || process.platform !== "linux") {
    t.skip("Pi 0.86.1 Linux clipboard regression");
    return;
  }

  const emptyPath = mkdtempSync(join(tmpdir(), "pi-copy-links-empty-path-"));
  const runtimeUrl = import.meta.resolve("@earendil-works/pi-coding-agent");
  const script = `
    import { copyToClipboard } from ${JSON.stringify(runtimeUrl)};
    try {
      await copyToClipboard("source\\t中文  ");
      process.stdout.write(JSON.stringify({ resolved: true }));
    } catch (error) {
      process.stdout.write(JSON.stringify({
        resolved: false,
        message: error instanceof Error ? error.message : String(error),
      }));
    }
  `;
  const env: NodeJS.ProcessEnv = {
    ...process.env, PATH: emptyPath, WAYLAND_DISPLAY: "fixture", XDG_SESSION_TYPE: "wayland",
  };
  const cleared = [
    "DISPLAY", "SSH_CONNECTION", "SSH_CLIENT", "MOSH_CONNECTION",
    "TMUX", "TMUX_PANE", "TERMUX_VERSION", "WT_SESSION",
  ];
  for (const key of cleared) {
    delete env[key];
  }

  try {
    const result = spawnSync(process.execPath, ["--input-type=module", "--eval", script], {
      cwd: process.cwd(), env, encoding: "utf8", timeout: 10_000,
    });
    assert.equal(result.status, 0, result.stderr);
    assert.doesNotMatch(result.stdout, /\x1b\]52;/, "a local displayed-session failure must not claim OSC52 success");
    assert.deepEqual(JSON.parse(result.stdout), {
      resolved: false,
      message: "Clipboard unavailable: install `wl-clipboard` (`wl-copy`) or check Wayland access",
    });
  } finally {
    rmSync(emptyPath, { recursive: true, force: true });
  }
});

test("failed reload keeps only a minimal passive guard and releases predecessor closures", () => {
  const adapterUrl = new URL("../src/adapter.ts", import.meta.url).href;
  const coreUrl = import.meta.resolve("@earendil-works/pi-coding-agent");
  const tuiUrl = import.meta.resolve("@earendil-works/pi-tui");
  const script = `
    import { VERSION } from ${JSON.stringify(coreUrl)};
    import { TuiAltScreen } from ${JSON.stringify(tuiUrl)};
    import { installAdapter } from ${JSON.stringify(adapterUrl)};

    const inputSymbol = Symbol.for("pi-copy-links:input-guard");
    const owner = TuiAltScreen.prototype;
    const baseline = Object.getOwnPropertyDescriptor(owner, "handleViewportInput");
    const nativeInput = baseline.value;
    let predecessor = function(data) { return nativeInput.call(this, data); };
    Object.defineProperty(predecessor, inputSymbol, { value: {
      baseMethod: nativeInput,
      baseDescriptor: baseline,
      passive: true,
      minimal: true,
      requiresQuiescence: true,
    }});
    Object.defineProperty(owner, "handleViewportInput", { ...baseline, value: predecessor });
    const predecessorRef = new WeakRef(predecessor);

    let adapter = installAdapter({
      version: VERSION,
      active: () => true,
      copy: async () => {},
      open: async () => {},
      notify: () => {},
      button: (text) => text,
      quiesce: () => false,
    });
    let active = owner.handleViewportInput;
    const activeRef = new WeakRef(active);
    const result = adapter.dispose();
    const successorPassive = owner.handleViewportInput;
    const successorMetadata = successorPassive[inputSymbol];
    adapter = null;
    predecessor = null;
    active = null;

    for (let turn = 0; turn < 16; turn++) {
      const pressure = Array.from({ length: 20_000 }, (_, index) => ({ index, turn }));
      global.gc();
      await new Promise((resolve) => setImmediate(resolve));
      if (pressure.length !== 20_000) throw new Error("allocation pressure corrupted");
    }
    const predecessorCollected = predecessorRef.deref() === undefined;
    const activeCollected = activeRef.deref() === undefined;

    let cleanup = installAdapter({
      version: VERSION,
      active: () => true,
      copy: async () => {},
      open: async () => {},
      notify: () => {},
      button: (text) => text,
      quiesce: () => true,
    });
    cleanup.dispose();
    cleanup = null;
    const prototypeRestored = owner.handleViewportInput === nativeInput;
    process.stdout.write(JSON.stringify({
      result,
      predecessorCollected,
      activeCollected,
      successorStillInstalled: successorPassive !== nativeInput,
      successorMinimal: successorMetadata?.minimal === true,
      directNativeBase: successorMetadata?.baseMethod === nativeInput,
      prototypeRestored,
    }));
  `;
  const result = spawnSync(process.execPath, [
    "--expose-gc", "--import", "tsx", "--input-type=module", "--eval", script,
  ], { cwd: process.cwd(), encoding: "utf8", timeout: 20_000 });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    result: { quiesced: false, passive: true },
    predecessorCollected: true,
    activeCollected: true,
    successorStillInstalled: true,
    successorMinimal: true,
    directNativeBase: true,
    prototypeRestored: true,
  });
});
