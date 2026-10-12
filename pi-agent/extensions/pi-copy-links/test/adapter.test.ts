import assert from "node:assert/strict";
import test from "node:test";
import { AssistantMessageComponent, getMarkdownTheme, initTheme, VERSION } from "@earendil-works/pi-coding-agent";
import {
  getCapabilities, getOsc8LinkAtColumn, hyperlink, Markdown, setCapabilities, stripTerminalSequences,
  TuiAltScreen, visibleWidth, type MarkdownTheme, type Terminal,
} from "@earendil-works/pi-tui";
import { installAdapter, type OpenOrigin } from "../src/adapter.ts";
import type { OpenLifetime } from "../src/browser.ts";

// Count WeakRef constructions for every adapter installed below. V8 both caps a
// Set at 2^24 entries and registers each WeakRef's target in a kept-objects list
// that drains only at event-loop turn boundaries, so sustaining those
// constructions from a render hook crashed Pi with
// "RangeError: Set maximum size exceeded" thrown out of render().
let weakRefCount = 0;
let weakRefCalls = 0;
const realWeakRef = WeakRef;
const weakRefFailureCalls = new Set<number>();
let weakRefAlwaysFails = false;
let weakRefsAreDead = false;
globalThis.WeakRef = new Proxy(realWeakRef, {
  construct(target, args: [object]) {
    // Pi 1.1.0 also caches parsed Markdown tokens in a WeakRef. The regression
    // being measured here is the adapter's bookkeeping, so let only that exact
    // host cache record pass and count adapter probe/owner registrations.
    const candidate = args[0] as { source?: unknown; tokens?: unknown };
    if (typeof candidate.source === "string" && Array.isArray(candidate.tokens)) {
      return Reflect.construct(target, args);
    }
    weakRefCalls += 1;
    if (weakRefAlwaysFails || weakRefFailureCalls.delete(weakRefCalls)) {
      throw new RangeError("Set maximum size exceeded");
    }
    weakRefCount += 1;
    if (weakRefsAreDead) return { deref: () => undefined };
    return Reflect.construct(target, args);
  },
}) as typeof WeakRef;

initTheme("dark", false);

class FakeTerminal implements Terminal {
  columns = 64; rows = 20; kittyProtocolActive = false;
  output = ""; input: (data: string) => void = () => {};
  start(input: (data: string) => void) { this.input = input; }
  stop() {} async drainInput() {} write(data: string) { this.output += data; }
  moveBy() {} hideCursor() {} showCursor() {} clearLine() {} clearFromCursor() {}
  clearScreen() {} setTitle() {} setProgress() {} setProgramStatus() {}
}

function message(text: string) {
  return {
    role: "assistant" as const, content: [{ type: "text" as const, text }],
    api: "openai-completions" as const, provider: "fixture", model: "fixture", timestamp: 0,
    stopReason: "stop" as const,
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
  };
}
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
function sgr(x: number, y: number, bits = 0, edge = 'M') { return `\x1b[<${bits};${x + 1};${y + 1}${edge}`; }
function tagged(terminal: FakeTerminal, clientPid: number | string, data: string) {
  terminal.input(`\x1b[998;${clientPid}~`);
  terminal.input(data);
}
function taggedClick(terminal: FakeTerminal, point: {x: number; y: number}, clientPid: number, bits = 16) {
  tagged(terminal, clientPid, sgr(point.x, point.y, bits));
  tagged(terminal, clientPid, sgr(point.x, point.y, bits, 'm'));
}
function screen(tui: TuiAltScreen): string[] { return (tui as unknown as { previousScreen: string[] }).previousScreen; }
function target(tui: TuiAltScreen, prefix: string) {
  for (const [y, line] of screen(tui).entries()) for (let x = 0; x < tui.terminal.columns; x++) {
    const url = getOsc8LinkAtColumn(line, x);
    if (url?.startsWith(prefix)) return { x, y, url };
  }
  assert.fail(`Missing ${prefix} in:\n${screen(tui).map(stripTerminalSequences).join('\n')}`);
}
function targetUrl(tui: TuiAltScreen, expected: string) {
  for (const [y, line] of screen(tui).entries()) for (let x = 0; x < tui.terminal.columns; x++) {
    if (getOsc8LinkAtColumn(line, x) === expected) return { x, y, url: expected };
  }
  assert.fail(`Missing ${expected} in:\n${screen(tui).map(stripTerminalSequences).join('\n')}`);
}
const runtimeSurfaces = [
  { name: "Markdown.render", owner: Markdown.prototype as unknown as Record<string, unknown>, key: "render" },
  { name: "Markdown.renderToken", owner: Markdown.prototype as unknown as Record<string, unknown>, key: "renderToken" },
  {
    name: "Markdown.renderInlineTokens",
    owner: Markdown.prototype as unknown as Record<string, unknown>,
    key: "renderInlineTokens",
  },
  {
    name: "TuiAltScreen.handleViewportInput",
    owner: TuiAltScreen.prototype as unknown as Record<string, unknown>,
    key: "handleViewportInput",
  },
] as const;
const runtimeMethods = () => runtimeSurfaces.map(({ owner, key }) => owner[key]);
const runtimeDescriptors = () => runtimeSurfaces.map(({ owner, key }) =>
  Object.getOwnPropertyDescriptor(owner, key));
function targets(tui: TuiAltScreen, prefix: string) {
  const found: Array<{ x: number; y: number; url: string }> = [];
  const seen = new Set<string>();
  for (const [y, line] of screen(tui).entries()) for (let x = 0; x < tui.terminal.columns; x++) {
    const url = getOsc8LinkAtColumn(line, x);
    if (url?.startsWith(prefix) && !seen.has(url)) {
      found.push({ x, y, url });
      seen.add(url);
    }
  }
  return found;
}
function click(terminal: FakeTerminal, point: {x: number; y: number}, bits = 0) {
  terminal.input(sgr(point.x, point.y, bits));
  terminal.input(sgr(point.x, point.y, bits, 'm'));
}
interface FixtureOptions {
  quiesce?: "success" | "failure" | "throw";
  copy?: (text: string) => Promise<void>;
  open?: (href: string, cwd: string, lifetime: OpenLifetime, origin: OpenOrigin) => Promise<void>;
  tmux?: boolean;
  cwd?: string;
  copySelection?: (text: string) => Promise<boolean>;
  installer?: typeof installAdapter;
}
function fixture(
  source: string,
  tmuxRecovery = false,
  flashMs?: number,
  copyFailure?: Error,
  fixtureOptions: FixtureOptions = {},
) {
  const terminal = new FakeTerminal();
  const copied: string[] = [], opened: string[] = [], nativeOpened: string[] = [], notices: string[] = [], errorNotices: string[] = [];
  const openRequests: Array<{ href: string; cwd: string; lifetime: OpenLifetime; origin: OpenOrigin }> = [];
  let currentCwd = fixtureOptions.cwd ?? "/fixture";
  let active = true;
  let renderRequests = 0;
  let tui: TuiAltScreen;
  const adapter = (fixtureOptions.installer ?? installAdapter)({ version: VERSION, active: () => active,
    copy: async text => {
      if (fixtureOptions.copy) return fixtureOptions.copy(text);
      if (copyFailure) throw copyFailure;
      copied.push(text);
    }, cwd: () => currentCwd, requireTmuxOrigin: fixtureOptions.tmux ?? false,
    open: async (href, cwd, lifetime, origin) => {
      openRequests.push({ href, cwd, lifetime, origin });
      if (fixtureOptions.open) await fixtureOptions.open(href, cwd, lifetime, origin);
      else opened.push(href);
    },
    notify: (text, error) => { notices.push(text); if (error) errorNotices.push(text); }, button: text => text,
    recoverTmuxRelease: () => tmuxRecovery, requestRender: () => { renderRequests += 1; },
    quiesce: () => {
      if (fixtureOptions.quiesce === "throw") throw new Error("quiescence failed");
      if (fixtureOptions.quiesce === "failure") return false;
      tui.invalidate();
      tui.renderNow(true);
      return screen(tui).every((row) => !row.includes("pi-copy://"));
    },
    flashMs });
  const component = new AssistantMessageComponent(message(source), false, undefined, "Thinking", 1, [adapter.transform]);
  tui = new TuiAltScreen(terminal, false, undefined, {
    openUrl: url => nativeOpened.push(url),
    copySelection: fixtureOptions.copySelection ?? (async () => true),
  });
  tui.addChild(component); tui.start(); tui.renderNow();
  return { terminal, component, tui, copied, opened, openRequests, nativeOpened, notices, errorNotices, adapter,
    get renderRequests() { return renderRequests; },
    setCwd(value: string) { currentCwd = value; },
    setActive(value: boolean) { active = value; component.invalidate(); tui.renderNow(); },
    close() { const result = adapter.dispose(); tui.stop(); return result; } };
}

test("native assistant buttons copy exact code, including nested tabs and no visual wraps", async () => {
  for (const [source, expected] of [
    ['```sh\nprintf "%s\\n" ' + 'abcdefgh'.repeat(15) + '\n```', 'printf "%s\\n" ' + 'abcdefgh'.repeat(15)],
    ['- ```make\n  target:\n  \techo done  \n  ```', 'target:\n\techo done  '],
    ['> ```py\n> if True:\n>     print("中文")\n> ```', 'if True:\n    print("中文")'],
    ['Instructions\n\n    echo a\n    \techo b', 'echo a\n\techo b'],
    ['```\n```', ''],
    ['```sh   example\necho metadata\n```', 'echo metadata'],
  ]) {
    const f = fixture(source!);
    try {
      const button = target(f.tui, 'pi-copy://'); click(f.terminal, button); await tick();
      assert.deepEqual(f.copied, [expected], source);
      assert.deepEqual(f.nativeOpened, []);
      assert.ok(stripTerminalSequences(screen(f.tui)[button.y]!).trimEnd().endsWith('[复制]'));
      assert.equal(visibleWidth(screen(f.tui)[button.y]!), f.terminal.columns);
    } finally { f.close(); }
  }
});

