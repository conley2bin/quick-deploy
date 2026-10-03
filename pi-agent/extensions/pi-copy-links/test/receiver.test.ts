import assert from "node:assert/strict";
import test from "node:test";
import {
  ReceiverResolutionError, resolveInputReceiver, resolveInputTopology, sameInputTopology,
  type InputTopology, type ReceiverDependencies, type ReceiverRunResult,
} from "../src/receiver.ts";

const socket = "/fixture/socket,with-comma";
const tmuxEnv = { TMUX: `${socket},123,0`, TMUX_PANE: "%64" };
const alias = "hostname 100.123.34.64\nuser conley\nport 22\n";
const connection = "100.123.34.64 49301 100.125.138.103 22";

type FakeProcess = {
  pid: number;
  ppid: number;
  sid: number;
  comm: string;
  starttime: string;
  uid: number;
  fd0?: string;
  env: Record<string, string>;
  argv: string[];
  masters: Map<string, number>;
  fdReadable: boolean;
};
type Client = { pid: number; tty: string; session: string; flags: string; pane: string };

function statBytes(process: FakeProcess): Buffer {
  const fields = Array.from({ length: 50 }, () => "0");
  fields[0] = "S";
  fields[1] = String(process.ppid);
  fields[2] = String(process.sid);
  fields[3] = String(process.sid);
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
  env: NodeJS.ProcessEnv = {};
  sshConfig = alias;
  paneTty = "/dev/pts/12";
  fail?: (program: string, args: readonly string[]) => ReceiverRunResult | Error | undefined;
  onRead?: (path: string, count: number) => void;
  private readonly reads = new Map<string, number>();

  constructor() {
    this.add({ pid: 1, ppid: 0, sid: 1, comm: "systemd", fd0: "/dev/null" });
  }

  add(value: Partial<FakeProcess> & Pick<FakeProcess, "pid" | "ppid" | "sid" | "comm">): FakeProcess {
    const process: FakeProcess = {
      starttime: String(value.pid * 10), uid: 1000, env: {}, argv: [value.comm],
      masters: new Map(), fdReadable: true, ...value,
    };
    this.processes.set(process.pid, process);
    return process;
  }

  master(process: FakeProcess, index: number, fd = "9"): void {
    process.masters.set(fd, index);
  }

  dependencies(): ReceiverDependencies {
    return {
      env: this.env,
      run: async (program, args) => {
        this.calls.push({ program, args });
        const failure = this.fail?.(program, args);
        if (failure instanceof Error) throw failure;
        if (failure) return failure;
        if (program === "ssh") {
          assert.deepEqual(args, ["-G", "conley-Y9000p"]);
          return { code: 0, stdout: this.sshConfig, stderr: "" };
        }
        assert.equal(program, "tmux");
        assert.deepEqual(args.slice(0, 2), ["-S", socket]);
        const command = args[2];
        if (command === "list-clients") {
          return { code: 0, stdout: this.clients.map((client) =>
            `pid=${client.pid}|tty=${client.tty}|session=${client.session}|flags=${client.flags}\n`).join(""), stderr: "" };
        }
        if (command === "display-message" && args.includes("-c")) {
          const tty = args[5], session = args[7];
          const client = this.clients.find((item) => item.tty === tty && item.session === session);
          assert.ok(client, "display-message must use the exact client's tty and session");
          return { code: 0, stdout:
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
          return environment(process.env);
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
        if (path === "/proc") return [...this.processes.keys()].map(String);
        const match = /^\/proc\/(\d+)\/fd$/u.exec(path);
        if (!match) throw new Error(`unexpected directory ${path}`);
        const process = this.processes.get(Number(match[1]));
        if (!process || !process.fdReadable) throw new Error("EACCES");
        return [...new Set([...(process.fd0 ? ["0"] : []), ...process.masters.keys()])];
      },
      readLink: async (path) => {
        const match = /^\/proc\/(\d+)\/fd\/(\d+)$/u.exec(path);
        if (!match) throw new Error(`unexpected link ${path}`);
        const process = this.processes.get(Number(match[1]));
        if (!process) throw new Error("ENOENT");
        if (match[2] === "0" && process.fd0) return process.fd0;
        if (process.masters.has(match[2]!)) return "/dev/pts/ptmx";
        throw new Error("ENOENT");
      },
    };
  }
}

function directSsh(): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 4001, sid: 4001, comm: "pi", fd0: "/dev/pts/12",
    env: { SSH_CONNECTION: connection, SSH_CLIENT: "stale", SSH_TTY: "/dev/pts/12" } });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12",
    env: { SSH_CONNECTION: connection, SSH_CLIENT: "100.123.34.64 49301 22", SSH_TTY: "/dev/pts/12" } });
  f.add({ pid: 3001, ppid: 2001, sid: 2001, comm: "sshd", argv: ["sshd: conley@pts/12  "],
    fdReadable: false });
  f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd" });
  return f;
}

