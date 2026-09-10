import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import { grid, PLACEHOLDER_GLYPH } from "../vendor/pi-tmux-images/kitty-placeholder.ts";

function installedPiRoot(): string {
  const cli = execFileSync("sh", ["-lc", "realpath \"$(command -v pi)\""], { encoding: "utf8" }).trim();
  return dirname(dirname(dirname(cli)));
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
