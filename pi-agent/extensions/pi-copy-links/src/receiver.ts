import { execFile } from "node:child_process";
import { readdir, readFile, readlink, stat as fsStat } from "node:fs/promises";
import { basename } from "node:path";
import { isIP } from "node:net";
import { hostname as osHostname } from "node:os";
import { promisify } from "node:util";
import type { OpenOrigin } from "./adapter.ts";
import { pairedSshAlias, type PairedSshAlias } from "./pairing.ts";

const exec = promisify(execFile);
const CLIENT_FORMAT = "pid=#{client_pid}|tty=#{client_tty}|session=#{session_id}|flags=#{client_flags}";
const VIEW_FORMAT = "pid=#{client_pid}|tty=#{client_tty}|session=#{session_id}|pane=#{pane_id}";
const PANE_TTY_FORMAT = "#{pane_tty}";
const REMOTE_USER = "conley";
const MAX_RELAY_DEPTH = 4;
const DEFAULT_SCAN_TIMEOUT_MS = 750;
const DENIED_MULTIPLEXERS = new Set([
  "screen", "zellij", "abduco", "dtach", "mosh-server", "dvtm",
]);
const TERMINAL_EMULATORS = new Set(["ghostty"]);
const UNVERIFIED_EMULATORS = new Set([
  "gnome-terminal-server", "kitty", "alacritty", "konsole", "xterm", "foot",
  "tilix", "xfce4-terminal", "wezterm-gui",
]);

export type InputReceiver = "local" | Readonly<{
  kind: "remote";
  alias: PairedSshAlias;
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
  /** Actual OS hostname; injectable for tests, never read from inherited env. */
  hostname?: () => string;
  run?: (program: string, args: readonly string[]) => Promise<ReceiverRunResult>;
  readProc?: (path: string) => Promise<Buffer>;
  readDir?: (path: string) => Promise<readonly string[]>;
  readLink?: (path: string) => Promise<string>;
  statPath?: (path: string) => Promise<{ dev: bigint | number; ino: bigint | number }>;
  scanTimeoutMs?: number;
}

