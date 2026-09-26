import { VERSION, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const CTRLV_BIN = process.env.PI_REMOTE_CLIPBOARD_CTRLV_BIN
  ?? `${process.env.HOME}/.local/lib/pi-remote-clipboard/ctrlv.py`;
const TIMEOUT_MS = 8000;

export default function piRemoteClipboard(pi: ExtensionAPI): void {
  pi.on("session_start", (_event, ctx) => {
    if (ctx.mode !== "tui") return;

    const editor = (ctx as unknown as { editor?: { handleInput?: (data: string) => void } }).editor;
    if (!editor?.handleInput) return;

    const originalHandleInput = editor.handleInput.bind(editor);
    editor.handleInput = (data: string) => {
      // Ctrl+V 到达编辑器之前拦截：先尝试远程剪贴板桥
      if (data !== "\x16") {
        originalHandleInput(data);
        return;
      }
      void (async () => {
        try {
          const { stdout, stderr } = await execFileAsync("python3", [CTRLV_BIN], {
            timeout: TIMEOUT_MS,
            encoding: "utf8",
          });
          const text = stdout.trim();
          if (text.length === 0) {
            // 本地 Pi 或剪贴板为空：回退原生
            originalHandleInput(data);
            return;
          }
          // 远程有内容：直接插入（图片路径或文本）
          const bracketed = `\x1b[200~${text}\x1b[201~`;
          originalHandleInput(bracketed);
        } catch (error: unknown) {
          const code = (error as { code?: number }).code;
          if (code === 2) {
            // 本地 Pi：回退原生
            originalHandleInput(data);
            return;
          }
          const message = error instanceof Error ? error.message : String(error);
          ctx.ui.notify(`远程剪贴板粘贴失败：${message}`, "error");
        }
      })();
    };
  });
}
