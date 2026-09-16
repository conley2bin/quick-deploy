import { spawnSync } from "node:child_process";
import type { LoadedImage } from "./images.ts";
import { MAX_FULL_PNG_BYTES } from "./images.ts";
import {
  BoundedTransport,
  completeImageTransaction,
  uploadTransactionBytes,
  TransportError,
  type TransportLimits,
  type TransportScheduler,
  type TransportSink,
} from "./transport.ts";
import { deleteImage, grid, placement } from "../vendor/pi-tmux-images/kitty-placeholder.ts";
import type { ViewerState } from "./viewers.ts";

export const MAX_ACTIVE_IMAGES = 64;
export const MAX_READ_IMAGES = 16;
export const MAX_RESIDENT_PNG_BYTES = 64 * 1024 * 1024;
export const MAX_PLACEMENTS_PER_IMAGE = 80;
export const MAX_PLACEMENT_CATALOG_BYTES = 80 * 56;
export const MAX_IMAGE_TRANSACTION_BYTES = 44 * 1024 * 1024;

type Owner = "inline" | "read";
export type CellSize = { widthPx: number; heightPx: number };
type TmuxResult = { status: number | null; stdout: string | null };
type TmuxCommand = (
  command: string,
  args: string[],
  options: { encoding: "utf8"; timeout: number },
) => TmuxResult;

type PreparedPlacement = { columns: number; rows: number; placementId: number };
type StoredImage = {
  image: LoadedImage;
  id: number;
  placements: Map<string, PreparedPlacement>;
  cell: CellSize;
  sent: Set<string>;
  terminalResource: boolean;
  releasing: boolean;
  uploading?: Promise<void>;
  releasePromise?: Promise<void>;
  error?: string;
};

export type BasicSink = { write(value: Buffer): boolean };

export interface TerminalImageOptions {
  /** Inline-owner resident PNG budget. */
  maxResidentPngBytes?: number;
  /** Read-owner resident PNG budget; independent from the inline budget. */
  maxReadResidentPngBytes?: number;
  transportLimits?: Partial<TransportLimits>;
  scheduler?: TransportScheduler;
}

export function supportsKitty(env: NodeJS.ProcessEnv = process.env, probe = probeTmux): boolean {
  const tmux = Boolean(env.TMUX || env.TERM?.startsWith("tmux"));
  const program = env.TERM_PROGRAM?.toLowerCase();
  const outer = Boolean(env.KITTY_WINDOW_ID || env.GHOSTTY_RESOURCES_DIR || env.WEZTERM_PANE || ["kitty", "ghostty", "wezterm"].includes(program || ""));
  return outer && (!tmux || probe(env));
}

export function probeTmux(env: NodeJS.ProcessEnv = process.env, run: TmuxCommand = spawnSync): boolean {
  const pane = env.TMUX_PANE?.trim();
  if (!pane || !/^%\d+$/u.test(pane)) return false;
  try {
    const result = run("tmux", ["show-options", "-Apv", "-t", pane, "allow-passthrough"], {
      encoding: "utf8",
      timeout: 1_000,
    });
    return result.status === 0 && /^(on|all|yes|true|1)$/iu.test(result.stdout?.trim() ?? "");
  } catch {
    return false;
  }
}

export function geometry(image: LoadedImage, availableWidth: number, cell: CellSize): { columns: number; rows: number } {
  const columnsLimit = Math.max(1, Math.min(80, availableWidth));
  const scale = Math.min((columnsLimit * cell.widthPx) / image.width, (24 * cell.heightPx) / image.height, 1);
  return {
    columns: Math.max(1, Math.ceil(image.width * scale / cell.widthPx)),
    rows: Math.max(1, Math.ceil(image.height * scale / cell.heightPx)),
  };
}

function placementCatalog(image: LoadedImage, cell: CellSize): Map<string, PreparedPlacement> {
  const catalog = new Map<string, PreparedPlacement>();
  for (let availableWidth = 1; availableWidth <= 80; availableWidth++) {
    const size = geometry(image, availableWidth, cell);
    const signature = `${size.columns}:${size.rows}`;
    if (!catalog.has(signature)) catalog.set(signature, { ...size, placementId: catalog.size + 1 });
  }
  return catalog;
}

export class TerminalImages {
  private readonly images = new Map<string, StoredImage>();
  private readonly usedIds = new Set<number>();
  private readonly transport: BoundedTransport;
  private readonly residentLimits: Record<Owner, number>;
  private readonly residentPngBytes: Record<Owner, number> = { inline: 0, read: 0 };
  private viewerManaged = false;
  private viewerReady = true;
  private viewerEpoch = "legacy";
  private viewerReason = "";
  private viewerReceivers: readonly string[] = [];

