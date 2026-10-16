import assert from "node:assert/strict";
import { execFileSync, spawn, spawnSync } from "node:child_process";
import { chmodSync, chownSync, copyFileSync, cpSync, existsSync, linkSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, readlinkSync, realpathSync, rmSync, statSync, symlinkSync, unlinkSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const source = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const vscodeCompanionName = "vscode-video-preview-repair";
const vscodeCompanion = join(source, "extensions", "pi-copy-links", vscodeCompanionName);
// Read the installed host's library/parser, never execute its CLI or mutate its
// package/home. Each fixture's executable is a tripwire, not the real Pi.
const installedCli = realpathSync(execFileSync("bash", ["-c", "type -P pi"], { encoding: "utf8" }).trim());
const properLockfilePath = createRequire(installedCli).resolve("proper-lockfile");
let packageDir = dirname(installedCli);
while (!existsSync(join(packageDir, "package.json"))) {
  const parent = dirname(packageDir);
  assert.notEqual(parent, packageDir, "installed Pi package not found");
  packageDir = parent;
}
const settingsModule = pathToFileURL(join(packageDir, "dist/core/settings-manager.js")).href;
const { SettingsManager } = await import(settingsModule);
const { discoverAndLoadExtensions } = await import(pathToFileURL(join(packageDir, "dist/core/extensions/loader.js")).href);
const { DefaultPackageManager } = await import(pathToFileURL(join(packageDir, "dist/core/package-manager.js")).href);
const lockfile = createRequire(installedCli)("proper-lockfile");

function fixture(t, location = "default") {
  const root = mkdtempSync(join(tmpdir(), "pi-main-install-"));
  const ownedRoot = realpathSync(root), rootInode = statSync(root).ino;
  const beforeCleanup = [];
  t.after(() => {
    for (const restore of beforeCleanup) restore();
    try { checkNoEffects(); }
    finally {
      assert.equal(lstatSync(root).isSymbolicLink(), false);
      assert.equal(realpathSync(root), ownedRoot);
      assert.equal(statSync(root).ino, rootInode);
      rmSync(root, { recursive: true, force: true });
    }
  });
  const home = join(root, "home with spaces"), cwd = join(root, "project with spaces");
  const checkout = join(root, "source with spaces/pi-agent"), bin = join(root, "bin");
  const trace = join(root, "leaf-trace"), effects = join(root, "forbidden-effects");
  for (const dir of [home, cwd, checkout, bin, join(root, "observations")]) mkdirSync(dir, { recursive: true });
  for (const file of ["install.sh", "configure-fullscreen.mjs"]) copyFileSync(join(source, file), join(checkout, file));
  chmodSync(join(checkout, "install.sh"), 0o755);
  mkdirSync(join(cwd, ".pi"));
  const projectSettings = join(cwd, ".pi/settings.json");
  writeFileSync(projectSettings, '{"tuiMode":"regular","projectOnly":true}\n');
  const agent = location === "default" ? join(home, ".pi/agent") :
    location === "relative" ? join(cwd, "relative agent") : join(home, "custom agent");
  const settings = join(agent, "settings.json");
  const env = { HOME: home, PATH: `${bin}:${process.env.PATH}`, LANG: "C.UTF-8", TRACE: trace,
    EFFECTS: effects, OBSERVATIONS: join(root, "observations") };
  if (location !== "default") env.PI_CODING_AGENT_DIR = location === "tilde" ? "~/custom agent" :
    location === "relative" ? "relative agent" : agent;

  const fakePi = join(root, "Pi package/cli.js");
  mkdirSync(join(dirname(fakePi), "node_modules"), { recursive: true });
  symlinkSync(dirname(properLockfilePath), join(dirname(fakePi), "node_modules/proper-lockfile"));
  writeFileSync(fakePi, '#!/bin/sh\nprintf "pi\\n" >> "$EFFECTS"\nexit 91\n');
  chmodSync(fakePi, 0o755);
  symlinkSync(fakePi, join(bin, "pi"));
  for (const name of ["npm", "ssh", "tmux", "wl-copy", "xclip", "xdg-open", "google-chrome-stable", "code", "systemctl"]) {
    writeFileSync(join(bin, name), `#!/bin/sh\nprintf '${name}\\n' >> "$EFFECTS"\nexit 91\n`);
    chmodSync(join(bin, name), 0o755);
  }
  function leaf(name, extra = "") {
    const path = join(checkout, "extensions", name, "install.sh");
    mkdirSync(dirname(path), { recursive: true });
    // Keep leaf effects limited to fixture-owned trace/snapshots.
    writeFileSync(path, `#!/usr/bin/env bash\nset -euo pipefail\nprintf '${name}\\n' >> "$TRACE"\npi_home=\"\${PI_CODING_AGENT_DIR:-\"$HOME/.pi/agent\"}\"\nprintf '%s' "$pi_home" > "$OBSERVATIONS/${name}-home"\nif [[ -f "$pi_home/settings.json" ]]; then cat -- "$pi_home/settings.json" > "$OBSERVATIONS/${name}-settings"; fi\n${extra}\n`);
  }
  leaf("alpha"); leaf("z-last");
  mkdirSync(join(checkout, "extensions/ignored-no-installer"));
  const skills = join(checkout, "skills/install-skills.sh");
  mkdirSync(dirname(skills));
  writeFileSync(skills, '#!/bin/sh\nprintf "skills\\n" >> "$TRACE"\n');
  function seed(value, mode = 0o640) {
    mkdirSync(agent, { recursive: true });
    writeFileSync(settings, value, { mode });
    chmodSync(settings, mode);
  }
  function options(extraEnv = {}) { return { cwd, env: { ...env, ...extraEnv }, encoding: "utf8", timeout: 10000 }; }
  function checkNoEffects() {
    assert.equal(existsSync(effects), false, "installer invoked a forbidden external effect");
    assert.equal(readFileSync(projectSettings, "utf8"), '{"tuiMode":"regular","projectOnly":true}\n');
  }
  return { root, home, cwd, checkout, bin, fakePi, agent, settings, env, trace, leaf, seed, options, beforeCleanup,
    run: (args = [], extraEnv = {}) => spawnSync("bash", [join(checkout, "install.sh"), ...args], options(extraEnv)),
    start: (args = [], extraEnv = {}) => startProcess("bash", [join(checkout, "install.sh"), ...args], options(extraEnv)),
    read: () => JSON.parse(readFileSync(settings, "utf8").replace(/^\uFEFF/, "")),
  };
}

function realInstallerFixture(t) {
  const f = fixture(t);
  rmSync(join(f.checkout, "extensions"), { recursive: true });
  const names = readdirSync(join(source, "extensions"))
    .filter(name => existsSync(join(source, "extensions", name, "install.sh"))).sort();
  assert.ok(names.length > 0);
  for (const name of names) cpSync(join(source, "extensions", name), join(f.checkout, "extensions", name), {
    recursive: true, filter: path => !["node_modules", ".git"].includes(basename(path)),
  });
  // Execute every real leaf's shell flow. npm and systemctl are explicitly
  // successful logging stubs, not dependency downloads or user-service work.
  const npmLog = join(f.root, "npm-stub.log"), serviceLog = join(f.root, "systemctl-stub.log");
  f.env.NPM_LOG = npmLog; f.env.SERVICE_LOG = serviceLog; f.env.CHECKOUT = f.checkout;
  writeFileSync(join(f.bin, "npm"), `#!/usr/bin/env bash
set -euo pipefail
[[ "$PWD" == "$CHECKOUT/extensions/"* && "$1" == ci ]] || { echo npm-unexpected >> "$EFFECTS"; exit 91; }
printf '%s\\t%s\\n' "$PWD" "$*" >> "$NPM_LOG"
if [[ "$PWD" == "\${FAIL_NPM_DIR:-}" ]]; then echo 'controlled real leaf npm failure' >&2; exit 23; fi
`);
  writeFileSync(join(f.bin, "systemctl"), `#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$SERVICE_LOG"
case "$*" in
  '--user is-active --quiet graphical-session.target'|'--user is-active --quiet pi-remote-clipboard.service') exit 3 ;;
  '--user daemon-reload'|'--user enable pi-remote-clipboard.service') exit 0 ;;
  *) echo systemctl-unexpected >> "$EFFECTS"; exit 91 ;;
esac
`);
  return { ...f, names, npmLog, serviceLog };
}

function assertRealInstallation(f) {
  const directories = readdirSync(join(source, "extensions"), { withFileTypes: true })
    .filter(entry => entry.isDirectory()).map(entry => entry.name).sort();
  assert.deepEqual(f.names, directories, "every top-level feature must have an install.sh");
  assert.deepEqual(readdirSync(join(f.agent, "extensions")).sort(), f.names, "only Pi leaves are linked");
  for (const name of f.names) {
    const target = join(f.agent, "extensions", name);
    const expected = join(f.checkout, "extensions", name, ...(name === "pi-remote-clipboard" ? ["pi-extension"] : []));
    assert.equal(lstatSync(target).isSymbolicLink(), true, name);
    assert.equal(realpathSync(target), expected, name);
  }
  const manager = SettingsManager.create(f.cwd, f.agent, { projectTrusted: false });
  assert.equal(manager.getTuiMode(), "fullscreen"); assert.deepEqual(manager.drainErrors(), []);
  assert.equal(readFileSync(join(f.home, ".local/lib/pi-remote-clipboard/provider.py"), "utf8"),
    readFileSync(join(f.checkout, "extensions/pi-remote-clipboard/src/provider.py"), "utf8"));
  assert.equal(existsSync(join(f.home, ".ssh/config")), false, "no SSH/auth fixture was manufactured");
  assert.equal(existsSync(join(f.home, ".ssh/known_hosts")), false);
  clean(f.agent);
}

function startProcess(command, args, options) {
  const child = spawn(command, args, options);
  let stdout = "", stderr = "";
  child.stdout.on("data", data => { stdout += data; }); child.stderr.on("data", data => { stderr += data; });
  const done = new Promise((resolve, reject) => {
    child.once("error", reject); child.once("close", status => resolve({ status, stdout, stderr }));
  });
  return { child, done };
}
function passed(result) { assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`); }
function failed(result, pattern) {
  assert.notEqual(result.status, 0); assert.equal(result.error, undefined, "test timed out or failed to spawn");
  assert.match(result.stderr, pattern); assert.doesNotMatch(result.stdout, /Global TUI default|New Pi sessions default/);
}
function unchanged(path, before) {
  assert.deepEqual(readFileSync(path), before.bytes);
  const after = statSync(path, { bigint: true });
  for (const field of ["dev", "ino", "mode", "uid", "gid", "mtimeNs", "ctimeNs"]) assert.equal(after[field], before.stat[field], field);
}
function snapshot(path) { return { bytes: readFileSync(path), stat: statSync(path, { bigint: true }) }; }
function clean(agent) {
  if (existsSync(agent)) assert.deepEqual(readdirSync(agent).filter(name => name.endsWith(".lock") || name.startsWith(".pi-settings-")), []);
}
function preload(f, body) {
  const path = join(f.root, "fault injection.mjs");
  writeFileSync(path, `import fs from "node:fs/promises";\nimport syncFs from "node:fs";\n${body}\n`);
  return { NODE_OPTIONS: `--import=${pathToFileURL(path).href}`, FAULT_TARGET: f.settings };
}
async function waitFor(path) {
  const deadline = Date.now() + 5000;
  while (!existsSync(path)) {
    assert.ok(Date.now() < deadline, `missing controlled marker ${path}`);
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

for (const location of ["default", "custom", "tilde", "relative"]) test(`fresh actual main entry: ${location} home and spaces`, t => {
  const f = fixture(t, location);
  passed(f.run()); assert.deepEqual(f.read(), { tuiMode: "fullscreen" });
  assert.equal(statSync(f.settings).mode & 0o777, 0o600);
  assert.equal(readFileSync(f.trace, "utf8"), "alpha\nz-last\n");
  for (const name of ["alpha", "z-last"]) {
    assert.equal(existsSync(join(f.root, "observations", `${name}-settings`)), false);
    const leafHome = readFileSync(join(f.root, "observations", `${name}-home`), "utf8");
    assert.equal(resolve(f.cwd, leafHome), f.agent);
  }
  if (location !== "default") assert.equal(existsSync(join(f.home, ".pi/agent/settings.json")), false);
  const manager = SettingsManager.create(f.cwd, f.agent, { projectTrusted: false });
  assert.equal(manager.getTuiMode(), "fullscreen"); assert.deepEqual(manager.drainErrors(), []);
  clean(f.agent);
});

test("actual host resolves only the feature entry with real nested components", async t => {
  const f = fixture(t);
  const manifest = JSON.parse(readFileSync(join(vscodeCompanion, "package.json"), "utf8"));
  assert.deepEqual(manifest.extensionKind, ["ui"]);
  assert.ok(manifest.engines.vscode);
  assert.equal(manifest.main, "./extension.js");
  assert.equal(manifest.pi, undefined);
  for (const name of ["install.sh", "index.ts", "index.js"]) assert.equal(existsSync(join(vscodeCompanion, name)), false, name);
  assert.equal(existsSync(join(vscodeCompanion, ".vscodeignore")), true, "hidden package exclusions stay with source");

  const feature = join(f.checkout, "extensions/pi-copy-links");
  cpSync(join(source, "extensions/pi-copy-links"), feature, {
    recursive: true, filter: path => !["node_modules", ".git"].includes(basename(path)),
  });
  assert.ok(existsSync(join(feature, "client/install.sh")), "real nested desktop helper is present");
  assert.ok(existsSync(join(feature, vscodeCompanionName, "extension.js")), "real nested VS Code entry is present");
  mkdirSync(join(f.agent, "extensions"), { recursive: true });
  symlinkSync(feature, join(f.agent, "extensions/pi-copy-links"));

  // Resolve the real manifest/tree first, without importing feature code.
  for (const agent of [f.checkout, f.agent]) {
    const entry = join(agent, "extensions/pi-copy-links/index.ts");
    const packages = new DefaultPackageManager({ cwd: f.cwd, agentDir: agent, settingsManager: SettingsManager.inMemory() });
    const resolved = await packages.resolveExtensionSources([agent], { temporary: true });
    assert.deepEqual(resolved.extensions.map(extension => extension.path), [entry], "package discovery excludes nested components");
  }
  // Keep the real manifest and nested components; stub only the Pi factory to
  // test the host loader without feature dependencies or activation effects.
  writeFileSync(join(feature, "index.ts"), "export default function () {}\n");
  for (const agent of [f.checkout, f.agent]) {
    const loaded = await discoverAndLoadExtensions([], f.cwd, agent);
    assert.deepEqual(loaded.errors, []);
    assert.deepEqual(loaded.extensions.map(extension => extension.path), [join(agent, "extensions/pi-copy-links/index.ts")],
      "only the declared feature entry loads, for both a directory and an installed-style symlink");
  }
});

test("all real discovered installers succeed in a fresh non-Git home and rerun", t => {
  const f = realInstallerFixture(t);
  assert.equal(existsSync(f.agent), false);
  const first = f.run(); passed(first);
  assert.deepEqual(f.read(), { tuiMode: "fullscreen" }); assertRealInstallation(f);
  for (const name of f.names) assert.ok(first.stdout.includes(`--- ${name} ---`));
  const before = snapshot(f.settings); passed(f.run()); unchanged(f.settings, before); assertRealInstallation(f);
  for (const path of [f.root, f.home, f.agent, f.cwd, f.checkout]) assert.equal(existsSync(join(path, ".git")), false);
  const npmRecords = readFileSync(f.npmLog, "utf8").trim().split("\n");
  const expected = [
    `${f.checkout}/extensions/pi-copy-links\tci --ignore-scripts --no-audit --no-fund`,
    `${f.checkout}/extensions/pi-inline-images\tci --ignore-scripts`,
  ];
  assert.deepEqual(npmRecords, [...expected, ...expected]);
  assert.deepEqual(readFileSync(f.serviceLog, "utf8").trim().split("\n"), Array(2).fill([
    "--user daemon-reload", "--user enable pi-remote-clipboard.service",
    "--user is-active --quiet graphical-session.target", "--user is-active --quiet pi-remote-clipboard.service",
  ]).flat());
  t.diagnostic(JSON.stringify({ status: first.status,
    sdkTuiMode: SettingsManager.create(f.cwd, f.agent, { projectTrusted: false }).getTuiMode(),
    installedLinks: readdirSync(join(f.agent, "extensions")).sort(), gitMetadata: existsSync(join(f.agent, ".git")),
    npm: "logging stub", systemctl: "logging no-op stub" }));
});

test("uninspectable Git ownership blocks real main before protected target or settings mutation", t => {
  if (process.getuid() === 0) { t.skip("requires unprivileged permission checks"); return; }
  for (const existing of [true, false]) {
    const f = realInstallerFixture(t); mkdirSync(f.agent, { recursive: true });
    if (existing) f.seed('{"tuiMode":"regular","owned":42}\n');
    f.env.GIT_CONFIG_NOSYSTEM = "1"; f.env.GIT_CONFIG_GLOBAL = "/dev/null";
    execFileSync("git", ["init", "-q", f.agent], { env: f.env });
    const target = join(f.agent, "extensions/pi-inline-images"), metadata = join(f.agent, ".git");
    mkdirSync(dirname(target)); writeFileSync(target, "indexed owner\n");
    execFileSync("git", ["-C", f.agent, "add", "--", "extensions/pi-inline-images"], { env: f.env });
    const index = readFileSync(join(metadata, "index")), before = existing ? snapshot(f.settings) : undefined;
    const mode = statSync(metadata).mode & 0o777; rmSync(target);
    f.beforeCleanup.push(() => chmodSync(metadata, mode)); chmodSync(metadata, 0o000);
    try {
      const result = f.run();
      t.diagnostic(JSON.stringify({ status: result.status, existingSettings: existing, protectedTargetExists: existsSync(target),
        sdkTuiMode: SettingsManager.create(f.cwd, f.agent, { projectTrusted: false }).getTuiMode() }));
      failed(result, /cannot inspect Git ownership/); assert.throws(() => lstatSync(target), { code: "ENOENT" });
      if (before) unchanged(f.settings, before); else assert.equal(existsSync(f.settings), false);
      assert.deepEqual(readFileSync(f.npmLog, "utf8").trim().split("\n"), [
        `${f.checkout}/extensions/pi-copy-links\tci --ignore-scripts --no-audit --no-fund`,
      ], "inline npm was never called");
      assert.equal(existsSync(f.serviceLog), false, "later leaves did not run");
    } finally { chmodSync(metadata, mode); }
    assert.deepEqual(readFileSync(join(metadata, "index")), index);
    assert.ok(execFileSync("git", ["-C", f.agent, "status", "--short"], { env: f.env, encoding: "utf8" }).includes("AD extensions/pi-inline-images"));
  }
});

test("all real discovered installers retain ordinary Git-home behavior", t => {
  const f = realInstallerFixture(t); f.seed('{"tuiMode":"regular","owned":42}\n');
  execFileSync("git", ["init", "-q", f.agent]);
  passed(f.run()); assert.deepEqual(f.read(), { tuiMode: "fullscreen", owned: 42 }); assertRealInstallation(f);
  const before = snapshot(f.settings); passed(f.run()); unchanged(f.settings, before);
  const exclude = readFileSync(join(f.agent, ".git/info/exclude"), "utf8").split("\n");
  for (const name of f.names.filter(name => name !== "pi-remote-clipboard")) assert.equal(exclude.filter(rule => rule === `/extensions/${name}`).length, 1);
});

test("failed real inline npm step prevents settings creation or change", t => {
  for (const existing of [false, true]) {
    const f = realInstallerFixture(t); if (existing) f.seed('{"tuiMode":"regular","owned":42}\n');
    const before = existing ? snapshot(f.settings) : undefined;
    const result = f.run([], { FAIL_NPM_DIR: join(f.checkout, "extensions/pi-inline-images") });
    assert.equal(result.status, 23); failed(result, /controlled real leaf npm failure/);
    if (before) unchanged(f.settings, before); else assert.equal(existsSync(f.settings), false);
    assert.equal(existsSync(join(f.agent, "extensions/pi-inline-images")), false);
    assert.equal(existsSync(f.serviceLog), false, "later real leaf did not run");
  }
});

test("real inline foreign target is retained and prevents fullscreen configuration", t => {
  const f = realInstallerFixture(t); f.seed('{"tuiMode":"regular","owned":42}\n'); const before = snapshot(f.settings);
  const target = join(f.agent, "extensions/pi-inline-images"); mkdirSync(dirname(target));
  writeFileSync(target, "foreign owner\n");
  failed(f.run(), /refusing to replace non-symlink/); unchanged(f.settings, before);
  assert.equal(readFileSync(target, "utf8"), "foreign owner\n");
  assert.equal(readFileSync(f.npmLog, "utf8").trim().split("\n").length, 1, "only the preceding real copy-links npm step ran");
  assert.equal(existsSync(f.serviceLog), false);
});

test("existing regular changes only tuiMode and retains ownership/permissions", t => {
  const f = fixture(t);
  const settings = { tuiMode: "regular", theme: "dark", packages: ["npm:example"], nested: { enabled: false, values: [null, "中文", 42] }, ["__proto__"]: "literal" };
  f.seed(`${JSON.stringify(settings)}\n`, 0o660);
  // A different permitted group verifies group ownership is copied, not reset
  // to the installer's primary group. No elevated privilege is needed.
  const otherGroup = process.getgroups().find(gid => gid !== process.getgid());
  if (otherGroup !== undefined) chownSync(f.settings, process.getuid(), otherGroup);
  const before = snapshot(f.settings);
  passed(f.run()); assert.deepEqual(f.read(), { ...settings, tuiMode: "fullscreen" });
  const after = statSync(f.settings, { bigint: true });
  for (const key of ["mode", "uid", "gid"]) assert.equal(after[key], before.stat[key]);
  assert.notEqual(after.ino, before.stat.ino, "changed content is atomically replaced");
  for (const name of ["alpha", "z-last"]) assert.deepEqual(readFileSync(join(f.root, "observations", `${name}-settings`)), before.bytes);
  clean(f.agent);
});

test("BOM is accepted/preserved and fullscreen rerun is an exact no-op, even read-only", t => {
  const f = fixture(t); f.seed('\uFEFF{ "tuiMode": "regular", "custom": 7 }\n');
  passed(f.run()); assert.equal(readFileSync(f.settings, "utf8")[0], "\uFEFF");
  assert.deepEqual(f.read(), { tuiMode: "fullscreen", custom: 7 });
  chmodSync(f.settings, 0o400);
  const before = snapshot(f.settings);
  const result = f.run(); passed(result); assert.match(result.stdout, /already fullscreen.*unchanged/);
  unchanged(f.settings, before); clean(f.agent);
});

test("already-fullscreen formatting and unrelated values are never rewritten", t => {
  const f = fixture(t); f.seed('{\n\t"unrelated" : [1,2], "tuiMode" : "fullscreen"\n}\n');
  const before = snapshot(f.settings); passed(f.run()); unchanged(f.settings, before); clean(f.agent);
});

for (const [name, data] of [
  ["malformed", '{"tuiMode":'], ["JSON comments", '{/*comment*/"x":1}'], ["empty", ""],
  ["array", "[]\n"], ["null", "null"], ["string", '"regular"'], ["number", "42"],
  ["boolean", "false"], ["BOM invalid", "\uFEFF{broken}"], ["invalid UTF-8", Buffer.from([0x7b, 0x22, 0x78, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d])],
]) test(`refuse ${name} configuration without resetting it`, t => {
  const f = fixture(t); f.seed(data); const before = snapshot(f.settings);
  failed(f.run(), /failed to configure global fullscreen/); unchanged(f.settings, before); clean(f.agent);
});

test("child failure means no setting creation/change and no later leaf", t => {
  for (const existing of [false, true]) {
    const f = fixture(t); if (existing) f.seed('{"tuiMode":"regular","owned":42}\n');
    const before = existing ? snapshot(f.settings) : undefined;
    f.leaf("alpha", 'echo "controlled leaf failure" >&2; exit 23');
    const result = f.run(); assert.equal(result.status, 23); failed(result, /controlled leaf failure/);
    assert.equal(readFileSync(f.trace, "utf8"), "alpha\n");
    if (existing) unchanged(f.settings, before); else assert.equal(existsSync(f.settings), false);
  }
});

test("optional skills complete before persistence; their failure leaves settings alone", t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular"}\n');
  const skills = join(f.checkout, "skills/install-skills.sh");
  writeFileSync(skills, '#!/bin/sh\nprintf "skills\\n" >> "$TRACE"\necho "controlled skills failure" >&2\nexit 24\n');
  const before = snapshot(f.settings); failed(f.run(["--skills"]), /controlled skills failure/); unchanged(f.settings, before);
  writeFileSync(skills, '#!/bin/sh\nprintf "skills\\n" >> "$TRACE"\n');
  passed(f.run(["--skills"])); assert.equal(f.read().tuiMode, "fullscreen");
  assert.equal(readFileSync(f.trace, "utf8"), "alpha\nz-last\nskills\nalpha\nz-last\nskills\n");
});

test("help, invalid argument and absent leaves do not create settings", t => {
  const f = fixture(t); passed(f.run(["--help"])); assert.equal(existsSync(f.trace), false);
  failed(f.run(["--bogus"]), /unknown option/);
  rmSync(join(f.checkout, "extensions"), { recursive: true });
  failed(f.run(), /no extension installers found/); assert.equal(existsSync(f.settings), false);
});

test("installed lock dependency is required; failure is explicit, not a reset", t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular"}\n'); const before = snapshot(f.settings);
  unlinkSync(join(dirname(f.fakePi), "node_modules/proper-lockfile"));
  failed(f.run(), /cannot load Pi's proper-lockfile dependency/); unchanged(f.settings, before);
});

test("preserve a settings symlink and atomically update its real source", t => {
  const f = fixture(t); mkdirSync(f.agent, { recursive: true });
  const target = join(f.root, "settings source with spaces/settings.json"); mkdirSync(dirname(target));
  writeFileSync(target, '{"tuiMode":"regular","sourceOwned":true}\n', { mode: 0o640 });
  symlinkSync(target, f.settings); const linkBefore = lstatSync(f.settings), before = snapshot(target);
  passed(f.run()); assert.equal(lstatSync(f.settings).isSymbolicLink(), true);
  assert.equal(lstatSync(f.settings).ino, linkBefore.ino); assert.equal(readlinkSync(f.settings), target);
  assert.deepEqual(f.read(), { tuiMode: "fullscreen", sourceOwned: true });
  assert.equal(statSync(target, { bigint: true }).mode, before.stat.mode);
  assert.notEqual(statSync(target, { bigint: true }).ino, before.stat.ino);
  const full = snapshot(target); passed(f.run()); unchanged(target, full); clean(f.agent); clean(dirname(target));
});

test("a symlinked Pi home preserves the directory alias and real settings source", t => {
  const f = fixture(t, "custom"), realAgent = join(f.root, "real agent"); mkdirSync(realAgent);
  symlinkSync(realAgent, f.agent); f.seed('{"custom":42}\n'); passed(f.run());
  assert.equal(lstatSync(f.agent).isSymbolicLink(), true);
  assert.deepEqual(JSON.parse(readFileSync(join(realAgent, "settings.json"), "utf8")), { custom: 42, tuiMode: "fullscreen" });
  clean(realAgent);
});

for (const kind of ["dangling symlink", "directory", "hardlink"]) test(`refuse ${kind} settings without replacing their owner`, t => {
  const f = fixture(t); mkdirSync(f.agent, { recursive: true });
  if (kind === "dangling symlink") symlinkSync(join(f.root, "missing-source"), f.settings);
  if (kind === "directory") mkdirSync(f.settings);
  if (kind === "hardlink") { f.seed('{"tuiMode":"regular"}'); linkSync(f.settings, join(f.root, "other-owner")); }
  const before = lstatSync(f.settings); const contents = kind === "hardlink" ? snapshot(f.settings) : undefined;
  failed(f.run(), /failed to configure global fullscreen/);
  assert.equal(lstatSync(f.settings).ino, before.ino);
  if (contents) unchanged(f.settings, contents);
  if (kind === "dangling symlink") assert.equal(existsSync(join(f.root, "missing-source")), false);
  clean(f.agent);
});

test("read-only settings or an unwritable agent directory fail without replacement", t => {
  if (process.getuid() === 0) { t.skip("permission denial requires an unprivileged test user"); return; }
  for (const kind of ["file", "directory"]) {
    const f = fixture(t); f.seed('{"tuiMode":"regular"}\n');
    chmodSync(kind === "file" ? f.settings : f.agent, kind === "file" ? 0o400 : 0o500);
    f.beforeCleanup.push(() => chmodSync(f.agent, 0o700));
    const before = snapshot(f.settings); failed(f.run(), /EACCES|permission denied/); unchanged(f.settings, before);
    clean(f.agent);
  }
});

test("metadata preservation failure keeps the old settings intact", t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular","owned":true}\n'); const before = snapshot(f.settings);
  writeFileSync(join(f.bin, "cp"), '#!/bin/sh\necho "cannot preserve ownership" >&2\nexit 1\n'); chmodSync(join(f.bin, "cp"), 0o755);
  failed(f.run(), /cannot preserve ownership/); unchanged(f.settings, before); clean(f.agent);
});

for (const fault of ["partial write", "rename"]) test(`${fault} failure never leaves partial settings`, t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular","owned":true}\n'); const before = snapshot(f.settings);
  const env = preload(f, fault === "rename" ? `
    const rename = fs.rename.bind(fs);
    fs.rename = async (from, to) => {
      if (to === process.env.FAULT_TARGET) throw Object.assign(new Error("controlled rename EACCES"), { code: "EACCES" });
      return rename(from, to);
    };` : `
    const open = fs.open.bind(fs);
    fs.open = async (...args) => {
      const file = await open(...args);
      if (args[1] === "wx") {
        const write = file.writeFile.bind(file);
        file.writeFile = async (text, encoding) => {
          await write(text.slice(0, 12), encoding);
          throw Object.assign(new Error("controlled partial write ENOSPC"), { code: "ENOSPC" });
        };
      }
      return file;
    };`);
  failed(f.run([], env), /controlled .*EACCES|controlled .*ENOSPC/); unchanged(f.settings, before); clean(f.agent);
});

test("an observed out-of-lock settings write is retained, not overwritten", t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular","owned":true}\n');
  const foreign = '{"tuiMode":"regular","foreignUpdate":"retained"}\n';
  const env = preload(f, `
    const open = fs.open.bind(fs);
    fs.open = async (...args) => {
      const file = await open(...args);
      if (args[1] === "wx") {
        const sync = file.sync.bind(file);
        file.sync = async () => { await sync(); await fs.writeFile(process.env.FAULT_TARGET, ${JSON.stringify(foreign)}); };
      }
      return file;
    };`);
  failed(f.run([], env), /settings changed outside the lock/);
  assert.equal(readFileSync(f.settings, "utf8"), foreign); clean(f.agent);
});

test("a replaced empty lock remains owned by its successor", t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular"}\n'); const before = snapshot(f.settings);
  const env = preload(f, `
    const open = fs.open.bind(fs);
    fs.open = async (...args) => {
      const file = await open(...args);
      if (args[1] === "wx") {
        const sync = file.sync.bind(file);
        file.sync = async () => {
          await sync();
          const prior = syncFs.lstatSync(process.env.FAULT_TARGET + ".lock").ino;
          await fs.rmdir(process.env.FAULT_TARGET + ".lock");
          await fs.mkdir(process.env.FAULT_TARGET + ".lock");
          syncFs.writeFileSync(${JSON.stringify(join(f.root, "lock-identities"))}, JSON.stringify([prior, syncFs.lstatSync(process.env.FAULT_TARGET + ".lock").ino]));
        };
      }
      return file;
    };`);
  const result = f.run([], env);
  assert.notEqual(result.status, 0, `lock identities ${readFileSync(join(f.root, "lock-identities"), "utf8")}: ${result.stdout} ${result.stderr}`);
  failed(result, /settings lock was replaced/); unchanged(f.settings, before);
  assert.equal(existsSync(`${f.settings}.lock`), true, "cleanup must not remove a successor's lock");
});

test("a primary parse failure stays visible when unlocking also fails", t => {
  const f = fixture(t); f.seed("{"); const before = snapshot(f.settings);
  const env = preload(f, `
    const rmdir = syncFs.rmdir;
    syncFs.rmdir = (...args) => {
      if (args[0] === process.env.FAULT_TARGET + ".lock") return args.at(-1)(new Error("controlled unlock failure"));
      return rmdir(...args);
    };`);
  const result = f.run([], env); failed(result, /JSON/); assert.match(result.stderr, /controlled unlock failure/);
  unchanged(f.settings, before); assert.equal(existsSync(`${f.settings}.lock`), true);
});

test("post-commit durability failure is explicit and leaves complete valid JSON", t => {
  const f = fixture(t); f.seed('{"owned":true}\n');
  const env = preload(f, `
    const open = fs.open.bind(fs);
    fs.open = async (...args) => {
      const file = await open(...args);
      if (args[0] === ${JSON.stringify(f.agent)}) file.sync = async () => { throw new Error("controlled directory fsync EIO"); };
      return file;
    };`);
  failed(f.run([], env), /fullscreen committed, but completion failed.*controlled directory fsync EIO/);
  assert.deepEqual(f.read(), { owned: true, tuiMode: "fullscreen" }); clean(f.agent);
});

test("active Pi-compatible lock refuses the update without removing that lock", async t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular","owned":true}\n'); const before = snapshot(f.settings);
  const release = await lockfile.lock(f.settings, { realpath: false });
  try { failed(f.run(), /already being held/); unchanged(f.settings, before); assert.ok(existsSync(`${f.settings}.lock`)); }
  finally { await release(); }
  clean(f.agent);
});

test("a lock on the real symlink target also blocks the main updater", async t => {
  const f = fixture(t); mkdirSync(f.agent, { recursive: true }); const target = join(f.root, "real-settings.json");
  writeFileSync(target, '{"tuiMode":"regular"}'); symlinkSync(target, f.settings); const before = snapshot(target);
  const release = await lockfile.lock(target, { realpath: false });
  try { failed(f.run(), /already being held/); unchanged(target, before); assert.ok(existsSync(`${target}.lock`)); }
  finally { await release(); }
  clean(f.agent);
});

test("foreign non-directory lock is preserved even if its mtime is old", t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular"}'); const before = snapshot(f.settings);
  const lock = `${f.settings}.lock`; writeFileSync(lock, "foreign lock owner");
  execFileSync("touch", ["-d", "2000-01-01", lock]); const lockBefore = snapshot(lock);
  failed(f.run(), /already being held|ENOTDIR/); unchanged(f.settings, before); unchanged(lock, lockBefore);
});

test("existing-file SDK write under Pi's lock is reread before merging fullscreen", async t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular","owned":42}\n');
  const held = join(f.root, "SDK-lock-held"), attempt = join(f.root, "installer-lock-attempt");
  const writerPath = join(f.root, "SDK writer.mjs");
  writeFileSync(writerPath, `import { FileSettingsStorage } from ${JSON.stringify(settingsModule)};
    import { existsSync, writeFileSync } from "node:fs";
    const storage = new FileSettingsStorage(${JSON.stringify(f.cwd)}, ${JSON.stringify(f.agent)});
    storage.withLock("global", current => {
      writeFileSync(${JSON.stringify(held)}, "held");
      const deadline = Date.now() + 5000;
      while (!existsSync(${JSON.stringify(attempt)})) { if (Date.now() > deadline) throw new Error("installer did not attempt lock"); }
      return JSON.stringify({ ...JSON.parse(current), sdkBeforeCommit: { retained: true } });
    });`);
  const writer = startProcess(process.execPath, [writerPath], f.options());
  try {
    await waitFor(held);
    const env = preload(f, `
      const mkdir = syncFs.mkdir;
      syncFs.mkdir = (...args) => {
        if (args[0] === process.env.FAULT_TARGET + ".lock") syncFs.writeFileSync(${JSON.stringify(attempt)}, "attempt");
        return mkdir(...args);
      };`);
    passed(await f.start([], env).done); passed(await writer.done);
    assert.deepEqual(f.read(), { tuiMode: "fullscreen", owned: 42, sdkBeforeCommit: { retained: true } }); clean(f.agent);
  } finally { if (writer.child.exitCode === null) writer.child.kill(); await writer.done; }
});

test("SDK modified-field merge after installation retains fullscreen; project override stays separate", async t => {
  const f = fixture(t); f.seed('{"tuiMode":"regular","owned":42}\n');
  const sdk = SettingsManager.create(f.cwd, f.agent);
  passed(f.run()); sdk.setDefaultModel("sdk-model-after-install"); await sdk.flush();
  assert.deepEqual(sdk.drainErrors(), []);
  assert.deepEqual(f.read(), { tuiMode: "fullscreen", owned: 42, defaultModel: "sdk-model-after-install" });
  const reloaded = SettingsManager.create(f.cwd, f.agent);
  assert.equal(reloaded.getTuiMode(), "regular", "trusted project settings override the global default"); clean(f.agent);
});

test("settings ACL and user xattr survive atomic replacement", t => {
  if (spawnSync("setfacl", ["--version"]).status !== 0 || spawnSync("python3", ["--version"]).status !== 0) {
    t.skip("optional metadata regression requires setfacl and python3"); return;
  }
  const f = fixture(t); f.seed('{"tuiMode":"regular","metadata":true}\n');
  execFileSync("setfacl", ["-m", "u:65534:r--", f.settings]);
  execFileSync("python3", ["-c", 'import os,sys; os.setxattr(sys.argv[1], "user.pi-installer-test", b"kept")', f.settings]);
  const acl = execFileSync("getfacl", ["-cp", f.settings], { encoding: "utf8" });
  passed(f.run()); assert.equal(execFileSync("getfacl", ["-cp", f.settings], { encoding: "utf8" }), acl);
  assert.equal(execFileSync("python3", ["-c", 'import os,sys; print(os.getxattr(sys.argv[1], "user.pi-installer-test").decode())', f.settings], { encoding: "utf8" }), "kept\n");
  assert.deepEqual(f.read(), { tuiMode: "fullscreen", metadata: true }); clean(f.agent);
});
