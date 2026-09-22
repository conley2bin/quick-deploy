import { spawn, type ChildProcess } from "node:child_process";

const QUERY_TIMEOUT_MS = 2_500;
const QUERY_KILL_GRACE_MS = 200;
const MAX_BUFFER = 4_096;
const DESKTOP_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*\.desktop$/u;

export interface OpenLifetime {
  readonly signal: AbortSignal;
  isCurrent(): boolean;
}

export interface SpawnRequest {
  readonly command: string;
  readonly args: readonly string[];
  readonly env?: NodeJS.ProcessEnv;
}

export type SpawnRunner = (
  command: string,
  args: readonly string[],
  options: {
    stdio: "ignore";
    shell: false;
    detached: true;
    env?: NodeJS.ProcessEnv;
  },
) => ChildProcess;

export interface BoundedCommandOptions {
  readonly signal: AbortSignal;
  readonly timeoutMs: number;
  readonly killGraceMs: number;
  readonly maxBuffer: number;
}

export interface CommandResult {
  readonly stdout: string;
  readonly stderr: string;
}

export type QueryRunner = (
  command: string,
  args: readonly string[],
  options: BoundedCommandOptions,
) => Promise<CommandResult>;

export type QuerySpawnRunner = (
  command: string,
  args: readonly string[],
  options: {
    stdio: ["ignore", "pipe", "pipe"];
    shell: false;
    detached: true;
  },
) => ChildProcess;

export interface BrowserDependencies {
  readonly platform?: NodeJS.Platform;
  readonly query?: QueryRunner;
  readonly spawn?: SpawnRunner;
}

/** Model-supplied URLs are data, never shell commands or executable URI schemes. */
export function webUrl(value: string): string | undefined {
  if (/[\x00-\x20\x7f-\x9f]/u.test(value)) return undefined;
  try {
    const parsed = new URL(value);
    return /^https?:\/\//iu.test(value) && parsed.hostname &&
      (parsed.protocol === "http:" || parsed.protocol === "https:") ? parsed.href : undefined;
  } catch { return undefined; }
}

function ensureCurrent(lifetime: OpenLifetime): void {
  if (lifetime.signal.aborted || !lifetime.isCurrent()) throw new Error("打开动作已失效");
}

function oneLine(value: string, label: string): string {
  if (!value || value.includes("\n") || value.includes("\r")) {
    throw new Error(`${label} 返回了无效结果`);
  }
  return value;
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}

function signalOwnedProcess(child: ChildProcess, signal: NodeJS.Signals): void {
  if (process.platform !== "win32" && typeof child.pid === "number" && child.pid > 0) {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ESRCH") return;
    }
  }
  try { child.kill(signal); }
  catch { /* Best-effort fallback for a process that already exited. */ }
}

/** Terminate one still-extension-owned detached process group. */
function terminateOwnedProcess(child: ChildProcess, killGraceMs: number): void {
  signalOwnedProcess(child, "SIGTERM");
  if (killGraceMs <= 0) {
    signalOwnedProcess(child, "SIGKILL");
    return;
  }

  let timer: ReturnType<typeof setTimeout> | undefined;
  const finish = () => {
    if (timer) clearTimeout(timer);
    child.removeListener("exit", onExit);
    // The group may contain descendants even after its leader exits.
    signalOwnedProcess(child, "SIGKILL");
  };
  const onExit = () => finish();
  child.once("exit", onExit);
  timer = setTimeout(finish, killGraceMs);
}

/**
 * Launch one GUI process without a shell. Ownership transfers only after every
 * listener is installed and unref succeeds. Abort after that handoff merely
 * detaches observers; it never kills the accepted application.
 */
export async function launchApplication(
  request: SpawnRequest,
  lifetime: OpenLifetime,
  run: SpawnRunner = spawn,
): Promise<void> {
  ensureCurrent(lifetime);
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    let child: ChildProcess | undefined;
    const detach = () => {
      if (child) {
        try { child.removeListener("error", onError); } catch { /* attempt remaining cleanup */ }
        try { child.removeListener("exit", onExit); } catch { /* attempt remaining cleanup */ }
      }
      try { lifetime.signal.removeEventListener("abort", onAbort); } catch { /* best effort */ }
    };
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      detach();
      if (error) reject(error);
      else resolve();
    };
    const failHandoff = (error: unknown) => {
      if (settled) return;
      settled = true;
      detach();
      if (child) terminateOwnedProcess(child, 0);
      reject(asError(error));
    };
    const onError = (error: Error) => {
      if (lifetime.isCurrent() && !lifetime.signal.aborted) finish(new Error(`${request.command}: ${error.message}`));
      else finish();
    };
    const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
      if (code === 0) finish();
      else if (lifetime.isCurrent() && !lifetime.signal.aborted) {
        finish(new Error(`${request.command} ${signal ? `被 ${signal} 中止` : `退出码 ${String(code)}`}`));
      } else finish();
    };
    const onAbort = () => finish();
    try {
      child = run(request.command, [...request.args], {
        stdio: "ignore", shell: false, detached: true, ...(request.env ? { env: request.env } : {}),
      });
      child.once("error", onError);
      child.once("exit", onExit);
      lifetime.signal.addEventListener("abort", onAbort, { once: true });
      child.unref();
      if (lifetime.signal.aborted) onAbort();
    } catch (error) {
      if (child) failHandoff(error);
      else reject(asError(error));
    }
  });
}

