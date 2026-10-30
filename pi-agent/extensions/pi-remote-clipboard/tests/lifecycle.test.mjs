import { execFileSync, spawn } from "node:child_process";
import { chmodSync, existsSync, linkSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";
import { afterEach, beforeEach, mock, test } from "node:test";

// Session-lifecycle tests: every child, UI object and clipboard byte is synthetic.
// No Pi session, provider, tmux server, physical clipboard or SSH connection is used.
let coreRoot = dirname(realpathSync(execFileSync("sh", ["-c", "command -v pi"], { encoding: "utf8" }).trim()));
while (coreRoot !== dirname(coreRoot)) {
  const pkg = join(coreRoot, "package.json");
  if (existsSync(pkg) && JSON.parse(readFileSync(pkg, "utf8")).name === "@earendil-works/pi-coding-agent") break;
  coreRoot = dirname(coreRoot);
}
const coreUrl = pathToFileURL(join(coreRoot, "dist/index.js")).href;
const tuiUrl = pathToFileURL(join(coreRoot, "node_modules/@earendil-works/pi-tui/dist/index.js")).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@earendil-works/pi-coding-agent") return { url: coreUrl, shortCircuit: true };
    if (specifier === "@earendil-works/pi-tui") return { url: tuiUrl, shortCircuit: true };
    if (specifier === "node:fs/promises" && context.parentURL?.endsWith("index.ts?faulty-fs")) {
      return { url: new URL("./fs-promises-fault.mjs", import.meta.url).href, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const temp = mkdtempSync(join(tmpdir(), "pi-remote-clipboard-lifecycle-"));
process.env.PI_REMOTE_CLIPBOARD_DIR = temp;
// Paste operations stage and deliver files under os.tmpdir(); keep them inside this fixture.
mkdirSync(join(temp, "tmpdir"));
process.env.TMPDIR = join(temp, "tmpdir");
const { InteractiveMode } = await import(coreUrl);
const { TuiAltScreen } = await import(tuiUrl);
const { default: piRemoteClipboard } = await import("../pi-extension/index.ts");
const { default: faultyClipboard } = await import("../pi-extension/index.ts?faulty-fs");

const pasteOwner = InteractiveMode.prototype;
const copyOwner = TuiAltScreen.prototype;
const sinkKey = Symbol.for("pi-remote-clipboard:copy-sink");
const pasteDescriptor = Object.getOwnPropertyDescriptor(pasteOwner, "handleClipboardPaste");
const copyDescriptor = Object.getOwnPropertyDescriptor(copyOwner, "copyTextToClipboard");
const sinkDescriptor = Object.getOwnPropertyDescriptor(globalThis, sinkKey);
const envKeys = ["TMUX", "TMUX_PANE", "SSH_CONNECTION", "SSH_CLIENT", "MOSH_CONNECTION", "PATH"];
const savedEnv = Object.fromEntries(envKeys.map((key) => [key, process.env[key]]));
const stdoutWrite = process.stdout.write;
const ttyDescriptor = Object.getOwnPropertyDescriptor(process.stdout, "isTTY");

const sleepTurn = () => new Promise((resolve) => setImmediate(resolve));
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const childHandles = () => process._getActiveHandles().filter((h) => h.constructor.name === "ChildProcess").length;
const names = (prefix) => readdirSync(temp).filter((name) => name.startsWith(prefix));
async function until(predicate, what) {
  for (let turn = 0; turn < 20000; turn += 1) {
    if (predicate()) return;
    await sleepTurn();
  }
  throw new Error(`timed out waiting for ${what}`);
}
const startedPids = () => names("started-").map((name) => Number(name.slice(8)));

// The only helper the extension runs for owned pastes (versioned artifact endpoint).
const ENDPOINT = "ctrlv-artifact-v1.py";
function writeHelper(name, source) {
  writeFileSync(join(temp, name), source);
}
// mode file: "ignoreterm" ignores SIGTERM; "fail" exits 1 after release; "grandchild" leaves a pipe-holding sleeper.
const helperSource = `import os, pathlib, signal, subprocess, sys, time
p = pathlib.Path(${JSON.stringify(temp)})
mode = (p / 'mode').read_text()
pid = os.getpid()
if 'ignoreterm' in mode:
    signal.signal(signal.SIGTERM, lambda *_: (p / ('term-' + str(pid))).write_text('seen'))
if 'grandchild' in mode:
    g = subprocess.Popen(['sleep', '60'])
    (p / ('grandchild-' + str(g.pid))).write_text('x')
(p / ('started-' + str(pid))).write_text('ready')
while not (p / 'release').exists():
    time.sleep(.01)
if 'fail' in mode:
    sys.exit(1)
if len(sys.argv) == 2:  # the versioned endpoint is called with exactly the staging directory
    sys.stdout.write('{"protocol":"pi-remote-clipboard/artifact-1","kind":"text"}\\n')
print('synthetic clipboard text')
`;
writeHelper(ENDPOINT, helperSource);
writeHelper("push.py", helperSource);

let fixtures = [];
function loadExtension(mode = "tui", factory = piRemoteClipboard) {
  const shortcuts = new Map();
  const commands = new Map();
  const events = new Map();
  const state = { ended: false };
  const notices = [];
  const pastes = [];
  const inserts = [];
  factory({
    registerShortcut(key, options) { shortcuts.set(key, options); },
    registerCommand(key, options) { commands.set(key, options); },
    on(event, handler) { events.set(event, handler); },
  });
  const ctx = {
    mode,
    ui: {
      notify(text, level) { notices.push({ afterShutdown: state.ended, text, level }); },
      pasteToEditor(text) { pastes.push({ afterShutdown: state.ended, text }); },
    },
  };
  const extension = {
    notices, pastes, inserts, ctx,
    start() { events.get("session_start")?.({ type: "session_start" }, ctx); },
    shutdown() { state.ended = true; return events.get("session_shutdown")?.({ type: "session_shutdown" }, ctx); },
    explicitPaste: () => shortcuts.get("ctrl+alt+v").handler(ctx),
    push: (args = "") => commands.get("rpush").handler(args, ctx),
    automaticPaste: () => pasteOwner.handleClipboardPaste.call({
      editor: { insertTextAtCursor(text) { inserts.push({ afterShutdown: state.ended, text }); } },
      ui: { requestRender() {} },
    }),
  };
  fixtures.push(extension);
  return extension;
}

beforeEach(() => {
  for (const name of readdirSync(join(temp, "tmpdir"))) rmSync(join(temp, "tmpdir", name), { recursive: true, force: true });
  rmSync(join(temp, "victim"), { force: true });
  delete globalThis.__rcFsFaults;
  for (const key of envKeys) if (key !== "PATH") delete process.env[key];
  for (const name of readdirSync(temp)) if (/^(started|term|grandchild|release|tmux|legacy-ran|stale-ran)/.test(name)) rmSync(join(temp, name), { force: true });
  writeFileSync(join(temp, "mode"), "");
  // The native seams are inert recorders so a stale fallback is observable.
  globalThis.nativePaste = 0;
  globalThis.nativeCopy = 0;
  Object.defineProperty(pasteOwner, "handleClipboardPaste", { ...pasteDescriptor, value: async () => { globalThis.nativePaste += 1; } });
  Object.defineProperty(copyOwner, "copyTextToClipboard", { ...copyDescriptor, value: async () => { globalThis.nativeCopy += 1; return true; } });
});

afterEach(async () => {
  // Restore stdout first: tests capture process.stdout.write, and the test runner's own reporter writes
  // through it; any await while it is still patched would swallow reports of other tests.
  process.stdout.write = stdoutWrite;
  if (ttyDescriptor) Object.defineProperty(process.stdout, "isTTY", ttyDescriptor); else delete process.stdout.isTTY;
  // Kill leftover fixtures, then join the owners' cleanup while the fake clock (if a test enabled it) is
  // still installed: quiescence waits on the KILL/grace timers that clock owns, so it is advanced here
  // until the join completes and only then replaced by the real one.
  for (const pid of [...startedPids(), ...names("grandchild-").map((name) => Number(name.slice(11)))]) {
    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
  }
  const joined = Promise.all(fixtures.map((extension) => extension.shutdown()));
  let finished = false;
  joined.then(() => { finished = true; }, () => { finished = true; });
  for (let turn = 0; turn < 200 && !finished; turn += 1) {
    try { mock.timers.tick(1000); } catch { /* real timers are active */ }
    await new Promise((resolve) => setImmediate(resolve));
  }
  mock.timers.reset();
  await joined;
  fixtures = [];
  Object.defineProperty(pasteOwner, "handleClipboardPaste", pasteDescriptor);
  Object.defineProperty(copyOwner, "copyTextToClipboard", copyDescriptor);
  if (sinkDescriptor) Object.defineProperty(globalThis, sinkKey, sinkDescriptor); else delete globalThis[sinkKey];
  for (const key of envKeys) {
    if (savedEnv[key] === undefined) delete process.env[key]; else process.env[key] = savedEnv[key];
  }
});

process.on("exit", () => rmSync(temp, { recursive: true, force: true }));

test("an old entry no longer late-writes: shutdown terminates its children and suppresses editor insertion and feedback", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const epochs = [];
  for (let epoch = 1; epoch <= 3; epoch += 1) {
    const extension = loadExtension();
    extension.start();
    const operations = [extension.automaticPaste(), extension.explicitPaste(), extension.push()];
    await until(() => startedPids().length === epoch * 3, "three ready helpers");
    extension.shutdown();
    epochs.push(extension);
    await Promise.all(operations); // cancellation resolves quietly, never rejects to the host
    await until(() => startedPids().every((pid) => !alive(pid)), "cooperative children to exit on TERM");
  }
  assert.equal(startedPids().filter(alive).length, 0);
  await until(() => childHandles() === 0, "child handle release");
  writeFileSync(join(temp, "release"), "go");
  await sleepTurn();
  for (const extension of epochs) {
    assert.deepEqual(extension.inserts, []);
    assert.deepEqual(extension.pastes, []);
    assert.deepEqual(extension.notices, []);
  }
  assert.equal(globalThis.nativePaste, 0);
});

test("an uncooperative helper is settled at the 8s deadline and KILLed after the TERM grace, with pipes released", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "mode"), "ignoreterm grandchild");
  const extension = loadExtension();
  extension.start();
  mock.timers.enable({ apis: ["setTimeout"] });
  let settled = false;
  const operation = extension.explicitPaste().then(() => { settled = true; });
  await until(() => startedPids().length === 1, "ready helper");
  const [pid] = startedPids();
  mock.timers.tick(7999);
  await sleepTurn();
  assert.equal(settled, false);
  mock.timers.tick(1); // operation deadline
  await operation;
  assert.equal(settled, true);
  assert.match(extension.notices.at(-1).text, /对端剪贴板读取失败/);
  assert.equal(extension.notices.at(-1).level, "error");
  await until(() => names("term-").length === 1, "TERM delivery");
  assert.equal(alive(pid), true, "TERM is ignored");
  assert.equal(childHandles(), 1);
  mock.timers.tick(1000); // TERM grace
  await until(() => !alive(pid), "KILL");
  await until(() => childHandles() === 0, "child handle release");
});

