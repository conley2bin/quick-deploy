import { VERSION, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const CTRLV_BIN = process.env.PI_REMOTE_CLIPBOARD_CTRLV_BIN
  ?? `${process.env.HOME}/.local/lib/pi-remote-clipboard/ctrlv.py`;
const TIMEOUT_MS = 8000;

export default function piRemoteClipboard(pi: ExtensionAPI): void {
  let unsubscribe: (() => void) | undefined;

  const handleRemotePaste = async (ctx: ExtensionContext): Promise<void> => {
    try {
      const { stdout, stderr } = await execFileAsync("python3", [CTRLV_BIN], {
        timeout: TIMEOUT_MS,
        encoding: "utf8",
      });
      const text = stdout.trim();
      if (text.length === 0) {
        ctx.ui.notify("远程剪贴板为空或当前为本地会话", "info");
        return;
      }
      ctx.ui.pasteToEditor(text);
    } catch (error: unknown) {
      const code = (error as { code?: number }).code;
      if (code === 2) {
        ctx.ui.notify("当前为本地会话，请使用原生 Ctrl+V", "info");
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      ctx.ui.notify(`远程剪贴板粘贴失败：${message}`, "error");
    }
  };

  pi.on("session_start", (_event, ctx) => {
    if (ctx.mode !== "tui") return;

    // 注册一个扩展快捷键：Ctrl+Alt+V 作为远程剪贴板粘贴
    // 用户原有的 Ctrl+V 保持 Pi 原生行为（本地剪贴板）
    pi.registerShortcut("ctrl+alt+v", {
      description: "从 SSH 对端粘贴剪贴板（文本或图片）",
      handler: (shortcutCtx) => handleRemotePaste(shortcutCtx),
    });
  });

  pi.on("session_shutdown", () => {
    unsubscribe?.();
    unsubscribe = undefined;
  });
}
