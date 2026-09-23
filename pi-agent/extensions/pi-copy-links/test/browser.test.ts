import assert from "node:assert/strict";
import { spawn as nodeSpawn, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import {
  launchApplication, openWebUrl, runBoundedCommand, webUrl,
  type OpenLifetime, type QueryRunner, type QuerySpawnRunner, type SpawnRunner,
} from "../src/browser.ts";

function lifetime(controller = new AbortController()): OpenLifetime & { current: boolean } {
  const value = { signal: controller.signal, current: true, isCurrent: () => value.current };
  return value;
}

function childRecorder(records: Array<{ command: string; args: readonly string[]; options: object }>, exit = 0): SpawnRunner {
  return (command, args, options) => {
    records.push({ command, args, options });
    const child = new EventEmitter() as ReturnType<SpawnRunner>;
    child.unref = () => child;
    queueMicrotask(() => child.emit("exit", exit, null));
    return child;
  };
}

function xdg(defaultId = "google-chrome.desktop", check = "yes", calls: string[][] = []): QueryRunner {
  return async (command, args, options) => {
    assert.equal(command, "xdg-settings");
    assert.equal(options.timeoutMs, 2500);
    assert.equal(options.killGraceMs, 200);
    assert.equal(options.maxBuffer, 4096);
    calls.push([...args]);
    return { stdout: args[0] === "get" ? `${defaultId}\n` : `${check}\n`, stderr: "" };
  };
}

const delay = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

async function processGone(pid: number, timeoutMs: number): Promise<boolean> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    try { process.kill(pid, 0); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ESRCH") return true; }
    await delay(20);
  }
  return false;
}

async function processGroupGone(pgid: number, timeoutMs: number): Promise<boolean> {
  const deadline = performance.now() + timeoutMs;
  while (performance.now() < deadline) {
    try { process.kill(-pgid, 0); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === "ESRCH") return true; }
    await delay(20);
  }
  return false;
}

function assertQueryOwnershipReleased(child: ChildProcess): void {
  assert.equal(child.listenerCount("error"), 0);
  assert.equal(child.listenerCount("exit"), 0);
  assert.equal(child.listenerCount("close"), 0);
  assert.equal(child.stdout?.listenerCount("data"), 0);
  assert.equal(child.stdout?.listenerCount("error"), 0);
  assert.equal(child.stderr?.listenerCount("data"), 0);
  assert.equal(child.stderr?.listenerCount("error"), 0);
  assert.ok(child.stdout?.destroyed || child.stdout?.readableEnded, "stdout has no live pipe handle");
  assert.ok(child.stderr?.destroyed || child.stderr?.readableEnded, "stderr has no live pipe handle");
}

async function runDelayedWriterCase(delayMs: number) {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-delayed-output-"));
  const pidFile = join(root, "descendant.pid");
  let leader: ChildProcess | undefined;
  let descendantPid: number | undefined;
  try {
    const started = performance.now();
    const result = await runBoundedCommand("/bin/sh", [
      "-c",
      `( sleep ${(delayMs / 1000).toFixed(3)}; printf 'delayed-output\\n' ) & printf '%s' "$!" > "$1"; exit 0`,
      "delayed-writer",
      pidFile,
    ], {
      signal: new AbortController().signal,
      timeoutMs: 1_000,
      killGraceMs: 100,
      maxBuffer: 4_096,
    }, (command, args, options) => {
      leader = nodeSpawn(command, [...args], options);
      return leader;
    });
    const elapsed = performance.now() - started;
    descendantPid = Number(readFileSync(pidFile, "utf8"));
    const completedLeader = leader;
    assert.ok(completedLeader?.pid, "captured the detached leader pid");
    const descendantGone = await processGone(descendantPid, 1_000);
    const groupGone = await processGroupGone(completedLeader.pid, 1_000);
    return { result, elapsed, leader: completedLeader, descendantPid, descendantGone, groupGone };
  } finally {
    if (leader?.pid) { try { process.kill(-leader.pid, "SIGKILL"); } catch { /* already gone */ } }
    if (descendantPid) { try { process.kill(descendantPid, "SIGKILL"); } catch { /* already gone */ } }
    await delay(20);
    rmSync(root, { recursive: true, force: true });
  }
}