export interface ProcessIdentity { readonly pid: number; readonly starttime: string }
export interface ImageIdentity { readonly dev: string; readonly ino: string }
export interface InputHop {
  readonly tty: string;
  readonly leader: ProcessIdentity;
  readonly holder: ProcessIdentity & { readonly comm: string };
  readonly proof: "master" | "sshd";
  readonly role: "sshd" | "tmux" | "relay" | "terminal" | "unknown";
  readonly image?: ImageIdentity;
  readonly relay?: "codoxear" | "script";
}
interface TopologyBase { readonly hops: readonly InputHop[] }
export type InputTopology =
  | (TopologyBase & { readonly kind: "local"; readonly basis: "terminal" })
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
  ttyNr: number;
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
  const [ppidRaw, pgrpRaw, sidRaw, ttyNrRaw, tpgidRaw] = [fields[1], fields[2], fields[3], fields[4], fields[5]];
  const starttime = fields[19];
  if (!comm || fields.length < 50 || !/^[A-Za-z]$/u.test(fields[0] ?? "") ||
      ["Z", "X", "x"].includes(fields[0]!) ||
      ![ppidRaw, pgrpRaw, sidRaw].every((value) => /^(0|[1-9]\d*)$/u.test(value ?? "")) ||
      ![ttyNrRaw, tpgidRaw].every((value) => /^-?(0|[1-9]\d*)$/u.test(value ?? "")) ||
      !/^[1-9]\d*$/u.test(starttime ?? "") ||
      ![ppidRaw, pgrpRaw, sidRaw, ttyNrRaw, tpgidRaw].every((value) => Number.isSafeInteger(Number(value)))) {
    reject("proc-incomplete", `process ${pid} has incomplete stat fields`);
  }
  return {
    pid,
    ppid: Number(ppidRaw),
    pgrp: Number(pgrpRaw),
    sid: Number(sidRaw),
    ttyNr: Number(ttyNrRaw),
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
    a.ttyNr === b.ttyNr && a.tpgid === b.tpgid && a.comm === b.comm && a.starttime === b.starttime;
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

function sshAlias(output: string, alias: PairedSshAlias): { hostname: string; port: number } {
  const properties = new Map<string, string[]>();
  for (const line of output.split("\n")) {
    const match = /^(hostname|user|port)\s+(\S+)\s*$/iu.exec(line);
    if (match) properties.set(match[1]!.toLowerCase(), [...(properties.get(match[1]!.toLowerCase()) ?? []), match[2]!]);
  }
  const host = properties.get("hostname"), user = properties.get("user"), ports = properties.get("port");
  const hostname = host?.length === 1 && literalIp(host[0]!);
  const remotePort = ports?.length === 1 && port(ports[0]!);
  if (!hostname || !remotePort || user?.length !== 1 || user[0] !== REMOTE_USER) {
    reject("ssh-alias-invalid", `${alias} must resolve to one literal IP, port and expected user`);
  }
  return { hostname, port: remotePort };
}

interface UnixSocketEntry { readonly path: string; readonly peerInode: string }
interface ProcAccess {
  env: NodeJS.ProcessEnv;
  hostname: () => string;
  run: NonNullable<ReceiverDependencies["run"]>;
  readProc: NonNullable<ReceiverDependencies["readProc"]>;
  readDir: NonNullable<ReceiverDependencies["readDir"]>;
  readLink: NonNullable<ReceiverDependencies["readLink"]>;
  statPath: NonNullable<ReceiverDependencies["statPath"]>;
  scanTimeoutMs: number;
  socketTable?: Promise<ReadonlyMap<string, UnixSocketEntry> | undefined>;
}

function access(dependencies: ReceiverDependencies): ProcAccess {
  const configured = dependencies.scanTimeoutMs;
  return {
    env: dependencies.env ?? process.env,
    hostname: dependencies.hostname ?? osHostname,
    run: dependencies.run ?? runDefault,
    readProc: dependencies.readProc ?? readFile,
    readDir: dependencies.readDir ?? (async (path) => readdir(path)),
    readLink: dependencies.readLink ?? readlink,
    statPath: dependencies.statPath ?? (async (path) => fsStat(path, { bigint: true })),
    scanTimeoutMs: typeof configured === "number" && Number.isFinite(configured) && configured > 0 ?
      configured : DEFAULT_SCAN_TIMEOUT_MS,
  };
}

function ttyIndex(tty: string): number | undefined {
  const match = /^\/dev\/pts\/(\d+)$/u.exec(tty);
  return match && Number.isSafeInteger(Number(match[1])) ? Number(match[1]) : undefined;
}

function controlsTty(process: ProcStat, tty: string): boolean {
  const index = ttyIndex(tty);
  if (index === undefined) return false;
  const device = BigInt.asUintN(32, BigInt(process.ttyNr));
  const major = Number((device >> 8n) & 0xfffn);
  const minor = Number((device & 0xffn) | ((device >> 12n) & 0xfff00n));
  return major === 136 && minor === index;
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

interface ExecutableImage { readonly path: string; readonly identity: ImageIdentity }

function imageIdentity(image: { dev: bigint | number; ino: bigint | number }): ImageIdentity {
  return Object.freeze({ dev: String(image.dev), ino: String(image.ino) });
}

async function executable(pid: number, accessor: ProcAccess): Promise<ExecutableImage | undefined> {
  const procPath = `/proc/${pid}/exe`;
  const rawPath = await link(accessor, procPath);
  if (!rawPath?.startsWith("/") || /[\x00-\x1f\x7f]/u.test(rawPath)) return undefined;
  let running: ImageIdentity;
  try { running = imageIdentity(await accessor.statPath(procPath)); }
  catch { return undefined; }

  const deletedSuffix = " (deleted)";
  let path = rawPath;
  if (rawPath.endsWith(deletedSuffix)) {
    try {
      const literal = imageIdentity(await accessor.statPath(rawPath));
      if (!sameImage(literal, running)) path = rawPath.slice(0, -deletedSuffix.length);
    } catch (error) {
      const code = (error as NodeJS.ErrnoException | undefined)?.code;
      if (code === "ENOENT" || code === "ENOTDIR") path = rawPath.slice(0, -deletedSuffix.length);
      else return undefined;
    }
  }
  if (!path) return undefined;
  return Object.freeze({ path, identity: running });
}

function sameImage(a: ImageIdentity | undefined, b: ImageIdentity | undefined): boolean {
  return a !== undefined && b !== undefined && a.dev === b.dev && a.ino === b.ino;
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
  // sshd retitles argv and may leave trailing NUL padding, never extra arguments.
  if (!args || args.slice(1).some((arg) => arg !== "")) return false;
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

function executableName(path: string | undefined): string | undefined {
  return path ? basename(path) : undefined;
}

function tmuxServerComm(comm: string): boolean {
  return comm === "tmux: server" || comm.startsWith("tmux: server");
}

function isTmuxServer(exe: string | undefined, comm: string): boolean {
  return executableName(exe) === "tmux" && tmuxServerComm(comm);
}

function isDeniedMultiplexer(exe: string | undefined, comm: string): boolean {
  return [comm, executableName(exe)].some((name) => name !== undefined && DENIED_MULTIPLEXERS.has(name.toLowerCase()));
}

function pythonExecutable(exe: string | undefined): boolean {
  return /^python(?:\d+(?:\.\d+)*)?$/u.test(executableName(exe) ?? "");
}

function codoxearBrokerEntrypoint(holder: ProcStat, args: readonly string[] | undefined): boolean {
  if (!args || !/^python(?:\d+(?:\.\d+)*)?$/u.test(basename(args[0] ?? ""))) return false;
  const module = args.indexOf("-m");
  const moduleEntry = module >= 0 && (args[module + 1] === "codoxear.broker" ||
    args[module + 1] === "codoxear" && args[module + 2] === "broker");
  const consoleEntry = holder.comm === "codoxear-broker" && basename(args[1] ?? "") === "codoxear-broker";
  return moduleEntry || consoleEntry;
}

function codoxearBrokerIdentity(
  holder: ProcStat, exe: string | undefined, args: readonly string[] | undefined,
): boolean {
  return pythonExecutable(exe) && codoxearBrokerEntrypoint(holder, args);
}

function relayIdentity(
  holder: ProcStat, exe: string | undefined, args: readonly string[] | undefined,
): "codoxear" | "script" | undefined {
  if (exe === "/usr/bin/script") return "script";
  if (codoxearBrokerIdentity(holder, exe, args)) return "codoxear";
  return undefined;
}

function terminalEmulatorIdentity(exe: string | undefined): boolean {
  return TERMINAL_EMULATORS.has(executableName(exe) ?? "");
}

interface DisplayEvidence {
  connected?: true;
  unavailable?: true;
  changed?: true;
}

async function ownDisplayEvidence(holder: ProcStat, accessor: ProcAccess): Promise<DisplayEvidence> {
  const sockets = await unixSockets(accessor);
  if (!sockets) return { unavailable: true };
  const processUid = await uid(holder.pid, accessor);
  if (!processUid) return {};
  const extraPaths = await displayPaths(holder.pid, processUid, accessor);
  const inodes = await ownedSocketInodes(holder.pid, accessor);
  for (const inode of inodes) {
    const peerInode = sockets.get(inode)?.peerInode;
    const peerPath = peerInode ? sockets.get(peerInode)?.path : undefined;
    if (peerPath && isDisplayServerPath(peerPath, processUid, extraPaths)) {
      let after: ProcStat;
      try { after = await stat(holder.pid, accessor.readProc); }
      catch { return { changed: true }; }
      return sameStat(holder, after) ? { connected: true } : { changed: true };
    }
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

function withRole(
  hop: InputHop, role: InputHop["role"], image?: ImageIdentity, relay?: InputHop["relay"],
): InputHop {
  return Object.freeze({ ...hop, role,
    ...(image === undefined ? {} : { image }), ...(relay === undefined ? {} : { relay }) });
}

function withHop(topology: InputTopology, hop: InputHop): InputTopology {
  return Object.freeze({ ...topology, hops: Object.freeze([hop, ...topology.hops]) });
}

function identitiesEqual(a: InputHop, b: InputHop): boolean {
  return a.tty === b.tty && a.proof === b.proof && a.role === b.role && a.relay === b.relay &&
    (a.image === undefined && b.image === undefined || sameImage(a.image, b.image)) &&
    a.leader.pid === b.leader.pid && a.leader.starttime === b.leader.starttime &&
    a.holder.pid === b.holder.pid && a.holder.starttime === b.holder.starttime && a.holder.comm === b.holder.comm;
}

/** Compare the frozen terminal incarnation, not mutable route diagnostics. */
export function sameInputTopology(a: InputTopology, b: InputTopology): boolean {
  if (a.kind !== b.kind || a.hops.length !== b.hops.length ||
      !a.hops.every((hop, index) => identitiesEqual(hop, b.hops[index]!))) return false;
  if (a.kind === "local" && b.kind === "local") return a.basis === b.basis;
  if (a.kind === "remote" && b.kind === "remote") {
    return a.receiver.alias === b.receiver.alias && a.receiver.peer === b.receiver.peer &&
      a.receiver.port === b.receiver.port && a.receiver.user === b.receiver.user;
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
    role: "unknown",
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
  const exe = await executable(holder.pid, accessor);
  if (proof.proof === "sshd") {
    const sshHop = withRole(hop, "sshd");
    try {
      const actualHostname = accessor.hostname();
      const selectedAlias = pairedSshAlias(actualHostname);
      if (!selectedAlias) {
        return unknown(`本机 ${holderName(actualHostname)} 没有配对 SSH 策略，已拒绝远程客户端`, [sshHop]);
      }
      const connection = await sshConnection(leader.pid, accessor.readProc);
      const alias = sshAlias(await command(accessor.run, "ssh", ["-G", selectedAlias], "ssh -G"), selectedAlias);
      if (connection.peer !== alias.hostname || connection.serverPort !== alias.port) {
        return unknown(`输入持有者 ${holderName(holder.comm)} 的 SSH 对端不是 ${selectedAlias}`, [sshHop]);
      }
      return Object.freeze({
        kind: "remote",
        receiver: Object.freeze({ kind: "remote", alias: selectedAlias,
          peer: alias.hostname, port: alias.port, user: REMOTE_USER }),
        hops: Object.freeze([sshHop]),
      });
    } catch {
      return unknown(`输入持有者 ${holderName(holder.comm)} 的 SSH 对端无法验证为配对客户端`, [sshHop]);
    }
  }

  if (exe && isTmuxServer(exe.path, holder.comm)) {
    const tmuxHop = withRole(hop, "tmux", exe.identity);
    const socket = /^(.+),\d+,\d+$/u.exec(accessor.env.TMUX ?? "")?.[1];
    const pane = accessor.env.TMUX_PANE;
    if (!socket?.startsWith("/") || /[\x00\r\n]/u.test(socket) || !pane || !/^%\d+$/u.test(pane)) {
      return unknown(`输入持有者 ${holderName(holder.comm)} 缺少匹配的 tmux socket/pane`, [tmuxHop]);
    }
    try {
      const output = await command(accessor.run, "tmux",
        ["-S", socket, "display-message", "-p", "-t", pane, "-F", PANE_TTY_FORMAT], "tmux pane_tty");
      if (output.trimEnd() !== tty || output.includes("\r")) {
        return unknown(`输入持有者 ${holderName(holder.comm)} 与 ${pane} 的 pane_tty 不匹配`, [tmuxHop]);
      }
    } catch { return unknown(`输入持有者 ${holderName(holder.comm)} 的 ${pane} 无法验证`, [tmuxHop]); }
    return Object.freeze({ kind: "tmux", socket, pane, inputTty: tty, hops: Object.freeze([tmuxHop]) });
  }

  if (exe && terminalEmulatorIdentity(exe.path)) {
    const terminalHop = withRole(hop, "terminal", exe.identity);
    const display = await ownDisplayEvidence(holder, accessor);
    if (display.unavailable) {
      return unknown("无法读取本机 Unix socket 连接，不能证明终端连接到本地显示服务", [terminalHop]);
    }
    if (display.changed) {
      return unknown(`输入持有者 ${holderName(holder.comm)} 的显示证据在检查期间发生变化`, [terminalHop]);
    }
    if (display.connected) {
      return Object.freeze({ kind: "local", basis: "terminal", hops: Object.freeze([terminalHop]) });
    }
    return unknown(`终端 ${holderName(holder.comm)} 没有连接到可验证的本地显示服务，无法归属`, [terminalHop]);
  }

  const unverifiedEmulator = executableName(exe?.path);
  if (unverifiedEmulator && UNVERIFIED_EMULATORS.has(unverifiedEmulator)) {
    return unknown(`终端 ${holderName(holder.comm)} 的逐窗口显示输入契约尚未验证，无法归属`, [hop]);
  }

  const relay = relayIdentity(holder, exe?.path, args);
  if (relay) {
    const relayHop = withRole(hop, "relay", exe!.identity, relay);
    const outerTty = await link(accessor, `/proc/${holder.pid}/fd/0`);
    if (!outerTty || ttyIndex(outerTty) === undefined || outerTty === tty) {
      return unknown(`输入中继 ${holderName(holder.comm)} 没有不同的外层 PTY，无法归属`, [relayHop]);
    }
    if (!controlsTty(holder, outerTty) || holder.pgrp <= 0 || holder.tpgid !== holder.pgrp) {
      return unknown(`输入中继 ${holderName(holder.comm)} 的 fd0 不是其前台控制 PTY，无法归属`, [relayHop]);
    }
    if (visited.has(holder.pid)) return unknown("输入终端中继形成循环，无法归属", [relayHop]);
    const nextVisited = new Set(visited);
    nextVisited.add(holder.pid);
    const relayed = await inspectInput(holder.pid, accessor, depth + 1, nextVisited);
    try {
      const current = await stat(holder.pid, accessor.readProc);
      if (!sameStat(holder, current) || await link(accessor, `/proc/${holder.pid}/fd/0`) !== outerTty ||
          !await stillHolds(proof, leader, tty, accessor)) {
        return unknown(`输入中继 ${holderName(holder.comm)} 在检查期间发生变化`, [relayHop]);
      }
    } catch { return unknown(`输入中继 ${holderName(holder.comm)} 在检查期间消失`, [relayHop]); }
    return withHop(relayed, relayHop);
  }

  if (isDeniedMultiplexer(exe?.path, holder.comm)) {
    return unknown(`输入持有者 ${holderName(holder.comm)} 是不支持的多路复用器，无法归属点击来源`, [hop]);
  }
  return unknown(`输入持有者 ${holderName(holder.comm)} 没有受支持的输入来源契约，无法归属`, [hop]);
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
  for (let hopIndex = 0; hopIndex < topology.hops.length; hopIndex++) {
    const hop = topology.hops[hopIndex]!;
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

    if (hop.role !== "sshd") {
      const exe = await executable(holder.pid, accessor);
      const args = await argv(holder.pid, accessor);
      if (!exe || !sameImage(hop.image, exe.identity)) return false;
      if (hop.role === "relay") {
        const next = topology.hops[hopIndex + 1];
        const entrypointValid = hop.relay === "script" ||
          hop.relay === "codoxear" && codoxearBrokerEntrypoint(holder, args);
        if (!next || !entrypointValid || !controlsTty(holder, next.tty) ||
            holder.pgrp <= 0 || holder.tpgid !== holder.pgrp ||
            await link(accessor, `/proc/${holder.pid}/fd/0`) !== next.tty) return false;
      } else if (hop.role === "terminal") {
        // Matching dev+ino retains the already admitted Ghostty role across path renames.
      } else if (hop.role === "tmux") {
        if (!tmuxServerComm(holder.comm)) return false;
      } else return false;
    }
    currentSubject = holder.pid;
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
