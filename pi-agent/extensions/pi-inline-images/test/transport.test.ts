import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { placement } from "../vendor/pi-tmux-images/kitty-placeholder.ts";
import {
  BoundedTransport,
  completeUploadTransaction,
  DEFAULT_TRANSPORT_LIMITS,
  type TransportScheduler,
  type TransportSink,
} from "../src/transport.ts";

class FakeClock implements TransportScheduler {
  private time = 0;
  private nextId = 1;
  private timers = new Map<number, { at: number; callback: () => void }>();

  now(): number { return this.time; }
  setTimeout(callback: () => void, delayMs: number): number {
    const id = this.nextId++;
    this.timers.set(id, { at: this.time + delayMs, callback });
    return id;
  }
  clearTimeout(handle: unknown): void { this.timers.delete(handle as number); }
  advance(milliseconds: number): void {
    const target = this.time + milliseconds;
    while (true) {
      const next = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
      if (!next) break;
      this.time = next[1].at;
      this.timers.delete(next[0]);
      next[1].callback();
    }
    this.time = target;
  }
  get count(): number { return this.timers.size; }
}

class CapturedSink extends EventEmitter implements TransportSink {
  writableNeedDrain = false;
  readonly writes: Array<{ value: string; at: number }> = [];
  readonly returns: boolean[] = [];
  reflectNeedDrain = true;
  onWrite?: (value: string, accepted: boolean) => void;

  constructor(private readonly clock: FakeClock) { super(); }

  write(value: string): boolean {
    this.writes.push({ value, at: this.clock.now() });
    const accepted = this.returns.shift() ?? true;
    this.onWrite?.(value, accepted);
    if (!accepted && this.reflectNeedDrain) this.writableNeedDrain = true;
    return accepted;
  }

  drain(): void {
    this.writableNeedDrain = false;
    this.emit("drain");
  }
}

type Apc = { controls: Map<string, string>; payload: string };

function decodeApcs(transaction: string): Apc[] {
  const apcs: Apc[] = [];
  const pattern = /\x1b_G([^;]*);([\s\S]*?)\x1b\\/gu;
  for (const match of transaction.matchAll(pattern)) {
    const controls = new Map((match[1] || "").split(",").filter(Boolean).map((field) => {
      const separator = field.indexOf("=");
      assert.notEqual(separator, -1, `invalid APC control field ${field}`);
      return [field.slice(0, separator), field.slice(separator + 1)];
    }));
    apcs.push({ controls, payload: match[2] });
  }
  assert.equal(apcs.map((apc) => `\x1b_G${[...apc.controls].map(([key, value]) => `${key}=${value}`).join(",")};${apc.payload}\x1b\\`).join(""), transaction);
  return apcs;
}

function transport(
  overrides: ConstructorParameters<typeof BoundedTransport>[1] = {},
): { clock: FakeClock; sink: CapturedSink; owner: BoundedTransport } {
  const clock = new FakeClock();
  const sink = new CapturedSink(clock);
  const owner = new BoundedTransport(sink, overrides, clock);
  return { clock, sink, owner };
}

test("a multipart Kitty upload is one captured write with legal APC continuation structure", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 25 });
  const sourceBytes = Buffer.alloc(7_000, 0xa5);
  const source = sourceBytes.toString("base64");
  const upload = completeUploadTransaction(sourceBytes, 0x71123456, false);
  const put = placement(0x71123456, 12, 7, false);

  const uploadResult = owner.enqueue(owner.generation, { transaction: upload, key: "upload:sha256" });
  const placementResult = owner.enqueue(owner.generation, { transaction: put, coalesceKey: "placement:71123456" });
  assert.equal(sink.writes.length, 1, "all upload chunks enter the sink in one call");

  const apcs = decodeApcs(sink.writes[0]!.value);
  assert.ok(apcs.length >= 3);
  assert.deepEqual(Object.fromEntries(apcs[0]!.controls), {
    a: "t", f: "100", i: String(0x71123456), q: "2", m: "1",
  });
  for (const continuation of apcs.slice(1)) {
    assert.deepEqual([...continuation.controls.keys()], ["m"], "continuations contain only the permitted m key");
  }
  assert.equal(apcs.at(-1)!.controls.get("m"), "0");
  assert.ok(apcs.every((apc) => apc.payload.length <= 4096));
  assert.equal(apcs.slice(0, -1).every((apc) => apc.payload.length % 4 === 0), true);
  assert.equal(apcs.map((apc) => apc.payload).join(""), source);

  clock.advance(24);
  assert.equal(sink.writes.length, 1);
  clock.advance(1);
  assert.deepEqual(sink.writes.map(({ value }) => value), [upload, put], "placement follows the complete upload transaction");
  assert.deepEqual(await uploadResult, { status: "accepted", generation: 0, bytes: Buffer.byteLength(upload) });
  assert.equal((await placementResult).status, "accepted");
  owner.dispose();
});

