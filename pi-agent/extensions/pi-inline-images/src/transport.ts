import { kitty, upload } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

export const DEFAULT_MAX_TRANSACTION_BYTES = 1 * 1024 * 1024;
export const DEFAULT_MAX_QUEUED_BYTES = 8 * 1024 * 1024;
export const DEFAULT_MAX_QUEUED_JOBS = 64;
export const DEFAULT_MAX_RESOURCES = 64;
export const DEFAULT_MIN_INTERVAL_MS = 50;
export const DEFAULT_DRAIN_TIMEOUT_MS = 5_000;

export type TransportErrorCode =
  | "cancelled"
  | "closed"
  | "drain-timeout"
  | "invalid"
  | "limit"
  | "sink";

export class TransportError extends Error {
  constructor(readonly code: TransportErrorCode, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "TransportError";
  }
}

export interface TransportSink {
  readonly writableNeedDrain?: boolean;
  write(value: string): boolean;
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
}

export interface TransportRequest {
  /** A complete protocol transaction. Multipart image chunks must all be present. */
  transaction: string;
  /** Retains successful identity within this generation and deduplicates repeats. */
  key?: string;
  /** Replaces an older unsent transaction with the same coalescing identity. */
  coalesceKey?: string;
}

export interface TransportResult {
  /** "accepted" means accepted by the writable, never acknowledged by the terminal. */
  status: "accepted" | "deduplicated";
  generation: number;
  bytes: number;
}

type PendingJob = {
  transaction: string;
  bytes: number;
  key?: string;
  coalesceKey?: string;
  generation: number;
  promise: Promise<TransportResult>;
  resolve: (result: TransportResult) => void;
  reject: (error: Error) => void;
};

type ReadyWaiter = {
  generation: number;
  resolve: () => void;
  reject: (error: Error) => void;
};

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
});

/**
 * Build one indivisible direct-upload transaction.
 *
 * Kitty requires every continuation APC to follow the preceding image chunk with
 * no intervening graphics command. Passing this entire string to one write()
 * call prevents another JavaScript writer from running between its chunks.
 */
export function completeUploadTransaction(png: Buffer | string, imageId: number, inTmux: boolean): string {
  if (typeof png === "string") return upload(png, imageId, inTmux).join("");
  const rawChunkBytes = 3 * 4096 / 4;
  let transaction = "";
  const chunkCount = Math.max(1, Math.ceil(png.length / rawChunkBytes));
  for (let index = 0; index < chunkCount; index++) {
    const payload = png.subarray(index * rawChunkBytes, (index + 1) * rawChunkBytes).toString("base64");
    const first = index === 0 ? `a=t,f=100,i=${imageId},q=2,` : "";
    transaction += kitty(`${first}m=${index + 1 < chunkCount ? 1 : 0};${payload}`, inTmux);
  }
  return transaction;
}

/** Owns and serializes every graphics write made by one extension runtime. */
export class BoundedTransport {
  private readonly limits: TransportLimits;
  private readonly queue: PendingJob[] = [];
  private readonly pendingKeys = new Map<string, PendingJob>();
  private readonly coalesced = new Map<string, PendingJob>();
  private readonly acceptedKeys = new Set<string>();
  private readonly readyWaiters: ReadyWaiter[] = [];
  private queuedBytes = 0;
  private currentGeneration = 0;
  private lastWriteAt: number | undefined;
  private pumpTimer: unknown;
  private drainTimer: unknown;
  private drainListener?: () => void;
  private backpressured = false;
  private failure?: TransportError;
  private disposed = false;

  private readonly errorListener = (error: Error) => {
    this.fail(new TransportError("sink", `graphics sink error: ${error.message}`, { cause: error }));
  };
  private readonly closeListener = () => {
    this.fail(new TransportError("closed", "graphics sink closed"));
  };

