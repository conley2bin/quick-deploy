import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import sharp from "sharp";
import { HostImageOwnershipAdapter } from "../src/host-adapter.ts";
import { loadImage, type LoadedImage } from "../src/images.ts";
import { transformMarkdown } from "../src/markdown.ts";
import { ImageSession } from "../src/session.ts";
import { geometry, TerminalImages } from "../src/terminal.ts";
import { PLACEHOLDER_GLYPH } from "../vendor/pi-tmux-images/kitty-placeholder.ts";
import { installedPiRoot } from "./pi-root.ts";

const root = installedPiRoot();
const timestamp = "2026-09-29T00:00:00.000Z";
const fixture = resolve("test/fixtures/color-block.png");
const countGlyphs = (lines: string[] | string) => [...(Array.isArray(lines) ? lines.join("\n") : lines)].filter((c) => c === PLACEHOLDER_GLYPH).length;

function message(...texts: string[]) {
  return {
    role: "assistant" as const, content: texts.map((text) => ({ type: "text" as const, text })),
    api: "fixture", provider: "none", model: "none", timestamp: 0, stopReason: "stop",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
  };
}

async function installedHost() {
  const agent = await import(pathToFileURL(resolve(root, "dist/index.js")).href);
  const assistant = await import(pathToFileURL(resolve(root, "dist/modes/interactive/components/assistant-message.js")).href);
  const theme = await import(pathToFileURL(resolve(root, "dist/modes/interactive/theme/theme.js")).href);
  const tui = await import(pathToFileURL(resolve(root, "node_modules/@earendil-works/pi-tui/dist/index.js")).href);
  theme.initTheme("dark", false);
  return { version: agent.VERSION as string, AssistantMessageComponent: assistant.AssistantMessageComponent, Markdown: tui.Markdown };
}

async function pairedFixture(
  messages: ReturnType<typeof message>[],
  fullscreen: boolean,
  loader: (href: string, cwd: string) => Promise<LoadedImage> = loadImage,
  copyFirst = true,
  bindImmediately = true,
) {
  const { version, AssistantMessageComponent, Markdown } = await installedHost();
  const wire: Buffer[] = [];
  let imageId = 0x07123450;
  const terminal = new TerminalImages(
    () => ++imageId, () => ({ widthPx: 10, heightPx: 20 }),
    { write: (bytes: Buffer) => { wire.push(bytes); return true; } },
    { TERM_PROGRAM: "ghostty" }, true, { transportLimits: { minIntervalMs: 0 } },
  );
  const session = new ImageSession(terminal, loader);
  const originals = messages.map((item) => JSON.stringify(item));
  const prepared = [];
  for (const item of messages) prepared.push(await session.prepareMessage(item, process.cwd()));
  const { installAdapter } = await import(pathToFileURL(resolve("../pi-copy-links/src/adapter.ts")).href);
  const copy = installAdapter({ version, active: () => fullscreen, copy: async () => undefined,
    cwd: () => process.cwd(), open: async () => undefined, notify: () => undefined, button: (text: string) => text });
  const transforms: Array<{ source: string; output: string }> = [];
  const inline = (markdown: string, context: { messageType: string; isStreaming: boolean; availableWidth: number }) => {
    if (context.messageType !== "assistant" || context.isStreaming) return markdown;
    const selected = session.preparedForRender(markdown);
    const output = selected ? transformMarkdown(selected, context.availableWidth, terminal) : markdown;
    transforms.push({ source: markdown, output });
    return output;
  };
  const transformers = copyFirst ? [copy.transform, inline] : [inline, copy.transform];
  const components = messages.map((item) => new AssistantMessageComponent(item, false, undefined, "Thinking...", 1, transformers));
  const entries = messages.map((item, index) => ({ type: "message", id: `test-${index}`, parentId: null, timestamp, message: item }));
  const ui = { children: [...components], terminal: { columns: 80 }, render: () => [], invalidate() {}, requestRender() {} };
  const host = new HostImageOwnershipAdapter(session, () => undefined,
    { version, sessionEntryToContextMessages: (entry) => entry.message ? [entry.message] : [],
      isAssistantTextMarkdown: (value) => value instanceof Markdown });
  host.setTui(ui as never);
  const bind = () => host.reconcile(entries, entries);
  if (bindImmediately) assert.equal(bind(), true);
  const markdowns = (index: number) => components[index].children.flatMap((child: { children?: object[] }) => child.children ?? [])
    .filter((child: object) => child instanceof Markdown);
  return { components, messages, prepared, session, terminal, wire, transforms, markdowns, bind, host, copy,
    async close() {
      host.dispose();
      copy.dispose();
      await session.reset(true);
      messages.forEach((item, index) => assert.equal(JSON.stringify(item), originals[index], "raw model/session message stays byte-exact"));
      components.forEach((component) => assert.equal(Object.prototype.hasOwnProperty.call(component, "render"), false,
        "assistant render override is restored on disposal"));
    } };
}