test("Pi 0.99.1 source-keyed parsed tokens keep copies and links current across source, width and theme changes", async (t) => {
  if (VERSION !== "0.99.1") {
    t.skip("source-keyed parsed-token cache was added in Pi 0.99.1");
    return;
  }
  const terminal = new FakeTerminal();
  const copies: string[] = [], opened: string[] = [], nativeOpened: string[] = [];
  const adapter = installAdapter({ version: VERSION, active: () => true,
    copy: async (text) => { copies.push(text); }, cwd: () => "/fixture",
    open: async (href) => { opened.push(href); }, notify: () => {}, button: (text) => text,
    quiesce: () => {
      tui.invalidate(); tui.renderNow(true);
      return screen(tui).every((row) => !row.includes("pi-copy://"));
    },
  });
  const source = "```make\ntarget:\n\techo done  \n```\n\n[WEB](https://example.com/?a=1&amp;b=2)";
  const markdown = new Markdown(source, 1, 0, getMarkdownTheme(), undefined, {
    transform: (text, availableWidth) => adapter.transform(text, {
      messageType: "assistant", isStreaming: false, availableWidth,
    }),
  });
  const tui = new TuiAltScreen(terminal, false, undefined, { openUrl: (url) => nativeOpened.push(url) });
  const parsedCache = markdown as unknown as {
    cachedTokens?: { source: string; tokens: unknown[] };
    theme: MarkdownTheme;
  };
  const button = () => {
    const found = targets(tui, "pi-copy://").filter((point) =>
      stripTerminalSequences(screen(tui)[point.y]!).includes("[复制]"));
    assert.equal(found.length, 1, "one visible source-aligned code button");
    return found[0]!;
  };
  const web = () => {
    const found = targets(tui, "pi-copy://").filter((point) =>
      stripTerminalSequences(screen(tui)[point.y]!).includes("WEB"));
    assert.equal(found.length, 1, "one source-aligned web link");
    return found[0]!;
  };
  try {
    tui.addChild(markdown); tui.start(); tui.renderNow(true);
    const cachedTokens = parsedCache.cachedTokens?.tokens;
    assert.ok(cachedTokens, "host parsed-token cache exists");
    click(terminal, button()); await tick();
    assert.deepEqual(copies, ["target:\n\techo done  "]);
    click(terminal, web(), 16); await tick();
    assert.deepEqual(opened, ["https://example.com/?a=1&b=2"]);

    // Replacing a Tab by exactly three spaces preserves the host's normalized
    // cache key but changes the only source-exact clipboard payload.
    markdown.setText(source.replace("\techo", "   echo"));
    terminal.columns = 42;
    tui.renderNow(true);
    assert.equal(parsedCache.cachedTokens?.tokens, cachedTokens, "new source reused the same native tokens");
    click(terminal, button()); await tick();
    assert.deepEqual(copies, ["target:\n\techo done  ", "target:\n   echo done  "]);
    const theme = parsedCache.theme;
    parsedCache.theme = { ...theme, codeBlockBorder: (text) => `\x1b[34m${theme.codeBlockBorder(text)}\x1b[39m` };
    markdown.invalidate();
    terminal.columns = 54;
    tui.renderNow(true);
    assert.equal(parsedCache.cachedTokens?.tokens, cachedTokens, "width/theme rerender reused parsed tokens");
    click(terminal, button()); await tick();
    click(terminal, web(), 16); await tick();
    assert.deepEqual(copies, ["target:\n\techo done  ", "target:\n   echo done  ", "target:\n   echo done  "]);
    assert.deepEqual(opened, ["https://example.com/?a=1&b=2", "https://example.com/?a=1&b=2"]);
    assert.deepEqual(nativeOpened, [], "private URL never escaped to Pi's opener");
  } finally { adapter.dispose(); tui.stop(); }
});

test("Pi 0.86.1 parser changes keep source blocks and reference URLs aligned", async (t) => {
  if (VERSION !== "0.86.1") {
    t.skip("Marked 18.0.11 compatibility boundary is specific to Pi 0.86.1");
    return;
  }
  for (const [source, expected] of [
    ["before\n```", ""],
    ["before\n~~~", ""],
    ["- [ ] first\n\n  ```make\n  target:\n  \techo task  \n  ```\n- [x] second", "target:\n\techo task  "],
    ["> ```sh\n> echo quote\n> ```\n> > nested\nlazy\n> rest", "echo quote"],
  ]) {
    const f = fixture(source!);
    try {
      click(f.terminal, target(f.tui, "pi-copy://")); await tick();
      assert.deepEqual(f.copied, [expected], source);
      assert.deepEqual(f.errorNotices, [], source);
    } finally { f.close(); }
  }

  const inner = "https://inner.example/path?a=1&b=two#inner";
  const outer = "https://outer.example/path?x=3&y=four#outer";
  const f = fixture(`[outer [inner][inside]][outside]\n\n[inside]: ${inner}\n[outside]: ${outer}`);
  try {
    const links = targets(f.tui, 'pi-copy://');
    assert.equal(links.length, 2);
    click(f.terminal, links[0]!, 16); await tick();
    click(f.terminal, links[1]!, 16); await tick();
    assert.deepEqual(f.opened, [inner, outer]);
    assert.deepEqual(f.nativeOpened, []);
  } finally { f.close(); }
});

test("divergent hidden blockquote code cannot steal a visible source-exact button", async (t) => {
  if (VERSION !== "0.86.1") {
    t.skip("the hidden-blockquote omission is a Marked 18.0.11 regression boundary");
    return;
  }
  const source = '> > nested\nlazy\n> ```sh\n> \tcmd\n> ```\n\n```sh\n   cmd\n```';
  const f = fixture(source);
  try {
    const buttons = targets(f.tui, 'pi-copy://');
    assert.equal(buttons.length, 1, "only the native visible block is actionable");
    click(f.terminal, buttons[0]!); await tick();
    assert.deepEqual(f.copied, ['   cmd'], "the visible spaces must not resolve to the hidden Tab source");
    assert.deepEqual(f.errorNotices, []);
  } finally { f.close(); }
});

test("ambiguous parser divergence omits the button and warns", async () => {
  const source =
    '> > nested\nlazy\n> ```sh\n> \tcmd\n> ```\n\n' +
    '> > other\n> ```sh\n>    cmd\n> ```';
  const f = fixture(source);
  try {
    assert.deepEqual(targets(f.tui, 'pi-copy://'), []);
    await tick();
    assert.equal(f.errorNotices.length, 1);
    assert.match(f.errorNotices[0]!, /无法唯一对应/);
  } finally { f.close(); }
});

test("ordinary repeated native blocks retain distinct source-order mappings", async () => {
  for (const [source, expected] of [
    ['```sh\nsame\n```\n\n```sh\nsame\n```', ['same', 'same']],
    ['```sh\n\tcmd\n```\n\n```sh\n   cmd\n```', ['\tcmd', '   cmd']],
  ] as const) {
    const f = fixture(source);
    try {
      const buttons = targets(f.tui, 'pi-copy://');
      assert.equal(buttons.length, 2, source);
      for (const button of buttons) { click(f.terminal, button); await tick(); }
      assert.deepEqual(f.copied, [...expected], source);
      assert.deepEqual(f.errorNotices, [], source);
    } finally { f.close(); }
  }
});

test("rendering an instance any number of times allocates at most one WeakRef", () => {
  // Regression: the hook used to run `tracked.add(new WeakRef(this))` on every
  // render call. V8 caps a Set at 2^24 entries, so sustained streaming killed the
  // process with "RangeError: Set maximum size exceeded" thrown out of render().
  const f = fixture('```sh\necho first\n```\n\nprose\n\n```sh\necho second\n```');
  try {
    const mounted = weakRefCount;
    for (let pass = 0; pass < 100_000; pass++) f.tui.renderNow();
    assert.equal(weakRefCount, mounted, "100k renders of one mounted instance allocate no WeakRef");
    f.tui.scrollToTop(); f.terminal.columns = 40;
    for (let pass = 0; pass < 50; pass++) f.tui.renderNow();
    assert.equal(weakRefCount, mounted, "scroll and resize re-render without allocating");
    f.component.invalidate();
    f.tui.renderNow();
    // Pi rebuilds the component's Markdown children inside updateContent, so one
    // invalidated message legitimately yields one registration per content block —
    // never one per render.
    const rebuilt = weakRefCount - mounted;
    assert.ok(rebuilt > 0 && rebuilt <= 3, `invalidation rebuilds at most one ref per content block, got ${rebuilt}`);
    for (let pass = 0; pass < 50; pass++) f.tui.renderNow();
    assert.equal(weakRefCount, mounted + rebuilt, "the rebuilt instances then stop allocating");
    const point = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(point.x, point.y)); f.tui.renderNow();
    assert.equal(weakRefCount, mounted + rebuilt, "press feedback allocates no WeakRef");
    assert.equal(target(f.tui, 'pi-copy://').url, point.url, "buttons stay tied to their blocks after 100k renders");
  } finally { f.close(); }
});

test("first and second WeakRef failures each degrade atomically and recover", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const offset of [1, 2]) {
    weakRefFailureCalls.add(weakRefCalls + offset);
    const f = fixture('```sh\necho x\n```');
    try {
      assert.deepEqual(targets(f.tui, 'pi-copy://'), [], `WeakRef call ${offset} leaves native Markdown`);
      assert.deepEqual(f.errorNotices, []);
      t.mock.timers.tick(1000);
      f.component.invalidate();
      f.tui.renderNow();
      assert.equal(targets(f.tui, 'pi-copy://').length, 1, `WeakRef call ${offset} recovers cleanly`);
    } finally { f.close(); }
  }
});

test("tracked Set.add failure rolls back registration without escaping render", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalAdd = Set.prototype.add;
  let fail = true;
  Set.prototype.add = function(this: Set<unknown>, value: unknown) {
    if (fail && value instanceof realWeakRef) {
      fail = false;
      throw new RangeError("Set maximum size exceeded");
    }
    return originalAdd.call(this, value);
  } as typeof Set.prototype.add;
  let f: ReturnType<typeof fixture> | undefined;
  try {
    assert.doesNotThrow(() => { f = fixture('```sh\necho x\n```'); });
    assert.deepEqual(targets(f!.tui, 'pi-copy://'), []);
  } finally { Set.prototype.add = originalAdd; }
  try {
    t.mock.timers.tick(1000);
    f!.component.invalidate();
    f!.tui.renderNow();
    assert.equal(targets(f!.tui, 'pi-copy://').length, 1);
  } finally { f?.close(); }
});

test("secondary finalizer rollback failure cannot escape owner registration", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalRegister = FinalizationRegistry.prototype.register;
  const originalUnregister = FinalizationRegistry.prototype.unregister;
  let failed = false;
  FinalizationRegistry.prototype.register = function(
    this: FinalizationRegistry<unknown>, ...args: Parameters<typeof originalRegister>
  ) {
    if (!failed) {
      failed = true;
      throw new RangeError('primary finalizer failure');
    }
    return Reflect.apply(originalRegister, this, args);
  } as typeof FinalizationRegistry.prototype.register;
  FinalizationRegistry.prototype.unregister = function() {
    throw new RangeError('secondary rollback failure');
  };
  let f: ReturnType<typeof fixture> | undefined;
  try {
    assert.doesNotThrow(() => { f = fixture('```sh\necho x\n```'); });
    assert.deepEqual(targets(f!.tui, 'pi-copy://'), []);
  } finally {
    FinalizationRegistry.prototype.register = originalRegister;
    FinalizationRegistry.prototype.unregister = originalUnregister;
  }
  try {
    t.mock.timers.tick(1000);
    f!.component.invalidate(); f!.tui.renderNow();
    assert.equal(targets(f!.tui, 'pi-copy://').length, 1);
  } finally { f?.close(); }
});

