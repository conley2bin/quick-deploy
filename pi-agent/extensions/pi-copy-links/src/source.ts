import { randomBytes } from "node:crypto";
import type { Code } from "mdast";
import remarkParse from "remark-parse";
import { unified } from "unified";
import { commandLike, inlineCodePaths, nonPathTextRanges, plainTextPaths, plainTextWordSpans, type PathSpan } from "./paths.ts";

const parser = unified().use(remarkParse);
// Process-wide so every module-URL reimport draws from one monotonic sequence. The generation makes
// namespaces unique; the fresh 96-bit nonce keeps them unguessable. The legacy Symbol.for(
// "pi-copy-links:reserved-prefixes") Set is left to older module instances; "v2-" is non-hex, so
// the formats are disjoint and no retired URI can be reactivated.
const PREFIX_GENERATION = Symbol.for("pi-copy-links:prefix-generation");
const prefixGlobal = globalThis as typeof globalThis & { [PREFIX_GENERATION]?: { next: number } };

function createCopyPrefix(): string {
  const state = prefixGlobal[PREFIX_GENERATION] ??= { next: 0 };
  if (typeof state !== "object" || state === null || !Number.isSafeInteger(state.next) || state.next < 0) {
    throw new Error("pi-copy prefix generation state is corrupt or exhausted");
  }
  const generation = state.next++;
  return `pi-copy://v2-${generation}-${randomBytes(12).toString("hex")}/`;
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
  url?: string;
  identifier?: string;
  lang?: string | null;
  meta?: string | null;
  position?: {
    start: { offset?: number };
    end: { offset?: number };
  };
}

interface SourcePathSpan {
  readonly kind: "text" | "codespan";
  readonly raw: string;
  readonly text: string;
  readonly paths: readonly PathSpan[];
  readonly allowed: boolean;
}

export interface PathMatchWork {
  commandLines: number;
  indexedCharacters: number;
  queries: number;
  cacheHits: number;
  lookups: number;
}

export interface SourceLinkEvidence {
  /** Pi-normalized native raw token spelling -> possible remark semantic hrefs. */
  readonly destinations: ReadonlyMap<string, ReadonlySet<string>>;
  /** Angle-bracket autolinks require native/source semantic agreement. */
  readonly explicitAutolinks: ReadonlySet<string>;
  /** Plain source text spans that can prove a native bare HTTP(S) autolink. */
  readonly plainTextSpans: readonly string[];
  readonly pathSpans: readonly SourcePathSpan[];
  /** Exact feasible leaf keys; null means conflicting/blocked source evidence. */
  readonly pathIndex: Map<string, SourcePathSpan | null>;
  readonly pathQueries: Map<string, readonly PathSpan[]>;
  /** Structural counters for linear-work regressions, not wall-clock admission. */
  readonly pathWork: PathMatchWork;
}

export function emptySourceLinkEvidence(): SourceLinkEvidence {
  return { destinations: new Map(), explicitAutolinks: new Set(), plainTextSpans: [], pathSpans: [],
    pathIndex: new Map(), pathQueries: new Map(),
    pathWork: { commandLines: 0, indexedCharacters: 0, queries: 0, cacheHits: 0, lookups: 0 } };
}

interface TextSlice { start: number; end: number }

// Same strict-del content grammar as Pi, including internal single tildes.
// Synthetic splits may overapproximate negative evidence; they never mint paths.
const STRICT_DEL = /(~~)(?=[^\s~])((?:\\.|[^\\])*?(?:\\.|[^\s~\\]))\1(?=[^~]|$)/gu;

