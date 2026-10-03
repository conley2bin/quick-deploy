import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chmodSync, copyFileSync, mkdirSync, mkdtempSync, readlinkSync, renameSync, rmSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  ReceiverResolutionError, resolveInputReceiver, resolveInputTopology, revalidateInputTopology,
  sameInputTopology, type InputTopology, type ReceiverDependencies, type ReceiverRunResult,
} from "../src/receiver.ts";

const socket = "/fixture/socket,with-comma";
const tmuxEnv = { TMUX: `${socket},123,0`, TMUX_PANE: "%64" };
const alias = "hostname 100.123.34.64\nuser conley\nport 22\n";
const connection = "100.123.34.64 49301 100.125.138.103 22";

type FakeProcess = {
  pid: number;
  ppid: number;
  pgrp: number;
  sid: number;
  ttyNr: number;
  tpgid: number;
  comm: string;
  starttime: string;
  uid: number;
  fd0?: string;
  exe: string;
  exeDeleted: boolean;
  imageDev: bigint;
  imageIno: bigint;
  env: Record<string, string>;
  rawEnvironment?: Buffer;
  argv: string[];
  masters: Map<string, number>;
  sockets: Map<string, string>;
  fdReadable: boolean;
};
type Client = { pid: number; tty: string; session: string; flags: string; pane: string };

function ptsDevice(index: number): number {
  return (136 << 8) | (index & 0xff) | ((index & 0xfff00) << 12);
}

function statBytes(process: FakeProcess): Buffer {
  const fields = Array.from({ length: 50 }, () => "0");
  fields[0] = "S";
  fields[1] = String(process.ppid);
  fields[2] = String(process.pgrp);
  fields[3] = String(process.sid);
  fields[4] = String(process.ttyNr);
  fields[5] = String(process.tpgid);
  fields[19] = process.starttime;
  return Buffer.from(`${process.pid} (${process.comm}) ${fields.join(" ")}\n`);
}

function environment(values: Record<string, string>): Buffer {
  return Buffer.from(Object.entries({ PATH: "/fixture", ...values }).map(([key, value]) => `${key}=${value}`).join("\0") + "\0");
}

class Fixture {
  readonly processes = new Map<number, FakeProcess>();
  readonly clients: Client[] = [];
  readonly calls: Array<{ program: string; args: readonly string[] }> = [];
  readonly procPaths: string[] = [];
  readonly dirPaths: string[] = [];
  readonly pathStats = new Map<string, { dev: bigint; ino: bigint }>();
  env: NodeJS.ProcessEnv = {};
  sshConfig = alias;
  paneTty = "/dev/pts/12";
  ssRows: string[] = [];
  fail?: (program: string, args: readonly string[]) => ReceiverRunResult | Error | undefined;
  onRead?: (path: string, count: number) => void;
  onReadDir?: (path: string, count: number) => void;
  onList?: (count: number) => void;
  onDisplay?: (count: number, client: Client) => string | undefined;
  private readonly reads = new Map<string, number>();
  private readonly dirReads = new Map<string, number>();
  private listCount = 0;
  private displayCount = 0;
  private nextInode = 100_000;

  constructor() {
    this.add({ pid: 1, ppid: 0, sid: 1, comm: "systemd", fd0: "/dev/null", pgrp: 1, tpgid: -1 });
  }

  add(value: Partial<FakeProcess> & Pick<FakeProcess, "pid" | "ppid" | "sid" | "comm">): FakeProcess {
    const pgrp = value.pgrp ?? value.sid;
    const fd0Index = /^\/dev\/pts\/(\d+)$/u.exec(value.fd0 ?? "")?.[1];
    const process: FakeProcess = {
      starttime: String(value.pid * 10), uid: 1000, exe: "/usr/bin/fixture-unknown", exeDeleted: false,
      imageDev: 1n, imageIno: BigInt(value.pid * 100), env: {}, argv: [value.comm],
      masters: new Map(), sockets: new Map(), fdReadable: true,
      ttyNr: value.ttyNr ?? (fd0Index === undefined ? 0 : ptsDevice(Number(fd0Index))),
      tpgid: value.tpgid ?? pgrp,
      ...value, pgrp,
    };
    this.processes.set(process.pid, process);
    return process;
  }

  master(process: FakeProcess, index: number, fd = "9"): void {
    process.masters.set(fd, index);
  }

  display(
    process: FakeProcess, path = `/run/user/${process.uid}/wayland-0`, fd = "20",
    forcedInodes?: readonly [number, number],
  ): void {
    const [clientNumber, serverNumber] = forcedInodes ?? [this.nextInode++, this.nextInode++];
    const client = String(clientNumber), server = String(serverNumber);
    const ssInode = (value: number) => String(value > 0x7fff_ffff ? value - 0x1_0000_0000 : value);
    process.sockets.set(fd, client);
    this.ssRows.push(
      `u_str ESTAB 0 0 * ${ssInode(clientNumber)} * ${ssInode(serverNumber)}`,
      `u_str ESTAB 0 0 ${path} ${ssInode(serverNumber)} * ${ssInode(clientNumber)}`,
    );
  }

