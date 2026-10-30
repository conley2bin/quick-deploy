// Isolated process for lifecycle.test.mjs: loads the extension through a managed symlink path and
// runs one explicit paste with the helper directory chosen by the extension's own default/override.
// Prints each notification as a JSON line. Synthetic: no provider, clipboard or SSH.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

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

const { default: piRemoteClipboard } = await import(pathToFileURL(process.env.RUNNER_EXTENSION).href);
const shortcuts = new Map();
const events = new Map();
piRemoteClipboard({
  registerShortcut(key, options) { shortcuts.set(key, options); },
  registerCommand() {},
  on(event, handler) { events.set(event, handler); },
});
const ctx = {
  mode: "print",
  ui: { notify(text, level) { console.log(JSON.stringify({ text, level })); }, pasteToEditor() {} },
};
events.get("session_start")({ type: "session_start" }, ctx);
await shortcuts.get("ctrl+alt+v").handler(ctx);
await events.get("session_shutdown")({ type: "session_shutdown" }, ctx);