test("failed action generation is detached and every retired URI remains suppressed", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalRegister = FinalizationRegistry.prototype.register;
  const originalUnregister = FinalizationRegistry.prototype.unregister;
  let oldUrl: string | undefined;
  let failed = false;
  FinalizationRegistry.prototype.register = function(
    this: FinalizationRegistry<unknown>, ...args: Parameters<typeof originalRegister>
  ) {
    const holdings = args[1];
    const result = Reflect.apply(originalRegister, this, args);
    if (!failed && typeof holdings === 'string' && holdings.startsWith('pi-copy://')) {
      failed = true;
      oldUrl = holdings;
      throw new RangeError('action finalizer committed then failed');
    }
    return result;
  } as typeof FinalizationRegistry.prototype.register;
  FinalizationRegistry.prototype.unregister = function() {
    throw new RangeError('action rollback unregister failed');
  };
  let f: ReturnType<typeof fixture> | undefined;
  try {
    assert.doesNotThrow(() => { f = fixture('```sh\necho old\n```'); });
    assert.ok(oldUrl);
    assert.deepEqual(targets(f!.tui, 'pi-copy://'), []);
  } finally {
    FinalizationRegistry.prototype.register = originalRegister;
    FinalizationRegistry.prototype.unregister = originalUnregister;
  }

  const staleClick = () => {
    const state = f!.tui as unknown as {
      previousScreen: string[]; previousScreenWidth: number; previousScreenHeight: number;
    };
    state.previousScreen = [hyperlink('stale', oldUrl!)];
    state.previousScreenWidth = f!.terminal.columns;
    state.previousScreenHeight = f!.terminal.rows;
    click(f!.terminal, { x: 0, y: 0 });
  };
  try {
    staleClick();
    assert.deepEqual(f!.copied, []);
    assert.deepEqual(f!.nativeOpened, []);
    t.mock.timers.tick(1000);
    f!.component.invalidate(); f!.tui.renderNow();
    const replacement = target(f!.tui, 'pi-copy://');
    assert.notEqual(replacement.url.slice(0, replacement.url.lastIndexOf('/') + 1),
      oldUrl!.slice(0, oldUrl!.lastIndexOf('/') + 1));
    staleClick();
    assert.deepEqual(f!.copied, [], 'retired URI cannot copy after recovery');
    assert.deepEqual(f!.nativeOpened, [], 'retired URI cannot reach native opener');
    f!.component.invalidate(); f!.tui.renderNow();
    click(f!.terminal, target(f!.tui, 'pi-copy://'));
  } finally { f?.close(); }
});

test("suspension cleanup failures are contained and a fresh generation recovers", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture('```sh\necho first\n```');
  const originalMapClear = Map.prototype.clear;
  const originalSetClear = Set.prototype.clear;
  const originalUnregister = FinalizationRegistry.prototype.unregister;
  weakRefFailureCalls.add(weakRefCalls + 1);
  Map.prototype.clear = function() { throw new RangeError('suspend map clear failed'); };
  Set.prototype.clear = function() { throw new RangeError('suspend set clear failed'); };
  FinalizationRegistry.prototype.unregister = function() {
    throw new RangeError('suspend unregister failed');
  };
  try {
    f.component.invalidate();
    assert.doesNotThrow(() => f.tui.renderNow());
    assert.deepEqual(targets(f.tui, 'pi-copy://'), []);
  } finally {
    Map.prototype.clear = originalMapClear;
    Set.prototype.clear = originalSetClear;
    FinalizationRegistry.prototype.unregister = originalUnregister;
  }
  try {
    t.mock.timers.tick(1000);
    f.component.invalidate(); f.tui.renderNow();
    assert.equal(targets(f.tui, 'pi-copy://').length, 1);
  } finally { f.close(); }
});

test("recovery stays disabled when fresh generation construction fails", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  weakRefAlwaysFails = true;
  const f = fixture('```sh\necho x\n```');
  weakRefAlwaysFails = false;
  const RealFinalizationRegistry = globalThis.FinalizationRegistry;
  globalThis.FinalizationRegistry = class {
    constructor() { throw new RangeError('new generation finalizer failed'); }
  } as unknown as typeof FinalizationRegistry;
  try {
    t.mock.timers.tick(1000);
    f.component.invalidate(); f.tui.renderNow();
    assert.deepEqual(targets(f.tui, 'pi-copy://'), []);
  } finally { globalThis.FinalizationRegistry = RealFinalizationRegistry; }
  try {
    t.mock.timers.tick(1000);
    f.component.invalidate(); f.tui.renderNow();
    assert.equal(targets(f.tui, 'pi-copy://').length, 1);
  } finally { f.close(); }
});

test("recovery rejects a fresh generation whose probe cleanup is incomplete", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  weakRefAlwaysFails = true;
  const f = fixture('```sh\necho x\n```');
  weakRefAlwaysFails = false;
  const originalUnregister = FinalizationRegistry.prototype.unregister;
  FinalizationRegistry.prototype.unregister = function() {
    throw new RangeError('probe unregister failed');
  };
  try {
    t.mock.timers.tick(1000);
    f.component.invalidate(); f.tui.renderNow();
    assert.deepEqual(targets(f.tui, 'pi-copy://'), []);
  } finally { FinalizationRegistry.prototype.unregister = originalUnregister; }
  try {
    t.mock.timers.tick(1000);
    f.component.invalidate(); f.tui.renderNow();
    assert.equal(targets(f.tui, 'pi-copy://').length, 1);
  } finally { f.close(); }
});

test("dead owner churn is pruned during registration", () => {
  const originalAdd = Set.prototype.add;
  let maximumTracked = 0;
  weakRefsAreDead = true;
  Set.prototype.add = function(this: Set<unknown>, value: unknown) {
    const result = originalAdd.call(this, value);
    if (value && typeof value === 'object' && 'deref' in value) maximumTracked = Math.max(maximumTracked, this.size);
    return result;
  } as typeof Set.prototype.add;
  const adapter = installAdapter({ version: VERSION, active: () => true,
    copy: async () => {}, cwd: () => '/fixture', open: async () => {}, notify: () => {}, button: text => text });
  try {
    for (let index = 0; index < 10_000; index++) {
      const markdown = new Markdown(`owner ${index}`, 0, 0, getMarkdownTheme(), undefined, {
        transform: (source, availableWidth) => adapter.transform(source, {
          messageType: 'user', isStreaming: false, availableWidth,
        }),
      });
      markdown.render(40);
    }
    assert.ok(maximumTracked <= 1, `dead WeakRefs are pruned before add; maximum was ${maximumTracked}`);
  } finally {
    adapter.dispose();
    Set.prototype.add = originalAdd;
    weakRefsAreDead = false;
  }
});

test("failed later-turn probes retry eight times, notify once, and stop", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  weakRefAlwaysFails = true;
  const f = fixture('```sh\necho x\n```');
  try {
    assert.deepEqual(targets(f.tui, 'pi-copy://'), []);
    for (let attempt = 1; attempt < 8; attempt++) {
      t.mock.timers.tick(1000);
      assert.deepEqual(f.errorNotices, [], `attempt ${attempt} is not terminal`);
    }
    t.mock.timers.tick(1000);
    assert.equal(f.errorNotices.length, 1);
    assert.match(f.errorNotices[0]!, /连续 8 次无法恢复/);
    const calls = weakRefCalls;
    t.mock.timers.tick(100_000);
    assert.equal(weakRefCalls, calls, "terminal suspension schedules no ninth probe");
    assert.equal(f.errorNotices.length, 1, "terminal notification is emitted once");
  } finally { weakRefAlwaysFails = false; f.close(); }
});

test("recovery succeeds after failed later-turn probes and disposal cancels a pending probe", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  weakRefAlwaysFails = true;
  const f = fixture('```sh\necho x\n```');
  try {
    t.mock.timers.tick(1000);
    t.mock.timers.tick(1000);
    weakRefAlwaysFails = false;
    const requestsBeforeSuccess = f.renderRequests;
    t.mock.timers.tick(1000);
    assert.ok(f.renderRequests > requestsBeforeSuccess, "successful recovery requests a native render");
    f.component.invalidate();
    f.tui.renderNow();
    assert.equal(targets(f.tui, 'pi-copy://').length, 1);
    assert.deepEqual(f.errorNotices, []);
  } finally { weakRefAlwaysFails = false; f.close(); }

  weakRefAlwaysFails = true;
  const disposed = fixture('```sh\necho y\n```');
  const calls = weakRefCalls;
  disposed.close();
  t.mock.timers.tick(10_000);
  assert.equal(weakRefCalls, calls, "dispose cancels the recovery timer");
  assert.deepEqual(disposed.errorNotices, []);
  weakRefAlwaysFails = false;
});

test("local links use live activation cwd once and bind the accepted request across awaits", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const f = fixture("[FILE](out.svg)", false, undefined, undefined, {
    cwd: "/cwd/A",
    open: async () => { await gate; },
  });
  try {
    const link = target(f.tui, "pi-copy://");
    f.setCwd("/cwd/B");
    click(f.terminal, link, 16);
    await tick();
    assert.equal(f.openRequests.length, 1);
    assert.deepEqual({ href: f.openRequests[0]!.href, cwd: f.openRequests[0]!.cwd }, {
      href: "out.svg", cwd: "/cwd/B",
    });
    f.setCwd("/cwd/C");
    assert.equal(f.openRequests[0]!.cwd, "/cwd/B", "accepted request never rereads ctx.cwd");
    release();
    await tick();
  } finally { release?.(); f.close(); }
});

test("local-link selection stays native and Ctrl must be present on both press and release", async () => {
  const selected: string[] = [];
  const f = fixture("[LOCAL](out.svg) trailing", false, undefined, undefined, {
    copySelection: async (text) => { selected.push(text); return true; },
  });
  try {
    const link = target(f.tui, "pi-copy://");
    f.terminal.input(sgr(link.x, link.y));
    f.terminal.input(sgr(link.x + 3, link.y, 32));
    f.terminal.input(sgr(link.x + 3, link.y, 0, "m"));
    await tick();
    assert.equal(selected.length, 1, "ordinary drag completes through Pi selection");
    assert.deepEqual(f.openRequests, []);

    f.terminal.input(sgr(link.x, link.y, 16));
    f.terminal.input(sgr(link.x, link.y, 0, "m"));
    await tick();
    assert.deepEqual(f.openRequests, [], "unmodified release cannot authorize a Ctrl press");
    click(f.terminal, link, 20);
    click(f.terminal, link, 24);
    click(f.terminal, link, 144);
    await tick();
    assert.deepEqual(f.openRequests, [], "Shift/Alt/extended combinations are not aliases for Ctrl+left");
  } finally { f.close(); }
});

test("nested, reference and table links bind by exact token evidence without table allocation churn", async () => {
  const source = "**[A](a.svg)** [B][b]\n\n| link |\n|---|\n| [C](c.json) |\n\n[b]: b.3mf";
  const before = weakRefCount;
  const f = fixture(source);
  try {
    const links = targets(f.tui, "pi-copy://");
    assert.equal(links.length, 3);
    for (const link of links) { click(f.terminal, link, 16); await tick(); }
    assert.deepEqual(f.opened, ["a.svg", "b.3mf", "c.json"]);
    const urls = links.map((link) => link.url);
    for (let pass = 0; pass < 20; pass++) f.tui.renderNow();
    assert.deepEqual(targets(f.tui, "pi-copy://").map((link) => link.url), urls);
    assert.ok(weakRefCount - before <= 4, "table measurement does not allocate duplicate owners/actions per render");
  } finally { f.close(); }
});