test("shutdown with a TERM-ignoring helper escalates to KILL within the grace and leaves no child handle", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "mode"), "ignoreterm");
  const extension = loadExtension();
  extension.start();
  const operations = [extension.explicitPaste(), extension.push()];
  await until(() => startedPids().length === 2, "ready helpers");
  mock.timers.enable({ apis: ["setTimeout"] });
  extension.shutdown();
  await Promise.all(operations);
  await until(() => names("term-").length === 2, "TERM delivery");
  assert.equal(startedPids().filter(alive).length, 2);
  mock.timers.tick(999);
  await sleepTurn();
  assert.equal(startedPids().filter(alive).length, 2);
  mock.timers.tick(1);
  await until(() => startedPids().every((pid) => !alive(pid)), "KILL of both helpers");
  await until(() => childHandles() === 0, "child handle release");
  extension.shutdown(); // repeat shutdown is a no-op
  assert.deepEqual(extension.notices, []);
});

test("cancelling during tmux remote detection yields no OSC52, native fallback or feedback; retired sink rejects with AbortError", async () => {
  const bin = join(temp, "bin");
  writeFileSync(join(temp, "tmux-wrapper"), "");
  rmSync(bin, { recursive: true, force: true });
  execFileSync("mkdir", ["-p", bin]);
  writeFileSync(join(bin, "tmux"), `#!/bin/sh\ntouch ${temp}/tmux-started-$$\nwhile [ ! -e ${temp}/tmux-release ]; do sleep 0.01; done\necho sess\n`);
  chmodSync(join(bin, "tmux"), 0o755);
  process.env.PATH = `${bin}:${savedEnv.PATH}`;
  process.env.TMUX = "fixture";
  process.env.TMUX_PANE = "%0";
  const writes = [];
  Object.defineProperty(process.stdout, "isTTY", { value: true, configurable: true });
  process.stdout.write = (chunk, ...rest) => { writes.push(String(chunk)); return true; };

  const extension = loadExtension();
  extension.start();
  const sink = globalThis[sinkKey];
  const copy = copyOwner.copyTextToClipboard("hello");
  const paste = extension.automaticPaste();
  const sinkCall = sink("hello").then(() => "resolved", (error) => error.name);
  await until(() => names("tmux-started-").length === 3, "three tmux detections");
  extension.shutdown();
  writeFileSync(join(temp, "tmux-release"), "go");
  assert.equal(await copy, false, "wrapper copy returns quietly");
  await paste;
  assert.equal(await sinkCall, "AbortError");
  assert.equal(await sink("again").then(() => "resolved", (error) => error.name), "AbortError");
  assert.deepEqual(writes.filter((text) => text.includes("]52;")), []);
  assert.equal(globalThis.nativeCopy, 0);
  assert.equal(globalThis.nativePaste, 0);
  assert.deepEqual(extension.notices, []);
  assert.equal(globalThis[sinkKey] === sink, false, "retired sink is unpublished");
});

