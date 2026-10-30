import * as PiAgent from "@earendil-works/pi-coding-agent";
import * as PiTui from "@earendil-works/pi-tui";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, realpathSync } from "node:fs";
import { link, lstat, mkdtemp, readFile, rm, stat, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * 助手脚本目录。显式 PI_REMOTE_CLIPBOARD_DIR 优先；默认取本扩展真实路径（经受管符号链接解析）
 * 旁的 ../src，使协议两端永远来自同一份检出，不依赖 install.sh 复制到 ~/.local/lib 的旧副本。
 * 仅当扩展被单独复制而没有同级 src 时才退回安装副本，其过期时由回执校验显式报错。
 */
function resolveLibDir(): string {
  const override = process.env.PI_REMOTE_CLIPBOARD_DIR;
  if (override !== undefined) return override;
  const colocated = join(dirname(realpathSync(fileURLToPath(import.meta.url))), "..", "src");
  return existsSync(join(colocated, "ctrlv-artifact-v1.py")) ? colocated : `${process.env.HOME}/.local/lib/pi-remote-clipboard`;
}
const LIB_DIR = resolveLibDir();
/** 受控粘贴只运行这个版本化入口；旧版助手目录里没有它，因此旧生产者（会无视 argv 自行写图片）永远不会被启动。 */
const ARTIFACT_ENDPOINT = `${LIB_DIR}/ctrlv-artifact-v1.py`;
const PUSH_BIN = `${LIB_DIR}/push.py`;
const TIMEOUT_MS = 8000;
/** TERM 之后给子进程的退出宽限；到期仍存活则 KILL。 */
const TERM_GRACE_MS = 1000;
/** 与 execFile 的 maxBuffer 同为字节预算（非字符数）：UTF-8 多字节文本也不得超过 1 MiB。 */
const MAX_OUTPUT_BYTES = 1024 * 1024;
/** 回执首行的预算：文本正文仍受 MAX_OUTPUT_BYTES 约束，回执不占用它。 */
const RECEIPT_HEADER_BYTES = 256;
/** ctrlv.py 的版本化回执协议；与 src/requester.py 的 ARTIFACT_PROTOCOL 同值。 */
const ARTIFACT_PROTOCOL = "pi-remote-clipboard/artifact-1";
const STAGING_PREFIX = "pi-remote-clipboard-stage-";
const MAX_OSC52_ENCODED = 100_000;
/** pi-copy-links 等扩展查询的复制分流函数；返回 true 表示已写往对端剪贴板。 */
const COPY_SINK_KEY = Symbol.for("pi-remote-clipboard:copy-sink");

/** Shared across module URLs; tagged wrappers are replacement owners, not middleware. */
const OWNED_LAYER_KEY = Symbol.for("pi-remote-clipboard:owned-layer:v1");
type SlotFlags = { writable: boolean; enumerable: boolean; configurable: boolean };
type Predecessor = { kind: "owned"; record: OwnedLayer; flags: SlotFlags }
  | { kind: "base"; descriptor: PropertyDescriptor | undefined };
interface OwnedLayer {
  live: boolean;
  callable: Function | undefined;
  previous: Predecessor;
}
interface Hook {
  target: object;
  key: PropertyKey;
  record: OwnedLayer;
  flags: SlotFlags;
}
const slotFlags = (d: PropertyDescriptor): SlotFlags => ({
  writable: d.writable === true, enumerable: d.enumerable === true, configurable: d.configurable === true,
});
const sameFlags = (d: PropertyDescriptor, flags: SlotFlags): boolean =>
  d.writable === flags.writable && d.enumerable === flags.enumerable && d.configurable === flags.configurable;

/** Metadata inspection never evaluates a function's foreign getters. */
function ownedLayer(value: unknown): OwnedLayer | undefined {
  if (typeof value !== "function") return undefined;
  const tagged = Object.getOwnPropertyDescriptor(value, OWNED_LAYER_KEY);
  if (!tagged || !("value" in tagged) || typeof tagged.value !== "object" || tagged.value === null) return undefined;
  const record = tagged.value as OwnedLayer;
  const live = Object.getOwnPropertyDescriptor(record, "live");
  const callable = Object.getOwnPropertyDescriptor(record, "callable");
  const previous = Object.getOwnPropertyDescriptor(record, "previous");
  if (!live || !("value" in live) || typeof live.value !== "boolean"
    || !callable || !("value" in callable) || (live.value && callable.value !== value)
    || !previous || !("value" in previous) || typeof previous.value !== "object" || previous.value === null) return undefined;
  const kind = Object.getOwnPropertyDescriptor(previous.value, "kind");
  if (!kind || !("value" in kind) || (kind.value !== "owned" && kind.value !== "base")) return undefined;
  return record;
}

/** Dead nodes contain forwarding information only; compress them without bypassing live restoration owners. */
function normalize(link: Predecessor): Predecessor {
  if (link.kind === "owned" && !link.record.live) {
    link.record.previous = normalize(link.record.previous);
    return link.record.previous;
  }
  return link;
}
function fallbackBase(record: OwnedLayer): Function {
  record.previous = normalize(record.previous);
  let link = record.previous;
  while (link.kind === "owned") {
    link.record.previous = normalize(link.record.previous);
    link = link.record.previous;
  }
  return link.descriptor!.value as Function;
}
function restorePredecessor(record: OwnedLayer): PropertyDescriptor | undefined {
  record.previous = normalize(record.previous);
  const link = record.previous;
  return link.kind === "base" ? link.descriptor : { ...link.flags, value: link.record.callable };
}
function uninstall(hook: Hook, callable: Function | undefined): void {
  const current = Object.getOwnPropertyDescriptor(hook.target, hook.key);
  if (!current || !("value" in current) || current.value !== callable || !sameFlags(current, hook.flags)) return;
  const previous = restorePredecessor(hook.record);
  if (previous) Object.defineProperty(hook.target, hook.key, previous);
  else if (!Reflect.deleteProperty(hook.target, hook.key)) throw new Error("pi-remote-clipboard: failed to remove owned hook");
}
/** Only own writable data slots are supported; absence is allowed solely for the global sink. */
function installHook(
  target: object | undefined, key: PropertyKey, method: boolean, create: (record: OwnedLayer) => Function,
): Hook | undefined {
  if (!target) return undefined;
  const descriptor = Object.getOwnPropertyDescriptor(target, key);
  if (descriptor ? (!("value" in descriptor) || descriptor.writable !== true
    || (method && typeof descriptor.value !== "function")) : method) return undefined;
  const predecessor = descriptor && ownedLayer(descriptor.value);
  const previous: Predecessor = predecessor
    ? normalize({ kind: "owned", record: predecessor, flags: slotFlags(descriptor!) })
    : { kind: "base", descriptor };
  const record: OwnedLayer = { live: true, callable: undefined, previous };
  const callable = create(record);
  record.callable = callable;
  Object.defineProperty(callable, OWNED_LAYER_KEY, { value: record });
  const flags = descriptor ? slotFlags(descriptor) : { writable: true, enumerable: true, configurable: true };
  const hook = { target, key, record, flags };
  try {
    Object.defineProperty(target, key, { ...flags, value: callable });
  } catch (error) {
    record.live = false;
    record.callable = undefined;
    // A custom define trap can publish and then throw. Undo any observable owned publication.
    try { uninstall(hook, callable); } catch (cleanupError) {
      console.error("pi-remote-clipboard: failed installation rollback", cleanupError);
    }
    throw error;
  }
  return hook;
}

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
  /** 进行中的制品操作（私有暂存目录、交付文件）；结算即其暂存与未交付文件已清理。永不 reject。 */
  readonly operations: Set<Promise<void>>;
}

