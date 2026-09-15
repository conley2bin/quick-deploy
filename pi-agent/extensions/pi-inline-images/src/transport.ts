import { kitty } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

export const DEFAULT_MAX_TRANSACTION_BYTES = 44 * 1024 * 1024;
/** Includes queued work plus the complete accepted-false write awaiting drain. */
export const DEFAULT_MAX_QUEUED_BYTES = 96 * 1024 * 1024;
export const DEFAULT_MAX_QUEUED_JOBS = 64;
export const DEFAULT_MAX_RESOURCES = 64;
export const DEFAULT_MIN_INTERVAL_MS = 50;
export const DEFAULT_DRAIN_TIMEOUT_MS = 60_000;
export const DEFAULT_WIRE_RATE_BYTES_PER_SECOND = 8 * 1024 * 1024;
const KITTY_BASE64_CHARS = 4_096;
const RAW_CHUNK_BYTES = KITTY_BASE64_CHARS / 4 * 3;

export type TransportErrorCode = "cancelled" | "closed" | "drain-timeout" | "invalid" | "limit" | "sink";

export class TransportError extends Error {
  constructor(readonly code: TransportErrorCode, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "TransportError";
  }
}

export interface TransportSink {
  readonly writableNeedDrain?: boolean;
  write(value: Buffer): boolean;
  on(event: "drain", listener: () => void): this;
  on(event: "error", listener: (error: Error) => void): this;
  on(event: "close", listener: () => void): this;
  removeListener(event: "drain", listener: () => void): this;
  removeListener(event: "error", listener: (error: Error) => void): this;
  removeListener(event: "close", listener: () => void): this;
}

