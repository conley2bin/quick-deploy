import { copyToClipboard, VERSION, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { TUI } from "@earendil-works/pi-tui";
import { checkCompatibility, compatibilityDiagnostic, installAdapter, type CompatibilityStatus } from "./src/adapter.ts";
import { remoteCopySink } from "./src/remote-copy.ts";
import { dispatchOpenTarget } from "./src/dispatcher.ts";
import { resolveInputTopology, type InputTopology } from "./src/receiver.ts";
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
  let inputTopology: InputTopology | undefined;
  let mouseWarning: string | undefined;
  let rollbackNotes: unknown[] | undefined;
  // Detach every owner before any fallible cleanup or awaited mouse release.
  async function releaseOwners(): Promise<unknown[]> {
    const retiredAdapter = adapter, retiredMouse = mouse;
    adapter = undefined;
    mouse = undefined;
    inputTopology = undefined;
    mouseWarning = undefined;
    const issues: unknown[] = [];
    try { retiredAdapter?.dispose(); } catch (error) { issues.push(error); }
    try { await retiredMouse?.dispose(); } catch (error) { issues.push(error); }
    return issues;
  }
  const reason = (error: unknown): string => {
    try { return error instanceof Error ? error.message : String(error); }
    catch { return "无法读取清理原因"; }
  };
  pi.registerMarkdownTransformer((source, context) => adapter?.transform(source, context) ?? source);

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    compatibility = undefined;
    try {
      const priorIssues = await releaseOwners();
      if (priorIssues.length) throw priorIssues[0];
      ctx.ui.setWidget(WIDGET, (current) => {
        tui = current; // Stable Pi proxy follows /settings mode switches.
        return { render: () => [], invalidate() {} };
      });
      compatibility = await checkCompatibility(VERSION);
      if (!compatibility.compatible) throw new Error(compatibility.cause);
      inputTopology = await resolveInputTopology();
      const activatedTopology = inputTopology;
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
        requireTmuxOrigin: activatedTopology.kind === "tmux",
        open: (href, cwd, lifetime, origin) => dispatchOpenTarget(
          href, cwd, lifetime, origin, { topology: activatedTopology },
        ),
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
        notify: (message, error) => {
          if (rollbackNotes) { if (error) rollbackNotes.push(message); return; }
          ctx.ui.notify(message, error ? "error" : "info");
        },
      });
      try {
        mouse = await enableTmuxMouse(activatedTopology);
        if (mouse.warnings.length) mouseWarning = `tmux 鼠标绑定缺少来源标记，未覆盖非受管绑定：${mouse.warnings.join(", ")}；缺标记的 Ctrl+点击会拒绝打开`;
      } catch (error) { mouseWarning = `tmux Ctrl+鼠标透传未就绪：${String(error)}`; }
      if (mouseWarning) ctx.ui.notify(mouseWarning, "warning");
      tui?.invalidate();
      tui?.requestRender();
      if (tui?.mode !== "fullscreen") ctx.ui.notify(`pi-copy-links 已加载。${HELP}`, "info");
      failure = undefined;
    } catch (error) {
      // Preserve the original startup failure while retiring hooks/actions and
      // releasing mouse ownership, even when later cleanup/UI operations fail.
      const primary = reason(error);
      rollbackNotes = [];
      rollbackNotes.push(...await releaseOwners());
      try { ctx.ui.setWidget(WIDGET, undefined); } catch (cleanup) { rollbackNotes.push(cleanup); }
      tui = undefined;
      const suffix = rollbackNotes.length ? `；启动清理：${rollbackNotes.map(reason).join("；")}` : "";
      rollbackNotes = undefined;
      failure = compatibilityDiagnostic(VERSION, primary + suffix);
      const warningKey = compatibilityDiagnostic(VERSION, primary);
      if (warningKey !== warnedFailure) {
        warnedFailure = warningKey;
        try { ctx.ui.notify(failure, "warning"); } catch { /* /copy-links retains the primary failure. */ }
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
      const contract = !failure && actual?.available && compatibility?.compatible ?
        `实际兼容检查通过（Pi ${VERSION}，隔离探针 ${compatibility.probeMs.toFixed(1)} ms）` : undefined;
      ctx.ui.notify([state, contract, mouseWarning].filter(Boolean).join("\n"),
        failure || (actual && !actual.available) || mouseWarning ? "warning" : "info");
    },
  });

  pi.on("session_shutdown", async (_event, ctx) => {
    const issues = await releaseOwners();
    if (ctx.mode === "tui") {
      try { tui?.invalidate(); } catch (error) { issues.push(error); }
      try { ctx.ui.setWidget(WIDGET, undefined); } catch (error) { issues.push(error); }
    }
    tui = undefined;
    if (issues.length && ctx.mode === "tui") {
      try { ctx.ui.notify(`pi-copy-links 会话清理失败：${reason(issues[0])}`, "warning"); } catch { /* noexcept */ }
    }
  });
}
