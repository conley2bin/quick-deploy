import { copyToClipboard, VERSION, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { TUI } from "@earendil-works/pi-tui";
import { checkCompatibility, compatibilityDiagnostic, installAdapter, type CompatibilityStatus } from "./src/adapter.ts";
import { remoteCopySink } from "./src/remote-copy.ts";
import { dispatchOpenTarget } from "./src/dispatcher.ts";
import { enableTmuxMouse } from "./src/tmux.ts";

const WIDGET = "pi-copy-links:bridge";
const HELP = "在 /settings 将 TUI mode 设为 fullscreen：代码块右下角左键复制，Markdown 网页/文件链接用 Ctrl+左键在 Chrome/VS Code 新窗口打开。普通模式的历史滚动由终端接管，不支持这些操作。";

export default function copyLinksExtension(pi: ExtensionAPI): void {
  let adapter: ReturnType<typeof installAdapter> | undefined;
  let tui: TUI | undefined;
  let failure: string | undefined;
  let compatibility: CompatibilityStatus | undefined;
  let warnedFailure: string | undefined;
  let mouse: Awaited<ReturnType<typeof enableTmuxMouse>> | undefined;
  let mouseWarning: string | undefined;
  pi.registerMarkdownTransformer((source, context) => adapter?.transform(source, context) ?? source);

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    adapter?.dispose();
    adapter = undefined;
    await mouse?.dispose();
    mouse = undefined;
    mouseWarning = undefined;
    ctx.ui.setWidget(WIDGET, (current) => {
      tui = current; // Stable Pi proxy follows /settings mode switches.
      return { render: () => [], invalidate() {} };
    });
    try {
      compatibility = await checkCompatibility(VERSION);
      if (!compatibility.compatible) throw new Error(compatibility.cause);
      // Re-capture the production descriptors after the isolated async probe;
      // installAdapter still commits the four real hooks as one transaction.
      adapter = installAdapter({
        version: VERSION,
        active: () => tui?.mode === "fullscreen",
        copy: async (text) => {
          const sink = remoteCopySink();
          if (sink && (await sink(text))) return;
          await copyToClipboard(text);
        },
        cwd: () => ctx.cwd,
        requireTmuxOrigin: Boolean(process.env.TMUX),
        open: (href, cwd, lifetime, origin) => dispatchOpenTarget(href, cwd, lifetime, origin),
        button: (text) => ctx.ui.theme.fg("accent", text),
        recoverTmuxRelease: () => mouse?.enabled === true && mouse.warnings.length === 0,
        requestRender: () => { tui?.invalidate(); tui?.requestRender(); },
        quiesce: () => {
          if (!tui) return false;
          const fullscreen = tui.mode === "fullscreen";
          tui.invalidate();
          (tui as TUI & { renderNow(force?: boolean): void }).renderNow(true);
          if (!fullscreen) return true;
          const screen = (tui as TUI & { previousScreen?: unknown }).previousScreen;
          return Array.isArray(screen) && screen.every(
            (row) => typeof row === "string" && !row.includes("pi-copy://"),
          );
        },
        notify: (message, error) => ctx.ui.notify(message, error ? "error" : "info"),
      });
      failure = undefined;
      try {
        mouse = await enableTmuxMouse();
        if (mouse.warnings.length) mouseWarning = `tmux 鼠标绑定缺少来源标记，未覆盖非受管绑定：${mouse.warnings.join(", ")}；缺标记的 Ctrl+点击会拒绝打开`;
      } catch (error) { mouseWarning = `tmux Ctrl+鼠标透传未就绪：${String(error)}`; }
      if (mouseWarning) ctx.ui.notify(mouseWarning, "warning");
      tui?.invalidate();
      tui?.requestRender();
      if (tui?.mode !== "fullscreen") ctx.ui.notify(`pi-copy-links 已加载。${HELP}`, "info");
    } catch (error) {
      failure = compatibilityDiagnostic(VERSION, error);
      if (failure !== warnedFailure) {
        warnedFailure = failure;
        ctx.ui.notify(failure, "warning");
      }
    }
  });

  pi.registerCommand("copy-links", {
    description: "查看代码复制和 Ctrl+单击网页/文件链接的启用状态",
    handler: async (_args, ctx) => {
      const actual = adapter?.status();
      const state = failure ?? (actual && !actual.available ? compatibilityDiagnostic(VERSION, actual.cause) :
        actual?.available && tui?.mode === "fullscreen" ?
          "已启用：点击 [复制] 获取原始代码；Ctrl+左键在新窗口打开 HTTP(S) 网页或现有本地目标。" : HELP);
      const contract = compatibility?.compatible ?
        `实际兼容检查通过（Pi ${VERSION}，隔离探针 ${compatibility.probeMs.toFixed(1)} ms）` : undefined;
      ctx.ui.notify([state, contract, mouseWarning].filter(Boolean).join("\n"),
        failure || (actual && !actual.available) || mouseWarning ? "warning" : "info");
    },
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    adapter?.dispose();
    adapter = undefined;
    try { await mouse?.dispose(); }
    catch (error) { if (ctx.mode === "tui") ctx.ui.notify(`tmux 鼠标标记清理失败：${String(error)}`, "warning"); }
    mouse = undefined;
    if (ctx.mode === "tui") {
      tui?.invalidate();
      ctx.ui.setWidget(WIDGET, undefined);
    }
    tui = undefined;
  });
}
