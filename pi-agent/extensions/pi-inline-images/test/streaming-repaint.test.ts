import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readdirSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import { installedPiRoot } from "./pi-root.ts";

/**
 * Regression: a streaming assistant turn must not force-repaint history.
 *
 * Each streaming update previously invalidated the whole tree and forced a render. The main-screen
 * renderer then cleared scrollback and emitted the entire conversation, including unchanged history.
 * Drive real message_start/message_update/message_end events through the extension runner and a real
 * TuiMainScreen; verify the emitted bytes and historical component invalidations, not just render calls.
 */
test("streaming assistant updates never force a history repaint but ownership transitions still do", () => {
  const source = resolve(".");
  const stage = mkdtempSync(join(tmpdir(), "pi-inline-streaming-"));
  try {
    for (const entry of ["index.ts", "src", "vendor", "package.json"]) {
      cpSync(resolve(source, entry), resolve(stage, entry), { recursive: true });
    }
    const modules = resolve(stage, "node_modules");
    mkdirSync(resolve(modules, "@earendil-works"), { recursive: true });
    const piRoot = installedPiRoot();
    symlinkSync(piRoot, resolve(modules, "@earendil-works/pi-coding-agent"));
    symlinkSync(resolve(piRoot, "node_modules/@earendil-works/pi-tui"), resolve(modules, "@earendil-works/pi-tui"));
    for (const entry of readdirSync(resolve(source, "node_modules"))) {
      if (entry === "@earendil-works") continue;
      symlinkSync(resolve(source, "node_modules", entry), resolve(modules, entry));
    }
    const stdout = execFileSync(process.execPath, ["--import", "tsx", resolve(source, "test/streaming-repaint-harness.mjs")], {
      cwd: stage,
      encoding: "utf8",
      env: {
        ...process.env,
        PI_HOST_ROOT: piRoot,
        NODE_PATH: `${resolve(piRoot, "node_modules")}:${resolve(source, "node_modules")}`,
      },
    });
    const marker = stdout.lastIndexOf("STREAM_JSON ");
    assert.ok(marker >= 0, `harness produced no report:\n${stdout}`);
    const report = JSON.parse(stdout.slice(marker + "STREAM_JSON ".length)) as {
      tokens: number;
      streaming: { forceRenders: number; renderRequests: number; historicalInvalidations: number; scrollbackWipes: number; bannerRepaints: number };
      streamingTextPreserved: boolean;
      messageEnd: { forceRenders: number };
      entriesChanged: { forceRenders: number };
      liveToolBound: boolean;
    };

    assert.deepEqual(
      {
        forceRenders: report.streaming.forceRenders,
        scrollbackWipes: report.streaming.scrollbackWipes,
        bannerRepaints: report.streaming.bannerRepaints,
        historicalInvalidations: report.streaming.historicalInvalidations,
      },
      { forceRenders: 0, scrollbackWipes: 0, bannerRepaints: 0, historicalInvalidations: 0 },
      `streaming turned into history repaints (${report.tokens} tokens)`,
    );
    assert.equal(report.streamingTextPreserved, true, "native streaming text must survive the change");
    assert.equal(report.liveToolBound, true, "live tool rows must still be bound by the adapter");
    assert.ok(report.messageEnd.forceRenders >= 1, "message_end must still rebuild so images can be prepared");
    assert.ok(report.entriesChanged.forceRenders >= 1, "ownership-change events must still rebuild");
  } finally {
    rmSync(stage, { recursive: true, force: true });
  }
});
