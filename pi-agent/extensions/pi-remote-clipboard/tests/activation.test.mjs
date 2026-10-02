import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

// Route the extension's host imports to the installed Pi without linking into
// either Pi home or this checkout. The test never starts Pi or a provider.
let coreRoot = dirname(realpathSync(execFileSync("sh", ["-c", "command -v pi"], { encoding: "utf8" }).trim()));
while (coreRoot !== dirname(coreRoot)) {
  const pkg = join(coreRoot, "package.json");
  if (existsSync(pkg) && JSON.parse(readFileSync(pkg, "utf8")).name === "@earendil-works/pi-coding-agent") break;
  coreRoot = dirname(coreRoot);
}
assert.ok(existsSync(join(coreRoot, "dist/index.js")), "installed Pi package not found");
const coreUrl = pathToFileURL(join(coreRoot, "dist/index.js")).href;
const tuiUrl = pathToFileURL(join(coreRoot, "node_modules/@earendil-works/pi-tui/dist/index.js")).href;
let agentModuleUrl = coreUrl;
let tuiModuleUrl = tuiUrl;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@earendil-works/pi-coding-agent") return { url: agentModuleUrl, shortCircuit: true };
    if (specifier === "@earendil-works/pi-tui") return { url: tuiModuleUrl, shortCircuit: true };
    return nextResolve(specifier, context);
  },
});
const { InteractiveMode } = await import(coreUrl);
const { TuiAltScreen } = await import(tuiUrl);
const { default: piRemoteClipboard } = await import("../pi-extension/index.ts");

const pasteOwner = InteractiveMode.prototype;
const copyOwner = TuiAltScreen.prototype;
const pasteKey = "handleClipboardPaste";
const copyKey = "copyTextToClipboard";
const sinkKey = Symbol.for("pi-remote-clipboard:copy-sink");
const pasteDescriptor = Object.getOwnPropertyDescriptor(pasteOwner, pasteKey);
const copyDescriptor = Object.getOwnPropertyDescriptor(copyOwner, copyKey);
const sinkDescriptor = Object.getOwnPropertyDescriptor(globalThis, sinkKey);
assert.equal(typeof pasteDescriptor?.value, "function", "installed Pi must expose the test's paste fixture");
assert.equal(typeof copyDescriptor?.value, "function", "installed Pi must expose the test's copy fixture");

afterEach(() => {
  Object.defineProperty(pasteOwner, pasteKey, pasteDescriptor);
  Object.defineProperty(copyOwner, copyKey, copyDescriptor);
  if (sinkDescriptor) Object.defineProperty(globalThis, sinkKey, sinkDescriptor);
  else delete globalThis[sinkKey];
});

function loadExtension(mode = "tui", factory = piRemoteClipboard) {
  const shortcuts = new Map();
  const commands = new Map();
  const events = new Map();
  const notices = [];
  factory({
    registerShortcut(key, options) { shortcuts.set(key, options); },
    registerCommand(key, options) { commands.set(key, options); },
    on(event, handler) { events.set(event, handler); },
  });
  const ctx = {
    mode,
    ui: { notify(text, level) { notices.push({ text, level }); } },
  };
  return {
    shortcuts, commands, notices,
    start() { events.get("session_start")?.({ type: "session_start" }, ctx); },
    shutdown() { events.get("session_shutdown")?.({ type: "session_shutdown" }, ctx); },
  };
}

function assertPublicRoutes(extension) {
  assert.equal(typeof extension.shortcuts.get("ctrl+alt+v")?.handler, "function");
  assert.equal(typeof extension.commands.get("rpush")?.handler, "function");
}

test("installed Pi registers public routes and wraps available private seams without version admission", () => {
  const extension = loadExtension();
  assertPublicRoutes(extension);
  assert.equal(pasteOwner[pasteKey], pasteDescriptor.value, "private hooks install only at TUI session start");
  try {
    extension.start();
    assert.notEqual(pasteOwner[pasteKey], pasteDescriptor.value);
    assert.notEqual(copyOwner[copyKey], copyDescriptor.value);
    assert.equal(typeof globalThis[sinkKey], "function");
    assert.deepEqual(extension.notices, []);
  } finally {
    extension.shutdown();
  }
  assert.equal(pasteOwner[pasteKey], pasteDescriptor.value);
  assert.equal(copyOwner[copyKey], copyDescriptor.value);
  assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, sinkKey), sinkDescriptor);
});

test("local Ctrl+V delegates to the original Pi method without reading any clipboard", async () => {
  const calls = [];
  const native = async function () { calls.push(this); };
  Object.defineProperty(pasteOwner, pasteKey, { ...pasteDescriptor, value: native });
  const keys = ["TMUX", "TMUX_PANE", "SSH_CONNECTION", "SSH_CLIENT", "MOSH_CONNECTION"];
  const previous = keys.map((key) => process.env[key]);
  for (const key of keys) delete process.env[key];
  const extension = loadExtension();
  const host = { editor: {}, ui: {} };
  try {
    extension.start();
    await pasteOwner[pasteKey].call(host);
    assert.deepEqual(calls, [host]);
    assert.deepEqual(extension.notices, []);
  } finally {
    extension.shutdown();
    for (const [index, key] of keys.entries()) {
      if (previous[index] === undefined) delete process.env[key];
      else process.env[key] = previous[index];
    }
  }
  assert.equal(pasteOwner[pasteKey], native);
});

