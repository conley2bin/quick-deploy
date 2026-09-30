import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const fixture = fileURLToPath(new URL("./probe-cache-fixture.mjs", import.meta.url));
for (const [kind, images] of [
  ["baseline", "iterm2"], ["undefined", "iterm2"], ["null", "kitty"], ["delayed", "none"],
  ["width-drift", "none"], ["tab-cache-drift", "none"],
]) {
  test(`probe cache boundary: ${kind}, capabilities ${images}`, () => {
    const result = spawnSync(process.execPath, ["--import", "tsx", fixture, kind!, images!], {
      cwd: process.cwd(), encoding: "utf8", timeout: 20_000,
    });
    assert.equal(result.status, 0, result.stderr + result.stdout);
    const evidence = JSON.parse(result.stdout.trim());
    assert.equal(evidence.capabilitiesUnchangedThroughout, true);
    if (kind === "width-drift" || kind === "tab-cache-drift") {
      assert.equal(evidence.check.compatible, false);
    } else {
      assert.equal(evidence.check.compatible, true, evidence.check.cause);
      assert.deepEqual(evidence.actualNativeCopies, ["target:\n\techo 中文  "]);
      assert.deepEqual(evidence.semanticOpen, ["https://example.com/?a=1&b=2"]);
      assert.equal(evidence.selection, true);
      assert.deepEqual(evidence.overlay, ["press", "release", "click"]);
      assert.deepEqual(evidence.dispose, { quiesced: true, passive: false });
    }
  });
}
