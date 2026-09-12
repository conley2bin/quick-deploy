import { spawnSync } from "node:child_process";
import type { LoadedImage } from "./images.ts";
import { MAX_PREVIEW_PNG_BYTES } from "./images.ts";
import {
  BoundedTransport,
  completeUploadTransaction,
  type TransportLimits,
  type TransportScheduler,
  type TransportSink,
} from "./transport.ts";
import { deleteImage, grid, placement } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

export const MAX_ACTIVE_IMAGES = 64;
export const MAX_RESIDENT_PNG_BYTES = 12 * 1024 * 1024;
export const MAX_PLACEMENTS_PER_IMAGE = 80;
export const MAX_PLACEMENT_CATALOG_BYTES = 80 * 56;

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
  ready: boolean;
  placements: Map<string, PreparedPlacement>;
  cell: CellSize;
  error?: string;
};

export type BasicSink = { write(value: string): boolean };

export interface TerminalImageOptions {
  maxResidentPngBytes?: number;
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
  private readonly maxResidentPngBytes: number;
  private residentPngBytes = 0;

  constructor(
    private readonly allocate: () => number,
    private readonly cellSize: () => CellSize,
    sink: TransportSink | BasicSink = process.stdout,
    private readonly env: NodeJS.ProcessEnv = process.env,
    private readonly capable = supportsKitty(env),
    options: TerminalImageOptions = {},
  ) {
    this.maxResidentPngBytes = options.maxResidentPngBytes ?? MAX_RESIDENT_PNG_BYTES;
    if (!Number.isSafeInteger(this.maxResidentPngBytes) || this.maxResidentPngBytes < 1) {
      throw new Error("maxResidentPngBytes must be a positive safe integer");
    }
    this.transport = new BoundedTransport(normalizeSink(sink), options.transportLimits, options.scheduler);
  }

  available(): boolean { return this.capable; }
  has(logicalId: string): boolean { return this.images.get(logicalId)?.ready === true; }
  count(): number { return this.images.size; }
  residentBytes(): number { return this.residentPngBytes; }
  pendingJobs(): number { return this.transport.pendingJobs; }
  failure(logicalId: string): string | undefined { return this.images.get(logicalId)?.error; }

  async prepare(logicalId: string, image: LoadedImage): Promise<void> {
    const existing = this.images.get(logicalId);
    if (existing) {
      if (existing.image.hash !== image.hash) throw new Error(`immutable image resource '${logicalId}' cannot be overwritten`);
      if (existing.error) throw new Error(existing.error);
      if (!existing.ready) throw new Error(`image resource '${logicalId}' is still pending`);
      return;
    }
    if (this.images.size >= MAX_ACTIVE_IMAGES) throw new Error(`inline image capacity reached (${MAX_ACTIVE_IMAGES})`);
    if (image.png.length > MAX_PREVIEW_PNG_BYTES) {
      throw new Error(`derived PNG preview is ${image.png.length} bytes; limit is ${MAX_PREVIEW_PNG_BYTES} bytes`);
    }
    if (this.residentPngBytes + image.png.length > this.maxResidentPngBytes) {
      throw new Error(`resident PNG budget reached (${this.maxResidentPngBytes} bytes)`);
    }

    const id = this.id();
    const cell = this.cellSize();
    const placements = placementCatalog(image, cell);
    if (placements.size > MAX_PLACEMENTS_PER_IMAGE) {
      throw new Error(`placement catalog has ${placements.size} entries; limit is ${MAX_PLACEMENTS_PER_IMAGE}`);
    }
    const state: StoredImage = { image, id, ready: false, placements, cell };
    this.images.set(logicalId, state);
    this.residentPngBytes += image.png.length;
    const generation = this.transport.generation;
    const upload = completeUploadTransaction(image.png, id, this.inTmux());
    const placementCommands = [...placements.values()].map((candidate) =>
      placement(id, candidate.columns, candidate.rows, this.inTmux(), candidate.placementId)).join("");
    const placementBytes = Buffer.byteLength(placementCommands);
    if (placementBytes > MAX_PLACEMENT_CATALOG_BYTES) {
      state.error = `placement catalog is ${placementBytes} bytes; limit is ${MAX_PLACEMENT_CATALOG_BYTES} bytes`;
      throw new Error(state.error);
    }
    try {
      await this.transport.enqueue(generation, { transaction: upload, key: `upload:${id}:${image.hash}` });
      await this.transport.enqueue(generation, { transaction: placementCommands });
      await this.transport.ready(generation);
      if (generation !== this.transport.generation || this.images.get(logicalId) !== state) {
        throw new Error("image preparation completed in an abandoned generation");
      }
      state.ready = true;
    } catch (error) {
      if (this.images.get(logicalId) === state) {
        state.error = error instanceof Error ? error.message : String(error);
      }
      throw error;
    }
  }

  /** Pure synchronous render: every possible width placement was prepared first. */
  render(logicalId: string, availableWidth: number): string[] {
    const state = this.images.get(logicalId);
    if (!state?.ready || state.error || !this.capable) return [];
    const currentCell = this.cellSize();
    if (currentCell.widthPx !== state.cell.widthPx || currentCell.heightPx !== state.cell.heightPx) {
      state.error = `terminal cell dimensions changed from ${state.cell.widthPx}x${state.cell.heightPx} px to ${currentCell.widthPx}x${currentCell.heightPx} px; reload required`;
      return [];
    }
    const size = geometry(state.image, availableWidth, state.cell);
    const prepared = state.placements.get(`${size.columns}:${size.rows}`);
    if (!prepared) {
      state.error = `no prepared placement for ${size.columns}x${size.rows}`;
      return [];
    }
    return grid(size.columns, size.rows, state.id, prepared.placementId);
  }

  /** Cancel unsent work while retaining successfully prepared resources in this runtime. */
  reconcile(): void {
    this.transport.cancel("image branch reconciliation", { retainAccepted: true });
    for (const [logicalId, state] of this.images) {
      if (state.ready) continue;
      this.images.delete(logicalId);
      this.residentPngBytes -= state.image.png.length;
    }
  }

  /** Invalidate late work, remove owned terminal resources in order, and optionally dispose. */
  async clear(dispose = false): Promise<void> {
    const ids = [...this.images.values()].map(({ id }) => id);
    this.transport.cancel("image session reset");
    this.images.clear();
    this.residentPngBytes = 0;
    if (this.capable) {
      const generation = this.transport.generation;
      await Promise.allSettled(ids.map((id) => this.transport.enqueue(generation, {
        transaction: deleteImage(id, this.inTmux()),
      })));
      await this.transport.ready(generation).catch(() => undefined);
    }
    if (dispose) this.transport.dispose();
  }

  private id(): number {
    let id = this.allocate() >>> 0;
    while (!id || this.usedIds.has(id)) id = this.allocate() >>> 0;
    this.usedIds.add(id);
    return id;
  }

  private inTmux(): boolean {
    return Boolean(this.env.TMUX || this.env.TERM?.startsWith("tmux"));
  }
}

function normalizeSink(sink: TransportSink | BasicSink): TransportSink {
  if ("on" in sink && typeof sink.on === "function" && "removeListener" in sink && typeof sink.removeListener === "function") {
    return sink as TransportSink;
  }
  return {
    write: (value) => sink.write(value),
    on() { return this; },
    removeListener() { return this; },
  };
}
