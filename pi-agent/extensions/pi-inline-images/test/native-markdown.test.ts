import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { parseMarkdownImages, transformMarkdown } from "../src/markdown.ts";
import { ImageSession } from "../src/session.ts";
import { TerminalImages } from "../src/terminal.ts";
import { grid, PLACEHOLDER_GLYPH } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

function installedPiRoot(): string {
  const cli = execFileSync("sh", ["-lc", "realpath \"$(command -v pi)\""], { encoding: "utf8" }).trim();
  return dirname(dirname(dirname(cli)));
}

function assistantMessage(text: string) {
  return {
    role: "assistant",
    content: [{ type: "text", text }],
    api: "fixture",
    provider: "none",
    model: "none",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop",
    timestamp: 0,
  };
}

async function renderAssistant(source: string, width: number) {
  const root = installedPiRoot();
  const module = await import(pathToFileURL(join(root, "dist/modes/interactive/components/assistant-message.js")).href);
  const theme = await import(pathToFileURL(join(root, "dist/modes/interactive/theme/theme.js")).href);
  theme.initTheme("dark", false);
  const terminal = new TerminalImages(() => 0x07123456, () => ({ widthPx: 10, heightPx: 20 }), { write: () => true }, { TERM_PROGRAM: "ghostty" }, true, { transportLimits: { minIntervalMs: 0 } });
  const image = { source: "fixture", hash: "fixture", width: 100, height: 100, previewWidth: 100, previewHeight: 100, png: Buffer.from("fixture") };
  const session = new ImageSession(terminal, async () => image);
  const prepared = await session.prepare(source.trim(), "/fixture");
  const transformer = (markdown: string, context: { messageType: string; isStreaming: boolean; availableWidth: number }) =>
    context.messageType === "assistant" && !context.isStreaming ? transformMarkdown(prepared, context.availableWidth, terminal) : markdown;
  const component = new module.AssistantMessageComponent(assistantMessage(source), false, undefined, "Thinking...", 1, [transformer]);
  return component.render(width) as string[];
}

