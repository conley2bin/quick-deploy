import assert from "node:assert/strict";
import test from "node:test";
import { VERSION, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  getCapabilities, getOsc8LinkAtColumn, Markdown, TuiAltScreen,
  type MarkdownTheme, type Terminal,
} from "@earendil-works/pi-tui";
import copyLinksExtension from "../index.ts";
import { checkCompatibility, compatibilityDiagnostic, installAdapter } from "../src/adapter.ts";
import { ActionStore } from "../src/source.ts";

const surfaces = [
  { owner: Markdown.prototype, key: "render", name: "Markdown.render" },
  { owner: Markdown.prototype, key: "renderToken", name: "Markdown.renderToken" },
  { owner: Markdown.prototype, key: "renderInlineTokens", name: "Markdown.renderInlineTokens" },
  { owner: TuiAltScreen.prototype, key: "handleViewportInput", name: "TuiAltScreen.handleViewportInput" },
];
const descriptors = () => surfaces.map(({ owner, key }) => Object.getOwnPropertyDescriptor(owner, key));
const clipboardSymbol = Symbol.for("pi-copy-links:clipboard-queue");
const identity = (text: string) => text;
const theme: MarkdownTheme = {
  heading: identity, link: identity, linkUrl: identity, code: identity, codeBlock: identity,
  codeBlockBorder: identity, quote: identity, quoteBorder: identity, hr: identity,
  listBullet: identity, bold: identity, italic: identity, strikethrough: identity, underline: identity,
};
function terminal() {
  return {
    columns: 64, rows: 40, kittyProtocolActive: false,
    start() { assert.fail("probe must not start a terminal"); }, stop() {},
    async drainInput() {}, write() {}, moveBy() {}, hideCursor() {}, showCursor() {},
    clearLine() {}, clearFromCursor() {}, clearScreen() {}, setTitle() {}, setProgress() {}, setProgramStatus() {},
  } satisfies Terminal;
}

test("compatible fictional version passes bounded real-adapter behavior without shared mutations", async () => {
  const before = descriptors();
  const capabilities = getCapabilities();
  const queueDescriptor = Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol);
  for (const version of ["fictional-Pi-999.1", "", VERSION]) {
    const result = await checkCompatibility(version);
    assert.equal(result.compatible, true, result.cause);
    assert.equal(result.version, version, "version is diagnostic metadata, including an empty label");
    assert.ok(result.probeMs >= 0);
    assert.equal(result.cause, undefined);
    assert.deepEqual(descriptors(), before);
    assert.equal(getCapabilities(), capabilities);
    assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol), queueDescriptor,
      "dummy clipboard actions cannot create or advance the process queue");
  }
});

test("each missing/unpatchable production hook rejects before installing isolated or real hooks", async () => {
  for (const surface of surfaces) {
    const baseline = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
    for (const kind of ["missing", "unpatchable"] as const) {
      try {
        Object.defineProperty(surface.owner, surface.key, {
          ...baseline, ...(kind === "missing" ? { value: undefined } : { writable: false }),
        });
        const before = descriptors();
        const result = await checkCompatibility("future-compatible-if-interfaces-work");
        assert.equal(result.compatible, false);
        assert.ok(result.cause?.includes(surface.name), result.cause);
        assert.match(result.cause!, kind === "missing" ? /缺少运行时方法/ : /不可写/);
        assert.deepEqual(descriptors(), before);
      } finally { Object.defineProperty(surface.owner, surface.key, baseline); }
    }
  }
});

test("same-signature token drift passes shape checks but fails observed source alignment", async () => {
  const surface = surfaces[1]!;
  const baseline = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
  const native = baseline.value;
  function drift(this: unknown, token: { type: string; text?: string }, width: number, next: string, style: unknown) {
    // Every hook remains callable with the same signature; only token semantics change.
    if (token.type === "code") token.text = "wrong native text";
    return native.call(this, token, width, next, style);
  }
  assert.equal(drift.length, native.length);
  try {
    Object.defineProperty(surface.owner, surface.key, { ...baseline, value: drift });
    const before = descriptors();
    const result = await checkCompatibility("same-shape-future");
    assert.equal(result.compatible, false);
    assert.match(result.cause!, /代码源文\/链接语义.*源文对齐动作/);
    assert.deepEqual(descriptors(), before);
  } finally { Object.defineProperty(surface.owner, surface.key, baseline); }
});

