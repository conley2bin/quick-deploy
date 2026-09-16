import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import sharp from "sharp";
import { MAX_FULL_PNG_BYTES, loadImage, type LoadedImage } from "../src/images.ts";
import { transformMarkdown } from "../src/markdown.ts";
import { ImageSession } from "../src/session.ts";
import {
  MAX_PLACEMENT_CATALOG_BYTES,
  MAX_PLACEMENTS_PER_IMAGE,
  MAX_RESIDENT_PNG_BYTES,
  TerminalImages,
} from "../src/terminal.ts";
import { completeUploadTransaction, DEFAULT_MAX_TRANSACTION_BYTES, uploadTransactionBytes, type TransportScheduler, type TransportSink } from "../src/transport.ts";
import { placement, PLACEHOLDER_GLYPH } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

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
  throwNext?: Error;

  constructor(private readonly clock: FakeClock) { super(); }
  write(value: Buffer): boolean {
    if (this.throwNext) {
      const error = this.throwNext;
      this.throwNext = undefined;
      throw error;
    }
    this.writes.push({ value: value.toString("utf8"), at: this.clock.now() });
    const accepted = this.returns.shift() ?? true;
    if (!accepted) this.writableNeedDrain = true;
    return accepted;
  }
  drain(): void {
    this.writableNeedDrain = false;
    this.emit("drain");
  }
}

function fakeImage(hash: string, png = Buffer.from(`png:${hash}`), width = 120, height = 80): LoadedImage {
  return { source: hash, hash, width, height, png };
}

function runtime(options: {
  maxResidentPngBytes?: number;
  minIntervalMs?: number;
  cell?: { widthPx: number; heightPx: number };
  transportLimits?: Record<string, number>;
} = {}) {
  const clock = new FakeClock();
  const sink = new CapturedSink(clock);
  const cell = options.cell ?? { widthPx: 10, heightPx: 20 };
  let id = 0x07123000;
  const terminal = new TerminalImages(
    () => ++id,
    () => ({ ...cell }),
    sink,
    { TERM_PROGRAM: "ghostty" },
    true,
    {
      maxResidentPngBytes: options.maxResidentPngBytes,
      scheduler: clock,
      transportLimits: { minIntervalMs: options.minIntervalMs ?? 50, ...options.transportLimits },
    },
  );
  return { clock, sink, terminal };
}

async function settleWithClock<T>(promise: Promise<T>, clock: FakeClock, stepMs = 50): Promise<T> {
  let settled: { ok: true; value: T } | { ok: false; error: unknown } | undefined;
  void promise.then(
    (value) => { settled = { ok: true, value }; },
    (error: unknown) => { settled = { ok: false, error }; },
  );
  for (let index = 0; index < 500 && !settled; index++) {
    await Promise.resolve();
    if (!settled && clock.count > 0) clock.advance(stepMs);
  }
  if (!settled) throw new Error("fake-clock operation did not settle");
  if (!settled.ok) throw settled.error;
  return settled.value;
}

function graphicsCommands(value: string): Array<Map<string, string>> {
  return [...value.matchAll(/\x1b_G([^;]*);[\s\S]*?\x1b\\/gu)].map((match) => new Map(
    (match[1] || "").split(",").filter(Boolean).map((field) => {
      const separator = field.indexOf("=");
      return [field.slice(0, separator), field.slice(separator + 1)];
    }),
  ));
}

function uploadId(value: string): number | undefined {
  const first = graphicsCommands(value)[0];
  return first?.get("a") === "t" ? Number(first.get("i")) : undefined;
}

