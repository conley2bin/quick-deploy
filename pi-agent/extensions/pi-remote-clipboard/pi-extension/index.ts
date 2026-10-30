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
/** 与 execFile 的 maxBuffer 同为字节预算（非字符数）：UTF-8 多字节文本也不得超过 1 MiB。 */
const MAX_OUTPUT_BYTES = 1024 * 1024;
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
  /** 仍占有资源（子进程组、定时器、管道）的辅助进程；直到清理完成才移出，与 promise 结算无关。 */
  readonly helpers: Set<Helper>;
  sink?: CopySink;
}

interface Helper {
  /** 结算为取消错误（若尚未结算）并终止进程组；幂等。 */
  abort(): void;
  /** 子进程组已退出或已被 KILL、管道已销毁、定时器已清除后 resolve；永不 reject。 */
  readonly quiescent: Promise<void>;
}

function newOwner(): Owner {
  return { dead: false, helpers: new Set() };
}

/** 标记死亡并取消全部辅助进程；返回它们完成清理的时刻。幂等，可重复调用。 */
function cancelOwner(owner: Owner): Promise<void> {
  owner.dead = true;
  const pending = [...owner.helpers];
  for (const helper of pending) {
    try {
      helper.abort();
    } catch {
      // 单个子进程终止失败不阻止其余操作被取消
    }
  }
  return Promise.all(pending.map((helper) => helper.quiescent)).then(() => undefined);
}

function assertLive(owner: Owner): void {
  if (owner.dead) throw new ClipboardCancelled();
}

const POSIX = process.platform !== "win32";

/**
 * 在 owner 名下运行子进程，字节预算与 execFile 的 maxBuffer 相同（stdout、stderr 各 1 MiB）。
 * POSIX 下子进程自成进程组，只向该组（经校验的正 PID）发信号，终止时连同其后代一起 TERM→KILL；
 * Windows 下只能终止直接子进程。
 * 到期 / 溢出 / 取消时 promise 立即结算，但 owner 仍持有该 helper，直到进程组被清理、
 * 管道销毁、定时器清除（Helper.quiescent），所以结算后的 TERM 不服从者仍在 shutdown 的等待范围内。
 */
