import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { dispatchOpenTarget, type DispatcherDependencies, type HelperSpawn } from "../src/dispatcher.ts";
import { pairedSshAlias } from "../src/pairing.ts";
import type { InputTopology } from "../src/receiver.ts";
import { sourceLinkEvidence, sourcePathMatches } from "../src/source.ts";

const fixture = fileURLToPath(new URL("./paired-helper-fixture.py", import.meta.url));
const roles = [
  { server: "conley-Y9000P", desktop: "conley-company", peer: "192.0.2.103", authority: "conley-Y9000p" },
  { server: "conley-company", desktop: "conley-Y9000P", peer: "192.0.2.64", authority: "conley-company" },
] as const;
const lifetime = () => ({ signal: new AbortController().signal, isCurrent: () => true });
const automatic = (path: string) => {
  const raw = "`" + path + "`";
  const matches = sourcePathMatches(sourceLinkEvidence("Open " + raw), "codespan", raw, path);
  assert.equal(matches.length, 1, "whole-source path must have one automatic target");
  return matches[0]!.href;
};
const commands = (root: string): { name: string; args: string[] }[] => {
  const path = join(root, "commands.jsonl");
  return existsSync(path) ? readFileSync(path, "utf8").trim().split("\n").map((line) => JSON.parse(line)) : [];
};

// Receiver kernel/PTY authority is exercised in receiver.test.ts. This fixture
// starts at its admitted topology boundary, then uses both exact production
// components over real pipes, replacing only SSH/desktop endpoints and OS host.
function endpoint(role: typeof roles[number], root: string, paths: string[], helperHost: string = role.desktop) {
  const alias = pairedSshAlias(role.server);
  assert.ok(alias);
  const topology: InputTopology = Object.freeze({ kind: "remote", hops: Object.freeze([]),
    receiver: Object.freeze({ kind: "remote", alias, peer: role.peer, port: 22, user: "conley" }) });
  let replies = "", input = "", calls = 0;
  const run: HelperSpawn = (command, args, options) => {
    calls++;
    assert.equal(command, "ssh");
    assert.deepEqual(args, ["-T", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes",
      "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", `HostName=${role.peer}`,
      "-o", "Port=22", "-o", "User=conley", alias, "~/.local/bin/pi-client-link-helper"]);
    assert.deepEqual(options, { stdio: ["pipe", "pipe", "pipe"], shell: false, detached: true });
    const child = spawn("python3", [fixture, root, helperHost, JSON.stringify(paths)], {
      ...options, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
    });
    child.stdout.on("data", (chunk: Buffer) => { replies += chunk.toString("utf8"); });
    // Observe the actual channel without replacing framing or synthesizing a reply.
    const write = child.stdin.write.bind(child.stdin);
    child.stdin.write = ((chunk: string | Buffer, ...args: unknown[]) => {
      input += chunk.toString();
      return Reflect.apply(write, undefined, [chunk, ...args]);
    }) as typeof child.stdin.write;
    return child;
  };
  const dependencies: DispatcherDependencies = { topology, revalidateTopology: async () => true,
    spawn: run, local: async () => assert.fail("paired action must not fall back to a server desktop") };
  return { dependencies, observed: () => ({ replies: replies.trim().split("\n").map((line) => JSON.parse(line)),
    input: input.trim().split("\n").map((line) => JSON.parse(line)), calls }) };
}

test("both rows: automatic path -> server stat -> pinned v2 helper -> opposite Code URI", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-paired-path-"));
  try {
    const server = join(root, "same-server-path");
    mkdirSync(server);
    const literal = "中文 %20#L12?:7.png", folder = "project.v2";
    writeFileSync(join(server, literal), "server-only sentinel; never read by desktop");
    writeFileSync(join(server, "Makefile"), "target:\n");
    mkdirSync(join(server, folder));
    const targets = [
      { href: automatic("./" + literal), path: join(server, literal), kind: "file", suffix: "" },
      { href: automatic("./" + folder + "/"), path: join(server, folder), kind: "directory", suffix: "" },
      { href: "./Makefile#L9C3", path: join(server, "Makefile"), kind: "file", suffix: ":9:3" },
    ];
    // Both roles use these exact same filesystem spellings. Only the paired
    // machine identity changes the authority, never path existence or basename.
    for (const role of roles) for (const [index, target] of targets.entries()) {
      const desktop = join(root, role.server + "-" + index);
      mkdirSync(desktop);
      const bridge = endpoint(role, desktop, targets.map(({ path }) => path));
      const stats: string[] = [];
      await dispatchOpenTarget(target.href, server, lifetime(), "unknown", { ...bridge.dependencies,
        stat: async (path) => { stats.push(path); return stat(path); } });
      assert.deepEqual(stats, [target.path]);
      const { replies, input, calls } = bridge.observed();
      assert.equal(calls, 1);
      assert.deepEqual(replies.map(({ phase }) => phase), ["prepared", "result"]);
      assert.deepEqual(input[1], { version: 2, phase: "commit", nonce: replies[0].nonce });
      const { phase, prepareDeadlineMs, ...request } = input[0];
      assert.equal(phase, "request");
      assert.equal(prepareDeadlineMs, 45000);
      assert.deepEqual(request, { version: 2, action: "open-file", path: target.path, kind: target.kind,
        ...(target.suffix ? { line: 9, column: 3 } : {}) });
      for (const reply of replies) assert.equal(reply.path, target.path);
      const remote = "ssh-remote+" + role.authority;
      const uri = "vscode-remote://" + remote + target.path.split("/").map(encodeURIComponent).join("/") + target.suffix;
      const opened = commands(desktop).filter(({ name }) => name === "code");
      assert.deepEqual(opened.map(({ args }) => args), [["--remote", remote, "--new-window",
        ...(target.suffix ? ["--goto"] : []), target.kind === "file" ? "--file-uri" : "--folder-uri", uri]]);
      assert.ok(commands(desktop).every(({ name }) => !["ssh", "ss", "xdg-open", "google-chrome-stable"].includes(name)));
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("unknown server or helper identity cannot cross the combined seam", async () => {
  const root = mkdtempSync(join(tmpdir(), "pi-paired-unknown-"));
  try {
    assert.equal(pairedSshAlias("unpaired-server"), undefined);
    const unknown: InputTopology = { kind: "unknown", hops: [], diagnostic: "unpaired-server has no paired SSH policy" };
    await assert.rejects(dispatchOpenTarget(automatic("./Makefile"), root, lifetime(), "unknown", {
      topology: unknown, resolveTopology: async () => unknown,
      stat: async () => assert.fail("unknown server must not stat"),
      spawn: () => assert.fail("unknown server must not spawn"),
      local: async () => assert.fail("unknown server must not launch locally"),
    }), /unpaired-server/u);
    writeFileSync(join(root, "Makefile"), "server sentinel");
    const desktop = join(root, "unknown-desktop");
    mkdirSync(desktop);
    const bridge = endpoint(roles[0], desktop, [join(root, "Makefile")], "unpaired-desktop");
    await assert.rejects(dispatchOpenTarget(automatic("./Makefile"), root, lifetime(), "unknown", bridge.dependencies),
      /unsupported_host/u);
    assert.deepEqual(commands(desktop), []);
    const observed = bridge.observed();
    assert.equal(observed.input.length, 1, "unknown helper receives no COMMIT");
    assert.equal(observed.replies.length, 1);
    assert.equal(observed.replies[0].error.code, "unsupported_host");
  } finally { rmSync(root, { recursive: true, force: true }); }
});
