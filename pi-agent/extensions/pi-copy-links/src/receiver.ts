import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { isIP } from "node:net";
import { promisify } from "node:util";
import type { OpenOrigin } from "./adapter.ts";

const exec = promisify(execFile);
const CLIENT_FORMAT = "pid=#{client_pid}|tty=#{client_tty}|session=#{session_id}|flags=#{client_flags}";
const VIEW_FORMAT = "pid=#{client_pid}|tty=#{client_tty}|session=#{session_id}|pane=#{pane_id}";
const REMOTE_ALIAS = "conley-Y9000p";
const REMOTE_USER = "conley";

export type InputReceiver = "local" | "conley-Y9000p";
export type ReceiverErrorCode =
  | "invalid-origin" | "missing-tmux" | "command-failed" | "client-not-found"
  | "ambiguous-client" | "client-mismatch" | "pane-mismatch"
  | "proc-unavailable" | "proc-incomplete" | "proc-changed"
  | "ssh-env-invalid" | "ssh-alias-invalid" | "ssh-peer-mismatch";

/** The caller must not turn a failure into a local browser/Code launch. */
export class ReceiverResolutionError extends Error {
  constructor(readonly code: ReceiverErrorCode, message: string) {
    super(message);
    this.name = "ReceiverResolutionError";
  }
}

export interface ReceiverRunResult { code: number; stdout: string; stderr: string }
export interface ReceiverDependencies {
  env?: NodeJS.ProcessEnv;
  run?: (program: string, args: readonly string[]) => Promise<ReceiverRunResult>;
  readProc?: (path: string) => Promise<Buffer>;
}

const runDefault: NonNullable<ReceiverDependencies["run"]> = async (program, args) => {
  const { stdout, stderr } = await exec(program, [...args], {
    encoding: "utf8", timeout: 2500, maxBuffer: 1024 * 1024,
  });
  return { code: 0, stdout, stderr };
};

function reject(code: ReceiverErrorCode, message: string): never {
  throw new ReceiverResolutionError(code, message);
}

async function command(
  run: NonNullable<ReceiverDependencies["run"]>, program: string, args: string[], label: string,
): Promise<string> {
  // Neither stderr nor stdout may contain a value safe to show to the user.
  let result: ReceiverRunResult;
  try { result = await run(program, args); }
  catch { return reject("command-failed", `${label} failed`); }
  if (result.code !== 0 || typeof result.stdout !== "string") reject("command-failed", `${label} failed`);
  return result.stdout;
}

interface Client { pid: number; tty: string; session: string; flags: string }
interface View { pid: number; tty: string; session: string; pane: string }

function positivePid(raw: string): number | undefined {
  if (!/^[1-9]\d*$/u.test(raw)) return undefined;
  const pid = Number(raw);
  return Number.isSafeInteger(pid) ? pid : undefined;
}

function parseClients(output: string): Client[] {
  if (!output.trim()) return [];
  return output.trimEnd().split("\n").map((line) => {
    const match = /^pid=([1-9]\d*)\|tty=(\/dev\/[\w/-]+)\|session=(\$\d+)\|flags=([^|\r\n]*)$/u.exec(line);
    const pid = match ? positivePid(match[1]!) : undefined;
    if (!match || pid === undefined) reject("client-mismatch", "tmux client inventory is malformed");
    return { pid, tty: match[2]!, session: match[3]!, flags: match[4]! };
  });
}

function attachedClient(output: string, origin: number): Client {
  const matches = parseClients(output).filter((client) => client.pid === origin);
  if (matches.length === 0) reject("client-not-found", "origin PID is not an attached tmux client");
  if (matches.length !== 1) reject("ambiguous-client", "origin PID matches multiple tmux clients");
  const client = matches[0]!;
  if (!client.flags.split(",").includes("attached")) reject("client-mismatch", "origin tmux client is not attached");
  return client;
}

function verifyView(output: string, client: Client, pane: string): void {
  const match = /^pid=([1-9]\d*)\|tty=(\/dev\/[\w/-]+)\|session=(\$\d+)\|pane=(%\d+)\n?$/u.exec(output);
  const view: View | undefined = match && positivePid(match[1]!) !== undefined ? {
    pid: Number(match[1]), tty: match[2]!, session: match[3]!, pane: match[4]!,
  } : undefined;
  if (!view || view.pid !== client.pid || view.tty !== client.tty || view.session !== client.session) {
    reject("client-mismatch", "tmux client identity changed during pane lookup");
  }
  if (view.pane !== pane) reject("pane-mismatch", "origin client is not viewing the source pane");
}

