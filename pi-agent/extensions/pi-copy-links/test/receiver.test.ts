import assert from "node:assert/strict";
import test from "node:test";
import {
  ReceiverResolutionError, resolveInputReceiver, type ReceiverDependencies, type ReceiverRunResult,
} from "../src/receiver.ts";

const socket = "/fixture/socket,with-comma";
const env = { TMUX: `${socket},123,0`, TMUX_PANE: "%64" };
const alias = "hostname 100.123.34.64\nuser conley\nport 22\n";
const sshEnvironment = Buffer.from("PATH=/fixture\0SSH_CONNECTION=100.123.34.64 49301 100.125.138.103 22\0TOKEN=must-never-appear\0");
type Client = { pid: number; tty: string; session: string; flags: string; pane: string };
const ssh: Client = { pid: 2686911, tty: "/dev/pts/110", session: "$1", flags: "attached,UTF-8", pane: "%64" };
const local: Client = { pid: 3897780, tty: "/dev/pts/35", session: "$8", flags: "attached,UTF-8", pane: "%64" };

function stat(pid: number, ppid: number, comm: string, starttime = `${pid * 10}`): Buffer {
  const fields = Array.from({ length: 50 }, () => "0");
  fields[0] = "S"; fields[1] = String(ppid); fields[19] = starttime;
  return Buffer.from(`${pid} (${comm}) ${fields.join(" ")}\n`);
}

function fixture(client = ssh, ancestors: Array<[number, number, string]> = [
  [2686911, 2686900, "tmux: client"], [2686900, 2686800, "zsh"],
  [2686800, 2660000, "sshd"], [2660000, 1, "sshd"], [1, 0, "systemd"],
]) {
  const clients: Client[] = [{ ...client }];
  const stats = new Map(ancestors.map(([pid, ppid, comm]) => [pid, stat(pid, ppid, comm)]));
  const commands: Array<{ program: string; args: readonly string[] }> = [];
  const procPaths: string[] = [];
  let listCount = 0, displayCount = 0;
  let sshConfig = alias;
  let environment: Buffer = sshEnvironment;
  let onList: ((count: number) => void) | undefined;
  let onDisplay: ((count: number, found: Client) => string) | undefined;
  let onStat: ((pid: number, count: number) => Buffer | undefined) | undefined;
  let fail: ((program: string, args: readonly string[]) => ReceiverRunResult | Error | undefined) | undefined;
  const reads = new Map<number, number>();
  const dependencies: ReceiverDependencies = {
    env,
    run: async (program, args) => {
      commands.push({ program, args });
      const failure = fail?.(program, args);
      if (failure instanceof Error) throw failure;
      if (failure) return failure;
      if (program === "ssh") {
        assert.deepEqual(args, ["-G", "conley-Y9000p"]);
        return { code: 0, stdout: sshConfig, stderr: "" };
      }
      assert.equal(program, "tmux");
      assert.deepEqual(args.slice(0, 2), ["-S", socket]);
      if (args[2] === "list-clients") {
        assert.deepEqual(args.slice(2, 4), ["list-clients", "-F"]);
        assert.equal(args.length, 5, "all sessions must be enumerated without -t");
        onList?.(++listCount);
        return { code: 0, stdout: clients.map((item) =>
          `pid=${item.pid}|tty=${item.tty}|session=${item.session}|flags=${item.flags}\n`).join(""), stderr: "" };
      }
      if (args[2] === "display-message") {
        assert.deepEqual(args.slice(2, 9), ["display-message", "-p", "-c", args[5], "-t", args[7], "-F"]);
        assert.equal(args.length, 10);
        const found = clients.find((item) => item.tty === args[5] && item.session === args[7]);
        assert.ok(found, "display-message must use the exact client's tty and session");
        return { code: 0, stdout: onDisplay?.(++displayCount, found) ??
          `pid=${found.pid}|tty=${found.tty}|session=${found.session}|pane=${found.pane}\n`, stderr: "" };
      }
      assert.fail("unexpected command");
    },
    readProc: async (path) => {
      procPaths.push(path);
      const match = /^\/proc\/(\d+)\/(stat|environ)$/u.exec(path);
      assert.ok(match, "only proc stat and origin environ are read");
      const pid = Number(match[1]);
      if (match[2] === "environ") {
        assert.equal(pid, client.pid);
        return environment;
      }
      const count = (reads.get(pid) ?? 0) + 1;
      reads.set(pid, count);
      const value = onStat?.(pid, count) ?? stats.get(pid);
      if (!value) throw new Error("simulated /proc ENOENT");
      return value;
    },
  };
  return {
    dependencies, clients, commands, procPaths, stats,
    setAlias: (value: string) => { sshConfig = value; },
    setEnvironment: (value: Buffer) => { environment = value; },
    onList: (callback: typeof onList) => { onList = callback; },
    onDisplay: (callback: typeof onDisplay) => { onDisplay = callback; },
    onStat: (callback: typeof onStat) => { onStat = callback; },
    fail: (callback: typeof fail) => { fail = callback; },
  };
}

