import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { IMAGE_BRIDGE_REPLY, IMAGE_BRIDGE_REQUEST, IMAGE_BRIDGE_VERSION, installGraphicsBridge, type GraphicsOwnerHandle } from "../src/bridge.ts";
import { TerminalImages } from "../src/terminal.ts";
import type { TransportScheduler, TransportSink } from "../src/transport.ts";
import type { ViewerState } from "../src/viewers.ts";

class Clock implements TransportScheduler {
  private time = 0;
  private id = 0;
  private timers = new Map<number, { at: number; callback: () => void }>();
  now(): number { return this.time; }
  setTimeout(callback: () => void, delayMs: number): number { const id = ++this.id; this.timers.set(id, { at: this.time + delayMs, callback }); return id; }
  clearTimeout(handle: unknown): void { this.timers.delete(handle as number); }
  advance(milliseconds: number): void {
    const target = this.time + milliseconds;
    while (true) {
      const next = [...this.timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      this.time = next[1].at; this.timers.delete(next[0]); next[1].callback();
    }
    this.time = target;
  }
  get count(): number { return this.timers.size; }
}

class Sink extends EventEmitter implements TransportSink {
  readonly writes: Buffer[] = [];
  write(value: Buffer): boolean { this.writes.push(Buffer.from(value)); return true; }
}

class Bus {
  private readonly handlers = new Map<string, Set<(data: unknown) => void>>();
  emit(channel: string, data: unknown): void { for (const handler of this.handlers.get(channel) ?? []) handler(data); }
  on(channel: string, handler: (data: unknown) => void): () => void {
    const handlers = this.handlers.get(channel) ?? new Set(); handlers.add(handler); this.handlers.set(channel, handlers);
    return () => handlers.delete(handler);
  }
}

function image(hash = "fixture") { return { source: hash, hash, width: 12, height: 8, png: Buffer.from(`png:${hash}`) }; }
function viewer(epoch: string, ready = true, reason = ""): ViewerState { return { epoch, ready, reason }; }
async function settle<T>(promise: Promise<T>, clock: Clock): Promise<T> {
  let done: { value?: T; error?: unknown; settled: boolean } = { settled: false };
  void promise.then((value) => { done = { value, settled: true }; }, (error) => { done = { error, settled: true }; });
  for (let turn = 0; turn < 20 && !done.settled; turn++) { await Promise.resolve(); if (clock.count) clock.advance(1_000); }
  if (done.error) throw done.error;
  if (!done.settled) throw new Error("viewer operation did not settle");
  return done.value as T;
}

function runtime() {
  const clock = new Clock();
  const sink = new Sink();
  let id = 0x07123455;
  const terminal = new TerminalImages(() => ++id, () => ({ widthPx: 10, heightPx: 20 }), sink, { TERM_PROGRAM: "ghostty" }, true, { scheduler: clock, transportLimits: { minIntervalMs: 0 } });
  terminal.setViewerManaged(true);
  return { clock, sink, terminal };
}

test("viewer-aware preparation is pending until a compatible visible receiver and resends only on a new epoch", async () => {
  const { clock, sink, terminal } = runtime();
  await terminal.prepare("one", image());
  assert.equal(sink.writes.length, 0, "hidden-first image writes no PNG");
  assert.match(terminal.failure("one") ?? "", /waiting for a compatible visible viewer/u);

  await settle(terminal.setViewer(viewer("viewer-a")), clock);
  assert.equal(sink.writes.length, 1);
  assert.equal(terminal.failure("one"), undefined);
  await terminal.setViewer(viewer("viewer-a", false, "no client is viewing this window"));await settle(terminal.setViewer(viewer("viewer-a")), clock);
  assert.equal(sink.writes.length, 1, "same attached viewer hide/show retains sent state");

  await settle(terminal.setViewer(viewer("viewer-b")), clock);
  assert.equal(sink.writes.length, 2, "new attached viewer receives a complete re-upload");
  await terminal.setViewer(viewer("mixed", false, "an incompatible client is viewing this window"));await terminal.prepare("two", image("second"));
  assert.equal(sink.writes.length, 2, "incompatible visible viewer remains pending with zero PNG traffic");
  await settle(terminal.clear(true), clock);
});

test("versioned bridge returns only the inline and read owner handles backed by one terminal", async () => {
  const { clock, sink, terminal } = runtime();
  await terminal.setViewer(viewer("viewer-a"));
  const bus = new Bus();
  const stop = installGraphicsBridge(bus as never, terminal);
  let reply: { handle?: GraphicsOwnerHandle } | undefined;
  const unlisten = bus.on(IMAGE_BRIDGE_REPLY, (value) => { reply = value as { handle?: GraphicsOwnerHandle }; });
  bus.emit(IMAGE_BRIDGE_REQUEST, { version: IMAGE_BRIDGE_VERSION, owner: "read", requestId: "fixture" });
  assert.equal(reply?.handle?.owner, "read");
  await settle(reply!.handle!.prepare("preview", image("read")), clock);
  assert.equal(reply!.handle!.render("preview", 20).length > 0, true);
  assert.equal(sink.writes.length, 1, "read handle uses the inline terminal transport");
  unlisten(); stop();
  await settle(terminal.clear(true), clock);
});