test("explicit and reference web links open source semantics while autolinks require parser agreement", async () => {
  const semantic = "https://example.com/?a=1&b=2";
  for (const source of [
    "[PLAIN](https://example.com/?a=1&b=2)",
    "[ENTITY](https://example.com/?a=1&amp;b=2)",
    "[REFERENCE][web]\n\n[web]: https://example.com/?a=1&amp;b=2",
  ]) {
    const f = fixture(source);
    try {
      click(f.terminal, target(f.tui, "pi-copy://"), 16);
      await tick();
      assert.deepEqual(f.opened, [semantic], source);
      assert.deepEqual(f.errorNotices, []);
    } finally { f.close(); }
  }

  const agreed = fixture("https://example.com/?a=1&b=2");
  try {
    click(agreed.terminal, target(agreed.tui, "pi-copy://"), 16);
    await tick();
    assert.deepEqual(agreed.opened, [semantic]);
  } finally { agreed.close(); }

  const disagreed = fixture("https://example.com/?a=1&amp;b=2");
  try {
    click(disagreed.terminal, target(disagreed.tui, "pi-copy://"), 16);
    await tick();
    assert.deepEqual(disagreed.openRequests, [], "bare autolink parser disagreement never opens native amp;b semantics");
    assert.equal(disagreed.errorNotices.length, 1);
    assert.match(disagreed.errorNotices[0]!, /网页自动链接源文与 Pi 解析不一致/);
  } finally { disagreed.close(); }
});

test("web semantic routing fails closed when private action registration is unavailable", async () => {
  const capabilities = getCapabilities();
  setCapabilities({ ...capabilities, hyperlinks: true });
  weakRefFailureCalls.add(weakRefCalls + 1);
  const f = fixture("[ENTITY](https://example.com/?a=1&amp;b=2)");
  try {
    const native = target(f.tui, "https:");
    click(f.terminal, native, 16);
    await tick();
    assert.deepEqual(f.openRequests, []);
    assert.deepEqual(f.nativeOpened, [], "the wrong native amp;b spelling never reaches Pi's opener");
  } finally { f.close(); setCapabilities(capabilities); }
});

test("local parser disagreement produces a private diagnostic; percent-encoded equivalent opens", async () => {
  for (const source of ["[TAB](<a\tb.svg>)", "[ENTITY](a&amp;b.svg)"]) {
    const f = fixture(source);
    try {
      click(f.terminal, target(f.tui, "pi-copy://"), 16);
      await tick();
      assert.deepEqual(f.openRequests, []);
      assert.equal(f.errorNotices.length, 1);
      assert.match(f.errorNotices[0]!, /解析不一致/);
    } finally { f.close(); }
  }
  const encoded = fixture("[ENTITY](a%26b.svg)");
  try {
    click(encoded.terminal, target(encoded.tui, "pi-copy://"), 16);
    await tick();
    assert.deepEqual(encoded.opened, ["a%26b.svg"]);
  } finally { encoded.close(); }
});

test("empty and unsupported schemes are private diagnostics with zero open calls", async () => {
  for (const source of ["[EMPTY]()", "[MAIL](mailto:user@example.com)", "[COMMAND](command:workbench.action.closeWindow)"]) {
    const f = fixture(source);
    try {
      click(f.terminal, target(f.tui, "pi-copy://"), 16);
      await tick();
      assert.deepEqual(f.openRequests, []);
      assert.equal(f.errorNotices.length, 1);
    } finally { f.close(); }
  }
});

test("generation retirement aborts pending open preparation and suppresses stale feedback", async () => {
  let observed: OpenLifetime | undefined;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const f = fixture("[LOCAL](out.svg)", false, undefined, undefined, {
    open: async (_href, _cwd, lifetime) => {
      observed = lifetime;
      await pending;
      throw new Error("late failure");
    },
  });
  let closed = false;
  try {
    click(f.terminal, target(f.tui, "pi-copy://"), 16);
    await tick();
    assert.ok(observed);
    f.close();
    closed = true;
    assert.equal(observed!.signal.aborted, true);
    release();
    await tick();
    assert.deepEqual(f.errorNotices, [], "retired generation emits no late open error");
  } finally { release?.(); if (!closed) f.close(); }
});

test("link-only failed disposal retains private-URI containment", async () => {
  const f = fixture("[LOCAL](out.svg)", false, undefined, undefined, { quiesce: "failure" });
  const link = target(f.tui, "pi-copy://");
  try {
    assert.deepEqual(f.adapter.dispose(), { quiesced: false, passive: true });
    click(f.terminal, link, 16);
    await tick();
    assert.deepEqual(f.openRequests, []);
    assert.deepEqual(f.nativeOpened, []);
  } finally { f.tui.stop(); }
});

test("Ctrl-click opens original wrapped Markdown URL; normal click and drag do not", async () => {
  const capabilities = getCapabilities(); setCapabilities({ ...capabilities, hyperlinks: false });
  const url = 'https://example.com/path?a=1&b=2#fragment';
  const f = fixture(`[中文 ${'wrapped link '.repeat(12)}](${url})`);
  try {
    const point = target(f.tui, 'pi-copy://');
    click(f.terminal, point); await tick(); assert.deepEqual(f.opened, []); assert.deepEqual(f.nativeOpened, []);
    click(f.terminal, point, 16); await tick(); assert.deepEqual(f.opened, [url]);
    f.terminal.input(sgr(point.x, point.y, 16));
    f.terminal.input(sgr(point.x + 1, point.y, 48));
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.deepEqual(f.opened, [url]);
    assert.equal(getCapabilities().hyperlinks, false, 'global capabilities remain unchanged');
    for (const line of screen(f.tui)) assert.ok(visibleWidth(line) <= f.terminal.columns);
  } finally { f.close(); setCapabilities(capabilities); }
});

test("wheel and selection gestures crossing a private cell retain native fullscreen routing", async () => {
  const selected: string[] = [];
  const source = `${"history line\n\n".repeat(35)}\`\`\`sh\necho x\n\`\`\``;
  const f = fixture(source, false, undefined, undefined, {
    copySelection: async (text) => { selected.push(text); return true; },
  });
  try {
    const point = target(f.tui, "pi-copy://");
    const beforeWheel = screen(f.tui).map(stripTerminalSequences);
    f.terminal.input(sgr(point.x, point.y, 64));
    f.tui.renderNow();
    assert.notDeepEqual(screen(f.tui).map(stripTerminalSequences), beforeWheel,
      "wheel-up over the button is routed to the native viewport");

    f.tui.scrollToBottom();
    f.tui.renderNow();
    const visible = target(f.tui, "pi-copy://");
    f.terminal.input(sgr(0, visible.y));
    f.terminal.input(sgr(visible.x, visible.y, 32));
    f.terminal.input(sgr(visible.x, visible.y, 0, "m"));
    await tick();
    assert.equal(selected.length, 1, "native copy-on-select completes across a private URI cell");
    assert.equal((f.tui as unknown as { selectionPressActive: boolean }).selectionPressActive, false);
    assert.deepEqual(f.copied, [], "a drag ending on the button is not a copy-button click");
  } finally { f.close(); }
});

test("overlay routing uses exact geometry/capture ownership and preserves transcript policy", async () => {
  const transcript = "https://example.com/transcript";
  const f = fixture(`prefix prefix prefix [ordinary](${transcript})\n\n\`\`\`sh\necho x\n\`\`\``);
  try {
    const transcriptButton = target(f.tui, "pi-copy://");
    const keyboardInputs: string[] = [];
    const focusedOverlay = f.tui.showOverlay({
      render: () => ["modal"],
      invalidate() {},
      handleInput: (data: string) => { keyboardInputs.push(data); },
    }, { anchor: "top-left", width: 8 });
    f.tui.renderNow();
    assert.equal(focusedOverlay.isFocused(), true);
    const bounds = focusedOverlay.getBounds()!;
    const transcriptPoint = targets(f.tui, 'pi-copy://')[0]!;
    assert.ok(
      transcriptPoint.x < bounds.col || transcriptPoint.x >= bounds.col + bounds.width ||
      transcriptPoint.y < bounds.row || transcriptPoint.y >= bounds.row + bounds.height,
      "critic fixture keeps the transcript URL outside the width-8 overlay",
    );
    click(f.terminal, transcriptPoint);
    await tick();
    assert.deepEqual(f.nativeOpened, [], "overlay existence does not authorize an outside transcript URL");
    assert.deepEqual(f.opened, []);
    click(f.terminal, transcriptPoint, 16);
    await tick();
    assert.deepEqual(f.opened, [transcript], "outside transcript URLs retain the Ctrl-only extension policy");

    const wheel = sgr(transcriptButton.x, transcriptButton.y, 64);
    f.terminal.input(wheel);
    assert.deepEqual(keyboardInputs, [wheel], "unclaimed wheel input reaches the focused overlay");
    focusedOverlay.hide();

    const privateEvents: string[] = [];
    const privateOverlay = f.tui.showOverlay({
      render: () => [hyperlink("private", transcriptButton.url)],
      invalidate() {},
      handleMouse: (event) => {
        privateEvents.push(event.type);
        return { handled: true, capture: event.type === "press" };
      },
    }, { anchor: "top-left", width: 12 });
    f.tui.renderNow();
    const privatePoint = targetUrl(f.tui, transcriptButton.url);
    click(f.terminal, privatePoint);
    await tick();
    assert.deepEqual(privateEvents, ["press", "release", "click"],
      "native overlay press/capture/release/click routing is preserved");
    assert.deepEqual(f.nativeOpened, [], "private overlay URLs never reach Pi's opener");
    assert.deepEqual(f.copied, [], "overlay-owned private URLs are not extension actions");
    privateOverlay.hide();

    const captureUrl = "https://example.com/captured-overlay";
    const captureEvents: string[] = [];
    const capturedOverlay = f.tui.showOverlay({
      render: () => ["capture"],
      invalidate() {},
      handleMouse: (event) => {
        captureEvents.push(event.type);
        if (event.type === "release") {
          (f.tui as unknown as { openUrl?: (url: string) => void }).openUrl?.(captureUrl);
        }
        return { handled: true, capture: event.type === "press" };
      },
    }, { anchor: "top-left", width: 8 });
    f.tui.renderNow();
    const capturedBounds = capturedOverlay.getBounds()!;
    f.terminal.input(sgr(capturedBounds.col, capturedBounds.row));
    f.terminal.input(sgr(capturedBounds.col + capturedBounds.width + 4, capturedBounds.row, 32));
    f.terminal.input(sgr(capturedBounds.col + capturedBounds.width + 4, capturedBounds.row, 0, "m"));
    await tick();
    assert.deepEqual(captureEvents, ["press", "drag", "release"]);
    assert.deepEqual(f.nativeOpened, [captureUrl],
      "an overlay capture retains native URL ownership when release is outside its rectangle");
    capturedOverlay.hide();

    const ordinary = "https://example.com/native-overlay";
    const ordinaryOverlay = f.tui.showOverlay({
      render: () => [hyperlink("ordinary", ordinary)], invalidate() {},
    }, { anchor: "top-left", width: 16 });
    f.tui.renderNow();
    click(f.terminal, targetUrl(f.tui, ordinary));
    await tick();
    assert.deepEqual(f.nativeOpened, [captureUrl, ordinary],
      "an overlay-owned non-private URL retains Pi's native opener policy");
    assert.deepEqual(f.opened, [transcript], "overlay URLs bypass the extension Ctrl-click action");
    ordinaryOverlay.hide();
  } finally { f.close(); }
});

