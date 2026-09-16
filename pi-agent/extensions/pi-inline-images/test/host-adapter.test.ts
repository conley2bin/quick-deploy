import assert from "node:assert/strict";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { HostImageOwnershipAdapter, type ReadPreviewCoordination } from "../src/host-adapter.ts";
import { ImageSession } from "../src/session.ts";
import { TerminalImages } from "../src/terminal.ts";
import { installedPiRoot } from "./pi-root.ts";

const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAEElEQVR42mP4z8DwHwAE/wJ/lJ4pWQAAAABJRU5ErkJggg==";
const timestamp = "2026-09-16T00:00:00.000Z";
function assistantMessage(id: string, calls: Array<{ id: string; name: string }>, text = "") {
  return {
    role: "assistant",
    content: [
      ...(text ? [{ type: "text", text }] : []),
      ...calls.map((call) => ({ type: "toolCall", id: call.id, name: call.name, arguments: { path: `${call.id}.png` } })),
    ],
    api: "fixture", provider: "none", model: "none",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "toolUse", timestamp: 0, id,
  };
}
function messageEntry(id: string, message: object) { return { type: "message", id, parentId: null, timestamp, message }; }
function toolResult(id: string, toolCallId: string, toolName = "read") {
  return messageEntry(id, { role: "toolResult", toolCallId, toolName, content: [{ type: "text", text: "image" }, { type: "image", mimeType: "image/png", data: png }], isError: false, timestamp: 0 });
}
function preview(id: string, toolCallId: string) {
  return { type: "custom", customType: "pi-tmux-images.preview", id: `custom-${id}`, parentId: null, timestamp, data: {
    logicalId: id,
    origin: { key: `tool:${toolCallId}`, blockIndex: 1 },
  } };
}
function hasNativeImage(component: { render(width: number): string[] }): boolean {
  return component.render(80).some((line) => line.includes("\x1b_G"));
}

async function hostComponents() {
  const root = installedPiRoot();
  const toolModule = await import(pathToFileURL(join(root, "dist/modes/interactive/components/tool-execution.js")).href);
  const assistantModule = await import(pathToFileURL(join(root, "dist/modes/interactive/components/assistant-message.js")).href);
  const theme = await import(pathToFileURL(join(root, "dist/modes/interactive/theme/theme.js")).href);
  const tui = await import(pathToFileURL(join(root, "node_modules/@earendil-works/pi-tui/dist/index.js")).href);
  theme.initTheme("dark", false);
  tui.setCapabilities({ images: "kitty", trueColor: true, hyperlinks: true });
  tui.setCellDimensions({ widthPx: 8, heightPx: 16 });
  const ui = { requestRender() {} };
  return {
    assistant: (message: object) => new assistantModule.AssistantMessageComponent(message, false, undefined, "Thinking...", 1, []),
    tool: (name: string, id: string) => new toolModule.ToolExecutionComponent(name, id, {}, { showImages: true, imageWidthCells: 60 }, undefined, ui, "/fixture"),
    setNativeProtocol: (images: "kitty" | "iterm2" | null) => tui.setCapabilities({ images, trueColor: true, hyperlinks: true }),
  };
}

function adapterFixture(children: object[], nativeImageProtocol?: () => "kitty" | "iterm2" | null) {
  const terminal = new TerminalImages(() => 1, () => ({ widthPx: 8, heightPx: 16 }), { write: () => true }, { TERM_PROGRAM: "ghostty" }, true, { transportLimits: { minIntervalMs: 0 } });
  const session = new ImageSession(terminal);
  const events: ReadPreviewCoordination[] = [];
  const adapter = new HostImageOwnershipAdapter(
    session,
    (event) => events.push(event),
    { version: "0.85.1", sessionEntryToContextMessages: (entry) => entry.message ? [entry.message] : [], nativeImageProtocol },
  );
  const tui = { children, terminal: { columns: 80 }, render: () => [], invalidate() {}, requestRender() {} };
  adapter.setTui(tui as never);
  return { adapter, events };
}

test("host adapter gives one complete read result exactly one custom bitmap owner", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-one", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  const result = (toolResult("result-one", "read-one") as { message: object }).message;
  readRow.updateResult(result);
  assert.equal(hasNativeImage(readRow), true);
  const entries = [messageEntry("assistant-entry", assistant), toolResult("result-entry", "read-one"), preview("preview-one", "read-one")];
  const raw = JSON.stringify(entries);
  const { adapter, events } = adapterFixture([assistantRow, readRow]);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  assert.equal(hasNativeImage(readRow), false, "native tool image is hidden only after complete custom ownership");
  assert.deepEqual(events.at(-1), { version: 1, ready: true, activeLogicalIds: ["preview-one"] });
  assert.equal(JSON.stringify(entries), raw, "display arbitration leaves raw session/tool content unchanged");
  adapter.dispose();
});

