import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";
import { installedPiRoot } from "./pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");

test("observed off survives actual compaction and navigateTree for same call, then explicit on restores one custom owner", () => {
  const copy = mkdtempSync(resolve(tmpdir(), "pi-preference-lifetime-"));
  try {
    cpSync(installed, copy, { recursive: true }); execFileSync(replay, ["apply", copy], { stdio: "pipe" });
    const modules = resolve(copy, "node_modules"); const piRoot = installedPiRoot(); mkdirSync(resolve(modules, "@earendil-works"), { recursive: true });
    symlinkSync(piRoot, resolve(modules, "@earendil-works/pi-coding-agent"), "dir");
    symlinkSync(resolve(piRoot, "node_modules/@earendil-works/pi-tui"), resolve(modules, "@earendil-works/pi-tui"), "dir");
    symlinkSync(resolve(process.env.HOME!, ".pi/agent/npm/node_modules/sharp"), resolve(modules, "sharp"), "dir");
    const stdout = execFileSync(process.execPath, ["--import", "tsx", resolve("test/preference-lifetime-harness.mjs"), copy, "png"], {
      cwd: resolve("."), encoding: "utf8",
      env: { ...process.env, PI_HOST_ROOT: piRoot, NODE_PATH: `${resolve(piRoot, "node_modules")}:${resolve(process.env.HOME!, ".pi/agent/npm/node_modules")}` },
    });
    const marker = stdout.lastIndexOf("PREFERENCE_JSON "); assert.ok(marker >= 0, stdout);
    const report = JSON.parse(stdout.slice(marker + "PREFERENCE_JSON ".length));
    assert.equal(report.explicitOff.setting, false);
    assert.deepEqual([report.explicitOff.state.images.native, report.explicitOff.state.images.customGlyphs], [0, 0]);
    assert.equal(report.afterCompactionPreference, false);
    assert.deepEqual([report.compacted.after.images.native, report.compacted.after.images.customGlyphs], [0, 0]);
    assert.equal(report.branchAfterOff.setting, false);
    assert.deepEqual([report.branchAfterOff.state.images.native, report.branchAfterOff.state.images.customGlyphs], [0, 0]);
    assert.equal(report.branchAfterOn.setting, true);
    assert.deepEqual([report.branchAfterOn.state.images.native, report.branchAfterOn.state.images.customGlyphs, report.branchAfterOn.state.images.withheld], [0, 40, false]);
  } finally { rmSync(copy, { recursive: true, force: true }); }
});