interface Helper {
  /** 结算为取消错误（若尚未结算）并终止进程组；幂等。 */
  abort(): void;
  /** 子进程组已退出或已被 KILL、管道已销毁、定时器已清除后 resolve；永不 reject。 */
  readonly quiescent: Promise<void>;
}

function newOwner(): Owner {
  return { dead: false, helpers: new Set(), operations: new Set() };
}

/** 标记死亡并取消全部辅助进程；返回它们完成清理的时刻。幂等，可重复调用。 */
function cancelOwner(owner: Owner): Promise<void> {
  owner.dead = true;
  const pending = [...owner.helpers];
  const operations = [...owner.operations];
  for (const helper of pending) {
    try {
      helper.abort();
    } catch {
      // 单个子进程终止失败不阻止其余操作被取消
    }
  }
  return Promise.all([...pending.map((helper) => helper.quiescent), ...operations]).then(() => undefined);
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
function startHelper(
  owner: Owner, file: string, args: string[], timeoutMs: number, stdoutBytes = MAX_OUTPUT_BYTES,
): { result: Promise<{ stdout: string; stderr: string }>; quiescent: Promise<void> } {
  let quiescent: Promise<void> = Promise.resolve();
  const result = new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
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
    quiescent = new Promise<void>((done) => { release = done; });

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
      // 不在此处 maybeFinish：结算原因（正常 close / 取消 / 超时 / 溢出）决定是否还要终止，
      // 清理资格只能在各路径完成自己的状态转移（terminating 等）之后判定。
    };
    const terminate = (): void => {
      if (terminating || quiet) return;
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
      if (outBytes + chunk.length > stdoutBytes) { overflow(); return; }
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
  return { result, quiescent };
}

function runHelper(
  owner: Owner, file: string, args: string[], timeoutMs: number,
): Promise<{ stdout: string; stderr: string }> {
  return startHelper(owner, file, args, timeoutMs).result;
}

/** 受控协议里表示“当前是本地会话”的唯一方式（回执 kind=local）；进程退出码 2 不再有此含义。 */
class ClipboardLocalSession extends Error {
  override name = "ClipboardLocalSession";
}

/** 助手没有按 artifact-1 协议回应（过期的安装副本、自定义助手或损坏的输出）；绝不据内容猜测。 */
class ClipboardProtocolError extends Error {
  override name = "ClipboardProtocolError";
}

type Pasted = { kind: "text"; text: string } | { kind: "image"; path: string };

function parseReceipt(stdout: string): { kind: "text"; text: string } | { kind: "image"; name: string } | { kind: "local" } {
  const fail = (why: string): never => {
    throw new ClipboardProtocolError(
      `${ARTIFACT_ENDPOINT} ${why}；需要 ${ARTIFACT_PROTOCOL} 助手（过期的安装副本或旧的 PI_REMOTE_CLIPBOARD_DIR 请更新为本检出的 src/）`,
    );
  };
  const newline = stdout.indexOf("\n");
  let header: { protocol?: unknown; kind?: unknown; name?: unknown } | undefined;
  try {
    header = newline < 0 ? undefined : JSON.parse(stdout.slice(0, newline));
  } catch {
    header = undefined;
  }
  if (typeof header !== "object" || header === null || header.protocol !== ARTIFACT_PROTOCOL) {
    return fail("未返回回执");
  }
  const body = stdout.slice(newline + 1);
  if (header.kind === "text") {
    if (Buffer.byteLength(body, "utf8") > MAX_OUTPUT_BYTES) return fail(`文本超过 ${MAX_OUTPUT_BYTES} 字节`);
    return { kind: "text", text: body };
  }
  if (header.kind === "local" && body === "") return { kind: "local" };
  if (header.kind === "image" && typeof header.name === "string" && body === "") {
    return { kind: "image", name: header.name };
  }
  return fail("回执格式无效");
}

/** 暂存目录内必须是助手刚创建的、本用户独占的普通图片文件；名字取自固定集合，不含路径。 */
async function validateStagedImage(staging: string, name: string): Promise<{ source: string; ext: string }> {
  const match = /^image\.(png|jpg|webp|gif)$/.exec(name);
  if (!match) throw new ClipboardProtocolError(`${ARTIFACT_ENDPOINT} 回执的图片名无效: ${JSON.stringify(name)}`);
  const source = join(staging, name);
  let stat;
  try {
    stat = await lstat(source);
  } catch {
    throw new ClipboardProtocolError(`${ARTIFACT_ENDPOINT} 回执指向未创建的图片: ${name}`);
  }
  const owned = typeof process.getuid !== "function" || stat.uid === process.getuid();
  if (!stat.isFile() || stat.nlink !== 1 || stat.size === 0 || (stat.mode & 0o077) !== 0 || !owned) {
    throw new ClipboardProtocolError(`${ARTIFACT_ENDPOINT} 回执的图片不是私有的普通文件: ${name}`);
  }
  return { source, ext: match[1] };
}

const noop = (): void => undefined;

/**
 * 一次“对端剪贴板 → 编辑器”的制品操作，整体归 owner 所有（shutdown 会等它结算）：
 * 私有暂存目录（0700，由本进程创建，助手只往里写）→ 助手 → 回执 → 图片以唯一私有名硬链接到暂存目录之外
 * → 删除暂存目录 → `consume` 同步插入并调用 adopt()。
 * 只有 adopt() 之后交付文件才归编辑器/历史所有，永不再删除；在此之前的任何失败或取消都会删除它。
 * 删除的只有本操作创建的暂存目录与本操作链接出的文件，路径绝不取自剪贴板文本。
 */
function pasteViaHelper<T>(owner: Owner, consume: (pasted: Pasted, adopt: () => void) => T): Promise<T> {
  let staging: string | undefined;
  let delivered: string | undefined;
  let adopted = false;
  let quiescent: Promise<void> = Promise.resolve();
  const removeStaging = async (): Promise<void> => {
    if (staging === undefined) return;
    await rm(staging, { recursive: true, force: true }); // 所有权保留到 rm 成功（force 使 ENOENT 也算已消失）
    staging = undefined;
  };
  const body = (async (): Promise<T> => {
    assertLive(owner);
    // 准入：没有版本化入口的目录（旧安装副本、旧的自定义目录）在创建任何东西、运行任何程序之前被拒绝。
    // 只读检查；绝不对旧脚本执行 --version/--probe，它会无视参数并自行写图片。
    if (!(await stat(ARTIFACT_ENDPOINT).then((entry) => entry.isFile(), () => false))) {
      throw new ClipboardProtocolError(
        `${ARTIFACT_ENDPOINT} 不存在；需要 ${ARTIFACT_PROTOCOL} 助手（过期的安装副本或旧的 PI_REMOTE_CLIPBOARD_DIR 请更新为本检出的 src/）`,
      );
    }
    assertLive(owner);
    staging = await mkdtemp(join(tmpdir(), STAGING_PREFIX));
    assertLive(owner);
    const started = startHelper(
      owner, "python3", [ARTIFACT_ENDPOINT, staging], TIMEOUT_MS, MAX_OUTPUT_BYTES + RECEIPT_HEADER_BYTES,
    );
    quiescent = started.quiescent;
    let stdout: string;
    try {
      ({ stdout } = await started.result);
    } catch (error: unknown) {
      // 本协议里退出码 2/64/70 只可能是解释器、参数或契约失败（半更新的目录），不是“本地会话”。
      const code = (error as { code?: unknown }).code;
      if (code === 2 || code === 64 || code === 70) {
        const detail = ((error as { stderr?: string }).stderr ?? "").trim() || (error as Error).message;
        throw new ClipboardProtocolError(`${ARTIFACT_ENDPOINT} 拒绝执行（退出码 ${code}）：${detail}`);
      }
      throw error;
    }
    assertLive(owner);
    const receipt = parseReceipt(stdout);
    if (receipt.kind === "local") throw new ClipboardLocalSession("local session");
    let pasted: Pasted;
    if (receipt.kind === "text") {
      pasted = receipt;
    } else {
      const { source, ext } = await validateStagedImage(staging, receipt.name);
      assertLive(owner);
      const target = join(tmpdir(), `pi-remote-clipboard-${randomUUID()}.${ext}`);
      await link(source, target); // 原子且不覆盖
      delivered = target;
      pasted = { kind: "image", path: target };
    }
    await removeStaging();
    assertLive(owner);
    return consume(pasted, () => { adopted = true; });
  })();
  // 清理与业务结果分离（同 Helper 的 result / quiescent）：业务结果（含 8 秒超时）立即返回给调用方，
  // 清理在助手进程组消失后进行，由 owner.operations 持有，shutdown 会等待它。
  // 只删除本操作创建的暂存目录与本操作链接出的、尚未被编辑器接收的文件；路径绝不取自剪贴板文本。
  const cleanup = body.then(noop, noop).then(async () => {
    await quiescent; // 写入者消失后才删目录：不与删除竞争，也不会在删除后重建
    // 两项释放相互独立：暂存目录删除失败（含主体里已失败过一次）不得跳过未交付文件的删除。
    const release = async (what: string, run: () => Promise<void>): Promise<void> => {
      try {
        await run();
      } catch (error: unknown) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
        console.error(`pi-remote-clipboard: 制品清理失败（${what}）: ${error instanceof Error ? error.message : String(error)}`);
      }
    };
    await release("暂存目录", removeStaging);
    if (delivered !== undefined && !adopted) await release("未交付文件", () => unlink(delivered as string));
  });
  owner.operations.add(cleanup);
  void cleanup.then(() => owner.operations.delete(cleanup));
  return body;
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

// ctx.ui.pasteToEditor wraps its argument in ESC[200~ ... ESC[201~ and feeds the editor, whose parser ends the
// data frame at the first ESC[201~ and re-enters the rest as raw keystrokes (skipping the paste C0 filter).
// Remove the native framing sequences so the whole payload stays inside the frame. A single left-to-right stack
// reduction: every UTF-16 unit is pushed once and the six-unit marker popped at most once, and after a pop the
// stack is again a fully reduced prefix, so markers rebuilt from the pieces around a removed one are caught by
// the same suffix check. Linear in the input, unlike repeating whole-string replaces until stable.
export function stripPasteFraming(text: string): string {
  const stack: number[] = [];
  for (let i = 0; i < text.length; i += 1) {
    stack.push(text.charCodeAt(i));
    const n = stack.length;
    if (
      n >= 6 && stack[n - 1] === 0x7e && (stack[n - 2] === 0x30 || stack[n - 2] === 0x31) && stack[n - 3] === 0x30 &&
      stack[n - 4] === 0x32 && stack[n - 5] === 0x5b && stack[n - 6] === 0x1b
    ) stack.length = n - 6;
  }
  const chunks: string[] = [];
  for (let i = 0; i < stack.length; i += 8192) chunks.push(String.fromCharCode(...stack.slice(i, i + 8192)));
  return chunks.join("");
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
  let hooks: Hook[] = [];
  function releaseHooks(): void {
    const pending = hooks.map((hook) => ({ hook, callable: hook.record.callable }));
    hooks = []; // Release all handles even when a descriptor operation throws.
    for (const { hook } of pending) {
      hook.record.live = false;
      hook.record.callable = undefined;
      hook.record.previous = normalize(hook.record.previous);
    }
    let failed = false;
    let firstError: unknown;
    for (const { hook, callable } of pending) {
      try { uninstall(hook, callable); } catch (error) {
        if (!failed) { failed = true; firstError = error; }
      }
    }
    if (failed) throw firstError;
  }

  interface PasteHost {
    editor?: { insertTextAtCursor?: (text: string) => void };
    ui?: { requestRender?: () => void };
    showError?: (message: string) => void;
  }

  /** 读取对端剪贴板并插入当前编辑器；"local" 表示应回退原生路径。 */
  async function pasteFromBridge(owner: Owner, host: PasteHost): Promise<"inserted" | "empty" | "local" | "error"> {
    try {
      return await pasteViaHelper(owner, (pasted, adopt): "inserted" | "empty" => {
        if (pasted.kind === "text") {
          const text = pasted.text.trim();
          if (text.length === 0) return "empty";
          host.editor?.insertTextAtCursor?.(text);
        } else {
          const editor = host.editor;
          if (typeof editor?.insertTextAtCursor !== "function") throw new Error("编辑器不支持插入图片路径");
          editor.insertTextAtCursor(pasted.path);
          adopt(); // 已插入：此后文件属于编辑器/历史，渲染失败也不得删除
        }
        host.ui?.requestRender?.();
        return "inserted";
      });
    } catch (error: unknown) {
      if (isCancelled(error)) throw error;
      // 失败发生在 shutdown 之后：先于本地回退与任何反馈取消（拒绝可能在 shutdown 前后穿过 await 边界）。
      if (owner.dead) throw new ClipboardCancelled();
      if (error instanceof ClipboardLocalSession) return "local";
      // 真实失败（协议、I/O、超时）与 Pi 原生粘贴失败走同一可见通道，不退化成静默的“无内容”。
      host.showError?.(`Failed to paste from clipboard: ${error instanceof Error ? error.message : String(error)}`);
      return "error";
    }
  }

  const handleRemotePaste = async (ctx: ExtensionContext): Promise<void> => {
    const owner = current;
    try {
      await pasteViaHelper(owner, (pasted, adopt) => {
        if (pasted.kind === "text") {
          const text = stripPasteFraming(pasted.text);
          if (text.trim().length === 0) {
            ctx.ui.notify("对端剪贴板为空", "info");
            return;
          }
          ctx.ui.pasteToEditor(text);
        } else {
          ctx.ui.pasteToEditor(pasted.path);
          adopt();
        }
      });
    } catch (error: unknown) {
      if (isCancelled(error) || owner.dead) return;
      if (error instanceof ClipboardLocalSession) {
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
    releaseHooks();
    const owner = newOwner();
    current = owner;
    if (ctx.mode !== "tui") return;

    // 复制方向分流：SSH 客户端在观看时，文本复制改走 OSC 52（经 tmux 到对端剪贴板）。
    // 本地会话完全走原生路径，一行行为不变。
    const unavailable: string[] = [];
    const altProto = PiTui.TuiAltScreen?.prototype;
    const modeProto = PiAgent.InteractiveMode?.prototype;
    try {
      const copy = installHook(altProto, "copyTextToClipboard", true, (record) =>
        async function (this: { flash?: (msg: string) => void }, ...args: [string, ...unknown[]]): Promise<boolean> {
          try {
            if (await copyToClientIfRemote(owner, args[0])) {
              if (owner.dead) return true; // 写入已发生；会话已结束，不再提示
              try { this.flash?.("已复制到对端剪贴板"); } catch { /* 提示失败不改变复制结果 */ }
              return true;
            }
          } catch (error: unknown) {
            if (isCancelled(error)) return false;
            throw error;
          }
          if (owner.dead) return false;
          return fallbackBase(record).apply(this, args);
        });
      if (copy) hooks.push(copy);
      else unavailable.push("TuiAltScreen.copyTextToClipboard 不可用，未接管文本复制");

      // 本地会话和桥的 local 回执均跳过所有本扩展层，原样调用不透明的原生/外来入口。
      const paste = installHook(modeProto, "handleClipboardPaste", true, (record) =>
        async function (this: PasteHost, ...args: unknown[]): Promise<void> {
          try {
            if (!(await isRemoteClient(owner))) {
              assertLive(owner);
              return fallbackBase(record).apply(this, args);
            }
            const outcome = await pasteFromBridge(owner, this);
            if (outcome === "local") {
              assertLive(owner);
              return fallbackBase(record).apply(this, args);
            }
          } catch (error: unknown) {
            if (isCancelled(error)) return;
            throw error;
          }
        });
      if (paste) hooks.push(paste);
      else unavailable.push("InteractiveMode.handleClipboardPaste 不可用，Ctrl+V 未通过桥接管");

      // Sink 保持来源 owner 绑定：活跃本地返回 false，退役后拒绝 AbortError。
      const sink = installHook(globalThis, COPY_SINK_KEY, false, () =>
        (text: string) => copyToClientIfRemote(owner, text));
      if (sink) hooks.push(sink);
      else unavailable.push("复制 sink 不可写，未接管复制分流");
    } catch (error) {
      void retire(owner);
      try { releaseHooks(); } catch (cleanupError) {
        console.error("pi-remote-clipboard: failed hook rollback", cleanupError);
      }
      throw error;
    }
    if (unavailable.length > 0) {
      ctx.ui.notify(`pi-remote-clipboard: ${unavailable.join("；")}；Ctrl+Alt+V 和 /rpush 仍可用`, "warning");
    }
  });

  pi.on("session_shutdown", async () => {
    // 先标记死亡并取消全部进行中的操作，再做可能抛错的钩子恢复；
    // 无论恢复是否抛错，都等待所有 owner（含已超时、已被替换的）的子进程组清理完毕，
    // 因为 Pi 在 shutdown 返回后即退出进程，会销毁尚未触发的 KILL 定时器。
    retire(current);
    try {
      releaseHooks();
    } finally {
      await Promise.all([...retiring]);
    }
  });
}