test("two identical read calls retain independent ownership and unrelated tool images remain native", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-many", [
    { id: "read-a", name: "read" },
    { id: "other", name: "other-image-tool" },
    { id: "read-b", name: "read" },
  ], "text before tools");
  const assistantRow = host.assistant(assistant);
  const readA = host.tool("read", "read-a");
  const other = host.tool("other-image-tool", "other");
  const readB = host.tool("read", "read-b");
  readA.updateResult((toolResult("ra", "read-a") as { message: object }).message);
  other.updateResult((toolResult("ro", "other", "other-image-tool") as { message: object }).message);
  readB.updateResult((toolResult("rb", "read-b") as { message: object }).message);
  const entries = [
    messageEntry("assistant-entry", assistant),
    toolResult("result-a", "read-a"),
    toolResult("result-other", "other", "other-image-tool"),
    toolResult("result-b", "read-b"),
    preview("logical-a", "read-a"),
    preview("logical-b", "read-b"),
  ];
  const { adapter, events } = adapterFixture([assistantRow, readA, other, readB]);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  assert.deepEqual([hasNativeImage(readA), hasNativeImage(other), hasNativeImage(readB)], [false, true, false]);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["logical-a", "logical-b"]);

  const cleared = [...entries, { type: "custom", customType: "pi-tmux-images.clear", id: "clear", parentId: null, timestamp, data: { marker: true } }];
  assert.equal(adapter.reconcile(cleared as never, cleared as never), true);
  assert.deepEqual([hasNativeImage(readA), hasNativeImage(other), hasNativeImage(readB)], [true, true, true]);
  adapter.dispose();
});

test("row-level native suppression requires custom coverage for every image block", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-multi-block", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  const resultEntry = toolResult("result", "read-one");
  const result = (resultEntry as { message: { content: object[] } }).message;
  result.content.push({ type: "image", mimeType: "image/png", data: png });
  readRow.updateResult(result);
  const entries = [messageEntry("assistant-entry", assistant), resultEntry, preview("preview-one", "read-one")];
  const { adapter, events } = adapterFixture([assistantRow, readRow]);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  assert.equal(hasNativeImage(readRow), true, "partial custom ownership cannot suppress the whole native row");
  assert.deepEqual(events.at(-1)?.activeLogicalIds, []);
  adapter.dispose();
});

test("pending row learns visible preference from its result and clear restores native display", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-pending", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  const initial = [messageEntry("assistant-entry", assistant)];
  const { adapter, events } = adapterFixture([assistantRow, readRow]);
  assert.equal(adapter.reconcile(initial as never, initial as never), true);
  assert.equal(hasNativeImage(readRow), false, "pending row has unknown preference, not a proven false preference");

  const resultEntry = toolResult("result", "read-one");
  readRow.updateResult((resultEntry as { message: object }).message);
  assert.equal(hasNativeImage(readRow), true);
  const owned = [...initial, resultEntry, preview("preview-one", "read-one")];
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.equal(hasNativeImage(readRow), false);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"]);

  const cleared = [...owned, { type: "custom", customType: "pi-tmux-images.clear", id: "clear", parentId: null, timestamp, data: { marker: true } }];
  assert.equal(adapter.reconcile(cleared as never, cleared as never), true);
  assert.equal(hasNativeImage(readRow), true, "clear restores the proven host-visible preference");
  adapter.dispose();
});

test("external off/on while claimed transfers authorization between zero and one owner", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-preference", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  const resultEntry = toolResult("result", "read-one");
  readRow.updateResult((resultEntry as { message: object }).message);
  const owned = [messageEntry("assistant-entry", assistant), resultEntry, preview("preview-one", "read-one")];
  const { adapter, events } = adapterFixture([assistantRow, readRow]);
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"]);

  readRow.setShowImages(false);
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.equal(hasNativeImage(readRow), false);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, [], "external off revokes custom authorization too");

  readRow.setShowImages(true);
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.equal(hasNativeImage(readRow), false, "external on re-enables the one custom owner, not native duplication");
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"]);
  adapter.dispose();
});

