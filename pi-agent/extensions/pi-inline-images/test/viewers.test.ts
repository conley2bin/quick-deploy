import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { IMAGE_BRIDGE_REPLY, IMAGE_BRIDGE_REQUEST, IMAGE_BRIDGE_VERSION, installGraphicsBridge, type GraphicsOwnerHandle } from "../src/bridge.ts";
import { TerminalImages } from "../src/terminal.ts";
import type { TransportScheduler, TransportSink } from "../src/transport.ts";
import { currentViewerState, MAX_TMUX_CLIENTS, MAX_TMUX_OUTPUT_BYTES, TMUX_SNAPSHOT_TIMEOUT_MS, type TmuxSnapshotRun, type ViewerState } from "../src/viewers.ts";

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
function viewer(epoch: string, ready = true, reason = ""): ViewerState {
  return { epoch, ready, reason, attached: [epoch], receivers: ready ? [epoch] : [] };
}
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
  await terminal.setViewer({ ready: false, epoch: "snapshot-error", reason: "tmux client snapshot failed", attached: undefined, receivers: [] });
  await settle(terminal.setViewer(viewer("viewer-a")), clock);
  assert.equal(sink.writes.length, 1, "a failed snapshot cannot prune a served viewer identity");
  await terminal.setViewer(viewer("viewer-a", false, "no client is viewing this window"));await settle(terminal.setViewer(viewer("viewer-a")), clock);
  assert.equal(sink.writes.length, 1, "same attached viewer hide/show retains sent state");

  await settle(terminal.setViewer({ ready: true, epoch: "viewer-a+b", reason: "", attached: ["viewer-a", "viewer-b"], receivers: ["viewer-a", "viewer-b"] }), clock);
  assert.equal(sink.writes.length, 2, "one unseen attached viewer receives a complete re-upload");
  await terminal.setViewer({ ready: false, epoch: "viewer-b-hidden", reason: "no client is viewing this window", attached: ["viewer-a", "viewer-b"], receivers: [] });
  await settle(terminal.setViewer({ ready: true, epoch: "viewer-b-only", reason: "", attached: ["viewer-a", "viewer-b"], receivers: ["viewer-b"] }), clock);
  assert.equal(sink.writes.length, 2, "a served attached viewer changing windows is not resent pixels");
  await settle(terminal.setViewer({ ready: true, epoch: "viewer-b-reconnected", reason: "", attached: ["viewer-a", "viewer-b-new"], receivers: ["viewer-b-new"] }), clock);
  assert.equal(sink.writes.length, 3, "a reconnected identity receives pixels");
  await terminal.setViewer(viewer("mixed", false, "an incompatible client is viewing this window"));await terminal.prepare("two", image("second"));
  assert.equal(sink.writes.length, 3, "incompatible visible viewer remains pending with zero PNG traffic");
  await settle(terminal.clear(true), clock);
});