async function fails(f: ReturnType<typeof fixture>, origin: number | "unknown", code: string): Promise<void> {
  await assert.rejects(resolveInputReceiver(origin, f.dependencies), (error: unknown) => {
    assert.ok(error instanceof ReceiverResolutionError);
    assert.equal(error.code, code);
    assert.doesNotMatch(error.message, /TOKEN=|must-never-appear|SSH_CONNECTION=/u);
    return true;
  });
}

test("exact SSH client in shared pane resolves paired receiver with bracketed identity and ancestry", async () => {
  const f = fixture();
  assert.deepEqual(await resolveInputReceiver(ssh.pid, f.dependencies),
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
  assert.deepEqual(f.commands.map(({ program, args }) => `${program} ${args[2] ?? args[0]}`), [
    "tmux list-clients", "tmux display-message", "ssh -G", "tmux list-clients", "tmux display-message",
  ]);
  assert.deepEqual(f.procPaths.filter((path) => path.endsWith("/environ")), [`/proc/${ssh.pid}/environ`]);
  assert.equal(f.procPaths.filter((path) => path === `/proc/${ssh.pid}/stat`).length, 3);
  const decorated = fixture();
  decorated.stats.set(2686800, stat(2686800, 2660000, "sshd-session"));
  decorated.stats.set(2660000, stat(2660000, 1, "sshd: conley"));
  assert.deepEqual(await resolveInputReceiver(ssh.pid, decorated.dependencies),
    { kind: "remote", peer: "100.123.34.64", port: 22, user: "conley" });
});

test("a literal IPv6 peer remains an SSH-usable unbracketed pinned address", async () => {
  const f = fixture();
  f.setAlias("hostname 2001:db8::64\nuser conley\nport 2222\n");
  f.setEnvironment(Buffer.from("SSH_CONNECTION=2001:db8::64 49301 2001:db8::10 2222\0"));
  assert.deepEqual(await resolveInputReceiver(ssh.pid, f.dependencies),
    { kind: "remote", peer: "2001:db8::64", port: 2222, user: "conley" });
});

test("grouped local Ghostty client on same pane is local despite inherited SSH_CONNECTION", async () => {
  const f = fixture(local, [
    [local.pid, 3897000, "tmux: client) with paren"], [3897000, 3897100, "ghostty"], [3897100, 1, "sh"], [1, 0, "systemd"],
  ]);
  assert.equal(await resolveInputReceiver(local.pid, f.dependencies), "local");
  assert.ok(f.commands.filter(({ program }) => program === "tmux").some(({ args }) => args.includes("$8")));
  assert.ok(f.commands.every(({ program }) => program !== "ssh"));
  assert.ok(f.procPaths.every((path) => path.endsWith("/stat")), "no local environment read");
});

test("missing origin or source socket/pane fails before any command or proc access", async () => {
  for (const origin of ["unknown", 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1] as const) {
    const f = fixture(); await fails(f, origin, "invalid-origin");
    assert.deepEqual(f.commands, []); assert.deepEqual(f.procPaths, []);
  }
  for (const invalid of [{}, { TMUX: `${socket},123,0` }, { TMUX_PANE: "%64" },
    { TMUX: "relative/socket,1,0", TMUX_PANE: "%64" }, { TMUX: `${socket},1,0`, TMUX_PANE: "%oops" }]) {
    const f = fixture(); f.dependencies.env = invalid; await fails(f, ssh.pid, "missing-tmux");
    assert.deepEqual(f.commands, []); assert.deepEqual(f.procPaths, []);
  }
});

test("foreign or stale client, duplicates and detached client cannot yield a receiver", async () => {
  const absent = fixture(); absent.clients.splice(0); await fails(absent, ssh.pid, "client-not-found");
  const duplicate = fixture(); duplicate.clients.push({ ...ssh }); await fails(duplicate, ssh.pid, "ambiguous-client");
  const detached = fixture(); detached.clients[0]!.flags = "UTF-8"; await fails(detached, ssh.pid, "client-mismatch");
  const stale = fixture(); stale.onList((count) => { if (count === 2) stale.clients.splice(0); });
  await fails(stale, ssh.pid, "client-not-found");
});

test("same-group wrong pane and client query returning a different identity are rejected", async () => {
  const wrong = fixture({ ...local, pane: "%57" }, [
    [local.pid, 3897000, "tmux: client"], [3897000, 1, "ghostty"], [1, 0, "systemd"],
  ]);
  await fails(wrong, local.pid, "pane-mismatch");
  const switched = fixture(); switched.onDisplay((count, found) =>
    `pid=${found.pid}|tty=${found.tty}|session=${found.session}|pane=${count === 2 ? "%57" : "%64"}\n`);
  await fails(switched, ssh.pid, "pane-mismatch");
  for (const field of ["pid=4016590", "tty=/dev/pts/999", "session=$8"] as const) {
    const f = fixture(); f.onDisplay((_count, found) => {
      const output = `pid=${found.pid}|tty=${found.tty}|session=${found.session}|pane=%64\n`;
      return output.replace(new RegExp(`${field.split("=")[0]}=[^|\\n]+`, "u"), field);
    });
    await fails(f, ssh.pid, "client-mismatch");
  }
  const moved = fixture(); moved.onList((count) => { if (count === 2) moved.clients[0]!.session = "$8"; });
  await fails(moved, ssh.pid, "client-mismatch");
});

test("PID reuse before and after inspection and ancestor replacement reject instead of misclassifying", async () => {
  const early = fixture(); early.onStat((pid, count) => pid === ssh.pid && count === 2 ?
    stat(pid, 2686900, "tmux: client", "999") : undefined);
  await fails(early, ssh.pid, "proc-changed");
  const late = fixture(); late.onStat((pid, count) => pid === ssh.pid && count === 3 ?
    stat(pid, 2686900, "tmux: client", "999") : undefined);
  await fails(late, ssh.pid, "proc-changed");
  const ancestor = fixture(); ancestor.onStat((pid, count) => pid === 2686800 && count === 2 ?
    stat(pid, 2660000, "sshd", "999") : undefined);
  await fails(ancestor, ssh.pid, "proc-changed");
});

test("unreadable, truncated, cyclic or severed proc ancestry cannot imply local", async () => {
  const unreadable = fixture(); unreadable.stats.delete(2686800); await fails(unreadable, ssh.pid, "proc-unavailable");
  const short = fixture(); short.stats.set(2686800, Buffer.from("2686800 (sshd) S 2660000 0\n"));
  await fails(short, ssh.pid, "proc-incomplete");
  const broken = fixture(); broken.stats.set(2686800, stat(2686800, 0, "sshd"));
  await fails(broken, ssh.pid, "proc-incomplete");
  const cyclic = fixture(); cyclic.stats.set(2686800, stat(2686800, ssh.pid, "sshd"));
  await fails(cyclic, ssh.pid, "proc-incomplete");
});

test("remote identity demands parseable filtered environment and a literal matching alias", async () => {
  for (const value of [Buffer.from("PATH=fixture\0"), Buffer.from("SSH_CONNECTION=bad\0"),
    Buffer.from("SSH_CONNECTION=100.123.34.64 49301 100.125.138.103 22"),
    Buffer.from("SSH_CONNECTION=100.123.34.64 49301 100.125.138.103 22\0SSH_CONNECTION=100.123.34.64 49301 100.125.138.103 22\0")]) {
    const f = fixture(); f.setEnvironment(value); await fails(f, ssh.pid, "ssh-env-invalid");
  }
  for (const config of ["hostname conley-Y9000p\nuser conley\nport 22\n",
    "hostname 100.123.34.64\nuser other\nport 22\n",
    "hostname 100.123.34.64\nuser conley\nport 0\n",
    "hostname 100.123.34.64\nhostname 100.123.34.64\nuser conley\nport 22\n"]) {
    const f = fixture(); f.setAlias(config); await fails(f, ssh.pid, "ssh-alias-invalid");
  }
  const peer = fixture(); peer.setEnvironment(Buffer.from("SSH_CONNECTION=100.123.34.65 49301 100.125.138.103 22\0"));
  await fails(peer, ssh.pid, "ssh-peer-mismatch");
  const port = fixture(); port.setEnvironment(Buffer.from("SSH_CONNECTION=100.123.34.64 49301 100.125.138.103 2222\0"));
  await fails(port, ssh.pid, "ssh-peer-mismatch");
});

test("tmux and ssh command failures are typed and never fall back to local", async () => {
  for (const step of ["list-clients", "display-message", "ssh"] as const) {
    const f = fixture();
    f.fail((program, args) => program === step || args[2] === step ?
      { code: 1, stdout: "SSH_CONNECTION=secret", stderr: "TOKEN=must-never-appear" } : undefined);
    await fails(f, ssh.pid, "command-failed");
  }
  const thrown = fixture(); thrown.fail((program) => program === "ssh" ? new Error("TOKEN=must-never-appear") : undefined);
  await fails(thrown, ssh.pid, "command-failed");
});
