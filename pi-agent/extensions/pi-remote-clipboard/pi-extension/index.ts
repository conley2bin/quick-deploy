import * as PiAgent from "@earendil-works/pi-coding-agent";
import * as PiTui from "@earendil-works/pi-tui";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";

const LIB_DIR = process.env.PI_REMOTE_CLIPBOARD_DIR
  ?? `${process.env.HOME}/.local/lib/pi-remote-clipboard`;
const CTRLV_BIN = `${LIB_DIR}/ctrlv.py`;
const PUSH_BIN = `${LIB_DIR}/push.py`;
const TIMEOUT_MS = 8000;
/** TERM 之后给子进程的退出宽限；到期仍存活则 KILL。 */
const TERM_GRACE_MS = 1000;
const MAX_OUTPUT_CHARS = 1024 * 1024;
const MAX_OSC52_ENCODED = 100_000;
/** pi-copy-links 等扩展查询的复制分流函数；返回 true 表示已写往对端剪贴板。 */
const COPY_SINK_KEY = Symbol.for("pi-remote-clipboard:copy-sink");

type CopySink = (text: string) => Promise<boolean>;

/** 会话已结束的取消结果；调用方必须能与“不是对端 / 复制失败”区分开。 */
class ClipboardCancelled extends Error {
  override name = "AbortError";
  constructor() {
    super("pi-remote-clipboard session ended");
  }
}

const isCancelled = (error: unknown): boolean => error instanceof ClipboardCancelled;

/** 一次 session_start 到 session_shutdown 的生命周期：其间启动的所有异步操作归它所有。 */
interface Owner {
  dead: boolean;
  /** 进行中的子进程操作；cancel 时逐个拒绝并终止。 */
  readonly aborts: Set<() => void>;
  sink?: CopySink;
}

function newOwner(): Owner {
  return { dead: false, aborts: new Set() };
}

function cancelOwner(owner: Owner): void {
  owner.dead = true;
  for (const abort of [...owner.aborts]) {
    try {
      abort();
    } catch {
      // 单个子进程终止失败不阻止其余操作被取消
    }
  }
  owner.aborts.clear();
}

function assertLive(owner: Owner): void {
  if (owner.dead) throw new ClipboardCancelled();
}

/**
 * 在 owner 名下运行子进程。独立于 execFile 的协作式 timeout：
 * 到期立即以超时错误结算，并 TERM→(TERM_GRACE_MS)→KILL；owner 取消时同样处理。
 * 结算后销毁管道、清除监听与定时器，不等待 close（被继承的管道可能永不关闭）。
 */
function runHelper(
  owner: Owner, file: string, args: string[], timeoutMs: number,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    if (owner.dead) {
      reject(new ClipboardCancelled());
      return;
    }
    const child = spawn(file, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let exited = false;
    let termSent = false;
    let killTimer: ReturnType<typeof setTimeout> | undefined;
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;

    const terminate = (): void => {
      if (exited || termSent) return;
      termSent = true;
      child.kill("SIGTERM");
      killTimer = setTimeout(() => {
        killTimer = undefined;
        if (!exited) child.kill("SIGKILL");
      }, TERM_GRACE_MS);
    };
    const settle = (finish: () => void): void => {
      if (settled) return;
      settled = true;
      owner.aborts.delete(abort);
      if (deadlineTimer) clearTimeout(deadlineTimer);
      deadlineTimer = undefined;
      child.stdout?.removeAllListeners("data");
      child.stderr?.removeAllListeners("data");
      child.stdout?.destroy();
      child.stderr?.destroy();
      finish();
    };
    const fail = (message: string, extra: object = {}): void =>
      settle(() => reject(Object.assign(new Error(message), { stderr }, extra)));
    const abort = (): void => {
      settle(() => reject(new ClipboardCancelled()));
      terminate();
    };
    owner.aborts.add(abort);

    child.stdout?.setEncoding("utf8");
    child.stderr?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      stdout += chunk;
      if (stdout.length > MAX_OUTPUT_CHARS) {
        fail(`${file} output exceeded ${MAX_OUTPUT_CHARS} characters`, { code: "ERR_MAXBUFFER" });
        terminate();
      }
    });
    child.stderr?.on("data", (chunk: string) => {
      if (stderr.length < MAX_OUTPUT_CHARS) stderr += chunk;
    });
    child.once("exit", () => {
      exited = true;
      if (killTimer) clearTimeout(killTimer);
      killTimer = undefined;
    });
    child.once("error", (error: NodeJS.ErrnoException) => {
      exited = true;
      fail(error.message, { code: error.code });
    });
    child.once("close", (code, signal) => {
      if (code === 0) settle(() => resolve({ stdout, stderr }));
      else fail(`${file} exited with ${code ?? signal}`, { code: code ?? undefined });
    });
    deadlineTimer = setTimeout(() => {
      deadlineTimer = undefined;
      fail(`${file} timed out after ${timeoutMs}ms`, { code: "ETIMEDOUT", killed: true });
      terminate();
    }, timeoutMs);
  });
}

