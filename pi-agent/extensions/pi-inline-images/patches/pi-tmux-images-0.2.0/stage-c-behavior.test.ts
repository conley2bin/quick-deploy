import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { installGraphicsBridge } from "../../src/bridge.ts";
import { TerminalImages } from "../../src/terminal.ts";
import type { TransportSink } from "../../src/transport.ts";
import type { ViewerState } from "../../src/viewers.ts";
import { installedPiRoot } from "../../test/pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/apply-stage-c-disposable.sh");
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
function image(hash: string, png = Buffer.from(`png:${hash}`)) {
  return { source: hash, hash, width: 8, height: 6, png };
}
function uploadCount(sink: Sink): number {
  return sink.writes.filter((value) => value.includes(Buffer.from("a=t,f=100"))).length;
}
function deleteCount(sink: Sink): number {
  return sink.writes.filter((value) => value.includes(Buffer.from("a=d,d=I"))).length;
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
  execFileSync(replay, [root], { stdio: "pipe" });
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
    for (let index = 0; index < 20; index++) {
      const data = Buffer.from(`tool-image-${index}`).toString("base64");
      const message = { role: "toolResult", toolCallId: `call-${index}`, content: [{ type: "image", mimeType: "image/png", data }] };
      await emit(fake.handlers, "message_end", { message }, context);
      branch.push({ type: "message", message });
    }

    const previews = branch.filter((entry) => entry.type === "custom" && entry.customType === ENTRY_TYPE);
    assert.equal(previews.length, 20, "admission continues after the sixteenth preview");
    assert.equal(terminal.count("read"), 16);
    assert.equal(terminal.count("inline"), 1);
    assert.equal(uploadCount(sink) - baselineUploads, 20);
    assert.equal(deleteCount(sink), 4, "each live eviction deletes the oldest backend resource");
    assert.ok(resourceTransitions >= 24, "bridge-only reads participate in backend monitor lifecycle callbacks");

    const renderer = fake.renderers.get(ENTRY_TYPE)!;
    const oldest = previews[0]!.data as Record<string, unknown>;
    const newest = previews.at(-1)!.data as Record<string, unknown>;
    const expired = renderer({ data: { ...oldest, path: `/very/${"目录".repeat(45)}/old-image.png` } }, {}, {}).render(16);
    assert.match(expired.join(" "), /Expired/u);
    assert.ok(expired.length > 1);
    assert.ok(expired.every((line) => visibleWidth(line) <= 16), "CJK expired notices fit width 16");
    const expiredWide = renderer({ data: { ...oldest, path: `/very/${"目录".repeat(45)}/old-image.png` } }, {}, {}).render(40);
    assert.ok(expiredWide.every((line) => visibleWidth(line) <= 40), "long expired notices fit width 40");
    assert.ok(renderer({ data: newest }, {}, {}).render(20).length > 0, "latest preview renders a shared placement grid");

    const beforeRestoreUploads = uploadCount(sink);
    await emit(fake.handlers, "session_tree", {}, context);
    assert.equal(terminal.count("read"), 16);
    assert.equal(uploadCount(sink) - beforeRestoreUploads, 16, "restore re-prepares the newest read resources through the bridge");

    await fake.commands.get("image")!.handler("clear", context);
    assert.equal(terminal.count("read"), 0);
    assert.equal(terminal.count("inline"), 1, "old clear cannot erase inline resources");
    assert.ok(terminal.render("inline:fixed", 20).length > 0);

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

test("late bridge binding prepares retained old-read images without any legacy graphics writer", async () => {
  const copy = disposablePackage();
  const sink = new Sink();
  let nextId = 500;
  const terminal = new TerminalImages(() => nextId++, () => ({ widthPx: 8, heightPx: 16 }), sink, { TERM_PROGRAM: "ghostty" }, true, {
    transportLimits: { minIntervalMs: 0, wireRateBytesPerSecond: 1_000_000_000 },
  });
  terminal.setViewerManaged(true);
  await terminal.setViewer(viewer());
  const bus = new Bus();
  try {
    const runtimeModule = await import(`${pathToFileURL(resolve(copy.root, "src/runtime.ts")).href}?late=${Date.now()}`);
    const runtime = new runtimeModule.PreviewRuntime({ loader: loadedFromPath, byteLoader: loadedFromBytes });
    const data = Buffer.from("late-image").toString("base64");
    const entry = await runtime.addBytes(data, "image/png", "late-read", "attached image");
    assert.equal(sink.writes.length, 0, "old runtime has no independent graphics fallback");
    assert.match(runtime.sharedFailure(entry.logicalId), /bridge unavailable/u);

    let readHandle: unknown;
    bus.on("pi-inline-images:graphics-owner:reply", (value) => { readHandle = (value as { handle?: unknown }).handle; });
    const removeBridge = installGraphicsBridge(bus as never, terminal);
    bus.emit("pi-inline-images:graphics-owner:request", { version: 1, owner: "read", requestId: "late" });
    await runtime.setShared(readHandle as never);
    assert.equal(terminal.count("read"), 1);
    assert.equal(uploadCount(sink), 1);
    assert.ok(runtime.emitPlaceholder(entry.logicalId, 20).length > 0);
    removeBridge();
    await runtime.clear();
  } finally {
    await terminal.clear(true).catch(() => undefined);
    copy.cleanup();
  }
});