test("native capability null authorizes custom without inventing preference and follows explicit off/on", async () => {
  const host = await hostComponents();
  host.setNativeProtocol(null);
  let protocol: "kitty" | "iterm2" | null = null;
  const assistant = assistantMessage("assistant-tmux", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  const resultEntry = toolResult("result", "read-one");
  readRow.updateResult((resultEntry as { message: object }).message);
  assert.equal(hasNativeImage(readRow), false);
  const owned = [messageEntry("assistant-entry", assistant), resultEntry, preview("preview-one", "read-one")];
  const { adapter, events } = adapterFixture([assistantRow, readRow], () => protocol);
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"], "native capability absence cannot masquerade as user image-off");

  readRow.setShowImages(false);
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, []);
  readRow.setShowImages(true);
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"]);
  protocol = "kitty"; host.setNativeProtocol("kitty"); readRow.invalidate();
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.equal(hasNativeImage(readRow), false, "later native capability is suppressed before custom authorization remains active");
  protocol = null; host.setNativeProtocol(null); readRow.invalidate();
  assert.equal(adapter.reconcile(owned as never, owned as never), true);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"]);

  const cleared = [...owned, { type: "custom", customType: "pi-tmux-images.clear", id: "clear", parentId: null, timestamp, data: { marker: true } }];
  assert.equal(adapter.reconcile(cleared as never, cleared as never), true);
  protocol = "kitty"; host.setNativeProtocol("kitty"); readRow.invalidate();
  assert.equal(adapter.reconcile(cleared as never, cleared as never), true);
  assert.equal(hasNativeImage(readRow), true, "clear preserved the latent host-enabled flag while native capability was absent");
  adapter.dispose();
});

test("reconstruction hold renews only on a new public component generation", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-rebuild", [{ id: "read-one", name: "read" }]);
  const resultEntry = toolResult("result", "read-one");
  const entries = [messageEntry("assistant-entry", assistant), resultEntry, preview("preview-one", "read-one")];
  const firstAssistant = host.assistant(assistant); const firstRow = host.tool("read", "read-one");
  firstRow.updateResult((resultEntry as { message: object }).message);
  const children: object[] = [firstAssistant, firstRow];
  const { adapter, events } = adapterFixture(children);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  adapter.suspend("fixture reconstruction");
  assert.equal(adapter.reconcile(entries as never, entries as never), false, "old component generation cannot reacquire during reconstruction hold");
  assert.equal(events.at(-1)?.ready, false);

  const nextAssistant = host.assistant(assistant); const nextRow = host.tool("read", "read-one");
  nextRow.updateResult((resultEntry as { message: object }).message); children.splice(0, children.length, nextAssistant, nextRow);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, ["preview-one"]);
  adapter.dispose();
});

test("association mismatch restores only adapter-owned suppression and publishes fail-closed coordination", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-mismatch", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  readRow.updateResult((toolResult("result", "read-one") as { message: object }).message);
  const entries = [messageEntry("assistant-entry", assistant), toolResult("result-entry", "read-one"), preview("preview-one", "read-one")];
  const children: object[] = [assistantRow, readRow];
  const { adapter, events } = adapterFixture(children);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  assert.equal(hasNativeImage(readRow), false);

  const overlayTool = host.tool("pending", "overlay");
  children.push({ children: [overlayTool], render: () => [], invalidate() {} });
  assert.equal(adapter.reconcile(entries as never, entries as never), false);
  assert.equal(hasNativeImage(readRow), true, "failed association restores only the row previously suppressed by the adapter");
  assert.equal(events.at(-1)?.ready, false);
  assert.deepEqual(events.at(-1)?.activeLogicalIds, []);
  adapter.dispose();
});

test("an externally disabled native row is never blindly re-enabled", async () => {
  const host = await hostComponents();
  const assistant = assistantMessage("assistant-disabled", [{ id: "read-one", name: "read" }]);
  const assistantRow = host.assistant(assistant);
  const readRow = host.tool("read", "read-one");
  readRow.updateResult((toolResult("result", "read-one") as { message: object }).message);
  readRow.setShowImages(false);
  const entries = [messageEntry("assistant-entry", assistant), toolResult("result-entry", "read-one"), preview("preview-one", "read-one")];
  const { adapter } = adapterFixture([assistantRow, readRow]);
  assert.equal(adapter.reconcile(entries as never, entries as never), true);
  const cleared = [...entries, { type: "custom", customType: "pi-tmux-images.clear", id: "clear", parentId: null, timestamp, data: { marker: true } }];
  assert.equal(adapter.reconcile(cleared as never, cleared as never), true);
  assert.equal(hasNativeImage(readRow), false);
  adapter.dispose();
});
