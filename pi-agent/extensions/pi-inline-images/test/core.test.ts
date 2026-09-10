import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";
import { loadImage } from "../src/images.ts";
import { parseMarkdownImages, transformMarkdown } from "../src/markdown.ts";
import { assistantTextBlocks, ImageSession, MAX_ACTIVE_IMAGES } from "../src/session.ts";
import { TerminalImages, geometry, supportsKitty } from "../src/terminal.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = join(HERE, "fixtures/color-block.png");
const kittyEnv = { TERM_PROGRAM: "ghostty" };
const image = { source: FIXTURE, hash: "abc", width: 40, height: 40, png: readFileSync(FIXTURE) };

function runtime() {
  let id = 0x71123400;
  const writes: string[] = [];
  const terminal = new TerminalImages(() => ++id, () => ({ widthPx: 10, heightPx: 20 }), { write: (value) => writes.push(value) }, kittyEnv, true);
  return { terminal, writes };
}

test("position-aware AST discovery preserves source order and excludes code", () => {
  const markdown = [
    "`![inline](skip.png)` then ![one](a.png)",
    "",
    "```md",
    "![fenced](skip2.png)",
    "```",
    "",
    "> ![two](<b image.webp>)",
    "",
    "![three][ref]",
    "",
    "[ref]: c.jpg",
  ].join("\n");
  const refs = parseMarkdownImages(markdown);
  assert.deepEqual(refs.map((ref) => [ref.alt, ref.href]), [["one", "a.png"], ["two", "b image.webp"], ["three", "c.jpg"]]);
  assert.ok(refs.every((ref, index) => index === 0 || ref.start > refs[index - 1].start));
});

test("parser-owned spans handle review code repros, unmatched ticks, escapes, references, and tables", () => {
  const repeated = [
    "Start\n\n    ![same](a.png)\n\n![same](a.png)",
    "> ~~~\n> ![same](a.png)\n> ~~~\n\n![same](a.png)",
    "- ~~~\n  ![same](a.png)\n  ~~~\n\n![same](a.png)",
  ];
  for (const source of repeated) assert.equal(parseMarkdownImages(source)[0]?.start, source.lastIndexOf("![same]"));
  const unmatched = "An unmatched ` marker\n\n\\![escaped](no.png)\n\n![real][id]\n\n[id]: a.png";
  const refs = parseMarkdownImages(unmatched);
  assert.deepEqual(refs.map((ref) => [ref.raw, ref.href]), [["![real][id]", "a.png"]]);
  const table = parseMarkdownImages("| image | text |\n| --- | --- |\n| ![x](a.png) | tail |");
  assert.equal(table[0]?.inTable, true);
});

test("local, file, and data resources decode to bounded PNG state", async () => {
  const local = await loadImage(FIXTURE, HERE);
  const relative = await loadImage("fixtures/color-block.png", HERE);
  const file = await loadImage(pathToFileURL(FIXTURE).href, "/");
  const data = await loadImage(`data:image/png;base64,${readFileSync(FIXTURE).toString("base64")}`, "/");
  assert.equal(local.hash, relative.hash);
  assert.equal(local.hash, file.hash);
  assert.equal(local.hash, data.hash);
  assert.ok(local.width > 0 && local.height > 0 && local.png.length > 0);
  await assert.rejects(loadImage("ftp://example.test/x.png", HERE), /unsupported resource scheme/);
  await assert.rejects(loadImage("file://remotehost/x.png", HERE), /remote file URL hosts/);
  await assert.rejects(loadImage("data:text/plain;base64,SGk=", HERE), /unsupported image format/);
});