async function runHeldPipeCase(timeoutMs: number, killGraceMs: number) {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-held-pipe-"));
  const pidFile = join(root, "descendant.pid");
  const readyFile = join(root, "ready");
  let leader: ChildProcess | undefined;
  let descendantPid: number | undefined;
  try {
    const started = performance.now();
    let error: unknown;
    try {
      await runBoundedCommand("/bin/sh", [
        "-c",
        `( trap '' TERM; printf ready > "$2"; while :; do sleep 1; done ) & ` +
          `printf '%s' "$!" > "$1"; while [ ! -s "$2" ]; do sleep 0.01; done; exit 0`,
        "held-pipe",
        pidFile,
        readyFile,
      ], {
        signal: new AbortController().signal,
        timeoutMs,
        killGraceMs,
        maxBuffer: 4_096,
      }, (command, args, options) => {
        leader = nodeSpawn(command, [...args], options);
        return leader;
      });
    } catch (current) { error = current; }
    const elapsed = performance.now() - started;
    const completedLeader = leader;
    assert.ok(completedLeader?.pid, "captured the detached leader pid");
    assert.ok(existsSync(readyFile), "descendant installed its TERM handler before leader exit");
    descendantPid = Number(readFileSync(pidFile, "utf8"));
    const descendantGone = await processGone(descendantPid, 1_000);
    const groupGone = await processGroupGone(completedLeader.pid, 1_000);
    return { error, elapsed, leader: completedLeader, descendantPid, descendantGone, groupGone };
  } finally {
    if (leader?.pid) { try { process.kill(-leader.pid, "SIGKILL"); } catch { /* already gone */ } }
    if (descendantPid) { try { process.kill(descendantPid, "SIGKILL"); } catch { /* already gone */ } }
    await delay(20);
    rmSync(root, { recursive: true, force: true });
  }
}

test("web validation preserves browser data and rejects whitespace, controls and other schemes", () => {
  const value = "https://example.com/path?q=a&cmd=$(touch%20evil);x='y'#中文";
  assert.equal(webUrl(value), new URL(value).href);
  for (const invalid of [
    "file:///etc/passwd", "javascript:alert(1)", "pi-copy://x/1", "--help", "http:example.com",
    "https://example.com/a b", "https://example.com/\nnext", "https://example.com/\u0085x",
  ]) assert.equal(webUrl(invalid), undefined, invalid);
});

test("configured Chrome default launches one explicit new-window argv without a shell", async () => {
  const queryCalls: string[][] = [];
  const spawns: Array<{ command: string; args: readonly string[]; options: object }> = [];
  const url = "https://example.com/path?a=1&b=$()';#中文";
  await openWebUrl(url, lifetime(), {
    platform: "linux", query: xdg("google-chrome.desktop", "yes", queryCalls), spawn: childRecorder(spawns),
  });
  assert.deepEqual(queryCalls, [
    ["get", "default-web-browser"],
    ["check", "default-web-browser", "google-chrome.desktop"],
  ]);
  assert.equal(spawns.length, 1);
  assert.equal(spawns[0]!.command, "google-chrome-stable");
  assert.deepEqual(spawns[0]!.args, ["--new-window", new URL(url).href]);
  assert.deepEqual(spawns[0]!.options, { stdio: "ignore", shell: false, detached: true });
});