test("suitable PNGs remain byte-exact at full resolution and fit the 44 MiB transaction bound", async () => {
  const width = 1_920;
  const height = 1_080;
  const raw = Buffer.allocUnsafe(width * height * 4);
  let state = 0x12345678;
  for (let index = 0; index < raw.length; index++) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    raw[index] = state >>> 24;
  }
  const source = await sharp(raw, { raw: { width, height, channels: 4 } }).png({ compressionLevel: 0 }).toBuffer();
  assert.ok(source.length > 1_000_000);
  const loaded = await loadImage(`data:image/png;base64,${source.toString("base64")}`, "/fixture");

  assert.deepEqual(loaded.png, source, "unoriented PNG is uploaded byte-for-byte");
  assert.deepEqual([loaded.width, loaded.height], [width, height]);
  const upload = completeUploadTransaction(source, 0xffffffff, true);
  assert.equal(upload.length, uploadTransactionBytes(source.length, 0xffffffff, true));
  assert.ok(completeUploadTransaction(Buffer.alloc(MAX_FULL_PNG_BYTES), 0xffffffff, true).length < DEFAULT_MAX_TRANSACTION_BYTES);

  const { clock, sink, terminal } = runtime({ minIntervalMs: 0 });
  await terminal.prepare("large", loaded);
  const wire = sink.writes[0]!.value;
  assert.ok(wire.includes("a=p"), "catalog follows the final m=0 upload in the same write");
  assert.ok(Buffer.byteLength(wire) <= DEFAULT_MAX_TRANSACTION_BYTES);
  const reconstructed = Buffer.from(
    [...wire.matchAll(/\x1b_G[^;]*;([A-Za-z0-9+/=]*)\x1b\\/gu)].map((match) => match[1]).join(""),
    "base64",
  );
  assert.deepEqual(reconstructed, source, "wire reassembly retains every PNG byte");
  const decoded = await sharp(reconstructed).raw().toBuffer({ resolveWithObject: true });
  assert.deepEqual([decoded.info.width, decoded.info.height, decoded.info.channels], [width, height, 4]);
  assert.deepEqual(decoded.data, raw, "wire image has identical pixels and alpha");
  await settleWithClock(terminal.clear(true), clock);
});

test("orientation conversion and 16-bit PNG fidelity never downsample", async () => {
  const orientedRaw = Buffer.from([
    255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255,
    255, 255, 0, 255, 255, 0, 255, 255, 0, 255, 255, 255,
  ]);
  const orientedJpeg = await sharp(orientedRaw, { raw: { width: 3, height: 2, channels: 4 } })
    .jpeg({ quality: 100 })
    .withMetadata({ orientation: 6 })
    .toBuffer();
  const oriented = await loadImage(`data:image/jpeg;base64,${orientedJpeg.toString("base64")}`, "/fixture");
  const expectedOrientation = await sharp(orientedJpeg).autoOrient().png({ palette: false }).toBuffer();
  assert.deepEqual([oriented.width, oriented.height], [2, 3]);
  assert.deepEqual(oriented.png, expectedOrientation, "orientation is applied at full dimensions with the lossless PNG path");

  const width = 7, height = 5, channels = 4;
  const raw16 = new Uint16Array(width * height * channels);
  for (let index = 0; index < raw16.length; index++) raw16[index] = (index * 1954) & 0xffff;
  const png16 = await sharp(raw16, { raw: { width, height, channels } })
    .toColourspace("rgb16")
    .png()
    .toBuffer();
  const metadata = await sharp(png16).metadata();
  assert.equal(metadata.depth, "ushort");
  const loaded16 = await loadImage(`data:image/png;base64,${png16.toString("base64")}`, "/fixture");
  assert.deepEqual([loaded16.width, loaded16.height], [width, height]);
  assert.deepEqual(loaded16.png, png16, "unoriented 16-bit PNG keeps exact source bytes and bit depth");
});

test("byte-exact PNG fast path rejects truncated pixel streams", async () => {
  const source = await sharp(Buffer.alloc(12 * 6 * 4, 127), { raw: { width: 12, height: 6, channels: 4 } })
    .png({ compressionLevel: 0 })
    .toBuffer();
  let offset = 8;
  let idatData = -1;
  let idatLength = 0;
  while (offset + 12 <= source.length) {
    const length = source.readUInt32BE(offset);
    if (source.subarray(offset + 4, offset + 8).toString("ascii") === "IDAT") {
      idatData = offset + 8;
      idatLength = length;
      break;
    }
    offset += length + 12;
  }
  assert.ok(idatData > 0 && idatLength > 1);
  const truncated = source.subarray(0, idatData + Math.floor(idatLength / 2));
  await assert.rejects(
    loadImage(`data:image/png;base64,${truncated.toString("base64")}`, "/fixture"),
    /invalid image content/u,
  );
});