function feasibleTextLeaves(raw: string): TextSlice[] {
  const leaves = new Map<string, TextSlice>();
  const add = (start: number, end: number) => { if (end > start) leaves.set(`${start}:${end}`, { start, end }); };
  const visit = (from: number, to: number): void => {
    add(from, to);
    const text = raw.slice(from, to);
    const cells = text.includes("|") ? [...text.matchAll(/[^|\n]+/gu)].map((match) =>
      ({ start: from + match.index, end: from + match.index + match[0].length })) : [{ start: from, end: to }];
    for (const cell of cells) {
      const value = raw.slice(cell.start, cell.end);
      const left = cell.start + value.length - value.trimStart().length;
      const right = cell.end - value.length + value.trimEnd().length;
      const variants = text.includes("|") ? [cell, { start: left, end: cell.end }, { start: cell.start, end: right }, { start: left, end: right }] : [cell];
      for (const variant of variants) {
        if (variant.end <= variant.start) continue;
        add(variant.start, variant.end);
        const value = raw.slice(variant.start, variant.end);
        const boundaries: Array<TextSlice & { content?: TextSlice }> = nonPathTextRanges(value).map((range) => {
          const word = value.slice(range.start, range.end);
          const url = /^(?:https?:\/\/|ftp:\/\/|www\.)/iu.test(word) ?
            /^(?:[^?!.,:;*_'"~()&]+|\([^)]*\)|&(?![a-zA-Z0-9]+;$)|[?!.,:;*_'"~)]+(?!$))+/u.exec(word)?.[0] ?? word : word;
          return { start: range.start, end: range.start + url.length };
        });
        for (const match of value.matchAll(STRICT_DEL)) {
          boundaries.push({ start: match.index, end: match.index + match[0].length,
            content: { start: match.index + 2, end: match.index + match[0].length - 2 } });
        }
        boundaries.sort((a, b) => a.start - b.start);
        let start = 0;
        for (const boundary of boundaries) {
          if (boundary.start < start) continue;
          add(variant.start + start, variant.start + boundary.start);
          if (boundary.content) visit(variant.start + boundary.content.start, variant.start + boundary.content.end);
          start = boundary.end;
        }
        add(variant.start + start, variant.end);
      }
    }
  };
  visit(0, raw.length);
  return [...leaves.values()];
}

function pathLeafKey(kind: "text" | "codespan", raw: string): string { return `${kind}\0${raw}`; }

function normalizedMarkdownSpan(value: string): string {
  return value.replace(/\r\n?/gu, "\n").replace(/\t/gu, "   ");
}

/** Preserve whole source-word authority across AST and synthetic render splits. */
function sourcePathCandidates(markdown: string, root: TreeNode): { paths: PathSpan[]; offsets: number[] } {
  const source = normalizedMarkdownSpan(markdown);
  const offsets: number[] = [];
  let normalized = 0;
  for (let at = 0; at < markdown.length; at++) {
    offsets.push(normalized);
    normalized += markdown[at] === "\t" ? 3 : markdown[at] === "\n" && markdown[at - 1] === "\r" ? 0 : 1;
  }
  offsets.push(normalized);
  const openings = new Map<number, number>(), closings = new Map<number, number>();
  const collectStyles = (node: TreeNode): void => {
    const start = node.position?.start.offset, end = node.position?.end.offset;
    if ((node.type === "strong" || node.type === "emphasis") && start !== undefined && end !== undefined) {
      const size = node.type === "strong" ? 2 : 1;
      openings.set(offsets[start]!, offsets[start + size]!);
      closings.set(offsets[end]!, offsets[end - size]!);
    }
    for (const child of node.children ?? []) collectStyles(child);
  };
  collectStyles(root);
  for (const match of source.matchAll(STRICT_DEL)) {
    // Do not treat a substring of a longer/escaped marker run as positive syntax.
    if (source[match.index - 1] === "~" || source[match.index - 1] === "\\") continue;
    openings.set(match.index, match.index + 2);
    closings.set(match.index + match[0].length, match.index + match[0].length - 2);
  }
  const paths: PathSpan[] = [];
  for (const word of plainTextWordSpans(source)) {
    let { start, end } = word;
    while (openings.has(start) && openings.get(start)! < end) start = openings.get(start)!;
    while (closings.has(end) && closings.get(end)! > start) end = closings.get(end)!;
    for (const path of plainTextPaths(source.slice(start, end))) {
      paths.push({ ...path, start: path.start + start, end: path.end + start });
    }
  }
  return { paths, offsets };
}

/** Only a complete candidate contained in a slice can carry positive authority. */
function projectPaths(paths: readonly PathSpan[], start: number, end: number): PathSpan[] {
  let low = 0, high = paths.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (paths[middle]!.end <= start) low = middle + 1;
    else high = middle;
  }
  const projected: PathSpan[] = [];
  for (let at = low; at < paths.length && paths[at]!.start < end; at++) {
    const path = paths[at]!;
    if (path.start >= start && path.end <= end) projected.push({ ...path, start: path.start - start, end: path.end - start });
  }
  return projected;
}

/** Collect source-exact link semantics before Pi normalizes Markdown for Marked. */
export function sourceLinkEvidence(markdown: string): SourceLinkEvidence {
  const root = parser.parse(markdown) as unknown as TreeNode;
  const definitions = new Map<string, string>();
  const destinations = new Map<string, Set<string>>();
  const explicitAutolinks = new Set<string>();
  const plainTextSpans: string[] = [];
  const collectDefinitions = (node: TreeNode): void => {
    if (node.type === "definition" && node.identifier !== undefined && node.url !== undefined &&
        !definitions.has(node.identifier)) definitions.set(node.identifier, node.url);
    for (const child of node.children ?? []) collectDefinitions(child);
  };
  const collectLinks = (node: TreeNode, insideLink = false): void => {
    const link = node.type === "link" || node.type === "linkReference";
    if (link) {
      const startOffset = node.position?.start.offset;
      const endOffset = node.position?.end.offset;
      if (startOffset === undefined || endOffset === undefined) {
        throw new Error("remark link node has no source span");
      }
      const destination = node.type === "link" ? node.url :
        node.identifier === undefined ? undefined : definitions.get(node.identifier);
      if (destination !== undefined) {
        const raw = normalizedMarkdownSpan(markdown.slice(startOffset, endOffset));
        const values = destinations.get(raw) ?? new Set<string>();
        values.add(destination);
        destinations.set(raw, values);
        if (node.type === "link" && raw.startsWith("<") && raw.endsWith(">")) {
          explicitAutolinks.add(raw);
        }
      }
    } else if (!insideLink && node.type === "text") {
      const startOffset = node.position?.start.offset;
      const endOffset = node.position?.end.offset;
      if (startOffset === undefined || endOffset === undefined) {
        throw new Error("remark text node has no source span");
      }
      plainTextSpans.push(normalizedMarkdownSpan(markdown.slice(startOffset, endOffset)));
    }
    for (const child of node.children ?? []) collectLinks(child, insideLink || link);
  };
  const pathSpans: SourcePathSpan[] = [];
  const candidates = sourcePathCandidates(markdown, root);
  const lineStarts: number[] = [];
  const blockedLines = [0];
  let lineOffset = 0;
  for (const line of markdown.split("\n")) {
    lineStarts.push(lineOffset);
    blockedLines.push(blockedLines.at(-1)! + Number(commandLike(line)));
    lineOffset += line.length + 1;
  }
  const lineAt = (offset: number): number => {
    let low = 0, high = lineStarts.length;
    while (low + 1 < high) {
      const middle = (low + high) >>> 1;
      if (lineStarts[middle]! <= offset) low = middle;
      else high = middle;
    }
    return low;
  };
  const containsHtml = (node: TreeNode): boolean => node.type === "html" || (node.children ?? []).some(containsHtml);
  const collectPaths = (node: TreeNode, excluded = false): void => {
    // These native tokens never supply independently actionable leaf text.
    if (["link", "linkReference", "image", "imageReference", "code", "html"].includes(node.type)) return;
    const blocked = excluded || (["paragraph", "heading"].includes(node.type) && containsHtml(node));
    if (node.type === "text" || node.type === "inlineCode") {
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      if (start === undefined || end === undefined) throw new Error("remark path leaf has no source span");
      const raw = normalizedMarkdownSpan(markdown.slice(start, end));
      const kind = node.type === "text" ? "text" : "codespan";
      const text = kind === "codespan" ? node.value ?? "" : normalizedMarkdownSpan(node.value ?? "");
      const firstLine = lineAt(start), lastLine = lineAt(Math.max(start, end - 1));
      const allowed = !blocked && blockedLines[lastLine + 1] === blockedLines[firstLine];
      // Text entity/escape disagreement is deliberately not guessed. Inline
      // code uses remark's literal value and additionally requires native equality.
      const paths = !allowed ? [] : kind === "codespan" ? inlineCodePaths(text) :
        raw === text ? projectPaths(candidates.paths, candidates.offsets[start]!, candidates.offsets[end]!) : [];
      pathSpans.push({ kind, raw, text, paths, allowed });
    }
    for (const child of node.children ?? []) collectPaths(child, blocked);
  };
  const pathIndex = new Map<string, SourcePathSpan | null>();
  const pathWork: PathMatchWork = { commandLines: lineStarts.length, indexedCharacters: 0, queries: 0, cacheHits: 0, lookups: 0 };
  const indexLeaf = (kind: "text" | "codespan", raw: string, text: string, paths: readonly PathSpan[]) => {
    pathWork.indexedCharacters += raw.length;
    const key = pathLeafKey(kind, raw);
    const previous = pathIndex.get(key);
    if (previous === null) return;
    if (previous && (previous.text !== text || JSON.stringify(previous.paths) !== JSON.stringify(paths))) {
      pathIndex.set(key, null);
    } else if (!previous) pathIndex.set(key, { kind, raw, text, paths, allowed: paths.length > 0 });
  };
  collectDefinitions(root);
  collectLinks(root);
  collectPaths(root);
  for (const span of pathSpans) {
    if (span.kind === "codespan") {
      indexLeaf(span.kind, span.raw, span.text, span.paths);
      continue;
    }
    for (const slice of feasibleTextLeaves(span.raw)) {
      const raw = span.raw.slice(slice.start, slice.end);
      const paths = projectPaths(span.paths, slice.start, slice.end);
      // A fragment can only add negative evidence, never a fresh target. This
      // also prevents an identical partial/blocked leaf borrowing another word.
      if (paths.length || plainTextPaths(raw).length) indexLeaf("text", raw, raw, paths);
    }
  }
  return { destinations, explicitAutolinks, plainTextSpans, pathSpans, pathIndex,
    pathQueries: new Map(), pathWork };
}

/** One cached exact lookup per native leaf; never rescan the original source. */
export function sourcePathMatches(
  evidence: SourceLinkEvidence,
  kind: "text" | "codespan",
  raw: string,
  text: string,
): readonly PathSpan[] {
  if (!evidence.pathIndex.size || !raw || !text || (kind === "text" && raw !== text)) return [];
  evidence.pathWork.queries += 1;
  const key = `${pathLeafKey(kind, raw)}\0${text}`;
  const cached = evidence.pathQueries.get(key);
  if (cached) { evidence.pathWork.cacheHits += 1; return cached; }
  evidence.pathWork.lookups += 1;
  const leaf = evidence.pathIndex.get(pathLeafKey(kind, raw));
  const paths = leaf && leaf.text === text ? leaf.paths : [];
  evidence.pathQueries.set(key, paths);
  return paths;
}

/** Derive remark's semantic spelling only for a native URL proven to occur in plain source text. */
export function sourceAutolinkDestination(evidence: SourceLinkEvidence, raw: string): string | undefined {
  if (!evidence.plainTextSpans.some((span) => span.includes(raw))) return undefined;
  const root = parser.parse(raw) as unknown as TreeNode;
  const paragraph = root.children?.length === 1 ? root.children[0] : undefined;
  const text = paragraph?.type === "paragraph" && paragraph.children?.length === 1 ? paragraph.children[0] : undefined;
  if (text?.type !== "text" || typeof text.value !== "string") return undefined;
  const startOffset = text.position?.start.offset;
  const endOffset = text.position?.end.offset;
  return startOffset === 0 && endOffset === raw.length ? text.value : undefined;
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

export type Action =
  | { kind: "copy"; text: string }
  | { kind: "open"; href: string }
  | { kind: "diagnostic"; message: string };

export interface ActionEntry {
  readonly url: string;
  readonly action: Readonly<Action>;
}
export type ActionSlot = ActionEntry | undefined;

interface OwnerRecord {
  actions: Array<Readonly<Action> | undefined>;
  slots: ActionSlot[];
}

function sameAction(
  left: Readonly<Action> | undefined,
  right: Readonly<Action> | undefined,
): boolean {
  if (left === undefined || right === undefined) return left === right;
  if (left.kind !== right.kind) return false;
  if (left.kind === "copy" && right.kind === "copy") return left.text === right.text;
  if (left.kind === "open" && right.kind === "open") return left.href === right.href;
  return left.kind === "diagnostic" && right.kind === "diagnostic" && left.message === right.message;
}

export interface CleanupIssue {
  operation: string;
  error: unknown;
}

export interface ActionLookup {
  entry?: ActionEntry;
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
export class ActionStore {
  readonly prefix = createCopyPrefix();
  private sequence = 0;
  private trusted = true;
  private poisonReason: unknown;
  private owners = new WeakMap<object, OwnerRecord>();
  private entries = new Map<string, WeakRef<ActionEntry>>();
  private cleanup = new FinalizationRegistry<string>((url) => {
    try { this.entries.delete(url); }
    catch (error) {
      this.trusted = false;
      this.poisonReason = error;
    }
  });

  isTrusted(): boolean { return this.trusted; }

  set(owner: object, actions: readonly (Readonly<Action> | undefined)[]): ActionSlot[] {
    if (!this.trusted) throw new Error("ActionStore generation is retired", { cause: this.poisonReason });
    const requested = actions.map((action) => action === undefined ? undefined : Object.freeze({ ...action }));
    let previous: OwnerRecord | undefined;
    const slots: ActionSlot[] = [];
    const created: Array<{ entry: ActionEntry; ref: WeakRef<ActionEntry> }> = [];
    const inserted: string[] = [];
    let nextSequence = this.sequence;
    let record: OwnerRecord | undefined;
    try {
      previous = this.owners.get(owner);
      if (previous && previous.actions.length === requested.length &&
          previous.actions.every((action, index) => sameAction(action, requested[index]))) {
        return previous.slots;
      }
      for (const action of requested) {
        if (action === undefined) {
          slots.push(undefined);
          continue;
        }
        const entry: ActionEntry = Object.freeze({ url: `${this.prefix}${++nextSequence}`, action });
        const ref = new WeakRef(entry);
        created.push({ entry, ref });
        this.cleanup.register(entry, entry.url, entry);
        slots.push(entry);
      }
      record = { actions: requested, slots };
      for (const { entry, ref } of created) {
        inserted.push(entry.url);
        this.entries.set(entry.url, ref);
        if (this.entries.get(entry.url) !== ref) throw new Error("ActionStore Map.set did not commit");
      }
      this.owners.set(owner, record);
      if (this.owners.get(owner) !== record) throw new Error("ActionStore WeakMap.set did not commit");
      this.sequence = nextSequence;

      // A successful replacement must synchronously retire the superseded URIs.
      // A prior screen row may still reference them, but lookup can no longer execute them.
      const supersededIssues: CleanupIssue[] = [];
      for (const entry of previous?.slots ?? []) {
        if (!entry) continue;
        attemptCleanup(supersededIssues, `superseded.entries.delete(${entry.url})`, () => {
          if (!this.entries.delete(entry.url)) throw new Error("Map.delete did not remove the superseded action");
        });
        attemptCleanup(supersededIssues, `superseded.finalizer.unregister(${entry.url})`, () => {
          if (!this.cleanup.unregister(entry)) throw new Error("FinalizationRegistry.unregister returned false");
        });
      }
      if (supersededIssues.length) {
        throw aggregateFailure(new Error("ActionStore superseded owner cleanup failed"), supersededIssues);
      }
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
      this.poisonReason = aggregateFailure(new Error("ActionStore owner cleanup failed"), issues);
    }
    return issues;
  }

  /** Retire the whole namespace before attempting cleanup. Never throws. */
  retire(): CleanupIssue[] {
    this.trusted = false;
    const issues: CleanupIssue[] = [];
    attemptCleanup(issues, "entries.clear", () => { this.entries.clear(); });
    attemptCleanup(issues, "owners.reset", () => { this.owners = new WeakMap(); });
    if (issues.length) this.poisonReason = aggregateFailure(this.poisonReason ?? new Error("ActionStore retired"), issues);
    return issues;
  }

  /** Exercise real entry containers; cleanup failure makes the probe fail. */
  probe(): void {
    if (!this.trusted) throw new Error("ActionStore generation is retired", { cause: this.poisonReason });
    const owner = {};
    let primary: unknown;
    try { this.set(owner, [{ kind: "copy", text: "" }]); }
    catch (error) { primary = error; }
    const issues = this.delete(owner);
    if (primary !== undefined || issues.length) {
      throw aggregateFailure(primary ?? new Error("ActionStore probe cleanup failed"), issues);
    }
  }

  lookup(url: string): ActionLookup {
    if (!this.trusted) return { error: this.poisonReason ?? new Error("ActionStore generation is retired") };
    try { return { entry: this.entries.get(url)?.deref() }; }
    catch (error) {
      this.trusted = false;
      this.poisonReason = error;
      return { error };
    }
  }

  get(url: string): ActionEntry | undefined { return this.lookup(url).entry; }
  owns(url: string): boolean { return url.startsWith(this.prefix); }
  clear(): void { this.retire(); }
}