function ghostty(display: Record<string, string> = { WAYLAND_DISPLAY: "wayland-0", DISPLAY: ":0" }): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 4001, sid: 4001, comm: "pi", fd0: "/dev/pts/12",
    env: { SSH_CONNECTION: "stale value", SSH_CLIENT: "stale", SSH_TTY: "/dev/pts/99" } });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12" });
  const terminal = f.add({ pid: 3001, ppid: 2500, sid: 2500, comm: "ghostty", env: {
    ...display, SSH_CONNECTION: "stale value", SSH_TTY: "/dev/pts/99",
  } });
  f.master(terminal, 12);
  f.add({ pid: 2500, ppid: 2001, sid: 2500, comm: "zsh" });
  f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd" });
  return f;
}

function legacyHolder(withSshEnvironment = false): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 4001, sid: 4001, comm: "pi", fd0: "/dev/pts/12",
    env: withSshEnvironment ? { SSH_CONNECTION: connection } : {} });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12" });
  const holder = f.add({ pid: 3001, ppid: 1, sid: 3001, comm: "mystery-terminal" });
  f.master(holder, 12);
  return f;
}

function broker(outer: "ssh" | "ghostty" | "tmux"): Fixture {
  const f = new Fixture();
  f.add({ pid: 5000, ppid: 3001, sid: 5000, comm: "pi", fd0: "/dev/pts/13",
    env: outer === "ssh" ? { SSH_CONNECTION: connection } : {} });
  const relay = f.add({ pid: 3001, ppid: 4001, sid: 4001, comm: "codoxear-broker",
    fd0: "/dev/pts/12", argv: ["/fixture/python", "/fixture/codoxear-broker", "--"] });
  f.master(relay, 13);
  f.add({ pid: 4001, ppid: 2001, sid: 4001, comm: "zsh", fd0: "/dev/pts/12",
    env: outer === "ssh" ? { SSH_CONNECTION: connection } : {} });
  if (outer === "ssh") {
    f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd", argv: ["sshd: conley@pts/12 "], fdReadable: false });
  } else if (outer === "ghostty") {
    const terminal = f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "ghostty", env: { WAYLAND_DISPLAY: "wayland-0" } });
    f.master(terminal, 12);
  } else {
    const tmux = f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "tmux: server" });
    f.master(tmux, 12);
    f.env = tmuxEnv;
    f.paneTty = "/dev/pts/12";
  }
  return f;
}

function tmuxClient(holder: "ssh" | "ghostty" | "tmux"): Fixture {
  const f = new Fixture();
  f.env = tmuxEnv;
  f.paneTty = "/dev/pts/99"; // The Pi pane; a nested client's outer pane must not match it.
  f.clients.push({ pid: 6000, tty: "/dev/pts/20", session: "$8", flags: "attached,UTF-8", pane: "%64" });
  f.add({ pid: 6000, ppid: 4001, sid: 4001, comm: "tmux: client", fd0: "/dev/pts/20" });
  f.add({ pid: 4001, ppid: 3001, sid: 4001, comm: "zsh", fd0: "/dev/pts/20",
    env: holder === "ssh" ? { SSH_CONNECTION: connection } : {} });
  if (holder === "ssh") {
    f.add({ pid: 3001, ppid: 1, sid: 3001, comm: "sshd", argv: ["sshd: conley@pts/20 "], fdReadable: false });
  } else if (holder === "ghostty") {
    const terminal = f.add({ pid: 3001, ppid: 2500, sid: 2500, comm: "ghostty",
      env: { WAYLAND_DISPLAY: "wayland-0", SSH_CONNECTION: "stale" } });
    f.master(terminal, 20);
    f.add({ pid: 2500, ppid: 2001, sid: 2500, comm: "zsh" });
    f.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd" });
  } else {
    const outer = f.add({ pid: 3001, ppid: 1, sid: 3001, comm: "tmux: server" });
    f.master(outer, 20);
  }
  return f;
}

