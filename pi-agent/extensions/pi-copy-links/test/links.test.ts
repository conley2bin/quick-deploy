import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import type { OpenLifetime, SpawnRunner } from "../src/browser.ts";
import { openTarget, parseOpenTarget } from "../src/links.ts";

function lifetime(controller = new AbortController()): OpenLifetime & { current: boolean } {
  const value = { signal: controller.signal, current: true, isCurrent: () => value.current };
  return value;
}

function recorder(records: Array<{ command: string; args: readonly string[]; env?: NodeJS.ProcessEnv }>): SpawnRunner {
  return (command, args, options) => {
    records.push({ command, args, env: options.env });
    const child = new EventEmitter() as ReturnType<SpawnRunner>;
    child.unref = () => child;
    queueMicrotask(() => child.emit("exit", 0, null));
    return child;
  };
}

test("destination grammar distinguishes web, local selectors, file URLs and encoded literals", () => {
  const cwd = "/work/project";
  assert.deepEqual(parseOpenTarget("https://host/path?q=x#L9", cwd), { kind: "web", url: "https://host/path?q=x#L9" });
  assert.deepEqual(parseOpenTarget("out.svg", cwd), { kind: "local", path: "/work/project/out.svg" });
  assert.deepEqual(parseOpenTarget("./src/file.ts:12:3", cwd), { kind: "local", path: "/work/project/src/file.ts", line: 12, column: 3 });
  assert.deepEqual(parseOpenTarget("src/file.ts:12:3", cwd), { kind: "local", path: "/work/project/src/file.ts", line: 12, column: 3 });
  assert.deepEqual(parseOpenTarget("src/file.ts#L12C3", cwd), { kind: "local", path: "/work/project/src/file.ts", line: 12, column: 3 });
  assert.deepEqual(parseOpenTarget("/tmp/file.ts:12", cwd), { kind: "local", path: "/tmp/file.ts", line: 12 });
  assert.deepEqual(parseOpenTarget("file:///tmp/file.ts#L12", cwd), { kind: "local", path: "/tmp/file.ts", line: 12 });
  assert.deepEqual(parseOpenTarget("file://localhost/tmp/name:12", cwd), { kind: "local", path: "/tmp/name:12" });
  assert.deepEqual(parseOpenTarget("name%3A12", cwd), { kind: "local", path: "/work/project/name:12" });
  assert.deepEqual(parseOpenTarget("name%23L12", cwd), { kind: "local", path: "/work/project/name#L12" });
  assert.deepEqual(parseOpenTarget("name%2520+中文", cwd), { kind: "local", path: "/work/project/name%20+中文" });
  assert.deepEqual(parseOpenTarget("literal%3Fname", cwd), { kind: "local", path: "/work/project/literal?name" });
});