test("public paste and rpush failures after shutdown notify nothing", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "mode"), "fail");
  const extension = loadExtension();
  extension.start();
  const operations = [extension.explicitPaste(), extension.push("/nonexistent.png")];
  await until(() => startedPids().length === 2, "ready helpers");
  extension.shutdown();
  writeFileSync(join(temp, "release"), "go");
  await Promise.all(operations);
  await until(() => childHandles() === 0, "child handle release");
  assert.deepEqual(extension.notices, []);
});

test("a stale old-epoch sink cannot be revived by a newer start, and the newer start owns its own sink", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const first = loadExtension();
  first.start();
  const oldSink = globalThis[sinkKey];
  first.shutdown();
  const second = loadExtension();
  second.start();
  const newSink = globalThis[sinkKey];
  assert.notEqual(oldSink, newSink);
  assert.equal(await oldSink("x").then(() => "resolved", (error) => error.name), "AbortError");
  Object.defineProperty(process.stdout, "isTTY", { value: true, configurable: true });
  const writes = [];
  process.stdout.write = (chunk) => { writes.push(String(chunk)); return true; };
  assert.equal(await newSink("hello"), true);
  assert.deepEqual(writes, [`\x1b]52;c;${Buffer.from("hello").toString("base64")}\x07`]);
  first.shutdown(); // stale shutdown does not unpublish the newer sink
  assert.equal(globalThis[sinkKey], newSink);
});

test("cooperative remote and local controls keep working under an active owner", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "release"), "go");
  const extension = loadExtension();
  extension.start();
  await extension.explicitPaste();
  assert.deepEqual(extension.pastes.map((p) => p.text.trim()), ["synthetic clipboard text"]);
  await extension.push();
  assert.deepEqual(extension.notices.map((n) => n.text), ["已推送到对端剪贴板"]);
  await extension.automaticPaste();
  assert.deepEqual(extension.inserts.map((p) => p.text), ["synthetic clipboard text"]);
  assert.equal(globalThis.nativePaste, 0);
  // exit code 2 means a local session: explicit route explains, automatic route falls back natively.
  writeHelper(ENDPOINT, `import json, sys\nsys.stdout.write(json.dumps({"protocol": "pi-remote-clipboard/artifact-1", "kind": "local"}) + "\\n")\n`);
  try {
    await extension.explicitPaste();
    assert.match(extension.notices.at(-1).text, /本地会话/);
    await extension.automaticPaste();
    assert.equal(globalThis.nativePaste, 1);
  } finally {
    writeHelper(ENDPOINT, helperSource);
  }
  delete process.env.SSH_CONNECTION;
  await extension.automaticPaste();
  assert.equal(globalThis.nativePaste, 2);
  assert.equal(await copyOwner.copyTextToClipboard("t"), true);
  assert.equal(globalThis.nativeCopy, 1);
  await until(() => childHandles() === 0, "child handle release");
});

// --- closure: negative-result race -------------------------------------------------------------
// copy-links runs `sink && (await sink(text))` and falls back to native copy on a falsy result.
const coupledCopy = (sink) => async (text) => {
  try {
    if (await sink(text)) return;
  } catch (error) {
    if (error.name === "AbortError") return;
    throw error;
  }
  globalThis.nativeCopy += 1;
};

test("shutdown between the awaited negative detection and its continuation cancels instead of returning false", async () => {
  // Local session: no TMUX, no SSH. isRemoteClient resolves false before the owner dies.
  const extension = loadExtension();
  extension.start();
  const sink = globalThis[sinkKey];
  const call = sink("x").then((value) => ({ value }), (error) => ({ name: error.name }));
  const coupled = coupledCopy(sink)("y");
  const wrapper = copyOwner.copyTextToClipboard("z");
  extension.shutdown();
  assert.deepEqual(await call, { name: "AbortError" });
  await coupled;
  assert.equal(await wrapper, false);
  assert.equal(globalThis.nativeCopy, 0, "no native fallback after cancel (coupled sink, wrapper)");
});

test("active owner controls: local sink resolves false and falls back, remote sink resolves true with feedback", async () => {
  const extension = loadExtension();
  extension.start();
  const sink = globalThis[sinkKey];
  assert.equal(await sink("x"), false);
  await coupledCopy(sink)("y");
  assert.equal(globalThis.nativeCopy, 1);
  process.env.SSH_CONNECTION = "fixture";
  Object.defineProperty(process.stdout, "isTTY", { value: true, configurable: true });
  const writes = [];
  process.stdout.write = (chunk) => { writes.push(String(chunk)); return true; };
  const flashes = [];
  assert.equal(await copyOwner.copyTextToClipboard.call({ flash: (m) => flashes.push(m) }, "hello"), true);
  assert.equal(writes.length, 1);
  assert.deepEqual(flashes, ["已复制到对端剪贴板"]);
});