test("unknown, inconsistent, malformed and non-Linux defaults fail without fallback", async () => {
  for (const [platform, id, check, pattern] of [
    ["linux", "firefox.desktop", "yes", /不支持默认浏览器/],
    ["linux", "google-chrome.desktop", "no", /关联不一致/],
    ["linux", "two ids.desktop\nother.desktop", "yes", /无效结果/],
    ["darwin", "google-chrome.desktop", "yes", /仅支持 Linux/],
  ] as const) {
    const spawns: Array<{ command: string; args: readonly string[]; options: object }> = [];
    await assert.rejects(
      openWebUrl("https://example.com", lifetime(), { platform, query: xdg(id, check), spawn: childRecorder(spawns) }),
      pattern,
    );
    assert.deepEqual(spawns, []);
  }
});

test("browser query failure is explicit and starts no fallback process", async () => {
  const spawns: Array<{ command: string; args: readonly string[]; options: object }> = [];
  const query: QueryRunner = async () => { throw new Error("ETIMEDOUT"); };
  await assert.rejects(
    openWebUrl("https://example.com", lifetime(), { platform: "linux", query, spawn: childRecorder(spawns) }),
    /无法查询默认浏览器.*ETIMEDOUT/,
  );
  assert.deepEqual(spawns, []);
});

test("aborting a paused browser query prevents application spawn", async () => {
  const controller = new AbortController();
  const current = lifetime(controller);
  const spawns: Array<{ command: string; args: readonly string[]; options: object }> = [];
  const query: QueryRunner = async (_command, _args, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  });
  const pending = openWebUrl("https://example.com", current, {
    platform: "linux", query, spawn: childRecorder(spawns),
  });
  controller.abort();
  await assert.rejects(pending, /动作已失效/);
  assert.deepEqual(spawns, []);
});

test("launcher errors and nonzero exits are explicit and never trigger a fallback", async () => {
  for (const failure of ["error", "exit"] as const) {
    const spawn: SpawnRunner = () => {
      const child = new EventEmitter() as ReturnType<SpawnRunner>;
      child.unref = () => child;
      child.kill = () => true;
      queueMicrotask(() => failure === "error" ? child.emit("error", new Error("ENOENT")) : child.emit("exit", 23, null));
      return child;
    };
    await assert.rejects(
      openWebUrl("https://example.com", lifetime(), { platform: "linux", query: xdg(), spawn }),
      failure === "error" ? /google-chrome-stable: ENOENT/ : /退出码 23/,
    );
  }
});

test("throwing unref rejects transactionally, detaches observers and kills the unhanded process", async () => {
  class CountingSignal extends EventTarget {
    aborted = false;
    reason: unknown;
    listeners = 0;
    override addEventListener(...args: Parameters<EventTarget["addEventListener"]>) {
      if (args[0] === "abort") this.listeners += 1;
      return super.addEventListener(...args);
    }
    override removeEventListener(...args: Parameters<EventTarget["removeEventListener"]>) {
      if (args[0] === "abort") this.listeners -= 1;
      return super.removeEventListener(...args);
    }
  }
  const signal = new CountingSignal();
  const kills: Array<string | number | undefined> = [];
  let retained = true;
  const child = Object.assign(new EventEmitter(), {
    unref() { throw new Error("unref failed"); },
    kill(value?: string | number) { kills.push(value); retained = false; return true; },
  }) as unknown as ReturnType<SpawnRunner>;
  const current: OpenLifetime = { signal: signal as unknown as AbortSignal, isCurrent: () => true };
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  const outer = new Promise<never>((_resolve, reject) => {
    watchdog = setTimeout(() => reject(new Error("outer watchdog expired")), 250);
  });

  try {
    await assert.rejects(Promise.race([
      launchApplication({ command: "fixture-app", args: [] }, current, () => child),
      outer,
    ]), /unref failed/);
  } finally {
    if (watchdog) clearTimeout(watchdog);
  }
  assert.equal(child.listenerCount("error"), 0);
  assert.equal(child.listenerCount("exit"), 0);
  assert.equal(signal.listeners, 0);
  assert.deepEqual(kills, ["SIGTERM", "SIGKILL"]);
  assert.equal(retained, false, "failed handoff leaves no ref'ed live fake child");
});

