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
import type { InputReceiver, InputTopology } from "../src/receiver.ts";
import { sourceLinkEvidence, sourcePathMatches } from "../src/source.ts";

const client: Extract<InputReceiver, { kind: "remote" }> =
  Object.freeze({ kind: "remote", alias: "conley-Y9000p", peer: "192.0.2.64", port: 2222, user: "conley" });
const companyClient: Extract<InputReceiver, { kind: "remote" }> =
  Object.freeze({ kind: "remote", alias: "conley-company", peer: "192.0.2.103", port: 2203, user: "conley" });
const tmux = { TMUX: "/fixture/tmux,1,0", TMUX_PANE: "%4" };
const hop = Object.freeze({
  tty: "/dev/pts/4",
  leader: Object.freeze({ pid: 4100, starttime: "41000" }),
  holder: Object.freeze({ pid: 4200, starttime: "42000", comm: "fixture-holder" }),
  proof: "master" as const,
  role: "terminal" as const,
});
const localTopology: InputTopology = Object.freeze({
  kind: "local", basis: "terminal", hops: Object.freeze([hop]),
});
const remoteTopology: InputTopology = Object.freeze({ kind: "remote", receiver: client, hops: Object.freeze([hop]) });
const tmuxTopology: InputTopology = Object.freeze({
  kind: "tmux", socket: "/fixture/tmux", pane: "%4", inputTty: "/dev/pts/4", hops: Object.freeze([hop]),
});
const unknownTopology: InputTopology = Object.freeze({
  kind: "unknown", diagnostic: "输入持有者 fixture-holder 无法归属到本机桌面或配对 SSH", hops: Object.freeze([hop]),
});

function lifetime(controller = new AbortController()): OpenLifetime & { current: boolean } {
  const value = { signal: controller.signal, current: true, isCurrent: () => value.current };
  return value;
}

type Invocation = { command: string; args: readonly string[]; options: unknown; input: string; kills: string[] };
function fakeSsh(
  invocations: Invocation[],
  reply: (request: HelperRequest) => { status: number; stdout: string; preparedStdout?: string; stderr?: string } | "hang" =
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
          try { payload = JSON.parse(value.preparedStdout ?? value.stdout) as Record<string, unknown>; }
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
  return {
    env: tmux,
    topology: tmuxTopology,
    resolveTopology: async () => tmuxTopology,
    revalidateTopology: async () => true,
    resolve: async () => client,
    spawn: fakeSsh(invocations),
    ...extras,
  };
}

function decoded(invocations: Invocation[]): HelperRequest[] {
  return invocations.map(({ input }) => {
    const { phase: _phase, prepareDeadlineMs: _deadline, ...request } =
      JSON.parse(input.split("\n")[0]!) as Record<string, unknown>;
    return request as HelperRequest;
  });
}

function pinned(invocation: Invocation, receiver = client): void {
  assert.equal(invocation.command, "ssh");
  assert.deepEqual(invocation.options, { stdio: ["pipe", "pipe", "pipe"], shell: false, detached: true });
  assert.deepEqual(invocation.args, ["-T", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes",
    "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", `HostName=${receiver.peer}`,
    "-o", `Port=${receiver.port}`, "-o", `User=${receiver.user}`, receiver.alias, "~/.local/bin/pi-client-link-helper"]);
  assert.ok(invocation.input.endsWith("\n"));
  assert.equal(invocation.input.trimEnd().split("\n").length, 2);
  assert.deepEqual(JSON.parse(invocation.input.split("\n")[1]!),
    { version: 2, phase: "commit", nonce: "a".repeat(32) });
  assert.doesNotMatch(invocation.args.join(" "), /example\.org|work\/|L12/u);
}

