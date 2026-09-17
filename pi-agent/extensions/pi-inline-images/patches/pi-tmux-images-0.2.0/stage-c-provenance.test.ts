import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import sharp from "sharp";
import { installGraphicsBridge } from "../../src/bridge.ts";
import { TerminalImages } from "../../src/terminal.ts";
import type { TransportSink } from "../../src/transport.ts";
import { installedPiRoot } from "../../test/pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");
const ENTRY_TYPE = "pi-tmux-images.preview";
const PLACEHOLDER = "\u{10EEEE}";

type Handler = (event: any, context: any) => unknown;
type Renderer = (entry: { data: unknown }, options: unknown, theme: unknown) => { render(width: number): string[] };

class Bus {
  private readonly handlers = new Map<string, Set<(data: unknown) => void>>();
  emit(channel: string, data: unknown): void { for (const handler of this.handlers.get(channel) ?? []) handler(data); }
  on(channel: string, handler: (data: unknown) => void): () => void {
    const handlers = this.handlers.get(channel) ?? new Set(); handlers.add(handler); this.handlers.set(channel, handlers);
    return () => handlers.delete(handler);
  }
}
class Sink extends EventEmitter implements TransportSink {
  readonly writes: Buffer[] = [];
  write(value: Buffer): boolean { this.writes.push(Buffer.from(value)); return true; }
}

