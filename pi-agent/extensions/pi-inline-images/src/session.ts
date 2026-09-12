import type { LoadedImage } from "./images.ts";
import { loadImage } from "./images.ts";
import { logicalId, parseMarkdownImages, type PreparedMarkdown, type PreparedReference } from "./markdown.ts";
import type { TerminalImages } from "./terminal.ts";

export { MAX_ACTIVE_IMAGES } from "./terminal.ts";

type Message = { role?: string; content?: unknown };
type Entry = { type?: string; message?: Message };

export function assistantTextBlocks(message: Message | undefined): string[] {
  if (!message || message.role !== "assistant") return [];
  if (typeof message.content === "string") return message.content.trim() ? [message.content.trim()] : [];
  if (!Array.isArray(message.content)) return [];
  return message.content.flatMap((part) => {
    if (!part || typeof part !== "object") return [];
    const value = part as { type?: string; text?: unknown };
    return value.type === "text" && typeof value.text === "string" && value.text.trim() ? [value.text.trim()] : [];
  });
}

export function versionedLogicalId(baseId: string, contentHash: string): string {
  return `${baseId}:${contentHash}`;
}

export class ImageSession {
  readonly markdown = new Map<string, PreparedMarkdown>();
  private generation = 0;

  constructor(private terminal: TerminalImages, private loader: (href: string, cwd: string) => Promise<LoadedImage> = loadImage) {}

  async prepare(source: string, cwd: string): Promise<PreparedMarkdown> {
    const references: PreparedReference[] = parseMarkdownImages(source).map((reference) => ({ ...reference, logicalId: logicalId(cwd, source, reference) }));
    const prepared: PreparedMarkdown = { source, cwd, references };
    this.markdown.set(source, prepared);
    const generation = this.generation;
    if (!this.terminal.available()) return prepared;

    for (const reference of references) {
      if (reference.inTable) continue;
      try {
        const image = await this.loader(reference.href, cwd);
        if (generation !== this.generation) return prepared;
        reference.logicalId = versionedLogicalId(reference.logicalId, image.hash);
        await this.terminal.prepare(reference.logicalId, image);
        if (generation !== this.generation) return prepared;
      } catch (error) {
        if (generation !== this.generation) return prepared;
        reference.error = error instanceof Error ? error.message : String(error);
      }
    }
    return prepared;
  }

  async restore(entries: readonly Entry[], cwd: string, reusePrepared = false): Promise<void> {
    const previous = reusePrepared ? new Map(this.markdown) : undefined;
    const generation = ++this.generation;
    this.markdown.clear();
    if (reusePrepared) this.terminal.reconcile();
    else await this.terminal.clear();
    if (generation !== this.generation) return;

    for (const entry of entries) {
      for (const source of assistantTextBlocks(entry.type === "message" ? entry.message : undefined)) {
        if (generation !== this.generation) return;
        const reusable = previous?.get(source);
        const resourcesReady = reusable?.references.every((reference) => reference.inTable || (!reference.error && this.terminal.has(reference.logicalId)));
        if (reusable?.cwd === cwd && resourcesReady) this.markdown.set(source, reusable);
        else await this.prepare(source, cwd);
      }
    }
  }

  async reset(dispose = false): Promise<void> {
    this.generation++;
    this.markdown.clear();
    await this.terminal.clear(dispose);
  }
}