test("worst catalog deduplicates widths to 80 entries and remains within its metadata budget", async () => {
  const clock = new FakeClock();
  const sink = new CapturedSink(clock);
  const terminal = new TerminalImages(
    () => 0xffffffff,
    () => ({ widthPx: 10, heightPx: 20 }),
    sink,
    { TERM: "tmux-256color", TMUX: "fixture" },
    true,
    { scheduler: clock, transportLimits: { minIntervalMs: 0 } },
  );
  await terminal.prepare("catalog", fakeImage("catalog", Buffer.from("png"), 1_000, 599));
  const catalog = sink.writes[0]!.value;
  const commands = graphicsCommands(catalog).filter((command) => command.get("a") === "p");
  assert.equal(commands.length, MAX_PLACEMENTS_PER_IMAGE);
  assert.equal(new Set(commands.map((command) => `${command.get("c")}:${command.get("r")}`)).size, commands.length);
  const placementBytes = commands.reduce((total, command) => total + Buffer.byteLength(placement(
    0xffffffff, Number(command.get("c")), Number(command.get("r")), true, Number(command.get("p")),
  )), 0);
  assert.equal(placementBytes, 4_432);
  assert.equal(MAX_PLACEMENT_CATALOG_BYTES, 4_480);
  assert.ok(placementBytes <= MAX_PLACEMENT_CATALOG_BYTES);
  await settleWithClock(terminal.clear(true), clock);
});

test("awaited preparation creates bounded placement catalogs before pure width-changing renders", async () => {
  const { clock, sink, terminal } = runtime();
  const source = "before\n\n![one](one.png)\n\n![two](two.png)\n\n![three](three.png)\n\nafter";
  const session = new ImageSession(terminal, async (href) => fakeImage(href));
  const prepared = await settleWithClock(session.prepare(source, "/fixture"), clock);

  const uploadWrites = sink.writes.filter(({ value }) => uploadId(value) !== undefined);
  const uploaded = uploadWrites.map(({ value }) => uploadId(value));
  assert.deepEqual(uploaded, [0x07123001, 0x07123002, 0x07123003]);
  assert.ok(sink.writes.every(({ value }) => Buffer.byteLength(value) <= DEFAULT_MAX_TRANSACTION_BYTES));
  assert.ok(sink.writes.every(({ at }, index, writes) => index === 0 || at - writes[index - 1]!.at >= 50));
  assert.equal(terminal.pendingJobs(), 0);

  const allCommands = sink.writes.flatMap(({ value }) => graphicsCommands(value));
  for (const id of uploaded) {
    assert.ok(allCommands.some((command) => command.get("a") === "p" && Number(command.get("i")) === id), `placements prepared for ${id}`);
  }
  const mapping = allCommands.find((command) =>
    command.get("a") === "p" && Number(command.get("i")) === uploaded[0] && command.get("c") === "6" && command.get("r") === "2");
  assert.ok(mapping, "catalog maps 6x2 geometry to a concrete placement");
  const customPlacementId = Number(mapping.get("p"));
  const mappedGrid = terminal.render(prepared.references[0]!.logicalId, 6).join("\n");
  assert.match(mappedGrid, new RegExp(`\\x1b\\[58;2;0;0;${customPlacementId}m`, "u"));
  assert.ok(mappedGrid.includes(`${PLACEHOLDER_GLYPH}\u0305\u0305\u033f`), "grid encodes row, column, and image high byte 7");

  const writesBeforeRender = sink.writes.length;
  let rendered = "";
  for (let index = 0; index < 1_000; index++) rendered = transformMarkdown(prepared, index % 80 + 1, terminal);
  assert.equal(sink.writes.length, writesBeforeRender, "1000 stable/width-changing renders write and enqueue zero transactions");
  assert.equal(terminal.pendingJobs(), 0);
  assert.ok(rendered.indexOf("before") < rendered.indexOf(PLACEHOLDER_GLYPH));
  assert.ok(rendered.indexOf(PLACEHOLDER_GLYPH) < rendered.indexOf("after"));
  await settleWithClock(session.reset(true), clock);
});

