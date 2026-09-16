import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";
import { installedPiRoot } from "./pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");

test("actual compaction revokes stale claims before rebuilt first frame and renews one custom owner", () => {
  const copy = mkdtempSync(resolve(tmpdir(), "pi-compaction-ownership-"));
  try {
    cpSync(installed, copy, { recursive: true }); execFileSync(replay, ["apply", copy], { stdio: "pipe" });
    const modules = resolve(copy, "node_modules"); const piRoot = installedPiRoot(); mkdirSync(resolve(modules, "@earendil-works"), { recursive: true });
    symlinkSync(piRoot, resolve(modules, "@earendil-works/pi-coding-agent"), "dir");
    symlinkSync(resolve(piRoot, "node_modules/@earendil-works/pi-tui"), resolve(modules, "@earendil-works/pi-tui"), "dir");
    symlinkSync(resolve(process.env.HOME!, ".pi/agent/npm/node_modules/sharp"), resolve(modules, "sharp"), "dir");
    const stdout = execFileSync(process.execPath, ["--import", "tsx", resolve("test/compaction-ownership-harness.mjs"), copy], {
      cwd: resolve("."), encoding: "utf8",
      env: { ...process.env, PI_HOST_ROOT: piRoot, NODE_PATH: `${resolve(piRoot, "node_modules")}:${resolve(process.env.HOME!, ".pi/agent/npm/node_modules")}` },
    });
    const marker = stdout.lastIndexOf("COMPACTION_JSON "); assert.ok(marker >= 0, stdout);
    const report = JSON.parse(stdout.slice(marker + "COMPACTION_JSON ".length));
    for (const state of [report.compactionFailed, report.compactionCancelled]) {
      assert.deepEqual([state.images.native, state.images.customGlyphs, state.images.withheld], [0, 40, false]);
    }
    const frames = report.compacted.frames;
    assert.ok(frames.length >= 2, frames);
    assert.ok(frames.every((frame: { native: number; customGlyphs: number }) => !(frame.native > 0 && frame.customGlyphs > 0)), frames);
    assert.deepEqual([frames[0].native, frames[0].customGlyphs], [1, 0], "first rebuilt frame is native-only while custom claim is revoked");
    assert.deepEqual([frames.at(-1).native, frames.at(-1).customGlyphs], [0, 40], "settled frame renews the full custom owner");
  } finally { rmSync(copy, { recursive: true, force: true }); }
});