function runHelper(
  owner: Owner, file: string, args: string[], timeoutMs: number,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    if (owner.dead) {
      reject(new ClipboardCancelled());
      return;
    }
    const child = spawn(file, args, { stdio: ["ignore", "pipe", "pipe"], detached: POSIX });
    const pid = child.pid;
    const groupId = POSIX && Number.isInteger(pid) && (pid as number) > 1 ? (pid as number) : undefined;
    const out: Buffer[] = [];
    const err: Buffer[] = [];
    let outBytes = 0;
    let errBytes = 0;
    let settled = false;
    let exited = false;
    let groupGone = false;
    let terminating = false;
    let killSent = false;
    let quiet = false;
    let killTimer: ReturnType<typeof setTimeout> | undefined;
    let waitTimer: ReturnType<typeof setTimeout> | undefined;
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
    let release!: () => void;
    const quiescent = new Promise<void>((done) => { release = done; });

    const signal = (sig: "SIGTERM" | "SIGKILL"): void => {
      try {
        if (groupId !== undefined) {
          if (!groupGone) process.kill(-groupId, sig);
        } else if (!exited) {
          child.kill(sig);
        }
      } catch {
        // ESRCH：进程组已不存在
      }
    };
    /** 进程组是否还有成员（领导进程退出之后用来判断后代）。 */
    const probeGroup = (): void => {
      if (groupId === undefined || groupGone) return;
      try {
        process.kill(-groupId, 0);
      } catch {
        groupGone = true;
      }
    };
    const finish = (): void => {
      if (quiet) return;
      quiet = true;
      if (killTimer) clearTimeout(killTimer);
      if (waitTimer) clearTimeout(waitTimer);
      if (deadlineTimer) clearTimeout(deadlineTimer);
      killTimer = waitTimer = deadlineTimer = undefined;
      child.stdout?.removeAllListeners("data");
      child.stderr?.removeAllListeners("data");
      child.stdout?.destroy();
      child.stderr?.destroy();
      owner.helpers.delete(helper);
      release();
    };
    /** 清理完成的条件：已结算且领导进程已退出；终止中则还需进程组已空，或 KILL 已发出。 */
    const maybeFinish = (): void => {
      if (!settled || !exited) return;
      if (!terminating || groupGone || killSent) finish();
    };
    const settle = (done: () => void): void => {
      if (settled) return;
      settled = true;
      done();
      maybeFinish();
    };
    const terminate = (): void => {
      if (terminating) return;
      terminating = true;
      signal("SIGTERM");
      killTimer = setTimeout(() => {
        killTimer = undefined;
        signal("SIGKILL");
        killSent = true;
        // KILL 之后最多再等一个宽限；领导进程处于不可中断状态时也不无限阻塞 shutdown。
        waitTimer = setTimeout(finish, TERM_GRACE_MS);
        maybeFinish();
      }, TERM_GRACE_MS);
      if (exited) {
        probeGroup();
        maybeFinish();
      }
    };
    const fail = (message: string, extra: object = {}): void => {
      const stderr = Buffer.concat(err).toString("utf8");
      settle(() => reject(Object.assign(new Error(message), { stderr }, extra)));
    };
    const overflow = (): void => {
      fail(`${file} output exceeded ${MAX_OUTPUT_BYTES} bytes`, { code: "ERR_MAXBUFFER" });
      terminate();
    };
    const helper: Helper = {
      quiescent,
      abort() {
        settle(() => reject(new ClipboardCancelled()));
        terminate();
      },
    };
    owner.helpers.add(helper);

    child.stdout?.on("data", (chunk: Buffer) => {
      if (settled) return;
      if (outBytes + chunk.length > MAX_OUTPUT_BYTES) { overflow(); return; }
      outBytes += chunk.length;
      out.push(chunk);
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      if (settled) return;
      if (errBytes + chunk.length > MAX_OUTPUT_BYTES) { overflow(); return; }
      errBytes += chunk.length;
      err.push(chunk);
    });
    child.once("exit", () => {
      exited = true;
      probeGroup();
      if (killTimer && groupGone) {
        clearTimeout(killTimer);
        killTimer = undefined;
      }
      maybeFinish();
    });
    child.once("error", (error: NodeJS.ErrnoException) => {
      exited = true;
      groupGone = true;
      fail(error.message, { code: error.code });
      finish();
    });
    child.once("close", (code, signalName) => {
      if (code === 0) {
        settle(() => resolve({
          stdout: Buffer.concat(out).toString("utf8"),
          stderr: Buffer.concat(err).toString("utf8"),
        }));
      } else {
        fail(`${file} exited with ${code ?? signalName}`, { code: code ?? undefined });
      }
      maybeFinish();
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
  const remote = await isRemoteClient(owner);
  // 取消必须先于任何返回值：false 会让 copy-links 走原生复制回退。
  assertLive(owner);
  return remote ? emitOsc52(text) : false;
}

export default function piRemoteClipboard(pi: ExtensionAPI): void {
  let current = newOwner();
  /** 已取消但尚未完成清理的 owner；shutdown 一并等待，连续 start 不会丢失前任。 */
  const retiring = new Set<Promise<void>>();
  function retire(owner: Owner): Promise<void> {
    const cleanup = cancelOwner(owner);
    retiring.add(cleanup);
    void cleanup.then(() => retiring.delete(cleanup));
    return cleanup;
  }
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
    void retire(current);
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
            if (owner.dead) return true; // 写入已发生；会话已结束，不再提示
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

  pi.on("session_shutdown", async () => {
    // 先标记死亡并取消全部进行中的操作，再做可能抛错的钩子恢复；
    // 无论恢复是否抛错，都等待所有 owner（含已超时、已被替换的）的子进程组清理完毕，
    // 因为 Pi 在 shutdown 返回后即退出进程，会销毁尚未触发的 KILL 定时器。
    const owner = current;
    retire(owner);
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
        await Promise.all([...retiring]);
      }
    }
  });
}