export interface TransportScheduler {
  now(): number;
  setTimeout(callback: () => void, delayMs: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface TransportLimits {
  maxTransactionBytes: number;
  maxQueuedBytes: number;
  maxQueuedJobs: number;
  maxResources: number;
  minIntervalMs: number;
  drainTimeoutMs: number;
  wireRateBytesPerSecond: number;
}

export interface TransportRequest {
  /** Complete graphics transaction. Functions are constructed only after admission. */
  transaction: Buffer | string | (() => Buffer);
  /** Required for a lazy transaction and must equal its final wire byte length. */
  bytes?: number;
  key?: string;
  coalesceKey?: string;
}

export interface TransportResult {
  status: "accepted" | "deduplicated";
  generation: number;
  bytes: number;
}

type PendingJob = {
  build: () => Buffer;
  bytes: number;
  key?: string;
  coalesceKey?: string;
  generation: number;
  promise: Promise<TransportResult>;
  resolve: (result: TransportResult) => void;
  reject: (error: Error) => void;
};

type ReadyWaiter = { generation: number; resolve: () => void; reject: (error: Error) => void };

const defaultScheduler: TransportScheduler = {
  now: () => Date.now(),
  setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimeout: (handle) => clearTimeout(handle as NodeJS.Timeout),
};

export const DEFAULT_TRANSPORT_LIMITS: Readonly<TransportLimits> = Object.freeze({
  maxTransactionBytes: DEFAULT_MAX_TRANSACTION_BYTES,
  maxQueuedBytes: DEFAULT_MAX_QUEUED_BYTES,
  maxQueuedJobs: DEFAULT_MAX_QUEUED_JOBS,
  maxResources: DEFAULT_MAX_RESOURCES,
  minIntervalMs: DEFAULT_MIN_INTERVAL_MS,
  drainTimeoutMs: DEFAULT_DRAIN_TIMEOUT_MS,
  wireRateBytesPerSecond: DEFAULT_WIRE_RATE_BYTES_PER_SECOND,
});

function encodedChunkBytes(rawBytes: number): number {
  return 4 * Math.ceil(rawBytes / 3);
}

/** Exact transaction size without allocating base64 or a full wire buffer. */
export function uploadTransactionBytes(pngBytes: number, imageId: number, inTmux: boolean): number {
  if (!Number.isSafeInteger(pngBytes) || pngBytes < 0) throw new TransportError("invalid", "PNG byte length must be a non-negative safe integer");
  const chunks = Math.max(1, Math.ceil(pngBytes / RAW_CHUNK_BYTES));
  let bytes = 0;
  for (let index = 0; index < chunks; index++) {
    const raw = Math.min(RAW_CHUNK_BYTES, Math.max(0, pngBytes - index * RAW_CHUNK_BYTES));
    const command = `${index === 0 ? `a=t,f=100,i=${imageId},q=2,` : ""}m=${index + 1 < chunks ? 1 : 0};`;
    // kitty() encloses command in ESC_G … ESC\\. tmux() adds DCS framing and
    // doubles its two ESC bytes: exactly 11 bytes beyond direct Kitty framing.
    bytes += command.length + encodedChunkBytes(raw) + (inTmux ? 16 : 5);
  }
  return bytes;
}

/** Build one indivisible upload from 4096-base64-character Kitty chunks. */
export function completeUploadTransaction(png: Buffer, imageId: number, inTmux: boolean): Buffer {
  const pieces: Buffer[] = [];
  const chunkCount = Math.max(1, Math.ceil(png.length / RAW_CHUNK_BYTES));
  for (let index = 0; index < chunkCount; index++) {
    const payload = png.subarray(index * RAW_CHUNK_BYTES, (index + 1) * RAW_CHUNK_BYTES).toString("base64");
    const first = index === 0 ? `a=t,f=100,i=${imageId},q=2,` : "";
    pieces.push(Buffer.from(kitty(`${first}m=${index + 1 < chunkCount ? 1 : 0};${payload}`, inTmux), "utf8"));
  }
  const transaction = Buffer.concat(pieces);
  const expected = uploadTransactionBytes(png.length, imageId, inTmux);
  if (transaction.length !== expected) throw new TransportError("invalid", "upload transaction size calculation mismatch");
  return transaction;
}

/** Upload followed by its complete placement catalog in exactly one write buffer. */
export function completeImageTransaction(png: Buffer, imageId: number, inTmux: boolean, catalog: string): Buffer {
  return Buffer.concat([completeUploadTransaction(png, imageId, inTmux), Buffer.from(catalog, "utf8")]);
}

/** Owns and serializes all graphics writes from this extension runtime. */
export class BoundedTransport {
  private readonly limits: TransportLimits;
  private readonly queue: PendingJob[] = [];
  private readonly pendingKeys = new Map<string, PendingJob>();
  private readonly coalesced = new Map<string, PendingJob>();
  private readonly acceptedKeys = new Set<string>();
  private readonly readyWaiters: ReadyWaiter[] = [];
  private queuedBytes = 0;
  private undrainedBytes = 0;
  private currentGeneration = 0;
  private lastWriteAt: number | undefined;
  private lastWriteBytes = 0;
  private pumpTimer: unknown;
  private drainTimer: unknown;
  private drainListener?: () => void;
  private backpressured = false;
  private writing = false;
  private drainedDuringWrite = false;
  private failure?: TransportError;
  private disposed = false;

  private readonly errorListener = (error: Error) => this.fail(new TransportError("sink", `graphics sink error: ${error.message}`, { cause: error }));
  private readonly closeListener = () => this.fail(new TransportError("closed", "graphics sink closed"));

  constructor(private readonly sink: TransportSink, limits: Partial<TransportLimits> = {}, private readonly scheduler: TransportScheduler = defaultScheduler) {
    this.limits = { ...DEFAULT_TRANSPORT_LIMITS, ...limits };
    for (const [name, value] of Object.entries(this.limits)) {
      if (!Number.isSafeInteger(value) || value < (name === "minIntervalMs" ? 0 : 1)) {
        throw new TransportError("invalid", `${name} must be a ${name === "minIntervalMs" ? "non-negative" : "positive"} safe integer`);
      }
    }
    this.sink.on("error", this.errorListener);
    this.sink.on("close", this.closeListener);
  }

  get generation(): number { return this.currentGeneration; }
  get pendingJobs(): number { return this.queue.length; }
  /** All admitted wire bytes, including accepted output that has not drained. */
  get pendingBytes(): number { return this.queuedBytes + this.undrainedBytes; }
  get retainedResources(): number { return this.acceptedKeys.size + this.pendingKeys.size; }

  enqueue(generation: number, request: TransportRequest): Promise<TransportResult> {
    if (this.disposed) return Promise.reject(new TransportError("closed", "graphics transport disposed"));
    if (this.failure) return Promise.reject(this.failure);
    if (generation !== this.currentGeneration) return Promise.reject(new TransportError("cancelled", `stale graphics generation ${generation}; current generation is ${this.currentGeneration}`));
    if (request.key && request.coalesceKey) return Promise.reject(new TransportError("invalid", "a transaction cannot be both retained and coalesced"));

    let build: () => Buffer;
    let bytes: number;
    if (typeof request.transaction === "function") {
      const reservedBytes = request.bytes;
      if (reservedBytes === undefined || !Number.isSafeInteger(reservedBytes) || reservedBytes < 1) return Promise.reject(new TransportError("invalid", "lazy graphics transaction requires positive exact bytes"));
      bytes = reservedBytes;
      build = request.transaction;
    } else {
      const transaction = Buffer.isBuffer(request.transaction) ? request.transaction : Buffer.from(request.transaction, "utf8");
      if (transaction.length === 0) return Promise.reject(new TransportError("invalid", "graphics transaction must not be empty"));
      bytes = transaction.length;
      if (request.bytes !== undefined && request.bytes !== bytes) return Promise.reject(new TransportError("invalid", "graphics transaction byte length mismatch"));
      build = () => transaction;
    }
    if (bytes > this.limits.maxTransactionBytes) return Promise.reject(new TransportError("limit", `graphics transaction is ${bytes} bytes; limit is ${this.limits.maxTransactionBytes} bytes`));

    if (request.key) {
      if (this.acceptedKeys.has(request.key)) return Promise.resolve({ status: "deduplicated", generation: this.currentGeneration, bytes: 0 });
      const pending = this.pendingKeys.get(request.key);
      if (pending) return pending.promise;
      if (this.acceptedKeys.size + this.pendingKeys.size >= this.limits.maxResources) return Promise.reject(new TransportError("limit", `graphics resource limit reached (${this.limits.maxResources})`));
    }
    if (request.coalesceKey) {
      const pending = this.coalesced.get(request.coalesceKey);
      if (pending) {
        const oldBytes = pending.bytes;
        const replacementBytes = this.queuedBytes - oldBytes + bytes + this.undrainedBytes;
        if (replacementBytes > this.limits.maxQueuedBytes) return Promise.reject(new TransportError("limit", `graphics wire budget would exceed ${this.limits.maxQueuedBytes}`));
        pending.build = build;
        pending.bytes = bytes;
        this.queuedBytes += bytes - oldBytes;
        return pending.promise;
      }
    }
    if (this.queue.length >= this.limits.maxQueuedJobs) return Promise.reject(new TransportError("limit", `queued graphics job limit reached (${this.limits.maxQueuedJobs})`));
    if (this.queuedBytes + this.undrainedBytes + bytes > this.limits.maxQueuedBytes) return Promise.reject(new TransportError("limit", `graphics wire budget would exceed ${this.limits.maxQueuedBytes}`));

    let resolve!: (result: TransportResult) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<TransportResult>((yes, no) => { resolve = yes; reject = no; });
    const job: PendingJob = { build, bytes, key: request.key, coalesceKey: request.coalesceKey, generation: this.currentGeneration, promise, resolve, reject };
    this.queue.push(job);
    this.queuedBytes += bytes;
    if (job.key) this.pendingKeys.set(job.key, job);
    if (job.coalesceKey) this.coalesced.set(job.coalesceKey, job);
    this.resume();
    return promise;
  }

  ready(generation: number): Promise<void> {
    if (this.disposed) return Promise.reject(new TransportError("closed", "graphics transport disposed"));
    if (this.failure) return Promise.reject(this.failure);
    if (generation !== this.currentGeneration) return Promise.reject(new TransportError("cancelled", `stale graphics generation ${generation}; current generation is ${this.currentGeneration}`));
    if (this.queue.length === 0 && !this.backpressured) return Promise.resolve();
    const promise = new Promise<void>((resolve, reject) => this.readyWaiters.push({ generation, resolve, reject }));
    if (this.backpressured) this.waitForDrain(); else this.resume();
    return promise;
  }

  /** Cancellation removes only unsent owner jobs; sink drain/rate debt survive. */
  cancel(reason = "graphics generation cancelled", options: { retainAccepted?: boolean } = {}): void {
    if (this.disposed) return;
    this.currentGeneration++;
    this.clearPumpTimer();
    this.rejectQueue(new TransportError("cancelled", reason));
    this.rejectReady(new TransportError("cancelled", reason));
    if (!options.retainAccepted) this.acceptedKeys.clear();
    if (this.sink.writableNeedDrain === true || this.undrainedBytes > 0) this.backpressured = true;
    if (this.backpressured) this.waitForDrain(); else this.clearDrainWait();
  }

  dispose(reason = "graphics transport disposed"): void {
    if (this.disposed) return;
    this.disposed = true;
    this.currentGeneration++;
    const error = new TransportError("closed", reason);
    this.clearPumpTimer();
    this.clearDrainWait();
    this.rejectQueue(error);
    this.rejectReady(error);
    this.acceptedKeys.clear();
    this.undrainedBytes = 0;
    this.backpressured = false;
    this.sink.removeListener("error", this.errorListener);
    this.sink.removeListener("close", this.closeListener);
  }

  private resume(): void {
    if (this.failure || this.disposed || this.queue.length === 0 || this.writing) return;
    if (!this.backpressured && (this.sink.writableNeedDrain === true || this.undrainedBytes > 0)) this.backpressured = true;
    if (this.backpressured) { this.waitForDrain(); return; }
    const now = this.scheduler.now();
    const rateDelay = this.lastWriteAt === undefined ? 0 : Math.ceil(this.lastWriteBytes / this.limits.wireRateBytesPerSecond * 1_000);
    const delay = this.lastWriteAt === undefined ? 0 : Math.max(0, this.lastWriteAt + Math.max(this.limits.minIntervalMs, rateDelay) - now);
    if (delay > 0) this.schedulePump(delay); else this.pump();
  }

  private pump(): void {
    this.pumpTimer = undefined;
    if (this.failure || this.disposed || this.backpressured) return;
    const job = this.queue[0];
    if (!job) return;
    if (job.generation !== this.currentGeneration) { this.rejectQueue(new TransportError("cancelled", "stale graphics generation")); return; }
    let transaction: Buffer;
    try {
      transaction = job.build();
      if (!Buffer.isBuffer(transaction) || transaction.length !== job.bytes) throw new Error("reserved graphics transaction bytes do not match constructed Buffer");
    } catch (error) {
      this.fail(new TransportError("invalid", "graphics transaction construction failed", { cause: error }));
      return;
    }
    let accepted: boolean;
    this.writing = true;
    this.drainedDuringWrite = false;
    this.observeDrain();
    try {
      accepted = this.sink.write(transaction);
      if (typeof accepted !== "boolean") throw new TypeError("graphics sink write() did not return a boolean");
    } catch (error) {
      this.writing = false;
      this.clearDrainWait();
      this.fail(new TransportError("sink", "graphics sink write failed", { cause: error }));
      return;
    }
    this.writing = false;
    if (this.disposed || this.failure) { this.clearDrainWait(); return; }
    if (this.queue[0] !== job) {
      this.lastWriteAt = this.scheduler.now();
      this.lastWriteBytes = job.bytes;
      if (!accepted && !this.drainedDuringWrite) {
        this.undrainedBytes += job.bytes;
        this.backpressured = true;
        this.observeDrain();
        this.startDrainTimeout();
      } else { this.backpressured = false; this.clearDrainWait(); if (this.queue.length > 0) this.resume(); }
      return;
    }
    this.queue.shift();
    this.queuedBytes -= job.bytes;
    if (job.key) { this.pendingKeys.delete(job.key); this.acceptedKeys.add(job.key); }
    if (job.coalesceKey) this.coalesced.delete(job.coalesceKey);
    this.lastWriteAt = this.scheduler.now();
    this.lastWriteBytes = job.bytes;
    job.resolve({ status: "accepted", generation: job.generation, bytes: job.bytes });
    if (!accepted && !this.drainedDuringWrite) {
      this.undrainedBytes += job.bytes;
      this.backpressured = true;
      this.observeDrain();
      this.startDrainTimeout();
    } else {
      this.backpressured = false;
      this.clearDrainWait();
      if (this.queue.length > 0) this.resume(); else this.settleReady();
    }
  }

  private schedulePump(delayMs: number): void { if (this.pumpTimer === undefined) this.pumpTimer = this.scheduler.setTimeout(() => this.pump(), delayMs); }
  private observeDrain(): void {
    if (this.drainListener || this.failure || this.disposed) return;
    const onDrain = () => {
      if (this.writing) { this.drainedDuringWrite = true; return; }
      this.undrainedBytes = 0;
      this.clearDrainWait();
      if (this.failure || this.disposed) return;
      this.backpressured = false;
      this.resume();
      this.settleReady();
    };
    this.drainListener = onDrain;
    this.sink.on("drain", onDrain);
  }
  private waitForDrain(): void { if (!this.failure && !this.disposed && (this.backpressured || this.sink.writableNeedDrain === true || this.undrainedBytes > 0)) { this.observeDrain(); this.startDrainTimeout(); } }
  private startDrainTimeout(): void {
    if (this.drainTimer !== undefined || !this.drainListener || this.failure || this.disposed) return;
    this.drainTimer = this.scheduler.setTimeout(() => { if (this.drainListener) this.fail(new TransportError("drain-timeout", `graphics sink did not drain within ${this.limits.drainTimeoutMs} ms`)); }, this.limits.drainTimeoutMs);
  }
  private fail(error: TransportError): void {
    if (this.failure || this.disposed) return;
    this.failure = error; this.currentGeneration++; this.clearPumpTimer(); this.clearDrainWait(); this.undrainedBytes = 0; this.rejectQueue(error); this.rejectReady(error);
  }
  private rejectQueue(error: TransportError): void {
    const pending = this.queue.splice(0); this.queuedBytes = 0; this.pendingKeys.clear(); this.coalesced.clear(); for (const job of pending) job.reject(error);
  }
  private settleReady(): void {
    if (this.queue.length || this.backpressured || this.failure || this.disposed) return;
    for (const waiter of this.readyWaiters.splice(0)) waiter.generation === this.currentGeneration ? waiter.resolve() : waiter.reject(new TransportError("cancelled", "stale graphics generation"));
  }
  private rejectReady(error: TransportError): void { for (const waiter of this.readyWaiters.splice(0)) waiter.reject(error); }
  private clearPumpTimer(): void { if (this.pumpTimer !== undefined) { this.scheduler.clearTimeout(this.pumpTimer); this.pumpTimer = undefined; } }
  private clearDrainWait(): void {
    if (this.drainListener) this.sink.removeListener("drain", this.drainListener);
    this.drainListener = undefined;
    if (this.drainTimer !== undefined) this.scheduler.clearTimeout(this.drainTimer);
    this.drainTimer = undefined;
  }
}