test("direct local with stale TMUX and verified tmux-local keep the unchanged local opener", async () => {
  const calls: unknown[][] = [], origins: unknown[] = [], invocations: Invocation[] = [];
  const local: NonNullable<DispatcherDependencies["local"]> = async (...args) => { calls.push(args); };
  const alive = lifetime();
  await dispatchOpenTarget("./file.ts#L12", "/work", alive, "unknown", {
    env: tmux,
    topology: localTopology,
    resolveTopology: async () => localTopology,
    revalidateTopology: async () => true,
    local,
    resolve: async (origin) => { origins.push(origin); throw Error("direct topology must not use tmux resolver"); },
    spawn: fakeSsh(invocations),
  });
  await dispatchOpenTarget("https://example.org", "/work", alive, 802, setup(invocations, {
    local, resolve: async (origin) => { origins.push(origin); return "local"; },
  }));
  assert.deepEqual(calls, [["./file.ts#L12", "/work", alive], ["https://example.org", "/work", alive]]);
  assert.deepEqual(origins, [802]);
  assert.deepEqual(invocations, []);
});

test("known activation route uses cheap revalidation without a full resolver scan", async () => {
  let revalidations = 0, resolutions = 0, localCalls = 0;
  await dispatchOpenTarget("https://example.org/local", "/work", lifetime(), "unknown", {
    topology: localTopology,
    resolveTopology: async () => { resolutions++; throw new Error("known route must not rescan"); },
    revalidateTopology: async (topology) => { revalidations++; assert.equal(topology, localTopology); return true; },
    local: async () => { localCalls++; },
  });
  assert.equal(revalidations, 1);
  assert.equal(resolutions, 0);
  assert.equal(localCalls, 1);
});

test("activation-unknown retries once; direct recovery works while late tmux requires reload", async () => {
  let localCalls = 0;
  await dispatchOpenTarget("https://example.org/local", "/work", lifetime(), "unknown", {
    topology: unknownTopology,
    resolveTopology: async () => localTopology,
    local: async () => { localCalls++; },
  });
  assert.equal(localCalls, 1);

  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), "unknown", {
    topology: unknownTopology,
    resolveTopology: async () => tmuxTopology,
    resolve: async () => assert.fail("late tmux must not use bindings that were never acquired"),
  }), /现已进入 tmux.*\/reload/u);
});

test("direct paired SSH uses the existing pinned v2 helper without tmux", async () => {
  const invocations: Invocation[] = [];
  await dispatchOpenTarget("https://example.org/direct", "/work", lifetime(), "unknown", {
    topology: remoteTopology,
    resolveTopology: async () => remoteTopology,
    revalidateTopology: async () => true,
    spawn: fakeSsh(invocations),
    local: async () => assert.fail("direct SSH must not use the company opener"),
  });
  assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url: "https://example.org/direct" }]);
  pinned(invocations[0]!);
});

test("both admitted aliases pin direct and tagged file/folder actions without adding protocol authority", async () => {
  for (const receiver of [client, companyClient]) {
    for (const direct of [false, true]) {
      const invocations: Invocation[] = [];
      const stats: string[] = [];
      const serverCwd = receiver === client ? "/company" : "/laptop";
      const dependencies = setup(invocations, {
        topology: direct ? Object.freeze({ ...remoteTopology, receiver }) : tmuxTopology,
        resolve: async () => receiver,
        stat: async (serverPath) => {
          stats.push(serverPath);
          return { isFile: () => !serverPath.endsWith("/folder"), isDirectory: () => serverPath.endsWith("/folder") };
        },
        local: async () => assert.fail("remote server resources must never use a local desktop fallback"),
      });
      const source = sourceLinkEvidence("Open `reports/中文%#?.png`");
      const discovered = sourcePathMatches(source, "codespan", "`reports/中文%#?.png`", "reports/中文%#?.png");
      assert.equal(discovered.length, 1);
      for (const href of [discovered[0]!.href, "./file.ts#L12C3", "./folder"]) {
        await dispatchOpenTarget(href, serverCwd, lifetime(), direct ? "unknown" : 802, dependencies);
      }
      assert.deepEqual(stats, [`${serverCwd}/reports/中文%#?.png`, `${serverCwd}/file.ts`, `${serverCwd}/folder`]);
      assert.deepEqual(decoded(invocations), [
        { version: 2, action: "open-file", kind: "file", path: `${serverCwd}/reports/中文%#?.png` },
        { version: 2, action: "open-file", kind: "file", path: `${serverCwd}/file.ts`, line: 12, column: 3 },
        { version: 2, action: "open-file", kind: "directory", path: `${serverCwd}/folder` },
      ]);
      invocations.forEach((invocation) => pinned(invocation, receiver));
      const request = JSON.parse(invocations[0]!.input.split("\n")[0]!);
      assert.deepEqual(Object.keys(request).sort(), ["action", "kind", "path", "phase", "prepareDeadlineMs", "version"]);
    }
  }
});

