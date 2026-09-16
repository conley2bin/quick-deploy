import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import sharp from "sharp";
import { installGraphicsBridge } from "../../src/bridge.ts";
import { TerminalImages } from "../../src/terminal.ts";
import type { TransportSink } from "../../src/transport.ts";
import type { ViewerState } from "../../src/viewers.ts";
import { installedPiRoot } from "../../test/pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");
const ENTRY_TYPE = "pi-tmux-images.preview";

type Handler = (event: unknown, context: unknown) => unknown;
type Renderer = (entry: { data: unknown }, options: unknown, theme: unknown) => { render(width: number): string[] };

class Bus {
  private readonly handlers = new Map<string, Set<(data: unknown) => void>>();
  emit(channel: string, data: unknown): void { for (const handler of this.handlers.get(channel) ?? []) handler(data); }
  on(channel: string, handler: (data: unknown) => void): () => void {
    const handlers = this.handlers.get(channel) ?? new Set();
    handlers.add(handler);
    this.handlers.set(channel, handlers);
    return () => handlers.delete(handler);
  }
}

class Sink extends EventEmitter implements TransportSink {
  readonly writes: Buffer[] = [];
  write(value: Buffer): boolean { this.writes.push(Buffer.from(value)); return true; }
}

function viewer(identity = "viewer-a"): ViewerState {
  return { ready: true, epoch: identity, reason: "", attached: [identity], receivers: [identity] };
}
function coordinate(bus: Bus, logicalIds: string[], ready = true): void {
  bus.emit("pi-inline-images:read-preview-coordination", { version: 1, ready, activeLogicalIds: logicalIds, ...(!ready && { reason: "fixture mismatch" }) });
}
function image(hash: string, png = Buffer.from(`png:${hash}`)) {
  return { source: hash, hash, width: 8, height: 6, png };
}
function uploadCount(sink: Sink): number {
  return sink.writes.filter((value) => value.includes(Buffer.from("a=t,f=100"))).length;
}
function deleteCount(sink: Sink): number {
  return sink.writes.filter((value) => value.includes(Buffer.from("a=d,d=I"))).length;
}
function uploadedPngs(sink: Sink): Buffer[] {
  const uploads: Buffer[] = [];
  let chunks: string[] | undefined;
  for (const write of sink.writes) {
    const value = write.toString("ascii");
    for (const match of value.matchAll(/\x1b_G([^;]*);([A-Za-z0-9+/=]*)\x1b\\/gu)) {
      const controls = new Map((match[1] ?? "").split(",").filter(Boolean).map((field) => {
        const separator = field.indexOf("=");
        return [field.slice(0, separator), field.slice(separator + 1)];
      }));
      if (controls.get("a") === "t") chunks = [];
      if (!chunks) continue;
      chunks.push(match[2] ?? "");
      if (controls.get("m") === "0") {
        uploads.push(Buffer.from(chunks.join(""), "base64"));
        chunks = undefined;
      }
    }
  }
  assert.equal(chunks, undefined, "captured upload ends with m=0");
  return uploads;
}
function visibleWidth(value: string): number {
  const plain = value.replace(/\x1b\[[0-?]*[ -/]*[@-~]/gu, "");
  let width = 0;
  for (const character of plain) {
    const code = character.codePointAt(0)!;
    if (/\p{Mark}/u.test(character)) continue;
    width += code >= 0x1100 && (code <= 0x115f || code === 0x2329 || code === 0x232a
      || (code >= 0x2e80 && code <= 0xa4cf) || (code >= 0xac00 && code <= 0xd7a3)
      || (code >= 0xf900 && code <= 0xfaff) || (code >= 0xfe10 && code <= 0xfe6f)
      || (code >= 0xff00 && code <= 0xff60) || (code >= 0x1f300 && code <= 0x1faff)) ? 2 : 1;
  }
  return width;
}

function disposablePackage(): { root: string; cleanup(): void } {
  const root = mkdtempSync(resolve(tmpdir(), "pi-tmux-images-behavior-"));
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

function fakeApi(bus: Bus, branch: Array<Record<string, unknown>>) {
  const handlers = new Map<string, Handler[]>();
  const renderers = new Map<string, Renderer>();
  const commands = new Map<string, { handler(args: string, context: unknown): Promise<void> }>();
  const api = {
    events: bus,
    getAllTools: () => [{ name: "read", description: "read", parameters: {}, promptGuidelines: [], sourceInfo: { path: "<builtin:read>", source: "builtin", scope: "temporary", origin: "top-level" } }],
    on(name: string, handler: Handler) { const list = handlers.get(name) ?? []; list.push(handler); handlers.set(name, list); },
    registerEntryRenderer(type: string, renderer: Renderer) { renderers.set(type, renderer); },
    registerCommand(name: string, command: { handler(args: string, context: unknown): Promise<void> }) { commands.set(name, command); },
    appendEntry(type: string, data: unknown) { branch.push({ type: "custom", customType: type, data }); },
  };
  return { api, handlers, renderers, commands };
}

async function emit(handlers: Map<string, Handler[]>, name: string, event: unknown, context: unknown): Promise<void> {
  for (const handler of handlers.get(name) ?? []) await handler(event, context);
}

function loadedFromBytes(data: string, _mime: string, path: string) {
  const bytes = Buffer.from(data, "base64");
  return Promise.resolve({
    path,
    hash: createHash("sha256").update(bytes).digest("hex"),
    originalMime: "image/png" as const,
    width: 8,
    height: 6,
    png: Buffer.from(`decoded:${bytes.toString("hex")}`),
  });
}

function loadedFromPath(path: string) {
  const bytes = Buffer.from(path);
  return Promise.resolve({
    path,
    hash: createHash("sha256").update(bytes).digest("hex"),
    originalMime: "image/png" as const,
    width: 8,
    height: 6,
    png: Buffer.from(`decoded-path:${path}`),
  });
}

test("old read preserves byte-exact 8/16-bit PNGs through the shared terminal wire", async () => {
  const copy = disposablePackage();
  const sink = new Sink();
  let nextId = 20;
  const terminal = new TerminalImages(() => nextId++, () => ({ widthPx: 8, heightPx: 16 }), sink, { TERM_PROGRAM: "ghostty" }, true, {
    transportLimits: { minIntervalMs: 0, wireRateBytesPerSecond: 1_000_000_000 },
  });
  terminal.setViewerManaged(true);
  await terminal.setViewer(viewer());
  const bus = new Bus();
  const removeBridge = installGraphicsBridge(bus as never, terminal);
  let readHandle: unknown;
  bus.on("pi-inline-images:graphics-owner:reply", (value) => { readHandle = (value as { handle?: unknown }).handle; });
  bus.emit("pi-inline-images:graphics-owner:request", { version: 1, owner: "read", requestId: "fidelity" });
  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?fidelity=${Date.now()}`);
    const runtime = new runtimeModule.PreviewRuntime();
    await runtime.setShared(readHandle as never);
    const width = 7, height = 5, channels = 4;
    const raw16 = new Uint16Array(width * height * channels);
    for (let index = 0; index < raw16.length; index++) raw16[index] = (index * 1954) & 0xffff;
    const png16 = await sharp(raw16, { raw: { width, height, channels } }).toColourspace("rgb16").png().toBuffer();
    const png8 = await sharp(Buffer.alloc(12 * 6 * 4, 127), { raw: { width: 12, height: 6, channels: 4 } })
      .png({ compressionLevel: 0 }).toBuffer();

    await runtime.addBytes(png16.toString("base64"), "image/png", "png-16");
    await runtime.addBytes(png8.toString("base64"), "image/png", "png-8");
    assert.deepEqual(runtime.get("png-16")!.png, png16);
    assert.deepEqual(runtime.get("png-8")!.png, png8);
    const uploads = uploadedPngs(sink);
    assert.deepEqual(uploads, [png16, png8], "shared owner uploads the exact old-read PNG bytes");
    assert.equal((await sharp(uploads[0]!).metadata()).depth, "ushort");
    await runtime.clear();
  } finally {
    removeBridge();
    await terminal.clear(true).catch(() => undefined);
    copy.cleanup();
  }
});

test("automatic corrupt read persists a wrapped failure without mutating the raw result", async () => {
  const copy = disposablePackage();
  const bus = new Bus();
  const branch: Array<Record<string, unknown>> = [];
  const fake = fakeApi(bus, branch);
  const context = { cwd: "/fixture", sessionManager: { getBranch: () => branch }, ui: { notify() { throw new Error("automatic failures belong in transcript notices"); } } };
  try {
    const extensionModule = await import(`${pathToFileURL(resolve(copy.root, "extensions/index.ts")).href}?automatic-error=${Date.now()}`);
    extensionModule.registerInlineImages(fake.api);
    const source = await sharp(Buffer.alloc(12 * 6 * 4, 127), { raw: { width: 12, height: 6, channels: 4 } })
      .png({ compressionLevel: 0 }).toBuffer();
    const truncated = source.subarray(0, Math.floor(source.length / 2));
    const message = { role: "toolResult", toolCallId: "corrupt-read", toolName: "read", content: [{ type: "image", mimeType: "image/png", data: truncated.toString("base64") }] };
    const raw = JSON.stringify(message);
    await emit(fake.handlers, "message_end", { message }, context);
    assert.equal(JSON.stringify(message), raw);
    const custom = branch.find((entry) => entry.type === "custom" && entry.customType === ENTRY_TYPE);
    assert.ok(custom, "failed automatic preview persists an ownership/error entry");
    const renderer = fake.renderers.get(ENTRY_TYPE)!;
    for (const width of [16, 40]) {
      const lines = renderer({ data: custom!.data }, {}, {}).render(width);
      assert.match(lines.join(" ").replace(/\s+/gu, " "), /Automatic preview failed: Invalid image content/u);
      assert.ok(lines.every((line) => visibleWidth(line) <= width));
    }
    await emit(fake.handlers, "session_shutdown", {}, context);
  } finally {
    copy.cleanup();
  }
});

test("restore resolves and decodes newest entries incrementally within the resident budget", async () => {
  const copy = disposablePackage();
  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?incremental=${Date.now()}`);
    const order: string[] = [];
    let runtime: InstanceType<typeof runtimeModule.PreviewRuntime>;
    runtime = new runtimeModule.PreviewRuntime({
      maxResidentPngBytes: 24,
      byteLoader: async (data: string, _mime: string, path: string) => {
        order.push(`decode:${data}:resident:${runtime.residentBytes()}`);
        return { path, hash: "a".repeat(64), originalMime: "image/png" as const, width: 8, height: 6, png: Buffer.alloc(12, Number(data)) };
      },
    });
    const entries = Array.from({ length: 4 }, (_, index) => ({
      path: "attached image",
      hash: "a".repeat(64),
      originalMime: "image/png" as const,
      width: 8,
      height: 6,
      logicalId: `incremental-image-${index}`,
      origin: { messageOrdinal: index, key: `tool:${index}`, blockIndex: 0, mimeType: "image/png", contentHash: "b".repeat(64) },
    }));
    const status = await runtime.rehydrate(entries, "/fixture", async (entry: { logicalId: string }) => {
      const index = entry.logicalId.at(-1)!;
      order.push(`resolve:${index}`);
      return { data: index, mimeType: "image/png", expectedHash: "a".repeat(64) };
    });
    assert.deepEqual(order, [
      "resolve:3", "decode:3:resident:0",
      "resolve:2", "decode:2:resident:12",
      "resolve:1", "decode:1:resident:24",
      "resolve:0", "decode:0:resident:24",
    ]);
    assert.equal(runtime.residentBytes(), 24);
    assert.ok(runtime.get("incremental-image-3"));
    assert.ok(runtime.get("incremental-image-2"));
    assert.match(status.get("incremental-image-1") ?? "", /byte cache/u);
    assert.match(status.get("incremental-image-0") ?? "", /byte cache/u);
    await runtime.clear();
  } finally {
    copy.cleanup();
  }
});

