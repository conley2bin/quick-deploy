import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

// Remote text goes through the public ctx.ui.pasteToEditor, which wraps it in native bracketed-paste
// delimiters. These tests drive the extension's real explicit-paste handler into Pi's actual
// createExtensionUIContext().pasteToEditor and the installed pi-tui Editor; the clipboard producer is a
// synthetic helper, and the editor's onSubmit is an inert recorder.
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

const temp = mkdtempSync(join(tmpdir(), "pi-remote-clipboard-framing-"));
process.env.PI_REMOTE_CLIPBOARD_DIR = temp;
mkdirSync(join(temp, "tmpdir"));
process.env.TMPDIR = join(temp, "tmpdir");
const { InteractiveMode } = await import(coreUrl);
const { Editor } = await import(tuiUrl);
const { default: piRemoteClipboard } = await import("../pi-extension/index.ts");
process.on("exit", () => rmSync(temp, { recursive: true, force: true }));

// The versioned endpoint prints a text receipt followed by the bytes of the `payload` file, verbatim.
writeFileSync(join(temp, "ctrlv-artifact-v1.py"), `import pathlib, sys
sys.stdout.write('{"protocol":"pi-remote-clipboard/artifact-1","kind":"text"}\\n')
sys.stdout.flush()
sys.stdout.buffer.write((pathlib.Path(${JSON.stringify(temp)}) / 'payload').read_bytes())
`);

const theme = { borderColor: (s) => s, selectList: {} };
const tui = { requestRender() {} };
const newEditor = () => new Editor(tui, theme);
const END = "\x1b[201~";
const START = "\x1b[200~";
const nativePaste = (text) => { // what the host does for a well-formed bracketed paste
  const editor = newEditor();
  editor.handleInput(`${START}${text}${END}`);
  return editor.getText();
};

const pasteOwner = InteractiveMode.prototype;
const pasteDescriptor = Object.getOwnPropertyDescriptor(pasteOwner, "handleClipboardPaste");
let shutdowns = [];
afterEach(async () => {
  await Promise.all(shutdowns.map((shutdown) => shutdown()));
  shutdowns = [];
  Object.defineProperty(pasteOwner, "handleClipboardPaste", pasteDescriptor);
  delete process.env.SSH_CONNECTION;
});

// Runs the extension's public explicit paste through the real UI context and real editor.
async function remotePaste(payload) {
  process.env.SSH_CONNECTION = "fixture";
  writeFileSync(join(temp, "payload"), payload);
  Object.defineProperty(pasteOwner, "handleClipboardPaste", { ...pasteDescriptor, value: async () => {} });
  const editor = newEditor();
  const submits = [];
  const inputs = [];
  editor.onSubmit = (text) => submits.push(text);
  const handleInput = editor.handleInput.bind(editor);
  editor.handleInput = (data) => { inputs.push(data); return handleInput(data); };
  const ui = InteractiveMode.prototype.createExtensionUIContext.call({ editor });
  const notices = [];
  ui.notify = (text, level) => notices.push({ text, level });
  const shortcuts = new Map();
  const events = new Map();
  piRemoteClipboard({
    registerShortcut(key, options) { shortcuts.set(key, options); },
    registerCommand() {},
    on(event, handler) { events.set(event, handler); },
  });
  const ctx = { mode: "tui", ui };
  events.get("session_start")({ type: "session_start" }, ctx);
  shutdowns.push(() => events.get("session_shutdown")({ type: "session_shutdown" }, ctx));
  await shortcuts.get("ctrl+alt+v").handler(ctx);
  return { text: editor.getText(), submits, inputs, notices };
}

const controls = (text) => [...text].filter((ch) => ch !== "\n" && ch.charCodeAt(0) < 32);

const framed = [
  ["embedded end delimiter followed by printable tail and C0", `head${END}tail\x00\x07more`, "headtailmore"],
  ["repeated delimiters", `a${END}b${START}c${END}${END}d\x01`, "abcd"],
  ["delimiters reassembled from nested markers", `x\x1b[20${END}1~y\x1b[2${START}01~z`, "xyz"],
];
for (const [name, payload, expected] of framed) {
  test(`framing markers in remote text cannot leave the paste frame: ${name}`, async () => {
    const result = await remotePaste(payload);
    assert.equal(result.text, expected);
    assert.equal(result.text, nativePaste(expected));
    assert.deepEqual(controls(result.text), []);
    assert.equal(result.inputs.length, 1, "one framed paste, no re-entered raw input");
    assert.deepEqual(result.submits, []);
    assert.deepEqual(result.notices, []);
  });
}