test("missing/foreign alias or invalid endpoint refuses before SSH, never selecting a default peer", async () => {
  const { alias: _alias, ...withoutAlias } = client;
  const invalid = [withoutAlias, ...["", "other", "conley-Y9000P", "--proxy-command=bad"].map((alias) => ({ ...client, alias })),
    { ...client, peer: "conley-company" }, { ...client, user: "other" }, { ...client, port: 0 },
    { ...client, port: 65_536 }];
  for (const receiver of invalid) {
    for (const direct of [false, true]) {
      const invocations: Invocation[] = [];
      const malformed = receiver as Extract<InputReceiver, { kind: "remote" }>;
      await assert.rejects(dispatchOpenTarget("https://example.org/?alias=conley-company", "/laptop", lifetime(), direct ? "unknown" : 802,
        setup(invocations, {
          topology: direct ? { ...remoteTopology, receiver: malformed } : tmuxTopology,
          resolve: async () => malformed,
          local: async () => assert.fail("malformed remote metadata must not launch locally"),
        })), /已验证的 SSH 客户端地址无效/u);
      assert.deepEqual(invocations, []);
    }
  }
});

test("reverse localhost keeps server backend, URL origin and PREPARED/COMMIT validation", async () => {
  const invocations: Invocation[] = [];
  const url = "https://localhost:8744/p?alias=conley-Y9000p";
  await dispatchOpenTarget(url, "/laptop", lifetime(), 802, setup(invocations, {
    resolve: async () => companyClient,
    probeBackend: async (host) => host === "::1",
    spawn: fakeSsh(invocations, (request) => {
      assert.equal(request.action, "open-web");
      return { status: 0, stdout: JSON.stringify({ ok: true, action: "open-web", openedUrl: request.url,
        tunnel: { remoteHost: request.backendHost, remotePort: 8744, localPort: 8744, reused: false } }) + "\n" };
    }),
    local: async () => assert.fail("reverse loopback must not open on the server desktop"),
  }));
  assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url, backendHost: "::1" }]);
  pinned(invocations[0]!, companyClient);
  await assert.rejects(dispatchOpenTarget(url, "/laptop", lifetime(), 802, setup(invocations, {
    resolve: async () => companyClient,
    probeBackend: async (host) => host === "::1",
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({ ok: true, action: "open-web", openedUrl: url,
      tunnel: { remoteHost: "127.0.0.1", remotePort: 8744, localPort: 8744, reused: false } }) + "\n" })),
  })), /网页\/隧道目标不匹配/u);
  assert.equal(invocations[1]!.input.trimEnd().split("\n").length, 1, "wrong reverse PREPARED must not COMMIT");
});

test("unpaired or unknown direct input refuses with its holder diagnostic", async () => {
  let localCalls = 0;
  const invocations: Invocation[] = [];
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), "unknown", {
    topology: unknownTopology,
    resolveTopology: async () => unknownTopology,
    local: async () => { localCalls++; },
    spawn: fakeSsh(invocations),
  }), /输入终端无法归属.*fixture-holder/u);
  assert.equal(localCalls, 0);
  assert.deepEqual(invocations, []);
});

test("tagged origin in a direct topology and changed holder identity both refuse", async () => {
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), 9999, {
    topology: localTopology,
    revalidateTopology: async () => true,
    local: async () => assert.fail("tagged direct input must not open locally"),
  }), /直接终端收到 tmux 来源标记/u);
  await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), "unknown", {
    topology: localTopology,
    revalidateTopology: async () => false,
    local: async () => assert.fail("changed terminal identity must not open locally"),
  }), /持有者或会话首进程.*变化/u);
});

