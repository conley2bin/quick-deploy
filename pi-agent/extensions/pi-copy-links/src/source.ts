import { randomBytes } from "node:crypto";
import type { Code } from "mdast";
import remarkParse from "remark-parse";
import { unified } from "unified";

const parser = unified().use(remarkParse);
const PREFIX_REGISTRY = Symbol.for("pi-copy-links:reserved-prefixes");
const prefixGlobal = globalThis as typeof globalThis & { [PREFIX_REGISTRY]?: Set<string> };
const reservedPrefixes = prefixGlobal[PREFIX_REGISTRY] ??= new Set<string>();

function createCopyPrefix(): string {
  for (let attempt = 0; attempt < 16; attempt++) {
    const prefix = `pi-copy://${randomBytes(12).toString("hex")}/`;
    if (reservedPrefixes.has(prefix)) continue;
    reservedPrefixes.add(prefix);
    return prefix;
  }
  throw new Error("unable to allocate a unique pi-copy namespace");
}

export type CodeContainer = "blockquote" | "list";

export interface CodeSyntax {
  kind: "fenced" | "indented";
  marker?: "`" | "~";
  markerLength?: number;
  info?: string;
  closed?: boolean;
}

export interface SourceCodeBlock {
  value: string;
  lang: string | null;
  meta: string | null;
  raw: string;
  startOffset: number;
  endOffset: number;
  path: CodeContainer[];
  syntax: CodeSyntax;
}

export interface RenderedCodeBlock {
  text: string;
  lang: string;
  raw: string;
  path: CodeContainer[];
  syntax: CodeSyntax;
}

interface TreeNode {
  type: string;
  children?: TreeNode[];
  value?: string;
  lang?: string | null;
  meta?: string | null;
  position?: {
    start: { offset?: number };
    end: { offset?: number };
  };
}

function canonicalInfo(value: string): string {
  return value.trim().replace(/\s+/gu, " ");
}

function stripContainerPrefix(line: string): string {
  let current = line;
  while (true) {
    const next = current.replace(/^[ \t]*>[ \t]?/u, "");
    if (next === current) return current.trim();
    current = next;
  }
}