function builtinReadTool() {
  return { name: "read", description: "read", parameters: {}, promptGuidelines: [], sourceInfo: { path: "<builtin:read>", source: "builtin", scope: "temporary", origin: "top-level" } };
}
function wrapperReadTool() {
  // A wrapper may look builtin at the event/result surface; its registry path is still not the base definition.
  return { name: "read", description: "read", parameters: {}, promptGuidelines: [], sourceInfo: { path: "<extension:shellgate-read>", source: "builtin", scope: "temporary", origin: "top-level" } };
}
function visibleWidth(value: string): number {
  const plain = value.replace(/\x1b\[[0-?]*[ -/]*[@-~]/gu, "");
  let width = 0;
  for (const character of plain) {
    const code = character.codePointAt(0)!;
    if (/\p{Mark}/u.test(character)) continue;
    width += code >= 0x1100 && (code <= 0x115f || (code >= 0x2e80 && code <= 0xa4cf)
      || (code >= 0xac00 && code <= 0xd7a3) || (code >= 0xf900 && code <= 0xfaff)
      || (code >= 0xff00 && code <= 0xff60) || (code >= 0x1f300 && code <= 0x1faff)) ? 2 : 1;
  }
  return width;
}

function disposablePackage(): { root: string; cleanup(): void } {
  const root = mkdtempSync(resolve(tmpdir(), "pi-tmux-images-provenance-"));
  cpSync(installed, root, { recursive: true });
  execFileSync(replay, ["apply", root], { stdio: "pipe" });
  const modules = resolve(root, "node_modules");
  mkdirSync(resolve(modules, "@earendil-works"), { recursive: true });
  const piRoot = installedPiRoot();
  symlinkSync(piRoot, resolve(modules, "@earendil-works/pi-coding-agent"), "dir");
  symlinkSync(resolve(piRoot, "node_modules/@earendil-works/pi-tui"), resolve(modules, "@earendil-works/pi-tui"), "dir");
  symlinkSync(resolve(process.env.HOME!, ".pi/agent/npm/node_modules/sharp"), resolve(modules, "sharp"), "dir");
  return { root, cleanup: () => rmSync(root, { recursive: true, force: true }) };
}

function fakeApi(bus: Bus, branch: Array<Record<string, unknown>>, tool: () => unknown) {
  const handlers = new Map<string, Handler[]>();
  const renderers = new Map<string, Renderer>();
  const api = {
    events: bus,
    getAllTools: () => [tool()],
    on(name: string, handler: Handler) { const list = handlers.get(name) ?? []; list.push(handler); handlers.set(name, list); },
    registerEntryRenderer(type: string, renderer: Renderer) { renderers.set(type, renderer); },
    registerCommand() {},
    appendEntry(type: string, data: unknown) { branch.push({ type: "custom", customType: type, data }); },
  };
  return { api, handlers, renderers };
}
async function emit(handlers: Map<string, Handler[]>, name: string, event: unknown, context: unknown): Promise<void> {
  for (const handler of handlers.get(name) ?? []) await handler(event, context);
}
function previewEntries(branch: Array<Record<string, unknown>>): any[] {
  return branch.filter((entry) => entry.type === "custom" && entry.customType === ENTRY_TYPE).map((entry) => entry.data);
}

async function rgbaPng(width: number, height: number, seed: number): Promise<{ png: Buffer; firstAlpha: number }> {
  const raw = Buffer.alloc(width * height * 4);
  for (let index = 0; index < raw.length; index += 4) {
    raw[index] = (seed + index) & 0xff;
    raw[index + 1] = (seed * 3 + index) & 0xff;
    raw[index + 2] = (seed * 7 + index) & 0xff;
    raw[index + 3] = index === 0 ? 17 + seed : (seed + index / 4) & 0xff;
  }
  return { png: await sharp(raw, { raw: { width, height, channels: 4 } }).png().toBuffer(), firstAlpha: 17 + seed };
}

async function assertReceivedPixels(runtime: any, logicalId: string, receivedData: string): Promise<void> {
  const actual = runtime.get(logicalId);
  assert.ok(actual);
  const [actualRaw, expectedRaw] = await Promise.all([
    sharp(actual.png).ensureAlpha().raw().toBuffer(),
    sharp(Buffer.from(receivedData, "base64")).ensureAlpha().raw().toBuffer(),
  ]);
  assert.deepEqual(actualRaw, expectedRaw);
}

test("real handlers substitute only proven builtin-local originals and keep every tool/model block immutable", async () => {
  const copy = disposablePackage();
  const fixture = mkdtempSync(resolve(tmpdir(), "pi-read-provenance-fixture-"));
  const longPath = resolve(fixture, `${"目录".repeat(40)}-alpha.png`);
  const changedPath = resolve(fixture, "changed.png");
  const mismatchPath = resolve(fixture, "mismatch.png");
  const wrapperPath = resolve(fixture, "wrapper.png");
  const original = await rgbaPng(2104, 6, 1);
  const changedOriginal = await rgbaPng(2104, 5, 2);
  const mismatchOriginal = await rgbaPng(64, 32, 3);
  const wrapperOriginal = await rgbaPng(2104, 4, 4);
  writeFileSync(longPath, original.png);
  writeFileSync(changedPath, changedOriginal.png);
  writeFileSync(mismatchPath, mismatchOriginal.png);
  writeFileSync(wrapperPath, wrapperOriginal.png);

  const piRoot = installedPiRoot();
  const { resizeImage } = await import(pathToFileURL(resolve(piRoot, "dist/index.js")).href);
  const resized = await resizeImage(original.png, "image/png");
  const changedResized = await resizeImage(changedOriginal.png, "image/png");
  const wrapperResized = await resizeImage(wrapperOriginal.png, "image/png");
  assert.ok(resized?.wasResized && changedResized?.wasResized && wrapperResized?.wasResized);

  const sink = new Sink();
  let nextId = 800;
  const terminal = new TerminalImages(() => nextId++, () => ({ widthPx: 8, heightPx: 16 }), sink, { TERM_PROGRAM: "ghostty" }, true, {
    transportLimits: { minIntervalMs: 0, wireRateBytesPerSecond: 1_000_000_000 },
  });
  terminal.setViewerManaged(true);
  await terminal.setViewer({ ready: true, epoch: "viewer", reason: "", attached: ["viewer"], receivers: ["viewer"] });
  const bus = new Bus();
  const removeBridge = installGraphicsBridge(bus as never, terminal);
  const branch: Array<Record<string, unknown>> = [];
  let selectedTool: () => unknown = builtinReadTool;
  const fake = fakeApi(bus, branch, () => selectedTool());
  const context = { cwd: fixture, sessionManager: { getBranch: () => branch }, ui: { notify() {} } };

  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?c2=${Date.now()}`);
    const extensionModule = await import(`${pathToFileURL(resolve(copy.root, "extensions/index.ts")).href}?c2=${Date.now()}`);
    const runtime = new runtimeModule.PreviewRuntime();
    extensionModule.registerInlineImages(fake.api, runtime);
    await emit(fake.handlers, "session_start", {}, context);

    const runRead = async (toolCallId: string, path: string, data: string, mimeType: string, afterCall?: () => void) => {
      await emit(fake.handlers, "tool_call", { type: "tool_call", toolName: "read", toolCallId, input: { path } }, context);
      afterCall?.();
      const content = [{ type: "text", text: "Read image" }, { type: "image", data, mimeType }];
      const resultEvent = { type: "tool_result", toolName: "read", toolCallId, input: { path }, content, isError: false, details: undefined };
      const resultBefore = JSON.stringify(resultEvent);
      await emit(fake.handlers, "tool_result", resultEvent, context);
      assert.equal(JSON.stringify(resultEvent), resultBefore, "tool_result bytes remain unchanged");
      const message = { role: "toolResult", toolCallId, content };
      const messageBefore = JSON.stringify(message);
      await emit(fake.handlers, "message_end", { type: "message_end", message }, context);
      assert.equal(JSON.stringify(message), messageBefore, "model/session message remains unchanged");
      branch.push({ type: "message", message });
      return previewEntries(branch).at(-1)!;
    };

    const verified = await runRead("verified", longPath, resized!.data, resized!.mimeType);
    assert.equal(verified.readProvenance.status, "verified-local-original");
    assert.equal(verified.readProvenance.toolCallId, "verified");
    assert.equal(verified.readProvenance.blockIndex, 1);
    assert.equal(verified.readProvenance.blockHash, verified.origin.contentHash);
    assert.equal(verified.readProvenance.original.sourceHash, verified.hash);
    assert.equal(verified.readProvenance.original.width, 2104);
    assert.equal(verified.width, 2104);
    assert.equal(verified.height, 6);
    const verifiedPixels = await sharp(runtime.get(verified.logicalId).png).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    assert.equal(verifiedPixels.info.width, 2104);
    assert.equal(verifiedPixels.info.height, 6);
    assert.equal(verifiedPixels.data[3], original.firstAlpha, "full-resolution alpha is preserved");
    assert.equal(runtime.fidelityNotice(verified.logicalId), undefined);

    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(runtime.get(verified.logicalId).width, 2104, "restore re-proves an unchanged builtin-local original");
    selectedTool = wrapperReadTool;
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(runtime.get(verified.logicalId).width, 2000, "restore cannot trust stale proof under a wrapper");
    assert.match(runtime.fidelityNotice(verified.logicalId), /not the standard builtin local reader/u);
    selectedTool = builtinReadTool;
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(runtime.get(verified.logicalId).width, 2104);
    unlinkSync(longPath);
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(runtime.get(verified.logicalId).width, 2000, "missing original falls back to received pixels");
    assert.match(runtime.fidelityNotice(verified.logicalId), /source is missing/u);
    await assertReceivedPixels(runtime, verified.logicalId, resized!.data);

    bus.emit("pi-inline-images:read-preview-coordination", { version: 1, ready: true, activeLogicalIds: [verified.logicalId] });
    const renderer = fake.renderers.get(ENTRY_TYPE)!;
    for (const width of [16, 40]) {
      const rendered = renderer({ data: verified }, {}, {}).render(width);
      assert.doesNotMatch(rendered.join("\n"), /Original resolution unavailable/u, "unverified origins render without a display notice");
      assert.ok(rendered.some((line) => line.includes(PLACEHOLDER)), `received pixels still render at width ${width}`);
      assert.ok(rendered.every((line) => visibleWidth(line) <= width), `rows fit width ${width}`);
    }

    const changed = await runRead("changed", changedPath, changedResized!.data, changedResized!.mimeType, () => {
      writeFileSync(changedPath, mismatchOriginal.png);
    });
    assert.equal(changed.readProvenance.status, "unverified");
    assert.equal(changed.readProvenance.reason, "source-changed");
    assert.equal(runtime.get(changed.logicalId).width, 2000);
    await assertReceivedPixels(runtime, changed.logicalId, changedResized!.data);

    const other = await rgbaPng(31, 17, 6);
    const mismatched = await runRead("mismatch", mismatchPath, other.png.toString("base64"), "image/png");
    assert.equal(mismatched.readProvenance.status, "unverified");
    assert.equal(mismatched.readProvenance.reason, "result-mismatch");
    assert.equal(runtime.get(mismatched.logicalId).width, 31);
    await assertReceivedPixels(runtime, mismatched.logicalId, other.png.toString("base64"));

    selectedTool = wrapperReadTool;
    const wrapper = await runRead("wrapper", wrapperPath, wrapperResized!.data, wrapperResized!.mimeType);
    assert.equal(wrapper.readProvenance.status, "unverified", "builtin-shaped wrapper sourceInfo cannot establish locality");
    assert.equal(wrapper.readProvenance.reason, "tool-source-unverified");
    assert.equal(runtime.get(wrapper.logicalId).width, 2000);
    assert.match(runtime.fidelityNotice(wrapper.logicalId), /not the standard builtin local reader/u);
    await assertReceivedPixels(runtime, wrapper.logicalId, wrapperResized!.data);

    selectedTool = builtinReadTool;
    const missing = await runRead("missing", resolve(fixture, "never-present.png"), other.png.toString("base64"), "image/png");
    assert.equal(missing.readProvenance.status, "unverified");
    assert.equal(missing.readProvenance.reason, "source-missing");
    assert.match(runtime.fidelityNotice(missing.logicalId), /source is missing/u);
    await assertReceivedPixels(runtime, missing.logicalId, other.png.toString("base64"));

    await emit(fake.handlers, "session_shutdown", {}, context);
  } finally {
    removeBridge();
    await terminal.clear(true).catch(() => undefined);
    copy.cleanup();
    rmSync(fixture, { recursive: true, force: true });
  }
});