const preserved = [
  ["partial non-markers", "\x1b[200 and \x1b[20~ and \x1b[2 and \x1b[201"],
  ["literal backslash escapes", "\\x1b[201~ \\u001b[200~ \\033[201~"],
  ["multiline, tabs and Unicode", "α\tβ\r\n日本語\n😀 e\u0301"],
  ["C0 without markers keeps native filtering", "a\x00b\x07c\nd"],
];
for (const [name, payload] of preserved) {
  test(`text without framing markers pastes exactly as the native editor does: ${name}`, async () => {
    const result = await remotePaste(payload);
    assert.equal(result.text, nativePaste(payload));
    assert.equal(result.inputs.length, 1);
    assert.deepEqual(controls(result.text), []);
    assert.deepEqual(result.submits, []);
  });
}

test("literal backslash escape strings reach the editor as data", async () => {
  const result = await remotePaste("\\x1b[201~ tail");
  assert.equal(result.text, "\\x1b[201~ tail");
});

test("text that is only framing markers is an empty clipboard, not a paste", async () => {
  const result = await remotePaste(`${START}${END}`);
  assert.equal(result.text, "");
  assert.equal(result.inputs.length, 0);
  assert.equal(result.notices.length, 1);
});

// --- stripPasteFraming complexity and equivalence -------------------------------------------------------
const { stripPasteFraming } = await import("../pi-extension/index.ts");

// The previous implementation: repeat whole-string replaceAll until nothing changes.
function fixedPointOracle(text) {
  let previous;
  do {
    previous = text;
    text = text.replaceAll(START, "").replaceAll(END, "");
  } while (text !== previous);
  return text;
}
// ESC[20 x N, ESC[201~, then "1~" x N: every pass removes only the innermost (re)formed delimiter.
const nested = (n) => "\x1b[20".repeat(n) + END + "1~".repeat(n);

// Total length handed to whole-string scanning String methods while fn runs; restored before returning.
function scannedLength(fn) {
  const names = ["replaceAll", "replace", "split", "indexOf", "includes", "slice", "substring", "concat", "match", "startsWith", "endsWith"];
  const saved = names.map((name) => [name, String.prototype[name]]);
  let scanned = 0;
  try {
    for (const [name, original] of saved) {
      String.prototype[name] = function (...args) { scanned += this.length; return original.apply(this, args); };
    }
    fn();
  } finally {
    for (const [name, original] of saved) String.prototype[name] = original;
  }
  return scanned;
}

test("stripPasteFraming scans linearly: nested-marker work doubles with the input, it does not square", () => {
  const work = [100, 200, 400].map((n) => scannedLength(() => assert.equal(stripPasteFraming(nested(n)), "")));
  assert.ok(work[1] <= work[0] * 2.5 && work[2] <= work[1] * 2.5, `scanned lengths ${work} grow superlinearly`);
  assert.ok(work[2] <= 20 * nested(400).length, "bounded passes over the input");
});

test("a 1 MiB deeply nested payload is fully reduced", () => {
  const n = Math.floor((1048576 - 6) / 6);
  const input = nested(n);
  assert.ok(input.length <= 1048576);
  assert.equal(stripPasteFraming(input), "");
  const withText = "α" + "\x1b[20".repeat(n) + END + "1~".repeat(n) + "😀";
  assert.equal(stripPasteFraming(withText), "α😀");
});

test("stripPasteFraming equals the fixed-point oracle on seeded mixed and nested inputs", () => {
  const pieces = ["\x1b", "[", "2", "0", "1", "~", "a", "é", "😀", "\ud83d", "\\x1b[201~", "\n", "\x00", START, END, "\x1b[20", "1~", "\x1b[2", "0~", "00~"];
  let seed = 12345;
  const rand = (limit) => { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return seed % limit; };
  for (let c = 0; c < 4000; c += 1) {
    let text = "";
    for (let k = rand(40); k > 0; k -= 1) text += pieces[rand(pieces.length)];
    const out = stripPasteFraming(text);
    assert.equal(out, fixedPointOracle(text), JSON.stringify(text));
    assert.ok(!out.includes(START) && !out.includes(END));
    assert.equal(stripPasteFraming(out), out, "idempotent");
    for (const cut of [0, text.length >> 1, text.length]) { // marker halves split across the cut still reduce the same
      assert.equal(stripPasteFraming(text.slice(0, cut) + text.slice(cut)), out);
    }
  }
  for (const n of [1, 2, 3, 7, 30]) assert.equal(stripPasteFraming(nested(n)), fixedPointOracle(nested(n)));
});
