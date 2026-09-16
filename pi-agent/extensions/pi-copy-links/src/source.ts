import { randomBytes } from "node:crypto";
import type { Code, RootContent } from "mdast";
import remarkParse from "remark-parse";
import { unified } from "unified";

const parser = unified().use(remarkParse);

/** Parse before Pi's display-only tab expansion, indentation and line wrapping. */
export function codeBlocks(markdown: string): Code[] {
  const blocks: Code[] = [];
  function visit(node: RootContent | { children: RootContent[] }): void {
    if ("type" in node && node.type === "code") blocks.push(node);
    else if ("children" in node) for (const child of node.children) visit(child);
  }
  visit(parser.parse(markdown));
  return blocks;
}

export interface CopyEntry { url: string; text: string }

/** A mounted Markdown owns its entries; the URI index does not retain transcripts. */
export class CopyStore {
  readonly prefix = `pi-copy://${randomBytes(12).toString("hex")}/`;
  private sequence = 0;
  private owners = new WeakMap<object, CopyEntry[]>();
  private entries = new Map<string, WeakRef<CopyEntry>>();
  private cleanup = new FinalizationRegistry<string>((url) => this.entries.delete(url));

  set(owner: object, texts: string[]): CopyEntry[] {
    const previous = this.owners.get(owner);
    if (previous && previous.length === texts.length && previous.every((entry, i) => entry.text === texts[i])) {
      return previous;
    }
    const entries = texts.map((text) => {
      const entry = { url: `${this.prefix}${++this.sequence}`, text };
      this.entries.set(entry.url, new WeakRef(entry));
      this.cleanup.register(entry, entry.url);
      return entry;
    });
    this.owners.set(owner, entries);
    return entries;
  }

  get(url: string): CopyEntry | undefined { return this.entries.get(url)?.deref(); }
  owns(url: string): boolean { return url.startsWith(this.prefix); }
  clear(): void { this.entries.clear(); this.owners = new WeakMap(); }
}
