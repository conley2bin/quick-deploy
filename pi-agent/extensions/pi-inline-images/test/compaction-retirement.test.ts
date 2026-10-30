import assert from "node:assert/strict";
import test from "node:test";
import { ImageSession } from "../src/session.ts";
import { TerminalImages } from "../src/terminal.ts";

const message = (text: string) => ({ role: "assistant", content: [{ type: "text", text }] });
const entry = (value: ReturnType<typeof message>) => ({ type: "message", message: value });
const image = (hash: string) => ({ source: hash, hash, width: 20, height: 20, png: Buffer.from(hash) });
function backend() {
  let id = 100;
  const terminal = new TerminalImages(() => ++id, () => ({ widthPx: 10, heightPx: 20 }), { write: () => true }, {}, true);
  terminal.setViewerManaged(true); // Hidden viewer: exercise residency without physical terminal traffic.
  return terminal;
}

test("compaction reuse keeps the surviving duplicate's pixels and block identity, retiring only inline history", async () => {
  const terminal = backend();
  let loads = 0;
  const session = new ImageSession(terminal, async () => image(`version-${++loads}`));
  const source = "![same](changing.png)";
  const first = message(source);
  const second = message(source);
  const old = (await session.prepareMessage(first, "/fixture"))[0]!;
  const current = (await session.prepareMessage(second, "/fixture"))[0]!;
  await terminal.prepare("read:independent", image("read"));
  await session.restore([entry(second)], "/fixture", true);
  assert.equal(session.withRenderBlock(second, 0, () => session.preparedForRender(source)), current);
  assert.equal(loads, 2, "a retained occurrence must not reread changed files");
  assert.equal(terminal.has(old.references[0]!.logicalId), false);
  assert.equal(terminal.has(current.references[0]!.logicalId), true);
  assert.equal(terminal.residentBytes("inline"), image("version-2").png.length);
  assert.equal(terminal.residentBytes("read"), 4);
  await session.restore([], "/fixture", true);
  assert.equal(session.markdown.size, 0);
  assert.equal(terminal.count("inline"), 0);
  assert.equal(terminal.residentBytes("inline"), 0);
  assert.equal(terminal.count("read"), 1);
  await session.reset(true);
});

test("shutdown during compaction preparation cannot repopulate catalogs, buffers or message bindings", async () => {
  const terminal = backend();
  let complete!: (value: ReturnType<typeof image>) => void;
  const session = new ImageSession(terminal, () => new Promise((resolve) => { complete = resolve; }));
  const current = message("![late](late.png)");
  const restoring = session.restore([entry(current)], "/fixture", true);
  // retainOwner's async boundary precedes the loader.
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(typeof complete, "function");
  await session.reset(true);
  complete(image("late"));
  await restoring;
  assert.equal(terminal.count(), 0);
  assert.equal(terminal.residentBytes(), 0);
  assert.equal(session.markdown.size, 0);
  assert.ok(session.withRenderBlock(current, 0, () => session.preparedForRender(current.content[0]!.text))?.references.every((reference) => reference.error),
    "stale restore must not reattach a successful prepared block after reset");
});
