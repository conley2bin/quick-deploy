import assert from "node:assert/strict";
import test from "node:test";
import { enableTmuxMouse, MOUSE_KEYS, type TmuxRun } from "../src/tmux.ts";
import type { InputTopology } from "../src/receiver.ts";

const STOCK_DOWN = "bind-key -T root MouseDown1Pane select-pane -t = \\; send-keys -M";
const STOCK_TAGGED = String.raw`bind-key -T root MouseDown1Pane select-pane -t = \; if-shell -F -t = "#{&&:#{@pi-copy-links-input},#{mouse_any_flag}}" "run-shell -C 'send-keys -l -t \"#{pane_id}\" \"\\e[998;#{client_pid}~\" ; send-keys -M'" "send-keys -M"`;
const condition = "#{&&:#{@pi-copy-links-input},#{mouse_any_flag}}";
const oldBinding = (key: string) => `bind-key -T root ${key} if-shell -F -t = "${condition}" "send-keys -M" ''`;
const taggedBinding = (key: string) => key === "MouseDown1Pane" ? STOCK_TAGGED :
  String.raw`bind-key -T root ${key} if-shell -F -t = "#{&&:#{@pi-copy-links-input},#{mouse_any_flag}}" "run-shell -C 'send-keys -l -t \"#{pane_id}\" \"\\e[998;#{client_pid}~\" ; send-keys -M'" ` +
    (key === "MouseUp1Pane" ? '"send-keys -M"' : "''");
const env = { TMUX: "/fixture/socket,with-comma,100,2", TMUX_PANE: "%42" };
const hop = Object.freeze({ tty: "/dev/pts/42", leader: Object.freeze({ pid: 1, starttime: "1" }),
  holder: Object.freeze({ pid: 2, starttime: "2", comm: "tmux: server" }) });
const topology: InputTopology = Object.freeze({ kind: "tmux", socket: "/fixture/socket,with-comma",
  pane: "%42", inputTty: "/dev/pts/42", hops: Object.freeze([hop]) });
const direct: InputTopology = Object.freeze({ kind: "local", basis: "gui", hops: Object.freeze([hop]) });

function server(version = "tmux 3.4") {
  const keys = new Map<string, string>([["MouseDown1Pane", STOCK_DOWN]]);
  const options = new Map<string, string>();
  const calls: string[][] = [];
  const run: TmuxRun = async (all) => {
    calls.push(all);
    assert.deepEqual(all.slice(0, 2), ["-S", "/fixture/socket,with-comma"]);
    const args = all.slice(2), command = args[0];
    if (command === "-V") return { code: 0, stdout: version, stderr: "" };
    if (command === "list-keys") return keys.has(args[3]!) ?
      { code: 0, stdout: keys.get(args[3]!)! + "\n", stderr: "" } :
      { code: 1, stdout: "", stderr: "unknown key" };
    if (command === "show-options") return { code: 0, stdout: options.get(args.at(-1)!) ?? "", stderr: "" };
    if (command === "bind-key") {
      keys.set(args[3]!, taggedBinding(args[3]!));
    } else if (command === "set-option") {
      if (args[1]!.includes("u")) options.delete(args.at(-1)!);
      else options.set(args.at(-2)!, args.at(-1)!);
    } else throw new Error("Unexpected tmux command: " + args.join(" "));
    return { code: 0, stdout: "", stderr: "" };
  };
  return { run, calls, keys, options };
}

function bindingCall(calls: string[][], key: string) {
  return calls.find((args) => args[2] === "bind-key" && args[5] === key);
}

