import { execFile } from "node:child_process";
import { readdir, readFile, readlink } from "node:fs/promises";
import { basename } from "node:path";
import { isIP } from "node:net";
import { promisify } from "node:util";
import type { OpenOrigin } from "./adapter.ts";

const exec = promisify(execFile);
const CLIENT_FORMAT = "pid=#{client_pid}|tty=#{client_tty}|session=#{session_id}|flags=#{client_flags}";
const VIEW_FORMAT = "pid=#{client_pid}|tty=#{client_tty}|session=#{session_id}|pane=#{pane_id}";
const PANE_TTY_FORMAT = "#{pane_tty}";
const REMOTE_ALIAS = "conley-Y9000p";
const REMOTE_USER = "conley";
const MAX_RELAY_DEPTH = 4;
const MAX_DISPLAY_ANCESTORS = 8;
const DEFAULT_SCAN_TIMEOUT_MS = 750;
const DENIED_MULTIPLEXERS = new Set([
  "screen", "zellij", "abduco", "dtach", "mosh-server", "dvtm",
]);

export type InputReceiver = "local" | Readonly<{
  kind: "remote";
  peer: string;
  port: number;
  user: "conley";
}>;
export type ReceiverErrorCode =
  | "invalid-origin" | "missing-tmux" | "command-failed" | "client-not-found"
  | "ambiguous-client" | "client-mismatch" | "pane-mismatch"
  | "proc-unavailable" | "proc-incomplete" | "proc-changed"
  | "ssh-env-invalid" | "ssh-alias-invalid" | "ssh-peer-mismatch"
  | "input-unattributed";

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
  readDir?: (path: string) => Promise<readonly string[]>;
  readLink?: (path: string) => Promise<string>;
  scanTimeoutMs?: number;
}

export interface ProcessIdentity { readonly pid: number; readonly starttime: string }
export interface InputHop {
  readonly tty: string;
  readonly leader: ProcessIdentity;
  readonly holder: ProcessIdentity & { readonly comm: string };
  readonly proof: "master" | "sshd";
}
interface TopologyBase { readonly hops: readonly InputHop[] }
export type InputTopology =
  | (TopologyBase & {
      readonly kind: "local"; readonly basis: "display"; readonly displayOwner: ProcessIdentity;
    })
  | (TopologyBase & { readonly kind: "remote"; readonly receiver: Exclude<InputReceiver, "local"> })
  | (TopologyBase & {
      readonly kind: "tmux"; readonly socket: string; readonly pane: string; readonly inputTty: string;
    })
  | (TopologyBase & { readonly kind: "unknown"; readonly diagnostic: string });

