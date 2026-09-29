// Provider-free production-loader repro: compaction rebuilds duplicate-source assistant Markdown
// after the old tree's forced wake. The new tree paints once without a host binding.
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const base = process.cwd();
const core = process.env.PI_HOST_ROOT;
if (!core) throw new Error("PI_HOST_ROOT is required");
delete process.env.TMUX;
delete process.env.TMUX_PANE;
process.env.TERM = "xterm-ghostty";
process.env.TERM_PROGRAM = "ghostty";
const chunk = readdirSync(resolve(core, "dist/bundle/chunks")).find((name) => /^virtual-modules-.*\.js$/u.test(name));
if (!chunk) throw new Error("installed Pi has no virtual module bundle");
const { VIRTUAL_MODULES } = await import(pathToFileURL(resolve(core, "dist/bundle/chunks", chunk)).href);
const pi = VIRTUAL_MODULES["@earendil-works/pi-coding-agent"];
const tui = VIRTUAL_MODULES["@earendil-works/pi-tui"];
pi.initTheme("dark", false);
tui.setCapabilities({ images: "kitty", trueColor: true, hyperlinks: true });
tui.setCellDimensions({ widthPx: 10, heightPx: 20 });

const settings = pi.SettingsManager.inMemory({ packages: [] });
const bus = pi.createEventBus();
const loader = new pi.DefaultResourceLoader({
  cwd: base, agentDir: resolve("/tmp", `pi-inline-compaction-absent-${process.pid}`),
  settingsManager: settings, eventBus: bus,
  additionalExtensionPaths: [resolve("../pi-copy-links/index.ts"), resolve("index.ts")],
  noExtensions: true, noSkills: true, noThemes: true, noPromptTemplates: true, noContextFiles: true,
  systemPrompt: "fixture", appendSystemPrompt: [],
});
await loader.reload();
const loaded = loader.getExtensions();
assert.deepEqual(loaded.errors, [], "both production extension factories must load");
assert.equal(loaded.extensions.length, 2, "the isolated loader must contain only the paired production extensions");
const manager = pi.SessionManager.inMemory(base);
const source = `Same ![image](${resolve("test/fixtures/color-block.png")}) end`;
const message = () => ({
  role: "assistant", content: [{ type: "text", text: source }],
  api: "fixture", provider: "none", model: "none", stopReason: "stop", timestamp: 0,
  usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
});
const first = manager.appendMessage(message());
manager.appendMessage(message());
const originalBranch = JSON.stringify(manager.getBranch());
class EventOnlySession extends pi.AgentSession { _buildRuntime() {} }
const session = new EventOnlySession({
  agent: { state: { tools: [], messages: [] }, subscribe() { return () => {}; } },
  sessionManager: manager, settingsManager: settings, cwd: base,
});
const runner = new pi.ExtensionRunner(loaded.extensions, loaded.runtime, base, manager, {});
runner.mode = "tui";
session._extensionRunner = runner;
const mode = Object.create(pi.InteractiveMode.prototype);
Object.assign(mode, {
  entriesRenderedByBoundaryCompaction: new Set(), runtimeHost: { session }, isInitialized: true,
  pendingTools: new Map(), chatContainer: new tui.Container(), footer: { invalidate() {} },
  hideThinkingBlock: false, hiddenThinkingLabel: "Thinking...", outputPad: 1,
  toolOutputExpanded: false, getMarkdownThemeWithSettings: () => pi.getMarkdownTheme(),
  getMarkdownTransformers: () => loaded.extensions.flatMap((extension) => extension.markdownTransformer ? [extension.markdownTransformer] : []),
  getRegisteredToolDefinition: () => undefined, maybeShowAssistantDiagnostics() {},
  maybeShowCacheMissNotice() {}, updatePendingMessagesDisplay() {}, clearStatusIndicator() {},
  compactionQueuedMessages: [],
});
let requests = 0;
let forced = 0;
let paintScheduled = false;
let lines = [];
const widgets = new Map();
const frames = [];
const rows = () => mode.chatContainer.children.filter((child) => child instanceof pi.AssistantMessageComponent);
const stats = () => {
  const text = lines.join("\n");
  return {
    glyphs: [...text].filter((c) => c === "\u{10EEEE}").length,
    notices: (text.match(/host image coordination/gu) ?? []).length,
    requests, forced,
  };
};
function paint() {
  lines = mode.chatContainer.render(80);
  for (const widget of widgets.values()) widget.render(80);
  frames.push(stats());
}
const ui = {
  mode: "fullscreen", children: [mode.chatContainer], terminal: { columns: 80 },
  invalidate() { for (const child of this.children) child.invalidate(); },
  requestRender(force) {
    requests++;
    if (force) forced++;
    if (!paintScheduled) {
      paintScheduled = true;
      setTimeout(() => { paintScheduled = false; paint(); }, 1);
    }
  },
  renderNow() { paint(); }, get previousScreen() { return lines; },
};
mode.ui = ui;
runner.uiContext = {
  setWidget(key, factory) {
    if (factory) widgets.set(key, factory(ui));
    else widgets.delete(key);
    ui.children = [mode.chatContainer, ...widgets.values()];
  },
  theme: { fg: (_color, text) => text }, notify() {},
};
loaded.runtime.getAllTools = () => [];
const coordination = [];
bus.on("pi-inline-images:read-preview-coordination", (value) => coordination.push(value));
const settle = () => new Promise((done) => setTimeout(done, 100));
// Capture Kitty protocol traffic without writing it to this test process's real terminal.
const writes = [];
const originalWrite = process.stdout.write;
process.stdout.write = function (bytes, ...args) {
  writes.push(Buffer.from(bytes));
  const callback = args.at(-1);
  if (typeof callback === "function") callback();
  return true;
};
let report;
try {
  await runner.emit({ type: "session_start", reason: "startup" });
  await settle();
  mode.renderSessionEntries(manager.buildContextEntries());
  paint();
  await settle();
  ui.invalidate();
  paint();
  await settle();
  const before = stats();
  assert.equal(rows().length, 2);
  assert.deepEqual([before.glyphs, before.notices], [4, 0], "history must be healthy before compaction");

  const compactId = manager.appendCompaction("summary", first, 1000);
  await runner.emit({ type: "session_compact", compactionEntry: manager.getEntry(compactId),
    fromExtension: true, reason: "manual", willRetry: false });
  // Let the forced wake drain against the old public component generation.
  await settle();
  const firstRebuiltFrame = frames.length;
  await mode.handleEvent({ type: "compaction_end", reason: "manual",
    result: { summary: "summary", firstKeptEntryId: first, tokensBefore: 1000 },
    aborted: false, willRetry: false });
  const rebuilt = rows();
  assert.equal(rebuilt.length, 2, "native compaction must create two new assistant rows");
  const boundBeforeFirstPaint = rebuilt.some((row) => Object.prototype.hasOwnProperty.call(row, "render"));
  // This is the causal boundary: native Markdown renders before the repaint widget can reconcile.
  paint();
  const unbound = stats();
  await settle();
  const recovered = stats();
  paint();
  await settle();
  const idle = stats();
  const forcedAfterRebuild = recovered.forced - unbound.forced;
  report = { before, unbound, recovered, idle, forcedAfterRebuild, boundBeforeFirstPaint,
    frames: frames.slice(firstRebuiltFrame), ready: coordination.at(-1)?.ready,
    bitmapUploads: writes.filter((bytes) => bytes.includes("a=t")).length,
    rawMessagesUnchanged: JSON.stringify(manager.getBranch().filter((entry) => entry.type === "message")) === originalBranch };
} finally {
  await runner.emit({ type: "session_shutdown" });
  await settle();
  process.stdout.write = originalWrite;
}
console.log(`INLINE_COMPACTION_JSON ${JSON.stringify(report)}`);