test("same-signature stale-source behavior is caught without inspecting host cache internals", async () => {
  const baseline = Object.getOwnPropertyDescriptor(Markdown.prototype, "setText")!;
  function stale(_text: string) { /* Simulates a host that keeps the old logical source. */ }
  assert.equal(stale.length, baseline.value.length);
  try {
    Object.defineProperty(Markdown.prototype, "setText", { ...baseline, value: stale });
    const result = await checkCompatibility("source-rerender-drift");
    assert.equal(result.compatible, false);
    assert.match(result.cause!, /source\/width rerender.*旧数据/);
  } finally { Object.defineProperty(Markdown.prototype, "setText", baseline); }
});

test("same-signature native-input drift is caught by selection and overlay behavior", async () => {
  const surface = surfaces[3]!;
  const baseline = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
  function inputDrift(_data: string) { return { consume: true }; }
  assert.equal(inputDrift.length, baseline.value.length);
  try {
    Object.defineProperty(surface.owner, surface.key, { ...baseline, value: inputDrift });
    const before = descriptors();
    const result = await checkCompatibility("input-behavior-drift");
    assert.equal(result.compatible, false);
    assert.match(result.cause!, /native mouse.*selection/);
    assert.deepEqual(descriptors(), before);
  } finally { Object.defineProperty(surface.owner, surface.key, baseline); }
});