test("shutdown landing on the OSC52 write itself (awaited true) skips stale feedback", async () => {
  process.env.SSH_CONNECTION = "fixture";
  Object.defineProperty(process.stdout, "isTTY", { value: true, configurable: true });
  const extension = loadExtension();
  extension.start();
  process.stdout.write = (chunk) => {
    if (String(chunk).includes("]52;")) extension.shutdown();
    return true;
  };
  const flashes = [];
  assert.equal(await copyOwner.copyTextToClipboard.call({ flash: (m) => flashes.push(m) }, "hello"), true);
  assert.deepEqual(flashes, []);
  assert.equal(globalThis.nativeCopy, 0);
});

// --- closure: process-group ownership ----------------------------------------------------------
const holdingDescendant = (name) => `import os, pathlib, subprocess
p = pathlib.Path(${JSON.stringify(temp)})
g = subprocess.Popen(['sh', '-c', 'trap "" TERM; while :; do sleep 1; done'])
(p / ('grandchild-' + str(g.pid))).write_text('x')
(p / ('started-' + str(os.getpid()))).write_text('ready')
${name}`;
const grandchildren = () => names("grandchild-").map((name) => Number(name.slice(11)));

test("a leader that exits early but leaves a TERM-ignoring pipe-holding descendant: the descendant is killed with its group at the deadline", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeHelper(ENDPOINT, holdingDescendant(""));
  try {
    const extension = loadExtension();
    extension.start();
    mock.timers.enable({ apis: ["setTimeout"] });
    const operation = extension.explicitPaste();
    await until(() => startedPids().length === 1 && grandchildren().length === 1, "leader and descendant");
    await until(() => !alive(startedPids()[0]), "leader exit");
    assert.equal(alive(grandchildren()[0]), true);
    mock.timers.tick(8000);
    await operation;
    assert.match(extension.notices.at(-1).text, /对端剪贴板读取失败/);
    mock.timers.tick(1000);
    await until(() => !alive(grandchildren()[0]), "group KILL of the descendant");
    await until(() => childHandles() === 0, "child handle release");
  } finally {
    writeHelper(ENDPOINT, helperSource);
  }
});

test("a TERM-ignoring leader with a pipe-holding grandchild: shutdown joins group TERM→KILL; both are gone before fixture cleanup", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "mode"), "ignoreterm grandchild");
  const extension = loadExtension();
  extension.start();
  const operation = extension.explicitPaste();
  await until(() => startedPids().length === 1 && grandchildren().length === 1, "leader and grandchild");
  mock.timers.enable({ apis: ["setTimeout"] });
  const shutdown = extension.shutdown();
  await until(() => names("term-").length === 1, "TERM delivery");
  assert.equal(alive(startedPids()[0]), true);
  mock.timers.tick(1000);
  await shutdown;
  await operation;
  assert.equal(alive(startedPids()[0]), false);
  assert.equal(alive(grandchildren()[0]), false);
  await until(() => childHandles() === 0, "libuv close callback of the child handle");
});

test("a second start retires the predecessor and the following shutdown joins both retirements", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "mode"), "ignoreterm");
  const extension = loadExtension();
  extension.start();
  const first = extension.explicitPaste();
  await until(() => startedPids().length === 1, "first helper");
  mock.timers.enable({ apis: ["setTimeout"] });
  extension.start(); // replaces and cancels the first owner; its helper ignores TERM
  await first;
  const shutdown = extension.shutdown();
  await until(() => names("term-").length === 1, "TERM delivery");
  mock.timers.tick(1000);
  await shutdown;
  assert.equal(startedPids().filter(alive).length, 0);
});

test("shutdown still joins cleanup when hook restoration throws", async () => {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "mode"), "ignoreterm");
  const extension = loadExtension();
  extension.start();
  const operation = extension.explicitPaste();
  await until(() => startedPids().length === 1, "helper");
  Object.defineProperty(copyOwner, "copyTextToClipboard", { configurable: true, get() { throw new Error("restore boom"); }, set() { throw new Error("restore boom"); } });
  mock.timers.enable({ apis: ["setTimeout"] });
  const shutdown = extension.shutdown().then(() => "ok", (error) => error.message);
  await until(() => names("term-").length === 1, "TERM delivery");
  mock.timers.tick(1000);
  assert.equal(await shutdown, "restore boom");
  await operation;
  assert.equal(startedPids().filter(alive).length, 0);
});

// --- closure: actual quit ---------------------------------------------------------------------
function runQuit(runnerMode) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const runner = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", new URL("./quit-runner.mjs", import.meta.url).pathname], {
      env: { ...process.env, RUNNER_TEMP: temp, RUNNER_MODE: runnerMode },
      stdio: ["ignore", "inherit", "inherit"],
    });
    const guard = setTimeout(() => { runner.kill("SIGKILL"); reject(new Error("runner did not exit")); }, 20000);
    runner.once("exit", (code) => { clearTimeout(guard); resolve({ code, elapsed: Date.now() - started }); });
  });
}

for (const runnerMode of ["inflight-exit", "timedout-exit"]) {
  test(`quit (${runnerMode}): after the awaited shutdown handler and an immediate process exit, no helper or descendant survives`, async () => {
    writeFileSync(join(temp, "mode"), "ignoreterm grandchild");
    const { code } = await runQuit(runnerMode);
    assert.equal(code, 0);
    const [pid] = startedPids();
    assert.ok(pid, "runner started a helper");
    assert.equal(alive(pid), false, "TERM-ignoring leader is gone");
    assert.equal(grandchildren().some(alive), false, "descendant is gone");
  });
}

for (const runnerMode of ["exited-quit", "exited-timedout-exit"]) {
  test(`quit (${runnerMode}): leader already exited, TERM-ignoring pipe-holder remains; descendant is gone after shutdown + immediate exit, before any manual kill`, async () => {
    writeHelper(ENDPOINT, holdingDescendant(""));
    try {
      const { code } = await runQuit(runnerMode);
      assert.equal(code, 0);
      assert.equal(startedPids().some(alive), false);
      const survivors = grandchildren().filter(alive);
      for (const pid of survivors) { try { process.kill(pid, "SIGKILL"); } catch { /* gone */ } }
      assert.deepEqual(survivors, [], "descendant survived the runner's quit");
    } finally {
      writeHelper(ENDPOINT, helperSource);
    }
  });
}