  dependencies(extras: Partial<ReceiverDependencies> = {}): ReceiverDependencies {
    const base: ReceiverDependencies = {
      env: this.env,
      run: async (program, args) => {
        this.calls.push({ program, args });
        const failure = this.fail?.(program, args);
        if (failure instanceof Error) throw failure;
        if (failure) return failure;
        if (program === "ss") {
          assert.deepEqual(args, ["-xH", "-a", "-n"]);
          return { code: 0, stdout: this.ssRows.join("\n") + (this.ssRows.length ? "\n" : ""), stderr: "" };
        }
        if (program === "ssh") {
          assert.deepEqual(args, ["-G", "conley-Y9000p"]);
          return { code: 0, stdout: this.sshConfig, stderr: "" };
        }
        assert.equal(program, "tmux");
        assert.deepEqual(args.slice(0, 2), ["-S", socket]);
        const command = args[2];
        if (command === "list-clients") {
          this.onList?.(++this.listCount);
          return { code: 0, stdout: this.clients.map((client) =>
            `pid=${client.pid}|tty=${client.tty}|session=${client.session}|flags=${client.flags}\n`).join(""), stderr: "" };
        }
        if (command === "display-message" && args.includes("-c")) {
          const tty = args[5], session = args[7];
          const client = this.clients.find((item) => item.tty === tty && item.session === session);
          assert.ok(client, "display-message must use the exact client's tty and session");
          return { code: 0, stdout: this.onDisplay?.(++this.displayCount, client) ??
            `pid=${client.pid}|tty=${client.tty}|session=${client.session}|pane=${client.pane}\n`, stderr: "" };
        }
        if (command === "display-message") {
          assert.deepEqual(args.slice(2), ["display-message", "-p", "-t", "%64", "-F", "#{pane_tty}"]);
          return { code: 0, stdout: `${this.paneTty}\n`, stderr: "" };
        }
        assert.fail(`unexpected command ${program} ${args.join(" ")}`);
      },
      readProc: async (path) => {
        this.procPaths.push(path);
        const count = (this.reads.get(path) ?? 0) + 1;
        this.reads.set(path, count);
        this.onRead?.(path, count);
        let match = /^\/proc\/(\d+)\/(stat|status|cmdline|environ)$/u.exec(path);
        if (match) {
          const process = this.processes.get(Number(match[1]));
          if (!process) throw new Error("ENOENT");
          if (match[2] === "stat") return statBytes(process);
          if (match[2] === "status") return Buffer.from(`Name:\t${process.comm}\nUid:\t${process.uid}\t${process.uid}\t${process.uid}\t${process.uid}\n`);
          if (match[2] === "cmdline") return Buffer.from(process.argv.join("\0") + "\0");
          return process.rawEnvironment ?? environment(process.env);
        }
        match = /^\/proc\/(\d+)\/fdinfo\/(\d+)$/u.exec(path);
        if (match) {
          const process = this.processes.get(Number(match[1]));
          const index = process?.masters.get(match[2]!);
          if (index === undefined) throw new Error("ENOENT");
          return Buffer.from(`pos:\t0\nflags:\t02100002\ntty-index:\t${index}\n`);
        }
        throw new Error(`unexpected proc read ${path}`);
      },
      readDir: async (path) => {
        this.dirPaths.push(path);
        const count = (this.dirReads.get(path) ?? 0) + 1;
        this.dirReads.set(path, count);
        this.onReadDir?.(path, count);
        if (path === "/proc") return [...this.processes.keys()].map(String);
        const match = /^\/proc\/(\d+)\/fd$/u.exec(path);
        if (!match) throw new Error(`unexpected directory ${path}`);
        const process = this.processes.get(Number(match[1]));
        if (!process || !process.fdReadable) throw new Error("EACCES");
        return [...new Set([
          ...(process.fd0 ? ["0"] : []), ...process.masters.keys(), ...process.sockets.keys(),
        ])];
      },
      readLink: async (path) => {
        let match = /^\/proc\/(\d+)\/exe$/u.exec(path);
        if (match) {
          const process = this.processes.get(Number(match[1]));
          if (!process) throw new Error("ENOENT");
          return process.exe + (process.exeDeleted ? " (deleted)" : "");
        }
        match = /^\/proc\/(\d+)\/fd\/(\d+)$/u.exec(path);
        if (!match) throw new Error(`unexpected link ${path}`);
        const process = this.processes.get(Number(match[1]));
        if (!process) throw new Error("ENOENT");
        if (match[2] === "0" && process.fd0) return process.fd0;
        if (process.masters.has(match[2]!)) return "/dev/pts/ptmx";
        const inode = process.sockets.get(match[2]!);
        if (inode) return `socket:[${inode}]`;
        throw new Error("ENOENT");
      },
      statPath: async (path) => {
        const match = /^\/proc\/(\d+)\/exe$/u.exec(path);
        if (match) {
          const process = this.processes.get(Number(match[1]));
          if (!process) throw new Error("ENOENT");
          return { dev: process.imageDev, ino: process.imageIno };
        }
        const literal = this.pathStats.get(path);
        if (literal) return literal;
        const error = new Error(`ENOENT: ${path}`) as NodeJS.ErrnoException;
        error.code = "ENOENT";
        throw error;
      },
    };
    return { ...base, ...extras };
  }
}