test("cell metric changes fail explicitly instead of selecting a missing placement catalog entry", async () => {
  const cell = { widthPx: 10, heightPx: 20 };
  const { clock, sink, terminal } = runtime({ minIntervalMs: 0, cell });
  const session = new ImageSession(terminal, async () => fakeImage("cell"));
  const prepared = await session.prepare("before ![cell](cell.png) after", "/fixture");
  const preparedWrites = sink.writes.length;
  cell.widthPx = 11;
  const output = transformMarkdown(prepared, 40, terminal);
  assert.match(output, /terminal cell dimensions changed from 10x20 px to 11x20 px; reload required/u);
  assert.equal(sink.writes.length, preparedWrites);
  await settleWithClock(session.reset(true), clock);
});

test("old-plugin graphics can occur only between complete inline upload writes", async () => {
  const { clock, sink, terminal } = runtime();
  const source = "![one](one.png)\n\n![two](two.png)";
  const session = new ImageSession(terminal, async (href) => fakeImage(href, Buffer.alloc(8_000, href === "one.png" ? 1 : 2)));
  const preparing = session.prepare(source, "/fixture");
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(sink.writes.length, 1);
  const firstApcs = graphicsCommands(sink.writes[0]!.value);
  const firstUpload = firstApcs.filter((command) => command.has("m"));
  assert.ok(firstUpload.length > 2 && firstUpload.at(-1)!.get("m") === "0");

  const legacy = placement(0x03334444, 5, 3, false);
  sink.write(Buffer.from(legacy));
  const prepared = await settleWithClock(preparing, clock);
  const secondUploadIndex = sink.writes.findIndex(({ value }) => uploadId(value) === 0x07123002);
  assert.ok(secondUploadIndex > 1);
  assert.equal(sink.writes[1]!.value, legacy);
  assert.equal(graphicsCommands(sink.writes[secondUploadIndex]!.value).filter((command) => command.has("m")).at(-1)!.get("m"), "0");
  assert.match(transformMarkdown(prepared, 30, terminal), new RegExp(PLACEHOLDER_GLYPH, "u"));
  await settleWithClock(session.reset(true), clock);
  const deletedIds = sink.writes.flatMap(({ value }) => graphicsCommands(value))
    .filter((command) => command.get("a") === "d" && command.get("d") === "I")
    .map((command) => Number(command.get("i")));
  assert.equal(deletedIds.includes(0x03334444), false, "cleanup never targets the old plugin's image ID");
});

test("resident pressure and sink failures remain explicit at the original Markdown position", async () => {
  const resident = runtime({ maxResidentPngBytes: 10, minIntervalMs: 0 });
  const session = new ImageSession(resident.terminal, async (href) => fakeImage(href, Buffer.alloc(6, href.charCodeAt(0))));
  const source = "start ![one](a.png) middle ![two](b.png) end";
  const prepared = await session.prepare(source, "/fixture");
  const output = transformMarkdown(prepared, 40, resident.terminal);
  assert.equal(resident.terminal.residentBytes(), 6);
  assert.match(output, /image unavailable: two — resident PNG budget reached \(10 bytes\)/u);
  assert.ok(output.indexOf("start") < output.indexOf(PLACEHOLDER_GLYPH));
  assert.ok(output.indexOf("resident PNG budget") < output.indexOf("end"));
  await settleWithClock(session.reset(true), resident.clock);

  const failed = runtime({ minIntervalMs: 0 });
  failed.sink.throwNext = new Error("captured sink failed");
  const failedSession = new ImageSession(failed.terminal, async () => fakeImage("broken"));
  const failedPrepared = await failedSession.prepare("before ![broken](x.png) after", "/fixture");
  assert.match(transformMarkdown(failedPrepared, 40, failed.terminal), /image unavailable: broken — graphics sink write failed/u);
  await assert.rejects(settleWithClock(failedSession.reset(true), failed.clock), /graphics sink write failed/u);
  assert.equal(failed.terminal.count(), 1, "fatal sink retains unresolved owned image identity");
  assert.equal(failed.sink.listenerCount("drain"), 0);
  assert.equal(failed.sink.listenerCount("error"), 0);
  assert.equal(failed.sink.listenerCount("close"), 0);
  assert.equal(MAX_RESIDENT_PNG_BYTES, 64 * 1024 * 1024);
});