test("row-reset Kitty grid survives Pi's native Markdown render and wrapping", async () => {
  const root = installedPiRoot();
  const tui = await import(pathToFileURL(join(root, "node_modules/@earendil-works/pi-tui/dist/index.js")).href);
  const agent = await import(pathToFileURL(join(root, "dist/index.js")).href);
  const source = "text-before\n\n![color block](./fixture.png)\n\ntext-after";
  const rows = grid(4, 2, 0x07123456);
  const transformed = source.replace("![color block](./fixture.png)", rows.join("\n"));
  const markdown = new tui.Markdown(source, 1, 0, agent.getMarkdownTheme(), undefined, {
    transform: () => transformed,
  });

  for (const width of [12, 7, 6]) {
    const rendered: string[] = markdown.render(width);
    const joined = rendered.join("\n");
    assert.equal([...joined].filter((character) => character === PLACEHOLDER_GLYPH).length, 8, `width ${width}`);
    const imageLines = rendered.map((line, index) => line.includes(PLACEHOLDER_GLYPH) ? index : -1).filter((index) => index >= 0);
    assert.equal(imageLines.length, 2, `grid keeps two rows at width ${width}`);
    assert.ok(imageLines[0] > 0, "text remains before the image");
    assert.ok(imageLines.at(-1)! < rendered.length - 1, "text remains after the image");
    assert.doesNotMatch(joined, /\x1b\[2m/, `underline-color bytes must not leak SGR dim at width ${width}`);
    for (const row of rendered.filter((line) => line.includes(PLACEHOLDER_GLYPH))) {
      assert.match(row, /\x1b\[38;2;18;52;86m/);
      assert.match(row, /\x1b\[58;2;18;52;86m/);
    }
  }
});

test("native AssistantMessage keeps exact code repros literal and renders only the later image", async () => {
  const sources = [
    "Start\n\n    ![same](a.png)\n\n![same](a.png)",
    "> ~~~\n> ![same](a.png)\n> ~~~\n\n![same](a.png)",
    "- ~~~\n  ![same](a.png)\n  ~~~\n\n![same](a.png)",
  ];
  for (const source of sources) {
    const lines = await renderAssistant(source, 24);
    const literal = lines.findIndex((line) => line.includes("![same](a.png)"));
    const bitmap = lines.findIndex((line) => line.includes(PLACEHOLDER_GLYPH));
    assert.ok(literal >= 0 && bitmap > literal, source);
  }
  const unmatched = (await renderAssistant("An unmatched ` marker\n\n![real](a.png)", 24)).join("\n");
  assert.match(unmatched, new RegExp(PLACEHOLDER_GLYPH, "u"));
});

test("native cached components retain immutable geometry across changed bytes and later failures", async () => {
  const root = installedPiRoot();
  const module = await import(pathToFileURL(join(root, "dist/modes/interactive/components/assistant-message.js")).href);
  const theme = await import(pathToFileURL(join(root, "dist/modes/interactive/theme/theme.js")).href);
  theme.initTheme("dark", false);
  let allocated = 0x07123450;
  const terminal = new TerminalImages(() => ++allocated, () => ({ widthPx: 10, heightPx: 20 }), { write: () => true }, { TERM_PROGRAM: "ghostty" }, true, { transportLimits: { minIntervalMs: 0 } });
  const versions = [
    { source: "fixture", hash: "large", width: 100, height: 100, previewWidth: 100, previewHeight: 100, png: Buffer.from("large") },
    { source: "fixture", hash: "small", width: 20, height: 20, previewWidth: 20, previewHeight: 20, png: Buffer.from("small") },
  ];
  let load = 0;
  const session = new ImageSession(terminal, async () => {
    const image = versions[load++];
    if (!image) throw new Error("changed file unreadable");
    return image;
  });
  const source = "Before\n\n![same](/tmp/image.png)\n\nAfter";
  const transformer = (markdown: string, context: { messageType: string; isStreaming: boolean; availableWidth: number }) => {
    const prepared = session.markdown.get(markdown);
    return prepared ? transformMarkdown(prepared, context.availableWidth, terminal) : markdown;
  };
  const component = () => new module.AssistantMessageComponent(assistantMessage(source), false, undefined, "Thinking...", 1, [transformer]);

  const firstPrepared = await session.prepare(source, "/fixture");
  const first = component();
  assert.equal(first.render(16).join("\n").split(PLACEHOLDER_GLYPH).length - 1, 50);
  const secondPrepared = await session.prepare(source, "/fixture");
  const second = component();
  assert.equal(second.render(16).join("\n").split(PLACEHOLDER_GLYPH).length - 1, 2);
  assert.notEqual(firstPrepared.references[0].logicalId, secondPrepared.references[0].logicalId, "changed bytes receive a distinct immutable resource ID");
  assert.equal(first.render(16).join("\n").split(PLACEHOLDER_GLYPH).length - 1, 50, "old same-width Markdown cache remains compatible with its old resource");

  await session.prepare(source, "/fixture");
  const failed = component().render(40).join("\n").replace(/\x1b(?:\][^\x07]*\x07|\[[0-?]*[ -/]*[@-~])/gu, "");
  assert.match(failed.replace(/\s+/gu, " "), /image unavailable: same — changed file unreadable/);
  assert.equal(first.render(16).join("\n").split(PLACEHOLDER_GLYPH).length - 1, 50, "a later failure does not invalidate cached old grids");
});

test("native AssistantMessage preserves GFM table columns with an explicit in-cell notice", async () => {
  const source = "| image | text |\n| --- | --- |\n| ![x](a.png) | tail |";
  const lines = await renderAssistant(source, 16);
  const joined = lines.join("\n");
  assert.doesNotMatch(joined, new RegExp(PLACEHOLDER_GLYPH, "u"));
  const stripAnsi = (value: string) => value.replace(/\x1b(?:\][^\x07]*\x07|\[[0-?]*[ -/]*[@-~])/gu, "");
  const words = stripAnsi(joined).replace(/[^A-Za-z]/gu, "");
  assert.match(words, /imagesintablesunsupported/);
  const tableLines = lines.map(stripAnsi).filter((line) => line.includes("│"));
  assert.ok(tableLines.length > 0 && tableLines.every((line) => (line.match(/│/gu) || []).length >= 3), "native table keeps two bordered columns");
  assert.match(tableLines.map((line) => line.split("│")[2]?.trim() || "").join(""), /tail/, "tail remains in the second column");
});

test("native AssistantMessage uses the first normalized reference definition", async () => {
  const root = installedPiRoot();
  const module = await import(pathToFileURL(join(root, "dist/modes/interactive/components/assistant-message.js")).href);
  const theme = await import(pathToFileURL(join(root, "dist/modes/interactive/theme/theme.js")).href);
  theme.initTheme("dark", false);
  const cases = [
    { source: "![x][id]\n\n[id]: first.png\n[id]: second.png", expected: "first.png" },
    { source: "![x][foo bar]\n\n[Foo   Bar]: normalized-first.png\n[FOO BAR]: normalized-second.png", expected: "normalized-first.png" },
  ];
  for (const { source, expected } of cases) {
    let loaded = "";
    const terminal = new TerminalImages(() => 0x07123456, () => ({ widthPx: 10, heightPx: 20 }), { write: () => true }, { TERM_PROGRAM: "ghostty" }, true, { transportLimits: { minIntervalMs: 0 } });
    const session = new ImageSession(terminal, async (href) => {
      loaded = href;
      return { source: href, hash: href, width: 20, height: 20, previewWidth: 20, previewHeight: 20, png: Buffer.from(href) };
    });
    const prepared = await session.prepare(source, "/fixture");
    const transformer = (markdown: string, context: { availableWidth: number }) => transformMarkdown(prepared, context.availableWidth, terminal);
    const lines = new module.AssistantMessageComponent(assistantMessage(source), false, undefined, "Thinking...", 1, [transformer]).render(40) as string[];
    assert.equal(loaded, expected);
    assert.match(lines.join("\n"), new RegExp(PLACEHOLDER_GLYPH, "u"));
  }
});

test("native table notices escape decoded pipe, backslash, and newline alt text", async () => {
  const cases = [
    { source: "| image | text |\n| --- | --- |\n| ![x\\|y](a.png) | tail |", alt: "x|y" },
    { source: "| image | text |\n| --- | --- |\n| ![x\\\\y\\|z&#10;q](a.png) | tail |", alt: "x\\y|z\nq" },
  ];
  const stripAnsi = (value: string) => value.replace(/\x1b(?:\][^\x07]*\x07|\[[0-?]*[ -/]*[@-~])/gu, "");
  for (const { source, alt } of cases) {
    assert.equal(parseMarkdownImages(source)[0]?.alt, alt);
    const lines = (await renderAssistant(source, 80)).map(stripAnsi);
    assert.doesNotMatch(lines.join("\n"), new RegExp(PLACEHOLDER_GLYPH, "u"));
    const tableLines = lines.filter((line) => line.includes("│"));
    assert.ok(tableLines.every((line) => (line.match(/│/gu) || []).length >= 3), source);
    assert.match(tableLines.map((line) => line.split("│")[2]?.trim() || "").join(""), /tail/, "tail stays in column two");
  }
});

test("native AssistantMessage output selects a precreated custom placement and emits no render traffic", async () => {
  const root = installedPiRoot();
  const module = await import(pathToFileURL(join(root, "dist/modes/interactive/components/assistant-message.js")).href);
  const theme = await import(pathToFileURL(join(root, "dist/modes/interactive/theme/theme.js")).href);
  theme.initTheme("dark", false);
  const writes: string[] = [];
  const terminal = new TerminalImages(
    () => 0x07123456,
    () => ({ widthPx: 10, heightPx: 20 }),
    { write: (value: Buffer) => { writes.push(value.toString("utf8")); return true; } },
    { TERM_PROGRAM: "ghostty" },
    true,
    { transportLimits: { minIntervalMs: 0 } },
  );
  const source = "before\n\n![mapped](mapped.png)\n\nafter";
  const session = new ImageSession(terminal, async () => ({
    source: "mapped", hash: "mapped", width: 120, height: 80,
    previewWidth: 120, previewHeight: 80, png: Buffer.from("mapped"),
  }));
  const prepared = await session.prepare(source, "/fixture");
  const transformer = (markdown: string, context: { availableWidth: number }) => transformMarkdown(prepared, context.availableWidth, terminal);
  const beforeRender = writes.length;
  const rendered = (new module.AssistantMessageComponent(assistantMessage(source), false, undefined, "Thinking...", 1, [transformer]).render(24) as string[]).join("\n");
  assert.equal(writes.length, beforeRender);
  const underline = /\x1b\[58;2;0;0;(\d+)m/u.exec(rendered);
  assert.ok(underline);
  const placementId = underline[1]!;
  assert.ok(writes.some((value) => value.includes(`p=${placementId},U=1`)), "native grid underline selects a prepared protocol placement ID");
  assert.ok(rendered.includes(`${PLACEHOLDER_GLYPH}\u0305\u0305\u033f`), "native output retains image-ID high-byte diacritic");
  await session.reset(true);
});

test("native AssistantMessage still renders paragraph and list images in source order", async () => {
  for (const source of ["before\n\n![x](a.png)\n\nafter", "- before ![x](a.png) after"]) {
    const joined = (await renderAssistant(source, 24)).join("\n");
    assert.match(joined, new RegExp(PLACEHOLDER_GLYPH, "u"));
    assert.ok(joined.indexOf("before") < joined.indexOf(PLACEHOLDER_GLYPH));
    assert.ok(joined.indexOf(PLACEHOLDER_GLYPH) < joined.indexOf("after"));
  }
});
