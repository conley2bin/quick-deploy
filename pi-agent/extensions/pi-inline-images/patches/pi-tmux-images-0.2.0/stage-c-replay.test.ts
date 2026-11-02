import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { appendFileSync, cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";

const installed = process.env.PI_TMUX_IMAGES_ROOT ?? resolve(process.env.HOME!, ".pi/agent/npm/node_modules/pi-tmux-images");
const patch = resolve("patches/pi-tmux-images-0.2.0/stage-c-recent-cache.patch");
const reviewUpgrade = resolve("patches/pi-tmux-images-0.2.0/stage-c-review-fixes.patch");
const ownershipUpgrade = resolve("patches/pi-tmux-images-0.2.0/stage-c-ownership-fixes.patch");
const noticeUpgrade = resolve("patches/pi-tmux-images-0.2.0/stage-c-notice-removal.patch");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");
const files = ["extensions/index.ts", "src/automatic.ts", "src/loader.ts", "src/runtime.ts", "src/renderer.ts", "src/transcript-entry.ts", "src/provenance.ts"];

function run(mode: "check" | "apply", root: string): string {
  return execFileSync(replay, [mode, root], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}
function snapshot(root: string): string[] {
  return files.map((file) => readFileSync(resolve(root, file)).toString("base64"));
}

test("Stage C replay is exact-source guarded and idempotent before or after deployment", () => {
  const copy = mkdtempSync(resolve(tmpdir(), "pi-tmux-images-stage-c-"));
  try {
    cpSync(installed, copy, { recursive: true });
    const installedState = run("check", copy);
    assert.match(installedState, /^(?:pristine|legacy-patched|previous-patched|ownership-patched|patched)$/u);
    if (installedState === "pristine") assert.equal(run("apply", copy), "patched");
    if (run("check", copy) === "patched") {
      execFileSync("patch", ["-R", "-d", copy, "-p1"], { input: readFileSync(noticeUpgrade), stdio: ["pipe", "pipe", "pipe"] });
    }
    assert.equal(run("check", copy), "ownership-patched");
    execFileSync("patch", ["-R", "-d", copy, "-p1"], { input: readFileSync(ownershipUpgrade), stdio: ["pipe", "pipe", "pipe"] });
    assert.equal(run("check", copy), "previous-patched");
    execFileSync("patch", ["-R", "-d", copy, "-p1"], { input: readFileSync(reviewUpgrade), stdio: ["pipe", "pipe", "pipe"] });
    assert.equal(run("check", copy), "legacy-patched");
    assert.equal(run("apply", copy), "patched", "both exact previously deployed states upgrade without a pristine reinstall");
    assert.equal(run("check", copy), "patched");
    // The cumulative patch is only applied forward from a pristine package and
    // is not reconstructible in reverse from legacy-patched (that leg left
    // rejects before this change), so the guard is exercised on the states a
    // real deployment can actually be in.
    const once = snapshot(copy);
    assert.equal(run("apply", copy), "already-patched");
    assert.deepEqual(snapshot(copy), once, "second apply changes no source bytes");

    const extension = readFileSync(resolve(copy, "extensions/index.ts"), "utf8");
    const automatic = readFileSync(resolve(copy, "src/automatic.ts"), "utf8");
    const loader = readFileSync(resolve(copy, "src/loader.ts"), "utf8");
    const renderer = readFileSync(resolve(copy, "src/renderer.ts"), "utf8");
    const runtime = readFileSync(resolve(copy, "src/runtime.ts"), "utf8");
    const provenance = readFileSync(resolve(copy, "src/provenance.ts"), "utf8");
    const transcript = readFileSync(resolve(copy, "src/transcript-entry.ts"), "utf8");
    assert.match(extension, /MAX_RECENT_PREVIEWS = 16/u);
    assert.doesNotMatch(extension, /activeEntries\(ctx\)\.length >= 16/u);
    assert.match(extension, /graphics-owner:request/u);
    assert.match(extension, /await runtime\.clear\(\)/u);
    assert.match(extension, /read-preview-coordination/u);
    assert.match(extension, /Automatic preview failed/u);
    assert.match(automatic, /bounded metadata without hashing\/copying/u);
    assert.match(automatic, /rejectedOriginFor/u);
    assert.match(renderer, /new Text\([^)]*\)\.render/u);
    assert.doesNotMatch(renderer, /fidelityNotice\(/u);
    assert.doesNotMatch(renderer, /new Image\(/u);
    assert.match(loader, /await sharp\(bytes, options\)\.stats\(\)/u);
    assert.match(loader, /metadata\.depth === "ushort"/u);
    assert.match(runtime, /SharedGraphicsHandle/u);
    assert.match(runtime, /await this\.shared\.prepare/u);
    assert.match(runtime, /for \(const entry of \[\.\.\.entries\]\.reverse\(\)\)/u);
    assert.doesNotMatch(runtime, /const candidates:/u);
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
    assert.match(transcript, /error\?: string/u);

    appendFileSync(resolve(copy, "src/runtime.ts"), "\n// unknown edit\n");
    assert.throws(() => run("check", copy), /unknown\/partial pi-tmux-images source state/u);
    assert.throws(() => run("apply", copy), /unknown\/partial pi-tmux-images source state/u);
  } finally {
    rmSync(copy, { recursive: true, force: true });
  }
});

// Replay applicability is a property of the exact source bytes. The package.json
// version label is not part of the decision in either direction.
function reverseTo(copy: string, patches: string[]): void {
  for (const file of patches) execFileSync("patch", ["-R", "-d", copy, "-p1"], { input: readFileSync(file), stdio: ["pipe", "pipe", "pipe"] });
}
function relabel(root: string, version: string): void {
  const file = resolve(root, "package.json");
  writeFileSync(file, JSON.stringify({ ...JSON.parse(readFileSync(file, "utf8")), version }, null, 2));
}
function tree(root: string): string[] {
  return [...files, "package.json"].map((file) => {
    try { return readFileSync(resolve(root, file)).toString("base64"); } catch { return "absent"; }
  });
}
function fail(mode: "check" | "apply", root: string): string {
  try { run(mode, root); } catch (error) { return String((error as { stderr?: unknown }).stderr); }
  return "";
}

test("Stage C replay outcome is independent of package.json version but rejects unknown source bytes", () => {
  const base = mkdtempSync(resolve(tmpdir(), "pi-tmux-images-stage-c-version-"));
  try {
    const original = resolve(base, "original");
    const relabeled = resolve(base, "relabeled");
    cpSync(installed, original, { recursive: true });
    // Reach the previously deployed legacy-patched source from whatever known state is installed,
    // so the version-independent result includes real patch application, not just the digest check.
    if (run("check", original) === "pristine") run("apply", original);
    if (run("check", original) === "patched") reverseTo(original, [noticeUpgrade]);
    reverseTo(original, [ownershipUpgrade, reviewUpgrade]);
    assert.equal(run("check", original), "legacy-patched");
    cpSync(original, relabeled, { recursive: true });
    for (const version of ["0.2.1", "9.9.9", "0.0.0-local"]) {
      relabel(relabeled, version);
      assert.equal(run("check", relabeled), "legacy-patched", `check is the same under version ${version}`);
    }
    assert.equal(run("apply", original), "patched");
    assert.equal(run("apply", relabeled), "patched");
    assert.deepEqual(tree(relabeled).slice(0, files.length), tree(original).slice(0, files.length), "relabeled package ends byte-identical in every patched source");
    assert.equal(JSON.parse(readFileSync(resolve(relabeled, "package.json"), "utf8")).version, "0.0.0-local", "replay never rewrites the version label");

    // An unknown source stays rejected under both the historical and a different label, leaving the target untouched.
    appendFileSync(resolve(relabeled, "src/runtime.ts"), "\n// unknown edit\n");
    for (const version of ["0.2.0", "9.9.9"]) {
      relabel(relabeled, version);
      const before = tree(relabeled);
      for (const mode of ["check", "apply"] as const) {
        assert.match(fail(mode, relabeled), /unknown\/partial pi-tmux-images source state/u, `${mode} rejects unknown bytes under version ${version}`);
      }
      assert.deepEqual(tree(relabeled), before, "rejected replay leaves the target byte-identical");
    }
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});