test("same-runtime branch reconciliation reuses unchanged prepared resources but a new factory uploads again", async () => {
  const first = runtime({ minIntervalMs: 0 });
  let loads = 0;
  const source = "![same](same.png)";
  const entries = [{ type: "message", message: { role: "assistant", content: [{ type: "text", text: source }] } }];
  const session = new ImageSession(first.terminal, async () => { loads++; return fakeImage("same"); });
  await session.restore(entries, "/fixture");
  const uploadsAfterFirstRestore = first.sink.writes.filter(({ value }) => uploadId(value) !== undefined).length;
  await session.restore(entries, "/fixture", true);
  assert.equal(loads, 1);
  assert.equal(first.sink.writes.filter(({ value }) => uploadId(value) !== undefined).length, uploadsAfterFirstRestore);

  const second = runtime({ minIntervalMs: 0 });
  const newFactory = new ImageSession(second.terminal, async () => fakeImage("same"));
  await newFactory.restore(entries, "/fixture");
  assert.equal(second.sink.writes.filter(({ value }) => uploadId(value) !== undefined).length, 1, "new runtime cannot assume the old terminal cache identity");
  await settleWithClock(session.reset(true), first.clock);
  await settleWithClock(newFactory.reset(true), second.clock);
});

test("many large distinct prepared previews stay within aggregate resident and wire budgets with visible rejection", async () => {
  const width = 512;
  const height = 256;
  const raw = Buffer.allocUnsafe(width * height * 3);
  let state = 0x9e3779b9;
  for (let index = 0; index < raw.length; index++) {
    state = (Math.imul(state, 1103515245) + 12345) >>> 0;
    raw[index] = state >>> 24;
  }
  const previews: LoadedImage[] = [];
  for (let index = 0; index < 36; index++) {
    raw[index] ^= index + 1;
    const png = await sharp(raw, { raw: { width, height, channels: 3 } }).png({ compressionLevel: 0 }).toBuffer();
    previews.push(fakeImage(`pressure-${index}`, png, width, height));
  }

  const { clock, sink, terminal } = runtime({ maxResidentPngBytes: 4 * 1024 * 1024, minIntervalMs: 0 });
  const session = new ImageSession(terminal, async (href) => previews[Number(/\d+/u.exec(href)?.[0])]!);
  const source = previews.map((_, index) => `![pressure-${index}](image-${index}.png)`).join("\n\n");
  const prepared = await settleWithClock(session.prepare(source, "/fixture"), clock);
  const rejected = prepared.references.filter((reference) => reference.error?.includes("resident PNG budget"));
  assert.ok(rejected.length > 0, "pressure must cross the aggregate budget");
  assert.ok(terminal.residentBytes() <= MAX_RESIDENT_PNG_BYTES);
  assert.equal(terminal.pendingJobs(), 0);
  const uploadWrites = sink.writes.filter(({ value }) => uploadId(value) !== undefined);
  assert.equal(uploadWrites.length, prepared.references.length - rejected.length);
  assert.ok(uploadWrites.every(({ value }) => Buffer.byteLength(value) <= DEFAULT_MAX_TRANSACTION_BYTES));
  assert.ok(uploadWrites.reduce((total, { value }) => total + Buffer.byteLength(value), 0) <= uploadWrites.length * DEFAULT_MAX_TRANSACTION_BYTES);
  assert.match(transformMarkdown(prepared, 1, terminal), /resident PNG budget reached/u);
  await settleWithClock(session.reset(true), clock);
});