test("retirement detaches a successfully handed-off application without killing it", async () => {
  const controller = new AbortController();
  const current = lifetime(controller);
  let child!: EventEmitter & { unref(): unknown; killCalls: number };
  const spawn: SpawnRunner = () => {
    child = Object.assign(new EventEmitter(), {
      unref() { return this; },
      killCalls: 0,
      kill() { this.killCalls += 1; return true; },
    });
    return child as unknown as ReturnType<SpawnRunner>;
  };
  const pending = openWebUrl("https://example.com", current, { platform: "linux", query: xdg(), spawn });
  await new Promise<void>((resolve) => setImmediate(resolve));
  controller.abort();
  await pending;
  assert.equal(child.killCalls, 0);
  assert.equal(child.listenerCount("exit"), 0);
  assert.equal(child.listenerCount("error"), 0);
});

test("close-before-exit and stream-end races wait for a known leader status and preserve output", async () => {
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  const child = Object.assign(new EventEmitter(), {
    stdout,
    stderr,
    kill() { return true; },
  }) as unknown as ReturnType<QuerySpawnRunner>;
  const pending = runBoundedCommand("race-fixture", [], {
    signal: new AbortController().signal, timeoutMs: 500, killGraceMs: 20, maxBuffer: 128,
  }, () => {
    queueMicrotask(() => {
      child.emit("close", null, null);
      stdout.emit("data", Buffer.from("race-output\n"));
      stdout.emit("end");
      stderr.emit("end");
      child.emit("exit", 0, null);
      stdout.emit("close");
      stderr.emit("close");
      stdout.destroy();
      stderr.destroy();
    });
    return child;
  });
  assert.deepEqual(await pending, { stdout: "race-output\n", stderr: "" });
  assertQueryOwnershipReleased(child);
});

test("query waits past leader exit for delayed descendant stdout and pipe closure", { timeout: 5_000 }, async () => {
  if (process.platform === "win32") return;
  const evidence = await runDelayedWriterCase(80);
  assert.deepEqual(evidence.result, { stdout: "delayed-output\n", stderr: "" });
  assert.ok(evidence.elapsed >= 60, `settled after ${evidence.elapsed} ms instead of leader exit`);
  assert.ok(evidence.elapsed < 1_000);
  assert.equal(evidence.descendantGone, true);
  assert.equal(evidence.groupGone, true);
  assertQueryOwnershipReleased(evidence.leader);
});

test("leader exit with a TERM-ignoring inherited-pipe descendant remains owned through deadline cleanup", { timeout: 5_000 }, async () => {
  if (process.platform === "win32") return;
  const evidence = await runHeldPipeCase(250, 75);
  assert.match(String(evidence.error), /timed out after 250 ms/);
  assert.ok(evidence.elapsed >= 200, `settled prematurely at ${evidence.elapsed} ms`);
  assert.ok(evidence.elapsed < 1_500);
  assert.equal(evidence.descendantGone, true, "SIGKILL escalation removed the TERM-ignoring pipe holder");
  assert.equal(evidence.groupGone, true, "the owned detached group is gone");
  assertQueryOwnershipReleased(evidence.leader);
});

test("repeated delayed-output and held-pipe variants leave zero owned listeners, handles or processes", { timeout: 10_000 }, async () => {
  if (process.platform === "win32") return;
  for (let pass = 0; pass < 3; pass++) {
    const delayed = await runDelayedWriterCase(35);
    assert.equal(delayed.result.stdout, "delayed-output\n");
    assert.equal(delayed.descendantGone, true);
    assert.equal(delayed.groupGone, true);
    assertQueryOwnershipReleased(delayed.leader);

    const held = await runHeldPipeCase(180, 50);
    assert.match(String(held.error), /timed out after 180 ms/);
    assert.equal(held.descendantGone, true);
    assert.equal(held.groupGone, true);
    assertQueryOwnershipReleased(held.leader);
  }
});

