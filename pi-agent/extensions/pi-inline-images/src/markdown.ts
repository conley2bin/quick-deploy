import { createHash } from "node:crypto";
import { marked } from "marked";
import type { TerminalImages } from "./terminal.ts";

export interface ImageReference {
  start: number;
  end: number;
  raw: string;
  href: string;
  alt: string;
  ordinal: number;
}

export type PreparedReference = ImageReference & { logicalId: string; error?: string };

export interface PreparedMarkdown {
  source: string;
  cwd: string;
  references: PreparedReference[];
}

function imageTokens(markdown: string): Array<{ raw: string; href: string; alt: string }> {
  const found: Array<{ raw: string; href: string; alt: string }> = [];
  const tokens = marked.lexer(markdown);
  marked.walkTokens(tokens, (token) => {
    if (token.type === "image") found.push({ raw: token.raw, href: token.href, alt: token.text || "image" });
  });
  return found;
}

function tickRunAt(source: string, offset: number): number {
  let length = 0;
  while (source[offset + length] === "`") length++;
  return length;
}

/** Locate only image tokens accepted by Marked, while skipping fenced and inline code. */
export function parseMarkdownImages(markdown: string): ImageReference[] {
  const expected = imageTokens(markdown);
  const references: ImageReference[] = [];
  let tokenIndex = 0;
  let inlineTicks = 0;
  let inComment = false;
  let fence: { character: string; length: number } | undefined;
  let lineStart = true;

  for (let offset = 0; offset < markdown.length && tokenIndex < expected.length;) {
    if (lineStart) {
      const newline = markdown.indexOf("\n", offset);
      const end = newline < 0 ? markdown.length : newline;
      const line = markdown.slice(offset, end);
      const match = /^ {0,3}(`{3,}|~{3,})/u.exec(line);
      if (match) {
        const character = match[1][0];
        if (!fence) fence = { character, length: match[1].length };
        else if (fence.character === character && match[1].length >= fence.length) fence = undefined;
        offset = end;
        continue;
      }
    }
    const character = markdown[offset];
    if (inComment) {
      const end = markdown.indexOf("-->", offset);
      if (end < 0) break;
      inComment = false;
      offset = end + 3;
      continue;
    }
    if (!inlineTicks && markdown.startsWith("<!--", offset)) {
      inComment = true;
      offset += 4;
      continue;
    }
    if (character === "\n") { lineStart = true; offset++; continue; }
    if (lineStart) lineStart = false;
    if (fence) { offset++; continue; }
    if (character === "`" && (offset === 0 || markdown[offset - 1] !== "\\")) {
      const run = tickRunAt(markdown, offset);
      if (!inlineTicks) inlineTicks = run;
      else if (run === inlineTicks) inlineTicks = 0;
      offset += run;
      continue;
    }
    if (!inlineTicks && character === "!" && (offset === 0 || markdown[offset - 1] !== "\\")) {
      const candidate = expected[tokenIndex];
      if (markdown.startsWith(candidate.raw, offset)) {
        references.push({ start: offset, end: offset + candidate.raw.length, ...candidate, ordinal: tokenIndex });
        tokenIndex++;
        offset += candidate.raw.length;
        continue;
      }
    }
    offset++;
  }
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

export function transformMarkdown(prepared: PreparedMarkdown, width: number, terminal: TerminalImages): string {
  let output = prepared.source;
  for (const reference of [...prepared.references].reverse()) {
    let replacement: string;
    if (reference.error) replacement = `[image unavailable: ${reference.alt} — ${safeReason(reference.error)}]`;
    else if (!terminal.available()) replacement = `[image unavailable: ${reference.alt} — Kitty graphics or tmux passthrough unavailable]`;
    else {
      const prefix = structuralPrefix(prepared.source, reference.start);
      const rows = terminal.render(reference.logicalId, Math.max(1, width - prefix.columns));
      if (!rows.length) replacement = `[image unavailable: ${reference.alt} — image state unavailable]`;
      else {
        const lineEnd = prepared.source.indexOf("\n", reference.end);
        const after = prepared.source.slice(reference.end, lineEnd < 0 ? prepared.source.length : lineEnd);
        const standalone = prefix.before.trim() === prefix.before.slice(0, prefix.columns).trim() && !after.trim();
        const joined = rows.join(`\n${prefix.continuation}`);
        replacement = standalone ? joined : `\n${prefix.continuation}${joined}\n${prefix.continuation}`;
      }
    }
    output = output.slice(0, reference.start) + replacement + output.slice(reference.end);
  }
  return output;
}