test("transform inserts ready images in place and gives visible failures", async () => {
  const { terminal } = runtime();
  const session = new ImageSession(terminal, async (href) => href === "bad.png" ? Promise.reject(new Error("decoding failed")) : image);
  const source = "before ![ok](good.png) middle ![bad](bad.png) after";
  const prepared = await session.prepare(source, "/work");
  const output = transformMarkdown(prepared, 30, terminal);
  assert.ok(output.indexOf("before") < output.indexOf("\u{10eeee}"));
  assert.ok(output.indexOf("\u{10eeee}") < output.indexOf("middle"));
  assert.match(output, /image unavailable: bad — decoding failed/);
  assert.ok(output.indexOf("decoding failed") < output.indexOf("after"));
});

test("list and quote placements retain structural continuation prefixes", async () => {
  const { terminal } = runtime();
  const session = new ImageSession(terminal, async () => image);
  const source = "> - lead ![ok](good.png) tail";
  const output = transformMarkdown(await session.prepare(source, "/work"), 20, terminal);
  const gridLines = output.split("\n").filter((line) => line.includes("\u{10eeee}"));
  assert.ok(gridLines.length > 1);
  assert.ok(gridLines.every((line) => line.startsWith(">   ")));
  assert.match(output, /\n> {3,}tail/);
});

test("resize deletes old owned placement before creating replacement; clear deletes only owned image", () => {
  const { terminal, writes } = runtime();
  terminal.set("logical", image);
  terminal.render("logical", 5);
  const firstWrites = writes.length;
  terminal.render("logical", 2);
  assert.ok(writes.slice(firstWrites).some((value) => value.includes("a=d,d=i")), "old placement deleted on geometry change");
  terminal.clear();
  assert.ok(writes.some((value) => value.includes("a=d,d=I")), "owned image deleted on clear");
});

test("capability boundary and geometry are deterministic", () => {
  assert.equal(supportsKitty({ TERM_PROGRAM: "ghostty" }), true);
  assert.equal(supportsKitty({ TERM_PROGRAM: "tmux", GHOSTTY_RESOURCES_DIR: "/x", TMUX: "yes" }, () => false), false);
  assert.equal(supportsKitty({ TERM_PROGRAM: "tmux", GHOSTTY_RESOURCES_DIR: "/x", TMUX: "yes" }, () => true), true);
  assert.deepEqual(geometry(image, 2, { widthPx: 10, heightPx: 20 }), { columns: 2, rows: 1 });
});

test("restore follows the active branch, reset clears state, and repeated paths are not globally suppressed", async () => {
  const { terminal } = runtime();
  let loads = 0;
  const session = new ImageSession(terminal, async () => { loads++; return image; });
  const first = "![same](same.png)";
  const second = "again ![same](same.png)";
  await session.restore([
    { type: "message", message: { role: "assistant", content: [{ type: "text", text: first }] } },
    { type: "message", message: { role: "assistant", content: [{ type: "text", text: second }] } },
  ], "/work");
  assert.equal(loads, 2, "separate source occurrences receive separate logical IDs");
  await session.prepare(first, "/work");
  assert.equal(loads, 3, "byte-identical later text reloads the resource so file changes become visible consistently");
  assert.equal(session.markdown.size, 2);
  session.reset();
  assert.equal(session.markdown.size, 0);
  assert.equal(terminal.count(), 0);
  assert.deepEqual(assistantTextBlocks({ role: "assistant", content: [{ type: "thinking", text: "no" }, { type: "text", text: "  yes\n" }] }), ["yes"], "cache keys match Pi AssistantMessage's native trim");
});

test("capacity exhaustion is visible instead of silently dropping references", async () => {
  const { terminal } = runtime();
  const session = new ImageSession(terminal, async () => image);
  const source = Array.from({ length: MAX_ACTIVE_IMAGES + 1 }, (_, index) => `![${index}](${index}.png)`).join("\n\n");
  const prepared = await session.prepare(source, "/work");
  assert.equal(prepared.references.at(-1)?.error, `inline image capacity reached (${MAX_ACTIVE_IMAGES})`);
  assert.match(transformMarkdown(prepared, 20, terminal), /inline image capacity reached/);
});