test("write(false) accepts the current transaction and blocks every later write until drain and pacing", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 50, drainTimeoutMs: 500 });
  sink.returns.push(false, true);
  const first = owner.enqueue(owner.generation, { transaction: "first" });
  const second = owner.enqueue(owner.generation, { transaction: "second" });

  assert.deepEqual(await first, { status: "accepted", generation: 0, bytes: 5 });
  assert.deepEqual(sink.writes.map(({ value }) => value), ["first"]);
  clock.advance(200);
  assert.deepEqual(sink.writes.map(({ value }) => value), ["first"], "time alone cannot bypass backpressure");
  sink.drain();
  assert.deepEqual(sink.writes.map(({ value }) => value), ["first", "second"], "elapsed pacing permits the next write at drain");
  assert.equal((await second).status, "accepted");
  assert.equal(sink.listenerCount("drain"), 0);
  owner.dispose();
});

test("synchronous drain and reentrant enqueue during write cannot be lost or recursively pumped", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 0, drainTimeoutMs: 20 });
  sink.reflectNeedDrain = false;
  sink.returns.push(false, true);
  let second!: Promise<unknown>;
  sink.onWrite = (value) => {
    if (value !== "first") return;
    second = owner.enqueue(owner.generation, { transaction: "second" });
    sink.emit("drain");
    assert.deepEqual(sink.writes.map(({ value: written }) => written), ["first"], "reentrant enqueue cannot pump inside write()");
  };

  await owner.enqueue(owner.generation, { transaction: "first" });
  clock.advance(0);
  await second;
  await owner.ready(owner.generation);
  assert.deepEqual(sink.writes.map(({ value }) => value), ["first", "second"]);
  assert.equal(sink.listenerCount("drain"), 0);
  assert.equal(clock.count, 0);
  owner.dispose();
});

test("cancellation preserves internally observed false-return flow control until real drain", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 0, drainTimeoutMs: 100 });
  sink.reflectNeedDrain = false;
  sink.returns.push(false, true);
  await owner.enqueue(owner.generation, { transaction: "accepted-before-cancel" });
  owner.cancel("switch generation");
  owner.cancel("duplicate cancellation");
  assert.equal(sink.listenerCount("drain"), 1, "duplicate cancel does not duplicate or remove flow-control observation");
  const afterCancel = owner.enqueue(owner.generation, { transaction: "after-cancel" });
  assert.deepEqual(sink.writes.map(({ value }) => value), ["accepted-before-cancel"]);
  assert.equal(sink.listenerCount("drain"), 1);
  sink.emit("drain");
  await afterCancel;
  assert.deepEqual(sink.writes.map(({ value }) => value), ["accepted-before-cancel", "after-cancel"]);
  assert.equal(sink.listenerCount("drain"), 0);
  assert.equal(clock.count, 0);
  owner.dispose();
});

test("synchronous sink error and close during write reject the active job without recursive output", async () => {
  for (const event of ["error", "close"] as const) {
    const { sink, owner } = transport({ minIntervalMs: 0 });
    sink.onWrite = () => {
      if (event === "error") sink.emit("error", new Error("sync broken"));
      else sink.emit("close");
      assert.equal(sink.writes.length, 1);
    };
    await assert.rejects(
      owner.enqueue(owner.generation, { transaction: event }),
      event === "error" ? /sink error: sync broken/u : /sink closed/u,
    );
    assert.equal(owner.pendingJobs, 0);
    assert.equal(sink.listenerCount("drain"), 0);
    owner.dispose();
  }
});

test("dispose settles blocked work and removes every timer/listener before late drain", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 0, drainTimeoutMs: 100 });
  sink.returns.push(false);
  await owner.enqueue(owner.generation, { transaction: "accepted" });
  const queued = owner.enqueue(owner.generation, { transaction: "queued" });
  const queuedClosed = assert.rejects(queued, (error: unknown) => error instanceof Error && error.name === "TransportError" && (error as { code?: string }).code === "closed");
  const ready = owner.ready(owner.generation);
  const readyClosed = assert.rejects(ready, (error: unknown) => error instanceof Error && (error as { code?: string }).code === "closed");
  assert.equal(sink.listenerCount("drain"), 1);
  assert.equal(clock.count, 1);

  owner.dispose();
  owner.dispose("repeated dispose is inert");
  await Promise.all([queuedClosed, readyClosed]);
  assert.equal(sink.listenerCount("drain"), 0);
  assert.equal(sink.listenerCount("error"), 0);
  assert.equal(sink.listenerCount("close"), 0);
  assert.equal(clock.count, 0);
  const writes = sink.writes.length;
  sink.drain();
  clock.advance(1_000);
  assert.equal(sink.writes.length, writes, "late drain/timer cannot write after disposal");
  await assert.rejects(owner.enqueue(owner.generation, { transaction: "late" }), /transport disposed/u);
});