const runDefault: NonNullable<ReceiverDependencies["run"]> = async (program, args) => {
  const { stdout, stderr } = await exec(program, [...args], {
    encoding: "utf8",
    timeout: program === "ss" ? 600 : 2500,
    maxBuffer: program === "ss" ? 4 * 1024 * 1024 : 1024 * 1024,
    shell: false,
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

interface ProcStat {
  pid: number;
  ppid: number;
  pgrp: number;
  sid: number;
  tpgid: number;
  comm: string;
  starttime: string;
}

/** /proc stat fields 4, 5, 6, 8 and 22 follow the final ')' ending field 2. */
function parseStat(pid: number, bytes: Buffer): ProcStat {
  const line = bytes.toString("utf8").trimEnd();
  const open = line.indexOf(" (");
  const close = line.lastIndexOf(")");
  if (open < 1 || close < open + 2 || positivePid(line.slice(0, open)) !== pid) {
    reject("proc-incomplete", `process ${pid} has malformed stat identity`);
  }
  const comm = line.slice(open + 2, close);
  const fields = line.slice(close + 1).trim().split(/\s+/u);
  const [ppidRaw, pgrpRaw, sidRaw, tpgidRaw] = [fields[1], fields[2], fields[3], fields[5]];
  const starttime = fields[19];
  if (!comm || fields.length < 50 || !/^[A-Za-z]$/u.test(fields[0] ?? "") ||
      ["Z", "X", "x"].includes(fields[0]!) ||
      ![ppidRaw, pgrpRaw, sidRaw].every((value) => /^(0|[1-9]\d*)$/u.test(value ?? "")) ||
      !/^-?(0|[1-9]\d*)$/u.test(tpgidRaw ?? "") ||
      !/^[1-9]\d*$/u.test(starttime ?? "") ||
      ![ppidRaw, pgrpRaw, sidRaw, tpgidRaw].every((value) => Number.isSafeInteger(Number(value)))) {
    reject("proc-incomplete", `process ${pid} has incomplete stat fields`);
  }
  return {
    pid,
    ppid: Number(ppidRaw),
    pgrp: Number(pgrpRaw),
    sid: Number(sidRaw),
    tpgid: Number(tpgidRaw),
    comm,
    starttime: starttime!,
  };
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
  return a.pid === b.pid && a.ppid === b.ppid && a.pgrp === b.pgrp && a.sid === b.sid &&
    a.tpgid === b.tpgid && a.comm === b.comm && a.starttime === b.starttime;
}

function sameIdentity(identity: ProcessIdentity, value: ProcStat): boolean {
  return identity.pid === value.pid && identity.starttime === value.starttime;
}

function sameAncestry(a: ProcStat[], b: ProcStat[]): boolean {
  return a.length === b.length && a.every((entry, index) => sameStat(entry, b[index]!));
}

function literalIp(value: string): string | undefined {
  const family = isIP(value);
  if (family === 4) return value;
  if (family === 6) {
    try { return new URL(`http://[${value}]/`).hostname.slice(1, -1).toLowerCase(); }
    catch { return undefined; }
  }
  return undefined;
}

function port(value: string): number | undefined {
  if (!/^[1-9]\d*$/u.test(value)) return undefined;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed <= 65535 ? parsed : undefined;
}

function parseEnvironment(pid: number, bytes: Buffer): Map<string, string> {
  if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.at(-1) !== 0) {
    reject("proc-incomplete", `process ${pid} environment is unavailable or incomplete`);
  }
  const values = new Map<string, string>();
  for (let start = 0; start < bytes.length; ) {
    const end = bytes.indexOf(0, start);
    if (end < 0) reject("proc-incomplete", `process ${pid} environment is incomplete`);
    const separator = bytes.indexOf(61, start);
    if (separator > start && separator < end) {
      const key = bytes.toString("utf8", start, separator);
      if (values.has(key)) reject("proc-incomplete", `process ${pid} environment has duplicate ${key}`);
      values.set(key, bytes.toString("utf8", separator + 1, end));
    }
    start = end + 1;
  }
  return values;
}

async function environment(pid: number, readProc: NonNullable<ReceiverDependencies["readProc"]>): Promise<Map<string, string>> {
  let bytes: Buffer;
  try { bytes = await readProc(`/proc/${pid}/environ`); }
  catch { return reject("proc-unavailable", `cannot read process ${pid} environment`); }
  return parseEnvironment(pid, bytes);
}

async function sshConnection(pid: number, readProc: NonNullable<ReceiverDependencies["readProc"]>) {
  const values = await environment(pid, readProc);
  const found = values.get("SSH_CONNECTION");
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

interface UnixSocketEntry { readonly path: string; readonly peerInode: string }
interface ProcAccess {
  env: NodeJS.ProcessEnv;
  run: NonNullable<ReceiverDependencies["run"]>;
  readProc: NonNullable<ReceiverDependencies["readProc"]>;
  readDir: NonNullable<ReceiverDependencies["readDir"]>;
  readLink: NonNullable<ReceiverDependencies["readLink"]>;
  scanTimeoutMs: number;
  socketTable?: Promise<ReadonlyMap<string, UnixSocketEntry> | undefined>;
}

function access(dependencies: ReceiverDependencies): ProcAccess {
  const configured = dependencies.scanTimeoutMs;
  return {
    env: dependencies.env ?? process.env,
    run: dependencies.run ?? runDefault,
    readProc: dependencies.readProc ?? readFile,
    readDir: dependencies.readDir ?? (async (path) => readdir(path)),
    readLink: dependencies.readLink ?? readlink,
    scanTimeoutMs: typeof configured === "number" && Number.isFinite(configured) && configured > 0 ?
      configured : DEFAULT_SCAN_TIMEOUT_MS,
  };
}

function ttyIndex(tty: string): number | undefined {
  const match = /^\/dev\/pts\/(\d+)$/u.exec(tty);
  return match && Number.isSafeInteger(Number(match[1])) ? Number(match[1]) : undefined;
}

async function link(accessor: ProcAccess, path: string): Promise<string | undefined> {
  try { return await accessor.readLink(path); }
  catch { return undefined; }
}

async function uid(pid: number, accessor: ProcAccess): Promise<readonly number[] | undefined> {
  let bytes: Buffer;
  try { bytes = await accessor.readProc(`/proc/${pid}/status`); }
  catch { return undefined; }
  const match = /^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$/mu.exec(bytes.toString("utf8"));
  if (!match) return undefined;
  const values = match.slice(1).map(Number);
  return values.every(Number.isSafeInteger) ? values : undefined;
}

function sameUid(a: readonly number[] | undefined, b: readonly number[] | undefined): boolean {
  return a !== undefined && b !== undefined && a.length === b.length && a.every((value, index) => value === b[index]);
}

async function argv(pid: number, accessor: ProcAccess): Promise<readonly string[] | undefined> {
  let bytes: Buffer;
  try { bytes = await accessor.readProc(`/proc/${pid}/cmdline`); }
  catch { return undefined; }
  if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.at(-1) !== 0) return undefined;
  try {
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return decoded.slice(0, -1).split("\0");
  } catch { return undefined; }
}

async function genericMasterHolder(candidate: ProcStat, leaderUid: readonly number[], index: number,
                                   accessor: ProcAccess): Promise<boolean> {
  if (!sameUid(await uid(candidate.pid, accessor), leaderUid)) return false;
  let fds: readonly string[];
  try { fds = await accessor.readDir(`/proc/${candidate.pid}/fd`); }
  catch { return false; }
  for (const fd of fds) {
    if (!/^\d+$/u.test(fd)) continue;
    const target = await link(accessor, `/proc/${candidate.pid}/fd/${fd}`);
    if (target !== "/dev/ptmx" && target !== "/dev/pts/ptmx") continue;
    let info: Buffer;
    try { info = await accessor.readProc(`/proc/${candidate.pid}/fdinfo/${fd}`); }
    catch { continue; }
    if (new RegExp(`(?:^|\\n)tty-index:\\s*${index}(?:\\n|$)`, "u").test(info.toString("utf8"))) return true;
  }
  return false;
}

function sshdComm(comm: string): boolean {
  return comm === "sshd" || comm === "sshd-session" || comm.startsWith("sshd:");
}

async function sshdHolder(candidate: ProcStat, leader: ProcStat, leaderUid: readonly number[], tty: string,
                          accessor: ProcAccess): Promise<boolean> {
  if (!sshdComm(candidate.comm) || leader.ppid !== candidate.pid ||
      await link(accessor, `/proc/${leader.pid}/fd/0`) !== tty ||
      !sameUid(await uid(candidate.pid, accessor), leaderUid)) return false;
  const args = await argv(candidate.pid, accessor);
  if (!args || args.length !== 1) return false;
  const number = ttyIndex(tty);
  return number !== undefined && new RegExp(`^(?:sshd|sshd-session): [^\\s/@]+@pts/${number}\\s*$`, "u").test(args[0]!);
}

interface HolderProof { stat: ProcStat; proof: "master" | "sshd" }
interface HolderSearch { holder?: HolderProof; timedOut: boolean }

async function fallbackHolderScan(
  leaderUid: readonly number[], index: number, accessor: ProcAccess,
): Promise<HolderSearch> {
  let expired = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<HolderSearch>((resolve) => {
    timer = setTimeout(() => { expired = true; resolve({ timedOut: true }); }, accessor.scanTimeoutMs);
  });
  const scan = (async (): Promise<HolderSearch> => {
    let entries: readonly string[];
    try { entries = await accessor.readDir("/proc"); }
    catch { return { timedOut: false }; }
    const found = new Map<number, HolderProof>();
    for (const entry of entries) {
      if (expired) return { timedOut: true };
      const pid = positivePid(entry);
      if (pid === undefined) continue;
      try {
        const candidate = await stat(pid, accessor.readProc);
        if (expired) return { timedOut: true };
        if (await genericMasterHolder(candidate, leaderUid, index, accessor)) {
          found.set(candidate.pid, { stat: candidate, proof: "master" });
          if (found.size > 1) return { timedOut: false };
        }
      } catch { /* Processes may exit or be unreadable during the bounded scan. */ }
    }
    return { holder: found.size === 1 ? [...found.values()][0] : undefined, timedOut: false };
  })();
  try { return await Promise.race([scan, timeout]); }
  finally { if (timer) clearTimeout(timer); expired = true; }
}

async function findHolder(leader: ProcStat, tty: string, accessor: ProcAccess): Promise<HolderSearch> {
  const index = ttyIndex(tty);
  const leaderUid = await uid(leader.pid, accessor);
  if (index === undefined || !leaderUid) return { timedOut: false };
  if (leader.ppid > 0) {
    try {
      const candidate = await stat(leader.ppid, accessor.readProc);
      if (await sshdHolder(candidate, leader, leaderUid, tty, accessor)) {
        return { holder: { stat: candidate, proof: "sshd" }, timedOut: false };
      }
      if (await genericMasterHolder(candidate, leaderUid, index, accessor)) {
        return { holder: { stat: candidate, proof: "master" }, timedOut: false };
      }
    } catch { /* The bounded fallback scan decides whether another readable holder is unique. */ }
  }
  return fallbackHolderScan(leaderUid, index, accessor);
}

async function stillHolds(proof: HolderProof, leader: ProcStat, tty: string, accessor: ProcAccess): Promise<boolean> {
  let current: ProcStat;
  try { current = await stat(proof.stat.pid, accessor.readProc); }
  catch { return false; }
  if (!sameStat(proof.stat, current)) return false;
  const leaderUid = await uid(leader.pid, accessor);
  const index = ttyIndex(tty);
  if (!leaderUid || index === undefined) return false;
  return proof.proof === "sshd" ? sshdHolder(current, leader, leaderUid, tty, accessor) :
    genericMasterHolder(current, leaderUid, index, accessor);
}

function normalizeSocketInode(raw: string): string | undefined {
  if (!/^-?\d+$/u.test(raw)) return undefined;
  try {
    let value = BigInt(raw);
    if (value < 0n) value += 1n << 32n;
    return value >= 0n ? value.toString() : undefined;
  } catch { return undefined; }
}

function parseUnixSockets(output: string): ReadonlyMap<string, UnixSocketEntry> {
  const sockets = new Map<string, UnixSocketEntry>();
  for (const line of output.split("\n")) {
    if (!line.trim()) continue;
    const fields = line.trim().split(/\s+/u);
    if (fields.length < 8 || !fields[0]!.startsWith("u_")) continue;
    const inode = normalizeSocketInode(fields[5]!);
    const peerInode = normalizeSocketInode(fields[7]!);
    if (!inode || !peerInode) continue;
    sockets.set(inode, { path: fields[4]!, peerInode });
  }
  return sockets;
}

async function unixSockets(accessor: ProcAccess): Promise<ReadonlyMap<string, UnixSocketEntry> | undefined> {
  accessor.socketTable ??= (async () => {
    let result: ReceiverRunResult;
    try { result = await accessor.run("ss", ["-xH", "-a", "-n"]); }
    catch { return undefined; }
    if (result.code !== 0 || typeof result.stdout !== "string") return undefined;
    return parseUnixSockets(result.stdout);
  })();
  return accessor.socketTable;
}

async function ownedSocketInodes(pid: number, accessor: ProcAccess): Promise<readonly string[]> {
  let fds: readonly string[];
  try { fds = await accessor.readDir(`/proc/${pid}/fd`); }
  catch { return []; }
  const inodes = new Set<string>();
  for (const fd of fds) {
    if (!/^\d+$/u.test(fd)) continue;
    const target = await link(accessor, `/proc/${pid}/fd/${fd}`);
    const match = /^socket:\[(\d+)\]$/u.exec(target ?? "");
    if (match) inodes.add(match[1]!);
  }
  return [...inodes];
}

function safeSocketPath(value: string): boolean {
  return value.startsWith("/") && !/[\x00-\x1f\x7f]/u.test(value);
}

async function displayPaths(pid: number, _processUid: readonly number[], accessor: ProcAccess): Promise<ReadonlySet<string>> {
  const paths = new Set<string>();
  try {
    const values = await environment(pid, accessor.readProc);
    const runtime = values.get("XDG_RUNTIME_DIR");
    const wayland = values.get("WAYLAND_DISPLAY");
    if (runtime && safeSocketPath(runtime) && wayland && !/[\x00-\x1f\x7f]/u.test(wayland)) {
      const path = wayland.startsWith("/") ? wayland : `${runtime.replace(/\/$/u, "")}/${wayland}`;
      if (safeSocketPath(path)) paths.add(path);
    }
  } catch { /* Environment paths are optional; a canonical live peer still proves the display. */ }
  return paths;
}

function isDisplayServerPath(path: string, processUid: readonly number[], extra: ReadonlySet<string>): boolean {
  const realUid = processUid[0];
  if (realUid !== undefined && new RegExp(`^/run/user/${realUid}/wayland-[^/\\s]+$`, "u").test(path)) return true;
  if (/^@?\/tmp\/\.X11-unix\/X\d+$/u.test(path)) return true;
  return extra.has(path);
}

function executableName(args: readonly string[] | undefined, comm: string): string {
  const first = args?.[0];
  return first ? basename(first) : comm;
}

function isTmuxServer(args: readonly string[] | undefined, comm: string): boolean {
  return comm === "tmux: server" || comm.startsWith("tmux: server") ||
    executableName(args, comm) === "tmux" && comm.includes("server");
}

function isDeniedMultiplexer(args: readonly string[] | undefined, comm: string): boolean {
  return [comm, executableName(args, comm)].some((name) => DENIED_MULTIPLEXERS.has(name.toLowerCase()));
}

interface DisplayEvidence {
  owner?: ProcessIdentity;
  unavailable?: true;
  changed?: true;
}

async function displayEvidence(holder: ProcStat, accessor: ProcAccess): Promise<DisplayEvidence> {
  const sockets = await unixSockets(accessor);
  if (!sockets) return { unavailable: true };
  const visited = new Set<number>();
  let current = holder;
  for (let depth = 0; depth < MAX_DISPLAY_ANCESTORS && !visited.has(current.pid); depth++) {
    visited.add(current.pid);
    const args = await argv(current.pid, accessor);
    if (sshdComm(current.comm) || isTmuxServer(args, current.comm) || isDeniedMultiplexer(args, current.comm)) return {};
    const processUid = await uid(current.pid, accessor);
    if (processUid) {
      const extraPaths = await displayPaths(current.pid, processUid, accessor);
      const inodes = await ownedSocketInodes(current.pid, accessor);
      for (const inode of inodes) {
        const peerInode = sockets.get(inode)?.peerInode;
        const peerPath = peerInode ? sockets.get(peerInode)?.path : undefined;
        if (peerPath && isDisplayServerPath(peerPath, processUid, extraPaths)) {
          let after: ProcStat;
          try { after = await stat(current.pid, accessor.readProc); }
          catch { return { changed: true }; }
          if (!sameStat(current, after)) return { changed: true };
          return { owner: processIdentity(current) };
        }
      }
    }
    if (current.pid === 1 || current.ppid < 1) return {};
    try { current = await stat(current.ppid, accessor.readProc); }
    catch { return { changed: true }; }
  }
  return {};
}

function processIdentity(value: ProcStat): ProcessIdentity {
  return Object.freeze({ pid: value.pid, starttime: value.starttime });
}

function holderName(comm: string): string {
  const clean = comm.replace(/[\x00-\x1f\x7f]/gu, "?").slice(0, 80);
  return clean || "未知进程";
}

function unknown(diagnostic: string, hops: readonly InputHop[] = []): InputTopology {
  return Object.freeze({ kind: "unknown", diagnostic, hops: Object.freeze([...hops]) });
}

function withHop(topology: InputTopology, hop: InputHop): InputTopology {
  return Object.freeze({ ...topology, hops: Object.freeze([hop, ...topology.hops]) });
}

function identitiesEqual(a: InputHop, b: InputHop): boolean {
  return a.tty === b.tty && a.proof === b.proof &&
    a.leader.pid === b.leader.pid && a.leader.starttime === b.leader.starttime &&
    a.holder.pid === b.holder.pid && a.holder.starttime === b.holder.starttime && a.holder.comm === b.holder.comm;
}

/** Compare the frozen terminal incarnation, not mutable route diagnostics. */
export function sameInputTopology(a: InputTopology, b: InputTopology): boolean {
  if (a.kind !== b.kind || a.hops.length !== b.hops.length ||
      !a.hops.every((hop, index) => identitiesEqual(hop, b.hops[index]!))) return false;
  if (a.kind === "local" && b.kind === "local") {
    return a.basis === b.basis && a.displayOwner.pid === b.displayOwner.pid &&
      a.displayOwner.starttime === b.displayOwner.starttime;
  }
  if (a.kind === "remote" && b.kind === "remote") {
    return a.receiver.peer === b.receiver.peer && a.receiver.port === b.receiver.port &&
      a.receiver.user === b.receiver.user;
  }
  if (a.kind === "tmux" && b.kind === "tmux") {
    return a.socket === b.socket && a.pane === b.pane && a.inputTty === b.inputTty;
  }
  return a.kind === "unknown" && b.kind === "unknown";
}

async function inspectInput(
  subjectPid: number, accessor: ProcAccess, depth: number, visited: ReadonlySet<number>,
): Promise<InputTopology> {
  if (depth > MAX_RELAY_DEPTH) return unknown("输入终端透明中继层级超过限制，无法归属");
  let subject: ProcStat;
  try { subject = await stat(subjectPid, accessor.readProc); }
  catch { return unknown(`无法读取输入进程 ${subjectPid} 的身份`); }
  const tty = await link(accessor, `/proc/${subjectPid}/fd/0`);
  if (!tty || ttyIndex(tty) === undefined) return unknown(`输入进程 ${subjectPid} 的 stdin 不是可识别的 PTY`);
  let leader: ProcStat;
  try {
    leader = await stat(subject.sid, accessor.readProc);
    if (leader.pid !== leader.sid || await link(accessor, `/proc/${leader.pid}/fd/0`) !== tty) {
      return unknown(`${tty} 的会话首进程无法验证`);
    }
  } catch { return unknown(`${tty} 的会话首进程无法读取`); }
  const search = await findHolder(leader, tty, accessor);
  if (search.timedOut) return unknown(`${tty} 的 PTY 主端扫描超过 ${accessor.scanTimeoutMs} ms，无法归属`);
  if (!search.holder) return unknown(`${tty} 的 PTY 主端持有者不唯一或不可验证`);
  const proof = search.holder;
  const holder = proof.stat;
  const hop: InputHop = Object.freeze({
    tty,
    leader: processIdentity(leader),
    holder: Object.freeze({ ...processIdentity(holder), comm: holderName(holder.comm) }),
    proof: proof.proof,
  });
  try {
    const afterSubject = await stat(subjectPid, accessor.readProc);
    const afterLeader = await stat(leader.pid, accessor.readProc);
    if (!sameStat(subject, afterSubject) || !sameStat(leader, afterLeader) ||
        await link(accessor, `/proc/${subjectPid}/fd/0`) !== tty ||
        !await stillHolds(proof, leader, tty, accessor)) {
      return unknown(`${tty} 的输入持有者在检查期间发生变化`, [hop]);
    }
  } catch { return unknown(`${tty} 的输入持有者在检查期间消失`, [hop]); }

  const args = await argv(holder.pid, accessor);
  if (proof.proof === "sshd") {
    try {
      const connection = await sshConnection(leader.pid, accessor.readProc);
      const alias = sshAlias(await command(accessor.run, "ssh", ["-G", REMOTE_ALIAS], "ssh -G"));
      if (connection.peer !== alias.hostname || connection.serverPort !== alias.port) {
        return unknown(`输入持有者 ${holderName(holder.comm)} 的 SSH 对端不是 ${REMOTE_ALIAS}`, [hop]);
      }
      return Object.freeze({
        kind: "remote",
        receiver: Object.freeze({ kind: "remote", peer: alias.hostname, port: alias.port, user: REMOTE_USER }),
        hops: Object.freeze([hop]),
      });
    } catch {
      return unknown(`输入持有者 ${holderName(holder.comm)} 的 SSH 对端无法验证为 ${REMOTE_ALIAS}`, [hop]);
    }
  }

  if (isTmuxServer(args, holder.comm)) {
    const socket = /^(.+),\d+,\d+$/u.exec(accessor.env.TMUX ?? "")?.[1];
    const pane = accessor.env.TMUX_PANE;
    if (!socket?.startsWith("/") || /[\x00\r\n]/u.test(socket) || !pane || !/^%\d+$/u.test(pane)) {
      return unknown(`输入持有者 ${holderName(holder.comm)} 缺少匹配的 tmux socket/pane`, [hop]);
    }
    try {
      const output = await command(accessor.run, "tmux",
        ["-S", socket, "display-message", "-p", "-t", pane, "-F", PANE_TTY_FORMAT], "tmux pane_tty");
      if (output.trimEnd() !== tty || output.includes("\r")) {
        return unknown(`输入持有者 ${holderName(holder.comm)} 与 ${pane} 的 pane_tty 不匹配`, [hop]);
      }
    } catch { return unknown(`输入持有者 ${holderName(holder.comm)} 的 ${pane} 无法验证`, [hop]); }
    return Object.freeze({ kind: "tmux", socket, pane, inputTty: tty, hops: Object.freeze([hop]) });
  }

  if (isDeniedMultiplexer(args, holder.comm)) {
    return unknown(`输入持有者 ${holderName(holder.comm)} 是不支持的多路复用器，无法归属点击来源`, [hop]);
  }

  const outerTty = await link(accessor, `/proc/${holder.pid}/fd/0`);
  if (outerTty && ttyIndex(outerTty) !== undefined && outerTty !== tty) {
    if (holder.pgrp <= 0 || holder.tpgid !== holder.pgrp) {
      return unknown(`输入持有者 ${holderName(holder.comm)} 不在外层 PTY 的前台进程组，不能作为透明中继`, [hop]);
    }
    if (visited.has(holder.pid)) return unknown("输入终端透明中继形成循环，无法归属", [hop]);
    const nextVisited = new Set(visited);
    nextVisited.add(holder.pid);
    const relayed = await inspectInput(holder.pid, accessor, depth + 1, nextVisited);
    try {
      const current = await stat(holder.pid, accessor.readProc);
      if (!sameStat(holder, current) || await link(accessor, `/proc/${holder.pid}/fd/0`) !== outerTty ||
          !await stillHolds(proof, leader, tty, accessor)) {
        return unknown(`输入持有者 ${holderName(holder.comm)} 在中继检查期间发生变化`, [hop]);
      }
    } catch { return unknown(`输入持有者 ${holderName(holder.comm)} 在中继检查期间消失`, [hop]); }
    return withHop(relayed, hop);
  }

  const display = await displayEvidence(holder, accessor);
  if (display.unavailable) return unknown("无法读取本机 Unix socket 连接，不能证明输入持有者连接到本地显示服务", [hop]);
  if (display.changed) return unknown(`输入持有者 ${holderName(holder.comm)} 的显示证据在检查期间发生变化`, [hop]);
  if (display.owner) {
    return Object.freeze({
      kind: "local", basis: "display", displayOwner: display.owner, hops: Object.freeze([hop]),
    });
  }
  return unknown(`输入持有者 ${holderName(holder.comm)} 没有连接到可验证的本地显示服务，无法归属`, [hop]);
}

/** Resolve the actual fd0 PTY transport. Unknown input is returned explicitly and never falls back locally/remotely. */
export async function resolveInputTopology(
  subjectPid: number = process.pid, dependencies: ReceiverDependencies = {},
): Promise<InputTopology> {
  if (!Number.isSafeInteger(subjectPid) || subjectPid < 1) return unknown("输入进程 PID 无效");
  return inspectInput(subjectPid, access(dependencies), 0, new Set([subjectPid]));
}

/** Cheaply revalidate one activation-time chain without rescanning /proc or rerunning ss/ssh/tmux. */
export async function revalidateInputTopology(
  subjectPid: number, topology: InputTopology, dependencies: ReceiverDependencies = {},
): Promise<boolean> {
  if (topology.kind === "unknown" || !Number.isSafeInteger(subjectPid) || subjectPid < 1) return false;
  const accessor = access(dependencies);
  let currentSubject = subjectPid;
  for (const hop of topology.hops) {
    if (await link(accessor, `/proc/${currentSubject}/fd/0`) !== hop.tty) return false;
    let leader: ProcStat, holder: ProcStat;
    try {
      leader = await stat(hop.leader.pid, accessor.readProc);
      holder = await stat(hop.holder.pid, accessor.readProc);
    } catch { return false; }
    if (!sameIdentity(hop.leader, leader) || !sameIdentity(hop.holder, holder) ||
        await link(accessor, `/proc/${leader.pid}/fd/0`) !== hop.tty) return false;
    const leaderUid = await uid(leader.pid, accessor);
    const index = ttyIndex(hop.tty);
    if (!leaderUid || index === undefined) return false;
    const valid = hop.proof === "sshd" ? await sshdHolder(holder, leader, leaderUid, hop.tty, accessor) :
      await genericMasterHolder(holder, leaderUid, index, accessor);
    if (!valid) return false;
    currentSubject = holder.pid;
  }
  if (topology.kind === "local") {
    try {
      const owner = await stat(topology.displayOwner.pid, accessor.readProc);
      if (!sameIdentity(topology.displayOwner, owner)) return false;
    } catch { return false; }
  }
  return true;
}

/** Resolve one frozen, source-verified tmux mouse origin. No fallback receiver exists. */
export async function resolveInputReceiver(
  origin: OpenOrigin, dependencies: ReceiverDependencies = {},
): Promise<InputReceiver> {
  const accessor = access(dependencies);
  if (typeof origin !== "number" || !Number.isSafeInteger(origin) || origin < 1) {
    reject("invalid-origin", "link activation has no valid tmux client PID");
  }
  const socket = /^(.+),\d+,\d+$/u.exec(accessor.env.TMUX ?? "")?.[1];
  const pane = accessor.env.TMUX_PANE;
  if (!socket?.startsWith("/") || /[\x00\r\n]/u.test(socket) || !pane || !/^%\d+$/u.test(pane)) {
    reject("missing-tmux", "link activation lacks a source tmux socket or pane");
  }
  const tmux = (args: string[], label: string) => command(accessor.run, "tmux", ["-S", socket, ...args], label);
  const inventory = () => tmux(["list-clients", "-F", CLIENT_FORMAT], "tmux list-clients");
  const view = (client: Client) => tmux([
    "display-message", "-p", "-c", client.tty, "-t", client.session, "-F", VIEW_FORMAT,
  ], "tmux display-message");

  const before = await stat(origin, accessor.readProc);
  const first = attachedClient(await inventory(), origin);
  verifyView(await view(first), first, pane);
  const chain = await ancestry(origin, accessor.readProc);
  if (!sameStat(before, chain[0]!)) reject("proc-changed", "origin process changed during inspection");

  const topology = await resolveInputTopology(origin, dependencies);
  if (topology.hops[0]?.tty !== first.tty) {
    reject("client-mismatch", "origin client terminal differs from the verified tmux client tty");
  }
  let receiver: InputReceiver;
  if (topology.kind === "remote") receiver = topology.receiver;
  else if (topology.kind === "local") receiver = "local";
  else reject("input-unattributed", topology.kind === "unknown" ? topology.diagnostic :
    "origin tmux client is behind another multiplexer");

  const last = attachedClient(await inventory(), origin);
  if (last.tty !== first.tty || last.session !== first.session || last.flags !== first.flags) {
    reject("client-mismatch", "origin tmux client identity changed during inspection");
  }
  verifyView(await view(last), last, pane);
  const after = await ancestry(origin, accessor.readProc);
  if (!sameAncestry(chain, after) || !sameStat(before, after[0]!)) {
    reject("proc-changed", "origin process ancestry changed during inspection");
  }
  return receiver;
}
