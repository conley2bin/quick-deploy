import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import type { ChildProcess } from "node:child_process";
import type { OpenLifetime } from "../src/browser.ts";
import {
  dispatchOpenTarget, runClientHelper, type DispatcherDependencies, type HelperRequest, type HelperSpawn,
} from "../src/dispatcher.ts";
import type { InputReceiver } from "../src/receiver.ts";

const client: Extract<InputReceiver, { kind: "remote" }> =
  { kind: "remote", peer: "192.0.2.64", port: 2222, user: "conley" };
const tmux = { TMUX: "/fixture/tmux,1,0", TMUX_PANE: "%4" };

function lifetime(controller = new AbortController()): OpenLifetime & { current: boolean } {
  const value = { signal: controller.signal, current: true, isCurrent: () => value.current };
  return value;
}

type Invocation = { command: string; args: readonly string[]; options: unknown; input: string; kills: string[] };
function fakeSsh(
  invocations: Invocation[],
  reply: (request: HelperRequest) => { status: number; stdout: string; stderr?: string } | "hang" =
    (request) => ({ status: 0, stdout: JSON.stringify(request.action === "open-file" ?
      { ok: true, action: "open-file", kind: request.kind, path: request.path } :
      { ok: true, action: "open-web", openedUrl: request.url, tunnel: null }) + "\n" }),
): HelperSpawn {
  return (command, args, options) => {
    const invocation: Invocation = { command, args, options, input: "", kills: [] };
    invocations.push(invocation);
    const child = new EventEmitter() as ChildProcess;
    const stdin = new PassThrough(), stdout = new PassThrough(), stderr = new PassThrough();
    Object.assign(child, { stdin, stdout, stderr, kill: (signal: string) => {
      invocation.kills.push(signal); return true;
    } });
    let buffer = "", lines = 0, response: Exclude<ReturnType<typeof reply>, "hang"> | undefined;
    stdin.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      invocation.input += text;
      buffer += text;
      for (let end = buffer.indexOf("\n"); end >= 0; end = buffer.indexOf("\n")) {
        const line = JSON.parse(buffer.slice(0, end)) as Record<string, unknown>;
        buffer = buffer.slice(end + 1);
        if (++lines === 1) {
          assert.equal(line.phase, "request");
          assert.equal(line.version, 2);
          assert.ok(Number.isInteger(line.prepareDeadlineMs));
          const value = reply(line as HelperRequest);
          if (value === "hang") return;
          response = value;
          if (value.status !== 0) {
            queueMicrotask(() => finish(value));
            return;
          }
          let payload: Record<string, unknown>;
          try { payload = JSON.parse(value.stdout) as Record<string, unknown>; }
          catch { payload = {}; }
          const prepared = { version: 2, phase: "prepared", nonce: "a".repeat(32), ...payload };
          queueMicrotask(() => stdout.write(JSON.stringify(prepared) + "\n"));
        } else if (lines === 2) {
          assert.equal(line.phase, "commit");
          assert.equal(line.nonce, "a".repeat(32));
          if (response) queueMicrotask(() => finish(response!));
        }
      }
    });
    function finish(value: Exclude<ReturnType<typeof reply>, "hang">): void {
      let text = value.stdout;
      try { text = JSON.stringify({ version: 2, phase: "result", ...JSON.parse(text) }) + "\n"; }
      catch { /* preserve bad output */ }
      stdout.end(text);
      stderr.end(value.stderr ?? "");
      child.emit("close", value.status, null);
    }
    return child;
  };
}

function setup(invocations: Invocation[], extras: DispatcherDependencies = {}): DispatcherDependencies {
  return { env: tmux, resolve: async () => client, spawn: fakeSsh(invocations), ...extras };
}

function decoded(invocations: Invocation[]): HelperRequest[] {
  return invocations.map(({ input }) => {
    const { phase: _phase, prepareDeadlineMs: _deadline, ...request } =
      JSON.parse(input.split("\n")[0]!) as Record<string, unknown>;
    return request as HelperRequest;
  });
}

function pinned(invocation: Invocation): void {
  assert.equal(invocation.command, "ssh");
  assert.deepEqual(invocation.options, { stdio: ["pipe", "pipe", "pipe"], shell: false, detached: true });
  assert.deepEqual(invocation.args, ["-T", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes",
    "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "HostName=192.0.2.64",
    "-o", "Port=2222", "-o", "User=conley", "conley-Y9000p", "~/.local/bin/pi-client-link-helper"]);
  assert.ok(invocation.input.endsWith("\n"));
  assert.equal(invocation.input.trimEnd().split("\n").length, 2);
  assert.deepEqual(JSON.parse(invocation.input.split("\n")[1]!),
    { version: 2, phase: "commit", nonce: "a".repeat(32) });
  assert.doesNotMatch(invocation.args.join(" "), /example\.org|work\/|L12/u);
}