test("probes preserve a live adapter and foreign delegating owners throughout awaits", async (t) => {
  const baseline = descriptors();
  const term = terminal();
  const copied: string[] = [], opened: string[] = [];
  let live: ReturnType<typeof installAdapter>;
  let markdown: Markdown;
  const tui = new TuiAltScreen(term, false, undefined, {
    openUrl() { assert.fail("private URI escaped to the live native opener"); }, copyOnSelect: false,
  });
  Object.defineProperty(tui, "requestRender", { value() {} });
  live = installAdapter({
    version: VERSION, active: () => true, copy: async (text) => { copied.push(text); },
    cwd: () => "/live", open: async (href) => { opened.push(href); }, notify() {}, button: identity,
    requestRender() {}, quiesce: () => { markdown.invalidate(); return markdown.render(64).every((line) => !line.includes("pi-copy://")); },
  });
  markdown = new Markdown("```sh\nlive\tdata  \n```\n\n[LIVE](https://live.example/)", 0, 0, theme, undefined, {
    transform: (source, availableWidth) => live.transform(source, { messageType: "assistant", isStreaming: false, availableWidth }),
  });
  const liveRuntime = tui as unknown as {
    previousScreen: string[]; previousScreenWidth: number; previousScreenHeight: number;
    handleViewportInput(data: string): unknown;
  };
  const paint = () => {
    markdown.invalidate(); liveRuntime.previousScreen = markdown.render(64);
    liveRuntime.previousScreenWidth = 64; liveRuntime.previousScreenHeight = 40;
  };
  const button = () => {
    for (const [y, line] of liveRuntime.previousScreen.entries()) {
      if (!line.includes("[复制]")) continue;
      for (let x = 0; x < 64; x++) {
        const url = getOsc8LinkAtColumn(line, x);
        if (url?.startsWith("pi-copy://")) return { x, y, url };
      }
    }
    assert.fail("missing live button");
  };
  const liveOwners: object[] = [];
  const realSet = ActionStore.prototype.set;
  const setSpy = t.mock.method(ActionStore.prototype, "set", function (this: ActionStore, owner: object, ...args: Parameters<ActionStore["set"]> extends [object, ...infer R] ? R : never) {
    if (owner === markdown) liveOwners.push(owner);
    return realSet.call(this, owner, ...args);
  });
  try {
    paint();
    const first = button();
    // A later foreign layer hides our hook metadata, forcing delegation through
    // the already-live wrapper. Hook-owner isolation must still hold.
    for (const surface of surfaces) {
      const own = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
      const predecessor = own.value;
      Object.defineProperty(surface.owner, surface.key, { ...own,
        value: function (this: unknown, ...args: unknown[]) { return predecessor.apply(this, args); },
      });
    }
    const foreign = descriptors();
    const queue = Reflect.get(globalThis, clipboardSymbol) as { tail: Promise<void> } | undefined;
    const priorTail = queue?.tail;
    const pending = checkCompatibility("probe-alongside-real-owner");
    // Re-render and activate a genuine mounted owner while the probe has yielded
    // at fake clipboard work. The action must remain in the genuine registry.
    paint();
    assert.equal(button().url, first.url);
    const point = button();
    liveRuntime.handleViewportInput(`\x1b[<0;${point.x + 1};${point.y + 1}M`);
    liveRuntime.handleViewportInput(`\x1b[<0;${point.x + 1};${point.y + 1}m`);
    const result = await pending;
    assert.equal(result.compatible, true, result.cause);
    await (Reflect.get(globalThis, clipboardSymbol) as { tail: Promise<void> }).tail;
    assert.deepEqual(copied, ["live\tdata  "], "real click reached only its real endpoint");
    assert.deepEqual(opened, []);
    assert.deepEqual(descriptors(), foreign, "foreign descriptors and methods stayed installed");
    assert.equal(live.status().available, true);
    assert.ok(liveOwners.length >= 2);
    // Each real-store set belongs to the real instance, never a probe subclass.
    const calls = setSpy.mock.calls;
    const realStore = calls.find((call) => call.arguments[0] === markdown)!.this;
    assert.ok(calls.filter((call) => call.this === realStore).every((call) => call.arguments[0] === markdown));
    assert.notEqual((Reflect.get(globalThis, clipboardSymbol) as { tail: Promise<void> }).tail, priorTail,
      "only the genuine copy advanced the process queue");
  } finally {
    // Test-owned foreign layers must be removed before normal owned disposal.
    for (const [index, surface] of surfaces.entries()) Object.defineProperty(surface.owner, surface.key, baseline[index]!);
    live.dispose();
  }
});

test("a pre-existing minimal passive reload guard is preserved and does not reject a conforming host", async () => {
  const surface = surfaces[3]!;
  const baseline = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
  let adapter: ReturnType<typeof installAdapter> | undefined;
  let markdown: Markdown;
  try {
    adapter = installAdapter({
      version: VERSION, active: () => true, copy: async () => {}, cwd: () => "/fixture", open: async () => {},
      notify() {}, button: identity, requestRender() {}, quiesce: () => false,
    });
    markdown = new Markdown("```sh\nretired\n```", 0, 0, theme, undefined, {
      transform: (source, availableWidth) => adapter!.transform(source, {
        messageType: "assistant", isStreaming: false, availableWidth,
      }),
    });
    assert.ok(markdown.render(64).some((line) => line.includes("pi-copy://")));
    assert.deepEqual(adapter.dispose(), { quiesced: false, passive: true });
    const before = descriptors();
    const result = await checkCompatibility("passive-guard-unknown-version");
    assert.equal(result.compatible, true, result.cause);
    assert.deepEqual(descriptors(), before, "the existing live stale-frame guard remains untouched");
  } finally {
    Object.defineProperty(surface.owner, surface.key, baseline);
    adapter?.dispose();
  }
});