function uploadedPngs(wire: Buffer[]): Buffer[] {
  return wire.filter((bytes) => bytes.includes("a=t"))
    .map((bytes) => Buffer.from([...bytes.toString("utf8").matchAll(/\x1b_G[^;]*;([A-Za-z0-9+/=]*)\x1b\\/gu)]
      .map((match) => match[1]).join(""), "base64"));
}

test("a 1280x430 PNG survives fullscreen decoration at its original uploaded resolution", async () => {
  const temp = mkdtempSync(resolve(tmpdir(), "pi-inline-wide-"));
  const imagePath = resolve(temp, "wide.png");
  const original = await sharp({ create: { width: 1280, height: 430, channels: 4, background: "#78502a" } }).png().toBuffer();
  writeFileSync(imagePath, original);
  let f: Awaited<ReturnType<typeof pairedFixture>> | undefined;
  try {
    f = await pairedFixture([message(`Frame ![wide](${imagePath}) end`)], true);
    const output = f.components[0].render(64).join("\n");
    const size = geometry({ width: 1280, height: 430 } as LoadedImage, 62, { widthPx: 10, heightPx: 20 });
    assert.equal(countGlyphs(output), size.columns * size.rows, "final native Markdown retains a real mapped grid");
    assert.deepEqual(uploadedPngs(f.wire), [original], "the exact full-resolution PNG, not a scaled preview, is uploaded");
    assert.doesNotMatch(output, /image unavailable/u);
  } finally { await f?.close(); rmSync(temp, { recursive: true, force: true }); }
});