/** Fence evidence retained by both remark source spans and native Marked raw tokens. */
export function codeSyntax(raw: string): CodeSyntax {
  const lines = raw.replace(/\r\n?/gu, "\n").split("\n");
  const opening = /^ {0,3}(`{3,}|~{3,})(.*)$/u.exec(lines[0] ?? "");
  if (!opening) return { kind: "indented" };
  const marker = opening[1]![0] as "`" | "~";
  const markerLength = opening[1]!.length;
  const fenceLines = [...lines];
  if (fenceLines.length > 1 && fenceLines.at(-1) === "") fenceLines.pop();
  const closing = stripContainerPrefix(fenceLines.at(-1) ?? "");
  const closed = fenceLines.length > 1 && closing.length >= markerLength &&
    closing.split("").every((character) => character === marker);
  return {
    kind: "fenced",
    marker,
    markerLength,
    info: canonicalInfo(opening[2] ?? ""),
    closed,
  };
}

function nextContainerPath(path: CodeContainer[], type: string): CodeContainer[] {
  if (type === "blockquote") return [...path, "blockquote"];
  // Marked renders nested lists through renderList() without another renderToken()
  // call. Collapse adjacent list ancestry so source and native paths describe the
  // same observable call stack.
  if (type === "list" && path.at(-1) !== "list") return [...path, "list"];
  return path;
}

/** Parse before Pi's display-only tab expansion, indentation and line wrapping. */
export function sourceCodeBlocks(markdown: string): SourceCodeBlock[] {
  const blocks: SourceCodeBlock[] = [];
  const visit = (node: TreeNode, path: CodeContainer[]): void => {
    if (node.type === "code") {
      const startOffset = node.position?.start.offset;
      const endOffset = node.position?.end.offset;
      if (startOffset === undefined || endOffset === undefined) {
        throw new Error("remark code node has no source span");
      }
      const raw = markdown.slice(startOffset, endOffset);
      blocks.push({
        value: node.value ?? "",
        lang: node.lang ?? null,
        meta: node.meta ?? null,
        raw,
        startOffset,
        endOffset,
        path: [...path],
        syntax: codeSyntax(raw),
      });
      return;
    }
    const childPath = nextContainerPath(path, node.type);
    for (const child of node.children ?? []) visit(child, childPath);
  };
  visit(parser.parse(markdown) as unknown as TreeNode, []);
  return blocks.sort((left, right) => left.startOffset - right.startOffset);
}

/** Compatibility helper retained for callers that only need mdast code values. */
export function codeBlocks(markdown: string): Code[] {
  const blocks: Code[] = [];
  const visit = (node: TreeNode): void => {
    if (node.type === "code") blocks.push(node as unknown as Code);
    else for (const child of node.children ?? []) visit(child);
  };
  visit(parser.parse(markdown) as unknown as TreeNode);
  return blocks;
}

function samePath(left: readonly CodeContainer[], right: readonly CodeContainer[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function sameSyntax(left: CodeSyntax, right: CodeSyntax): boolean {
  return left.kind === right.kind && left.marker === right.marker &&
    left.markerLength === right.markerLength && left.info === right.info && left.closed === right.closed;
}

function blockMatches(source: SourceCodeBlock, rendered: RenderedCodeBlock): boolean {
  const language = canonicalInfo([source.lang, source.meta].filter(Boolean).join(" "));
  return source.value.replace(/\t/gu, "   ") === rendered.text &&
    language === canonicalInfo(rendered.lang) && samePath(source.path, rendered.path) &&
    sameSyntax(source.syntax, rendered.syntax);
}

export interface CodeAlignment {
  /** Source index for each native token; undefined means no unique safe mapping. */
  sourceIndexes: Array<number | undefined>;
  complete: boolean;
  ambiguous: boolean;
  /** Structural work counter used to assert linear pointer traversal in tests. */
  comparisons: number;
}

/**
 * Align every native token monotonically to source spans. Greedy forward and
 * backward scans give each token its earliest and latest feasible source index.
 * The index is forced across every complete alignment exactly when both agree.
 */
export function alignCodeBlocks(
  sources: readonly SourceCodeBlock[],
  rendered: readonly RenderedCodeBlock[],
): CodeAlignment {
  const earliest = new Array<number>(rendered.length);
  const latest = new Array<number>(rendered.length);
  let comparisons = 0;
  let source = 0;
  for (let native = 0; native < rendered.length; native++) {
    while (source < sources.length) {
      comparisons += 1;
      if (blockMatches(sources[source]!, rendered[native]!)) break;
      source += 1;
    }
    if (source >= sources.length) {
      return {
        sourceIndexes: rendered.map(() => undefined),
        complete: false,
        ambiguous: true,
        comparisons,
      };
    }
    earliest[native] = source;
    source += 1;
  }

  source = sources.length - 1;
  for (let native = rendered.length - 1; native >= 0; native--) {
    while (source >= 0) {
      comparisons += 1;
      if (blockMatches(sources[source]!, rendered[native]!)) break;
      source -= 1;
    }
    if (source < 0) {
      return {
        sourceIndexes: rendered.map(() => undefined),
        complete: false,
        ambiguous: true,
        comparisons,
      };
    }
    latest[native] = source;
    source -= 1;
  }

  const sourceIndexes = rendered.map((_token, native) =>
    earliest[native] === latest[native] ? earliest[native] : undefined);
  return {
    sourceIndexes,
    complete: true,
    ambiguous: sourceIndexes.some((index) => index === undefined),
    comparisons,
  };
}

export interface CopyEntry { url: string; text: string }
export type CopySlot = CopyEntry | undefined;

interface OwnerRecord {
  texts: Array<string | undefined>;
  slots: CopySlot[];
}

export interface CleanupIssue {
  operation: string;
  error: unknown;
}

export interface CopyLookup {
  entry?: CopyEntry;
  error?: unknown;
}

function errorText(error: unknown): string {
  try { return error instanceof Error ? `${error.name}: ${error.message}` : String(error); }
  catch { return "unprintable bookkeeping failure"; }
}

function attemptCleanup(issues: CleanupIssue[], operation: string, action: () => void): void {
  try { action(); }
  catch (error) { issues.push({ operation, error }); }
}

function aggregateFailure(primary: unknown, issues: readonly CleanupIssue[]): Error {
  if (issues.length === 0) return primary instanceof Error ? primary : new Error(errorText(primary));
  const detail = issues.map((issue) => `${issue.operation}: ${errorText(issue.error)}`).join("; ");
  return new AggregateError(
    [primary, ...issues.map((issue) => issue.error)],
    `${errorText(primary)}; cleanup failures: ${detail}`,
    { cause: primary },
  );
}

/** A mounted Markdown owns its entries; each instance is one disposable namespace generation. */
export class CopyStore {
  readonly prefix = createCopyPrefix();
  private sequence = 0;
  private trusted = true;
  private poisonReason: unknown;
  private owners = new WeakMap<object, OwnerRecord>();
  private entries = new Map<string, WeakRef<CopyEntry>>();
  private cleanup = new FinalizationRegistry<string>((url) => {
    try { this.entries.delete(url); }
    catch (error) {
      this.trusted = false;
      this.poisonReason = error;
    }
  });

  isTrusted(): boolean { return this.trusted; }

  set(owner: object, texts: readonly (string | undefined)[]): CopySlot[] {
    if (!this.trusted) throw new Error("CopyStore generation is retired", { cause: this.poisonReason });
    const requested = [...texts];
    let previous: OwnerRecord | undefined;
    const slots: CopySlot[] = [];
    const created: Array<{ entry: CopyEntry; ref: WeakRef<CopyEntry> }> = [];
    const inserted: string[] = [];
    let nextSequence = this.sequence;
    let record: OwnerRecord | undefined;
    try {
      previous = this.owners.get(owner);
      if (previous && previous.texts.length === requested.length &&
          previous.texts.every((text, index) => text === requested[index])) {
        return previous.slots;
      }
      for (const text of requested) {
        if (text === undefined) {
          slots.push(undefined);
          continue;
        }
        const entry = { url: `${this.prefix}${++nextSequence}`, text };
        const ref = new WeakRef(entry);
        created.push({ entry, ref });
        this.cleanup.register(entry, entry.url, entry);
        slots.push(entry);
      }
      record = { texts: requested, slots };
      for (const { entry, ref } of created) {
        inserted.push(entry.url);
        this.entries.set(entry.url, ref);
        if (this.entries.get(entry.url) !== ref) throw new Error("CopyStore Map.set did not commit");
      }
      this.owners.set(owner, record);
      if (this.owners.get(owner) !== record) throw new Error("CopyStore WeakMap.set did not commit");
      this.sequence = nextSequence;
      return slots;
    } catch (primary) {
      this.trusted = false;
      this.poisonReason = primary;
      const issues: CleanupIssue[] = [];
      // The generation is retired after any failure, so removing the candidate
      // owner is safer than trying to restore a potentially unobservable record.
      attemptCleanup(issues, "owners.delete", () => { this.owners.delete(owner); });
      for (const url of inserted) {
        attemptCleanup(issues, `entries.delete(${url})`, () => { this.entries.delete(url); });
      }
      for (const { entry } of created) {
        attemptCleanup(issues, `finalizer.unregister(${entry.url})`, () => { this.cleanup.unregister(entry); });
      }
      throw aggregateFailure(primary, issues);
    }
  }

  /** Delete one owner's entries. Never throws; any cleanup failure poisons this generation. */
  delete(owner: object): CleanupIssue[] {
    const issues: CleanupIssue[] = [];
    let record: OwnerRecord | undefined;
    attemptCleanup(issues, "owners.get", () => { record = this.owners.get(owner); });
    for (const entry of record?.slots ?? []) {
      if (!entry) continue;
      attemptCleanup(issues, `entries.delete(${entry.url})`, () => {
        if (!this.entries.delete(entry.url)) throw new Error("Map.delete did not remove the action");
      });
      attemptCleanup(issues, `finalizer.unregister(${entry.url})`, () => {
        if (!this.cleanup.unregister(entry)) throw new Error("FinalizationRegistry.unregister returned false");
      });
    }
    attemptCleanup(issues, "owners.delete", () => {
      if (record && !this.owners.delete(owner)) throw new Error("WeakMap.delete did not remove the owner");
      if (!record) this.owners.delete(owner);
    });
    if (issues.length) {
      this.trusted = false;
      this.poisonReason = aggregateFailure(new Error("CopyStore owner cleanup failed"), issues);
    }
    return issues;
  }

  /** Retire the whole namespace before attempting cleanup. Never throws. */
  retire(): CleanupIssue[] {
    this.trusted = false;
    const issues: CleanupIssue[] = [];
    attemptCleanup(issues, "entries.clear", () => { this.entries.clear(); });
    attemptCleanup(issues, "owners.reset", () => { this.owners = new WeakMap(); });
    if (issues.length) this.poisonReason = aggregateFailure(this.poisonReason ?? new Error("CopyStore retired"), issues);
    return issues;
  }

  /** Exercise real entry containers; cleanup failure makes the probe fail. */
  probe(): void {
    if (!this.trusted) throw new Error("CopyStore generation is retired", { cause: this.poisonReason });
    const owner = {};
    let primary: unknown;
    try { this.set(owner, [""]); }
    catch (error) { primary = error; }
    const issues = this.delete(owner);
    if (primary !== undefined || issues.length) {
      throw aggregateFailure(primary ?? new Error("CopyStore probe cleanup failed"), issues);
    }
  }

  lookup(url: string): CopyLookup {
    if (!this.trusted) return { error: this.poisonReason ?? new Error("CopyStore generation is retired") };
    try { return { entry: this.entries.get(url)?.deref() }; }
    catch (error) {
      this.trusted = false;
      this.poisonReason = error;
      return { error };
    }
  }

  get(url: string): CopyEntry | undefined { return this.lookup(url).entry; }
  owns(url: string): boolean { return url.startsWith(this.prefix); }
  clear(): void { this.retire(); }
}