/** tmux 场景按“当前 session 中最近活动的 client”的祖先链判定；非 tmux 用 SSH 环境变量。
 * 不用 display-message 的 client_pid：多 client 同 session 时它解析到别的 client。 */
async function isRemoteClient(owner: Owner): Promise<boolean> {
  assertLive(owner);
  const pane = process.env.TMUX_PANE;
  if (process.env.TMUX && pane) {
    try {
      const sessionOut = await runHelper(
        owner, "tmux", ["display-message", "-p", "-t", pane, "#{session_name}"], 1000,
      );
      assertLive(owner);
      const session = sessionOut.stdout.trim();
      if (!session) return false;
      const listOut = await runHelper(
        owner, "tmux", ["list-clients", "-t", session, "-F", "#{client_pid} #{client_activity}"], 1000,
      );
      assertLive(owner);
      let bestPid = -1;
      let bestActivity = -1;
      for (const line of listOut.stdout.split("\n")) {
        const parts = line.trim().split(/\s+/);
        if (parts.length < 2) continue;
        const pid = Number.parseInt(parts[0], 10);
        const activity = Number.parseInt(parts[1], 10);
        if (Number.isFinite(pid) && Number.isFinite(activity) && activity > bestActivity) {
          bestActivity = activity;
          bestPid = pid;
        }
      }
      if (bestPid <= 0) return false;
      let pid = bestPid;
      for (let depth = 0; depth < 16 && pid > 1; depth += 1) {
        let stat: string;
        try {
          stat = await readFile(`/proc/${pid}/stat`, "utf8");
        } catch {
          assertLive(owner);
          return false;
        }
        assertLive(owner);
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
    } catch (error: unknown) {
      if (isCancelled(error)) throw error;
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

async function copyToClientIfRemote(owner: Owner, text: string): Promise<boolean> {
  if (!(await isRemoteClient(owner))) return false;
  assertLive(owner);
  return emitOsc52(text);
}

export default function piRemoteClipboard(pi: ExtensionAPI): void {
  let current = newOwner();
  let restoreCopy: (() => void) | undefined;
  let restorePaste: (() => void) | undefined;

  interface PasteHost {
    editor?: { insertTextAtCursor?: (text: string) => void };
    ui?: { requestRender?: () => void };
  }

  /** 读取对端剪贴板并插入当前编辑器；"local" 表示应回退原生路径。 */
  async function pasteFromBridge(owner: Owner, host: PasteHost): Promise<"inserted" | "empty" | "local" | "error"> {
    try {
      const { stdout } = await runHelper(owner, "python3", [CTRLV_BIN], TIMEOUT_MS);
      assertLive(owner);
      const text = stdout.trim();
      if (text.length === 0) return "empty";
      host.editor?.insertTextAtCursor?.(text);
      host.ui?.requestRender?.();
      return "inserted";
    } catch (error: unknown) {
      if (isCancelled(error)) throw error;
      const code = (error as { code?: number }).code;
      if (code === 2) return "local";
      return "error";
    }
  }

  const handleRemotePaste = async (ctx: ExtensionContext): Promise<void> => {
    const owner = current;
    try {
      const { stdout } = await runHelper(owner, "python3", [CTRLV_BIN], TIMEOUT_MS);
      assertLive(owner);
      if (stdout.trim().length === 0) {
        ctx.ui.notify("对端剪贴板为空", "info");
        return;
      }
      ctx.ui.pasteToEditor(stdout);
    } catch (error: unknown) {
      if (isCancelled(error) || owner.dead) return;
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
      const owner = current;
      try {
        await runHelper(owner, "python3", argv, TIMEOUT_MS);
        assertLive(owner);
        ctx.ui.notify("已推送到对端剪贴板", "info");
      } catch (error: unknown) {
        if (isCancelled(error) || owner.dead) return;
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
    // 每次启动都开启新的所有者；仍存活的前任（未经 shutdown 的重叠启动）在此被取消。
    cancelOwner(current);
    const owner = newOwner();
    current = owner;
    if (ctx.mode !== "tui") return;

    // 复制方向分流：SSH 客户端在观看时，文本复制改走 OSC 52（经 tmux 到对端剪贴板）。
    // 本地会话完全走原生路径，一行行为不变。
    const unavailable: string[] = [];
    const altProto = PiTui.TuiAltScreen?.prototype as unknown as {
      copyTextToClipboard?: (this: { flash?: (msg: string) => void }, text: string) => Promise<boolean>;
    } | undefined;
    if (typeof altProto?.copyTextToClipboard === "function"
      && Object.getOwnPropertyDescriptor(altProto, "copyTextToClipboard")?.writable) {
      const original = altProto.copyTextToClipboard;
      altProto.copyTextToClipboard = async function (text: string): Promise<boolean> {
        try {
          if (await copyToClientIfRemote(owner, text)) {
            try {
              this.flash?.("已复制到对端剪贴板");
            } catch {
              // 提示失败不改变复制结果
            }
            return true;
          }
        } catch (error: unknown) {
          if (isCancelled(error)) return false;
          throw error;
        }
        if (owner.dead) return false;
        return original.call(this, text);
      };
      restoreCopy = () => {
        if (altProto.copyTextToClipboard !== undefined && altProto.copyTextToClipboard !== original) {
          altProto.copyTextToClipboard = original;
        }
      };
    } else {
      unavailable.push("TuiAltScreen.copyTextToClipboard 不可用，未接管文本复制");
    }

    // 粘贴方向分流：Ctrl+V 按键不变，包住 Pi 的粘贴入口。
    // SSH 客户端在观看时走剪贴板桥（文本直接插入，图片落临时文件插路径）；
    // 本地会话原样调用原生 handleClipboardPaste（图片优先语义不变）。
    const modeProto = PiAgent.InteractiveMode?.prototype as unknown as {
      handleClipboardPaste?: (this: PasteHost) => Promise<void>;
    } | undefined;
    if (typeof modeProto?.handleClipboardPaste === "function"
      && Object.getOwnPropertyDescriptor(modeProto, "handleClipboardPaste")?.writable) {
      const originalPaste = modeProto.handleClipboardPaste;
      modeProto.handleClipboardPaste = async function (this: PasteHost): Promise<void> {
        try {
          if (!(await isRemoteClient(owner))) {
            assertLive(owner);
            return originalPaste.call(this);
          }
          const outcome = await pasteFromBridge(owner, this);
          if (outcome === "local") {
            assertLive(owner);
            return originalPaste.call(this);
          }
        } catch (error: unknown) {
          if (isCancelled(error)) return;
          throw error;
        }
      };
      restorePaste = () => {
        if (modeProto.handleClipboardPaste !== undefined && modeProto.handleClipboardPaste !== originalPaste) {
          modeProto.handleClipboardPaste = originalPaste;
        }
      };
    } else {
      unavailable.push("InteractiveMode.handleClipboardPaste 不可用，Ctrl+V 未通过桥接管");
    }

    // 供 pi-copy-links 等扩展在点击时调用：返回 true 表示已写往对端。
    // 已退役的 sink 以 AbortError 拒绝，调用方不会误入原生复制回退。
    const sink: CopySink = (text) => copyToClientIfRemote(owner, text);
    owner.sink = sink;
    (globalThis as Record<symbol, unknown>)[COPY_SINK_KEY] = sink;
    if (unavailable.length > 0) {
      ctx.ui.notify(`pi-remote-clipboard: ${unavailable.join("；")}；Ctrl+Alt+V 和 /rpush 仍可用`, "warning");
    }
  });

  pi.on("session_shutdown", () => {
    // 先标记死亡并取消全部进行中的操作，再做可能抛错的钩子恢复。
    const owner = current;
    cancelOwner(owner);
    try {
      restoreCopy?.();
    } finally {
      restoreCopy = undefined;
      try {
        restorePaste?.();
      } finally {
        restorePaste = undefined;
        const globals = globalThis as Record<symbol, unknown>;
        if (owner.sink && globals[COPY_SINK_KEY] === owner.sink) {
          delete globals[COPY_SINK_KEY];
        }
      }
    }
  });
}
