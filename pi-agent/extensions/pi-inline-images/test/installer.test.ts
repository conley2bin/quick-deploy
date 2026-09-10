import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, test } from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const INSTALL = resolve(HERE, "../install.sh");
const temporary: string[] = [];
afterEach(() => { while (temporary.length) rmSync(temporary.pop()!, { recursive: true, force: true }); });

function environment() {
  const root = mkdtempSync(join(tmpdir(), "pi inline installer "));
  temporary.push(root);
  const home = join(root, "home with spaces");
  const piHome = join(home, ".pi", "agent");
  const bin = join(root, "bin");
  mkdirSync(piHome, { recursive: true });
  mkdirSync(bin);
  execFileSync("git", ["init", "-q", piHome]);
  writeFileSync(join(piHome, "settings.json"), "{\"owned\":\"user\"}\n");
  writeFileSync(join(bin, "npm"), "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NPM_LOG\"\n", { mode: 0o755 });
  chmodSync(INSTALL, 0o755);
  const npmLog = join(root, "npm.log");
  return { root, home, piHome, npmLog, env: { ...process.env, HOME: home, PI_CODING_AGENT_DIR: piHome, NPM_LOG: npmLog, PATH: `${bin}:${process.env.PATH}` } };
}

test("installer is idempotent with spaces and preserves tracked settings", () => {
  const setup = environment();
  const before = readFileSync(join(setup.piHome, "settings.json"), "utf8");
  execFileSync(INSTALL, { env: setup.env });
  execFileSync(INSTALL, { env: setup.env });
  const target = join(setup.piHome, "extensions/pi-inline-images");
  assert.equal(execFileSync("readlink", ["-f", target], { encoding: "utf8" }).trim(), resolve(HERE, ".."));
  const excludePath = execFileSync("git", ["-C", setup.piHome, "rev-parse", "--git-path", "info/exclude"], { encoding: "utf8" }).trim();
  const exclude = readFileSync(resolve(setup.piHome, excludePath), "utf8");
  assert.equal(exclude.split("\n").filter((line) => line === "/extensions/pi-inline-images").length, 1);
  assert.equal(readFileSync(join(setup.piHome, "settings.json"), "utf8"), before);
  assert.equal(readFileSync(setup.npmLog, "utf8").trim().split("\n").length, 2);
  assert.equal(execFileSync("git", ["-C", setup.piHome, "status", "--short"], { encoding: "utf8" }).trim(), "?? settings.json");
});

test("installer refuses Git-index ownership even when tracked target is deleted from worktree", () => {
  const setup = environment();
  const target = join(setup.piHome, "extensions/pi-inline-images");
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, "tracked owner\n");
  execFileSync("git", ["-C", setup.piHome, "add", "settings.json", "extensions/pi-inline-images"]);
  execFileSync("git", ["-C", setup.piHome, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]);
  rmSync(target);
  const excludePath = resolve(setup.piHome, execFileSync("git", ["-C", setup.piHome, "rev-parse", "--git-path", "info/exclude"], { encoding: "utf8" }).trim());
  const beforeExclude = readFileSync(excludePath, "utf8");
  const beforeStatus = execFileSync("git", ["-C", setup.piHome, "status", "--short"], { encoding: "utf8" });

  const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /refusing Git-index-owned target/);
  assert.equal(readFileSync(excludePath, "utf8"), beforeExclude, "exclude is untouched");
  assert.equal(execFileSync("git", ["-C", setup.piHome, "status", "--short"], { encoding: "utf8" }), beforeStatus, "tracked deletion does not become a symlink type-change");
  assert.equal(spawnSync("test", ["-e", setup.npmLog]).status, 1, "npm is not invoked");
  assert.equal(spawnSync("test", ["-e", target]).status, 1, "deleted tracked target remains absent");
});

test("installer also refuses indexed descendants of an absent target directory", () => {
  const setup = environment();
  const descendant = join(setup.piHome, "extensions/pi-inline-images/owned.txt");
  mkdirSync(dirname(descendant), { recursive: true });
  writeFileSync(descendant, "tracked descendant\n");
  execFileSync("git", ["-C", setup.piHome, "add", "settings.json", "extensions/pi-inline-images/owned.txt"]);
  execFileSync("git", ["-C", setup.piHome, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"]);
  rmSync(join(setup.piHome, "extensions/pi-inline-images"), { recursive: true });
  const excludePath = resolve(setup.piHome, execFileSync("git", ["-C", setup.piHome, "rev-parse", "--git-path", "info/exclude"], { encoding: "utf8" }).trim());
  const before = readFileSync(excludePath, "utf8");
  const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /refusing Git-index-owned target/);
  assert.equal(readFileSync(excludePath, "utf8"), before);
  assert.equal(spawnSync("test", ["-e", setup.npmLog]).status, 1);
  assert.equal(spawnSync("test", ["-e", join(setup.piHome, "extensions/pi-inline-images")]).status, 1);
});

test("installer refuses unknown targets before changing exclude or dependencies", () => {
  const setup = environment();
  const target = join(setup.piHome, "extensions/pi-inline-images");
  mkdirSync(target, { recursive: true });
  writeFileSync(join(target, "mine"), "do not replace");
  const excludePath = resolve(setup.piHome, execFileSync("git", ["-C", setup.piHome, "rev-parse", "--git-path", "info/exclude"], { encoding: "utf8" }).trim());
  const before = readFileSync(excludePath, "utf8");
  const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /refusing to replace non-symlink/);
  assert.equal(readFileSync(excludePath, "utf8"), before);
  assert.equal(spawnSync("test", ["-e", setup.npmLog]).status, 1, "npm was not invoked after conflict");
  assert.equal(readFileSync(join(target, "mine"), "utf8"), "do not replace");
});
