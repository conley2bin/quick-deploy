import type { LoadedImage } from "./images.ts";
import { loadImage } from "./images.ts";
import { logicalId, parseMarkdownImages, type PreparedMarkdown, type PreparedReference } from "./markdown.ts";
import type { TerminalImages } from "./terminal.ts";

export { MAX_ACTIVE_IMAGES } from "./terminal.ts";

type Message = { role?: string; content?: unknown };
type Entry = { type?: string; message?: unknown };

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
  private preparedMessages = new WeakMap<object, PreparedMarkdown[]>();
  private readonly coordinationFailures = new Map<string, PreparedMarkdown>();
  private activeRender?: { message: object; blockIndex: number };
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

  async prepareMessage(message: Message, cwd: string): Promise<PreparedMarkdown[]> {
    if (!message || typeof message !== "object") return [];
    const prepared: PreparedMarkdown[] = [];
    for (const source of assistantTextBlocks(message)) prepared.push(await this.prepare(source, cwd));
    this.preparedMessages.set(message, prepared);
    return prepared;
  }

  /** Scope Markdown resolution to one positively associated native assistant component. */
  withRenderMessage<T>(message: object, render: () => T): T {
    const previous = this.activeRender;
    this.activeRender = { message, blockIndex: 0 };
    try { return render(); } finally { this.activeRender = previous; }
  }

  preparedForRender(source: string): PreparedMarkdown | undefined {
    if (this.activeRender) {
      const candidate = this.preparedMessages.get(this.activeRender.message)?.[this.activeRender.blockIndex++];
      return candidate?.source === source ? candidate : this.coordinationFailure(source, candidate?.cwd);
    }
    const candidates = this.occurrences.get(source);
    if (!candidates?.length) return this.markdown.get(source);
    return candidates.length === 1 ? candidates[0] : this.coordinationFailure(source, candidates[0]?.cwd);
  }

  async restore(entries: readonly Entry[], cwd: string, reusePrepared = false): Promise<void> {
    const previous = reusePrepared
      ? new Map([...this.occurrences].map(([source, prepared]) => [source, [...prepared]]))
      : undefined;
    const generation = ++this.generation;
    const sources = entries.flatMap((entry) => assistantTextBlocks(entry.type === "message" ? entry.message as Message : undefined));
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
      const message = entry.type === "message" && entry.message && typeof entry.message === "object" ? entry.message as Message : undefined;
      const preparedMessage: PreparedMarkdown[] = [];
      for (const source of assistantTextBlocks(message)) {
        if (generation !== this.generation) return;
        const index = occurrence.get(source) ?? 0;
        occurrence.set(source, index + 1);
        const reusable = previous?.get(source)?.[index];
        const resourcesReady = reusable?.references.every((reference) => reference.inTable || (!reference.error && this.terminal.has(reference.logicalId)));
        if (reusable?.cwd === cwd && resourcesReady) {
          this.record(reusable);
          preparedMessage.push(reusable);
        } else preparedMessage.push(await this.prepare(source, cwd));
      }
      if (message && typeof message === "object") this.preparedMessages.set(message, preparedMessage);
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
    this.coordinationFailures.delete(prepared.source);
  }

  private coordinationFailure(source: string, cwd = process.cwd()): PreparedMarkdown {
    const cached = this.coordinationFailures.get(source);
    if (cached) return cached;
    const references: PreparedReference[] = parseMarkdownImages(source).map((reference) => ({
      ...reference,
      logicalId: logicalId(cwd, source, reference),
      error: "host image coordination unavailable for this Markdown occurrence",
    }));
    const prepared = { source, cwd, references };
    this.coordinationFailures.set(source, prepared);
    return prepared;
  }

  private clearPrepared(): void {
    this.markdown.clear();
    this.occurrences.clear();
    this.preparedMessages = new WeakMap();
    this.coordinationFailures.clear();
    this.activeRender = undefined;
  }
}