test("temporary opener filters are reentrant and never clobber a synchronous replacement", async () => {
  const replaced = fixture("```sh\necho replacement\n```");
  const oldOpen: string[] = [];
  const newerOpen: string[] = [];
  const oldOpener = (url: string) => { oldOpen.push(url); };
  const newerOpener = (url: string) => { newerOpen.push(url); };
  (replaced.tui as unknown as { openUrl?: (url: string) => void }).openUrl = oldOpener;
  try {
    const overlay = replaced.tui.showOverlay({
      render: () => ["replace"], invalidate() {},
      handleMouse: (event) => {
        if (event.type === "press") {
          (replaced.tui as unknown as { openUrl?: (url: string) => void }).openUrl = newerOpener;
        }
        return { handled: true };
      },
    }, { anchor: "top-left", width: 8 });
    replaced.tui.renderNow();
    const bounds = overlay.getBounds()!;
    replaced.terminal.input(sgr(bounds.col, bounds.row));
    assert.equal((replaced.tui as unknown as { openUrl?: unknown }).openUrl, newerOpener,
      "finally restoration is scoped to the filter it installed");
    assert.deepEqual(oldOpen, []);
    assert.deepEqual(newerOpen, []);
    overlay.hide();
  } finally { replaced.close(); }

  const inputSurface = runtimeSurfaces[3]!;
  const baselineDescriptor = Object.getOwnPropertyDescriptor(inputSurface.owner, inputSurface.key)!;
  const terminal = new FakeTerminal();
  const opened: string[] = [];
  const originalOpener = (url: string) => { opened.push(url); };
  const outer = sgr(20, 4);
  const inner = sgr(0, 0);
  const nestedUrl = "https://example.com/nested-overlay";
  const filterSymbol = Symbol.for("pi-copy-links:open-url-filter");
  const stableBases: boolean[] = [];
  let tui: TuiAltScreen | undefined;
  let adapter: ReturnType<typeof installAdapter> | undefined;
  function reentrantNative(this: unknown, data: string) {
    const runtime = this as {
      openUrl?: (url: string) => void;
      handleViewportInput(data: string): unknown;
    };
    const metadata = (runtime.openUrl as ((url: string) => void) & {
      [filterSymbol]?: { base?: (url: string) => void };
    } | undefined)?.[filterSymbol];
    stableBases.push(metadata?.base === originalOpener);
    if (data === outer) return runtime.handleViewportInput(inner);
    runtime.openUrl?.(nestedUrl);
    return { consume: true };
  }
  try {
    Object.defineProperty(inputSurface.owner, inputSurface.key, {
      ...baselineDescriptor, value: reentrantNative,
    });
    adapter = installAdapter({
      version: VERSION, active: () => true, copy: async () => {}, cwd: () => '/fixture', open: async () => {},
      notify: () => {}, button: text => text,
    });
    tui = new TuiAltScreen(terminal, false, undefined, { openUrl: originalOpener });
    tui.showOverlay({ render: () => ["nested"], invalidate() {} }, { anchor: "top-left", width: 8 });
    tui.start();
    tui.renderNow();
    terminal.input(outer);
    assert.deepEqual(opened, [nestedUrl],
      "inner overlay-owned dispatch unwraps the outer transcript-suppressing filter");
    assert.deepEqual(stableBases, [true, true], "both owned filters retain the same stable base opener");
    assert.equal((tui as unknown as { openUrl?: unknown }).openUrl, originalOpener,
      "nested filters restore in LIFO order");
  } finally {
    adapter?.dispose();
    tui?.stop();
    Object.defineProperty(inputSurface.owner, inputSurface.key, baselineDescriptor);
  }
});

test("active opener filter transactionally overrides configurable no-op and throwing setters", async () => {
  for (const mode of ["noop", "throw"] as const) {
    const escaped: string[] = [];
    const f = fixture("plain transcript");
    const sink = (url: string) => { escaped.push(url); };
    const getter = () => sink;
    const setter = mode === "noop" ? () => {} : () => { throw new Error("setter refused filter"); };
    const descriptor: PropertyDescriptor = {
      configurable: true,
      enumerable: true,
      get: getter,
      set: setter,
    };
    Object.defineProperty(f.tui, "openUrl", descriptor);
    try {
      const privateUrl = `pi-copy://foreign/${mode}`;
      const overlay = f.tui.showOverlay({
        render: () => [hyperlink("private", privateUrl)], invalidate() {},
      }, { anchor: "top-left", width: 12 });
      f.tui.renderNow();
      click(f.terminal, targetUrl(f.tui, privateUrl));
      await tick();
      assert.deepEqual(escaped, [], `${mode}: overlay-owned private URL stays filtered`);
      assert.deepEqual(Object.getOwnPropertyDescriptor(f.tui, "openUrl"), descriptor,
        `${mode}: the exact accessor descriptor is restored after each native dispatch`);
      assert.equal((f.tui as unknown as { openUrl?: unknown }).openUrl, sink);
      overlay.hide();
    } finally { f.close(); }
  }
});

test("configurable commit-then-refuse accessor is never invoked and rolls back exactly", async () => {
  const escaped: string[] = [];
  const f = fixture("plain transcript");
  const sink = (url: string) => { escaped.push(url); };
  let current = sink;
  let writes = 0;
  const getter = () => current;
  const setter = (value: (url: string) => void) => {
    writes += 1;
    if (writes === 1) current = value;
    else throw new Error("rollback refused");
  };
  const descriptor: PropertyDescriptor = {
    configurable: true,
    enumerable: true,
    get: getter,
    set: setter,
  };
  Object.defineProperty(f.tui, "openUrl", descriptor);
  try {
    const privateUrl = "pi-copy://foreign/commit-refuse";
    f.tui.showOverlay({
      render: () => [hyperlink("private", privateUrl)], invalidate() {},
    }, { anchor: "top-left", width: 16 });
    f.tui.renderNow();
    click(f.terminal, targetUrl(f.tui, privateUrl));
    await tick();
    assert.equal(writes, 0, "acquisition and rollback never invoke the accessor setter");
    assert.equal(current, sink);
    assert.deepEqual(escaped, []);
    assert.deepEqual(Object.getOwnPropertyDescriptor(f.tui, "openUrl"), descriptor);
    (f.tui as unknown as { openUrl(url: string): void }).openUrl("https://example.com/after");
    assert.deepEqual(escaped, ["https://example.com/after"], "ordinary predecessor remains usable afterward");
  } finally { f.close(); }
});

test("minimal passive guard transactionally filters stale URIs through hostile accessors", async () => {
  const baselineMethods = runtimeMethods();
  for (const mode of ["noop", "throw"] as const) {
    const escaped: string[] = [];
    const f = fixture("```sh\necho stale\n```", false, undefined, undefined, { quiesce: "failure" });
    const stale = target(f.tui, "pi-copy://");
    const result = f.adapter.dispose();
    assert.deepEqual(result, { quiesced: false, passive: true });
    const sink = (url: string) => { escaped.push(url); };
    const getter = () => sink;
    const setter = mode === "noop" ? () => {} : () => { throw new Error("setter refused passive filter"); };
    const descriptor: PropertyDescriptor = {
      configurable: true,
      enumerable: false,
      get: getter,
      set: setter,
    };
    Object.defineProperty(f.tui, "openUrl", descriptor);
    let cleanup: ReturnType<typeof installAdapter> | undefined;
    try {
      click(f.terminal, stale);
      await tick();
      assert.deepEqual(escaped, [], `${mode}: retired URI stays contained by the minimal guard`);
      assert.deepEqual(Object.getOwnPropertyDescriptor(f.tui, "openUrl"), descriptor,
        `${mode}: passive dispatch restores the exact accessor descriptor`);
      assert.equal((f.tui as unknown as { openUrl?: unknown }).openUrl, sink);

      Object.defineProperty(f.tui, "openUrl", {
        configurable: true, enumerable: true, writable: true, value: sink,
      });
      cleanup = installAdapter({
        version: VERSION, active: () => true, copy: async () => {}, cwd: () => '/fixture', open: async () => {},
        notify: () => {}, button: text => text,
        quiesce: () => {
          f.component.invalidate();
          f.tui.invalidate();
          f.tui.renderNow(true);
          return screen(f.tui).every((row) => !row.includes("pi-copy://"));
        },
      });
      assert.deepEqual(cleanup.dispose(), { quiesced: true, passive: false });
      cleanup = undefined;
      assert.deepEqual(runtimeMethods(), baselineMethods);
    } finally {
      cleanup?.dispose();
      f.tui.stop();
    }
  }
});

test("non-configurable commit-then-refuse accessor fails closed without setter calls", async () => {
  const escaped: string[] = [];
  const f = fixture("plain transcript");
  const sink = (url: string) => { escaped.push(url); };
  let current = sink;
  let writes = 0;
  const getter = () => current;
  const setter = (value: (url: string) => void) => {
    writes += 1;
    if (writes === 1) current = value;
    else throw new Error("rollback refused");
  };
  const descriptor: PropertyDescriptor = {
    configurable: false,
    enumerable: true,
    get: getter,
    set: setter,
  };
  Object.defineProperty(f.tui, "openUrl", descriptor);
  try {
    const privateUrl = "pi-copy://foreign/non-configurable";
    const events: string[] = [];
    f.tui.showOverlay({
      render: () => [hyperlink("private", privateUrl)], invalidate() {},
      handleMouse: (event) => { events.push(event.type); return { handled: true }; },
    }, { anchor: "top-left", width: 18 });
    f.tui.renderNow();
    click(f.terminal, targetUrl(f.tui, privateUrl));
    await tick();
    assert.equal(writes, 0);
    assert.equal(current, sink);
    assert.deepEqual(escaped, []);
    assert.deepEqual(events, [], "native overlay dispatch is withheld when descriptor rollback is impossible");
    assert.deepEqual(Object.getOwnPropertyDescriptor(f.tui, "openUrl"), descriptor);
    (f.tui as unknown as { openUrl(url: string): void }).openUrl("https://example.com/after");
    assert.deepEqual(escaped, ["https://example.com/after"]);
    assert.equal((f.tui as unknown as { selectionPressActive: boolean }).selectionPressActive, false);
  } finally { f.close(); }
});

test("inherited commit-then-refuse accessor remains untouched when prior own property is absent", async () => {
  const prototype = TuiAltScreen.prototype as unknown as Record<string, unknown>;
  const priorPrototype = Object.getOwnPropertyDescriptor(prototype, "openUrl");
  const escaped: string[] = [];
  const sink = (url: string) => { escaped.push(url); };
  let current = sink;
  let writes = 0;
  const getter = () => current;
  const setter = (value: (url: string) => void) => {
    writes += 1;
    if (writes === 1) current = value;
    else throw new Error("rollback refused");
  };
  const inheritedDescriptor: PropertyDescriptor = {
    configurable: true,
    enumerable: false,
    get: getter,
    set: setter,
  };
  const f = fixture("plain transcript");
  try {
    Object.defineProperty(prototype, "openUrl", inheritedDescriptor);
    assert.equal(Reflect.deleteProperty(f.tui, "openUrl"), true);
    assert.equal(Object.hasOwn(f.tui, "openUrl"), false);
    const privateUrl = "pi-copy://foreign/absent";
    f.tui.showOverlay({
      render: () => [hyperlink("private", privateUrl)], invalidate() {},
    }, { anchor: "top-left", width: 12 });
    f.tui.renderNow();
    click(f.terminal, targetUrl(f.tui, privateUrl));
    await tick();
    assert.equal(writes, 0, "own data shadow avoids invoking the inherited setter");
    assert.equal(current, sink);
    assert.deepEqual(escaped, []);
    assert.equal(Object.hasOwn(f.tui, "openUrl"), false,
      "temporary own filter is deleted rather than materializing an opener slot");
    assert.equal((f.tui as unknown as { openUrl?: unknown }).openUrl, sink);
    (f.tui as unknown as { openUrl(url: string): void }).openUrl("https://example.com/after");
    assert.deepEqual(escaped, ["https://example.com/after"]);
  } finally {
    f.close();
    if (priorPrototype) Object.defineProperty(prototype, "openUrl", priorPrototype);
    else Reflect.deleteProperty(prototype, "openUrl");
  }
});