test("tmux snapshots treat inactive unzoomed panes as visible and enforce zoom, suspension, shape, and client bounds", () => {
  type Fixture = { policy?: string; pane?: string; clients?: string; fail?: string };
  const run = (fixture: Fixture): TmuxSnapshotRun => (args, options) => {
    assert.equal(options.timeout, TMUX_SNAPSHOT_TIMEOUT_MS);
    assert.equal(options.maxBuffer, MAX_TMUX_OUTPUT_BYTES);
    const command = args[0];
    if (command === fixture.fail) return { status: null, error: new Error("snapshot failed"), stdout: "" };
    if (command === "show-options") return { status: 0, stdout: fixture.policy ?? "on\n" };
    if (command === "display-message") return { status: 0, stdout: fixture.pane ?? "@7\t0\t0\n" };
    if (command === "list-clients") return { status: 0, stdout: fixture.clients ?? "41\t1700000000\t/dev/pts/9\txterm-ghostty\tmain\t@7\t0\tfocused\n" };
    throw new Error(`unexpected command ${command}`);
  };
  const env = { TMUX: "/tmp/tmux", TMUX_PANE: "%3", TERM: "tmux-256color" };

  const unzoomed = currentViewerState(env, run({}));
  assert.equal(unzoomed.ready, true, "inactive pane is visible when the window is not zoomed");
  assert.equal(unzoomed.receivers.length, 1);
  assert.deepEqual(unzoomed.attached, unzoomed.receivers);

  const zoomHidden = currentViewerState(env, run({ pane: "@7\t0\t1\n" }));
  assert.equal(zoomHidden.ready, false);
  assert.match(zoomHidden.reason, /hidden by the zoomed window/u);
  assert.equal(zoomHidden.attached?.length, 1);

  const zoomActive = currentViewerState(env, run({ pane: "@7\t1\t1\n" }));
  assert.equal(zoomActive.ready, true);

  const otherWindow = currentViewerState(env, run({ clients: "41\t1700000000\t/dev/pts/9\txterm-ghostty\tmain\t@8\t0\tfocused\n" }));
  assert.equal(otherWindow.ready, false);
  assert.match(otherWindow.reason, /no client is viewing/u);
  assert.equal(otherWindow.attached?.length, 1, "attachment identity survives a window switch");

  const suspended = currentViewerState(env, run({ clients: "41\t1700000000\t/dev/pts/9\txterm-ghostty\tmain\t@7\t0\tfocused,suspended\n" }));
  assert.equal(suspended.ready, false);
  assert.match(suspended.reason, /suspended/u);

  const withControlClient = currentViewerState(env, run({ clients: [
    "40\t1699999999\t\ttmux-256color\tmain\t@7\t1\tattached,control-mode",
    "41\t1700000000\t/dev/pts/9\txterm-ghostty\tmain\t@7\t0\tfocused",
  ].join("\n") + "\n" }));
  assert.equal(withControlClient.ready, true, "control clients without a tty are not terminal viewers");
  assert.equal(withControlClient.attached?.length, 1);

  const mixed = currentViewerState(env, run({ clients: [
    "41\t1700000000\t/dev/pts/9\txterm-ghostty\tmain\t@7\t0\tfocused",
    "42\t1700000001\t/dev/pts/10\txterm-256color\tmain\t@7\t0\tfocused",
  ].join("\n") + "\n" }));
  assert.equal(mixed.ready, false);
  assert.match(mixed.reason, /incompatible/u);

  const tooMany = Array.from({ length: MAX_TMUX_CLIENTS + 1 }, (_, index) =>
    `${100 + index}\t${1700000000 + index}\t/dev/pts/${index}\txterm-ghostty\tmain\t@7\t0\tfocused`).join("\n") + "\n";
  const bounded = currentViewerState(env, run({ clients: tooMany }));
  assert.equal(bounded.ready, false);
  assert.match(bounded.reason, /exceeds 32/u);
  assert.equal(bounded.attached, undefined);

  const malformed = currentViewerState(env, run({ clients: "missing\tfields\n" }));
  assert.equal(malformed.ready, false);
  assert.match(malformed.reason, /malformed/u);
  assert.equal(malformed.attached, undefined);

  const failed = currentViewerState(env, run({ fail: "list-clients" }));
  assert.equal(failed.ready, false);
  assert.match(failed.reason, /snapshot failed/u);
  assert.equal(failed.attached, undefined, "failed snapshots cannot prune served identities");
});

test("read-only bridge resources remain pending while hidden and drive lifecycle callbacks", async () => {
  const { clock, sink, terminal } = runtime();
  const bus = new Bus();
  let changes = 0;
  const stop = installGraphicsBridge(bus as never, terminal, () => { changes++; });
  let read: GraphicsOwnerHandle | undefined;
  bus.on(IMAGE_BRIDGE_REPLY, (value) => { read = (value as { handle?: GraphicsOwnerHandle }).handle; });
  bus.emit(IMAGE_BRIDGE_REQUEST, { version: IMAGE_BRIDGE_VERSION, owner: "read", requestId: "hidden-read" });
  await read!.prepare("preview", image("hidden-read"));
  assert.equal(sink.writes.length, 0);
  assert.match(read!.failure("preview") ?? "", /waiting for a compatible visible viewer/u);
  assert.equal(changes, 1, "read admission notifies the shared monitor lifecycle");
  await settle(terminal.setViewer(viewer("viewer-a")), clock);
  assert.equal(sink.writes.length, 1);
  await settle(read!.reset(), clock);
  assert.equal(changes, 2, "read reset notifies the shared monitor lifecycle");
  stop();
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
