import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { allocateImageId, getCellDimensions } from "@earendil-works/pi-tui";
import { installGraphicsBridge } from "./src/bridge.ts";
import { transformMarkdown } from "./src/markdown.ts";
import { assistantTextBlocks, ImageSession } from "./src/session.ts";
import { TerminalImages } from "./src/terminal.ts";
import { currentViewerState, ViewerMonitor } from "./src/viewers.ts";

type TuiRepaint = { invalidate(): void; requestRender(force?: boolean): void };

export default function piInlineImages(pi: ExtensionAPI) {
  const terminal = new TerminalImages(allocateImageId, getCellDimensions);
  terminal.setViewerManaged(true);
  const session = new ImageSession(terminal);
  let tui: TuiRepaint | undefined;
  let repaintQueued = false;
  const wake = () => {
    if (!tui || repaintQueued) return;
    repaintQueued = true;
    queueMicrotask(() => {
      repaintQueued = false;
      tui?.invalidate();
      tui?.requestRender(true);
    });
  };
  const viewers = new ViewerMonitor({ snapshot: currentViewerState }, async (state) => {
    try { await terminal.setViewer(state); } finally { wake(); }
  });
  const syncMonitor = () => {
    if (terminal.count() > 0) viewers.start();
    else {
      viewers.stop();
      terminal.pauseViewers();
    }
  };
  const resourcesChanged = () => { syncMonitor(); wake(); };
  const removeBridge = installGraphicsBridge(pi.events, terminal, resourcesChanged);
  let widgetUi: { setWidget(key: string, content: unknown): void } | undefined;

  const installWakeWidget = (ui: { setWidget(key: string, content: unknown): void }) => {
    widgetUi = ui;
    ui.setWidget("pi-inline-images:repaint-bridge", (candidate: TuiRepaint) => {
      tui = candidate;
      return { render: () => [], invalidate() {} };
    });
  };

  pi.registerMarkdownTransformer((markdown, context) => {
    if (context.messageType !== "assistant" || context.isStreaming) return markdown;
    const prepared = session.preparedForRender(markdown);
    return prepared ? transformMarkdown(prepared, context.availableWidth, terminal) : markdown;
  });

  pi.on("message_end", async (event, context) => {
    if (context.mode !== "tui") return;
    for (const source of assistantTextBlocks(event.message as never)) await session.prepare(source, context.cwd);
    syncMonitor();
    wake();
  });

  const restore = async (_event: unknown, context: { mode: string; cwd: string; ui: { setWidget(key: string, content: unknown): void }; sessionManager: { getBranch(): readonly never[] } }) => {
    if (context.mode === "tui") {
      installWakeWidget(context.ui);
      await session.restore(context.sessionManager.getBranch(), context.cwd);
      syncMonitor();
      wake();
    } else {
      viewers.stop();
      await session.reset();
    }
  };
  pi.on("session_start", restore as never);
  pi.on("session_tree", (async (_event: unknown, context: { mode: string; cwd: string; ui: { setWidget(key: string, content: unknown): void }; sessionManager: { getBranch(): readonly never[] } }) => {
    if (context.mode === "tui") {
      installWakeWidget(context.ui);
      await session.restore(context.sessionManager.getBranch(), context.cwd, true);
      syncMonitor();
      wake();
    } else {
      viewers.stop();
      await session.reset();
    }
  }) as never);
  pi.on("session_shutdown", async () => {
    viewers.stop();
    removeBridge();
    widgetUi?.setWidget("pi-inline-images:repaint-bridge", undefined);
    widgetUi = undefined;
    tui = undefined;
    await session.reset(true);
  });
}
