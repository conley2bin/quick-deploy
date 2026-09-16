import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";

function isPiRoot(candidate: string): boolean {
  try {
    const metadata = JSON.parse(readFileSync(resolve(candidate, "package.json"), "utf8")) as { name?: unknown };
    return metadata.name === "@earendil-works/pi-coding-agent" && existsSync(resolve(candidate, "dist/index.js"));
  } catch {
    return false;
  }
}

function rootForCli(cli: string): string | undefined {
  let current = dirname(cli);
  for (let depth = 0; depth < 5; depth++, current = dirname(current)) {
    if (isPiRoot(current)) return current;
  }
  return undefined;
}

/** Locate the installed Pi package without assuming `pi` is a symlink. */
export function installedPiRoot(): string {
  const override = process.env.PI_TEST_CORE;
  if (override) {
    const root = resolve(override);
    if (!isPiRoot(root)) throw new Error(`PI_TEST_CORE is not a Pi package root: ${root}`);
    return root;
  }

  const command = execFileSync("sh", ["-lc", "command -v pi"], { encoding: "utf8" }).trim();
  if (!command) throw new Error("pi is not on PATH");
  const resolved = realpathSync(command);
  const direct = rootForCli(resolved);
  if (direct) return direct;

  const wrapper = readFileSync(resolved, "utf8");
  const match = /^agent=["']([^"']+\/dist\/(?:bundle\/)?cli\.js)["']$/mu.exec(wrapper);
  if (!match) throw new Error(`cannot locate Pi package from wrapper: ${resolved}`);
  const cli = resolve(match[1]!.replace(/^\$HOME\//u, `${process.env.HOME}/`));
  const root = rootForCli(cli);
  if (!root) throw new Error(`Pi wrapper target is not a valid package: ${cli}`);
  return root;
}
