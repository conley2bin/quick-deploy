import { spawnSync } from "node:child_process";
import type { LoadedImage } from "./images.ts";
import { deleteImage, deletePlacement, grid, placement, upload } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

export type Sink = { write(value: string): unknown };
export type CellSize = { widthPx: number; heightPx: number };
type TmuxResult = { status: number | null; stdout: string | null };
type TmuxCommand = (
  command: string,
  args: string[],
  options: { encoding: "utf8"; timeout: number },
) => TmuxResult;

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

export class TerminalImages {
  private images = new Map<string, LoadedImage>();
  private ids = new Map<string, number>();
  private uploaded = new Map<number, string>();
  private placements = new Map<number, string>();

  constructor(
    private allocate: () => number,
    private cellSize: () => CellSize,
    private sink: Sink = process.stdout,
    private env: NodeJS.ProcessEnv = process.env,
    private capable = supportsKitty(env),
  ) {}

  available(): boolean { return this.capable; }
  set(logicalId: string, image: LoadedImage): void {
    const existing = this.images.get(logicalId);
    if (existing && existing.hash !== image.hash) throw new Error(`immutable image resource '${logicalId}' cannot be overwritten`);
    this.images.set(logicalId, image);
  }
  has(logicalId: string): boolean { return this.images.has(logicalId); }
  count(): number { return this.images.size; }

  private id(logicalId: string): number {
    const prior = this.ids.get(logicalId);
    if (prior) return prior;
    let id = this.allocate() >>> 0;
    while (!id || [...this.ids.values()].includes(id)) id = this.allocate() >>> 0;
    this.ids.set(logicalId, id);
    return id;
  }

  render(logicalId: string, availableWidth: number): string[] {
    const image = this.images.get(logicalId);
    if (!image || !this.capable) return [];
    const id = this.id(logicalId);
    const inTmux = Boolean(this.env.TMUX || this.env.TERM?.startsWith("tmux"));
    const png = image.png.toString("base64");
    if (this.uploaded.get(id) !== image.hash) {
      for (const sequence of upload(png, id, inTmux)) this.sink.write(sequence);
      this.uploaded.set(id, image.hash);
    }
    const size = geometry(image, availableWidth, this.cellSize());
    const signature = `${size.columns}:${size.rows}`;
    if (this.placements.get(id) !== signature) {
      if (this.placements.has(id)) this.sink.write(deletePlacement(id, inTmux));
      this.sink.write(placement(id, size.columns, size.rows, inTmux));
      this.placements.set(id, signature);
    }
    return grid(size.columns, size.rows, id);
  }

  clear(): void {
    if (this.capable) {
      const inTmux = Boolean(this.env.TMUX || this.env.TERM?.startsWith("tmux"));
      for (const id of this.ids.values()) this.sink.write(deleteImage(id, inTmux));
    }
    this.images.clear();
    this.ids.clear();
    this.uploaded.clear();
    this.placements.clear();
  }
}
