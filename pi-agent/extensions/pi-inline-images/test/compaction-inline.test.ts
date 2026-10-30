import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import test from "node:test";
import { installedPiRoot } from "./pi-root.ts";

test("production-loader compaction replaces cached unbound notices after newly binding identical image messages", () => {
  const root = installedPiRoot();
  const stdout = execFileSync(process.execPath, ["--import", "tsx", resolve("test/compaction-inline-harness.mjs")], {
    cwd: resolve("."), encoding: "utf8", timeout: 60_000,
    env: { ...process.env, PI_HOST_ROOT: root },
  });
  const marker = stdout.lastIndexOf("INLINE_COMPACTION_JSON ");
  assert.ok(marker >= 0, `harness produced no report:\n${stdout}`);
  const report = JSON.parse(stdout.slice(marker + "INLINE_COMPACTION_JSON ".length)) as {
    before: { glyphs: number; notices: number };
    unbound: { glyphs: number; notices: number };
    recovered: { glyphs: number; notices: number };
    idle: { glyphs: number; notices: number };
    forcedAfterRebuild: number; boundBeforeFirstPaint: boolean;
    frames: Array<{ glyphs: number; notices: number }>;
    ready: boolean; bitmapUploads: number; rawMessagesUnchanged: boolean;
    retirement: { rounds: number; capacityFailures: number; indexedAfterCompaction: number; failedPreserved: boolean };
  };
  assert.deepEqual([report.before.glyphs, report.before.notices], [4, 0]);
  assert.equal(report.boundBeforeFirstPaint, false, "new Pi rows first render without an adapter binding");
  assert.deepEqual([report.unbound.glyphs, report.unbound.notices], [0, 2],
    "the first rebuilt frame must actually cache two coordination notices");
  assert.equal(report.ready, true, "the later host association succeeds");
  assert.deepEqual([report.recovered.glyphs, report.recovered.notices], [4, 0],
    "successful binding must invalidate the new assistants' cached notices once");
  assert.deepEqual([report.idle.glyphs, report.idle.notices], [4, 0],
    "stable rerender must preserve image grids without renewed invalidation");
  assert.equal(report.forcedAfterRebuild, 0, "recovery must not force a global history replay");
  assert.equal(report.bitmapUploads, 1, "identical unchanged PNG occurrences share one original-size Kitty upload");
  assert.equal(report.rawMessagesUnchanged, true, "compaction display repair leaves persisted messages untouched");
  assert.deepEqual(report.retirement, { rounds: 66, capacityFailures: 0, indexedAfterCompaction: 0, failedPreserved: true },
    "successful compaction retires historical catalogs before capacity fills; failed/aborted compaction preserves active state");
});