/**
 * Collect one extension-owned subprocess with a real wall-clock deadline.
 * Timeout/abort/output overflow settle independently of child cooperation, then
 * TERM and bounded KILL the detached process group.
 */
export async function runBoundedCommand(
  command: string,
  args: readonly string[],
  options: BoundedCommandOptions,
  run: QuerySpawnRunner = spawn,
): Promise<CommandResult> {
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0) throw new RangeError("timeoutMs must be positive");
  if (!Number.isFinite(options.killGraceMs) || options.killGraceMs < 0) throw new RangeError("killGraceMs must be non-negative");
  if (!Number.isSafeInteger(options.maxBuffer) || options.maxBuffer <= 0) throw new RangeError("maxBuffer must be positive");
  if (options.signal.aborted) throw new Error("subprocess aborted");

  return new Promise<CommandResult>((resolve, reject) => {
    let settled = false;
    let child: ChildProcess | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let stdoutDone = false;
    let stderrDone = false;
    let childClosed = false;
    let leaderStatus: { code: number | null; signal: NodeJS.Signals | null } | undefined;

    const detach = () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
      if (child) {
        try { child.removeListener("error", onError); } catch { /* attempt remaining cleanup */ }
        try { child.removeListener("exit", onExit); } catch { /* attempt remaining cleanup */ }
        try { child.removeListener("close", onChildClose); } catch { /* attempt remaining cleanup */ }
        try { child.stdout?.removeListener("data", onStdout); } catch { /* attempt remaining cleanup */ }
        try { child.stdout?.removeListener("end", onStdoutDone); } catch { /* attempt remaining cleanup */ }
        try { child.stdout?.removeListener("close", onStdoutDone); } catch { /* attempt remaining cleanup */ }
        try { child.stdout?.removeListener("error", onStdoutError); } catch { /* attempt remaining cleanup */ }
        try { child.stderr?.removeListener("data", onStderr); } catch { /* attempt remaining cleanup */ }
        try { child.stderr?.removeListener("end", onStderrDone); } catch { /* attempt remaining cleanup */ }
        try { child.stderr?.removeListener("close", onStderrDone); } catch { /* attempt remaining cleanup */ }
        try { child.stderr?.removeListener("error", onStderrError); } catch { /* attempt remaining cleanup */ }
      }
      try { options.signal.removeEventListener("abort", onAbort); } catch { /* best effort */ }
    };
    const destroyPipes = () => {
      try { child?.stdout?.destroy(); } catch { /* best effort */ }
      try { child?.stderr?.destroy(); } catch { /* best effort */ }
    };
    const settle = (error?: Error, terminate = false) => {
      if (settled) return;
      settled = true;
      detach();
      if (terminate) destroyPipes();
      if (terminate && child) terminateOwnedProcess(child, options.killGraceMs);
      if (error) reject(error);
      else resolve({ stdout: Buffer.concat(stdout).toString("utf8"), stderr: Buffer.concat(stderr).toString("utf8") });
    };
    const append = (chunks: Buffer[], stream: "stdout" | "stderr", chunk: Buffer | string) => {
      if (settled) return;
      const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      if (stream === "stdout") stdoutBytes += buffer.length;
      else stderrBytes += buffer.length;
      if ((stream === "stdout" ? stdoutBytes : stderrBytes) > options.maxBuffer) {
        settle(new Error(`${command} ${stream} exceeded ${options.maxBuffer} bytes`), true);
        return;
      }
      chunks.push(buffer);
    };
    const recordLeaderStatus = (code: number | null, signal: NodeJS.Signals | null) => {
      if (!leaderStatus || (leaderStatus.code === null && leaderStatus.signal === null &&
          (code !== null || signal !== null))) {
        leaderStatus = { code, signal };
      }
    };
    const completeNormally = () => {
      if (settled || !leaderStatus || (leaderStatus.code === null && leaderStatus.signal === null) ||
          (!childClosed && !(stdoutDone && stderrDone))) return;
      const { code, signal } = leaderStatus;
      if (code === 0) settle();
      else settle(new Error(`${command} ${signal ? `被 ${signal} 中止` : `退出码 ${String(code)}`}`));
    };
    const onStdout = (chunk: Buffer | string) => append(stdout, "stdout", chunk);
    const onStderr = (chunk: Buffer | string) => append(stderr, "stderr", chunk);
    const onStdoutDone = () => { stdoutDone = true; completeNormally(); };
    const onStderrDone = () => { stderrDone = true; completeNormally(); };
    const onStdoutError = (error: Error) => settle(new Error(`${command} stdout: ${error.message}`), true);
    const onStderrError = (error: Error) => settle(new Error(`${command} stderr: ${error.message}`), true);
    const onError = (error: Error) => settle(new Error(`${command}: ${error.message}`), true);
    const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
      recordLeaderStatus(code, signal);
      completeNormally();
    };
    const onChildClose = (code: number | null, signal: NodeJS.Signals | null) => {
      recordLeaderStatus(code, signal);
      childClosed = true;
      completeNormally();
    };
    const onAbort = () => settle(new Error(`${command} aborted`), true);
    const onTimeout = () => settle(new Error(`${command} timed out after ${options.timeoutMs} ms`), true);

    try {
      child = run(command, [...args], { stdio: ["ignore", "pipe", "pipe"], shell: false, detached: true });
      if (!child.stdout || !child.stderr) throw new Error(`${command} did not provide output pipes`);
      child.stdout.on("data", onStdout);
      child.stdout.once("end", onStdoutDone);
      child.stdout.once("close", onStdoutDone);
      child.stdout.once("error", onStdoutError);
      child.stderr.on("data", onStderr);
      child.stderr.once("end", onStderrDone);
      child.stderr.once("close", onStderrDone);
      child.stderr.once("error", onStderrError);
      child.once("error", onError);
      child.once("exit", onExit);
      child.once("close", onChildClose);
      options.signal.addEventListener("abort", onAbort, { once: true });
      timer = setTimeout(onTimeout, options.timeoutMs);
      if (options.signal.aborted) onAbort();
    } catch (error) {
      settle(asError(error), child !== undefined);
    }
  });
}