  constructor(
    private readonly sink: TransportSink,
    limits: Partial<TransportLimits> = {},
    private readonly scheduler: TransportScheduler = defaultScheduler,
  ) {
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
  get pendingBytes(): number { return this.queuedBytes; }
  get retainedResources(): number { return this.acceptedKeys.size + this.pendingKeys.size; }

  enqueue(generation: number, request: TransportRequest): Promise<TransportResult> {
    if (this.disposed) return Promise.reject(new TransportError("closed", "graphics transport disposed"));
    if (this.failure) return Promise.reject(this.failure);
    if (generation !== this.currentGeneration) {
      return Promise.reject(new TransportError("cancelled", `stale graphics generation ${generation}; current generation is ${this.currentGeneration}`));
    }
    if (!request.transaction) return Promise.reject(new TransportError("invalid", "graphics transaction must not be empty"));
    if (request.key && request.coalesceKey) {
      return Promise.reject(new TransportError("invalid", "a transaction cannot be both retained and coalesced"));
    }

    const bytes = Buffer.byteLength(request.transaction, "utf8");
    if (bytes > this.limits.maxTransactionBytes) {
      return Promise.reject(new TransportError("limit", `graphics transaction is ${bytes} bytes; limit is ${this.limits.maxTransactionBytes} bytes`));
    }

    if (request.key) {
      if (this.acceptedKeys.has(request.key)) {
        return Promise.resolve({ status: "deduplicated", generation: this.currentGeneration, bytes: 0 });
      }
      const pending = this.pendingKeys.get(request.key);
      if (pending) return pending.promise;
      if (this.acceptedKeys.size + this.pendingKeys.size >= this.limits.maxResources) {
        return Promise.reject(new TransportError("limit", `graphics resource limit reached (${this.limits.maxResources})`));
      }
    }

    if (request.coalesceKey) {
      const pending = this.coalesced.get(request.coalesceKey);
      if (pending) {
        const replacementBytes = this.queuedBytes - pending.bytes + bytes;
        if (replacementBytes > this.limits.maxQueuedBytes) {
          return Promise.reject(new TransportError("limit", `queued graphics bytes would exceed ${this.limits.maxQueuedBytes}`));
        }
        pending.transaction = request.transaction;
        pending.bytes = bytes;
        this.queuedBytes = replacementBytes;
        return pending.promise;
      }
    }

    if (this.queue.length >= this.limits.maxQueuedJobs) {
      return Promise.reject(new TransportError("limit", `queued graphics job limit reached (${this.limits.maxQueuedJobs})`));
    }
    if (this.queuedBytes + bytes > this.limits.maxQueuedBytes) {
      return Promise.reject(new TransportError("limit", `queued graphics bytes would exceed ${this.limits.maxQueuedBytes}`));
    }

    let resolve!: (result: TransportResult) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<TransportResult>((yes, no) => { resolve = yes; reject = no; });
    const job: PendingJob = {
      transaction: request.transaction,
      bytes,
      key: request.key,
      coalesceKey: request.coalesceKey,
      generation: this.currentGeneration,
      promise,
      resolve,
      reject,
    };
    this.queue.push(job);
    this.queuedBytes += bytes;
    if (job.key) this.pendingKeys.set(job.key, job);
    if (job.coalesceKey) this.coalesced.set(job.coalesceKey, job);
    this.resume();
    return promise;
  }

  /** Wait until admitted work is written and a false-returning sink has drained. */
  ready(generation: number): Promise<void> {
    if (this.disposed) return Promise.reject(new TransportError("closed", "graphics transport disposed"));
    if (this.failure) return Promise.reject(this.failure);
    if (generation !== this.currentGeneration) {
      return Promise.reject(new TransportError("cancelled", `stale graphics generation ${generation}; current generation is ${this.currentGeneration}`));
    }
    if (this.queue.length === 0 && !this.backpressured) return Promise.resolve();
    const promise = new Promise<void>((resolve, reject) => {
      this.readyWaiters.push({ generation, resolve, reject });
    });
    if (this.backpressured) this.waitForDrain();
    else this.resume();
    return promise;
  }

  /** Cancel only unsent work and start a fresh generation. */
  cancel(reason = "graphics generation cancelled", options: { retainAccepted?: boolean } = {}): void {
    if (this.disposed) return;
    this.currentGeneration++;
    this.clearPumpTimer();
    this.clearDrainWait();
    this.rejectQueue(new TransportError("cancelled", reason));
    this.rejectReady(new TransportError("cancelled", reason));
    if (!options.retainAccepted) this.acceptedKeys.clear();
    this.backpressured = this.sink.writableNeedDrain ?? this.backpressured;
  }

  /** Permanently release sink listeners. The transport cannot be reused. */
  dispose(reason = "graphics transport disposed"): void {
    if (this.disposed) return;
    this.cancel(reason);
    this.disposed = true;
    this.sink.removeListener("error", this.errorListener);
    this.sink.removeListener("close", this.closeListener);
  }

  private resume(): void {
    if (this.failure || this.disposed || this.queue.length === 0) return;
    if (this.backpressured) {
      if (this.sink.writableNeedDrain === false) this.backpressured = false;
      else {
        this.waitForDrain();
        return;
      }
    }
    const now = this.scheduler.now();
    const delay = this.lastWriteAt === undefined ? 0 : Math.max(0, this.lastWriteAt + this.limits.minIntervalMs - now);
    if (delay > 0) this.schedulePump(delay);
    else this.pump();
  }

  private pump(): void {
    this.pumpTimer = undefined;
    if (this.failure || this.disposed || this.backpressured) return;
    const job = this.queue[0];
    if (!job) return;
    if (job.generation !== this.currentGeneration) {
      this.rejectQueue(new TransportError("cancelled", "stale graphics generation"));
      return;
    }

    let accepted: boolean;
    try {
      accepted = this.sink.write(job.transaction);
      if (typeof accepted !== "boolean") throw new TypeError("graphics sink write() did not return a boolean");
    } catch (error) {
      this.fail(new TransportError("sink", "graphics sink write failed", { cause: error }));
      return;
    }
    if (this.failure) return;

    this.queue.shift();
    this.queuedBytes -= job.bytes;
    if (job.key) {
      this.pendingKeys.delete(job.key);
      this.acceptedKeys.add(job.key);
    }
    if (job.coalesceKey) this.coalesced.delete(job.coalesceKey);
    this.lastWriteAt = this.scheduler.now();
    job.resolve({ status: "accepted", generation: job.generation, bytes: job.bytes });

    if (!accepted) {
      this.backpressured = true;
      this.waitForDrain();
    } else if (this.queue.length > 0) {
      this.schedulePump(this.limits.minIntervalMs);
    } else {
      this.settleReady();
    }
  }

  private schedulePump(delayMs: number): void {
    if (this.pumpTimer !== undefined) return;
    this.pumpTimer = this.scheduler.setTimeout(() => this.pump(), delayMs);
  }

  private waitForDrain(): void {
    if (this.drainListener || this.failure || this.disposed || (this.queue.length === 0 && this.readyWaiters.length === 0)) return;
    const generation = this.currentGeneration;
    const onDrain = () => {
      this.clearDrainWait();
      if (generation !== this.currentGeneration || this.failure || this.disposed) return;
      this.backpressured = false;
      this.resume();
      this.settleReady();
    };
    this.drainListener = onDrain;
    this.sink.on("drain", onDrain);
    this.drainTimer = this.scheduler.setTimeout(() => {
      if (this.drainListener !== onDrain) return;
      this.fail(new TransportError("drain-timeout", `graphics sink did not drain within ${this.limits.drainTimeoutMs} ms`));
    }, this.limits.drainTimeoutMs);
  }

  private fail(error: TransportError): void {
    if (this.failure || this.disposed) return;
    this.failure = error;
    this.currentGeneration++;
    this.clearPumpTimer();
    this.clearDrainWait();
    this.rejectQueue(error);
    this.rejectReady(error);
  }

  private rejectQueue(error: TransportError): void {
    const pending = this.queue.splice(0);
    this.queuedBytes = 0;
    this.pendingKeys.clear();
    this.coalesced.clear();
    for (const job of pending) job.reject(error);
  }

  private settleReady(): void {
    if (this.queue.length > 0 || this.backpressured || this.failure || this.disposed) return;
    const ready = this.readyWaiters.splice(0);
    for (const waiter of ready) {
      if (waiter.generation === this.currentGeneration) waiter.resolve();
      else waiter.reject(new TransportError("cancelled", "stale graphics generation"));
    }
  }

  private rejectReady(error: TransportError): void {
    for (const waiter of this.readyWaiters.splice(0)) waiter.reject(error);
  }

  private clearPumpTimer(): void {
    if (this.pumpTimer === undefined) return;
    this.scheduler.clearTimeout(this.pumpTimer);
    this.pumpTimer = undefined;
  }

  private clearDrainWait(): void {
    if (this.drainListener) this.sink.removeListener("drain", this.drainListener);
    this.drainListener = undefined;
    if (this.drainTimer !== undefined) this.scheduler.clearTimeout(this.drainTimer);
    this.drainTimer = undefined;
  }
}