test("non-tmux local and verified tmux-local keep the unchanged local opener", async () => {
  const calls: unknown[][] = [], origins: unknown[] = [], invocations: Invocation[] = [];
  const local: NonNullable<DispatcherDependencies["local"]> = async (...args) => { calls.push(args); };
  const alive = lifetime();
  await dispatchOpenTarget("./file.ts#L12", "/work", alive, "unknown", {
    env: {}, local, resolve: async (origin) => { origins.push(origin); throw Error("unexpected resolver"); },
    spawn: fakeSsh(invocations),
  });
  await dispatchOpenTarget("https://example.org", "/work", alive, 802, {
    env: tmux, local, resolve: async (origin) => { origins.push(origin); return "local"; },
    spawn: fakeSsh(invocations),
  });
  assert.deepEqual(calls, [["./file.ts#L12", "/work", alive], ["https://example.org", "/work", alive]]);
  assert.deepEqual(origins, [802]);
  assert.deepEqual(invocations, []);
});

test("direct SSH with no tmux and unknown/foreign/stale tmux origin fail closed", async () => {
  let localCalls = 0;
  const invocations: Invocation[] = [];
  const local = async () => { localCalls++; };
  for (const key of ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"] as const) {
    await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), "unknown",
      { env: { [key]: "remote" }, local, spawn: fakeSsh(invocations) }), /SSH 输入未经过/u);
  }
  for (const origin of ["unknown", 9999] as const) {
    await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), origin,
      setup(invocations, { resolve: async () => { throw new Error("origin not attached or in wrong pane"); }, local })),
    /origin not attached/u);
  }
  assert.equal(localCalls, 0);
  assert.deepEqual(invocations, []);
});

test("verified remote canonical web URL and rewritten loopback tunnel use only JSON stdin", async () => {
  const invocations: Invocation[] = [];
  let localCalls = 0;
  const deps = setup(invocations, { local: async () => { localCalls++; },
    spawn: fakeSsh(invocations, (request) => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: "http://127.0.0.1:54465/a?q=1#frag",
      tunnel: { remoteHost: "127.0.0.1", remotePort: 8743, localPort: 54465, reused: false },
    }) + "\n" })) });
  await dispatchOpenTarget("HTTP://127.0.0.1:8743/a?q=1#frag", "/work", lifetime(), 802, deps);
  assert.equal(localCalls, 0);
  assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url: "http://127.0.0.1:8743/a?q=1#frag" }]);
  pinned(invocations[0]!);
});

test("loopback replies require a tunnel bound to the same company address", async () => {
  const invocations: Invocation[] = [];
  for (const [href, opened, remoteHost, pattern] of [
    ["http://localhost:8743/a", "http://localhost:8743/a", null, /缺少必需的公司回环隧道/u],
    ["http://127.0.0.1:8743/a", "http://127.0.0.2:54465/a", "127.0.0.2", /隧道目标不匹配/u],
  ] as const) {
    await assert.rejects(dispatchOpenTarget(href, "/work", lifetime(), 802, setup(invocations, {
      spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify(remoteHost ?
        { ok: true, action: "open-web", openedUrl: opened,
          tunnel: { remoteHost, remotePort: 8743, localPort: 54465, reused: false } } :
        { ok: true, action: "open-web", openedUrl: opened, tunnel: null }) + "\n" })),
    })), pattern);
  }
  // The helper explicitly writes :80 even though WHATWG omits that default port.
  await dispatchOpenTarget("http://localhost/path", "/work", lifetime(), 802, setup(invocations, {
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: "http://127.0.0.1:80/path",
      tunnel: { remoteHost: "127.0.0.1", remotePort: 80, localPort: 80, reused: true },
    }) + "\n" })),
  }));
  const mapped = "http://[::ffff:127.0.0.1]:8743/a";
  await dispatchOpenTarget(mapped, "/work", lifetime(), 802, setup(invocations, {
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: "http://127.0.0.1:54465/a",
      tunnel: { remoteHost: "127.0.0.1", remotePort: 8743, localPort: 54465, reused: true },
    }) + "\n" })),
  }));
});

