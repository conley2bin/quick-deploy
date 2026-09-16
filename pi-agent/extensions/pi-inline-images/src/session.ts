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
  /** Latest occurrence retained for compatibility with direct session users. */
  readonly markdown = new Map<string, PreparedMarkdown>();
  private readonly occurrences = new Map<string, PreparedMarkdown[]>();
  private readonly renderCursor = new Map<string, number>();
  private renderCursorResetQueued = false;
  private generation = 0;

  constructor(private terminal: TerminalImages, private loader: (href: string, cwd: string) => Promise<LoadedImage> = loadImage) {}

  async prepare(source: string, cwd: string): Promise<PreparedMarkdown> {
    const references: PreparedReference[] = parseMarkdownImages(source).map((reference) => ({ ...reference, logicalId: logicalId(cwd, source, reference) }));
    const prepared: PreparedMarkdown = { source, cwd, references };
    this.record(prepared);
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

  /** Resolve identical Markdown text by occurrence order for one synchronous TUI render pass. */
  preparedForRender(source: string): PreparedMarkdown | undefined {
    if (!this.renderCursorResetQueued) {
      this.renderCursorResetQueued = true;
      queueMicrotask(() => {
        this.renderCursor.clear();
        this.renderCursorResetQueued = false;
      });
    }
    const candidates = this.occurrences.get(source);
    if (!candidates?.length) return this.markdown.get(source);
    const index = this.renderCursor.get(source) ?? 0;
    this.renderCursor.set(source, index + 1);
    return candidates[Math.min(index, candidates.length - 1)];
  }

  async restore(entries: readonly Entry[], cwd: string, reusePrepared = false): Promise<void> {
    const previous = reusePrepared
      ? new Map([...this.occurrences].map(([source, prepared]) => [source, [...prepared]]))
      : undefined;
    const generation = ++this.generation;
    const sources = entries.flatMap((entry) => assistantTextBlocks(entry.type === "message" ? entry.message : undefined));
    const retained = new Set<string>();
    if (previous) {
      const occurrence = new Map<string, number>();
      for (const source of sources) {
        const index = occurrence.get(source) ?? 0;
        occurrence.set(source, index + 1);
        const prepared = previous.get(source)?.[index];
        if (prepared?.cwd === cwd) for (const reference of prepared.references) if (!reference.inTable && !reference.error) retained.add(reference.logicalId);
      }
      await this.terminal.retainOwner("inline", retained);
    } else await this.terminal.resetOwner("inline");
    this.clearPrepared();
    if (generation !== this.generation) return;

    const occurrence = new Map<string, number>();
    for (const entry of entries) {
      for (const source of assistantTextBlocks(entry.type === "message" ? entry.message : undefined)) {
        if (generation !== this.generation) return;
        const index = occurrence.get(source) ?? 0;
        occurrence.set(source, index + 1);
        const reusable = previous?.get(source)?.[index];
        const resourcesReady = reusable?.references.every((reference) => reference.inTable || (!reference.error && this.terminal.has(reference.logicalId)));
        if (reusable?.cwd === cwd && resourcesReady) this.record(reusable);
        else await this.prepare(source, cwd);
      }
    }
  }

  async reset(dispose = false): Promise<void> {
    this.generation++;
    this.clearPrepared();
    if (dispose) await this.terminal.clear(true);
    else await this.terminal.resetOwner("inline");
  }

  private record(prepared: PreparedMarkdown): void {
    const occurrences = this.occurrences.get(prepared.source) ?? [];
    occurrences.push(prepared);
    this.occurrences.set(prepared.source, occurrences);
    this.markdown.set(prepared.source, prepared);
  }

  private clearPrepared(): void {
    this.markdown.clear();
    this.occurrences.clear();
    this.renderCursor.clear();
    this.renderCursorResetQueued = false;
  }
}