test("drag, overlay, focus loss, changed screen and resize cancel button gestures", async () => {
  const f = fixture('```sh\necho x\n```');
  try {
    let p = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(p.x, p.y)); f.terminal.input(sgr(p.x + 1, p.y, 32)); f.terminal.input(sgr(p.x, p.y, 0, 'm'));
    await tick(); assert.deepEqual(f.copied, []);
    f.terminal.input(sgr(p.x, p.y)); f.terminal.input('\x1b[O'); f.terminal.input(sgr(p.x, p.y, 0, 'm'));
    await tick(); assert.deepEqual(f.copied, []);
    f.terminal.input(sgr(p.x, p.y));
    const overlay = f.tui.showOverlay({ render: () => ['modal'], invalidate() {} }); f.tui.renderNow();
    f.terminal.input(sgr(p.x, p.y, 0, 'm')); await tick(); assert.deepEqual(f.copied, []);
    overlay.hide(); f.tui.renderNow(); p = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(p.x, p.y)); f.terminal.columns = 50; f.tui.renderNow();
    f.terminal.input(sgr(p.x, p.y, 0, 'm')); await tick(); assert.deepEqual(f.copied, []);
    p = target(f.tui, 'pi-copy://'); click(f.terminal, p); await tick(); assert.deepEqual(f.copied, ['echo x']);
  } finally { f.close(); }
});

test("history scrolling and cache/width changes keep each button tied to its block", async () => {
  const f = fixture('```sh\necho first\n```\n\n' + 'spacer\n\n'.repeat(35) + '```sh\necho last\n```');
  try {
    click(f.terminal, target(f.tui, 'pi-copy://')); await tick(); assert.deepEqual(f.copied, ['echo last']);
    f.tui.scrollToTop(); f.tui.renderNow();
    click(f.terminal, target(f.tui, 'pi-copy://')); await tick(); assert.deepEqual(f.copied, ['echo last', 'echo first']);
    f.terminal.columns = 20; f.tui.renderNow();
    click(f.terminal, target(f.tui, 'pi-copy://')); await tick(); assert.equal(f.copied.at(-1), 'echo first');
    const old = target(f.tui, 'pi-copy://').url; f.tui.renderNow(); assert.equal(target(f.tui, 'pi-copy://').url, old);
  } finally { f.close(); }
});

test("all reasonable narrow widths remain bounded and code buttons stay at the right edge", () => {
  const f = fixture('- ```sh\n  echo a\n  ```');
  try {
    for (const width of [8, 10, 16, 25, 80]) {
      f.terminal.columns = width; f.tui.renderNow();
      const p = target(f.tui, 'pi-copy://');
      for (const line of screen(f.tui)) assert.ok(visibleWidth(line) <= width, `${width}: ${line}`);
      assert.ok(getOsc8LinkAtColumn(screen(f.tui)[p.y]!, width - 2)?.startsWith('pi-copy://'), 'ends at content edge');
    }
  } finally { f.close(); }
});

