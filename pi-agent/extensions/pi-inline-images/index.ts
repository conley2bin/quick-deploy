import { createHash } from "node:crypto";
import { sessionEntryToContextMessages, VERSION, type ExtensionAPI, type SessionEntry } from "@earendil-works/pi-coding-agent";
import { allocateImageId, getCapabilities, getCellDimensions, type TUI } from "@earendil-works/pi-tui";
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
  sessionManager: { getBranch(): SessionEntry[]; buildContextEntries(): SessionEntry[]; getSessionId(): string };
};

export default function piInlineImages(pi: ExtensionAPI) {
  const terminal = new TerminalImages(allocateImageId, getCellDimensions);
  terminal.setViewerManaged(true);
  const session = new ImageSession(terminal);
  let wake: (force?: boolean) => void = () => undefined;
  const host = new HostImageOwnershipAdapter(
    session,
    (coordination) => pi.events.emit(READ_PREVIEW_COORDINATION, coordination),
    { version: VERSION, sessionEntryToContextMessages, nativeImageProtocol: () => getCapabilities().images },
    () => wake(),
  );
  let tui: TUI | undefined;
  let hostContext: HostContext | undefined;
  let treeSignature: string | undefined;
  let treeReconcileQueued = false;
  const reconcileHost = () => {
    if (tui && hostContext) return host.reconcile(hostContext.sessionManager.buildContextEntries(), hostContext.sessionManager.getBranch(), hostContext.sessionManager.getSessionId());
    return false;
  };
  /**
   * Identity of persisted claims (preview/clear entries, message roles and call ids). Deliberately cheaper
   * and narrower than the adapter's reconciliation signature: it excludes live streaming text, so a frame
   * that only carried new token text can never look like an ownership change.
   */
  const persistedDataSignature = () => {
    const entries = hostContext?.sessionManager.buildContextEntries() ?? [];
    const hash = createHash("sha256");
    for (const entry of entries) {
      const message = (entry as { message?: { role?: unknown; toolCallId?: unknown } }).message;
      const data = (entry as { data?: { logicalId?: unknown; origin?: { key?: unknown; blockIndex?: unknown } } }).data;
      hash.update(`${entry.type}\u0000${String(entry.customType ?? "")}\u0000${String(message?.role ?? "")}\u0000${String(message?.toolCallId ?? "")}\u0000${String(data?.logicalId ?? "")}\u0000${String(data?.origin?.key ?? "")}\u0000${String(data?.origin?.blockIndex ?? "")}\u0000`);
    }
    return hash.digest("hex").slice(0, 16);
  };
  /** Structural + persisted-claim gate; both parts ignore streaming churn. */
  const currentTreeSignature = () => `${host.publicTreeSignature()}|${persistedDataSignature()}`;
  /**
   * Safety net for host-side changes that emit no extension event (component rebuilds, peer claims).
   * Guarded by identity signatures so ordinary streaming never schedules work; the reconcile it lowers to
   * is a diff repaint, never a full-history replay.
   */
  const scheduleTreeReconcile = () => {
    const observed = currentTreeSignature();
    if (treeReconcileQueued || observed === treeSignature) return;
    treeReconcileQueued = true;
    queueMicrotask(() => {
      treeReconcileQueued = false;
      const current = currentTreeSignature();
      if (current === treeSignature) return;
      treeSignature = current;
      wake(false);
    });
  };
  let repaintQueued = false;
  let forceRepaintQueued = false;
  /**
   * `force` marks transitions that changed what the frame must show beyond ordinary live bindings: image
   * ownership/placement changes, restores, viewer changes, message_end. A forced repaint resets renderer
   * state, which on the main-screen renderer re-emits the entire scrollback (`ESC[3J` + every line), so
   * routine live updates must stay on the diff path.
   */
  wake = (force = true) => {
    if (!tui) return;
    forceRepaintQueued ||= force;
    if (repaintQueued) return;
    repaintQueued = true;
    queueMicrotask(() => {
      repaintQueued = false;
      const forceRepaint = forceRepaintQueued;
      forceRepaintQueued = false;
      reconcileHost();
      if (forceRepaint) {
        tui?.invalidate();
        tui?.requestRender(true);
      }
      else {
        tui?.requestRender();
      }
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
      treeSignature = undefined;
      return { render: () => { scheduleTreeReconcile(); return []; }, invalidate() {} };
    });
  };

  pi.registerMarkdownTransformer((markdown, context) => {
    if (context.messageType !== "assistant" || context.isStreaming) return markdown;
    const prepared = session.preparedForRender(markdown);
    return prepared ? transformMarkdown(prepared, context.availableWidth, terminal) : markdown;
  });

  const observeLiveMessage = (phase: "start" | "update", event: { message: unknown }, context: HostContext) => {
    if (context.mode !== "tui") return;
    hostContext = context;
    host.observeMessage(phase, event.message);
    // Streaming tokens are the host's own render path; only reconcile and diff, never force.
    wake(false);
  };
  pi.on("message_start", ((event: { message: unknown }, context: HostContext) => observeLiveMessage("start", event, context)) as never);
  pi.on("message_update", ((event: { message: unknown }, context: HostContext) => observeLiveMessage("update", event, context)) as never);
  pi.on("message_end", async (event, context) => {
    if (context.mode !== "tui") return;
    hostContext = context as unknown as HostContext;
    host.observeMessage("end", event.message);
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
  pi.on("session_compact", (() => {
    host.suspend("compaction component reconstruction");
    treeSignature = undefined;
    wake();
  }) as never);
  pi.on("session_compact_failed", (() => wake()) as never);
  pi.on("session_tree", (async (_event: unknown, context: HostContext) => {
    if (context.mode === "tui") {
      host.suspend("session tree component reconstruction", false);
      treeSignature = undefined;
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
    treeSignature = undefined;
    treeReconcileQueued = false;
    widgetUi?.setWidget("pi-inline-images:repaint-bridge", undefined);
    widgetUi = undefined;
    tui = undefined;
    await session.reset(true);
  });
}