async function queryDefaultBrowser(
  lifetime: OpenLifetime,
  run: QueryRunner,
): Promise<string> {
  ensureCurrent(lifetime);
  const options: BoundedCommandOptions = {
    signal: lifetime.signal,
    timeoutMs: QUERY_TIMEOUT_MS,
    killGraceMs: QUERY_KILL_GRACE_MS,
    maxBuffer: MAX_BUFFER,
  };
  let result: CommandResult;
  try { result = await run("xdg-settings", ["get", "default-web-browser"], options); }
  catch (error) {
    if (lifetime.signal.aborted || !lifetime.isCurrent()) throw new Error("打开动作已失效");
    throw new Error(`无法查询默认浏览器：${asError(error).message}`);
  }
  const desktopId = oneLine(result.stdout.trim(), "xdg-settings");
  if (!DESKTOP_ID.test(desktopId)) throw new Error(`默认浏览器标识无效：${desktopId}`);
  if (desktopId !== "google-chrome.desktop") {
    throw new Error(`不支持默认浏览器 ${desktopId}；当前仅支持 google-chrome.desktop`);
  }
  ensureCurrent(lifetime);
  try {
    result = await run("xdg-settings", ["check", "default-web-browser", desktopId], options);
  } catch (error) {
    if (lifetime.signal.aborted || !lifetime.isCurrent()) throw new Error("打开动作已失效");
    throw new Error(`无法核对默认浏览器：${asError(error).message}`);
  }
  if (oneLine(result.stdout.trim(), "xdg-settings check") !== "yes") {
    throw new Error(`默认浏览器关联不一致：${desktopId}`);
  }
  return desktopId;
}

export async function openWebUrl(
  url: string,
  lifetime: OpenLifetime,
  dependencies: BrowserDependencies = {},
): Promise<void> {
  const target = webUrl(url);
  if (!target) throw new Error("只支持有效的 HTTP(S) 链接");
  if ((dependencies.platform ?? process.platform) !== "linux") {
    throw new Error("当前版本仅支持 Linux 显式新窗口启动");
  }
  await queryDefaultBrowser(lifetime, dependencies.query ?? runBoundedCommand);
  ensureCurrent(lifetime);
  await launchApplication(
    { command: "google-chrome-stable", args: ["--new-window", target] },
    lifetime,
    dependencies.spawn,
  );
}