  constructor(
    private readonly allocate: () => number,
    private readonly cellSize: () => CellSize,
    sink: TransportSink | BasicSink = process.stdout,
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly capable = supportsKitty(env),
    options: TerminalImageOptions = {},
  ) {
    this.residentLimits = {
      inline: options.maxResidentPngBytes ?? MAX_RESIDENT_PNG_BYTES,
      read: options.maxReadResidentPngBytes ?? MAX_RESIDENT_PNG_BYTES,
    };
    for (const [owner, limit] of Object.entries(this.residentLimits)) {
      if (!Number.isSafeInteger(limit) || limit < 1) throw new Error(`${owner} resident PNG budget must be a positive safe integer`);
    }
    this.transport = new BoundedTransport(normalizeSink(sink), options.transportLimits, options.scheduler);
  }

  available(): boolean { return this.viewerManaged || this.capable; }
  has(logicalId: string): boolean { const state = this.images.get(logicalId); return Boolean(state && !state.error && !state.releasing); }
  count(owner?: Owner): number { return owner ? this.ownerCount(owner) : this.images.size; }
  residentBytes(owner?: Owner): number { return owner ? this.residentPngBytes[owner] : this.residentPngBytes.inline + this.residentPngBytes.read; }
  pendingJobs(): number { return this.transport.pendingJobs; }
  failure(logicalId: string): string | undefined {
    const image = this.images.get(logicalId);
    if (!image) return undefined;
    if (image.error) return image.error;
    if (this.viewerManaged && !this.renderReady(image)) return this.viewerReason || "waiting for a compatible visible viewer";
    return undefined;
  }

  /** Viewer-aware mode makes live snapshots authoritative instead of constructor environment. */
  setViewerManaged(managed: boolean): void {
    this.viewerManaged = managed;
    if (managed) this.pauseViewers();
  }

  /** Called whenever the monitor stops, so the next resource cannot use a stale snapshot. */
  pauseViewers(reason = "waiting for a compatible visible viewer"): void {
    if (!this.viewerManaged) return;
    this.viewerReady = false;
    this.viewerEpoch = "paused";
    this.viewerReason = reason;
    this.viewerReceivers = [];
  }

  async setViewer(snapshot: ViewerState): Promise<void> {
    const changed = snapshot.epoch !== this.viewerEpoch || snapshot.ready !== this.viewerReady;
    this.viewerReady = snapshot.ready;
    this.viewerEpoch = snapshot.epoch;
    this.viewerReason = snapshot.reason;
    this.viewerReceivers = snapshot.receivers;

    if (snapshot.attached) {
      const attached = new Set(snapshot.attached);
      for (const image of this.images.values()) for (const identity of image.sent) if (!attached.has(identity)) image.sent.delete(identity);
    }
    if (changed) {
      for (const logicalId of this.images.keys()) {
        const owner = this.ownerOf(logicalId);
        this.transport.cancelResource(owner, this.uploadScope(logicalId), "viewer snapshot changed before upload");
      }
    }
    if (!snapshot.ready || snapshot.receivers.length === 0) return;
    for (const [logicalId, image] of this.images) await this.ensureUploaded(logicalId, image);
  }

