import { spawn, type ChildProcess } from "node:child_process";
import { isIP } from "node:net";
import type { OpenOrigin } from "./adapter.ts";
import { webUrl, type OpenLifetime } from "./browser.ts";
import { openTarget, parseOpenTarget, validateLocalTarget, type OpenTargetDependencies } from "./links.ts";
import { resolveInputReceiver, type InputReceiver } from "./receiver.ts";

const REMOTE_ALIAS = "conley-Y9000p";
const HELPER = "~/.local/bin/pi-client-link-helper";
const MAX_REQUEST = 65_536;
const MAX_REPLY = 65_536;
const MAX_STDERR = 4_096;
const HELPER_DEADLINE_MS = 60_000;
const KILL_GRACE_MS = 200;

type WebRequest = Readonly<{ version: 1; action: "open-web"; url: string }>;
type FileRequest = Readonly<{
  version: 1; action: "open-file"; kind: "file" | "directory";
  path: string; line?: number; column?: number;
}>;
export type HelperRequest = WebRequest | FileRequest;
type RemoteReceiver = Extract<InputReceiver, { kind: "remote" }>;

export type HelperSpawn = (
  command: string, args: readonly string[], options: {
    stdio: ["pipe", "pipe", "pipe"]; shell: false; detached: true;
  },
) => ChildProcess;

export interface DispatcherDependencies {
  readonly env?: NodeJS.ProcessEnv;
  readonly resolve?: (origin: OpenOrigin) => Promise<InputReceiver>;
  readonly local?: (href: string, cwd: string, lifetime: OpenLifetime) => Promise<void>;
  readonly stat?: OpenTargetDependencies["stat"];
  readonly spawn?: HelperSpawn;
  readonly timeoutMs?: number;
}

