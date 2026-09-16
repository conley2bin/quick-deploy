import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";
import { installedPiRoot } from "./pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");

test("actual Pi live events reconcile read ownership, streaming tails, user attachments, and image preference", () => {
  const copy = mkdtempSync(resolve(tmpdir(), "pi-live-ownership-"));
  try {
    cpSync(installed, copy, { recursive: true });
    execFileSync(replay, ["apply", copy], { stdio: "pipe" });
    const modules = resolve(copy, "node_modules");
    const piRoot = installedPiRoot();
    mkdirSync(resolve(modules, "@earendil-works"), { recursive: true });
    symlinkSync(piRoot, resolve(modules, "@earendil-works/pi-coding-agent"), "dir");
    symlinkSync(resolve(piRoot, "node_modules/@earendil-works/pi-tui"), resolve(modules, "@earendil-works/pi-tui"), "dir");
    symlinkSync(resolve(process.env.HOME!, ".pi/agent/npm/node_modules/sharp"), resolve(modules, "sharp"), "dir");
    const stdout = execFileSync(process.execPath, ["--import", "tsx", resolve("test/live-ownership-harness.mjs"), copy], {
      cwd: resolve("."),
      encoding: "utf8",
      env: { ...process.env, PI_HOST_ROOT: piRoot, NODE_PATH: `${resolve(piRoot, "node_modules")}:${resolve(process.env.HOME!, ".pi/agent/npm/node_modules")}` },
    });
    const marker = stdout.lastIndexOf("LIVE_JSON ");
    assert.ok(marker >= 0, stdout);
    const report = JSON.parse(stdout.slice(marker + "LIVE_JSON ".length));
    assert.deepEqual([report.liveReadFinal.images.native, report.liveReadFinal.images.customGlyphs, report.liveReadFinal.images.withheld], [0, 2, false]);
    assert.deepEqual([report.nextStreamingUpdate.images.native, report.nextStreamingUpdate.images.customGlyphs, report.nextStreamingUpdate.images.withheld], [0, 2, false]);
    assert.deepEqual([report.externalImagesOff.images.native, report.externalImagesOff.images.customGlyphs, report.externalImagesOff.images.withheld], [0, 0, true]);
    assert.deepEqual([report.externalImagesOn.images.native, report.externalImagesOn.images.customGlyphs, report.externalImagesOn.images.withheld], [0, 2, false]);
    assert.equal(report.userAttachment.unchanged, true);
    assert.deepEqual([report.userAttachment.images.customGlyphs, report.userAttachment.images.withheld], [2, false]);
    assert.deepEqual([report.twoIdenticalRestored.images.native, report.twoIdenticalRestored.images.customGlyphs], [0, 6]);
    assert.equal(report.twoIdenticalRestored.coordination.activeLogicalIds.length, 2);
    assert.equal(report.componentDispose.assistantWrappers, 0);
    assert.equal(report.componentDispose.toolWrappers, 0);
  } finally {
    rmSync(copy, { recursive: true, force: true });
  }
});