test("success and behavior failures retire stores, restore owned descriptors and cancel all probe timers", async (t) => {
  const nativeTimeout = setTimeout, nativeClear = clearTimeout;
  const outstanding = new Set<ReturnType<typeof setTimeout>>();
  t.mock.method(globalThis, "setTimeout", ((callback: () => void, delay: number, ...args: unknown[]) => {
    const timer = nativeTimeout(callback, delay, ...args) as unknown as ReturnType<typeof setTimeout>;
    outstanding.add(timer); return timer;
  }) as typeof setTimeout);
  t.mock.method(globalThis, "clearTimeout", ((timer: ReturnType<typeof setTimeout>) => {
    outstanding.delete(timer); nativeClear(timer);
  }) as typeof clearTimeout);
  const stores: ActionStore[] = [];
  const retired = ActionStore.prototype.retire;
  t.mock.method(ActionStore.prototype, "retire", function (this: ActionStore) {
    stores.push(this); return retired.call(this);
  });
  const owners: Array<{ owner: object; prototype: object }> = [];
  const invalidate = Markdown.prototype.invalidate;
  t.mock.method(Markdown.prototype, "invalidate", function (this: Markdown) {
    if (!owners.some((item) => item.owner === this)) owners.push({ owner: this, prototype: Object.getPrototypeOf(this) });
    return invalidate.call(this);
  });
  const baseline = descriptors();
  const queue = Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol);
  for (const failure of [false, true]) {
    const setText = Object.getOwnPropertyDescriptor(Markdown.prototype, "setText")!;
    try {
      if (failure) Object.defineProperty(Markdown.prototype, "setText", { ...setText, value(_source: string) {} });
      const result = await checkCompatibility("cleanup-fixture");
      assert.equal(result.compatible, !failure, result.cause);
      assert.equal(outstanding.size, 0, "flash/recovery timers were synchronously cancelled");
      assert.deepEqual(descriptors(), baseline);
      assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol), queue);
    } finally { Object.defineProperty(Markdown.prototype, "setText", setText); }
  }
  assert.equal(stores.length, 2);
  assert.ok(stores.every((store) => !store.isTrusted()));
  assert.ok(stores.every((store) => (store as unknown as { entries: Map<string, unknown> }).entries.size === 0));
  assert.equal(owners.length, 2, "one bounded Markdown fixture per probe");
  for (const item of owners) {
    assert.notEqual(item.prototype, Markdown.prototype);
    for (const surface of surfaces.slice(0, 3)) {
      // The isolated subclass restores the exact method captured from the host;
      // none of its hook functions retains the retired adapter's active wrapper.
      assert.equal(Reflect.get(item.prototype, surface.key), Reflect.get(surface.owner, surface.key));
    }
  }
});

function startupFixture(mode: string, tuiMode: "fullscreen" | "regular" = "fullscreen") {
  const events = new Map<string, (event: unknown, ctx: unknown) => Promise<unknown>>();
  const commands = new Map<string, { handler: (args: string, ctx: unknown) => Promise<unknown> }>();
  const notices: Array<{ text: string; level: string }> = [];
  const tui = { mode: tuiMode, previousScreen: [], invalidate() {}, requestRender() {}, renderNow() {} };
  let transforms = 0, widgets = 0;
  const pi = {
    registerMarkdownTransformer() { transforms += 1; },
    on(name: string, handler: (event: unknown, ctx: unknown) => Promise<unknown>) { events.set(name, handler); },
    registerCommand(name: string, command: { handler: (args: string, ctx: unknown) => Promise<unknown> }) { commands.set(name, command); },
  } as unknown as ExtensionAPI;
  const ctx = { mode, cwd: "/fixture", ui: {
    theme: { fg: (_color: string, text: string) => text },
    notify(text: string, level: string) { notices.push({ text, level }); },
    setWidget(_key: string, factory?: (current: unknown) => unknown) { widgets += 1; factory?.(tui); },
  } };
  copyLinksExtension(pi);
  return { ctx, notices, events, commands, get widgets() { return widgets; }, get transforms() { return transforms; } };
}