function ensureCurrent(lifetime: OpenLifetime): void {
  if (lifetime.signal.aborted || !lifetime.isCurrent()) throw new Error("打开动作已失效");
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function validPort(value: unknown): value is number {
  return Number.isInteger(value) && typeof value === "number" && value >= 1 && value <= 65_535;
}

function loopbackBindHost(url: URL): string | undefined {
  const host = url.hostname.replace(/^\[|\]$/gu, "").toLowerCase().replace(/\.$/u, "");
  if (host === "localhost") return "127.0.0.1";
  if (isIP(host) === 4 && host.startsWith("127.")) return host;
  if (host === "::1") return host;
  const mapped = /^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/u.exec(host);
  if (mapped) {
    const high = Number.parseInt(mapped[1]!, 16), low = Number.parseInt(mapped[2]!, 16);
    if (high >>> 8 === 127) return `127.${high & 255}.${low >>> 8}.${low & 255}`;
  }
  return undefined;
}

function sshArguments(receiver: RemoteReceiver): string[] {
  if (typeof receiver.peer !== "string" || !isIP(receiver.peer) ||
      !validPort(receiver.port) || receiver.user !== "conley") {
    throw new Error("已验证的 SSH 客户端地址无效");
  }
  // Command-line HostName/User/Port pin the verified peer even if the alias's
  // ssh_config changes between `ssh -G` and this invocation. Other alias
  // settings (notably the authentication key) remain available.
  return ["-T", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes",
    "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
    "-o", `HostName=${receiver.peer}`, "-o", `Port=${receiver.port}`,
    "-o", `User=${receiver.user}`, REMOTE_ALIAS, HELPER];
}

function signalGroup(child: ChildProcess, signal: NodeJS.Signals): void {
  if (typeof child.pid === "number" && child.pid > 0 && process.platform !== "win32") {
    try { process.kill(-child.pid, signal); return; }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ESRCH") return; }
  }
  try { child.kill(signal); } catch { /* already gone */ }
}

function stop(child: ChildProcess): void {
  signalGroup(child, "SIGTERM");
  // A leader may exit while a descendant still holds the reply pipe.
  const timer = setTimeout(() => signalGroup(child, "SIGKILL"), KILL_GRACE_MS);
  timer.unref();
}

/** One request on stdin, one bounded reply; no target ever enters SSH argv or a shell command. */
export async function runClientHelper(
  receiver: RemoteReceiver, request: HelperRequest, lifetime: OpenLifetime,
  run: HelperSpawn = spawn, timeoutMs = HELPER_DEADLINE_MS,
): Promise<void> {
  ensureCurrent(lifetime);
  const args = sshArguments(receiver);
  const input = Buffer.from(JSON.stringify(request) + "\n", "utf8");
  if (input.length > MAX_REQUEST) throw new Error("客户端助手请求超过 64 KiB");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error("客户端助手超时设置无效");
  const result = await new Promise<{ code: number | null; stdout: Buffer; stderr: Buffer }>((resolve, reject) => {
    let child: ChildProcess | undefined;
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const out: Buffer[] = [], err: Buffer[] = [];
    let outBytes = 0, errBytes = 0;
    const detach = () => {
      if (timer) clearTimeout(timer);
      lifetime.signal.removeEventListener("abort", onAbort);
      child?.removeListener("error", onOutputError);
      child?.removeListener("close", onClose);
      child?.stdin?.removeListener("error", onInputError);
      child?.stdout?.removeListener("data", onOut);
      child?.stderr?.removeListener("data", onErr);
      child?.stdout?.removeListener("error", onOutputError);
      child?.stderr?.removeListener("error", onOutputError);
    };
    const fail = (error: Error) => {
      if (settled) return;
      settled = true;
      detach();
      child?.stdout?.destroy(); child?.stderr?.destroy(); child?.stdin?.destroy();
      if (child) stop(child);
      reject(error);
    };
    const append = (chunks: Buffer[], chunk: Buffer | string, stdout: boolean) => {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      if (stdout) outBytes += bytes.length;
      else errBytes += bytes.length;
      if ((stdout ? outBytes > MAX_REPLY : errBytes > MAX_STDERR)) {
        fail(new Error(`客户端助手${stdout ? "回复" : "诊断"}超过大小限制`));
      } else chunks.push(bytes);
    };
    const onOut = (chunk: Buffer | string) => append(out, chunk, true);
    const onErr = (chunk: Buffer | string) => append(err, chunk, false);
    const onInputError = (error: Error) => fail(new Error(`无法向客户端助手发送请求：${error.message}`));
    const onOutputError = (error: Error) => fail(new Error(`无法读取客户端助手回复：${error.message}`));
    const onAbort = () => fail(new Error("打开动作已失效"));
    const onClose = (code: number | null) => {
      if (settled) return;
      settled = true;
      detach();
      resolve({ code, stdout: Buffer.concat(out), stderr: Buffer.concat(err) });
    };
    try {
      child = run("ssh", args, { stdio: ["pipe", "pipe", "pipe"], shell: false, detached: true });
      if (!child.stdin || !child.stdout || !child.stderr) throw new Error("SSH 未提供完整的 stdin/stdout/stderr");
      child.once("error", onOutputError);
      child.once("close", onClose);
      child.stdin.once("error", onInputError);
      child.stdout.on("data", onOut);
      child.stderr.on("data", onErr);
      child.stdout.once("error", onOutputError);
      child.stderr.once("error", onOutputError);
      lifetime.signal.addEventListener("abort", onAbort, { once: true });
      timer = setTimeout(() => fail(new Error(`客户端助手 SSH 请求超时（${timeoutMs} ms）`)), timeoutMs);
      if (lifetime.signal.aborted || !lifetime.isCurrent()) onAbort();
      else child.stdin.end(input);
    } catch (error) {
      fail(new Error(`无法启动客户端 SSH：${error instanceof Error ? error.message : String(error)}`));
    }
  });
  ensureCurrent(lifetime);
  verifyReply(result, request);
  ensureCurrent(lifetime);
}

function verifyReply(result: { code: number | null; stdout: Buffer; stderr: Buffer }, request: HelperRequest): void {
  let reply: unknown;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(result.stdout);
    if (!text.endsWith("\n") || text.indexOf("\n") !== text.length - 1 || text.includes("\r")) {
      throw new Error("expected exactly one newline-terminated JSON reply");
    }
    reply = JSON.parse(text.slice(0, -1));
  } catch {
    if (result.code === 255) throw new Error("客户端 SSH 连接或认证失败（退出码 255）");
    if (result.code === 127) throw new Error("Y9000p 客户端助手未安装或不可执行（退出码 127）");
    if (result.code !== 0) throw new Error(`客户端 SSH/助手退出码 ${String(result.code)}，且没有有效 JSON 回复`);
    throw new Error("客户端助手返回无效 JSON 协议回复");
  }
  if (!record(reply)) throw new Error("客户端助手返回无效 JSON 协议回复");
  if (result.code !== 0) {
    if (exactKeys(reply, ["ok", "error"]) && reply.ok === false && record(reply.error) &&
        exactKeys(reply.error, ["code", "message"]) &&
        typeof reply.error.code === "string" && /^[a-z][a-z0-9_]{0,63}$/u.test(reply.error.code) &&
        typeof reply.error.message === "string" && reply.error.message.length <= 1024 &&
        !/[\x00-\x1f\x7f]/u.test(reply.error.message)) {
      throw new Error(`Y9000p 助手 ${reply.error.code}：${reply.error.message}`);
    }
    throw new Error("客户端助手返回无效 JSON 协议回复（退出状态与错误结构不匹配）");
  }
  if (reply.ok !== true || reply.action !== request.action) {
    throw new Error("客户端助手返回无效 JSON 协议回复（操作不匹配）");
  }
  if (request.action === "open-file") {
    if (!exactKeys(reply, ["ok", "action", "kind", "path"]) ||
        reply.kind !== request.kind || reply.path !== request.path) {
      throw new Error("客户端助手返回无效 JSON 协议回复（文件目标不匹配）");
    }
    return;
  }
  if (!exactKeys(reply, ["ok", "action", "openedUrl", "tunnel"]) ||
      typeof reply.openedUrl !== "string" || !webUrl(reply.openedUrl)) {
    throw new Error("客户端助手返回无效 JSON 协议回复（网页目标不匹配）");
  }
  const original = new URL(request.url), opened = new URL(reply.openedUrl);
  const bindHost = loopbackBindHost(original);
  if (reply.tunnel === null) {
    if (bindHost) throw new Error("客户端助手返回无效 JSON 协议回复（缺少必需的公司回环隧道）");
    if (reply.openedUrl !== request.url) throw new Error("客户端助手返回无效 JSON 协议回复（网页目标不匹配）");
    return;
  }
  const tunnel = reply.tunnel;
  if (!record(tunnel) || !exactKeys(tunnel, ["remoteHost", "remotePort", "localPort", "reused"]) ||
      typeof tunnel.remoteHost !== "string" || !isIP(tunnel.remoteHost) ||
      !validPort(tunnel.remotePort) || !validPort(tunnel.localPort) || typeof tunnel.reused !== "boolean") {
    throw new Error("客户端助手返回无效 JSON 协议回复（隧道不匹配）");
  }
  const port = (url: URL) => Number(url.port || (url.protocol === "https:" ? 443 : 80));
  if (!bindHost || tunnel.remoteHost !== bindHost ||
      original.protocol !== opened.protocol || original.pathname !== opened.pathname ||
      original.search !== opened.search || original.hash !== opened.hash ||
      original.username !== opened.username || original.password !== opened.password ||
      port(original) !== tunnel.remotePort || port(opened) !== tunnel.localPort ||
      opened.hostname.replace(/^\[|\]$/gu, "") !== tunnel.remoteHost) {
    throw new Error("客户端助手返回无效 JSON 协议回复（网页/隧道目标不匹配）");
  }
}