function directSsh(): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 4001, sid: 4001, comm: "pi", fd0: "/dev/pts/12",
    env: { SSH_CONNECTION: connection, SSH_CLIENT: "stale", SSH_TTY: "/dev/pts/12" } });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12",
    env: { SSH_CONNECTION: connection, SSH_CLIENT: "100.123.34.64 49301 22", SSH_TTY: "/dev/pts/12" } });
  f.add({ pid: 3001, ppid: 2001, sid: 2001, comm: "sshd", argv: ["sshd: conley@pts/12  "],
    fdReadable: false, tpgid: -1 });
  f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd", tpgid: -1 });
  return f;
}

function localDisplay(withSocket = true): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 4001, sid: 4001, comm: "pi", fd0: "/dev/pts/12",
    env: { SSH_CONNECTION: "stale value", SSH_CLIENT: "stale", SSH_TTY: "/dev/pts/99" } });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12" });
  const terminal = f.add({ pid: 3001, ppid: 2500, sid: 2500, comm: "ghostty", exe: "/usr/bin/ghostty", env: {
    GDK_BACKEND: "x11", DISPLAY: "localhost:10.0", WAYLAND_DISPLAY: "stale-wayland",
  } });
  f.master(terminal, 12);
  if (withSocket) f.display(terminal);
  f.add({ pid: 2500, ppid: 2001, sid: 2500, comm: "zsh" });
  f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd", tpgid: -1 });
  return f;
}

function unknownHolder(): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 4001, sid: 4001, comm: "pi", fd0: "/dev/pts/12" });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12" });
  const holder = f.add({ pid: 3001, ppid: 1, sid: 3001, comm: "ttyd-like", exe: "/usr/bin/ttyd", tpgid: -1 });
  f.master(holder, 12);
  return f;
}

function relay(outer: "ssh" | "display" | "tmux", relayName = "script"): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 3001, sid: 5000, comm: "pi", fd0: "/dev/pts/13" });
  const transparent = f.add({ pid: 3001, ppid: 4001, sid: 4001, pgrp: 4001, tpgid: 4001,
    comm: relayName, fd0: "/dev/pts/12",
    ...(relayName === "script" ? { exe: "/usr/bin/script", argv: ["/usr/bin/script"] } :
      relayName === "codoxear-broker" ? {
        exe: "/usr/bin/python3.12",
        argv: ["/fixture/venv/bin/python", "/fixture/venv/bin/codoxear-broker", "--"],
      } : {}),
  });
  f.master(transparent, 13);
  f.add({ pid: 4001, ppid: 2001, sid: 4001, pgrp: 4001, tpgid: 4001,
    comm: "zsh", fd0: "/dev/pts/12", env: outer === "ssh" ? { SSH_CONNECTION: connection } : {} });
  if (outer === "ssh") {
    f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd", argv: ["sshd: conley@pts/12 "],
      fdReadable: false, tpgid: -1 });
  } else if (outer === "display") {
    const terminal = f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "ghostty", exe: "/usr/bin/ghostty", tpgid: -1 });
    f.master(terminal, 12);
    f.display(terminal);
  } else {
    const tmux = f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "tmux: server", exe: "/usr/bin/tmux", tpgid: -1 });
    f.master(tmux, 12);
    f.env = tmuxEnv;
    f.paneTty = "/dev/pts/12";
  }
  return f;
}

function addTmuxClient(f: Fixture, holder: "ssh" | "display" | "tmux"): void {
  f.env = tmuxEnv;
  f.clients.push({ pid: 6000, tty: "/dev/pts/20", session: "$8", flags: "attached,UTF-8", pane: "%64" });
  f.add({ pid: 6000, ppid: 4100, sid: 4100, comm: "tmux: client", fd0: "/dev/pts/20" });
  f.add({ pid: 4100, ppid: 3100, sid: 4100, comm: "zsh", fd0: "/dev/pts/20",
    env: holder === "ssh" ? { SSH_CONNECTION: connection } : {} });
  if (holder === "ssh") {
    f.add({ pid: 3100, ppid: 1, sid: 3100, comm: "sshd", argv: ["sshd: conley@pts/20 "],
      fdReadable: false, tpgid: -1 });
  } else if (holder === "display") {
    const terminal = f.add({ pid: 3100, ppid: 1, sid: 3100, comm: "ghostty", exe: "/usr/bin/ghostty", tpgid: -1 });
    f.master(terminal, 20);
    f.display(terminal, "@/tmp/.X11-unix/X8");
  } else {
    const outer = f.add({ pid: 3100, ppid: 1, sid: 3100, comm: "tmux: server", exe: "/usr/bin/tmux", tpgid: -1 });
    f.master(outer, 20);
  }
}

function tmuxClient(holder: "ssh" | "display" | "tmux"): Fixture {
  const f = new Fixture();
  f.paneTty = "/dev/pts/99"; // Source Pi pane; nested client's outer pane must not match it.
  addTmuxClient(f, holder);
  return f;
}

function assertUnknown(topology: InputTopology, pattern: RegExp): void {
  assert.equal(topology.kind, "unknown");
  if (topology.kind === "unknown") assert.match(topology.diagnostic, pattern);
}