function assertUnknown(topology: InputTopology, pattern: RegExp): void {
  assert.equal(topology.kind, "unknown");
  if (topology.kind === "unknown") assert.match(topology.diagnostic, pattern);
}

test("direct sshd-held PTY resolves the paired receiver from the session leader's initial environment", async () => {
  const f = directSsh();
  const route = await resolveInputTopology(5000, f.dependencies());
  assert.deepEqual(route.kind === "remote" ? route.receiver : route,
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
  assert.equal(route.hops[0]?.holder.pid, 3001);
  assert.ok(f.procPaths.includes("/proc/4001/environ"));
  assert.ok(!f.procPaths.includes("/proc/5000/environ") || route.kind === "remote");
});

test("direct sshd-held PTY with an unpaired peer is unknown and never becomes local", async () => {
  const f = directSsh();
  f.processes.get(4001)!.env.SSH_CONNECTION = "100.123.34.65 49301 100.125.138.103 22";
  assertUnknown(await resolveInputTopology(5000, f.dependencies()), /不是 conley-Y9000p|无法验证为 conley-Y9000p/u);
  const sessionBinary = directSsh();
  Object.assign(sessionBinary.processes.get(3001)!, {
    comm: "sshd-session", argv: ["sshd-session: conley@pts/12  "],
  });
  assert.equal((await resolveInputTopology(5000, sessionBinary.dependencies())).kind, "remote");
});

test("local Ghostty wins at the nearest owner despite stale SSH environment and sshd ancestors above it", async () => {
  const route = await resolveInputTopology(5000, ghostty().dependencies());
  assert.equal(route.kind, "local");
  if (route.kind === "local") assert.equal(route.basis, "gui");
  assert.equal(route.hops[0]?.holder.comm, "ghostty");
});

test("X-forwarded Ghostty without Wayland is unknown", async () => {
  assertUnknown(await resolveInputTopology(5000, ghostty({ DISPLAY: "localhost:10.0" }).dependencies()),
    /ghostty.*本地 Wayland\/X/u);
});

test("Codoxear broker relays its fixed fd0 to SSH, Ghostty, or tmux topology", async () => {
  const ssh = await resolveInputTopology(5000, broker("ssh").dependencies());
  assert.equal(ssh.kind, "remote");
  assert.deepEqual(ssh.hops.map((hop) => hop.holder.comm), ["codoxear-broker", "sshd"]);
  const localFixture = broker("ghostty");
  const local = await resolveInputTopology(5000, localFixture.dependencies());
  assert.equal(local.kind, "local");
  assert.deepEqual(local.hops.map((hop) => hop.holder.comm), ["codoxear-broker", "ghostty"]);
  Object.assign(localFixture.processes.get(3001)!, {
    comm: "python3", argv: ["/usr/bin/python3", "-m", "codoxear", "broker"],
  });
  assert.equal((await resolveInputTopology(5000, localFixture.dependencies())).kind, "local",
    "python -m codoxear broker is the same fixed-stdin relay");
  const multiplexed = await resolveInputTopology(5000, broker("tmux").dependencies());
  assert.equal(multiplexed.kind, "tmux");
  assert.deepEqual(multiplexed.hops.map((hop) => hop.holder.comm), ["codoxear-broker", "tmux: server"]);
});

test("unknown holder preserves local legacy only without any SSH provenance", async () => {
  const clean = await resolveInputTopology(5000, legacyHolder(false).dependencies());
  assert.equal(clean.kind, "local");
  if (clean.kind === "local") assert.equal(clean.basis, "legacy");
  const inherited = await resolveInputTopology(5000, legacyHolder(true).dependencies());
  assertUnknown(inherited, /mystery-terminal.*无法归属/u);
  const ancestor = legacyHolder(false);
  ancestor.processes.get(3001)!.ppid = 2001;
  ancestor.add({ pid: 2001, ppid: 1, sid: 2001, comm: "sshd" });
  assertUnknown(await resolveInputTopology(5000, ancestor.dependencies()), /mystery-terminal.*无法归属/u);
});

test("stdin outside a PTY is unknown", async () => {
  const f = legacyHolder(false);
  f.processes.get(5000)!.fd0 = "/dev/null";
  assertUnknown(await resolveInputTopology(5000, f.dependencies()), /stdin 不是可识别的 PTY/u);
});

test("candidate verification failure falls back to the unique readable tty-index holder", async () => {
  const f = legacyHolder(false);
  const wrong = f.processes.get(3001)!;
  wrong.masters.clear();
  const actual = f.add({ pid: 3002, ppid: 1, sid: 3002, comm: "actual-holder" });
  f.master(actual, 12);
  const route = await resolveInputTopology(5000, f.dependencies());
  assert.equal(route.kind, "local");
  assert.equal(route.hops[0]?.holder.pid, 3002);
});

test("tmux marked client uses its nearest owner: Ghostty local, sshd remote, nested tmux refused", async () => {
  const local = tmuxClient("ghostty");
  assert.equal(await resolveInputReceiver(6000, local.dependencies()), "local");
  const remote = tmuxClient("ssh");
  assert.deepEqual(await resolveInputReceiver(6000, remote.dependencies()),
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
  const nested = tmuxClient("tmux");
  await assert.rejects(resolveInputReceiver(6000, nested.dependencies()), (error: unknown) => {
    assert.ok(error instanceof ReceiverResolutionError);
    assert.equal(error.code, "input-unattributed");
    return true;
  });
});

test("tmux inventory, pane, attachment and origin checks remain fail closed", async () => {
  for (const origin of ["unknown", 0, -1, 1.5] as const) {
    const f = tmuxClient("ssh");
    await assert.rejects(resolveInputReceiver(origin, f.dependencies()), (error: unknown) =>
      error instanceof ReceiverResolutionError && error.code === "invalid-origin");
  }
  const absent = tmuxClient("ssh");
  absent.clients.length = 0;
  await assert.rejects(resolveInputReceiver(6000, absent.dependencies()), (error: unknown) =>
    error instanceof ReceiverResolutionError && error.code === "client-not-found");
  const detached = tmuxClient("ssh");
  detached.clients[0]!.flags = "UTF-8";
  await assert.rejects(resolveInputReceiver(6000, detached.dependencies()), (error: unknown) =>
    error instanceof ReceiverResolutionError && error.code === "client-mismatch");
  const wrongPane = tmuxClient("ssh");
  wrongPane.clients[0]!.pane = "%99";
  await assert.rejects(resolveInputReceiver(6000, wrongPane.dependencies()), (error: unknown) =>
    error instanceof ReceiverResolutionError && error.code === "pane-mismatch");
});

test("malformed SSH environment/config and command failures never yield a receiver", async () => {
  for (const configure of [
    (f: Fixture) => { f.processes.get(4001)!.env.SSH_CONNECTION = "bad"; },
    (f: Fixture) => { f.sshConfig = "hostname conley-Y9000p\nuser conley\nport 22\n"; },
    (f: Fixture) => { f.fail = (program) => program === "ssh" ? new Error("secret") : undefined; },
  ]) {
    const f = directSsh(); configure(f);
    assertUnknown(await resolveInputTopology(5000, f.dependencies()), /SSH 对端/u);
  }
});

test("frozen topology comparison detects holder or leader identity replacement", async () => {
  const f = ghostty();
  const first = await resolveInputTopology(5000, f.dependencies());
  const same = await resolveInputTopology(5000, f.dependencies());
  assert.equal(sameInputTopology(first, same), true);
  f.processes.get(3001)!.starttime = "999999";
  const replacedHolder = await resolveInputTopology(5000, f.dependencies());
  assert.equal(sameInputTopology(first, replacedHolder), false);
  f.processes.get(3001)!.starttime = "30010";
  f.processes.get(4001)!.starttime = "888888";
  const replacedLeader = await resolveInputTopology(5000, f.dependencies());
  assert.equal(sameInputTopology(first, replacedLeader), false);
});