test("repeated query deadlines leave no child or stream listener accumulation", async () => {
  const children: Array<ChildProcessProbe> = [];
  interface ChildProcessProbe extends EventEmitter {
    stdout: PassThrough;
    stderr: PassThrough;
    kills: NodeJS.Signals[];
    kill(signal?: NodeJS.Signals | number): boolean;
  }
  const run: QuerySpawnRunner = () => {
    const child = Object.assign(new EventEmitter(), {
      stdout: new PassThrough(),
      stderr: new PassThrough(),
      kills: [] as NodeJS.Signals[],
      kill(signal: NodeJS.Signals = "SIGTERM") { this.kills.push(signal); return true; },
    }) as ChildProcessProbe;
    children.push(child);
    return child as unknown as ReturnType<QuerySpawnRunner>;
  };
  const controller = new AbortController();
  const results = await Promise.allSettled(Array.from({ length: 8 }, () =>
    runBoundedCommand("fixture-query", [], {
      signal: controller.signal, timeoutMs: 10, killGraceMs: 10, maxBuffer: 32,
    }, run)));
  assert.ok(results.every((result) => result.status === "rejected" && /timed out/.test(String(result.reason))));
  await delay(30);
  for (const child of children) {
    assert.deepEqual(child.kills, ["SIGTERM", "SIGKILL"]);
    assert.equal(child.listenerCount("error"), 0);
    assert.equal(child.listenerCount("exit"), 0);
    assert.equal(child.stdout.listenerCount("data"), 0);
    assert.equal(child.stderr.listenerCount("data"), 0);
  }
});

test("owned query timeout settles independently and escalates against a SIGTERM-ignoring child", { timeout: 5_000 }, async () => {
  if (process.platform === "win32") return;
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-hard-timeout-"));
  const ready = join(root, "ready");
  const term = join(root, "term");
  const helper = join(root, "ignore-term.mjs");
  writeFileSync(helper, `
    import { appendFileSync, writeFileSync } from "node:fs";
    process.on("SIGTERM", () => appendFileSync(${JSON.stringify(term)}, "TERM\\n"));
    writeFileSync(${JSON.stringify(ready)}, String(process.pid));
    setInterval(() => {}, 1000);
  `);
  let pid: number | undefined;
  const run: QuerySpawnRunner = (command, args, options) => {
    const child = nodeSpawn(command, [...args], options);
    pid = child.pid;
    return child;
  };
  const controller = new AbortController();
  const started = performance.now();
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  try {
    const outer = new Promise<never>((_resolve, reject) => {
      watchdog = setTimeout(() => reject(new Error("outer watchdog expired")), 1_500);
    });
    await assert.rejects(Promise.race([
      runBoundedCommand(process.execPath, [helper], {
        signal: controller.signal, timeoutMs: 300, killGraceMs: 100, maxBuffer: 4096,
      }, run),
      outer,
    ]), /timed out after 300 ms/);
    assert.ok(performance.now() - started < 1_200, "deadline settles before the robust outer watchdog");
    assert.ok(existsSync(ready), "real helper reached its SIGTERM handler setup");
    const recordedPid = Number(readFileSync(ready, "utf8"));
    assert.equal(pid, recordedPid);
    const deadline = performance.now() + 500;
    let termEvidence = "";
    while (!termEvidence && performance.now() < deadline) {
      if (existsSync(term)) termEvidence = readFileSync(term, "utf8");
      if (!termEvidence) await delay(10);
    }
    assert.match(termEvidence, /TERM/, "helper observed and ignored SIGTERM");
    assert.equal(await processGone(recordedPid, 1_000), true, "bounded SIGKILL cleanup removed the helper group");
  } finally {
    if (watchdog) clearTimeout(watchdog);
    if (pid) {
      try { process.kill(-pid, "SIGKILL"); } catch { /* test-owned group is already gone */ }
    }
    rmSync(root, { recursive: true, force: true });
  }
});