test("ready waits for accepted-false bytes to drain even when no later graphics job exists", async () => {
  const { clock, sink, owner } = transport({ drainTimeoutMs: 100 });
  sink.returns.push(false);
  await owner.enqueue(owner.generation, { transaction: "accepted" });
  let ready = false;
  const waiting = owner.ready(owner.generation).then(() => { ready = true; });
  clock.advance(99);
  await Promise.resolve();
  assert.equal(ready, false);
  sink.drain();
  await waiting;
  assert.equal(ready, true);
  assert.equal(sink.listenerCount("drain"), 0);
  owner.dispose();
});

test("documented default bounds equal the executable limits and enforce the exact maximum write", async () => {
  assert.deepEqual(DEFAULT_TRANSPORT_LIMITS, {
    maxTransactionBytes: 1 * 1024 * 1024,
    maxQueuedBytes: 8 * 1024 * 1024,
    maxQueuedJobs: 64,
    maxResources: 64,
    minIntervalMs: 50,
    drainTimeoutMs: 5_000,
  });
  const { sink, owner } = transport();
  const boundary = "x".repeat(DEFAULT_TRANSPORT_LIMITS.maxTransactionBytes);
  await owner.enqueue(owner.generation, { transaction: boundary });
  assert.equal(Buffer.byteLength(sink.writes[0]!.value), DEFAULT_TRANSPORT_LIMITS.maxTransactionBytes);
  await assert.rejects(
    owner.enqueue(owner.generation, { transaction: `${boundary}x` }),
    /transaction is 1048577 bytes; limit is 1048576/u,
  );
  owner.dispose();
});

test("maximum write bytes and transaction start rate are exact and finite", async () => {
  const { clock, sink, owner } = transport({
    maxTransactionBytes: 4,
    maxQueuedBytes: 16,
    minIntervalMs: 50,
  });
  const one = owner.enqueue(owner.generation, { transaction: "aaaa" });
  const two = owner.enqueue(owner.generation, { transaction: "bbbb" });
  const three = owner.enqueue(owner.generation, { transaction: "cccc" });
  await assert.rejects(owner.enqueue(owner.generation, { transaction: "ééé" }), /transaction is 6 bytes; limit is 4/u);
  await assert.rejects(owner.enqueue(owner.generation, { transaction: "12345" }), /transaction is 5 bytes; limit is 4/u);

  assert.deepEqual(sink.writes.map(({ at }) => at), [0]);
  clock.advance(49);
  assert.deepEqual(sink.writes.map(({ at }) => at), [0]);
  clock.advance(1);
  clock.advance(50);
  assert.deepEqual(sink.writes.map(({ at }) => at), [0, 50, 100]);
  assert.ok(sink.writes.every(({ value }) => Buffer.byteLength(value) <= 4));
  await Promise.all([one, two, three]);
  owner.dispose();
});

test("job, queued-byte, and retained-resource limits reject without disturbing admitted work", async () => {
  const a = transport({
    maxTransactionBytes: 10,
    maxQueuedBytes: 5,
    maxQueuedJobs: 2,
    maxResources: 2,
    minIntervalMs: 1,
  });
  a.sink.returns.push(false);
  await a.owner.enqueue(a.owner.generation, { transaction: "x", key: "resource-1" });
  const second = a.owner.enqueue(a.owner.generation, { transaction: "aa", key: "resource-2" });
  const secondRejected = assert.rejects(second, /cancel test cleanup/u);
  const third = a.owner.enqueue(a.owner.generation, { transaction: "bbb" });
  const thirdRejected = assert.rejects(third, /cancel test cleanup/u);
  await assert.rejects(a.owner.enqueue(a.owner.generation, { transaction: "z", key: "resource-3" }), /resource limit reached \(2\)/u);
  await assert.rejects(a.owner.enqueue(a.owner.generation, { transaction: "c" }), /job limit reached \(2\)/u);
  a.owner.cancel("cancel test cleanup");
  await Promise.all([secondRejected, thirdRejected]);

  const b = transport({ maxTransactionBytes: 10, maxQueuedBytes: 4, minIntervalMs: 1 });
  b.sink.returns.push(false);
  await b.owner.enqueue(b.owner.generation, { transaction: "x" });
  const queued = b.owner.enqueue(b.owner.generation, { transaction: "1234" });
  const queuedRejected = assert.rejects(queued, /byte test cleanup/u);
  await assert.rejects(b.owner.enqueue(b.owner.generation, { transaction: "z" }), /queued graphics bytes would exceed 4/u);
  b.owner.cancel("byte test cleanup");
  await queuedRejected;
  a.owner.dispose();
  b.owner.dispose();
});

