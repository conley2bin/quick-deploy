import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";
import { installedPiRoot } from "./pi-root.ts";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");

test("actual Pi native capability null still authorizes ready custom tmux owner and honors explicit off/on", () => {
  const copy = mkdtempSync(resolve(tmpdir(), "pi-native-tmux-capability-"));
  try {
    cpSync(installed, copy, { recursive: true });
    execFileSync(replay, ["apply", copy], { stdio: "pipe" });
    const modules = resolve(copy, "node_modules"); const piRoot = installedPiRoot();
    mkdirSync(resolve(modules, "@earendil-works"), { recursive: true });
    symlinkSync(piRoot, resolve(modules, "@earendil-works/pi-coding-agent"), "dir");
    symlinkSync(resolve(piRoot, "node_modules/@earendil-works/pi-tui"), resolve(modules, "@earendil-works/pi-tui"), "dir");
    symlinkSync(resolve(process.env.HOME!, ".pi/agent/npm/node_modules/sharp"), resolve(modules, "sharp"), "dir");
    const stdout = execFileSync(process.execPath, ["--import", "tsx", resolve("test/native-tmux-capability-harness.mjs"), copy, "png"], {
      cwd: resolve("."), encoding: "utf8",
      env: { ...process.env, PI_HOST_ROOT: piRoot, NODE_PATH: `${resolve(piRoot, "node_modules")}:${resolve(process.env.HOME!, ".pi/agent/npm/node_modules")}` },
    });
    const marker = stdout.lastIndexOf("TMUX_JSON "); assert.ok(marker >= 0, stdout);
    const report = JSON.parse(stdout.slice(marker + "TMUX_JSON ".length));
    assert.equal(report.rapid.detectedTmux.images, null);
    const settled = report.rapid.history[0].after350;
    assert.equal(settled.coordination.activeLogicalIds.length, 1);
    assert.deepEqual(settled.images, { native: 0, customGlyphs: 40, withheld: false });
    assert.deepEqual(report.externalOff.images, { native: 0, customGlyphs: 0, withheld: true });
    assert.deepEqual(report.externalOn.images, { native: 0, customGlyphs: 40, withheld: false });
  } finally {
    rmSync(copy, { recursive: true, force: true });
  }
});
