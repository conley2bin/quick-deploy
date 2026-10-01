import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, existsSync, lstatSync, mkdtempSync, mkdirSync, readFileSync, readlinkSync, realpathSync, rmSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, test } from "node:test";

const HERE = dirname(fileURLToPath(import.meta.url));
const INSTALL = resolve(HERE, "../install.sh");
const temporary: { root: string; canonical: string; inode: number }[] = [];
afterEach(() => {
  while (temporary.length) {
    const { root, canonical, inode } = temporary.pop()!;
    assert.equal(lstatSync(root).isSymbolicLink(), false);
    assert.equal(realpathSync(root), canonical);
    assert.equal(statSync(root).ino, inode);
    rmSync(root, { recursive: true, force: true });
  }
});

function environment(git = true) {
  const root = mkdtempSync(join(tmpdir(), "pi inline installer "));
  temporary.push({ root, canonical: realpathSync(root), inode: statSync(root).ino });
  const home = join(root, "home with spaces");
  const piHome = join(home, ".pi", "agent");
  const bin = join(root, "bin");
  mkdirSync(piHome, { recursive: true });
  mkdirSync(bin);
  if (git) execFileSync("git", ["init", "-q", piHome]);
  writeFileSync(join(piHome, "settings.json"), "{\"owned\":\"user\"}\n");
  writeFileSync(join(bin, "npm"), "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NPM_LOG\"\n", { mode: 0o755 });
  const npmLog = join(root, "npm.log");
  return { root, home, piHome, npmLog, env: { ...process.env, HOME: home, PI_CODING_AGENT_DIR: piHome, NPM_LOG: npmLog, PATH: `${bin}:${process.env.PATH}`, LC_ALL: "C" } };
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

test("inaccessible Git metadata cannot bypass a deleted indexed target", t => {
  if (typeof process.getuid === "function" && process.getuid() === 0) { t.skip("requires unprivileged permission checks"); return; }
  const setup = environment(), target = join(setup.piHome, "extensions/pi-inline-images"), metadata = join(setup.piHome, ".git");
  mkdirSync(dirname(target)); writeFileSync(target, "indexed owner\n");
  execFileSync("git", ["-C", setup.piHome, "add", "--", "extensions/pi-inline-images"], { env: setup.env });
  const index = readFileSync(join(metadata, "index")), settings = readFileSync(join(setup.piHome, "settings.json"));
  const mode = statSync(metadata).mode & 0o777; rmSync(target); chmodSync(metadata, 0o000);
  try {
    assert.notEqual(spawnSync("git", ["-C", setup.piHome, "rev-parse", "--git-dir"], { env: setup.env }).status, 0);
    const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });
    assert.notEqual(result.status, 0, result.stdout); assert.match(result.stderr, /cannot inspect Git ownership/);
    assert.equal(existsSync(setup.npmLog), false); assert.throws(() => lstatSync(target), { code: "ENOENT" });
    assert.deepEqual(readFileSync(join(setup.piHome, "settings.json")), settings);
  } finally { chmodSync(metadata, mode); }
  assert.deepEqual(readFileSync(join(metadata, "index")), index);
  assert.equal(execFileSync("git", ["-C", setup.piHome, "status", "--short"], { env: setup.env, encoding: "utf8" }).includes("AD extensions/pi-inline-images"), true);
});

test("failed Git inspection rejects markers and explicit ownership context before npm", () => {
  for (const kind of ["gitfile", "dangling marker", "explicit context", "empty explicit context"]) {
    const setup = environment(false), marker = join(setup.piHome, ".git");
    let env: NodeJS.ProcessEnv = setup.env;
    if (kind === "gitfile") writeFileSync(marker, `gitdir: ${join(setup.root, "missing metadata")}\n`);
    if (kind === "dangling marker") symlinkSync(join(setup.root, "missing metadata"), marker);
    if (kind === "explicit context" || kind === "empty explicit context") env = { ...env, GIT_DIR: kind === "empty explicit context" ? "" : join(setup.root, "missing metadata") };
    const settings = readFileSync(join(setup.piHome, "settings.json"));
    const result = spawnSync(INSTALL, { env, encoding: "utf8" });
    assert.notEqual(result.status, 0); assert.match(result.stderr, /cannot inspect Git ownership/);
    assert.equal(existsSync(setup.npmLog), false); assert.equal(existsSync(join(setup.piHome, "extensions/pi-inline-images")), false);
    assert.deepEqual(readFileSync(join(setup.piHome, "settings.json")), settings);
    if (kind === "gitfile") assert.equal(readFileSync(marker, "utf8"), `gitdir: ${join(setup.root, "missing metadata")}\n`);
    if (kind === "dangling marker") assert.equal(readlinkSync(marker), join(setup.root, "missing metadata"));
  }
});