test("quit (inflight-natural): no timer or pipe keeps the process alive after shutdown", async () => {
  writeFileSync(join(temp, "mode"), "ignoreterm grandchild");
  const { code, elapsed } = await runQuit("inflight-natural");
  assert.equal(code, 0);
  assert.ok(elapsed < 6000, `runner exited naturally in ${elapsed} ms (a leaked 8 s deadline would exceed this)`);
  assert.equal(alive(startedPids()[0]), false);
  assert.equal(grandchildren().some(alive), false);
});

// --- closure: byte budget ---------------------------------------------------------------------
test("the 1 MiB budget counts bytes: multibyte output over 1 MiB is rejected while under-budget multibyte and exact-boundary output pass", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const emit = (script) => writeHelper(ENDPOINT, `import sys\n${script}\n`);
  try {
    const extension = loadExtension();
    extension.start();
    const receipt = `sys.stdout.write('{"protocol":"pi-remote-clipboard/artifact-1","kind":"text"}\\n'); `;
    emit(`${receipt}sys.stdout.write('€' * 340000)`); // 1,020,000 body bytes
    await extension.explicitPaste();
    assert.equal(extension.pastes.length, 1);
    emit(`${receipt}sys.stdout.flush(); sys.stdout.buffer.write(b'a' * 1048576)`); // exactly the budget
    await extension.explicitPaste();
    assert.equal(extension.pastes.length, 2);
    emit(`${receipt}sys.stdout.write('€' * 360000)`); // 1,080,000 bytes, 360,000 characters
    await extension.explicitPaste();
    assert.equal(extension.pastes.length, 2);
    assert.match(extension.notices.at(-1).text, /exceeded/);
    assert.equal(extension.notices.at(-1).level, "error");
    emit("sys.stderr.write('e' * (2 * 1024 * 1024))\nsys.stderr.flush()\nimport time\ntime.sleep(30)"); // huge stderr
    await extension.explicitPaste();
    assert.match(extension.notices.at(-1).text, /exceeded/);
    await until(() => childHandles() === 0, "child handle release after oversized stderr");
  } finally {
    writeHelper(ENDPOINT, helperSource);
  }
});

// --- step 4: image artifact ownership ---------------------------------------------------------
const tmpEntries = () => readdirSync(join(temp, "tmpdir"));
const PNG = "b'\\x89PNG\\r\\n\\x1a\\n' + b'synthetic'";
const RECEIPT = (extra) => `json.dumps({'protocol': 'pi-remote-clipboard/artifact-1', ${extra}}) + '\\n'`;
// A staging-aware helper. `body` runs after the stage path is known and may write files / print.
const stagedHelper = (body, { wait = false } = {}) => `import json, os, pathlib, sys, time
p = pathlib.Path(${JSON.stringify(temp)})
stage = sys.argv[1]
def make(name, data=${PNG}):
    fd = os.open(os.path.join(stage, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, data); os.close(fd)
(p / ('started-' + str(os.getpid()))).write_text('ready')
${wait ? "while not (p / 'release').exists():\n    time.sleep(.01)" : ""}
${body}
`;
const withHelper = async (source, run) => {
  writeHelper(ENDPOINT, source);
  try { await run(); } finally { writeHelper(ENDPOINT, helperSource); }
};
const imageReceipt = `make('image.png')\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`;

test("a delivered image is a private file outside staging, survives shutdown, and is not removed when rendering fails after insertion", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(imageReceipt), async () => {
    const extension = loadExtension();
    extension.start();
    const old = process.umask(0);
    try { await extension.automaticPaste(); } finally { process.umask(old); }
    assert.equal(extension.inserts.length, 1);
    const path = extension.inserts[0].text;
    assert.deepEqual(tmpEntries(), [path.split("/").at(-1)], "staging directory is gone, only the delivered file remains");
    assert.equal(path.startsWith(join(temp, "tmpdir")), true);
    assert.equal(statSync(path).mode & 0o777, 0o600);
    await extension.shutdown();
    assert.equal(existsSync(path), true, "editor/history own the file after shutdown");
    // Insertion succeeded, then the render step fails: the file stays.
    const second = loadExtension();
    second.start();
    const host = { editor: { insertTextAtCursor(text) { host.inserted = text; } }, ui: { requestRender() { throw new Error("render boom"); } } };
    await pasteOwner.handleClipboardPaste.call(host);
    assert.equal(existsSync(host.inserted), true, "render failure after insertion must not delete the inserted image");
    // Explicit shortcut: image path is pasted and survives shutdown too.
    await second.explicitPaste();
    const explicit = second.pastes.at(-1).text;
    await second.shutdown();
    assert.equal(existsSync(explicit), true);
    assert.equal(tmpEntries().length, 3);
  });
});

test("an insertion that throws removes the undelivered image; an insertion that triggers shutdown keeps the inserted one", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(imageReceipt), async () => {
    const extension = loadExtension();
    extension.start();
    await pasteOwner.handleClipboardPaste.call({ editor: { insertTextAtCursor() { throw new Error("insert boom"); } }, ui: {}, showError() {} });
    await until(() => tmpEntries().length === 0, "undelivered image cleanup");
    let inserted;
    await pasteOwner.handleClipboardPaste.call({
      editor: { insertTextAtCursor(text) { inserted = text; extension.shutdown(); } }, ui: {},
    });
    assert.equal(existsSync(inserted), true);
    assert.equal(tmpEntries().length, 1);
  });
});

test("cancelling while the helper holds a written image removes the staging directory; no delivered file is left", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(imageReceipt, { wait: true }), async () => {
    const extension = loadExtension();
    extension.start();
    const operations = [extension.automaticPaste(), extension.explicitPaste()];
    await until(() => startedPids().length === 2, "two helpers writing");
    assert.equal(tmpEntries().length, 2, "private staging directories exist while the helpers run");
    await extension.shutdown();
    await Promise.all(operations);
    assert.deepEqual(tmpEntries(), []);
    assert.deepEqual(extension.inserts, []);
    assert.deepEqual(extension.pastes, []);
  });
});