test("ordinary Markdown, regular-mode render and shutdown are not decorated", () => {
  const originals = runtimeMethods();
  const descriptors = runtimeDescriptors();
  const f = fixture('```sh\necho x\n```');
  try {
    const ordinary = new Markdown('```sh\necho y\n```', 1, 0, getMarkdownTheme());
    assert.doesNotMatch(ordinary.render(60).join('\n'), /pi-copy:\/\//);
    f.setActive(false); assert.doesNotMatch(screen(f.tui).join('\n'), /pi-copy:\/\//);
    f.setActive(true); assert.match(screen(f.tui).join('\n'), /pi-copy:\/\//);
  } finally { f.close(); }
  assert.deepEqual(runtimeMethods(), originals, "all four hooks are restored by dispose");
  assert.deepEqual(runtimeDescriptors(), descriptors, "all four descriptors are restored by dispose");
});

test("successful disposal synchronously removes private screen state before restoring input", () => {
  const baselineMethods = runtimeMethods();
  const baselineDescriptors = runtimeDescriptors();
  const f = fixture("```sh\necho quiesce\n```");
  try {
    assert.equal(targets(f.tui, "pi-copy://").length, 1);
    const result = f.adapter.dispose();
    assert.deepEqual(result, { quiesced: true, passive: false });
    assert.deepEqual(targets(f.tui, "pi-copy://"), [], "forced native frame contains no private URI");
    assert.deepEqual(runtimeMethods(), baselineMethods);
    assert.deepEqual(runtimeDescriptors(), baselineDescriptors);
  } finally { f.tui.stop(); }
});

test("failed quiescence retains one private-URL guard across reload and failed replacement", async () => {
  const baselineMethods = runtimeMethods();
  const baselineDescriptors = runtimeDescriptors();
  const inputSurface = runtimeSurfaces[3]!;
  const f = fixture("```sh\necho stale\n```", false, undefined, undefined, { quiesce: "failure" });
  const stale = target(f.tui, "pi-copy://");
  try {
    const result = f.adapter.dispose();
    assert.deepEqual(result, { quiesced: false, passive: true });
    assert.match(screen(f.tui).join("\n"), /pi-copy:\/\//, "failed flush leaves stale terminal evidence");
    assert.deepEqual(runtimeMethods().slice(0, 3), baselineMethods.slice(0, 3));
    assert.notEqual(runtimeMethods()[3], baselineMethods[3], "only the passive input guard remains");

    click(f.terminal, stale);
    await tick();
    assert.deepEqual(f.nativeOpened, [], "reload interval cannot open a stale private URI");
    assert.deepEqual(f.copied, [], "retired action is inert during the reload interval");

    const passiveDescriptor = Object.getOwnPropertyDescriptor(inputSurface.owner, inputSurface.key)!;
    const passiveMethod = runtimeMethods()[3];
    try {
      Object.defineProperty(inputSurface.owner, inputSurface.key, {
        configurable: true,
        enumerable: passiveDescriptor.enumerable,
        get: () => passiveMethod,
        set: () => {},
      });
      assert.throws(() => installAdapter({
        version: VERSION, active: () => true, copy: async () => {}, cwd: () => '/fixture', open: async () => {},
        notify: () => {}, button: text => text,
      }), /原子安装失败/);
      assert.equal(runtimeMethods()[3], passiveMethod, "failed replacement rolls back to the guard");
      assert.deepEqual(runtimeMethods().slice(0, 3), baselineMethods.slice(0, 3));
      click(f.terminal, stale);
      await tick();
      assert.deepEqual(f.nativeOpened, [], "replacement failure cannot expose the stale URI");
    } finally {
      Object.defineProperty(inputSurface.owner, inputSurface.key, passiveDescriptor);
    }
  } finally {
    let replacement: ReturnType<typeof installAdapter> | undefined;
    try {
      let quiesceCalls = 0;
      replacement = installAdapter({
        version: VERSION, active: () => true, copy: async () => {}, cwd: () => '/fixture', open: async () => {},
        notify: () => {}, button: text => text,
        quiesce: () => {
          quiesceCalls += 1;
          f.component.invalidate();
          f.tui.invalidate();
          f.tui.renderNow(true);
          return screen(f.tui).every((row) => !row.includes("pi-copy://"));
        },
      });
      const replacementResult = replacement.dispose();
      replacement = undefined;
      assert.equal(quiesceCalls, 1, "an absorbed stale guard requires quiescence before any new render");
      assert.deepEqual(replacementResult, { quiesced: true, passive: false });
      assert.deepEqual(targets(f.tui, "pi-copy://"), []);
      assert.deepEqual(runtimeMethods(), baselineMethods, "a verified successor absorbs the passive guard");
      assert.deepEqual(runtimeDescriptors(), baselineDescriptors);
    } finally {
      replacement?.dispose();
      f.tui.stop();
    }
  }
});

test("repeated failed shutdowns keep a bounded passive guard chain", () => {
  const baselineMethods = runtimeMethods();
  const baselineDescriptors = runtimeDescriptors();
  const guardSymbol = Symbol.for("pi-copy-links:input-guard");
  for (let reload = 0; reload < 12; reload++) {
    const f = fixture(`\`\`\`sh\necho ${reload}\n\`\`\``, false, undefined, undefined, {
      quiesce: reload % 2 === 0 ? "failure" : "throw",
    });
    const result = f.adapter.dispose();
    f.tui.stop();
    assert.deepEqual(result, { quiesced: false, passive: true });
    const guard = (runtimeMethods()[3] as { [guardSymbol]?: {
      baseMethod: unknown; passive: boolean; minimal?: true;
    } })[guardSymbol];
    assert.equal(guard?.passive, true);
    assert.equal(guard?.minimal, true, `reload ${reload} leaves only a dedicated minimal guard`);
    assert.equal(guard?.baseMethod, baselineMethods[3], `reload ${reload} delegates directly to native input`);
  }
  const final = fixture("```sh\necho final\n```");
  final.close();
  assert.deepEqual(runtimeMethods(), baselineMethods, "verified final shutdown removes the one remaining guard");
  assert.deepEqual(runtimeDescriptors(), baselineDescriptors);
});

test("dispose never clobbers a later input wrapper", () => {
  const baselineDescriptors = runtimeDescriptors();
  const inputSurface = runtimeSurfaces[3]!;
  const f = fixture("```sh\necho owner\n```");
  const adapterInput = runtimeMethods()[3] as (this: unknown, data: string) => unknown;
  const installedDescriptor = Object.getOwnPropertyDescriptor(inputSurface.owner, inputSurface.key)!;
  function laterWrapper(this: unknown, data: string) { return adapterInput.call(this, data); }
  Object.defineProperty(inputSurface.owner, inputSurface.key, { ...installedDescriptor, value: laterWrapper });
  try {
    const result = f.adapter.dispose();
    assert.equal(result.quiesced, true);
    assert.equal(result.passive, true);
    assert.equal(runtimeMethods()[3], laterWrapper);
    assert.deepEqual(targets(f.tui, "pi-copy://"), []);
  } finally {
    f.tui.stop();
    for (const [index, surface] of runtimeSurfaces.entries()) {
      Object.defineProperty(surface.owner, surface.key, baselineDescriptors[index]!);
    }
  }
});

test("unknown Pi version metadata does not block conforming hooks", () => {
  const originals = runtimeMethods();
  const descriptors = runtimeDescriptors();
  const adapter = installAdapter({ version: 'fictional-next-release', active: () => true,
    copy: async () => {}, cwd: () => '/fixture', open: async () => {}, notify: () => {}, button: s => s });
  assert.equal(adapter.status().available, true);
  assert.notDeepEqual(runtimeMethods(), originals);
  assert.deepEqual(adapter.dispose(), { quiesced: true, passive: false });
  assert.deepEqual(runtimeMethods(), originals);
  assert.deepEqual(runtimeDescriptors(), descriptors);
});

test("each missing private runtime method is diagnosed before any hook is installed", () => {
  for (const missing of runtimeSurfaces) {
    const descriptor = Object.getOwnPropertyDescriptor(missing.owner, missing.key);
    assert.ok(descriptor, missing.name);
    const originals = runtimeMethods();
    try {
      Object.defineProperty(missing.owner, missing.key, { ...descriptor, value: undefined });
      assert.throws(() => installAdapter({ version: VERSION, active: () => true,
        copy: async () => {}, cwd: () => '/fixture', open: async () => {}, notify: () => {}, button: s => s }),
      new RegExp(`缺少运行时方法 ${missing.name.replaceAll(".", "\\.")}`));
      assert.deepEqual(runtimeMethods(), originals.map((method, index) =>
        runtimeSurfaces[index] === missing ? undefined : method), missing.name);
    } finally {
      Object.defineProperty(missing.owner, missing.key, descriptor);
    }
    assert.deepEqual(runtimeMethods(), originals, `${missing.name} restored by test`);
  }
});

test("non-writable hook preflight leaves every runtime descriptor untouched", () => {
  const surface = runtimeSurfaces[2]!;
  const baseline = runtimeMethods();
  const descriptor = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
  try {
    Object.defineProperty(surface.owner, surface.key, { ...descriptor, writable: false });
    const transactionDescriptors = runtimeDescriptors();
    assert.throws(() => installAdapter({ version: VERSION, active: () => true,
      copy: async () => {}, cwd: () => '/fixture', open: async () => {}, notify: () => {}, button: text => text }),
    /运行时方法不可写 Markdown\.renderInlineTokens/);
    assert.deepEqual(runtimeMethods(), baseline);
    assert.deepEqual(runtimeDescriptors(), transactionDescriptors);
  } finally { Object.defineProperty(surface.owner, surface.key, descriptor); }
});

test("throwing and no-op setters roll back all hooks and emit no private action URI", () => {
  const surface = runtimeSurfaces[3]!;
  const baselineMethods = runtimeMethods();
  const baselineDescriptor = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
  for (const mode of ['throw', 'noop'] as const) {
    const setter = mode === 'throw' ? () => { throw new Error('setter refused'); } : () => {};
    const transactionDescriptor: PropertyDescriptor = {
      configurable: true,
      enumerable: baselineDescriptor.enumerable,
      get: () => baselineMethods[3],
      set: setter,
    };
    try {
      Object.defineProperty(surface.owner, surface.key, transactionDescriptor);
      const transactionDescriptors = runtimeDescriptors();
      assert.throws(() => installAdapter({ version: VERSION, active: () => true,
        copy: async () => {}, cwd: () => '/fixture', open: async () => {}, notify: () => {}, button: text => text }),
      /原子安装失败/);
      assert.deepEqual(runtimeMethods(), baselineMethods, mode);
      assert.deepEqual(runtimeDescriptors(), transactionDescriptors, mode);
      const restored = Object.getOwnPropertyDescriptor(surface.owner, surface.key)!;
      assert.equal(restored.get, transactionDescriptor.get, mode);
      assert.equal(restored.set, transactionDescriptor.set, mode);

      const terminal = new FakeTerminal();
      const nativeOpened: string[] = [];
      const component = new AssistantMessageComponent(message('```sh\necho native\n```'));
      const tui = new TuiAltScreen(terminal, false, undefined, { openUrl: url => nativeOpened.push(url) });
      tui.addChild(component); tui.start(); tui.renderNow();
      assert.deepEqual(targets(tui, 'pi-copy://'), [], `${mode}: failed install rendered no private URI`);
      assert.deepEqual(nativeOpened, []);
      tui.stop();
    } finally { Object.defineProperty(surface.owner, surface.key, baselineDescriptor); }
  }
});


test("tmux Ctrl links and copy buttons require one client PID across press and release", async () => {
  const f = fixture("[WEB](https://example.com/origin)\n\n```sh\necho source\n```", true, undefined, undefined, { tmux: true });
  try {
    const link = targets(f.tui, "pi-copy://")[0]!;
    const copy = targets(f.tui, "pi-copy://")[1]!;
    taggedClick(f.terminal, link, 12001);
    await tick();
    assert.deepEqual(f.openRequests.map(({ href, origin }) => ({ href, origin })),
      [{ href: "https://example.com/origin", origin: 12001 }]);
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16));
    tagged(f.terminal, 12002, sgr(link.x, link.y, 16, "m"));
    tagged(f.terminal, 12002, sgr(copy.x, copy.y));
    tagged(f.terminal, 12001, sgr(copy.x, copy.y, 0, "m"));
    await tick();
    assert.equal(f.openRequests.length, 1, "different clients cannot combine same-cell link edges");
    assert.deepEqual(f.copied, [], "different clients cannot combine same-cell copy edges");
    taggedClick(f.terminal, copy, 12002, 0);
    await tick();
    assert.deepEqual(f.copied, ["echo source"], "the tagged ordinary copy button still works");
    assert.deepEqual(f.nativeOpened, []);
  } finally { f.close(); }
});

test("tmux markers leave native selection, wheel and overlay mouse ownership intact", async () => {
  const selected: string[] = [];
  const source = `${"history line\n\n".repeat(35)}[WEB](https://example.com/overlay)\n\n\`\`\`sh\necho source\n\`\`\``;
  const f = fixture(source, false, undefined, undefined, {
    tmux: true, copySelection: async (text) => { selected.push(text); return true; },
  });
  try {
    const point = target(f.tui, "pi-copy://");
    const before = screen(f.tui).map(stripTerminalSequences);
    tagged(f.terminal, 12001, sgr(point.x, point.y, 64));
    f.tui.renderNow();
    assert.notDeepEqual(screen(f.tui).map(stripTerminalSequences), before);
    f.tui.scrollToBottom(); f.tui.renderNow();
    const link = targets(f.tui, "pi-copy://")[0]!;
    tagged(f.terminal, 12001, sgr(0, link.y));
    f.terminal.input(sgr(link.x, link.y, 32)); // Native stock drag has no extension marker.
    tagged(f.terminal, 12001, sgr(link.x, link.y, 0, "m"));
    await tick();
    assert.equal(selected.length, 1);
    assert.deepEqual(f.openRequests, []);

    const events: string[] = [];
    const overlay = f.tui.showOverlay({
      render: () => [hyperlink("private", link.url)], invalidate() {},
      handleMouse: (event) => { events.push(event.type); return { handled: true, capture: event.type === "press" }; },
    }, { anchor: "top-left", width: 12 });
    f.tui.renderNow();
    const overlayPoint = targetUrl(f.tui, link.url);
    taggedClick(f.terminal, overlayPoint, 12001, 0);
    await tick();
    assert.deepEqual(events, ["press", "release", "click"]);
    assert.deepEqual(f.nativeOpened, []);
    assert.deepEqual(f.openRequests, []);
    overlay.hide();
  } finally { f.close(); }
});

test("tmux missing, malformed, duplicate and displaced origin markers fail visibly without local open", async () => {
  const f = fixture("[WEB](https://example.com/origin)", false, undefined, undefined, { tmux: true });
  try {
    const link = target(f.tui, "pi-copy://");
    click(f.terminal, link, 16);
    tagged(f.terminal, "NaN", sgr(link.x, link.y, 16));
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16, "m"));
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16));
    f.terminal.input("\x1b[998;12002~");
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16, "m"));
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16));
    f.terminal.input("\x1b[O");
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16, "m"));
    await tick();
    assert.deepEqual(f.openRequests, []);
    assert.ok(f.errorNotices.length >= 3, "Ctrl release without same tagged press reports the routing failure");
    assert.ok(f.errorNotices.every((message) => message.includes("来源标记")));
    assert.deepEqual(f.nativeOpened, []);
    taggedClick(f.terminal, link, 12001);
    await tick();
    assert.deepEqual(f.openRequests.map(({ origin }) => origin), [12001], "a later valid gesture recovers");
  } finally { f.close(); }
});

test("tmux lost-press recovery cannot borrow another client's ordinary release", async () => {
  const f = fixture("[WEB](https://example.com/recovered)", true, undefined, undefined, { tmux: true });
  try {
    const link = target(f.tui, "pi-copy://");
    tagged(f.terminal, 12001, sgr(link.x, link.y, 0, "m"));
    tagged(f.terminal, 12002, sgr(link.x, link.y, 16, "m"));
    await tick();
    assert.deepEqual(f.openRequests, []);
    tagged(f.terminal, 12001, sgr(link.x, link.y, 0, "m"));
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16, "m"));
    await tick();
    assert.deepEqual(f.openRequests.map(({ origin }) => origin), [12001]);
    // A foreign release must not rearm recovery after canceling another client's pending press.
    tagged(f.terminal, 12001, sgr(link.x, link.y, 16));
    tagged(f.terminal, 12002, sgr(link.x, link.y, 0, "m"));
    tagged(f.terminal, 12002, sgr(link.x, link.y, 16, "m"));
    await tick();
    assert.equal(f.openRequests.length, 1);
  } finally { f.close(); }
});

test("local non-tmux Ctrl link still opens with explicit unknown origin", async () => {
  const f = fixture("[WEB](https://example.com/local)");
  try {
    click(f.terminal, target(f.tui, "pi-copy://"), 16);
    await tick();
    assert.deepEqual(f.openRequests.map(({ href, origin }) => ({ href, origin })),
      [{ href: "https://example.com/local", origin: "unknown" }]);
  } finally { f.close(); }
});

test("tmux lost-press recovery requires the same action cell, a recent modifier transition, stable screen and no drag", async () => {
  const f = fixture('[link](https://example.com/)', true);
  try {
    let point = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.deepEqual(f.opened, [], 'arbitrary orphan release is not a click');

    const blank = { x: f.terminal.columns - 2, y: 0 };
    assert.equal(getOsc8LinkAtColumn(screen(f.tui)[blank.y] ?? "", blank.x), undefined);
    click(f.terminal, blank);
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.deepEqual(f.opened, [], 'an ordinary release on another cell cannot authorize the link orphan release');

    click(f.terminal, point); await tick();
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.deepEqual(f.opened, ['https://example.com/'], 'the exact same-action modifier-transition loss is recovered');
    click(f.terminal, point); await tick();
    f.terminal.input(sgr(point.x + 1, point.y, 48));
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.equal(f.opened.length, 1, 'drag cancels recovery even if it returns to the same link');
    click(f.terminal, point); await tick();
    f.component.updateContent(message('inserted row\n\n[link](https://example.com/)')); f.tui.renderNow();
    point = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.equal(f.opened.length, 1, 'changed viewport cannot recover a missing press');
  } finally { f.close(); }
});