test("verified peer is pinned into command-line SSH options despite post-resolution alias drift", async () => {
  const invocations: Invocation[] = [];
  let aliasNow = "192.0.2.64";
  const dependencies = setup(invocations, { resolve: async () => {
    aliasNow = "203.0.113.8"; // Equivalent to ssh_config changing after `ssh -G`.
    return client;
  } });
  await dispatchOpenTarget("https://example.org/a", "/work", lifetime(), 802, dependencies);
  assert.equal(aliasNow, "203.0.113.8");
  pinned(invocations[0]!);
  assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url: "https://example.org/a" }]);
  // OpenSSH's actual config expansion must honor the verified endpoint even
  // after a fixture alias changes host/user/port. No real host is contacted.
  const root = mkdtempSync(join(tmpdir(), "pi-dispatcher-config-"));
  try {
    const config = join(root, "config");
    writeFileSync(config, "Host conley-Y9000p\n HostName 203.0.113.8\n User changed\n Port 2200\n");
    const effective = execFileSync("ssh", ["-G", "-F", config, ...invocations[0]!.args.slice(0, -1)],
      { encoding: "utf8" });
    assert.match(effective, /^hostname 192\.0\.2\.64$/mu);
    assert.match(effective, /^user conley$/mu);
    assert.match(effective, /^port 2222$/mu);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("company stat follows symlinks and transmits exact file/directory kind plus locations", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-dispatcher-"));
  const file = join(root, "extensionless");
  const folder = join(root, "folder.v2");
  const link = join(root, "linked");
  writeFileSync(file, "hello"); mkdirSync(folder); symlinkSync(file, link);
  const invocations: Invocation[] = [];
  try {
    for (const href of ["./extensionless#L12C3", "./folder.v2", "./linked"]) {
      await dispatchOpenTarget(href, root, lifetime(), 802, setup(invocations));
    }
    assert.deepEqual(decoded(invocations), [
      { version: 2, action: "open-file", kind: "file", path: file, line: 12, column: 3 },
      { version: 2, action: "open-file", kind: "directory", path: folder },
      { version: 2, action: "open-file", kind: "file", path: link },
    ]);
    invocations.forEach(pinned);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("missing, dangling, special, directory-with-line and colon goto cannot reach SSH", async (t) => {
  if (process.platform !== "linux") { t.skip("POSIX special-file fixture"); return; }
  const root = mkdtempSync(join(tmpdir(), "pi-dispatcher-invalid-"));
  mkdirSync(join(root, "dir"));
  symlinkSync(join(root, "missing"), join(root, "dangling"));
  execFileSync("mkfifo", [join(root, "pipe")]);
  writeFileSync(join(root, "colon:name"), "x");
  const invocations: Invocation[] = [];
  try {
    for (const [href, pattern] of [["missing", /不存在/u], ["dangling", /不存在/u],
      ["pipe", /普通文件或目录/u], ["./dir:12", /目录不能/u],
      ["./colon%3Aname#L2", /--goto/u], ["./x:2147483648", /超出范围/u]] as const) {
      await assert.rejects(dispatchOpenTarget(href, root, lifetime(), 802, setup(invocations)), pattern);
    }
    assert.deepEqual(invocations, []);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("SSH, helper, invalid reply, mismatched status/target all report errors with no local fallback", async () => {
  const modes: Array<[string, number, string, RegExp]> = [
    ["auth", 255, "", /SSH 连接或认证失败/u],
    ["missing helper", 127, "", /助手未安装/u],
    ["helper error", 1, '{"ok":false,"error":{"code":"gui_unavailable","message":"no desktop"}}\n', /gui_unavailable.*no desktop/u],
    ["bad json", 0, "banner\n{}\n", /无效 JSON 协议/u],
    ["bad status", 0, '{"ok":false,"error":{"code":"browser_failed","message":"x"}}\n', /操作不匹配/u],
    ["bad action", 0, '{"ok":true,"action":"open-file","kind":"file","path":"/bad"}\n', /操作不匹配/u],
    ["bad URL", 0, '{"ok":true,"action":"open-web","openedUrl":"https://attacker.invalid","tunnel":null}\n', /网页目标不匹配/u],
    ["bad tunnel", 0, '{"ok":true,"action":"open-web","openedUrl":"https://example.org/a","tunnel":{"remoteHost":"127.0.0.1","remotePort":80,"localPort":5,"reused":false}}\n', /隧道目标不匹配/u],
  ];
  let localCalls = 0;
  for (const [label, status, stdout, pattern] of modes) {
    const invocations: Invocation[] = [];
    await assert.rejects(dispatchOpenTarget("https://example.org/a", "/work", lifetime(), 802,
      setup(invocations, { local: async () => { localCalls++; },
        spawn: fakeSsh(invocations, () => ({ status, stdout })) })), pattern, label);
    assert.equal(invocations.length, 1, label);
  }
  assert.equal(localCalls, 0);
});

test("abort before, during resolver, during stat, and during SSH suppresses dispatch/feedback", async () => {
  const invocations: Invocation[] = [];
  const aborted = new AbortController(); aborted.abort();
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(aborted), 802,
    setup(invocations)), /动作已失效/u);
  const resolving = lifetime();
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", resolving, 802,
    setup(invocations, { resolve: async () => { resolving.current = false; return client; } })), /动作已失效/u);
  const checking = lifetime();
  await assert.rejects(dispatchOpenTarget("./file", "/work", checking, 802,
    setup(invocations, { stat: async () => { checking.current = false;
      return { isFile: () => true, isDirectory: () => false }; } })), /动作已失效/u);
  assert.equal(invocations.length, 0);
  const controller = new AbortController(), active = lifetime(controller);
  let localCalls = 0;
  const hung = fakeSsh(invocations, () => { controller.abort(); return "hang"; });
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", active, 802,
    setup(invocations, { spawn: hung, local: async () => { localCalls++; } })), /动作已失效/u);
  assert.equal(localCalls, 0);
  assert.deepEqual(invocations.at(0)?.kills, ["SIGTERM"]);
});

