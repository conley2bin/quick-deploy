import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const fixture = fileURLToPath(new URL("./startup-rollback-fixture.mjs", import.meta.url));
for (const kind of ["invalidate", "requestRender", "notify", "widget", "mouse-warning-ui", "unsafe-secondary"]) {
  test(`startup acquisition rollback: ${kind}`, () => {
    const result = spawnSync(process.execPath, ["--import", "tsx", fixture, kind], {
      cwd: process.cwd(), encoding: "utf8", timeout: 20_000,
    });
    assert.equal(result.status, 0, result.stderr + result.stdout);
    const evidence = JSON.parse(result.stdout.trim());
    assert.equal(evidence.mouse.owned, false);
    assert.equal(evidence.activeMarkdownHooks, 0);
    assert.equal(evidence.privateTransformer, false);
    assert.equal(evidence.minimalPassiveGuard, kind === "unsafe-secondary");
    assert.deepEqual(evidence.nativeOpens, []);
  });
}
