import type { LoadedImage } from "./images.ts";
import { loadImage } from "./images.ts";
import { logicalId, parseMarkdownImages, type PreparedMarkdown, type PreparedReference } from "./markdown.ts";
import type { TerminalImages } from "./terminal.ts";

export const MAX_ACTIVE_IMAGES = 64;

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

export class ImageSession {
  readonly markdown = new Map<string, PreparedMarkdown>();
  private generation = 0;

  constructor(private terminal: TerminalImages, private loader: (href: string, cwd: string) => Promise<LoadedImage> = loadImage) {}

  async prepare(source: string, cwd: string): Promise<PreparedMarkdown> {
    const references: PreparedReference[] = parseMarkdownImages(source).map((reference) => ({ ...reference, logicalId: logicalId(cwd, source, reference) }));
    const prepared: PreparedMarkdown = { source, cwd, references };
    this.markdown.set(source, prepared);
    const generation = this.generation;
    for (const reference of references) {
      if (!this.terminal.has(reference.logicalId) && this.terminal.count() >= MAX_ACTIVE_IMAGES) {
        reference.error = `inline image capacity reached (${MAX_ACTIVE_IMAGES})`;
        continue;
      }
      try {
        const image = await this.loader(reference.href, cwd);
        if (generation !== this.generation) return prepared;
        this.terminal.set(reference.logicalId, image);
      } catch (error) {
        reference.error = error instanceof Error ? error.message : String(error);
      }
    }
    return prepared;
  }

  async restore(entries: readonly Entry[], cwd: string): Promise<void> {
    this.reset();
    const generation = this.generation;
    for (const entry of entries) {
      for (const source of assistantTextBlocks(entry.type === "message" ? entry.message : undefined)) {
        if (generation !== this.generation) return;
        await this.prepare(source, cwd);
      }
    }
  }

  reset(): void {
    this.generation++;
    this.markdown.clear();
    this.terminal.clear();
  }
}