test("patched old extension sustains 20 previews through the real shared backend, restores, wraps, and clears only read", async () => {
  const copy = disposablePackage();
  const sink = new Sink();
  let nextId = 100;
  const terminal = new TerminalImages(() => nextId++, () => ({ widthPx: 8, heightPx: 16 }), sink, { TERM_PROGRAM: "ghostty" }, true, {
    transportLimits: { minIntervalMs: 0, wireRateBytesPerSecond: 1_000_000_000 },
  });
  terminal.setViewerManaged(true);
  await terminal.setViewer(viewer());
  const bus = new Bus();
  let resourceTransitions = 0;
  const removeBridge = installGraphicsBridge(bus as never, terminal, () => { resourceTransitions++; });
  const branch: Array<Record<string, unknown>> = [];
  const fake = fakeApi(bus, branch);
  const context = { cwd: "/fixture", sessionManager: { getBranch: () => branch }, ui: { notify() {} } };

  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?behavior=${Date.now()}`);
    const extensionModule = await import(`${pathToFileURL(resolve(copy.root, "extensions/index.ts")).href}?behavior=${Date.now()}`);
    const runtime = new runtimeModule.PreviewRuntime({ loader: loadedFromPath, byteLoader: loadedFromBytes });
    extensionModule.registerInlineImages(fake.api, runtime);
    await emit(fake.handlers, "session_start", {}, context);

    await terminal.prepare("inline:fixed", image("inline-fixed"));
    const baselineUploads = uploadCount(sink);
    const messages: Array<Record<string, unknown>> = [];
    for (let index = 0; index < 20; index++) {
      const data = Buffer.from(`tool-image-${index}`).toString("base64");
      const message = { role: "toolResult", toolCallId: `call-${index}`, content: [{ type: "image", mimeType: "image/png", data }] };
      await emit(fake.handlers, "message_end", { message }, context);
      branch.push({ type: "message", message });
      messages.push(message);
    }
    const beforeDuplicateUploads = uploadCount(sink);
    await emit(fake.handlers, "message_end", { message: messages.at(-1) }, context);

    const previews = branch.filter((entry) => entry.type === "custom" && entry.customType === ENTRY_TYPE);
    assert.equal(previews.length, 20, "admission continues after the sixteenth preview without duplicating a repeated tool identity");
    assert.equal(uploadCount(sink), beforeDuplicateUploads, "repeated message_end does not re-prepare a persisted tool block");
    assert.equal(terminal.count("read"), 16);
    assert.equal(terminal.count("inline"), 1);
    assert.equal(uploadCount(sink) - baselineUploads, 20);
    assert.equal(deleteCount(sink), 4, "each live eviction deletes the oldest backend resource");
    assert.ok(resourceTransitions >= 24, "bridge-only reads participate in backend monitor lifecycle callbacks");

    const renderer = fake.renderers.get(ENTRY_TYPE)!;
    const oldest = previews[0]!.data as Record<string, unknown>;
    const newest = previews.at(-1)!.data as Record<string, unknown>;
    assert.match(renderer({ data: newest }, {}, {}).render(40).join(" "), /coordination unavailable/u);
    coordinate(bus, previews.slice(-16).map((entry) => String((entry.data as { logicalId: unknown }).logicalId)));
    const expired = renderer({ data: { ...oldest, path: `/very/${"目录".repeat(45)}/old-image.png` } }, {}, {}).render(16);
    assert.match(expired.join(" "), /Expired/u);
    assert.ok(expired.length > 1);
    assert.ok(expired.every((line) => visibleWidth(line) <= 16), "CJK expired notices fit width 16");
    const expiredWide = renderer({ data: { ...oldest, path: `/very/${"目录".repeat(45)}/old-image.png` } }, {}, {}).render(40);
    assert.ok(expiredWide.every((line) => visibleWidth(line) <= 40), "long expired notices fit width 40");
    assert.ok(renderer({ data: newest }, {}, {}).render(20).length > 0, "latest preview renders a shared placement grid");

    const rawMessages = JSON.stringify(messages);
    const beforeRestoreUploads = uploadCount(sink);
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(terminal.count("read"), 16);
    assert.equal(uploadCount(sink) - beforeRestoreUploads, 16, "restore re-prepares the newest read resources through the bridge");
    assert.equal(JSON.stringify(messages), rawMessages, "reload/restore never rewrites raw tool messages");

    await fake.commands.get("image")!.handler("clear", context);
    assert.equal(terminal.count("read"), 0);
    assert.equal(terminal.count("inline"), 1, "old clear cannot erase inline resources");
    assert.ok(terminal.render("inline:fixed", 20).length > 0);
    assert.equal(JSON.stringify(messages), rawMessages, "clear leaves tool/model content byte-identical");

    const noticeComponents = [
      renderer({ data: { ...newest, path: `/missing/${"x".repeat(91)}-${"目录".repeat(20)}.png` } }, {}, {}),
      renderer({ data: { invalid: true } }, {}, {}),
      fake.renderers.get("pi-tmux-images.clear")!({ data: { marker: true } }, {}, {}),
    ];
    for (const width of [16, 40]) for (const component of noticeComponents) {
      assert.ok(component.render(width).every((line) => visibleWidth(line) <= width), `notice fits width ${width}`);
    }

    await emit(fake.handlers, "session_shutdown", {}, context);
    removeBridge();
    await terminal.clear(true);
    assert.equal(sink.listenerCount("drain"), 0);
    assert.equal(sink.listenerCount("error"), 0);
    assert.equal(sink.listenerCount("close"), 0);
  } finally {
    removeBridge();
    await terminal.clear(true).catch(() => undefined);
    copy.cleanup();
  }
});

test("old extension evicts by read byte budget before count saturation", async () => {
  const copy = disposablePackage();
  const sink = new Sink();
  let nextId = 400;
  const terminal = new TerminalImages(() => nextId++, () => ({ widthPx: 8, heightPx: 16 }), sink, { TERM_PROGRAM: "ghostty" }, true, {
    maxReadResidentPngBytes: 30,
    transportLimits: { minIntervalMs: 0, wireRateBytesPerSecond: 1_000_000_000 },
  });
  terminal.setViewerManaged(true);
  await terminal.setViewer(viewer());
  const bus = new Bus();
  const removeBridge = installGraphicsBridge(bus as never, terminal);
  const branch: Array<Record<string, unknown>> = [];
  const fake = fakeApi(bus, branch);
  const context = { cwd: "/fixture", sessionManager: { getBranch: () => branch }, ui: { notify() {} } };
  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?bytes=${Date.now()}`);
    const extensionModule = await import(`${pathToFileURL(resolve(copy.root, "extensions/index.ts")).href}?bytes=${Date.now()}`);
    const runtime = new runtimeModule.PreviewRuntime({
      maxResidentPngBytes: 30,
      byteLoader: async (data: string, _mime: string, path: string) => ({
        path,
        hash: createHash("sha256").update(Buffer.from(data, "base64")).digest("hex"),
        originalMime: "image/png" as const,
        width: 8,
        height: 6,
        png: Buffer.alloc(12, Buffer.from(data, "base64")[0] ?? 0),
      }),
    });
    extensionModule.registerInlineImages(fake.api, runtime);
    await emit(fake.handlers, "session_start", {}, context);
    for (let index = 0; index < 4; index++) {
      const data = Buffer.from(`byte-image-${index}`).toString("base64");
      const message = { role: "toolResult", toolCallId: `byte-${index}`, content: [{ type: "image", mimeType: "image/png", data }] };
      await emit(fake.handlers, "message_end", { message }, context);
      branch.push({ type: "message", message });
    }
    const previews = branch.filter((entry) => entry.type === "custom" && entry.customType === ENTRY_TYPE);
    assert.equal(previews.length, 4);
    assert.equal(terminal.count("read"), 2);
    assert.equal(terminal.residentBytes("read"), 24);
    assert.equal(runtime.residentBytes(), 24);
    coordinate(bus, previews.slice(-2).map((entry) => String((entry.data as { logicalId: unknown }).logicalId)));
    const renderer = fake.renderers.get(ENTRY_TYPE)!;
    assert.match(renderer({ data: previews[0]!.data }, {}, {}).render(20).join(" "), /byte cache/u);
    assert.ok(renderer({ data: previews.at(-1)!.data }, {}, {}).render(20).length > 0);
    const beforeRestore = uploadCount(sink);
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(terminal.count("read"), 2);
    assert.equal(runtime.residentBytes(), 24);
    assert.equal(uploadCount(sink) - beforeRestore, 2, "restore retains the newest byte-bounded subset");
    assert.match(renderer({ data: previews[0]!.data }, {}, {}).render(20).join(" "), /byte cache/u);
    await emit(fake.handlers, "session_shutdown", {}, context);
  } finally {
    removeBridge();
    await terminal.clear(true).catch(() => undefined);
    copy.cleanup();
  }
});