test("tmux 3.4 stock down is adopted after exact match; mouse marker follows selection", async () => {
  const f = server();
  const first = await enableTmuxMouse(topology, env, f.run);
  assert.equal(first.enabled, true); assert.deepEqual(first.warnings, []);
  assert.equal(f.keys.size, MOUSE_KEYS.length);
  assert.equal(f.options.get("@pi-copy-links-prior-MouseDown1Pane"), STOCK_DOWN);
  const down = bindingCall(f.calls, "MouseDown1Pane")!;
  assert.deepEqual(down.slice(6, 14), ["select-pane", "-t", "=", "\\;", "if-shell", "-F", "-t", "="]);
  assert.equal(down[14], condition);
  assert.match(down[15]!, /run-shell -C 'send-keys -l -t "#\{pane_id\}" "\\e\[998;#\{client_pid\}~" ; send-keys -M'/);
  assert.equal(down[16], "send-keys -M", "unowned pane retains stock send after select");
  const up = bindingCall(f.calls, "MouseUp1Pane")!;
  assert.equal(up.at(-1), "send-keys -M", "unowned pane retains native ordinary release");
  assert.ok([...f.keys.values()].every((value) => value.includes("MouseDown1Pane") || value.includes("@pi-copy-links-input")));
  await first.dispose(); assert.equal(f.options.has("@pi-copy-links-input"), false);
  const count = f.calls.filter((args) => args[2] === "bind-key").length;
  const second = await enableTmuxMouse(topology, env, f.run); assert.deepEqual(second.warnings, []);
  assert.equal(f.calls.filter((args) => args[2] === "bind-key").length, count);
  await second.dispose();
  assert.equal(f.keys.size, MOUSE_KEYS.length, "shared guarded keys stay inert after unload");
});

test("receipt-owned legacy Ctrl bindings upgrade; forged and foreign bindings stay untouched", async () => {
  const f = server();
  const upgrade = "C-MouseDown1Pane", foreign = "C-MouseUp1Pane", forged = "C-MouseDrag1Pane";
  f.keys.set(upgrade, oldBinding(upgrade));
  f.options.set(`@pi-copy-links-binding-${upgrade}`, oldBinding(upgrade));
  f.keys.set(foreign, "foreign custom binding");
  f.keys.set(forged, "foreign command with forged receipt");
  f.options.set(`@pi-copy-links-binding-${forged}`, "foreign command with forged receipt");
  const result = await enableTmuxMouse(topology, env, f.run);
  assert.deepEqual(result.warnings, [foreign, forged]);
  assert.ok(bindingCall(f.calls, upgrade), "exact receipt-owned old command was upgraded");
  assert.ok(!bindingCall(f.calls, foreign));
  assert.equal(f.keys.get(foreign), "foreign custom binding");
  assert.equal(f.keys.get(forged), "foreign command with forged receipt", "a forged receipt cannot authorize an upgrade");
  await result.dispose();
});

test("non-stock mouse down and non-3.4 stock stay untouched and warn", async () => {
  for (const [version, value] of [["tmux 3.3", STOCK_DOWN], ["tmux 3.4", "foreign pane binding"]]) {
    const f = server(version);
    f.keys.set("MouseDown1Pane", value!);
    const result = await enableTmuxMouse(topology, env, f.run);
    assert.deepEqual(result.warnings, ["MouseDown1Pane"]);
    assert.equal(f.keys.get("MouseDown1Pane"), value);
    await result.dispose();
  }
});

test("pane marker cleanup restores prior ownership but never removes a later owner", async () => {
  const f = server(); f.options.set("@pi-copy-links-input", "outer-owner");
  const first = await enableTmuxMouse(topology, env, f.run); await first.dispose();
  assert.equal(f.options.get("@pi-copy-links-input"), "outer-owner");
  const second = await enableTmuxMouse(topology, env, f.run);
  f.options.set("@pi-copy-links-input", "later-owner"); await second.dispose();
  assert.equal(f.options.get("@pi-copy-links-input"), "later-owner");
});

test("direct topology ignores even stale TMUX env; command failures are not missing keys", async () => {
  const f = server();
  assert.equal((await enableTmuxMouse(direct, env, f.run)).enabled, false);
  assert.equal(f.calls.length, 0, "direct topology must not inspect or claim tmux bindings/pane ownership");
  await assert.rejects(() => enableTmuxMouse(topology, env,
    async () => ({ code: 2, stdout: "", stderr: "server unavailable" })), /server unavailable/);
});