interface ProcStat { pid: number; ppid: number; comm: string; starttime: string }

/** /proc stat fields 4 and 22 follow the final ')' ending field 2. */
function parseStat(pid: number, bytes: Buffer): ProcStat {
  const line = bytes.toString("utf8").trimEnd();
  const open = line.indexOf(" (");
  const close = line.lastIndexOf(")");
  if (open < 1 || close < open + 2 || positivePid(line.slice(0, open)) !== pid) {
    reject("proc-incomplete", `process ${pid} has malformed stat identity`);
  }
  const comm = line.slice(open + 2, close);
  const fields = line.slice(close + 1).trim().split(/\s+/u);
  const ppidRaw = fields[1]; // fields[0] is the process state (stat field 3).
  const starttime = fields[19];
  if (!comm || fields.length < 50 || !/^[A-Za-z]$/u.test(fields[0] ?? "") ||
      ["Z", "X", "x"].includes(fields[0]!) || !/^(0|[1-9]\d*)$/u.test(ppidRaw ?? "") ||
      !/^[1-9]\d*$/u.test(starttime ?? "") || !Number.isSafeInteger(Number(ppidRaw))) {
    reject("proc-incomplete", `process ${pid} has incomplete stat fields`);
  }
  return { pid, ppid: Number(ppidRaw), comm, starttime: starttime! };
}

async function stat(pid: number, readProc: NonNullable<ReceiverDependencies["readProc"]>): Promise<ProcStat> {
  let bytes: Buffer;
  try { bytes = await readProc(`/proc/${pid}/stat`); }
  catch { return reject("proc-unavailable", `cannot read process ${pid} stat`); }
  if (!Buffer.isBuffer(bytes)) reject("proc-incomplete", `process ${pid} stat is not a buffer`);
  return parseStat(pid, bytes);
}

async function ancestry(pid: number, readProc: NonNullable<ReceiverDependencies["readProc"]>): Promise<ProcStat[]> {
  const chain: ProcStat[] = [], visited = new Set<number>();
  let current = pid;
  while (chain.length < 256 && !visited.has(current)) {
    visited.add(current);
    const entry = await stat(current, readProc);
    chain.push(entry);
    if (current === 1) {
      if (entry.ppid !== 0) reject("proc-incomplete", "PID 1 has an invalid parent");
      return chain;
    }
    if (entry.ppid === 0) reject("proc-incomplete", "process ancestry ended before PID 1");
    current = entry.ppid;
  }
  return reject("proc-incomplete", "process ancestry is cyclic or exceeds the inspection limit");
}

function sameStat(a: ProcStat, b: ProcStat): boolean {
  return a.pid === b.pid && a.ppid === b.ppid && a.comm === b.comm && a.starttime === b.starttime;
}

function sameAncestry(a: ProcStat[], b: ProcStat[]): boolean {
  return a.length === b.length && a.every((entry, index) => sameStat(entry, b[index]!));
}

function literalIp(value: string): string | undefined {
  const family = isIP(value);
  if (family === 4) return value;
  if (family === 6) {
    try { return new URL(`http://[${value}]/`).hostname.toLowerCase(); }
    catch { return undefined; }
  }
  return undefined;
}

function port(value: string): number | undefined {
  if (!/^[1-9]\d*$/u.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= 65535 ? parsed : undefined;
}

async function sshConnection(pid: number, readProc: NonNullable<ReceiverDependencies["readProc"]>) {
  let bytes: Buffer;
  try { bytes = await readProc(`/proc/${pid}/environ`); }
  catch { return reject("proc-unavailable", `cannot read process ${pid} SSH_CONNECTION`); }
  if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.at(-1) !== 0) {
    reject("ssh-env-invalid", "origin SSH_CONNECTION is unavailable or incomplete");
  }
  const prefix = Buffer.from("SSH_CONNECTION=");
  let found: string | undefined;
  for (let start = 0; start < bytes.length; ) {
    const end = bytes.indexOf(0, start);
    if (end < 0) reject("ssh-env-invalid", "origin environment is incomplete");
    if (bytes.subarray(start, start + prefix.length).equals(prefix)) {
      if (found !== undefined) reject("ssh-env-invalid", "origin has duplicate SSH_CONNECTION entries");
      found = bytes.toString("utf8", start + prefix.length, end);
    }
    start = end + 1;
  }
  const fields = found?.split(" ");
  if (!fields || fields.length !== 4 || !literalIp(fields[0]!) || !port(fields[1]!) ||
      !literalIp(fields[2]!) || !port(fields[3]!)) {
    reject("ssh-env-invalid", "origin SSH_CONNECTION has invalid addresses or ports");
  }
  return { peer: literalIp(fields[0]!)!, serverPort: port(fields[3]!)! };
}