test("foreign or stale tmux origin still fails closed", async () => {
  const invocations: Invocation[] = [];
  for (const origin of ["unknown", 9999] as const) {
    await assert.rejects(dispatchOpenTarget("https://example.org", "/work", lifetime(), origin,
      setup(invocations, { resolve: async () => { throw new Error("origin not attached or in wrong pane"); } })),
    /origin not attached/u);
  }
  assert.deepEqual(invocations, []);
});

test("verified remote canonical web URL retains its exact loopback origin in JSON", async () => {
  const invocations: Invocation[] = [];
  let localCalls = 0;
  const url = "http://127.0.0.1:8743/a?q=1#frag";
  const deps = setup(invocations, { local: async () => { localCalls++; },
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: url,
      tunnel: { remoteHost: "127.0.0.1", remotePort: 8743, localPort: 8743, reused: false },
    }) + "\n" })) });
  await dispatchOpenTarget("HTTP://127.0.0.1:8743/a?q=1#frag", "/work", lifetime(), 802, deps);
  assert.equal(localCalls, 0);
  assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url }]);
  pinned(invocations[0]!);
});

test("loopback success replies require the source host and exact original port", async () => {
  const invocations: Invocation[] = [];
  const source = "http://127.0.0.1:8743/a";
  for (const [opened, remoteHost, remotePort, localPort] of [
    ["http://127.0.0.1:54465/a", "127.0.0.1", 8743, 54465],
    ["http://127.0.0.2:8743/a", "127.0.0.2", 8743, 8743],
    [source, "127.0.0.1", 8743, 54465],
    [source, "127.0.0.1", 54465, 8743],
  ] as const) {
    const before = invocations.length;
    await assert.rejects(dispatchOpenTarget(source, "/work", lifetime(), 802, setup(invocations, {
      spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
        ok: true, action: "open-web", openedUrl: opened,
        tunnel: { remoteHost, remotePort, localPort, reused: false },
      }) + "\n" })),
    })), /网页\/隧道目标不匹配/u);
    assert.equal(invocations[before]!.input.trimEnd().split("\n").length, 1, "invalid PREPARED must not COMMIT");
  }
  await assert.rejects(dispatchOpenTarget(source, "/work", lifetime(), 802, setup(invocations, {
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: source, tunnel: null,
    }) + "\n" })),
  })), /缺少必需的服务器回环隧道/u);
  const explicitIp = "http://127.0.0.1/path";
  await assert.rejects(dispatchOpenTarget(explicitIp, "/work", lifetime(), 802, setup(invocations, {
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: "http://127.0.0.1:80/path",
      tunnel: { remoteHost: "127.0.0.1", remotePort: 80, localPort: 80, reused: true },
    }) + "\n" })),
  })), /网页\/隧道目标不匹配/u);
  const beforeFinal = invocations.length;
  await assert.rejects(dispatchOpenTarget(source, "/work", lifetime(), 802, setup(invocations, {
    spawn: fakeSsh(invocations, () => ({ status: 0,
      preparedStdout: JSON.stringify({ ok: true, action: "open-web", openedUrl: source,
        tunnel: { remoteHost: "127.0.0.1", remotePort: 8743, localPort: 8743, reused: false } }) + "\n",
      stdout: JSON.stringify({ ok: true, action: "open-web", openedUrl: "http://127.0.0.1:54465/a",
        tunnel: { remoteHost: "127.0.0.1", remotePort: 8743, localPort: 54465, reused: false } }) + "\n",
    })),
  })), /网页\/隧道目标不匹配/u);
  assert.equal(invocations[beforeFinal]!.input.trimEnd().split("\n").length, 2,
    "an altered final reply must not be reported as success after COMMIT");
  const mapped = "http://[::ffff:127.0.0.1]:8743/a";
  const canonical = "http://[::ffff:7f00:1]:8743/a";
  await dispatchOpenTarget(mapped, "/work", lifetime(), 802, setup(invocations, {
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: canonical,
      tunnel: { remoteHost: "::ffff:7f00:1", remotePort: 8743, localPort: 8743, reused: true },
    }) + "\n" })),
  }));
});