/** Preserve local Pi behavior; a direct SSH session has no attributable tmux client. */
export async function dispatchOpenTarget(
  href: string, cwd: string, lifetime: OpenLifetime, origin: OpenOrigin,
  dependencies: DispatcherDependencies = {},
): Promise<void> {
  ensureCurrent(lifetime);
  const env = dependencies.env ?? process.env;
  if (!env.TMUX) {
    if (origin !== "unknown" || env.SSH_CONNECTION || env.SSH_CLIENT || env.SSH_TTY) {
      throw new Error("SSH 输入未经过带来源标记的 tmux，无法确定打开链接的客户端");
    }
    await (dependencies.local ?? openTarget)(href, cwd, lifetime);
    return;
  }
  const receiver = await (dependencies.resolve ?? resolveInputReceiver)(origin);
  ensureCurrent(lifetime);
  if (receiver === "local") {
    await (dependencies.local ?? openTarget)(href, cwd, lifetime);
    return;
  }
  if (!receiver || receiver.kind !== "remote") throw new Error("无法确认打开链接的客户端");
  const target = parseOpenTarget(href, cwd);
  let request: HelperRequest;
  if (target.kind === "web") request = { version: 1, action: "open-web", url: target.url };
  else {
    const kind = await validateLocalTarget(target, lifetime, dependencies.stat);
    request = { version: 1, action: "open-file", kind, path: target.path,
      ...(target.line === undefined ? {} : { line: target.line }),
      ...(target.column === undefined ? {} : { column: target.column }) };
  }
  ensureCurrent(lifetime);
  await runClientHelper(receiver, request, lifetime, dependencies.spawn, dependencies.timeoutMs);
}
