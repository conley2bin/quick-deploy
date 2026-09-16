import { copyToClipboard, VERSION, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import type { TUI } from "@earendil-works/pi-tui";
import { installAdapter } from "./src/adapter.ts";
import { openWebUrl } from "./src/browser.ts";
import { enableTmuxMouse } from "./src/tmux.ts";

const WIDGET = "pi-copy-links:bridge";
const HELP = "在 /settings 将 TUI mode 设为 fullscreen：代码块右下角左键复制，Ctrl+左键打开网页。普通模式的历史滚动由终端接管，不支持这些复制按钮。";

export default function copyLinksExtension(pi: ExtensionAPI): void {
  let adapter: ReturnType<typeof installAdapter> | undefined;
  let tui: TUI | undefined;
  let failure: string | undefined;
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
      adapter = installAdapter({
        version: VERSION,
        active: () => tui?.mode === "fullscreen",
        copy: copyToClipboard,
        open: openWebUrl,
        button: (text) => ctx.ui.theme.fg("accent", text),
        recoverTmuxRelease: () => mouse?.enabled === true && mouse.warnings.length === 0,
        notify: (message, error) => ctx.ui.notify(message, error ? "error" : "info"),
      });
      failure = undefined;
      try {
        mouse = await enableTmuxMouse();
        if (mouse.warnings.length) mouseWarning = `tmux 已有自定义绑定，未覆盖：${mouse.warnings.join(", ")}；Ctrl+点击可能被拦截`;
      } catch (error) { mouseWarning = `tmux Ctrl+鼠标透传未就绪：${String(error)}`; }
      if (mouseWarning) ctx.ui.notify(mouseWarning, "warning");
      tui?.invalidate();
      tui?.requestRender();
      if (tui?.mode !== "fullscreen") ctx.ui.notify(`pi-copy-links 已加载。${HELP}`, "info");
    } catch (error) {
      failure = String(error);
      ctx.ui.notify(failure, "error");
    }
  });

  pi.registerCommand("copy-links", {
    description: "查看代码复制按钮和 Ctrl+单击链接的启用状态",
    handler: async (_args, ctx) => {
      const state = failure ?? (adapter && tui?.mode === "fullscreen" ?
        "已启用：点击 [复制] 获取原始代码；Ctrl+左键打开 HTTP(S) 网页。" : HELP);
      ctx.ui.notify([state, mouseWarning].filter(Boolean).join("\n"), failure ? "error" : mouseWarning ? "warning" : "info");
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