test("localhost selects sole reachable numeric company backend without rewriting URL or probing HTTP", async () => {
  for (const reachable of ["::1", "127.0.0.1"] as const) {
    const invocations: Invocation[] = [];
    const probes: Array<[string, number]> = [];
    const url = "https://localhost:8744/a?q=1#frag";
    await dispatchOpenTarget("HTTPS://localhost:8744/a?q=1#frag", "/work", lifetime(), 802,
      setup(invocations, {
        probeBackend: async (host, port) => { probes.push([host, port]); return host === reachable; },
        lookupLocalhost: async () => { throw Error("DNS must not decide a sole reachable family"); },
        spawn: fakeSsh(invocations, (request) => {
          assert.equal(request.action, "open-web");
          return { status: 0, stdout: JSON.stringify({
            ok: true, action: "open-web", openedUrl: request.url,
            tunnel: { remoteHost: request.backendHost, remotePort: 8744, localPort: 8744, reused: false },
          }) + "\n" };
        }),
      }));
    assert.deepEqual(probes, [["127.0.0.1", 8744], ["::1", 8744]]);
    assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url, backendHost: reachable }]);
    pinned(invocations[0]!);
  }
});

test("both reachable localhost families use the first matching company resolver entry", async () => {
  for (const [order, expected] of [
    [[{ address: "::1", family: 6 }, { address: "127.0.0.1", family: 4 }], "::1"],
    [[{ address: "192.0.2.4", family: 4 }, { address: "127.0.0.1", family: 4 },
      { address: "::1", family: 6 }], "127.0.0.1"],
  ] as const) {
    const invocations: Invocation[] = [];
    let dnsCalls = 0;
    await dispatchOpenTarget("http://localhost:3456/path", "/work", lifetime(), 802,
      setup(invocations, {
        probeBackend: async () => true,
        lookupLocalhost: async () => { dnsCalls++; return order; },
        spawn: fakeSsh(invocations, (request) => {
          assert.equal(request.action, "open-web");
          return { status: 0, stdout: JSON.stringify({
            ok: true, action: "open-web", openedUrl: request.url,
            tunnel: { remoteHost: request.backendHost, remotePort: 3456, localPort: 3456, reused: true },
          }) + "\n" };
        }),
      }));
    assert.equal(dnsCalls, 1);
    assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web",
      url: "http://localhost:3456/path", backendHost: expected }]);
  }
});

test("unreachable or resolver-ambiguous localhost fails before SSH with no guessed backend", async () => {
  for (const [v4, v6, addresses, error] of [
    [false, false, [], /均无 TCP 服务/u],
    [true, true, [], /DNS 未返回已验证/u],
    [true, true, [{ address: "127.0.0.2", family: 4 }], /DNS 未返回已验证/u],
  ] as const) {
    const invocations: Invocation[] = [];
    await assert.rejects(dispatchOpenTarget("http://localhost:4234/", "/work", lifetime(), 802,
      setup(invocations, {
        probeBackend: async (host) => host === "::1" ? v6 : v4,
        lookupLocalhost: async () => addresses,
      })), error);
    assert.deepEqual(invocations, []);
  }
});

test("localhost TCP and DNS probes obey abort and one bounded deadline before SSH", async () => {
  const invocations: Invocation[] = [];
  const controller = new AbortController();
  const active = lifetime(controller);
  let started!: () => void;
  const bothStarted = new Promise<void>((resolve) => { started = resolve; });
  const signals: AbortSignal[] = [];
  const pending = dispatchOpenTarget("http://localhost:4234/", "/work", active, 802,
    setup(invocations, { probeBackend: async (_host, _port, signal) => {
      signals.push(signal);
      if (signals.length === 2) started();
      return await new Promise<boolean>(() => {});
    } }));
  await bothStarted;
  controller.abort();
  await assert.rejects(pending, /打开动作已失效/u);
  assert.ok(signals.every((signal) => signal.aborted));
  assert.deepEqual(invocations, []);

  const tcpSignals: AbortSignal[] = [];
  await assert.rejects(dispatchOpenTarget("https://localhost/", "/work", lifetime(), 802,
    setup(invocations, { backendTimeoutMs: 15, probeBackend: async (_host, _port, signal) => {
      tcpSignals.push(signal);
      return await new Promise<boolean>(() => {});
    } })), /TCP\/DNS 探测超时/u);
  assert.ok(tcpSignals.every((signal) => signal.aborted));
  let dnsSignal: AbortSignal | undefined;
  await assert.rejects(dispatchOpenTarget("https://localhost/", "/work", lifetime(), 802,
    setup(invocations, { backendTimeoutMs: 15, probeBackend: async () => true,
      lookupLocalhost: async (signal) => { dnsSignal = signal; return await new Promise(() => {}); },
    })), /TCP\/DNS 探测超时/u);
  assert.equal(dnsSignal?.aborted, true);
  assert.deepEqual(invocations, []);
});