test("failed Git inspection follows physical ancestors and refuses inaccessible directory lookup", t => {
  if (typeof process.getuid === "function" && process.getuid() === 0) { t.skip("requires unprivileged permission checks"); return; }
  const setup = environment(false), metadata = join(setup.home, ".git");
  execFileSync("git", ["init", "-q", setup.home], { env: setup.env });
  const alias = join(setup.root, "Pi parent alias"); symlinkSync(join(setup.home, ".pi"), alias);
  const mode = statSync(metadata).mode & 0o777; chmodSync(metadata, 0o000);
  try {
    const result = spawnSync(INSTALL, { env: { ...setup.env, PI_CODING_AGENT_DIR: join(alias, "agent") }, encoding: "utf8" });
    assert.notEqual(result.status, 0); assert.match(result.stderr, /cannot inspect Git ownership/);
    assert.equal(existsSync(setup.npmLog), false); assert.equal(existsSync(join(setup.piHome, "extensions/pi-inline-images")), false);
  } finally { chmodSync(metadata, mode); }
  const inaccessible = environment(false), homeMode = statSync(inaccessible.piHome).mode & 0o777;
  chmodSync(inaccessible.piHome, 0o000);
  try {
    const result = spawnSync(INSTALL, { env: inaccessible.env, encoding: "utf8" });
    assert.notEqual(result.status, 0); assert.match(result.stderr, /cannot inspect Git ownership/);
    assert.equal(existsSync(inaccessible.npmLog), false);
  } finally { chmodSync(inaccessible.piHome, homeMode); }
});

test("installer supports a missing non-Git Pi home and reruns without creating settings or Git metadata", () => {
  const setup = environment(false); rmSync(setup.piHome, { recursive: true });
  assert.equal(existsSync(setup.piHome), false);
  execFileSync(INSTALL, { env: setup.env }); execFileSync(INSTALL, { env: setup.env });
  const target = join(setup.piHome, "extensions/pi-inline-images");
  assert.equal(realpathSync(target), resolve(HERE, ".."));
  assert.equal(existsSync(join(setup.piHome, "settings.json")), false);
  assert.equal(existsSync(join(setup.piHome, ".git")), false);
  assert.equal(readFileSync(setup.npmLog, "utf8").trim().split("\n").length, 2);
});

test("installer preserves existing non-Git settings and the already-owned link", () => {
  const setup = environment(false), settings = join(setup.piHome, "settings.json");
  const before = readFileSync(settings), beforeStat = statSync(settings, { bigint: true });
  execFileSync(INSTALL, { env: setup.env });
  const target = join(setup.piHome, "extensions/pi-inline-images"), link = lstatSync(target);
  execFileSync(INSTALL, { env: setup.env });
  assert.equal(lstatSync(target).ino, link.ino);
  assert.equal(realpathSync(target), resolve(HERE, ".."));
  assert.deepEqual(readFileSync(settings), before);
  assert.equal(statSync(settings, { bigint: true }).mtimeNs, beforeStat.mtimeNs);
  assert.equal(existsSync(join(setup.piHome, ".git")), false);
});

test("foreign files, directories and symlinks are refused before npm in both home types", () => {
  for (const git of [false, true]) for (const kind of ["file", "directory", "symlink"]) {
    const setup = environment(git), target = join(setup.piHome, "extensions/pi-inline-images");
    mkdirSync(dirname(target), { recursive: true });
    if (kind === "file") writeFileSync(target, "foreign file\n");
    if (kind === "directory") { mkdirSync(target); writeFileSync(join(target, "mine"), "foreign directory\n"); }
    if (kind === "symlink") symlinkSync(join(setup.root, "foreign source"), target);
    const before = lstatSync(target), settings = readFileSync(join(setup.piHome, "settings.json"));
    const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });
    assert.notEqual(result.status, 0); assert.match(result.stderr, /refusing to replace/);
    assert.equal(lstatSync(target).ino, before.ino); assert.equal(existsSync(setup.npmLog), false);
    assert.deepEqual(readFileSync(join(setup.piHome, "settings.json")), settings);
    if (kind === "file") assert.equal(readFileSync(target, "utf8"), "foreign file\n");
    if (kind === "directory") assert.equal(readFileSync(join(target, "mine"), "utf8"), "foreign directory\n");
    if (kind === "symlink") assert.equal(readlinkSync(target), join(setup.root, "foreign source"));
  }
});

test("an unwritable Git exclude still refuses installation before dependencies", t => {
  if (typeof process.getuid === "function" && process.getuid() === 0) { t.skip("requires unprivileged permission checks"); return; }
  const setup = environment(), exclude = join(setup.piHome, ".git/info/exclude");
  chmodSync(exclude, 0o400); const before = readFileSync(exclude);
  const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });
  assert.notEqual(result.status, 0); assert.match(result.stderr, /Git exclude is not writable/);
  assert.deepEqual(readFileSync(exclude), before); assert.equal(existsSync(setup.npmLog), false);
  assert.equal(existsSync(join(setup.piHome, "extensions/pi-inline-images")), false);
});

test("an unwritable non-Git home fails explicitly without changing settings", t => {
  if (typeof process.getuid === "function" && process.getuid() === 0) { t.skip("requires unprivileged permission checks"); return; }
  const setup = environment(false), settings = join(setup.piHome, "settings.json");
  const before = readFileSync(settings); chmodSync(setup.piHome, 0o500);
  try {
    const result = spawnSync(INSTALL, { env: setup.env, encoding: "utf8" });
    assert.notEqual(result.status, 0); assert.match(result.stderr, /Permission denied/);
    assert.deepEqual(readFileSync(settings), before);
    assert.equal(existsSync(join(setup.piHome, "extensions/pi-inline-images")), false);
    assert.equal(existsSync(join(setup.piHome, ".git")), false);
  } finally { chmodSync(setup.piHome, 0o700); }
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
