// Isolated process for lifecycle.test.mjs: loads the extension through Pi's own loadExtensions (jiti) from a
// managed symlink path, then runs one explicit Ctrl+Alt+V. The helper directory is whatever the extension
// itself chooses (default/override). Prints each notification as a JSON line. Synthetic: no provider,
// clipboard, SSH or Pi session.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

let coreRoot = dirname(realpathSync(execFileSync("sh", ["-c", "command -v pi"], { encoding: "utf8" }).trim()));
while (coreRoot !== dirname(coreRoot)) {
  const pkg = join(coreRoot, "package.json");
  if (existsSync(pkg) && JSON.parse(readFileSync(pkg, "utf8")).name === "@earendil-works/pi-coding-agent") break;
  coreRoot = dirname(coreRoot);
}
const { loadExtensions } = await import(pathToFileURL(join(coreRoot, "dist/core/extensions/loader.js")).href);
const result = await loadExtensions([process.env.RUNNER_EXTENSION], process.cwd());
if (result.errors.length > 0) {
  console.log(JSON.stringify({ loadErrors: result.errors.map((entry) => entry.error) }));
  process.exit(3);
}
const [extension] = result.extensions;
const ctx = {
  mode: "print",
  ui: { notify(text, level) { console.log(JSON.stringify({ text, level })); }, pasteToEditor() {} },
};
for (const handler of extension.handlers.get("session_start") ?? []) await handler({ type: "session_start" }, ctx);
await extension.shortcuts.get("ctrl+alt+v").handler(ctx);
for (const handler of extension.handlers.get("session_shutdown") ?? []) await handler({ type: "session_shutdown" }, ctx);