test("cancelling before the staging directory exists or right after the helper finished leaves nothing", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(imageReceipt), async () => {
    for (const waitForHelper of [false, true]) {
      const extension = loadExtension();
      extension.start();
      const operation = extension.explicitPaste();
      if (waitForHelper) await until(() => startedPids().length > 0, "helper start");
      await extension.shutdown(); // mkdtemp / helper may be in flight
      await operation;
      assert.deepEqual(tmpEntries(), []);
      assert.deepEqual(extension.pastes, []);
      rmSync(join(temp, "release"), { force: true });
      for (const name of names("started-")) rmSync(join(temp, name), { force: true });
    }
  });
});

test("16 failed artifact operations leave zero undelivered files", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(`make('image.png')\nsys.exit(1)`), async () => {
    const extension = loadExtension();
    extension.start();
    for (let i = 0; i < 16; i += 1) await extension.explicitPaste();
    assert.equal(extension.notices.filter((n) => n.level === "error").length, 16);
    await extension.shutdown();
    assert.deepEqual(tmpEntries(), []);
  });
});

test("malformed or foreign receipts surface a protocol error and can never delete a user file", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const victim = join(temp, "victim");
  const cases = {
    legacyPlainText: `print('/etc/hostname')`,
    legacyPlainPath: `print(${JSON.stringify(victim)})`,
    traversalName: `sys.stdout.write(${RECEIPT(`'kind': 'image', 'name': '../../victim'`)})`,
    absoluteName: `sys.stdout.write(${RECEIPT(`'kind': 'image', 'name': ${JSON.stringify(victim)}`)})`,
    unknownKind: `sys.stdout.write(${RECEIPT("'kind': 'file'")})`,
    imageWithBody: `make('image.png')\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")} + 'extra')`,
    missingFile: `sys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`,
    symlink: `os.symlink(${JSON.stringify(victim)}, os.path.join(stage, 'image.png'))\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`,
    hardlink: `os.link(${JSON.stringify(victim)}, os.path.join(stage, 'image.png'))\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`,
    directory: `os.mkdir(os.path.join(stage, 'image.png'))\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`,
    groupReadable: `make('image.png')\nos.chmod(os.path.join(stage, 'image.png'), 0o644)\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`,
  };
  for (const [label, body] of Object.entries(cases)) {
    writeFileSync(victim, "precious user data");
    await withHelper(stagedHelper(body), async () => {
      const extension = loadExtension();
      extension.start();
      await extension.explicitPaste();
      assert.deepEqual(extension.pastes, [], label);
      assert.match(extension.notices.at(-1).text, /对端剪贴板读取失败.*(artifact-1|回执)/s, label);
      await pasteOwner.handleClipboardPaste.call({ editor: { insertTextAtCursor() { assert.fail(label); } }, ui: {}, showError(message) { extension.notices.push({ text: message, level: "error" }); } });
      assert.match(extension.notices.at(-1).text, /artifact-1|回执/, label);
      await extension.shutdown();
      assert.equal(readFileSync(victim, "utf8"), "precious user data", label);
      assert.deepEqual(tmpEntries(), [], label);
    });
    for (const name of names("started-")) rmSync(join(temp, name), { force: true });
  }
});

test("clipboard text shaped like a path or an image receipt is pasted verbatim and nothing is deleted", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const victim = join(temp, "victim");
  writeFileSync(victim, "precious user data");
  const shaped = [victim, JSON.stringify({ protocol: "pi-remote-clipboard/artifact-1", kind: "image", name: "image.png" })];
  for (const text of shaped) {
    await withHelper(stagedHelper(`sys.stdout.write(${RECEIPT("'kind': 'text'")} + ${JSON.stringify(text)})`), async () => {
      const extension = loadExtension();
      extension.start();
      await extension.explicitPaste();
      assert.deepEqual(extension.pastes.map((p) => p.text), [text]);
      await extension.shutdown();
      assert.equal(readFileSync(victim, "utf8"), "precious user data");
    });
    for (const name of names("started-")) rmSync(join(temp, name), { force: true });
  }
});

test("empty, local (exit 2) and error outcomes are unchanged under the artifact protocol", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const extension = loadExtension();
  extension.start();
  await withHelper(stagedHelper(`sys.stdout.write(${RECEIPT("'kind': 'text'")})`), async () => {
    await extension.explicitPaste();
    assert.equal(extension.notices.at(-1).text, "对端剪贴板为空");
  });
  await withHelper(`import json, sys\nsys.stdout.write(json.dumps({"protocol": "pi-remote-clipboard/artifact-1", "kind": "local"}) + "\\n")\n`, async () => {
    await extension.explicitPaste();
    assert.match(extension.notices.at(-1).text, /本地会话/);
    await extension.automaticPaste();
    assert.equal(globalThis.nativePaste, 1);
  });
  await withHelper(`import sys\nsys.stderr.write('boom')\nsys.exit(1)\n`, async () => {
    await extension.explicitPaste();
    assert.equal(extension.notices.at(-1).level, "error");
  });
  await extension.shutdown();
  assert.deepEqual(tmpEntries(), []);
});

// --- step 4: deployment — default helper comes from the checkout behind the managed link --------
function runDefaultHelper(env) {
  return new Promise((resolve, reject) => {
    const runner = spawn(process.execPath, ["--experimental-strip-types", "--no-warnings", new URL("./jiti-managed-runner.mjs", import.meta.url).pathname], {
      env: Object.fromEntries(Object.entries({ ...process.env, ...env }).filter(([, value]) => value !== undefined)),
      stdio: ["ignore", "pipe", "inherit"],
    });
    let out = "";
    runner.stdout.on("data", (chunk) => { out += chunk; });
    const guard = setTimeout(() => { runner.kill("SIGKILL"); reject(new Error("runner did not exit")); }, 20000);
    runner.once("exit", (code) => { clearTimeout(guard); resolve({ code, notices: out.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line)) }); });
  });
}

