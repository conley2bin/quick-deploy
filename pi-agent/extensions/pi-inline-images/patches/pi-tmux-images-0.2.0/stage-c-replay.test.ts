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
    const provenance = readFileSync(resolve(copy, "src/provenance.ts"), "utf8");
    const transcript = readFileSync(resolve(copy, "src/transcript-entry.ts"), "utf8");
    assert.match(extension, /MAX_RECENT_PREVIEWS = 16/u);
    assert.doesNotMatch(extension, /activeEntries\(ctx\)\.length >= 16/u);
    assert.match(extension, /graphics-owner:request/u);
    assert.match(extension, /await runtime\.clear\(\)/u);
    assert.match(renderer, /new Text\([^)]*\)\.render/u);
    assert.doesNotMatch(renderer, /new Image\(/u);
    assert.match(runtime, /SharedGraphicsHandle/u);
    assert.match(runtime, /await this\.shared\.prepare/u);
    assert.doesNotMatch(runtime, /process\.stdout|terminalIds|renderMode|deleteImage|\bupload\(/u);
    assert.match(extension, /pi\.on\("tool_call"/u);
    assert.match(extension, /pi\.on\("tool_result"/u);
    assert.match(extension, /pi\.getAllTools\(\)/u);
    assert.match(provenance, /MAX_TRACKED_READS = 64/u);
    assert.match(provenance, /MAX_FROZEN_BYTES = 64 \* 1024 \* 1024/u);
    assert.match(provenance, /<builtin:read>/u);
    assert.match(provenance, /await import\("@earendil-works\/pi-coding-agent"\)/u);
    assert.match(provenance, /resizeImage\(capture\.bytes, capture\.mimeType\)/u);
    assert.match(transcript, /verified-local-original/u);
  } finally {
    rmSync(copy, { recursive: true, force: true });
  }
});