test("TUI concrete failure warns once, preserves Markdown, and /copy-links reports the cause", async () => {
  const baseline = Object.getOwnPropertyDescriptor(Markdown.prototype, "renderToken")!;
  try {
    Object.defineProperty(Markdown.prototype, "renderToken", { ...baseline, value: undefined });
    const before = descriptors();
    const f = startupFixture("tui");
    await f.events.get("session_start")!({}, f.ctx);
    await f.events.get("session_start")!({}, f.ctx);
    assert.equal(f.notices.length, 1);
    assert.equal(f.notices[0]!.level, "warning");
    assert.match(f.notices[0]!.text, /增强已停用.*Markdown\.renderToken.*原生 Markdown\/Pi 保持可用/);
    assert.ok(f.notices[0]!.text.length < 400);
    assert.deepEqual(descriptors(), before);
    await f.commands.get("copy-links")!.handler("", f.ctx);
    assert.equal(f.notices.length, 2);
    assert.equal(f.notices[1]!.text, f.notices[0]!.text);
    await f.events.get("session_shutdown")!({}, f.ctx);
  } finally { Object.defineProperty(Markdown.prototype, "renderToken", baseline); }
});

test("non-TUI session modes never probe, install hooks, mutate queues or acquire endpoints", async (t) => {
  const realSet = t.mock.method(ActionStore.prototype, "set", () => assert.fail("non-TUI must not probe"));
  const before = descriptors(), queue = Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol);
  for (const mode of ["rpc", "json", "print"]) {
    const f = startupFixture(mode);
    await f.events.get("session_start")!({}, f.ctx);
    assert.equal(f.widgets, 0); assert.deepEqual(f.notices, []);
    assert.equal(f.transforms, 1, "transformer remains a passive source identity");
    await f.events.get("session_shutdown")!({}, f.ctx);
  }
  assert.equal(realSet.mock.callCount(), 0);
  assert.deepEqual(descriptors(), before);
  assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, clipboardSymbol), queue);
});

test("/copy-links reports measured real compatibility after successful TUI startup", async () => {
  // This test process, not the user's pane, omits tmux acquisition.
  const savedTmux = process.env.TMUX;
  delete process.env.TMUX;
  const f = startupFixture("tui"), before = descriptors();
  try {
    await f.events.get("session_start")!({}, f.ctx);
    await f.commands.get("copy-links")!.handler("", f.ctx);
    assert.match(f.notices.at(-1)!.text, /已启用.*\n实际兼容检查通过.*隔离探针 [\d.]+ ms/s);
    assert.equal(f.notices.at(-1)!.level, "info");
  } finally {
    await f.events.get("session_shutdown")!({}, f.ctx);
    if (savedTmux === undefined) delete process.env.TMUX; else process.env.TMUX = savedTmux;
  }
  assert.deepEqual(descriptors(), before);
});

test("regular TUI startup reports English fullscreen guidance", async () => {
  const savedTmux = process.env.TMUX;
  delete process.env.TMUX;
  const f = startupFixture("tui", "regular"), before = descriptors();
  try {
    await f.events.get("session_start")!({}, f.ctx);
    assert.deepEqual(f.notices, [{
      text: "pi-copy-links loaded. Set TUI mode to fullscreen in /settings: left-click the copy button in the lower-right corner of a code block, or Ctrl+left-click a Markdown web/file link to open it in a new Chrome/VS Code window. Regular mode leaves history scrolling to the terminal, so these actions are unavailable.",
      level: "info",
    }]);
    assert.doesNotMatch(f.notices[0]!.text, /[\u3400-\u9fff]/);
  } finally {
    await f.events.get("session_shutdown")!({}, f.ctx);
    if (savedTmux === undefined) delete process.env.TMUX; else process.env.TMUX = savedTmux;
  }
  assert.deepEqual(descriptors(), before);
});

test("diagnostics bound arbitrary version/cause bytes and retain the first specific cause", () => {
  const message = compatibilityDiagnostic("future\n" + "v".repeat(1000), new Error("missing hook\n" + "x".repeat(2000)));
  assert.match(message, /missing hook/);
  assert.doesNotMatch(message, /\n/);
  assert.ok(message.length < 400);
});