test("cleanup admits deletes sequentially under a one-job queue and retains ownership on failure", async () => {
  const bounded = runtime({ minIntervalMs: 0, transportLimits: { maxQueuedJobs: 1, maxQueuedBytes: 5_000 } });
  await bounded.terminal.prepare("one", fakeImage("one"));
  await settleWithClock(bounded.terminal.prepare("two", fakeImage("two")), bounded.clock);
  const beforeDeletes = bounded.sink.writes.length;
  await settleWithClock(bounded.terminal.clear(), bounded.clock);
  const deletes = bounded.sink.writes.slice(beforeDeletes).flatMap(({ value }) => graphicsCommands(value))
    .filter((command) => command.get("a") === "d" && command.get("d") === "I");
  assert.equal(deletes.length, 2);
  assert.equal(bounded.terminal.count(), 0);
  assert.equal(bounded.terminal.residentBytes(), 0);
  await settleWithClock(bounded.terminal.clear(true), bounded.clock);

  const failed = runtime({ minIntervalMs: 0, transportLimits: { maxQueuedJobs: 1, maxQueuedBytes: 5_000 } });
  await failed.terminal.prepare("owned", fakeImage("owned"));
  failed.sink.throwNext = new Error("delete failed");
  await assert.rejects(settleWithClock(failed.terminal.clear(true), failed.clock), /graphics sink write failed/u);
  assert.equal(failed.terminal.count(), 1, "failed delete retains unresolved ownership");
  assert.equal(failed.terminal.residentBytes(), fakeImage("owned").png.length);
  assert.equal(failed.sink.listenerCount("drain"), 0);
  assert.equal(failed.sink.listenerCount("error"), 0);
  assert.equal(failed.sink.listenerCount("close"), 0);
  failed.clock.advance(10_000);
  assert.equal(failed.clock.count, 0);
});

test("reset rejects late loads and cancels backpressured placements before ordered deletion", async () => {
  const late = runtime({ minIntervalMs: 0 });
  let release!: (image: LoadedImage) => void;
  const deferred = new Promise<LoadedImage>((resolve) => { release = resolve; });
  const lateSession = new ImageSession(late.terminal, async () => deferred);
  const preparing = lateSession.prepare("![late](late.png)", "/fixture");
  await Promise.resolve();
  await settleWithClock(lateSession.reset(), late.clock);
  release(fakeImage("late"));
  await preparing;
  assert.equal(late.sink.writes.length, 0);
  assert.equal(late.terminal.count(), 0);
  await settleWithClock(lateSession.reset(true), late.clock);

  const blocked = runtime({ minIntervalMs: 0 });
  blocked.sink.returns.push(false);
  const blockedSession = new ImageSession(blocked.terminal, async () => fakeImage("blocked"));
  const blockedPreparing = blockedSession.prepare("![blocked](blocked.png)", "/fixture");
  for (let index = 0; index < 5; index++) await Promise.resolve();
  assert.equal(blocked.sink.writes.filter(({ value }) => uploadId(value) !== undefined).length, 1);
  assert.equal(blocked.terminal.pendingJobs(), 0, "upload and placement catalog are one accepted atomic transaction");
  const initialPlacementCount = blocked.sink.writes.flatMap(({ value }) => graphicsCommands(value))
    .filter((command) => command.get("a") === "p").length;
  const resetting = blockedSession.reset(true);
  await Promise.resolve();
  assert.equal(blocked.sink.listenerCount("drain"), 1);
  blocked.sink.drain();
  const abandoned = await blockedPreparing;
  await settleWithClock(resetting, blocked.clock);
  assert.doesNotMatch(transformMarkdown(abandoned, 20, blocked.terminal), new RegExp(PLACEHOLDER_GLYPH, "u"));
  assert.equal(blocked.sink.writes.flatMap(({ value }) => graphicsCommands(value))
    .filter((command) => command.get("a") === "p").length, initialPlacementCount);
  assert.equal(blocked.sink.writes.some(({ value }) => graphicsCommands(value).some((command) => command.get("a") === "d" && command.get("d") === "I")), true);
  blocked.clock.advance(1_000);
  assert.equal(blocked.sink.writes.flatMap(({ value }) => graphicsCommands(value))
    .filter((command) => command.get("a") === "p").length, initialPlacementCount, "reset emits no later placement catalog");
  assert.equal(blocked.sink.listenerCount("drain"), 0);
  assert.equal(blocked.sink.listenerCount("error"), 0);
  assert.equal(blocked.sink.listenerCount("close"), 0);
  assert.equal(blocked.clock.count, 0);
});