async function firstOutputLine(stream: NodeJS.ReadableStream, timeoutMs = 5000): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = "";
    const timer = setTimeout(() => finish(new Error("child output timeout")), timeoutMs);
    const onData = (chunk: Buffer | string) => {
      data += chunk.toString();
      const end = data.indexOf("\n");
      if (end >= 0) finish(data.slice(0, end));
    };
    const finish = (result: string | Error) => {
      clearTimeout(timer);
      stream.removeListener("data", onData);
      result instanceof Error ? reject(result) : resolve(result);
    };
    stream.on("data", onData);
  });
}

const KERNEL_HOLDER_SCRIPT = [
  "import json, os, pty, select, socket, time",
  "path=f'/tmp/.X11-unix/X{os.getpid()}'",
  "server=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)",
  "server.bind(path); server.listen(1)",
  "client=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); client.connect(path)",
  "peer,_=server.accept()",
  "ready_r,ready_w=os.pipe()",
  "pid,master=pty.fork()",
  "if pid == 0:",
  "  os.close(ready_r)",
  "  ready=os.isatty(0) and os.ttyname(0).startswith('/dev/pts/') and os.getsid(0)==os.getpid()",
  "  os.write(ready_w, b'R' if ready else b'E'); os.close(ready_w)",
  "  if not ready: os._exit(2)",
  "  time.sleep(60); os._exit(0)",
  "os.close(ready_w)",
  "readable,_,_=select.select([ready_r],[],[],5.0)",
  "if not readable: raise TimeoutError('forkpty child readiness timeout')",
  "marker=os.read(ready_r,1); os.close(ready_r)",
  "if marker != b'R': raise RuntimeError('forkpty child tty/session setup failed')",
  "print(json.dumps({'subject':pid,'display':path}), flush=True)",
  "time.sleep(60)",
].join("\n");

async function launchKernelHolder(
  t: { after(callback: () => void): void }, executable: string, root: string,
): Promise<{ holder: ReturnType<typeof spawn>; subject: number }> {
  mkdirSync("/tmp/.X11-unix", { recursive: true, mode: 0o1777 });
  const holder = spawn(executable, ["-c", KERNEL_HOLDER_SCRIPT], { stdio: ["ignore", "pipe", "pipe"] });
  const record = JSON.parse(await firstOutputLine(holder.stdout!)) as { subject: number; display: string };
  t.after(() => {
    try { process.kill(record.subject, "SIGKILL"); } catch { /* exited */ }
    try { holder.kill("SIGKILL"); } catch { /* exited */ }
    try { unlinkSync(record.display); } catch { /* removed */ }
    rmSync(root, { recursive: true, force: true });
  });
  return { holder, subject: record.subject };
}

async function receiverFails(f: Fixture, origin: number | "unknown", code: string): Promise<void> {
  await assert.rejects(resolveInputReceiver(origin, f.dependencies()), (error: unknown) => {
    assert.ok(error instanceof ReceiverResolutionError);
    assert.equal(error.code, code);
    assert.doesNotMatch(error.message, /TOKEN=|must-never-appear|SSH_CONNECTION=/u);
    return true;
  });
}

