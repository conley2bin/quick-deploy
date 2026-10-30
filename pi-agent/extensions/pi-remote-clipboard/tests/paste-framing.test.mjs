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
