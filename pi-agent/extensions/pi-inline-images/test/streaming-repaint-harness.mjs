// Streaming repaint regression harness.
// Loads the ACTUAL pi-inline-images entrypoint through the real extension runner, drives real
// message_start/message_update/message_end events, and paints through a real TuiMainScreen over a
// recording terminal, so a forced history re-emission is observable as ESC[3J + a repeated banner.
// Run with: node --import tsx test/streaming-repaint-harness.mjs   (PI_HOST_ROOT required)
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const base = process.cwd();
const core = process.env.PI_HOST_ROOT;
if (!core) throw new Error("PI_HOST_ROOT is required");
delete process.env.TMUX;
delete process.env.TMUX_PANE;
process.env.TERM = "xterm-ghostty";
process.env.TERM_PROGRAM = "ghostty";
const imp = (p) => import(pathToFileURL(p).href);

const { InteractiveMode } = await imp(`${core}/dist/modes/interactive/interactive-mode.js`);
const { AgentSession } = await imp(`${core}/dist/core/agent-session.js`);
const { SessionManager } = await imp(`${core}/dist/core/session-manager.js`);
const { SettingsManager } = await imp(`${core}/dist/core/settings-manager.js`);
const { loadExtensions } = await imp(`${core}/dist/core/extensions/loader.js`);
const { ExtensionRunner } = await imp(`${core}/dist/core/extensions/runner.js`);
const { createEventBus } = await imp(`${core}/dist/core/event-bus.js`);
const piTui = await imp(`${core}/node_modules/@earendil-works/pi-tui/dist/index.js`);
const theme = await imp(`${core}/dist/modes/interactive/theme/theme.js`);
const { AssistantMessageComponent } = await imp(`${core}/dist/modes/interactive/components/assistant-message.js`);
const { ToolExecutionComponent } = await imp(`${core}/dist/modes/interactive/components/tool-execution.js`);
theme.initTheme("dark", false);
piTui.setCapabilities({ images: null, trueColor: true, hyperlinks: true });
piTui.setCellDimensions({ widthPx: 10, heightPx: 20 });

const BANNER = "PI-STARTUP-BANNER";
const TOKENS = 40;
let phase = "startup";
const writes = [];
class RecordingTerminal {
  constructor(columns, rows) { this._c = columns; this._r = rows; }
  start() {} stop() {} async drainInput() {}
  write(data) { writes.push({ phase, data }); }
  get columns() { return this._c; } get rows() { return this._r; }
  get kittyProtocolActive() { return false; }
  moveBy() {} hideCursor() {} showCursor() {} clearLine() {} clearFromCursor() {} clearScreen() {} setTitle() {} setProgress() {}
}
const ui = new piTui.TuiMainScreen(new RecordingTerminal(100, 24), false, undefined);
let forceRenders = 0;
const resetRenderState = ui.resetRenderState.bind(ui);
ui.resetRenderState = () => { forceRenders++; return resetRenderState(); };
let renderRequests = 0;
const requestRender = ui.requestRender.bind(ui);
ui.requestRender = (...args) => { renderRequests++; return requestRender(...args); };
const paint = () => ui.renderNow();
const phaseStats = (mark) => {
  const slice = writes.slice(mark.index);
  const text = slice.map((entry) => entry.data).join("");
  return { forceRenders: forceRenders - mark.forceRenders, renderRequests: renderRequests - mark.renderRequests,
           historicalInvalidations: historicalInvalidations - mark.historicalInvalidations,
           scrollbackWipes: text.split("\x1b[3J").length - 1, bannerRepaints: text.split(BANNER).length - 1 };
};
let historicalInvalidations = 0;

class EventOnlySession extends AgentSession { _buildRuntime() {} }
const sessionManager = SessionManager.inMemory(base);
const settingsManager = SettingsManager.inMemory();
const bus = createEventBus();
const coordination = [];
bus.on("pi-inline-images:read-preview-coordination", (value) => coordination.push(value));
const loaded = await loadExtensions([`${base}/index.ts`], base, bus);
assert.deepEqual(loaded.errors, [], "entrypoint must load without extension errors");
const runner = new ExtensionRunner(loaded.extensions, loaded.runtime, base, sessionManager, {});
runner.mode = "tui";
const session = new EventOnlySession({
  agent: { state: { tools: [], messages: [] }, subscribe() { return () => {}; } },
  sessionManager, settingsManager, cwd: base,
});
session._extensionRunner = runner;

const assistant = (text, calls = []) => ({
  role: "assistant",
  content: [...(text ? [{ type: "text", text }] : []), ...calls.map((id) => ({ type: "toolCall", id, name: "read", arguments: { path: "x.png" } }))],
  api: "fixture", provider: "none", model: "none",
  usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
  stopReason: calls.length ? "toolUse" : "stop", timestamp: 0,
});

