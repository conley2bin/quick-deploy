import { sessionEntryToContextMessages, VERSION, type ExtensionAPI, type SessionEntry } from "@earendil-works/pi-coding-agent";
import { allocateImageId, getCellDimensions, type TUI } from "@earendil-works/pi-tui";
import { installGraphicsBridge } from "./src/bridge.ts";
import {
  HostImageOwnershipAdapter,
  READ_PREVIEW_COORDINATION,
  READ_PREVIEW_ENTRIES_CHANGED,
} from "./src/host-adapter.ts";
import { transformMarkdown } from "./src/markdown.ts";
import { ImageSession } from "./src/session.ts";
import { TerminalImages } from "./src/terminal.ts";
import { currentViewerState, ViewerMonitor } from "./src/viewers.ts";

type HostContext = {
  mode: string;
  cwd: string;
  ui: { setWidget(key: string, content: unknown): void };
  sessionManager: { getBranch(): SessionEntry[]; buildContextEntries(): SessionEntry[] };
};

export default function piInlineImages(pi: ExtensionAPI) {
  const terminal = new TerminalImages(allocateImageId, getCellDimensions);
  terminal.setViewerManaged(true);
  const session = new ImageSession(terminal);
  const host = new HostImageOwnershipAdapter(
    session,
    (coordination) => pi.events.emit(READ_PREVIEW_COORDINATION, coordination),
    { version: VERSION, sessionEntryToContextMessages },
  );
  let tui: TUI | undefined;
  let hostContext: HostContext | undefined;
  let publicTreeSignature: string | undefined;
  let treeReconcileQueued = false;
  const reconcileHost = () => {
    if (tui && hostContext) return host.reconcile(hostContext.sessionManager.buildContextEntries(), hostContext.sessionManager.getBranch());
    return false;
  };
  const scheduleTreeReconcile = () => {
    const observed = host.publicTreeSignature();
    if (treeReconcileQueued || observed === publicTreeSignature) return;
    treeReconcileQueued = true;
    queueMicrotask(() => {
      treeReconcileQueued = false;
      const current = host.publicTreeSignature();
      if (current === publicTreeSignature) return;
      publicTreeSignature = current;
      if (reconcileHost()) {
        tui?.invalidate();
        tui?.requestRender(true);
      }
    });
  };
  let repaintQueued = false;
  const wake = () => {
    if (!tui || repaintQueued) return;
    repaintQueued = true;
    queueMicrotask(() => {
      repaintQueued = false;
      reconcileHost();
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
  const removeOwnershipChanged = pi.events.on(READ_PREVIEW_ENTRIES_CHANGED, (value) => {
    if (value && typeof value === "object" && (value as { version?: unknown }).version === 1) wake();
  });
  let widgetUi: { setWidget(key: string, content: unknown): void } | undefined;

  const installWakeWidget = (ui: { setWidget(key: string, content: unknown): void }) => {
    widgetUi = ui;
    ui.setWidget("pi-inline-images:repaint-bridge", (candidate: TUI) => {
      tui = candidate;
      host.setTui(candidate);
      publicTreeSignature = undefined;
      return { render: () => { scheduleTreeReconcile(); return []; }, invalidate() {} };
    });
  };

  pi.registerMarkdownTransformer((markdown, context) => {
    if (context.messageType !== "assistant" || context.isStreaming) return markdown;
    const prepared = session.preparedForRender(markdown);
    return prepared ? transformMarkdown(prepared, context.availableWidth, terminal) : markdown;
  });

  pi.on("message_end", async (event, context) => {
    if (context.mode !== "tui") return;
    hostContext = context as unknown as HostContext;
    await session.prepareMessage(event.message as never, context.cwd);
    syncMonitor();
    wake();
  });

  const restore = async (_event: unknown, context: HostContext) => {
    if (context.mode === "tui") {
      hostContext = context;
      installWakeWidget(context.ui);
      await session.restore(context.sessionManager.buildContextEntries(), context.cwd);
      syncMonitor();
      wake();
    } else {
      viewers.stop();
      hostContext = undefined;
      await session.reset();
    }
  };
  pi.on("session_start", restore as never);
  pi.on("session_tree", (async (_event: unknown, context: HostContext) => {
    if (context.mode === "tui") {
      hostContext = context;
      installWakeWidget(context.ui);
      await session.restore(context.sessionManager.buildContextEntries(), context.cwd, true);
      syncMonitor();
      wake();
    } else {
      viewers.stop();
      hostContext = undefined;
      await session.reset();
    }
  }) as never);
  pi.on("session_shutdown", async () => {
    viewers.stop();
    removeBridge();
    removeOwnershipChanged();
    host.dispose();
    hostContext = undefined;
    publicTreeSignature = undefined;
    treeReconcileQueued = false;
    widgetUi?.setWidget("pi-inline-images:repaint-bridge", undefined);
    widgetUi = undefined;
    tui = undefined;
    await session.reset(true);
  });
}