  async prepare(logicalId: string, image: LoadedImage): Promise<void> {
    const existing = this.images.get(logicalId);
    if (existing) {
      if (existing.image.hash !== image.hash) throw new Error(`immutable image resource '${logicalId}' cannot be overwritten`);
      if (existing.error) throw new Error(existing.error);
      if (!existing.releasing) await this.ensureUploaded(logicalId, existing);
      return;
    }

    const owner = this.ownerOf(logicalId);
    const ownerLimit = owner === "read" ? MAX_READ_IMAGES : MAX_ACTIVE_IMAGES;
    if (this.ownerCount(owner) >= ownerLimit) throw new Error(`${owner} image capacity reached (${ownerLimit})`);
    if (image.png.length > MAX_FULL_PNG_BYTES) throw new Error(`full PNG is ${image.png.length} bytes; limit is ${MAX_FULL_PNG_BYTES} bytes`);
    if (this.residentPngBytes[owner] + image.png.length > this.residentLimits[owner]) {
      const label = owner === "inline" ? "resident PNG budget" : "read resident PNG budget";
      throw new Error(`${label} reached (${this.residentLimits[owner]} bytes)`);
    }

    const id = this.id();
    const cell = this.cellSize();
    const placements = placementCatalog(image, cell);
    if (placements.size > MAX_PLACEMENTS_PER_IMAGE) {
      this.usedIds.delete(id);
      throw new Error(`placement catalog has ${placements.size} entries; limit is ${MAX_PLACEMENTS_PER_IMAGE}`);
    }
    const placementCommands = this.placementCommands(id, placements);
    const placementBytes = Buffer.byteLength(placementCommands);
    if (placementBytes > MAX_PLACEMENT_CATALOG_BYTES) {
      this.usedIds.delete(id);
      throw new Error(`placement catalog is ${placementBytes} bytes; limit is ${MAX_PLACEMENT_CATALOG_BYTES} bytes`);
    }
    const transactionBytes = uploadTransactionBytes(image.png.length, id, this.inTmux()) + placementBytes;
    if (transactionBytes > MAX_IMAGE_TRANSACTION_BYTES) {
      this.usedIds.delete(id);
      throw new Error(`full image transaction is ${transactionBytes} bytes; limit is ${MAX_IMAGE_TRANSACTION_BYTES} bytes`);
    }

    const stored: StoredImage = {
      image,
      id,
      placements,
      cell,
      sent: new Set(),
      terminalResource: false,
      releasing: false,
    };
    this.images.set(logicalId, stored);
    this.residentPngBytes[owner] += image.png.length;
    await this.ensureUploaded(logicalId, stored, placementCommands, transactionBytes);
  }

  private async ensureUploaded(logicalId: string, image: StoredImage, catalog?: string, wireBytes?: number): Promise<void> {
    while (this.images.get(logicalId) === image && !image.releasing && !image.error) {
      const receivers = this.viewerManaged ? this.viewerReceivers : ["legacy"];
      if ((this.viewerManaged && !this.viewerReady) || receivers.length === 0 || receivers.every((identity) => image.sent.has(identity))) return;
      if (image.uploading) {
        await image.uploading;
        continue;
      }
      const epoch = this.viewerEpoch;
      const targets = receivers.filter((identity) => !image.sent.has(identity));
      const operation = this.upload(logicalId, image, epoch, targets, catalog, wireBytes);
      image.uploading = operation;
      try {
        await operation;
      } finally {
        if (image.uploading === operation) image.uploading = undefined;
      }
      catalog = undefined;
      wireBytes = undefined;
    }
  }

  private async upload(
    logicalId: string,
    image: StoredImage,
    epoch: string,
    targets: readonly string[],
    catalog?: string,
    wireBytes?: number,
  ): Promise<void> {
    const owner = this.ownerOf(logicalId);
    const placementCommands = catalog ?? this.placementCommands(image.id, image.placements);
    const transactionBytes = wireBytes ?? uploadTransactionBytes(image.image.png.length, image.id, this.inTmux()) + Buffer.byteLength(placementCommands);
    const generation = this.transport.generation;
    try {
      await this.transport.enqueue(generation, {
        transaction: () => completeImageTransaction(image.image.png, image.id, this.inTmux(), placementCommands),
        bytes: transactionBytes,
        owner,
        resource: this.uploadScope(logicalId),
      });
      image.terminalResource = true;
      await this.transport.ready(generation);
      if (this.images.get(logicalId) !== image || image.releasing) return;
      if (this.viewerManaged && (!this.viewerReady || this.viewerEpoch !== epoch
        || targets.some((identity) => !this.viewerReceivers.includes(identity)))) return;
      for (const identity of targets) image.sent.add(identity);
    } catch (error) {
      if (error instanceof TransportError && error.code === "cancelled") return;
      if (this.images.get(logicalId) === image) image.error = error instanceof Error ? error.message : String(error);
      throw error;
    }
  }

  /** Pure synchronous render: every possible width placement was prepared first. */
  render(logicalId: string, availableWidth: number): string[] {
    const image = this.images.get(logicalId);
    if (!image || image.error || image.releasing || !this.renderReady(image)) return [];
    const currentCell = this.cellSize();
    if (currentCell.widthPx !== image.cell.widthPx || currentCell.heightPx !== image.cell.heightPx) {
      image.error = `terminal cell dimensions changed from ${image.cell.widthPx}x${image.cell.heightPx} px to ${currentCell.widthPx}x${currentCell.heightPx} px; reload required`;
      return [];
    }
    const size = geometry(image.image, availableWidth, image.cell);
    const prepared = image.placements.get(`${size.columns}:${size.rows}`);
    if (!prepared) {
      image.error = `no prepared placement for ${size.columns}x${size.rows}`;
      return [];
    }
    return grid(size.columns, size.rows, image.id, prepared.placementId);
  }