test("retained keys deduplicate accepted uploads and pending placement geometry coalesces to latest", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 20 });
  sink.returns.push(false, true);
  await owner.enqueue(owner.generation, { transaction: "upload", key: "content/version" });
  assert.deepEqual(await owner.enqueue(owner.generation, { transaction: "duplicate", key: "content/version" }), {
    status: "deduplicated", generation: 0, bytes: 0,
  });

  const oldPlacement = owner.enqueue(owner.generation, { transaction: "place:10x5", coalesceKey: "placement:42" });
  const latestPlacement = owner.enqueue(owner.generation, { transaction: "place:20x8", coalesceKey: "placement:42" });
  assert.equal(oldPlacement, latestPlacement, "coalesced callers share settlement for the final transaction");
  assert.equal(owner.pendingJobs, 1);
  sink.drain();
  clock.advance(20);
  assert.deepEqual(sink.writes.map(({ value }) => value), ["upload", "place:20x8"]);
  await latestPlacement;
  owner.dispose();
});

test("generation cancellation rejects queued jobs, preserves drain flow control, and never writes them later", async () => {
  const { clock, sink, owner } = transport({ minIntervalMs: 50, drainTimeoutMs: 500 });
  sink.returns.push(false);
  const accepted = owner.enqueue(owner.generation, { transaction: "accepted" });
  const abandoned = owner.enqueue(owner.generation, { transaction: "abandoned" });
  const abandonedRejected = assert.rejects(abandoned, /reload/u);
  await accepted;
  assert.equal(sink.listenerCount("drain"), 1);

  owner.cancel("reload cancelled queued graphics");
  await abandonedRejected;
  assert.equal(owner.generation, 1);
  assert.equal(owner.pendingJobs, 0);
  assert.equal(owner.pendingBytes, 0);
  assert.equal(owner.retainedResources, 0);
  assert.equal(sink.listenerCount("drain"), 1, "flow-control listener survives generation cancellation");
  assert.equal(clock.count, 1, "outstanding false return retains its drain deadline");

  sink.drain();
  assert.equal(sink.listenerCount("drain"), 0);
  assert.equal(clock.count, 0);
  clock.advance(1_000);
  assert.deepEqual(sink.writes.map(({ value }) => value), ["accepted"]);
  await assert.rejects(
    owner.enqueue(0, { transaction: "late-old-generation" }),
    /stale graphics generation 0; current generation is 1/u,
  );
  const fresh = owner.enqueue(owner.generation, { transaction: "fresh" });
  assert.equal(sink.writes.length, 2);
  assert.deepEqual(await fresh, { status: "accepted", generation: 1, bytes: 5 });
  owner.dispose();
});

test("sink error, close, and slow drain settle unsent jobs and remove transient listeners", async () => {
  for (const event of ["error", "close"] as const) {
    const { sink, owner } = transport({ minIntervalMs: 50 });
    const first = owner.enqueue(owner.generation, { transaction: "first" });
    const queued = owner.enqueue(owner.generation, { transaction: "queued" });
    const rejected = assert.rejects(queued, event === "error" ? /sink error: broken/u : /sink closed/u);
    await first;
    if (event === "error") sink.emit("error", new Error("broken"));
    else sink.emit("close");
    await rejected;
    assert.equal(owner.pendingJobs, 0);
    assert.equal(sink.listenerCount("drain"), 0);
    owner.dispose();
    assert.equal(sink.listenerCount("error"), 0);
    assert.equal(sink.listenerCount("close"), 0);
  }

  const slow = transport({ minIntervalMs: 1, drainTimeoutMs: 10 });
  slow.sink.returns.push(false);
  await slow.owner.enqueue(slow.owner.generation, { transaction: "accepted" });
  const waiting = slow.owner.enqueue(slow.owner.generation, { transaction: "waiting" });
  const timedOut = assert.rejects(waiting, /did not drain within 10 ms/u);
  slow.clock.advance(10);
  await timedOut;
  assert.equal(slow.owner.pendingJobs, 0);
  assert.equal(slow.sink.listenerCount("drain"), 0);
  assert.equal(slow.clock.count, 0);
  await assert.rejects(slow.owner.enqueue(slow.owner.generation, { transaction: "late" }), /did not drain within 10 ms/u);
  slow.owner.dispose();
});