test("missing private paste method leaves Ctrl+V unmodified, with copy routing and explicit shortcut", () => {
  Object.defineProperty(pasteOwner, pasteKey, { ...pasteDescriptor, value: undefined });
  const extension = loadExtension();
  assertPublicRoutes(extension);
  try {
    extension.start();
    assert.equal(pasteOwner[pasteKey], undefined);
    assert.notEqual(copyOwner[copyKey], copyDescriptor.value);
    assert.equal(typeof globalThis[sinkKey], "function");
    assert.equal(extension.notices.length, 1);
    assert.match(extension.notices[0].text, /InteractiveMode\.handleClipboardPaste.*Ctrl\+V.*Ctrl\+Alt\+V/);
    assert.equal(extension.notices[0].level, "warning");
  } finally {
    extension.shutdown();
  }
  assert.equal(pasteOwner[pasteKey], undefined, "shutdown preserves the absent host seam");
});

test("missing private copy method does not block automatic paste or public routes", () => {
  Object.defineProperty(copyOwner, copyKey, { ...copyDescriptor, value: undefined });
  const extension = loadExtension();
  assertPublicRoutes(extension);
  try {
    extension.start();
    assert.equal(copyOwner[copyKey], undefined);
    assert.notEqual(pasteOwner[pasteKey], pasteDescriptor.value);
    assert.equal(typeof globalThis[sinkKey], "function");
    assert.equal(extension.notices.length, 1);
    assert.match(extension.notices[0].text, /TuiAltScreen\.copyTextToClipboard.*Ctrl\+Alt\+V/);
  } finally {
    extension.shutdown();
  }
  assert.equal(copyOwner[copyKey], undefined);
  assert.equal(pasteOwner[pasteKey], pasteDescriptor.value);
});

test("read-only private paste method is left alone and diagnosed without losing explicit routes", () => {
  Object.defineProperty(pasteOwner, pasteKey, { ...pasteDescriptor, writable: false });
  const extension = loadExtension();
  assertPublicRoutes(extension);
  try {
    extension.start();
    assert.equal(pasteOwner[pasteKey], pasteDescriptor.value);
    assert.notEqual(copyOwner[copyKey], copyDescriptor.value);
    assert.match(extension.notices[0].text, /InteractiveMode\.handleClipboardPaste.*Ctrl\+Alt\+V/);
  } finally {
    extension.shutdown();
  }
});

test("a Pi module without InteractiveMode or VERSION still loads the public routes", async () => {
  agentModuleUrl = "data:text/javascript,export const unrelated = true";
  let factory;
  try {
    ({ default: factory } = await import("../pi-extension/index.ts?missing-interactive-mode"));
  } finally {
    agentModuleUrl = coreUrl;
  }
  const extension = loadExtension("tui", factory);
  assertPublicRoutes(extension);
  try {
    extension.start();
    assert.equal(typeof globalThis[sinkKey], "function");
    assert.notEqual(copyOwner[copyKey], copyDescriptor.value);
    assert.equal(extension.notices.length, 1);
    assert.match(extension.notices[0].text, /InteractiveMode\.handleClipboardPaste.*Ctrl\+Alt\+V/);
  } finally {
    extension.shutdown();
  }
});

test("a Pi TUI module without TuiAltScreen still keeps automatic and explicit paste", async () => {
  tuiModuleUrl = "data:text/javascript,export const unrelated = true";
  let factory;
  try {
    ({ default: factory } = await import("../pi-extension/index.ts?missing-tui-alt-screen"));
  } finally {
    tuiModuleUrl = tuiUrl;
  }
  const extension = loadExtension("tui", factory);
  assertPublicRoutes(extension);
  try {
    extension.start();
    assert.notEqual(pasteOwner[pasteKey], pasteDescriptor.value);
    assert.equal(extension.notices.length, 1);
    assert.match(extension.notices[0].text, /TuiAltScreen\.copyTextToClipboard.*Ctrl\+Alt\+V/);
  } finally {
    extension.shutdown();
  }
});

test("non-TUI modes retain public routes without touching private prototypes", () => {
  const extension = loadExtension("print");
  assertPublicRoutes(extension);
  extension.start();
  assert.equal(pasteOwner[pasteKey], pasteDescriptor.value);
  assert.equal(copyOwner[copyKey], copyDescriptor.value);
  assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, sinkKey), sinkDescriptor);
  assert.deepEqual(extension.notices, []);
  extension.shutdown();
});
