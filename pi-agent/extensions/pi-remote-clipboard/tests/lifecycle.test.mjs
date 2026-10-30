import { execFileSync, spawn } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
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
    return nextResolve(specifier, context);
  },
});

const temp = mkdtempSync(join(tmpdir(), "pi-remote-clipboard-lifecycle-"));
process.env.PI_REMOTE_CLIPBOARD_DIR = temp;
const { InteractiveMode } = await import(coreUrl);
const { TuiAltScreen } = await import(tuiUrl);
const { default: piRemoteClipboard } = await import("../pi-extension/index.ts");

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
print('synthetic clipboard text')
`;
writeHelper("ctrlv.py", helperSource);
writeHelper("push.py", helperSource);

let fixtures = [];
function loadExtension(mode = "tui") {
  const shortcuts = new Map();
  const commands = new Map();
  const events = new Map();
  const state = { ended: false };
  const notices = [];
  const pastes = [];
  const inserts = [];
  piRemoteClipboard({
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
  for (const key of envKeys) if (key !== "PATH") delete process.env[key];
  for (const name of readdirSync(temp)) if (/^(started|term|grandchild|release|tmux)/.test(name)) rmSync(join(temp, name), { force: true });
  writeFileSync(join(temp, "mode"), "");
  // The native seams are inert recorders so a stale fallback is observable.
  globalThis.nativePaste = 0;
  globalThis.nativeCopy = 0;
  Object.defineProperty(pasteOwner, "handleClipboardPaste", { ...pasteDescriptor, value: async () => { globalThis.nativePaste += 1; } });
  Object.defineProperty(copyOwner, "copyTextToClipboard", { ...copyDescriptor, value: async () => { globalThis.nativeCopy += 1; return true; } });
});

afterEach(async () => {
  mock.timers.reset();
  for (const pid of [...startedPids(), ...names("grandchild-").map((name) => Number(name.slice(11)))]) {
    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
  }
  await Promise.all(fixtures.map((extension) => extension.shutdown()));
  fixtures = [];
  process.stdout.write = stdoutWrite;
  if (ttyDescriptor) Object.defineProperty(process.stdout, "isTTY", ttyDescriptor); else delete process.stdout.isTTY;
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
  writeHelper("ctrlv.py", "import sys\nsys.exit(2)\n");
  try {
    await extension.explicitPaste();
    assert.match(extension.notices.at(-1).text, /本地会话/);
    await extension.automaticPaste();
    assert.equal(globalThis.nativePaste, 1);
  } finally {
    writeHelper("ctrlv.py", helperSource);
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
  writeHelper("ctrlv.py", holdingDescendant(""));
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
    writeHelper("ctrlv.py", helperSource);
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
    writeHelper("ctrlv.py", holdingDescendant(""));
    try {
      const { code } = await runQuit(runnerMode);
      assert.equal(code, 0);
      assert.equal(startedPids().some(alive), false);
      const survivors = grandchildren().filter(alive);
      for (const pid of survivors) { try { process.kill(pid, "SIGKILL"); } catch { /* gone */ } }
      assert.deepEqual(survivors, [], "descendant survived the runner's quit");
    } finally {
      writeHelper("ctrlv.py", helperSource);
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
  const emit = (script) => writeHelper("ctrlv.py", `import sys\n${script}\n`);
  try {
    const extension = loadExtension();
    extension.start();
    emit("sys.stdout.write('€' * 340000)"); // 1,020,000 bytes
    await extension.explicitPaste();
    assert.equal(extension.pastes.length, 1);
    emit("sys.stdout.buffer.write(b'a' * 1048576)"); // exactly the budget
    await extension.explicitPaste();
    assert.equal(extension.pastes.length, 2);
    emit("sys.stdout.write('€' * 360000)"); // 1,080,000 bytes, 360,000 characters
    await extension.explicitPaste();
    assert.equal(extension.pastes.length, 2);
    assert.match(extension.notices.at(-1).text, /exceeded/);
    assert.equal(extension.notices.at(-1).level, "error");
    emit("sys.stderr.write('e' * (2 * 1024 * 1024))\nsys.stderr.flush()\nimport time\ntime.sleep(30)"); // huge stderr
    await extension.explicitPaste();
    assert.match(extension.notices.at(-1).text, /exceeded/);
    await until(() => childHandles() === 0, "child handle release after oversized stderr");
  } finally {
    writeHelper("ctrlv.py", helperSource);
  }
});
