import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const replay = resolve("patches/pi-tmux-images-0.2.0/apply-stage-c-disposable.sh");

test("Stage C replay is exact-version guarded and applies only to a disposable package copy", () => {
  const copy = mkdtempSync(resolve(tmpdir(), "pi-tmux-images-stage-c-"));
  try {
    cpSync(installed, copy, { recursive: true });
    execFileSync(replay, [copy], { stdio: "pipe" });
    const extension = readFileSync(resolve(copy, "extensions/index.ts"), "utf8");
    const renderer = readFileSync(resolve(copy, "src/renderer.ts"), "utf8");
    const runtime = readFileSync(resolve(copy, "src/runtime.ts"), "utf8");
    assert.match(extension, /MAX_RECENT_PREVIEWS = 16/u);
    assert.doesNotMatch(extension, /activeEntries\(ctx\)\.length >= 16/u);
    assert.match(extension, /graphics-owner:request/u);
    assert.match(renderer, /new Text\([^)]*\)\.render/u);
    assert.match(runtime, /SharedGraphicsHandle/u);
  } finally {
    rmSync(copy, { recursive: true, force: true });
  }
});