test("through Pi's own jiti loader and a managed symlink, the default helper is the checkout's even when a stale installed copy exists; an explicit obsolete override is rejected before any producer runs", async () => {
  const home = join(temp, "fakehome");
  const stale = join(home, ".local/lib/pi-remote-clipboard");
  mkdirSync(stale, { recursive: true });
  // A real pre-artifact install: only ctrlv.py exists (it ignores argv and would write a UUID image into tmp).
  writeFileSync(join(stale, "ctrlv.py"), `import pathlib\npathlib.Path(${JSON.stringify(join(temp, "stale-ran"))}).write_text('x')\nprint('/tmp/stale-guess.png')\n`);
  const managed = join(temp, "managed-extension");
  rmSync(managed, { force: true });
  symlinkSync(new URL("../pi-extension", import.meta.url).pathname, managed);
  // A tmux that knows no clients makes the real endpoint classify the session as local without
  // consulting process ancestry, and the bridge port is pointed at nothing: no provider is ever contacted.
  const fakeBin = join(temp, "fake-tmux-bin");
  mkdirSync(fakeBin, { recursive: true });
  writeFileSync(join(fakeBin, "tmux"), "#!/bin/sh\nexit 1\n");
  chmodSync(join(fakeBin, "tmux"), 0o755);
  // PI_REMOTE_CLIPBOARD_DIR is removed from the child env (the suite itself sets it); JITI_FS_CACHE=0 keeps
  // jiti's own transform cache out of the tmp directory whose contents the assertions count.
  const env = {
    RUNNER_EXTENSION: join(managed, "index.ts"), HOME: home, JITI_FS_CACHE: "0", PI_REMOTE_CLIPBOARD_DIR: undefined,
    TMUX: "fixture", TMUX_PANE: "%0", PATH: `${fakeBin}:${savedEnv.PATH}`, PI_REMOTE_CLIPBOARD_REMOTE_PORT: "1",
  };
  const base = env;
  // Checkout helper: the real versioned endpoint reports a local session by receipt; the stale copy never executes.
  const fromCheckout = await runDefaultHelper(base);
  assert.equal(fromCheckout.code, 0);
  assert.match(fromCheckout.notices.at(-1).text, /本地会话/);
  assert.equal(existsSync(join(temp, "stale-ran")), false, "stale installed helper must not run by default");
  // An explicit override to the obsolete copy is honoured as a location, but the legacy producer never runs.
  const overridden = await runDefaultHelper({ ...env, PI_REMOTE_CLIPBOARD_DIR: stale });
  assert.equal(overridden.code, 0);
  assert.equal(existsSync(join(temp, "stale-ran")), false, "legacy producer must not run at all");
  assert.match(overridden.notices.at(-1).text, /artifact-1/);
  assert.equal(overridden.notices.at(-1).level, "error");
  assert.deepEqual(tmpEntries(), []);
});

// --- step 4 follow-up: failure ownership ------------------------------------------------------
const armFaults = (faults) => { globalThis.__rcFsFaults = { rm: 0, rmCalls: [], unlinkCalls: [], ...faults }; return globalThis.__rcFsFaults; };

test("a first staging removal that fails after the image link: the error is visible, the finalizer still removes the staging directory and the unadopted target", async () => {
  process.env.SSH_CONNECTION = "fixture";
  const victim = join(temp, "victim");
  writeFileSync(victim, "precious user data");
  await withHelper(stagedHelper(imageReceipt), async () => {
    const faults = armFaults({ rm: 1 });
    const extension = loadExtension("tui", faultyClipboard);
    extension.start();
    await extension.explicitPaste();
    assert.match(extension.notices.at(-1).text, /对端剪贴板读取失败.*synthetic rm failure/s);
    assert.deepEqual(extension.pastes, []);
    // Automatic route: same failure is shown through Pi's error channel, with no fallback.
    await until(() => tmpEntries().length === 0, "first operation's finalizer");
    faults.rm = 1;
    const shown = [];
    await pasteOwner.handleClipboardPaste.call({ editor: { insertTextAtCursor() { assert.fail("must not insert"); } }, ui: {}, showError: (m) => shown.push(m) });
    assert.equal(shown.length, 1);
    assert.match(shown[0], /synthetic rm failure/);
    assert.equal(globalThis.nativePaste, 0);
    await extension.shutdown(); // joins both finalizers
    assert.deepEqual(tmpEntries(), [], "staging directories and unadopted targets are gone (real file counts)");
    assert.equal(faults.rmCalls.length, 4, "per operation: the body attempt failed once, then the finalizer retried the retained directory");
    assert.equal(faults.unlinkCalls.length, 2);
    assert.equal(readFileSync(victim, "utf8"), "precious user data");
  });
});

test("a persistent staging removal failure does not skip the independent unlink of the unadopted target; the directory stays private and is reported", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(imageReceipt), async () => {
    const faults = armFaults({ rm: 99 });
    const logged = [];
    const originalError = console.error;
    console.error = (...args) => logged.push(args.join(" "));
    try {
      const extension = loadExtension("tui", faultyClipboard);
      extension.start();
      await extension.explicitPaste();
      await extension.shutdown();
      assert.equal(faults.unlinkCalls.length, 1, "target was unlinked despite the earlier rm failures");
    } finally {
      console.error = originalError;
    }
    assert.equal(logged.filter((line) => line.includes("暂存目录")).length, 1, "residual directory is reported explicitly");
    const left = tmpEntries();
    assert.equal(left.length, 1);
    assert.equal(left[0].startsWith("pi-remote-clipboard-stage-"), true);
    assert.equal(statSync(join(temp, "tmpdir", left[0])).mode & 0o777, 0o700);
    delete globalThis.__rcFsFaults;
  });
});