test("bounded SSH timeout and oversized reply kill the request rather than opening company GUI", async () => {
  const invocations: Invocation[] = [];
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), 802,
    setup(invocations, { timeoutMs: 15, spawn: fakeSsh(invocations, () => "hang") })), /SSH 请求超时/u);
  assert.deepEqual(invocations[0]!.kills, ["SIGTERM"]);
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), 802,
    setup(invocations, { spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: "x".repeat(65_537), tunnel: null,
    }) + "\n" })) })), /回复超过大小限制/u);
  assert.deepEqual(invocations[1]!.kills, ["SIGTERM"]);
});

test("cumulative PREPARED plus final stdout remains bounded", async () => {
  const invocations: Invocation[] = [];
  const oversizedFinal: HelperSpawn = (command, args, options) => {
    const invocation: Invocation = { command, args, options, input: "", kills: [] };
    invocations.push(invocation);
    const child = new EventEmitter() as ChildProcess;
    const stdin = new PassThrough(), stdout = new PassThrough(), stderr = new PassThrough();
    Object.assign(child, { stdin, stdout, stderr, kill: (signal: string) => {
      invocation.kills.push(signal); return true;
    } });
    stdin.on("data", (chunk: Buffer) => {
      invocation.input += chunk.toString();
      if (invocation.input.trimEnd().split("\n").length === 1) {
        stdout.write(JSON.stringify({ version: 2, phase: "prepared", nonce: "a".repeat(32),
          ok: true, action: "open-web", openedUrl: "https://example.org/", tunnel: null }) + "\n");
      } else {
        stdout.write("x".repeat(65_536));
      }
    });
    return child;
  };
  await assert.rejects(runClientHelper(client,
    { version: 2, action: "open-web", url: "https://example.org/" }, lifetime(), oversizedFinal),
  /回复超过大小限制/u);
  assert.equal(invocations[0]!.input.trimEnd().split("\n").length, 2);
  assert.deepEqual(invocations[0]!.kills, ["SIGTERM"]);
});

test("a real fake-SSH process holding pipes is killed after the independent deadline", async () => {
  let pid: number | undefined;
  const standIn: HelperSpawn = (_command, _args, options) => {
    const child = spawn(process.execPath, ["-e", "process.on('SIGTERM',()=>{}); setInterval(()=>{},1000)"], options);
    pid = child.pid;
    return child;
  };
  await assert.rejects(runClientHelper(client,
    { version: 2, action: "open-web", url: "https://example.org/" }, lifetime(), standIn, 100),
  /SSH 请求超时/u);
  assert.ok(pid);
  await new Promise((resolve) => setTimeout(resolve, 350));
  assert.throws(() => process.kill(pid!, 0), { code: "ESRCH" });
});

test("invalid PREPARED and lifetime revoked before COMMIT never authorize the helper", async () => {
  const invocations: Invocation[] = [];
  await assert.rejects(dispatchOpenTarget("https://example.org/a", "/work", lifetime(), 802,
    setup(invocations, { spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: "https://attacker.invalid", tunnel: null,
    }) + "\n" })) })), /网页目标不匹配/u);
  assert.equal(invocations[0]!.input.trimEnd().split("\n").length, 1);
  const active = lifetime();
  await assert.rejects(runClientHelper(client,
    { version: 2, action: "open-web", url: "https://example.org/" }, active,
    fakeSsh(invocations, (request) => {
      active.current = false;
      assert.equal(request.action, "open-web");
      return { status: 0, stdout: JSON.stringify({
        ok: true, action: "open-web", openedUrl: request.url, tunnel: null,
      }) + "\n" };
    })), /动作已失效/u);
  assert.equal(invocations[1]!.input.trimEnd().split("\n").length, 1);
});

test("reply processing checks lifetime after SSH completes", async () => {
  const active = lifetime();
  const invocations: Invocation[] = [];
  await assert.rejects(runClientHelper(client,
    { version: 2, action: "open-web", url: "https://example.org/" }, active,
    fakeSsh(invocations, () => {
      active.current = false;
      return { status: 0, stdout: JSON.stringify({ ok: true, action: "open-web",
        openedUrl: "https://example.org/", tunnel: null }) + "\n" };
    })), /动作已失效/u);
});