function sshAlias(output: string): { hostname: string; port: number } {
  const properties = new Map<string, string[]>();
  for (const line of output.split("\n")) {
    const match = /^(hostname|user|port)\s+(\S+)\s*$/iu.exec(line);
    if (match) properties.set(match[1]!.toLowerCase(), [...(properties.get(match[1]!.toLowerCase()) ?? []), match[2]!]);
  }
  const host = properties.get("hostname"), user = properties.get("user"), ports = properties.get("port");
  const hostname = host?.length === 1 && literalIp(host[0]!);
  const remotePort = ports?.length === 1 && port(ports[0]!);
  if (!hostname || !remotePort || user?.length !== 1 || user[0] !== REMOTE_USER) {
    reject("ssh-alias-invalid", `${REMOTE_ALIAS} must resolve to one literal IP, port and expected user`);
  }
  return { hostname, port: remotePort };
}

/** Resolve one frozen, source-verified mouse origin. No fallback receiver exists. */
export async function resolveInputReceiver(
  origin: OpenOrigin, dependencies: ReceiverDependencies = {},
): Promise<InputReceiver> {
  const env = dependencies.env ?? process.env;
  const run = dependencies.run ?? runDefault;
  const readProc = dependencies.readProc ?? readFile;
  if (typeof origin !== "number" || !Number.isSafeInteger(origin) || origin < 1) {
    reject("invalid-origin", "link activation has no valid tmux client PID");
  }
  const socket = /^(.+),\d+,\d+$/u.exec(env.TMUX ?? "")?.[1];
  const pane = env.TMUX_PANE;
  if (!socket?.startsWith("/") || /[\x00\r\n]/u.test(socket) || !pane || !/^%\d+$/u.test(pane)) {
    reject("missing-tmux", "link activation lacks a source tmux socket or pane");
  }
  const tmux = (args: string[], label: string) => command(run, "tmux", ["-S", socket, ...args], label);
  const inventory = () => tmux(["list-clients", "-F", CLIENT_FORMAT], "tmux list-clients");
  const view = (client: Client) => tmux([
    "display-message", "-p", "-c", client.tty, "-t", client.session, "-F", VIEW_FORMAT,
  ], "tmux display-message");

  const before = await stat(origin, readProc);
  const first = attachedClient(await inventory(), origin);
  verifyView(await view(first), first, pane);
  const chain = await ancestry(origin, readProc);
  if (!sameStat(before, chain[0]!)) reject("proc-changed", "origin process changed during inspection");

  const remote = chain.some((entry) => entry.comm === "sshd" || entry.comm === "sshd-session" ||
    entry.comm.startsWith("sshd:"));
  if (remote) {
    const connection = await sshConnection(origin, readProc);
    const alias = sshAlias(await command(run, "ssh", ["-G", REMOTE_ALIAS], "ssh -G"));
    if (connection.peer !== alias.hostname || connection.serverPort !== alias.port) {
      reject("ssh-peer-mismatch", `origin SSH peer is not ${REMOTE_ALIAS}`);
    }
  }

  const last = attachedClient(await inventory(), origin);
  if (last.tty !== first.tty || last.session !== first.session || last.flags !== first.flags) {
    reject("client-mismatch", "origin tmux client identity changed during inspection");
  }
  verifyView(await view(last), last, pane);
  const after = await ancestry(origin, readProc);
  if (!sameAncestry(chain, after) || !sameStat(before, after[0]!)) {
    reject("proc-changed", "origin process ancestry changed during inspection");
  }
  return remote ? REMOTE_ALIAS : "local";
}