const mode = Object.create(InteractiveMode.prototype);
Object.assign(mode, {
  runtimeHost: { session }, isInitialized: true, pendingTools: new Map(), chatContainer: new piTui.Container(),
  footer: { invalidate() {} }, hideThinkingBlock: false, hiddenThinkingLabel: "Thinking...", outputPad: 1,
  toolOutputExpanded: false,
  getMarkdownThemeWithSettings: () => theme.getMarkdownTheme(),
  getMarkdownTransformers: () => loaded.extensions.flatMap((extension) => (extension.markdownTransformer ? [extension.markdownTransformer] : [])),
  getRegisteredToolDefinition: () => undefined,
  maybeShowAssistantDiagnostics() {}, maybeShowCacheMissNotice() {}, updatePendingMessagesDisplay() {},
});
ui.addChild(new piTui.Text(BANNER, 0, 0));
ui.addChild(mode.chatContainer);
let widget;
mode.ui = ui;
runner.uiContext = {
  setWidget(_key, factory) {
    if (widget) ui.removeChild(widget);
    widget = factory ? factory(ui) : undefined;
    if (widget) ui.addChild(widget);
  },
  notify() {},
};
loaded.runtime.getAllTools = () => [];
loaded.runtime.appendEntry = (type, data) => sessionManager.appendCustomEntry(type, data);

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
const event = async (payload) => { await session._handleAgentEvent(payload); await settle(); };
session.subscribe((payload) => mode.handleEvent(payload));

await runner.emit({ type: "session_start", reason: "startup" });
paint();
await settle();
for (let index = 0; index < 12; index++) {
  await event({ type: "message_start", message: assistant() });
  await event({ type: "message_end", message: assistant(`history ${index}`) });
}
if (mode.chatContainer.children.length < 12) {
  mode.chatContainer.clear();
  mode.renderSessionEntries(sessionManager.buildContextEntries());
}
const historical = mode.chatContainer.children.filter((child) => child instanceof AssistantMessageComponent);
assert.ok(historical.length >= 12, `expected nonempty history, saw ${historical.length}`);
for (const component of historical) {
  const original = component.invalidate.bind(component);
  component.invalidate = () => { historicalInvalidations++; return original(); };
}
paint();

// --- streaming phase: many assistant tokens, each followed by the application's own frame
phase = "streaming";
const mark = { index: writes.length, forceRenders, renderRequests, historicalInvalidations };
await event({ type: "message_start", message: assistant() });
let text = "";
for (let index = 0; index < TOKENS; index++) {
  text += `tok${index} `;
  const partial = assistant(text);
  await event({ type: "message_update", message: partial, assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: `tok${index} `, partial } });
  paint();
}
const streaming = phaseStats(mark);
const rendered = ui.render(100).join("\n");
const streamingTextPreserved = rendered.includes(`tok${TOKENS - 1} `);

// --- controls: image/ownership transitions must still rebuild, live tool rows must still bind
phase = "message_end";
const endMark = { index: writes.length, forceRenders, renderRequests, historicalInvalidations };
await event({ type: "message_end", message: assistant(text) });
paint();
await settle();
const messageEnd = phaseStats(endMark);

phase = "entries-changed";
const entriesMark = { index: writes.length, forceRenders, renderRequests, historicalInvalidations };
bus.emit("pi-inline-images:read-preview-entries-changed", { version: 1 });
await settle();
paint();
const entriesChanged = phaseStats(entriesMark);

phase = "live-tool";
await event({ type: "message_start", message: assistant() });
const withCall = assistant("", ["live-tool-row"]);
await event({ type: "message_update", message: withCall, assistantMessageEvent: { type: "toolcall_end", contentIndex: 0, toolCall: withCall.content[0], partial: withCall } });
await event({ type: "tool_execution_start", toolCallId: "live-tool-row", toolName: "read", args: { path: "x.png" } });
paint();
await settle();
const toolRow = [...mode.chatContainer.children].reverse().find((child) => child instanceof ToolExecutionComponent && child.toolCallId === "live-tool-row");
const liveToolBound = Boolean(toolRow) && toolRow.setShowImages !== ToolExecutionComponent.prototype.setShowImages;

await runner.emit({ type: "session_shutdown" });
await settle();
assert.equal(forceRenders, forceRenders, "counter sanity");
console.log(`STREAM_JSON ${JSON.stringify({
  tokens: TOKENS, historyRows: historical.length, streaming, streamingTextPreserved,
  messageEnd: { forceRenders: messageEnd.forceRenders },
  entriesChanged: { forceRenders: entriesChanged.forceRenders },
  liveToolBound, coordinationEvents: coordination.length,
})}`);
