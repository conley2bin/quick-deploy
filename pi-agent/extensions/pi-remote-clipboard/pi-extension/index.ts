import { VERSION, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import { TuiAltScreen } from "@earendil-works/pi-tui";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const LIB_DIR = process.env.PI_REMOTE_CLIPBOARD_DIR
  ?? `${process.env.HOME}/.local/lib/pi-remote-clipboard`;
const CTRLV_BIN = `${LIB_DIR}/ctrlv.py`;
const PUSH_BIN = `${LIB_DIR}/push.py`;
const TIMEOUT_MS = 8000;
const MAX_OSC52_ENCODED = 100_000;
const SUPPORTED_PI = ["0.86.1", "0.87.1"];
/** pi-copy-links 等扩展查询的复制分流函数；返回 true 表示已写往对端剪贴板。 */
const COPY_SINK_KEY = Symbol.for("pi-remote-clipboard:copy-sink");

type CopySink = (text: string) => Promise<boolean>;

/** tmux 场景按“当前输入 client 的祖先链”判定；非 tmux 用 SSH 环境变量。 */
async function isRemoteClient(): Promise<boolean> {
  const pane = process.env.TMUX_PANE;
  if (process.env.TMUX && pane) {
    try {
      const { stdout } = await execFileAsync(
        "tmux", ["display-message", "-p", "-t", pane, "#{client_pid}"],
        { timeout: 1000, encoding: "utf8" },
      );
      let pid = Number.parseInt(stdout.trim(), 10);
      for (let depth = 0; depth < 16 && pid > 1; depth += 1) {
        let stat: string;
        try {
          stat = await readFile(`/proc/${pid}/stat`, "utf8");
        } catch {
          return false;
        }
        const close = stat.lastIndexOf(")");
        if (close < 0) return false;
        const comm = stat.slice(stat.indexOf("(") + 1, close);
        if (comm === "sshd") return true;
        const rest = stat.slice(close + 2).split(" ");
        const ppid = Number.parseInt(rest[1] ?? "", 10);
        if (!Number.isFinite(ppid)) return false;
        pid = ppid;
      }
      return false;
    } catch {
      return false;
    }
  }
  return Boolean(process.env.SSH_CONNECTION || process.env.SSH_CLIENT || process.env.MOSH_CONNECTION);
}

/** 向 pane 写 OSC 52；tmux set-clipboard=on 会转发给正在观看的客户端。 */
function emitOsc52(text: string): boolean {
  if (process.stdout.isTTY !== true) return false;
  if (typeof text !== "string" || text.length === 0) return false;
  const encoded = Buffer.from(text, "utf8").toString("base64");
  if (encoded.length > MAX_OSC52_ENCODED) return false;
  try {
    process.stdout.write(`\x1b]52;c;${encoded}\x07`);
    return true;
  } catch {
    return false;
  }
}

async function copyToClientIfRemote(text: string): Promise<boolean> {
  if (!(await isRemoteClient())) return false;
  return emitOsc52(text);
}

export default function piRemoteClipboard(pi: ExtensionAPI): void {
  if (!SUPPORTED_PI.includes(VERSION)) return;

  let restoreCopy: (() => void) | undefined;

  const handleRemotePaste = async (ctx: ExtensionContext): Promise<void> => {
    try {
      const { stdout } = await execFileAsync("python3", [CTRLV_BIN], {
        timeout: TIMEOUT_MS,
        encoding: "utf8",
      });
      const text = stdout.trim();
      if (text.length === 0) {
        ctx.ui.notify("对端剪贴板为空", "info");
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
      ctx.ui.notify(`对端剪贴板读取失败：${message}`, "error");
    }
  };

  // 快捷键与命令在加载时注册，确保首次 session 就可用；handler 每次拿到自己的 ctx。
  pi.registerShortcut("ctrl+alt+v", {
    description: "从 SSH 对端粘贴剪贴板（文本或图片）",
    handler: (shortcutCtx) => handleRemotePaste(shortcutCtx),
  });

  pi.registerCommand("rpush", {
    description: "把本机图片/剪贴板推送到 SSH 对端剪贴板（/rpush [图片路径]）",
    handler: async (args, ctx) => {
      const path = args.trim();
      const argv = path ? [PUSH_BIN, path] : [PUSH_BIN];
      try {
        await execFileAsync("python3", argv, { timeout: TIMEOUT_MS, encoding: "utf8" });
        ctx.ui.notify("已推送到对端剪贴板", "info");
      } catch (error: unknown) {
        const err = error as { code?: number; stderr?: string };
        if (err.code === 2) {
          ctx.ui.notify("当前为本地会话，无需推送", "info");
          return;
        }
        const detail = (err.stderr ?? "").trim() || (error instanceof Error ? error.message : String(error));
        ctx.ui.notify(`推送失败：${detail}`, "error");
      }
    },
  });

  pi.on("session_start", (_event, ctx) => {
    if (ctx.mode !== "tui") return;

    // 复制方向分流：SSH 客户端在观看时，文本复制改走 OSC 52（经 tmux 到对端剪贴板）。
    // 本地会话完全走原生路径，一行行为不变。
    const altProto = TuiAltScreen.prototype as unknown as {
      copyTextToClipboard?: (this: { flash?: (msg: string) => void }, text: string) => Promise<boolean>;
    };
    if (typeof altProto.copyTextToClipboard !== "function") return;
    const original = altProto.copyTextToClipboard;
    altProto.copyTextToClipboard = async function (text: string): Promise<boolean> {
      if (await copyToClientIfRemote(text)) {
        try {
          this.flash?.("已复制到对端剪贴板");
        } catch {
          // 提示失败不改变复制结果
        }
        return true;
      }
      return original.call(this, text);
    };
    restoreCopy = () => {
      if (altProto.copyTextToClipboard !== undefined && altProto.copyTextToClipboard !== original) {
        altProto.copyTextToClipboard = original;
      }
    };

    // 供 pi-copy-links 等扩展在点击时调用：返回 true 表示已写往对端。
    const sink: CopySink = copyToClientIfRemote;
    (globalThis as Record<symbol, unknown>)[COPY_SINK_KEY] = sink;
  });

  pi.on("session_shutdown", () => {
    restoreCopy?.();
    restoreCopy = undefined;
    const globals = globalThis as Record<symbol, unknown>;
    if (globals[COPY_SINK_KEY] === copyToClientIfRemote) {
      delete globals[COPY_SINK_KEY];
    }
  });
}
