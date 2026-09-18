import assert from "node:assert/strict";
import test from "node:test";
import { AssistantMessageComponent, getMarkdownTheme, initTheme, VERSION } from "@earendil-works/pi-coding-agent";
import { getCapabilities, getOsc8LinkAtColumn, Markdown, setCapabilities, stripTerminalSequences, TuiAltScreen, visibleWidth, type Terminal } from "@earendil-works/pi-tui";
import { installAdapter } from "../src/adapter.ts";

// Count WeakRef constructions for every adapter installed below. V8 both caps a
// Set at 2^24 entries and registers each WeakRef's target in a kept-objects list
// that drains only at event-loop turn boundaries, so sustaining those
// constructions from a render hook crashed Pi with
// "RangeError: Set maximum size exceeded" thrown out of render().
let weakRefCount = 0;
let weakRefCalls = 0;
const realWeakRef = WeakRef;
let weakRefFails = false;
globalThis.WeakRef = new Proxy(realWeakRef, {
  construct(target, args: [object]) {
    weakRefCalls += 1;
    if (weakRefFails) throw new RangeError("Set maximum size exceeded");
    weakRefCount += 1;
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
  clearScreen() {} setTitle() {} setProgress() {}
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
function screen(tui: TuiAltScreen): string[] { return (tui as unknown as { previousScreen: string[] }).previousScreen; }
function target(tui: TuiAltScreen, prefix: string) {
  for (const [y, line] of screen(tui).entries()) for (let x = 0; x < tui.terminal.columns; x++) {
    const url = getOsc8LinkAtColumn(line, x);
    if (url?.startsWith(prefix)) return { x, y, url };
  }
  assert.fail(`Missing ${prefix} in:\n${screen(tui).map(stripTerminalSequences).join('\n')}`);
}
function click(terminal: FakeTerminal, point: {x: number; y: number}, bits = 0) {
  terminal.input(sgr(point.x, point.y, bits)); terminal.input(sgr(point.x, point.y, bits, 'm'));
}
function fixture(source: string, tmuxRecovery = false, flashMs?: number) {
  const terminal = new FakeTerminal();
  const copied: string[] = [], opened: string[] = [], nativeOpened: string[] = [], notices: string[] = [];
  let active = true;
  const adapter = installAdapter({ version: VERSION, active: () => active,
    copy: async text => { copied.push(text); }, open: async url => { opened.push(url); },
    notify: text => { notices.push(text); }, button: text => text, recoverTmuxRelease: () => tmuxRecovery, flashMs });
  const component = new AssistantMessageComponent(message(source), false, undefined, "Thinking", 1, [adapter.transform]);
  const tui = new TuiAltScreen(terminal, false, undefined, { openUrl: url => nativeOpened.push(url), copySelection: async () => true });
  tui.addChild(component); tui.start(); tui.renderNow();
  return { terminal, component, tui, copied, opened, nativeOpened, notices, adapter,
    setActive(value: boolean) { active = value; component.invalidate(); tui.renderNow(); },
    close() { adapter.dispose(); tui.stop(); } };
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

test("exhausted V8 WeakRef capacity degrades to plain Markdown instead of crashing", async (t) => {
  // The process-wide 2^24 ceiling is not ours, but crossing it inside a render
  // hook used to kill Pi with an uncaughtException. It has to degrade instead.
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture('```sh\necho x\n```');
  const buttons = () => /pi-copy:\/\//.test(screen(f.tui).join("\n"));
  try {
    weakRefFails = true;
    const before = weakRefCalls;
    f.component.invalidate();
    assert.doesNotThrow(() => f.tui.renderNow(), "a render survives a WeakRef construction failure");
    assert.ok(weakRefCalls > before, "the exhausted construction was attempted");
    assert.equal(buttons(), false, "the feature degrades to plain Markdown");
    weakRefFails = false;
    t.mock.timers.tick(1000);
    f.component.invalidate();
    f.tui.renderNow();
    assert.equal(buttons(), true, "the feature recovers once the construction succeeds again");
    click(f.terminal, target(f.tui, 'pi-copy://')); await tick();
    assert.deepEqual(f.copied, ['echo x'], "the recovered button still copies the exact block");
  } finally { weakRefFails = false; f.close(); }
});

test("Ctrl-click opens original wrapped Markdown URL; normal click and drag do not", async () => {
  const capabilities = getCapabilities(); setCapabilities({ ...capabilities, hyperlinks: false });
  const url = 'https://example.com/path?a=1&b=2#fragment';
  const f = fixture(`[中文 ${'wrapped link '.repeat(12)}](${url})`);
  try {
    const point = target(f.tui, 'https:');
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
  const original = Markdown.prototype.render;
  const f = fixture('```sh\necho x\n```');
  try {
    const ordinary = new Markdown('```sh\necho y\n```', 1, 0, getMarkdownTheme());
    assert.doesNotMatch(ordinary.render(60).join('\n'), /pi-copy:\/\//);
    f.setActive(false); assert.doesNotMatch(screen(f.tui).join('\n'), /pi-copy:\/\//);
    f.setActive(true); assert.match(screen(f.tui).join('\n'), /pi-copy:\/\//);
  } finally { f.close(); }
  assert.equal(Markdown.prototype.render, original);
});

test("incompatible Pi refuses adaptation before any prototype changes", () => {
  const original = Markdown.prototype.render;
  assert.throws(() => installAdapter({ version: '9.9.9', active: () => true,
    copy: async () => {}, open: async () => {}, notify: () => {}, button: s => s }), /仅适配/);
  assert.equal(Markdown.prototype.render, original);
});


test("tmux lost-press recovery requires a recent modifier transition, stable screen and no drag", async () => {
  const f = fixture('[link](https://example.com/)', true);
  try {
    let point = target(f.tui, 'https:');
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.deepEqual(f.opened, [], 'arbitrary orphan release is not a click');
    click(f.terminal, point); await tick();
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.deepEqual(f.opened, ['https://example.com/'], 'known tmux modifier-transition loss is recovered');
    click(f.terminal, point); await tick();
    f.terminal.input(sgr(point.x + 1, point.y, 48));
    f.terminal.input(sgr(point.x, point.y, 16, 'm')); await tick();
    assert.equal(f.opened.length, 1, 'drag cancels recovery even if it returns to the same link');
    click(f.terminal, point); await tick();
    f.component.updateContent(message('inserted row\n\n[link](https://example.com/)')); f.tui.renderNow();
    point = target(f.tui, 'https:');
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

test("Ctrl-press highlights the link, release restores it, drag cancels the effect", async () => {
  const f = fixture('[link](https://example.com/)');
  try {
    const p = target(f.tui, 'https:');
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