test("the paired adapters preserve the image when registration order is reversed", async () => {
  const source = `Before ![color](${fixture})\n\n\`\`\`sh\necho after\n\`\`\``;
  const f = await pairedFixture([message(source)], true, loadImage, false);
  try {
    const output = f.components[0].render(64).join("\n");
    assert.equal(f.transforms.length, 2);
    assert.equal(countGlyphs(output), 2);
    assert.match(output, /pi-copy:\/\//u);
    assert.doesNotMatch(output, /image unavailable/u);
  } finally { await f.close(); }
});

for (const fullscreen of [true, false]) {
  test(`late binding invalidates only newly associated assistant caches once in ${fullscreen ? "fullscreen" : "regular"} mode`, async () => {
    const repeated = `Same ![color](${fixture}) end`;
    const f = await pairedFixture([message(repeated), message(repeated)], fullscreen, loadImage, true, false);
    try {
      const unbound = f.components.map((component) => component.render(64).join("\n"));
      assert.equal(unbound.reduce((sum, text) => sum + countGlyphs(text), 0), 0);
      assert.ok(unbound.every((text) => text.includes("host image coordination")), "each unbound Markdown caches a visible notice");
      const invalidations = [0, 0];
      for (const [index] of f.components.entries()) {
        const block = f.markdowns(index)[0];
        const original = block.invalidate.bind(block);
        block.invalidate = () => { invalidations[index]++; original(); };
      }

      assert.equal(f.bind(), true);
      assert.deepEqual(invalidations, [1, 1], "each newly bound assistant clears its stale native Markdown cache once");
      assert.deepEqual(f.components.map((component) => countGlyphs(component.render(64))), [2, 2]);
      const beforeStableBind = [...invalidations];
      assert.equal(f.bind(), true);
      assert.deepEqual(invalidations, beforeStableBind, "stable reconciliation performs no additional cache invalidation");
      f.components.forEach((component) => component.render(64));
      const afterStableRender = [...invalidations]; // copy-links may invalidate during its own fullscreen decorate pass
      assert.equal(f.bind(), true);
      assert.deepEqual(invalidations, afterStableRender, "stable rerender cannot cause a later host invalidation");
    } finally { await f.close(); }
  });
}

test("unverifiable Markdown child association emits the coordination notice and restores temporary overrides", async () => {
  const first = `First ![one](${fixture})`;
  const second = `Second ![two](${fixture})`;
  const f = await pairedFixture([message(first, second)], true);
  try {
    const blocks = f.markdowns(0);
    const nativeRender = blocks[1].render;
    Object.defineProperty(blocks[1], "render", { value: nativeRender, writable: false, configurable: true });
    const output = f.components[0].render(64).join("\n");
    assert.equal(countGlyphs(output), 0, "no image is assigned by a guessed invocation cursor");
    assert.match(output, /host image coordination\s+unavailable for this Markdown occurrence/u);
    assert.equal(Object.getOwnPropertyDescriptor(blocks[1], "render")?.value, nativeRender);
    assert.equal(Object.prototype.hasOwnProperty.call(blocks[0], "render"), false, "earlier temporary override was unwound");
  } finally { await f.close(); }
});

for (const fullscreen of [true, false]) {
  test(`both adapters render the finalized bitmap in ${fullscreen ? "fullscreen" : "regular"} mode`, async () => {
    const source = `Before\n\n![color block](${fixture})\n\n\`\`\`sh\necho paired\n\`\`\`\n\nAfter`;
    const f = await pairedFixture([message(source)], fullscreen);
    try {
      const rendered = f.components[0].render(64).join("\n");
      assert.equal(f.transforms.length, fullscreen ? 2 : 1,
        "the real copy-links adapter triggers two native transform passes only in fullscreen");
      assert.ok(f.transforms.every(({ output }) => countGlyphs(output) === 2), "every native pass selects the prepared grid");
      assert.ok(rendered.includes(PLACEHOLDER_GLYPH), `final render must retain the bitmap; got ${rendered}`);
      assert.equal(countGlyphs(rendered), 2);
      assert.doesNotMatch(rendered, /host image coordination unavailable|\x1b_G/u);
      if (fullscreen) assert.match(rendered, /pi-copy:\/\//u, "second native pass decorates the source-exact code block");
      else assert.doesNotMatch(rendered, /pi-copy:\/\//u);
      assert.deepEqual(uploadedPngs(f.wire), [readFileSync(fixture)], "display grid names the exact uploaded PNG");
      assert.equal(Object.prototype.hasOwnProperty.call(f.markdowns(0)[0], "render"), false,
        "temporary Markdown render override is restored after the parent render");
    } finally { await f.close(); }
  });

  test(`two distinct blocks and a later reply keep their bindings in ${fullscreen ? "fullscreen" : "regular"} mode`, async () => {
    const a = `First ![one](${fixture}) end`;
    const b = `Second ![two](${fixture}) end`;
    const later = `Later ![three](${fixture}) end`;
    const f = await pairedFixture([message(a, b), message(later)], fullscreen);
    try {
      const first = f.components[0].render(48).join("\n");
      assert.equal(countGlyphs(first), 4);
      assert.ok(first.indexOf("First") < first.indexOf(PLACEHOLDER_GLYPH));
      assert.ok(first.indexOf("Second") > first.indexOf(PLACEHOLDER_GLYPH));
      assert.doesNotMatch(first, /host image coordination unavailable/u);
      const markdowns = f.markdowns(0);
      assert.equal(markdowns.length, 2, "native Pi keeps separate Markdown components per assistant text block");
      const initialTransforms = f.transforms.length;
      markdowns[1].invalidate();
      const rerendered = f.components[0].render(48).join("\n");
      assert.equal(countGlyphs(rerendered), 4, "a cached first block cannot shift the second block's image binding");
      assert.doesNotMatch(rerendered, /host image coordination unavailable/u);
      if (!fullscreen) assert.deepEqual(f.transforms.slice(initialTransforms).map(({ source }) => source), [b],
        "regular Pi invokes only the independently invalidated second block");
      assert.equal(countGlyphs(f.components[1].render(48)), 2, "a later assistant reply binds to its own prepared message");
      assert.equal(f.terminal.count(), 3, "each different Markdown occurrence has one custom image owner");
    } finally { await f.close(); }
  });

  test(`identical blocks keep distinct changed-file bitmaps in ${fullscreen ? "fullscreen" : "regular"} mode`, async () => {
    const temp = mkdtempSync(resolve(tmpdir(), "pi-inline-paired-"));
    const imagePath = resolve(temp, "changing.png");
    const red = await sharp({ create: { width: 20, height: 20, channels: 4, background: "red" } }).png().toBuffer();
    const blue = await sharp({ create: { width: 40, height: 20, channels: 4, background: "blue" } }).png().toBuffer();
    writeFileSync(imagePath, red);
    const repeated = `Same ![changed](${imagePath}) end`;
    let loads = 0;
    let f: Awaited<ReturnType<typeof pairedFixture>> | undefined;
    try {
      f = await pairedFixture([message(repeated, repeated)], fullscreen, async (href, cwd) => {
        const loaded = await loadImage(href, cwd);
        if (++loads === 1) writeFileSync(imagePath, blue);
        return loaded;
      });
      assert.equal(loads, 2);
      assert.notEqual(f.prepared[0][0].references[0].logicalId, f.prepared[0][1].references[0].logicalId,
        "a changed file receives a distinct immutable logical image identity");
      assert.deepEqual(uploadedPngs(f.wire), [red, blue], "both historical versions upload their original PNG bytes");
      const rendered = f.components[0].render(64).join("\n");
      assert.equal(countGlyphs(rendered), 6, "first 2-cell grid precedes the second 4-cell grid");
      assert.deepEqual(f.markdowns(0).map((block: { render(width: number): string[] }) => countGlyphs(block.render(64))), [2, 4]);
      const twoBlocks = f.markdowns(0);
      twoBlocks[0].invalidate();
      assert.equal(countGlyphs(f.components[0].render(64)), 6);
      twoBlocks[1].invalidate();
      assert.equal(countGlyphs(f.components[0].render(64)), 6);
      assert.deepEqual(twoBlocks.map((block: { render(width: number): string[] }) => countGlyphs(block.render(64))), [2, 4],
        "independent invalidation never assigns a later PNG to the earlier identical Markdown block");
      assert.equal(f.terminal.count(), 2);
    } finally {
      await f?.close();
      rmSync(temp, { recursive: true, force: true });
    }
  });
}