test("ambiguous, malformed and unsupported targets fail before filesystem interpretation", () => {
  const cwd = "/work/project";
  const cases: Array<[string, RegExp]> = [
    ["README.md:12", /歧义/], ["name:12", /歧义/], ["//host/path", /authority/],
    ["file://other-host/path", /远程/], ["file:relative", /file:\/\/\//], ["file:/path", /file:\/\/\//],
    ["mailto:a@b", /不支持 URI scheme/], ["vscode:x", /不支持 URI scheme/], ["command:x", /不支持 URI scheme/],
    ["javascript:x", /不支持 URI scheme/], ["data:text/plain,x", /不支持 URI scheme/],
    ["x?query", /查询参数/], ["x?", /查询参数/], ["#", /片段/], ["#section", /片段/],
    ["x#L1-L2", /片段/], ["./x:12#L13", /同时包含/], ["./x:0", /正十进制/],
    ["./x:-1", /正十进制/], ["./x:2147483648", /超出范围/], ["x#L0", /片段|正十进制/],
    ["x#L2147483648", /超出范围/], ["x%", /百分号/], ["x%FF", /UTF-8/], ["x%00", /控制字符/],
    ["x%2Fy", /路径分隔符/], ["file:///tmp/a%2Fb", /路径分隔符/], ["file:///tmp\\a", /反斜杠/],
  ];
  for (const [value, pattern] of cases) assert.throws(() => parseOpenTarget(value, cwd), pattern, value);
  assert.throws(() => parseOpenTarget("x", "relative/cwd"), /绝对路径/);
});

test("existing files, directories and symlinks launch VS Code with explicit argv and clean IPC env", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-targets-"));
  const file = join(root, "file with 空格.ts");
  const directory = join(root, "folder");
  const link = join(root, "linked.ts");
  writeFileSync(file, "line\n");
  mkdirSync(directory);
  symlinkSync(file, link);
  const records: Array<{ command: string; args: readonly string[]; env?: NodeJS.ProcessEnv }> = [];
  const env = { PATH: "/fixture", VSCODE_IPC_HOOK_CLI: "/remote/socket", DISPLAY: ":1" };
  try {
    await openTarget("./file%20with%20%E7%A9%BA%E6%A0%BC.ts#L12C3", root, lifetime(), { platform: "linux", spawn: recorder(records), env });
    await openTarget("./folder", root, lifetime(), { platform: "linux", spawn: recorder(records), env });
    await openTarget("./linked.ts", root, lifetime(), { platform: "linux", spawn: recorder(records), env });
    assert.deepEqual(records.map(({ command, args }) => ({ command, args })), [
      { command: "code", args: ["--new-window", "--goto", "--", `${file}:12:3`] },
      { command: "code", args: ["--new-window", "--", directory] },
      { command: "code", args: ["--new-window", "--", link] },
    ]);
    assert.equal(records[0]!.env?.VSCODE_IPC_HOOK_CLI, undefined);
    assert.equal(records[0]!.env?.DISPLAY, ":1");
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("missing, dangling, special and invalid-position targets never launch Code", async (t) => {
  if (process.platform !== "linux") { t.skip("POSIX special-file fixture"); return; }
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-invalid-"));
  mkdirSync(join(root, "folder"));
  symlinkSync(join(root, "missing"), join(root, "dangling"));
  execFileSync("mkfifo", [join(root, "pipe")]);
  writeFileSync(join(root, "colon:name.ts"), "x");
  const records: Array<{ command: string; args: readonly string[]; env?: NodeJS.ProcessEnv }> = [];
  try {
    for (const [href, pattern] of [
      ["missing", /不存在/], ["dangling", /不存在/], ["pipe", /普通文件或目录/],
      ["./folder:2", /目录不能/], ["colon%3Aname.ts#L2", /--goto/],
    ] as const) await assert.rejects(openTarget(href, root, lifetime(), { platform: "linux", spawn: recorder(records) }), pattern);
    assert.deepEqual(records, []);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("missing Code is reported with no alternate launcher", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-missing-code-"));
  writeFileSync(join(root, "file.ts"), "x");
  const commands: string[] = [];
  const spawn: SpawnRunner = (command) => {
    commands.push(command);
    const child = new EventEmitter() as ReturnType<SpawnRunner>;
    child.unref = () => child;
    queueMicrotask(() => child.emit("error", new Error("ENOENT")));
    return child;
  };
  try {
    await assert.rejects(openTarget("file.ts", root, lifetime(), { platform: "linux", spawn }), /code: ENOENT/);
    assert.deepEqual(commands, ["code"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("file URL spelling is preserved as an absolute Code target", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-copy-links-file-url-"));
  const file = join(root, "a+b.json");
  writeFileSync(file, "{}");
  const records: Array<{ command: string; args: readonly string[]; env?: NodeJS.ProcessEnv }> = [];
  try {
    await openTarget(pathToFileURL(file).href, "/ignored", lifetime(), { platform: "linux", spawn: recorder(records) });
    assert.deepEqual(records[0]!.args, ["--new-window", "--", file]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("retirement while stat is paused prevents Code spawn", async () => {
  const controller = new AbortController();
  const current = lifetime(controller);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const records: Array<{ command: string; args: readonly string[]; env?: NodeJS.ProcessEnv }> = [];
  const pending = openTarget("file.ts", "/work", current, {
    platform: "linux",
    stat: async () => { await gate; return { isDirectory: () => false, isFile: () => true }; },
    spawn: recorder(records),
  });
  controller.abort();
  release();
  await assert.rejects(pending, /动作已失效/);
  assert.deepEqual(records, []);
});