test("a protocol or I/O rejection that crosses shutdown produces no late error, local fallback or feedback", async () => {
  process.env.SSH_CONNECTION = "fixture";
  // I/O failure: shutdown lands exactly when the pre-insert removal fails.
  await withHelper(stagedHelper(imageReceipt), async () => {
    const extension = loadExtension("tui", faultyClipboard);
    extension.start();
    armFaults({ rm: 1, onRmFail: () => { extension.shutdown(); } });
    const shown = [];
    await pasteOwner.handleClipboardPaste.call({ editor: { insertTextAtCursor() { assert.fail("must not insert"); } }, ui: {}, showError: (m) => shown.push(m) });
    await extension.shutdown();
    assert.deepEqual(shown, []);
    assert.equal(globalThis.nativePaste, 0);
    assert.deepEqual(tmpEntries(), []);
  });
  // Protocol failure (a staged symlink): shutdown lands right after validation's lstat.
  const victim = join(temp, "victim");
  writeFileSync(victim, "precious user data");
  await withHelper(stagedHelper(`os.symlink(${JSON.stringify(victim)}, os.path.join(stage, 'image.png'))\nsys.stdout.write(${RECEIPT("'kind': 'image', 'name': 'image.png'")})`), async () => {
    const extension = loadExtension("tui", faultyClipboard);
    extension.start();
    armFaults({ afterLstat: () => { extension.shutdown(); } });
    const shown = [];
    await pasteOwner.handleClipboardPaste.call({ editor: { insertTextAtCursor() { assert.fail("must not insert"); } }, ui: {}, showError: (m) => shown.push(m) });
    await extension.shutdown();
    assert.deepEqual(shown, []);
    assert.deepEqual(extension.notices, []);
    assert.equal(globalThis.nativePaste, 0);
    assert.equal(readFileSync(victim, "utf8"), "precious user data");
    assert.deepEqual(tmpEntries(), []);
  });
});

test("an adopted image survives a failing finalizer removal and a render failure", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(stagedHelper(imageReceipt), async () => {
    const extension = loadExtension("tui", faultyClipboard);
    extension.start();
    const faults = armFaults({ rm: 0 });
    const host = { editor: { insertTextAtCursor(text) { host.inserted = text; } }, ui: { requestRender() { throw new Error("render boom"); } }, showError() {} };
    await pasteOwner.handleClipboardPaste.call(host);
    await extension.shutdown();
    assert.equal(existsSync(host.inserted), true);
    assert.equal(faults.unlinkCalls.length, 0, "adopted file is never unlinked");
    assert.equal(tmpEntries().length, 1);
  });
});

// --- protocol admission: legacy / partial helper directories are refused before any producer I/O -----
const REAL_SRC = new URL("../src/", import.meta.url).pathname;
async function loadWithHelperDir(dir) {
  const previous = process.env.PI_REMOTE_CLIPBOARD_DIR;
  process.env.PI_REMOTE_CLIPBOARD_DIR = dir;
  try {
    return (await import(`../pi-extension/index.ts?helper-dir-${dir}`)).default;
  } finally {
    process.env.PI_REMOTE_CLIPBOARD_DIR = previous;
  }
}
// Producer side effects a legacy ctrlv.py would have: a marker plus a UUID image in tmp, outside any staging.
const legacyProducer = `import os, pathlib, sys, uuid
def main():
    pathlib.Path(${JSON.stringify(temp)}, 'legacy-ran').write_text(' '.join(sys.argv))
    open(os.path.join(${JSON.stringify(join(temp, "tmpdir"))}, 'pi-remote-clipboard-' + str(uuid.uuid4()) + '.png'), 'wb').write(b'x')
    sys.stdout.write('/etc/passwd')
if __name__ == '__main__':
    main()
`;
function helperDir(label, files) {
  const dir = join(temp, `helpers-${label}`);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir);
  for (const [name, source] of Object.entries(files)) writeFileSync(join(dir, name), source);
  return dir;
}
const real = (name) => readFileSync(join(REAL_SRC, name), "utf8");
const ownedRoutes = async (extension) => {
  const shown = [];
  await extension.explicitPaste();
  await pasteOwner.handleClipboardPaste.call({ editor: { insertTextAtCursor() { assert.fail("must not insert"); } }, ui: {}, showError: (m) => shown.push(m) });
  return shown;
};

const refusedDirs = {
  "legacy dir (only ctrlv.py, ignores argv)": () => helperDir("legacy", { "ctrlv.py": legacyProducer }),
  "new entry beside an old ctrlv.py (no artifact_paste)": () => helperDir("old-ctrlv", {
    "ctrlv.py": legacyProducer, [ENDPOINT]: real(ENDPOINT), "requester.py": real("requester.py"), "ssh_detect.py": real("ssh_detect.py"),
  }),
  "new entry and ctrlv beside an old requester (no staging parameter)": () => helperDir("old-requester", {
    "ctrlv.py": real("ctrlv.py"), [ENDPOINT]: real(ENDPOINT), "ssh_detect.py": real("ssh_detect.py"),
    "requester.py": `import pathlib\nARTIFACT_PROTOCOL = 'pi-remote-clipboard/artifact-1'\nDEFAULT_PORT = 1\n`
      + `def request_clipboard():\n    pathlib.Path(${JSON.stringify(join(temp, "legacy-ran"))}).write_text('provider read')\n    return {}\n`
      + `def handle_response(payload):\n    return None, None\n`,
  }),
};
for (const [label, make] of Object.entries(refusedDirs)) {
  test(`${label}: refused with a protocol error before any producer I/O, no native fallback, zero files outside staging`, async () => {
    process.env.SSH_CONNECTION = "fixture";
    const factory = await loadWithHelperDir(make());
    const extension = loadExtension("tui", factory);
    extension.start();
    const shown = await ownedRoutes(extension);
    assert.equal(existsSync(join(temp, "legacy-ran")), false, "no legacy producer may run");
    assert.equal(shown.length, 1);
    assert.match(shown[0], /artifact-1/);
    assert.match(extension.notices.at(-1).text, /对端剪贴板读取失败.*artifact-1/s);
    assert.equal(extension.notices.at(-1).level, "error");
    assert.equal(globalThis.nativePaste, 0, "protocol failure is not a local session");
    assert.deepEqual(extension.pastes, []);
    await extension.shutdown();
    assert.deepEqual(tmpEntries(), [], "no image outside the operation's staging, and no staging left");
  });
}

test("exit status 2 from the versioned endpoint is a protocol error, never a local session or native fallback", async () => {
  process.env.SSH_CONNECTION = "fixture";
  await withHelper(`import sys\nsys.stderr.write('usage: argparse failure')\nsys.exit(2)\n`, async () => {
    const extension = loadExtension();
    extension.start();
    const shown = await ownedRoutes(extension);
    assert.equal(globalThis.nativePaste, 0);
    assert.equal(shown.length, 1);
    assert.match(shown[0], /argparse failure/);
    assert.match(extension.notices.at(-1).text, /argparse failure/);
    await extension.shutdown();
    assert.deepEqual(tmpEntries(), []);
  });
});