  /** Release one resource through the shared queue without touching another owner. */
  async release(logicalId: string): Promise<void> {
    const image = this.images.get(logicalId);
    if (!image) return;
    if (image.releasePromise) return image.releasePromise;
    const owner = this.ownerOf(logicalId);
    image.releasing = true;
    this.transport.cancelResource(owner, this.uploadScope(logicalId), "image resource released");
    const release = (async () => {
      try {
        await image.uploading?.catch((error: unknown) => {
          if (!(error instanceof TransportError && error.code === "cancelled")) throw error;
        });
        if (image.terminalResource) {
          const generation = this.transport.generation;
          await this.transport.enqueue(generation, {
            transaction: deleteImage(image.id, this.inTmux()),
            owner,
            resource: `delete:${logicalId}`,
          });
          await this.transport.ready(generation);
        }
        if (this.images.get(logicalId) === image) this.forget(logicalId, image, owner);
      } catch (error) {
        image.releasing = false;
        image.error = error instanceof Error ? error.message : String(error);
        throw error;
      }
    })();
    image.releasePromise = release;
    return release;
  }

  async resetOwner(owner: Owner): Promise<void> {
    const resources = [...this.images.keys()].filter((logicalId) => this.ownerOf(logicalId) === owner);
    for (const logicalId of resources) this.transport.cancelResource(owner, this.uploadScope(logicalId), `${owner} graphics reset`);
    for (const logicalId of resources) await this.release(logicalId);
  }

  async retainOwner(owner: Owner, logicalIds: ReadonlySet<string>): Promise<void> {
    for (const logicalId of [...this.images.keys()]) {
      if (this.ownerOf(logicalId) === owner && !logicalIds.has(logicalId)) await this.release(logicalId);
    }
  }

  /** Final backend teardown. Ordinary owner resets must use resetOwner(). */
  async clear(dispose = false): Promise<void> {
    try {
      if (this.transport.terminalFailure) throw this.transport.terminalFailure;
      this.transport.cancel("image backend reset", { retainAccepted: true });
      for (const image of this.images.values()) image.releasing = true;
      for (const image of this.images.values()) await image.uploading?.catch(() => undefined);
      if (!this.capable && !this.viewerManaged) {
        for (const [logicalId, image] of [...this.images]) this.forget(logicalId, image, this.ownerOf(logicalId));
        return;
      }
      const generation = this.transport.generation;
      for (const [logicalId, image] of [...this.images]) {
        if (image.terminalResource) {
          await this.transport.enqueue(generation, {
            transaction: deleteImage(image.id, this.inTmux()),
            owner: this.ownerOf(logicalId),
            resource: `delete:${logicalId}`,
          });
          await this.transport.ready(generation);
        }
        if (this.images.get(logicalId) === image) this.forget(logicalId, image, this.ownerOf(logicalId));
      }
    } finally {
      if (dispose) this.transport.dispose();
    }
  }

  private renderReady(image: StoredImage): boolean {
    if (!this.viewerManaged) return this.capable && image.sent.has("legacy");
    return this.viewerReady && this.viewerReceivers.length > 0 && this.viewerReceivers.every((identity) => image.sent.has(identity));
  }

  private placementCommands(id: number, placements: Map<string, PreparedPlacement>): string {
    return [...placements.values()].map((candidate) =>
      placement(id, candidate.columns, candidate.rows, this.inTmux(), candidate.placementId)).join("");
  }

  private forget(logicalId: string, image: StoredImage, owner: Owner): void {
    this.images.delete(logicalId);
    this.residentPngBytes[owner] -= image.image.png.length;
    image.placements.clear();
    image.sent.clear();
    this.usedIds.delete(image.id);
  }

  private ownerOf(logicalId: string): Owner { return logicalId.startsWith("read:") ? "read" : "inline"; }
  private ownerCount(owner: Owner): number { return [...this.images.keys()].filter((logicalId) => this.ownerOf(logicalId) === owner).length; }
  private uploadScope(logicalId: string): string { return `upload:${logicalId}`; }

  private id(): number {
    let id = this.allocate() >>> 0;
    while (!id || this.usedIds.has(id)) id = this.allocate() >>> 0;
    this.usedIds.add(id);
    return id;
  }

  private inTmux(): boolean { return Boolean(this.env.TMUX || this.env.TERM?.startsWith("tmux")); }
}

function normalizeSink(sink: TransportSink | BasicSink): TransportSink {
  if ("on" in sink && typeof sink.on === "function" && "removeListener" in sink && typeof sink.removeListener === "function") return sink as TransportSink;
  return {
    write: (value) => sink.write(value),
    on() { return this; },
    removeListener() { return this; },
  };
}
