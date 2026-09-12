import { createHash } from "node:crypto";
import type { Definition, Image, ImageReference as MdastImageReference, Node, Parent, Root } from "mdast";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";
import type { TerminalImages } from "./terminal.ts";

const markdownParser = unified().use(remarkParse).use(remarkGfm);

export interface ImageReference {
  start: number;
  end: number;
  raw: string;
  href: string;
  alt: string;
  ordinal: number;
  inTable: boolean;
}

export type PreparedReference = ImageReference & { logicalId: string; error?: string };

export interface PreparedMarkdown {
  source: string;
  cwd: string;
  references: PreparedReference[];
}

function isParent(node: Node): node is Parent {
  return Array.isArray((node as Parent).children);
}

function sourceOffsets(node: Node): { start: number; end: number } | undefined {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  return typeof start === "number" && typeof end === "number" ? { start, end } : undefined;
}

/** Use the parser's own source spans and container ancestry; no second Markdown grammar is maintained here. */
export function parseMarkdownImages(markdown: string): ImageReference[] {
  const tree = markdownParser.parse(markdown) as Root;
  const definitions = new Map<string, Definition>();
  const references: ImageReference[] = [];

  const collectDefinitions = (node: Node): void => {
    if (node.type === "definition") {
      const definition = node as Definition;
      if (!definitions.has(definition.identifier)) definitions.set(definition.identifier, definition);
    }
    if (isParent(node)) for (const child of node.children) collectDefinitions(child);
  };
  collectDefinitions(tree);

  const collectImages = (node: Node, inTable: boolean): void => {
    const insideTable = inTable || node.type === "table";
    if (node.type === "image" || node.type === "imageReference") {
      const offsets = sourceOffsets(node);
      const image = node as Image | MdastImageReference;
      const definition = node.type === "imageReference" ? definitions.get((node as MdastImageReference).identifier) : undefined;
      const href = node.type === "image" ? (node as Image).url : definition?.url;
      if (offsets && href) references.push({
        ...offsets,
        raw: markdown.slice(offsets.start, offsets.end),
        href,
        alt: image.alt || "image",
        ordinal: references.length,
        inTable: insideTable,
      });
    }
    if (isParent(node)) for (const child of node.children) collectImages(child, insideTable);
  };
  collectImages(tree, false);
  return references;
}

export function logicalId(cwd: string, markdown: string, reference: ImageReference): string {
  return createHash("sha256").update(cwd).update("\0").update(markdown).update("\0").update(String(reference.ordinal)).update("\0").update(reference.href).digest("hex").slice(0, 24);
}

function structuralPrefix(source: string, offset: number): { before: string; continuation: string; columns: number } {
  const lineStart = source.lastIndexOf("\n", offset - 1) + 1;
  const before = source.slice(lineStart, offset);
  const match = /^(\s*(?:(?:>\s*)+)?)(?:(?:[-+*]|\d+[.)])\s+)?/u.exec(before);
  const quote = match?.[1] || "";
  const full = match?.[0] || "";
  const listWidth = full.length - quote.length;
  return { before, continuation: quote + " ".repeat(listWidth), columns: Math.max(0, full.length) };
}

function safeReason(error: string): string {
  return error.replace(/[\[\]\r\n]/gu, " ").slice(0, 160);
}

function tableCellText(value: string): string {
  return value.replace(/\r\n?|\n/gu, " ").replace(/\\/gu, "\\\\").replace(/\|/gu, "\\|");
}

function renderReference(prepared: PreparedMarkdown, reference: PreparedReference, width: number, terminal: TerminalImages): string {
  if (reference.inTable) return `[image unavailable: ${tableCellText(reference.alt)} — inline images in tables unsupported]`;
  if (reference.error) return `[image unavailable: ${reference.alt} — ${safeReason(reference.error)}]`;
  if (!terminal.available()) return `[image unavailable: ${reference.alt} — Kitty graphics or tmux passthrough unavailable]`;

  const prefix = structuralPrefix(prepared.source, reference.start);
  const rows = terminal.render(reference.logicalId, Math.max(1, width - prefix.columns));
  if (!rows.length) {
    const reason = terminal.failure(reference.logicalId) ?? "image state unavailable";
    return `[image unavailable: ${reference.alt} — ${safeReason(reason)}]`;
  }
  const lineEnd = prepared.source.indexOf("\n", reference.end);
  const after = prepared.source.slice(reference.end, lineEnd < 0 ? prepared.source.length : lineEnd);
  const standalone = prefix.before.trim() === prefix.before.slice(0, prefix.columns).trim() && !after.trim();
  const joined = rows.join(`\n${prefix.continuation}`);
  return standalone ? joined : `\n${prefix.continuation}${joined}\n${prefix.continuation}`;
}

export function transformMarkdown(prepared: PreparedMarkdown, width: number, terminal: TerminalImages): string {
  // Terminal side effects are requested in source order; source replacement remains
  // reverse-ordered so parser offsets stay valid.
  const replacements = prepared.references.map((reference) => renderReference(prepared, reference, width, terminal));
  let output = prepared.source;
  for (let index = prepared.references.length - 1; index >= 0; index--) {
    const reference = prepared.references[index]!;
    output = output.slice(0, reference.start) + replacements[index]! + output.slice(reference.end);
  }
  return output;
}