test("native partial-fence trimming does not copy guessed or outdated code", async () => {
  const f = fixture('start');
  try {
    f.component.updateContent(message('```sh\necho x\n``'), true); f.tui.renderNow();
    assert.doesNotMatch(screen(f.tui).join('\n'), /pi-copy:\/\//);
    assert.deepEqual(f.notices, []);
    f.component.updateContent(message('```sh\necho x\n```'), false); f.tui.renderNow();
    click(f.terminal, target(f.tui, 'pi-copy://')); await tick();
    assert.deepEqual(f.copied, ['echo x']);
  } finally { f.close(); }
});

test("press reverse-highlights the button, release restores, success flashes briefly", async () => {
  const f = fixture('```sh\necho x\n```', false, 30);
  try {
    const p = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(p.x, p.y)); f.tui.renderNow();
    assert.match(screen(f.tui)[p.y]!, /\x1b\[7m/, 'held press shows reverse video');
    f.terminal.input(sgr(p.x, p.y, 0, 'm')); await tick(); f.tui.renderNow();
    assert.deepEqual(f.copied, ['echo x']);
    assert.match(screen(f.tui)[p.y]!, /\x1b\[7m/, 'successful copy keeps a short flash');
    await new Promise(resolve => setTimeout(resolve, 80));
    f.tui.renderNow();
    assert.doesNotMatch(screen(f.tui)[p.y]!, /\x1b\[7m/, 'flash expires');
  } finally { f.close(); }
});

test("rejected clipboard writes report an error without a success flash", async () => {
  const f = fixture('```sh\necho x\n```', false, 30, new Error("clipboard unavailable"));
  try {
    const p = target(f.tui, 'pi-copy://');
    click(f.terminal, p); await tick(); f.tui.renderNow();
    assert.deepEqual(f.copied, []);
    assert.deepEqual(f.notices, ["复制失败：Error: clipboard unavailable"]);
    assert.deepEqual(f.errorNotices, f.notices);
    assert.doesNotMatch(screen(f.tui)[p.y]!, /\x1b\[7m/, "failure never shows the success flash");
  } finally { f.close(); }
});

test("finalizer poison blocks the next queued clipboard action after the first endpoint rejects", async () => {
  const RealFinalizationRegistry = globalThis.FinalizationRegistry;
  const registries: Array<{
    callback: (holdings: unknown) => void;
    records: Array<{ holdings: unknown; token?: object }>;
  }> = [];
  class CapturingFinalizationRegistry {
    callback: (holdings: unknown) => void;
    records: Array<{ holdings: unknown; token?: object }> = [];
    constructor(callback: (holdings: unknown) => void) {
      this.callback = callback;
      registries.push(this);
    }
    register(_target: object, holdings: unknown, token?: object) {
      this.records.push({ holdings, token });
    }
    unregister(token: object) {
      const before = this.records.length;
      this.records = this.records.filter((record) => record.token !== token);
      return this.records.length !== before;
    }
  }
  globalThis.FinalizationRegistry = CapturingFinalizationRegistry as unknown as typeof FinalizationRegistry;

  let rejectFirst!: (error: Error) => void;
  const first = new Promise<void>((_resolve, reject) => { rejectFirst = reject; });
  const copies: string[] = [];
  let f: ReturnType<typeof fixture> | undefined;
  try {
    f = fixture("```sh\nx\n```", false, undefined, undefined, {
      copy: async (text) => {
        copies.push(text);
        if (copies.length === 1) await first;
      },
    });
    const button = target(f.tui, "pi-copy://");
    click(f.terminal, button);
    click(f.terminal, button);
    await tick();
    assert.deepEqual(copies, ["x"], "the second accepted click is queued behind the first endpoint");

    const generationRegistry = registries.find((registry) => registry.records.some((record) =>
      typeof record.holdings === "object" && record.holdings !== null &&
      typeof (record.holdings as { deref?: unknown }).deref === "function"));
    const ownerRecord = generationRegistry?.records.find((record) =>
      typeof record.holdings === "object" && record.holdings !== null &&
      typeof (record.holdings as { deref?: unknown }).deref === "function");
    assert.ok(generationRegistry && ownerRecord, "captured the generation owner finalizer");

    const originalDelete = Set.prototype.delete;
    Set.prototype.delete = function(this: Set<unknown>, value: unknown) {
      if (value === ownerRecord.holdings) throw new RangeError("finalizer Set.delete failed");
      return originalDelete.call(this, value);
    } as typeof Set.prototype.delete;
    try { generationRegistry.callback(ownerRecord.holdings); }
    finally { Set.prototype.delete = originalDelete; }

    rejectFirst(new Error("endpoint failed"));
    await tick();
    await tick();
    await tick();
    assert.deepEqual(copies, ["x"], "a poisoned generation never starts the queued clipboard write");
    assert.deepEqual(f.notices, [], "poisoned-generation rejection and queued completion emit no current feedback");
  } finally {
    rejectFirst?.(new Error("test cleanup"));
    f?.close();
    globalThis.FinalizationRegistry = RealFinalizationRegistry;
  }
});

test("stale clipboard rejection is silent while a fresh generation remains globally ordered", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let rejectOld!: (error: Error) => void;
  const oldWrite = new Promise<void>((_resolve, reject) => { rejectOld = reject; });
  const writes: string[] = [];
  const f = fixture("```sh\necho ordered\n```", false, 30, undefined, {
    copy: async (text) => {
      writes.push(text);
      if (writes.length === 1) await oldWrite;
    },
  });
  try {
    const stale = target(f.tui, "pi-copy://");
    click(f.terminal, stale);
    await tick();
    assert.deepEqual(writes, ["echo ordered"]);

    const originalGet = Map.prototype.get;
    Map.prototype.get = function(this: Map<unknown, unknown>, key: unknown) {
      if (key === stale.url) throw new RangeError("retire generation while clipboard is pending");
      return originalGet.call(this, key);
    } as typeof Map.prototype.get;
    try { click(f.terminal, stale); }
    finally { Map.prototype.get = originalGet; }
    await tick();

    t.mock.timers.tick(1000);
    f.component.invalidate();
    f.tui.renderNow();
    const fresh = target(f.tui, "pi-copy://");
    assert.notEqual(fresh.url.split("/")[2], stale.url.split("/")[2]);
    f.notices.length = 0;
    f.errorNotices.length = 0;
    click(f.terminal, fresh);
    await tick();
    assert.deepEqual(writes, ["echo ordered"], "fresh write waits behind the non-abortable old write");

    rejectOld(new Error("old clipboard failed"));
    await tick();
    await tick();
    assert.deepEqual(writes, ["echo ordered", "echo ordered"], "fresh write runs after old settlement");
    assert.deepEqual(f.errorNotices, [], "retired generation cannot emit delayed failure feedback");
    assert.deepEqual(f.notices, ["代码已复制"], "only the current generation emits feedback");
  } finally { f.close(); }
});

test("process clipboard tail swallows rejection and preserves within-adapter order", async () => {
  const started: string[] = [];
  const effects: string[] = [];
  let attempt = 0;
  const f = fixture("```sh\necho first\n```\n\n```sh\necho second\n```", false, undefined, undefined, {
    copy: async (text) => {
      started.push(text);
      attempt += 1;
      if (attempt === 1) throw new Error("first write failed");
      effects.push(text);
    },
  });
  try {
    const buttons = targets(f.tui, "pi-copy://");
    assert.equal(buttons.length, 2);
    click(f.terminal, buttons[0]!);
    click(f.terminal, buttons[1]!);
    await tick();
    await tick();
    await tick();
    assert.deepEqual(started, ["echo first", "echo second"]);
    assert.deepEqual(effects, ["echo second"], "a rejected operation cannot poison the settled global tail");
    assert.deepEqual(f.errorNotices, ["复制失败：Error: first write failed"]);
    assert.equal(f.notices.at(-1), "代码已复制");
  } finally { f.close(); }
});

test("process-wide clipboard queue preserves final-click order across adapter and module replacement", async () => {
  const adapterUrl = new URL("../src/adapter.ts", import.meta.url).href;
  const oldModule = await import(`${adapterUrl}?clipboard-old=${Date.now()}`) as typeof import("../src/adapter.ts");
  const freshModule = await import(`${adapterUrl}?clipboard-new=${Date.now()}`) as typeof import("../src/adapter.ts");
  assert.notEqual(oldModule.installAdapter, freshModule.installAdapter, "fixture uses independently evaluated modules");
  let releaseOld!: () => void;
  const oldGate = new Promise<void>((resolve) => { releaseOld = resolve; });
  const started: string[] = [];
  const effects: string[] = [];
  const copy = async (text: string) => {
    started.push(text);
    if (text === "echo old") await oldGate;
    effects.push(text);
  };

  const old = fixture("```sh\necho old\n```", false, undefined, undefined, {
    copy, installer: oldModule.installAdapter,
  });
  let oldStopped = false;
  let fresh: ReturnType<typeof fixture> | undefined;
  try {
    click(old.terminal, target(old.tui, "pi-copy://"));
    await tick();
    assert.deepEqual(started, ["echo old"]);
    assert.deepEqual(effects, []);

    old.adapter.dispose();
    old.tui.stop();
    oldStopped = true;
    fresh = fixture("```sh\necho new\n```", false, undefined, undefined, {
      copy, installer: freshModule.installAdapter,
    });
    click(fresh.terminal, target(fresh.tui, "pi-copy://"));
    await tick();
    await tick();
    assert.deepEqual(started, ["echo old"],
      "a hung non-abortable old endpoint blocks the successor adapter rather than allowing an overwrite race");
    assert.deepEqual(effects, []);

    releaseOld();
    await tick();
    await tick();
    await tick();
    assert.deepEqual(started, ["echo old", "echo new"]);
    assert.deepEqual(effects, ["echo old", "echo new"],
      "the delayed old side effect completes before the newer click starts");
    assert.equal(effects.at(-1), "echo new");
    assert.deepEqual(old.notices, [], "disposed adapter emits no delayed success feedback");
    assert.deepEqual(fresh.notices, ["代码已复制"]);
  } finally {
    if (!oldStopped) old.close();
    fresh?.close();
  }
});

test("Ctrl-press highlights the link, release restores it, drag cancels the effect", async () => {
  const f = fixture('[link](https://example.com/)');
  try {
    const p = target(f.tui, 'pi-copy://');
    f.terminal.input(sgr(p.x, p.y, 16)); f.tui.renderNow();
    assert.match(screen(f.tui)[p.y]!, /\x1b\[7m/, 'held Ctrl press shows reverse video');
    f.terminal.input(sgr(p.x, p.y, 16, 'm')); await tick(); f.tui.renderNow();
    assert.deepEqual(f.opened, ['https://example.com/']);
    assert.doesNotMatch(screen(f.tui)[p.y]!, /\x1b\[7m/, 'release restores the link');
    f.terminal.input(sgr(p.x, p.y, 16));
    f.terminal.input(sgr(p.x + 1, p.y, 48)); f.tui.renderNow();
    assert.doesNotMatch(screen(f.tui)[p.y]!, /\x1b\[7m/, 'drag clears the press effect');
    f.terminal.input(sgr(p.x, p.y, 16, 'm')); await tick();
    assert.equal(f.opened.length, 1, 'drag did not open');
  } finally { f.close(); }
});