test("direct sshd-held PTY resolves the paired receiver and accepts sshd-session/IPv6", async () => {
  const f = directSsh();
  const route = await resolveInputTopology(5000, f.dependencies());
  assert.deepEqual(route.kind === "remote" ? route.receiver : route,
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
  assert.equal(route.hops[0]?.proof, "sshd");
  assert.ok(f.procPaths.includes("/proc/4001/environ"));

  const sessionBinary = directSsh();
  Object.assign(sessionBinary.processes.get(3001)!, {
    comm: "sshd-session", argv: ["sshd-session: conley@pts/12  "],
  });
  assert.equal((await resolveInputTopology(5000, sessionBinary.dependencies())).kind, "remote");

  const ipv6 = directSsh();
  ipv6.sshConfig = "hostname 2001:db8::64\nuser conley\nport 2222\n";
  ipv6.processes.get(4001)!.env.SSH_CONNECTION = "2001:db8::64 49301 2001:db8::10 2222";
  const ipv6Route = await resolveInputTopology(5000, ipv6.dependencies());
  assert.deepEqual(ipv6Route.kind === "remote" ? ipv6Route.receiver : ipv6Route,
    { kind: "remote", peer: "2001:db8::64", port: 2222, user: "conley" });
});

test("unpaired peer and malformed SSH environment/config stay unknown without leaking values", async () => {
  const unpaired = directSsh();
  unpaired.processes.get(4001)!.env.SSH_CONNECTION = "100.123.34.65 49301 100.125.138.103 22";
  assertUnknown(await resolveInputTopology(5000, unpaired.dependencies()), /SSH 对端/u);

  for (const bytes of [Buffer.from("PATH=fixture\0"), Buffer.from("SSH_CONNECTION=bad\0"),
    Buffer.from(`SSH_CONNECTION=${connection}`), Buffer.from(`SSH_CONNECTION=${connection}\0SSH_CONNECTION=${connection}\0TOKEN=secret\0`)]) {
    const f = directSsh(); f.processes.get(4001)!.rawEnvironment = bytes;
    const topology = await resolveInputTopology(5000, f.dependencies());
    assertUnknown(topology, /SSH 对端/u);
    assert.doesNotMatch(topology.kind === "unknown" ? topology.diagnostic : "", /TOKEN|secret|SSH_CONNECTION=/u);
  }
  for (const config of ["hostname conley-Y9000p\nuser conley\nport 22\n",
    "hostname 100.123.34.64\nuser other\nport 22\n",
    "hostname 100.123.34.64\nhostname 100.123.34.64\nuser conley\nport 22\n"]) {
    const f = directSsh(); f.sshConfig = config;
    assertUnknown(await resolveInputTopology(5000, f.dependencies()), /SSH 对端/u);
  }
});

test("recognized terminal's own Wayland peer proves local despite stale SSH ancestry and foreground SSH fd0", async () => {
  const f = localDisplay(true);
  const terminal = f.processes.get(3001)!;
  terminal.comm = "not-a-trusted-name"; // Executable realpath, not comm, owns the contract.
  terminal.fd0 = "/dev/pts/20";
  terminal.ttyNr = ptsDevice(20);
  terminal.pgrp = 3001;
  terminal.tpgid = 3001;
  const route = await resolveInputTopology(5000, f.dependencies());
  assert.equal(route.kind, "local");
  if (route.kind === "local") assert.equal(route.basis, "terminal");
  assert.equal(route.hops[0]?.role, "terminal");
  assert.equal(f.calls.filter(({ program }) => program === "ss").length, 1);
  assert.ok(f.calls.every(({ program }) => program !== "ssh"));
});

test("signed ss inode values above 2^31 map to owned display peers", async () => {
  const f = localDisplay(false);
  f.display(f.processes.get(3001)!, "/run/user/1000/wayland-0", "21",
    [3_000_000_000, 3_000_000_001]);
  assert.equal((await resolveInputTopology(5000, f.dependencies())).kind, "local");
});

test("forced-X11 forwarded Ghostty with stale WAYLAND_DISPLAY is not local without a display socket", async () => {
  const f = localDisplay(false);
  assertUnknown(await resolveInputTopology(5000, f.dependencies()), /ghostty.*没有连接到/u);
});

test("ttyd-style network holder cannot inherit a launching terminal's display authority and stays unknown after reparenting", async () => {
  const f = unknownHolder();
  const holder = f.processes.get(3001)!;
  holder.fd0 = "/dev/null";
  holder.ttyNr = 0;
  holder.ppid = 2500;
  const ghostty = f.add({ pid: 2500, ppid: 1, sid: 2500, comm: "ghostty", exe: "/usr/bin/ghostty", tpgid: -1 });
  f.display(ghostty, "@/tmp/.X11-unix/X17");
  const before = await resolveInputTopology(5000, f.dependencies());
  assertUnknown(before, /ttyd-like.*没有受支持的输入来源契约/u);
  assert.equal(f.calls.filter(({ program }) => program === "ss").length, 0,
    "an unrelated ancestor's display sockets must not even be inspected");
  holder.ppid = 1;
  const after = await resolveInputTopology(5000, f.dependencies());
  assertUnknown(after, /ttyd-like.*没有受支持的输入来源契约/u);
  assert.equal(await revalidateInputTopology(5000, before, f.dependencies()), false);
});

test("emulator authority is keyed by executable realpath, never comm alone", async () => {
  const allowed = localDisplay(true);
  allowed.processes.get(3001)!.comm = "arbitrary-comm";
  assert.equal((await resolveInputTopology(5000, allowed.dependencies())).kind, "local");

  const forged = localDisplay(true);
  forged.processes.get(3001)!.exe = "/usr/bin/python3";
  assertUnknown(await resolveInputTopology(5000, forged.dependencies()), /ghostty.*没有受支持的输入来源契约/u);

  for (const name of ["gnome-terminal-server", "kitty", "alacritty", "konsole", "xterm", "foot",
    "tilix", "xfce4-terminal", "wezterm-gui"]) {
    const unsupported = localDisplay(true);
    Object.assign(unsupported.processes.get(3001)!, { comm: name, exe: `/usr/bin/${name}` });
    assertUnknown(await resolveInputTopology(5000, unsupported.dependencies()), /逐窗口显示输入契约尚未验证/u);
  }
});

test("holder with no display socket and no SSH environment is unknown; there is no legacy fallback", async () => {
  const route = await resolveInputTopology(5000, unknownHolder().dependencies());
  assertUnknown(route, /ttyd-like.*没有受支持的输入来源契约/u);
});

test("identity-bound script/Codoxear relays unwrap to SSH, display, and tmux", async () => {
  const ssh = await resolveInputTopology(5000, relay("ssh").dependencies());
  assert.equal(ssh.kind, "remote");
  assert.deepEqual(ssh.hops.map((hop) => [hop.holder.comm, hop.role]), [["script", "relay"], ["sshd", "sshd"]]);

  const local = await resolveInputTopology(5000, relay("display", "codoxear-broker").dependencies());
  assert.equal(local.kind, "local");
  assert.deepEqual(local.hops.map((hop) => [hop.holder.comm, hop.role]),
    [["codoxear-broker", "relay"], ["ghostty", "terminal"]]);

  const moduleBroker = relay("tmux", "codoxear-broker");
  Object.assign(moduleBroker.processes.get(3001)!, {
    comm: "python3", exe: "/usr/bin/python3.12", argv: ["python3", "-m", "codoxear", "broker"],
  });
  assert.equal((await resolveInputTopology(5000, moduleBroker.dependencies())).kind, "tmux");

  const shared = relay("tmux", "script");
  const multiplexed = await resolveInputTopology(5000, shared.dependencies());
  assert.equal(multiplexed.kind, "tmux");
  assert.deepEqual(multiplexed.hops.map((hop) => hop.holder.comm), ["script", "tmux: server"]);
  addTmuxClient(shared, "ssh");
  assert.deepEqual(await resolveInputReceiver(6000, shared.dependencies()),
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
});

test("relay requires matching controlling tty and foreground pgrp", async () => {
  const background = relay("tmux");
  background.processes.get(3001)!.tpgid = 9999;
  assertUnknown(await resolveInputTopology(5000, background.dependencies()), /fd0 不是其前台控制 PTY/u);

  const redirected = relay("tmux");
  redirected.processes.get(3001)!.ttyNr = ptsDevice(99);
  assertUnknown(await resolveInputTopology(5000, redirected.dependencies()), /fd0 不是其前台控制 PTY/u);
});

test("relay controlling-tty decoding supports pts minor above 255", async () => {
  const f = relay("tmux", "script");
  const relayProcess = f.processes.get(3001)!;
  relayProcess.fd0 = "/dev/pts/281";
  relayProcess.ttyNr = ptsDevice(281);
  f.processes.get(4001)!.fd0 = "/dev/pts/281";
  const tmux = f.processes.get(2001)!;
  tmux.masters.clear();
  f.master(tmux, 281);
  f.paneTty = "/dev/pts/281";
  assert.equal((await resolveInputTopology(5000, f.dependencies())).kind, "tmux");
  relayProcess.ttyNr = ptsDevice(280);
  assertUnknown(await resolveInputTopology(5000, f.dependencies()), /fd0 不是其前台控制 PTY/u);
});

test("unknown foreground program, forged script comm, and arbitrary Python are not relays", async () => {
  const unknown = relay("tmux", "unknown-forwarder");
  assertUnknown(await resolveInputTopology(5000, unknown.dependencies()), /unknown-forwarder.*没有受支持的输入来源契约/u);
  assert.equal(unknown.calls.filter(({ program }) => program === "tmux").length, 0);

  const forgedScript = relay("tmux", "script");
  forgedScript.processes.get(3001)!.exe = "/tmp/not-util-linux-script";
  assertUnknown(await resolveInputTopology(5000, forgedScript.dependencies()), /script.*没有受支持的输入来源契约/u);

  const arbitraryPython = relay("tmux", "codoxear-broker");
  arbitraryPython.processes.get(3001)!.argv = ["python3", "arbitrary.py"];
  assertUnknown(await resolveInputTopology(5000, arbitraryPython.dependencies()), /codoxear-broker.*没有受支持的输入来源契约/u);
});

test("known non-tmux multiplexers are denied before relay recursion", async () => {
  for (const name of ["screen", "SCREEN", "zellij", "abduco", "dtach", "mosh-server", "dvtm"]) {
    const f = relay("display", name);
    assertUnknown(await resolveInputTopology(5000, f.dependencies()), /不支持的多路复用器/u);
  }
});

test("ss failure leaves a recognized terminal unknown", async () => {
  const f = localDisplay(true);
  f.fail = (program) => program === "ss" ? { code: 1, stdout: "secret", stderr: "TOKEN" } : undefined;
  assertUnknown(await resolveInputTopology(5000, f.dependencies()), /无法读取本机 Unix socket/u);
});

test("stdin outside a PTY is unknown", async () => {
  const f = unknownHolder();
  f.processes.get(5000)!.fd0 = "/dev/null";
  assertUnknown(await resolveInputTopology(5000, f.dependencies()), /stdin 不是可识别的 PTY/u);
});

test("candidate failure uses a unique bounded scan and scan deadline returns unknown", async () => {
  const f = unknownHolder();
  f.processes.get(3001)!.masters.clear();
  const actual = f.add({ pid: 3002, ppid: 1, sid: 3002, comm: "actual-holder", tpgid: -1 });
  f.master(actual, 12);
  const route = await resolveInputTopology(5000, f.dependencies());
  assert.equal(route.hops[0]?.holder.pid, 3002);
  assertUnknown(route, /actual-holder.*没有受支持的输入来源契约/u);

  const slow = unknownHolder();
  slow.processes.get(3001)!.masters.clear();
  const base = slow.dependencies();
  const result = await resolveInputTopology(5000, {
    ...base,
    scanTimeoutMs: 5,
    readDir: async (path) => path === "/proc" ? new Promise<readonly string[]>(() => {}) : base.readDir!(path),
  });
  assertUnknown(result, /扫描超过 5 ms/u);
});

test("cheap frozen-chain revalidation never scans proc and detects holder replacement", async () => {
  const f = localDisplay(true);
  const route = await resolveInputTopology(5000, f.dependencies());
  f.dirPaths.length = 0;
  assert.equal(await revalidateInputTopology(5000, route, f.dependencies()), true);
  assert.ok(!f.dirPaths.includes("/proc"), "revalidation may inspect holder fds but must not scan process inventory");
  assert.equal(f.calls.filter(({ program }) => program === "ss").length, 1,
    "revalidation must not invoke ss again");
  f.processes.get(3001)!.starttime = "999999";
  assert.equal(await revalidateInputTopology(5000, route, f.dependencies()), false);
});

test("deleted executable annotation preserves image identity while a literal suffix stays literal", async () => {
  const deleted = localDisplay(true);
  const route = await resolveInputTopology(5000, deleted.dependencies());
  assert.equal(route.kind, "local");
  deleted.processes.get(3001)!.exeDeleted = true;
  assert.equal(await revalidateInputTopology(5000, route, deleted.dependencies()), true);
  const fresh = await resolveInputTopology(5000, deleted.dependencies());
  assert.equal(fresh.kind, "local");
  assert.equal(sameInputTopology(route, fresh), true);

  const literal = localDisplay(true);
  const process = literal.processes.get(3001)!;
  process.exe = "/tmp/ghostty";
  process.exeDeleted = true;
  literal.pathStats.set("/tmp/ghostty (deleted)", { dev: process.imageDev, ino: process.imageIno });
  assertUnknown(await resolveInputTopology(5000, literal.dependencies()), /没有受支持的输入来源契约/u);
});

test("kernel literal ghostty (deleted) filename is not admitted", { timeout: 15_000 }, async (t) => {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-exe-literal-"));
  const executable = join(root, "ghostty (deleted)");
  copyFileSync("/usr/bin/python3", executable);
  chmodSync(executable, 0o755);
  const { subject } = await launchKernelHolder(t, executable, root);
  assert.match(readlinkSync(`/proc/${subject}/exe`), /ghostty \(deleted\)$/u);
  assertUnknown(await resolveInputTopology(subject, { env: {} }), /没有受支持的输入来源契约/u);
});

test("kernel rename-aside and replacement preserve cached admitted image", { timeout: 15_000 }, async (t) => {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-exe-replace-"));
  const executable = join(root, "ghostty");
  const aside = join(root, "ghostty.old");
  const python = "/usr/bin/python3";
  copyFileSync(python, executable);
  chmodSync(executable, 0o755);
  const { holder, subject } = await launchKernelHolder(t, executable, root);
  assert.equal(holder.pid !== undefined && holder.pid > 0, true);
  const route = await resolveInputTopology(subject, { env: {} });
  assert.equal(route.kind, "local");
  assert.equal(route.hops[0]?.holder.pid, holder.pid);

  renameSync(executable, aside);
  assert.match(readlinkSync(`/proc/${holder.pid}/exe`), /ghostty\.old$/u);
  assert.equal(await revalidateInputTopology(subject, route, { env: {} }), true);
  assertUnknown(await resolveInputTopology(subject, { env: {} }), /没有受支持的输入来源契约/u);
  renameSync(aside, executable);
  assert.equal(await revalidateInputTopology(subject, route, { env: {} }), true);

  unlinkSync(executable);
  copyFileSync(python, executable);
  chmodSync(executable, 0o755);
  assert.match(readlinkSync(`/proc/${holder.pid}/exe`), /ghostty \(deleted\)$/u);
  assert.equal(await revalidateInputTopology(subject, route, { env: {} }), true);
  const fresh = await resolveInputTopology(subject, { env: {} });
  assert.equal(fresh.kind, "local");
  assert.equal(sameInputTopology(route, fresh), true);

  const fixture = localDisplay(true);
  const frozen = await resolveInputTopology(5000, fixture.dependencies());
  fixture.processes.get(3001)!.imageIno += 1n;
  assert.equal(await revalidateInputTopology(5000, frozen, fixture.dependencies()), false,
    "an actual image change must still invalidate the admitted role");
});

test("relay revalidation retains identity, fd0, controlling tty, and foreground requirements", async () => {
  const f = relay("tmux", "script");
  const route = await resolveInputTopology(5000, f.dependencies());
  assert.equal(route.kind, "tmux");
  assert.equal(await revalidateInputTopology(5000, route, f.dependencies()), true);
  f.processes.get(3001)!.tpgid = 9999;
  assert.equal(await revalidateInputTopology(5000, route, f.dependencies()), false);
  f.processes.get(3001)!.tpgid = 4001;
  f.processes.get(3001)!.ttyNr = ptsDevice(99);
  assert.equal(await revalidateInputTopology(5000, route, f.dependencies()), false);
});

test("tmux marked client resolves display/sshd and refuses a nested tmux client", async () => {
  const local = tmuxClient("display");
  assert.equal(await resolveInputReceiver(6000, local.dependencies()), "local");
  const remote = tmuxClient("ssh");
  assert.deepEqual(await resolveInputReceiver(6000, remote.dependencies()),
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
  const nested = tmuxClient("tmux");
  await receiverFails(nested, 6000, "input-unattributed");
});

test("missing origin or source socket/pane fails before commands or proc access", async () => {
  for (const origin of ["unknown", 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1] as const) {
    const f = tmuxClient("ssh"); await receiverFails(f, origin, "invalid-origin");
    assert.deepEqual(f.calls, []); assert.deepEqual(f.procPaths, []);
  }
  for (const invalid of [{}, { TMUX: `${socket},123,0` }, { TMUX_PANE: "%64" },
    { TMUX: "relative/socket,1,0", TMUX_PANE: "%64" }, { TMUX: `${socket},1,0`, TMUX_PANE: "%oops" }]) {
    const f = tmuxClient("ssh"); f.env = invalid; await receiverFails(f, 6000, "missing-tmux");
    assert.deepEqual(f.calls, []); assert.deepEqual(f.procPaths, []);
  }
});

test("foreign, duplicate, detached, and second-inventory mutations cannot yield a receiver", async () => {
  const absent = tmuxClient("ssh"); absent.clients.length = 0; await receiverFails(absent, 6000, "client-not-found");
  const duplicate = tmuxClient("ssh"); duplicate.clients.push({ ...duplicate.clients[0]! });
  await receiverFails(duplicate, 6000, "ambiguous-client");
  const detached = tmuxClient("ssh"); detached.clients[0]!.flags = "UTF-8";
  await receiverFails(detached, 6000, "client-mismatch");
  const stale = tmuxClient("ssh"); stale.onList = (count) => { if (count === 2) stale.clients.length = 0; };
  await receiverFails(stale, 6000, "client-not-found");
  const moved = tmuxClient("ssh"); moved.onList = (count) => { if (count === 2) moved.clients[0]!.session = "$9"; };
  await receiverFails(moved, 6000, "client-mismatch");
});

test("wrong pane and second-view identity/pane mutations are rejected", async () => {
  const wrong = tmuxClient("ssh"); wrong.clients[0]!.pane = "%57";
  await receiverFails(wrong, 6000, "pane-mismatch");
  const switched = tmuxClient("ssh"); switched.onDisplay = (count, client) =>
    `pid=${client.pid}|tty=${client.tty}|session=${client.session}|pane=${count === 2 ? "%57" : "%64"}\n`;
  await receiverFails(switched, 6000, "pane-mismatch");
  for (const replacement of ["pid=7000", "tty=/dev/pts/999", "session=$9"] as const) {
    const f = tmuxClient("ssh");
    f.onDisplay = (_count, client) => {
      const output = `pid=${client.pid}|tty=${client.tty}|session=${client.session}|pane=%64\n`;
      return output.replace(new RegExp(`${replacement.split("=")[0]}=[^|\\n]+`, "u"), replacement);
    };
    await receiverFails(f, 6000, "client-mismatch");
  }
});

test("mid-inspection origin and ancestor replacements reject instead of misclassifying", async () => {
  const early = tmuxClient("ssh");
  early.onRead = (path, count) => { if (path === "/proc/6000/stat" && count === 2) early.processes.get(6000)!.starttime = "999"; };
  await receiverFails(early, 6000, "proc-changed");

  const late = tmuxClient("ssh");
  late.onRead = (path, count) => { if (path === "/proc/6000/stat" && count === 5) late.processes.get(6000)!.starttime = "999"; };
  await receiverFails(late, 6000, "proc-changed");

  const ancestor = tmuxClient("ssh");
  ancestor.onRead = (path, count) => { if (path === "/proc/4100/stat" && count === 4) ancestor.processes.get(4100)!.starttime = "999"; };
  await receiverFails(ancestor, 6000, "proc-changed");
});

test("unreadable, truncated, cyclic, or severed tmux-client ancestry cannot imply local", async () => {
  const unreadable = tmuxClient("ssh"); unreadable.processes.delete(4100);
  await receiverFails(unreadable, 6000, "proc-unavailable");
  const truncated = tmuxClient("ssh");
  const base = truncated.dependencies();
  await assert.rejects(resolveInputReceiver(6000, {
    ...base,
    readProc: async (path) => path === "/proc/4100/stat" ? Buffer.from("4100 (zsh) S 3100 0\n") : base.readProc!(path),
  }), (error: unknown) => error instanceof ReceiverResolutionError && error.code === "proc-incomplete");
  const cyclic = tmuxClient("ssh"); cyclic.processes.get(4100)!.ppid = 6000;
  await receiverFails(cyclic, 6000, "proc-incomplete");
  const severed = tmuxClient("ssh"); severed.processes.get(4100)!.ppid = 0;
  await receiverFails(severed, 6000, "proc-incomplete");
});

test("tmux/ssh/ss command failures remain fail closed and redact outputs", async () => {
  for (const step of ["list-clients", "display-message"] as const) {
    const f = tmuxClient("ssh");
    f.fail = (program, args) => program === "tmux" && args[2] === step ?
      { code: 1, stdout: "SSH_CONNECTION=secret", stderr: "TOKEN=secret" } : undefined;
    await receiverFails(f, 6000, "command-failed");
  }
  const ssh = tmuxClient("ssh");
  ssh.fail = (program) => program === "ssh" ? new Error("TOKEN=secret") : undefined;
  await receiverFails(ssh, 6000, "input-unattributed");
  const display = tmuxClient("display");
  display.fail = (program) => program === "ss" ? { code: 1, stdout: "TOKEN", stderr: "secret" } : undefined;
  await receiverFails(display, 6000, "input-unattributed");
});

test("frozen topology equality detects route, holder, and leader replacement", async () => {
  const f = localDisplay(true);
  const first = await resolveInputTopology(5000, f.dependencies());
  assert.equal(sameInputTopology(first, await resolveInputTopology(5000, f.dependencies())), true);
  f.processes.get(3001)!.starttime = "999999";
  assert.equal(sameInputTopology(first, await resolveInputTopology(5000, f.dependencies())), false);
  f.processes.get(3001)!.starttime = "30010";
  f.processes.get(4001)!.starttime = "888888";
  assert.equal(sameInputTopology(first, await resolveInputTopology(5000, f.dependencies())), false);
});