test("localhost PREPARED must attest selected backend, original hostname and exact port", async () => {
  const url = "https://localhost:8453/p";
  for (const [openedUrl, remoteHost, localPort] of [
    [url, "127.0.0.1", 8453],
    ["https://127.0.0.1:8453/p", "::1", 8453],
    [url, "::1", 45555],
  ] as const) {
    const invocations: Invocation[] = [];
    await assert.rejects(dispatchOpenTarget(url, "/work", lifetime(), 802, setup(invocations, {
      probeBackend: async (host) => host === "::1",
      spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
        ok: true, action: "open-web", openedUrl,
        tunnel: { remoteHost, remotePort: 8453, localPort, reused: false },
      }) + "\n" })),
    })), /网页\/隧道目标不匹配/u);
    assert.equal(invocations[0]!.input.trimEnd().split("\n").length, 1,
      "wrong PREPARED must not COMMIT");
  }
  const invocations: Invocation[] = [];
  await assert.rejects(dispatchOpenTarget(url, "/work", lifetime(), 802, setup(invocations, {
    probeBackend: async (host) => host === "::1",
    spawn: fakeSsh(invocations, () => ({ status: 0, stdout: JSON.stringify({
      ok: true, action: "open-web", openedUrl: url, tunnel: null,
    }) + "\n" })),
  })), /缺少必需的服务器回环隧道/u);
  assert.equal(invocations[0]!.input.trimEnd().split("\n").length, 1);
});

test("external, explicit IP, file and local routes never consult localhost probes", async () => {
  const invocations: Invocation[] = [];
  const calls: string[] = [];
  const deps = setup(invocations, {
    probeBackend: async () => { throw Error("unexpected localhost probe"); },
    lookupLocalhost: async () => { throw Error("unexpected localhost DNS"); },
    local: async () => { calls.push("local"); },
    stat: async () => ({ isFile: () => true, isDirectory: () => false }),
    spawn: fakeSsh(invocations, (request) => {
      if (request.action === "open-file") return { status: 0, stdout: JSON.stringify({
        ok: true, action: "open-file", kind: request.kind, path: request.path,
      }) + "\n" };
      return { status: 0, stdout: JSON.stringify({ ok: true, action: "open-web",
        openedUrl: request.url, tunnel: request.url.includes("127.0.0.1") ?
          { remoteHost: "127.0.0.1", remotePort: 8453, localPort: 8453, reused: false } : null,
      }) + "\n" };
    }),
  });
  await dispatchOpenTarget("http://127.0.0.1:8453/p", "/work", lifetime(), 802, deps);
  await dispatchOpenTarget("https://[::1]:8453/p", "/work", lifetime(), 802,
    { ...deps, spawn: fakeSsh(invocations, (request) => {
      assert.equal(request.action, "open-web");
      return { status: 0, stdout: JSON.stringify({
        ok: true, action: "open-web", openedUrl: request.url,
        tunnel: { remoteHost: "::1", remotePort: 8453, localPort: 8453, reused: false },
      }) + "\n" };
    }) });
  await dispatchOpenTarget("https://example.org/x", "/work", lifetime(), 802, deps);
  await dispatchOpenTarget("./file.txt", "/work", lifetime(), 802, deps);
  await dispatchOpenTarget("https://localhost:8453/p", "/work", lifetime(), 802,
    { ...deps, resolve: async () => "local" });
  assert.deepEqual(decoded(invocations), [
    { version: 2, action: "open-web", url: "http://127.0.0.1:8453/p" },
    { version: 2, action: "open-web", url: "https://[::1]:8453/p" },
    { version: 2, action: "open-web", url: "https://example.org/x" },
    { version: 2, action: "open-file", kind: "file", path: "/work/file.txt" },
  ]);
  assert.deepEqual(calls, ["local"]);
});

