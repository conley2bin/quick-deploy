// Test-only extension. An offline provider and a snapshot command let the PTY
// driver exercise the real bundled CLI without LLM calls or a GUI dependency.
import { appendFileSync, writeFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { getOsc8LinkAtColumn, stripTerminalSequences, type TUI } from "@earendil-works/pi-tui";

export default function fixture(pi: ExtensionAPI) {
  let tui: TUI | undefined;
  let inputListener: ((chunk: Buffer) => void) | undefined;
  pi.registerProvider("copy-links-fixture", {
    api: "openai-completions", baseUrl: "http://127.0.0.1:1", apiKey: "fixture-not-a-secret",
    models: [{ id: "offline", name: "Offline fixture", reasoning: false, input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32000, maxTokens: 1000 }],
    streamSimple() { throw new Error("The native fixture must not call a model"); },
  });
  pi.on("session_start", (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    if (process.env.PI_COPY_LINKS_TEST_DIR) {
      inputListener = (chunk) => appendFileSync(`${process.env.PI_COPY_LINKS_TEST_DIR}/input.raw`, chunk);
      process.stdin.on("data", inputListener);
    }
    ctx.ui.setWidget("copy-links-test", (current) => {
      tui = current;
      return { render: () => [], invalidate() {} };
    });
  });
  pi.registerCommand("fixture-snapshot", {
    handler: async (_args, ctx) => {
      if (!tui || !process.env.PI_COPY_LINKS_TEST_DIR) throw new Error("Missing test TUI/directory");
      ctx.ui.setEditorText(""); tui.renderNow(true);
      const lines = (tui as unknown as { previousScreen: string[] }).previousScreen ?? [];
      const targets: { url: string; x: number; y: number }[] = [];
      const seen = new Set<string>();
      for (const [y, line] of lines.entries()) for (let x = 0; x < tui.terminal.columns; x++) {
        const url = getOsc8LinkAtColumn(line, x);
        if (url && !seen.has(url)) { targets.push({ url, x, y }); seen.add(url); }
      }
      writeFileSync(`${process.env.PI_COPY_LINKS_TEST_DIR}/snapshot.json`, JSON.stringify({
        mode: tui.mode, lines: lines.map(stripTerminalSequences), targets,
      }, null, 2));
    },
  });
  pi.registerCommand("fixture-quit", { handler: async (_args, ctx) => ctx.shutdown() });
  pi.on("session_shutdown", () => {
    if (inputListener) process.stdin.off("data", inputListener);
    inputListener = undefined;
    tui = undefined;
  });
}