test("old extension binds after either factory order and ignores a version-mismatched bridge", async () => {
  const copy = disposablePackage();
  const sink = new Sink();
  let nextId = 500;
  const terminal = new TerminalImages(() => nextId++, () => ({ widthPx: 8, heightPx: 16 }), sink, { TERM_PROGRAM: "ghostty" }, true, {
    transportLimits: { minIntervalMs: 0, wireRateBytesPerSecond: 1_000_000_000 },
  });
  terminal.setViewerManaged(true);
  await terminal.setViewer(viewer());
  const bus = new Bus();
  const branch: Array<Record<string, unknown>> = [];
  const fake = fakeApi(bus, branch);
  const context = { cwd: "/fixture", sessionManager: { getBranch: () => branch }, ui: { notify() {} } };
  let request: Record<string, unknown> | undefined;
  bus.on("pi-inline-images:graphics-owner:request", (value) => { request = value as Record<string, unknown>; });
  let removeBridge: (() => void) | undefined;
  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?late=${Date.now()}`);
    const extensionModule = await import(`${pathToFileURL(resolve(copy.root, "extensions/index.ts")).href}?late=${Date.now()}`);
    const runtime = new runtimeModule.PreviewRuntime({ loader: loadedFromPath, byteLoader: loadedFromBytes });
    extensionModule.registerInlineImages(fake.api, runtime);
    await emit(fake.handlers, "session_start", {}, context);
    assert.ok(request, "old-first factory order emits a bridge request");

    const data = Buffer.from("late-image").toString("base64");
    const message = { role: "toolResult", toolCallId: "late", content: [{ type: "image", mimeType: "image/png", data }] };
    await emit(fake.handlers, "message_end", { message }, context);
    branch.push({ type: "message", message });
    const entry = branch.find((candidate) => candidate.type === "custom" && candidate.customType === ENTRY_TYPE)!.data as Record<string, unknown>;
    coordinate(bus, [String(entry.logicalId)]);
    assert.equal(sink.writes.length, 0, "missing bridge has no independent graphics fallback");
    assert.match(fake.renderers.get(ENTRY_TYPE)!({ data: entry }, {}, {}).render(20).join("").replace(/\s/gu, ""), /bridgeunavailable/u);

    bus.emit("pi-inline-images:graphics-owner:reply", { ...request, version: 2, handle: {} });
    assert.equal(sink.writes.length, 0, "version mismatch cannot bind an unverified handle");
    assert.match(runtime.sharedFailure(String(entry.logicalId)), /bridge unavailable/u);

    removeBridge = installGraphicsBridge(bus as never, terminal);
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(terminal.count("read"), 1);
    assert.equal(uploadCount(sink), 1);
    assert.ok(runtime.emitPlaceholder(String(entry.logicalId), 20).length > 0);
    await emit(fake.handlers, "session_shutdown", {}, context);
  } finally {
    removeBridge?.();
    await terminal.clear(true).catch(() => undefined);
    copy.cleanup();
  }
});