test("both aliases retain literal peer/user/port pinning in real OpenSSH after alias drift", async () => {
  for (const receiver of [client, companyClient]) {
    const invocations: Invocation[] = [];
    let aliasNow = receiver.peer;
    const dependencies = setup(invocations, { resolve: async () => {
      aliasNow = "203.0.113.8"; // Equivalent to ssh_config changing after `ssh -G`.
      return receiver;
    } });
    await dispatchOpenTarget("https://example.org/a", "/work", lifetime(), 802, dependencies);
    assert.equal(aliasNow, "203.0.113.8");
    pinned(invocations[0]!, receiver);
    assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-web", url: "https://example.org/a" }]);
    // OpenSSH's actual config expansion must honor the verified endpoint even
    // after a fixture alias changes host/user/port. No real host is contacted.
    const root = mkdtempSync(join(tmpdir(), "pi-dispatcher-config-"));
    try {
      const config = join(root, "config");
      writeFileSync(config, `Host ${receiver.alias}\n HostName 203.0.113.8\n User changed\n Port 2200\n`);
      const effective = execFileSync("ssh", ["-G", "-F", config, ...invocations[0]!.args.slice(0, -1)],
        { encoding: "utf8" });
      assert.ok(effective.includes(`hostname ${receiver.peer}\n`));
      assert.match(effective, /^user conley$/mu);
      assert.ok(effective.includes(`port ${receiver.port}\n`));
    } finally { rmSync(root, { recursive: true, force: true }); }
  }
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
    invocations.forEach((invocation) => pinned(invocation));
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("auto-discovered paths retain company validation and the pinned paired v2 file route", async () => {
  for (const direct of [false, true]) {
    const invocations: Invocation[] = [];
    const stats: string[] = [];
    const path = "reports/中文%#?.png";
    const evidence = sourceLinkEvidence(`Open \`${path}\``);
    const discovered = sourcePathMatches(evidence, "codespan", `\`${path}\``, path);
    assert.equal(discovered.length, 1);
    const dependencies = setup(invocations, {
      ...(direct ? { topology: remoteTopology } : {}),
      stat: async (companyPath) => {
        stats.push(companyPath);
        return { isFile: () => true, isDirectory: () => false };
      },
      local: async () => assert.fail("an automatic company path must never fall back to company GUI"),
      probeBackend: async () => assert.fail("files do not use browser tunnel preparation"),
    });
    assert.deepEqual(stats, [], "source/native path discovery performs no filesystem IO");
    await dispatchOpenTarget(discovered[0]!.href, "/company/click-cwd", lifetime(), direct ? "unknown" : 802, dependencies);
    assert.deepEqual(stats, ["/company/click-cwd/reports/中文%#?.png"], "stat runs exactly once on company");
    assert.deepEqual(decoded(invocations), [{ version: 2, action: "open-file", kind: "file",
      path: "/company/click-cwd/reports/中文%#?.png" }]);
    pinned(invocations[0]!);
  }
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
  for (const receiver of [client, companyClient]) {
    for (const [label, status, stdout, pattern] of modes) {
      const invocations: Invocation[] = [];
      await assert.rejects(dispatchOpenTarget("https://example.org/a", "/work", lifetime(), 802,
        setup(invocations, { resolve: async () => receiver, local: async () => { localCalls++; },
          spawn: fakeSsh(invocations, () => ({ status, stdout })) })), (error: unknown) => {
        assert.ok(error instanceof Error);
        assert.match(error.message, pattern, label);
        assert.doesNotMatch(error.message, /Y9000p|公司/u, "diagnostics must describe client/server roles in either direction");
        return true;
      });
      assert.equal(invocations.length, 1, label);
    }
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
