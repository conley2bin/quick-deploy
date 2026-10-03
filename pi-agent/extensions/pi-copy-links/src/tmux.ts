import { execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import { promisify } from "node:util";
import type { InputTopology } from "./receiver.ts";

const exec = promisify(execFile);
const OPTION = "@pi-copy-links-input";
const CONDITION = `#{&&:#{${OPTION}},#{mouse_any_flag}}`;
const STOCK_DOWN = "bind-key -T root MouseDown1Pane select-pane -t = \\; send-keys -M";
const STOCK_TAGGED = String.raw`bind-key -T root MouseDown1Pane select-pane -t = \; if-shell -F -t = "#{&&:#{@pi-copy-links-input},#{mouse_any_flag}}" "run-shell -C 'send-keys -l -t \"#{pane_id}\" \"\\e[998;#{client_pid}~\" ; send-keys -M'" "send-keys -M"`;
const TAGGED_SEND = String.raw`run-shell -C 'send-keys -l -t "#{pane_id}" "\e[998;#{client_pid}~" ; send-keys -M'`;
const taggedBinding = (key: string) => key === "MouseDown1Pane" ? STOCK_TAGGED :
  String.raw`bind-key -T root ${key} if-shell -F -t = "#{&&:#{@pi-copy-links-input},#{mouse_any_flag}}" "run-shell -C 'send-keys -l -t \"#{pane_id}\" \"\\e[998;#{client_pid}~\" ; send-keys -M'" ` +
    (key === "MouseUp1Pane" ? '"send-keys -M"' : "''");
export const MOUSE_KEYS = [
  "MouseDown1Pane", "MouseUp1Pane",
  "C-MouseDown1Pane", "C-MouseUp1Pane", "C-MouseDrag1Pane", "C-MouseDragEnd1Pane",
  // Tmux replaces fast later presses with second/double/triple click key classes.
  "C-SecondClick1Pane", "C-DoubleClick1Pane", "C-TripleClick1Pane", "SecondClick1Pane",
];
type Result = { code: number; stdout: string; stderr: string };
export type TmuxRun = (args: string[]) => Promise<Result>;
const runTmux: TmuxRun = async (args) => {
  try {
    const result = await exec("tmux", args, { encoding: "utf8", timeout: 2500 });
    return { code: 0, stdout: result.stdout, stderr: result.stderr };
  } catch (error) {
    const failure = error as { code?: string | number; stdout?: string; stderr?: string };
    if (typeof failure.code !== "number") throw error;
    return { code: failure.code, stdout: failure.stdout ?? "", stderr: failure.stderr ?? "" };
  }
};

/** Send the event-context client PID before tmux's original mouse event. Only
 * the exact tmux 3.4 stock down binding may be adopted: its select-pane runs
 * first in both branches, and an unowned pane still runs stock send-keys -M.
 * Older extension bindings are upgraded only with their exact stored receipt.
 * Shared guarded keys remain inert after the last pane owner unloads.
 */
export async function enableTmuxMouse(
  topology: InputTopology, env: NodeJS.ProcessEnv = process.env, run: TmuxRun = runTmux,
) {
  if (topology.kind !== "tmux") {
    return { enabled: false, warnings: [] as string[], dispose: async () => {} };
  }
  const socket = /^(.*),\d+,\d+$/u.exec(env.TMUX ?? "")?.[1];
  const pane = env.TMUX_PANE;
  if (!socket || !pane || !/^%\d+$/u.test(pane) ||
      socket !== topology.socket || pane !== topology.pane) {
    return { enabled: false, warnings: [] as string[], dispose: async () => {} };
  }
  const call = (args: string[]) => run(["-S", socket, ...args]);
  const checked = async (args: string[]) => {
    const result = await call(args);
    if (result.code !== 0) throw new Error(`tmux ${args[0]}: ${result.stderr.trim() || result.code}`);
    return result.stdout.trim();
  };
  const warnings: string[] = [];
  for (const key of MOUSE_KEYS) {
    const current = await call(["list-keys", "-T", "root", key]);
    const receiptKey = `@pi-copy-links-binding-${key}`;
    const saved = await checked(["show-options", "-sqv", receiptKey]);
    const oldBinding = `bind-key -T root ${key} if-shell -F -t = "${CONDITION}" "send-keys -M" ''`;
    const stockDown = key === "MouseDown1Pane" && current.code === 0 && current.stdout.trim() === STOCK_DOWN;
    const priorKey = "@pi-copy-links-prior-MouseDown1Pane";
    if (stockDown) {
      const version = await checked(["-V"]);
      if (version !== "tmux 3.4") { warnings.push(key); continue; }
      const prior = await checked(["show-options", "-sqv", priorKey]);
      if (prior && prior !== STOCK_DOWN) { warnings.push(key); continue; }
      await checked(["set-option", "-s", priorKey, STOCK_DOWN]);
    } else if (current.code === 0 && current.stdout.trim()) {
      // Existing new bindings require their receipt; an upgrade additionally
      // requires the *known* old extension command, not an arbitrary receipt.
      if (!saved || current.stdout.trim() !== saved) { warnings.push(key); continue; }
      if (saved === taggedBinding(key)) {
        if (key !== "MouseDown1Pane" ||
            await checked(["show-options", "-sqv", priorKey]) === STOCK_DOWN) continue;
        warnings.push(key);
        continue;
      }
      if (saved !== oldBinding || key === "MouseUp1Pane") { warnings.push(key); continue; }
    } else if (current.code !== 1) {
      // Only tmux's documented unknown-key exit 1 means a missing binding.
      throw new Error(`tmux list-keys: ${current.stderr.trim()}`);
    }
    if (key === "MouseDown1Pane" && !stockDown) {
      // A missing/default-changed down key cannot preserve pane selection.
      warnings.push(key);
      continue;
    }
    const commands = key === "MouseDown1Pane" ?
      ["select-pane", "-t", "=", "\\;", "if-shell", "-F", "-t", "=", CONDITION, TAGGED_SEND, "send-keys -M"] :
      ["if-shell", "-F", "-t", "=", CONDITION, TAGGED_SEND, key === "MouseUp1Pane" ? "send-keys -M" : ""];
    await checked(["bind-key", "-T", "root", key, ...commands]);
    const canonical = await checked(["list-keys", "-T", "root", key]);
    if (canonical !== taggedBinding(key)) {
      throw new Error(`tmux ${key}: installed mouse binding differs from the verified command`);
    }
    await checked(["set-option", "-s", receiptKey, canonical]);
  }
  const previous = await checked(["show-options", "-pqv", "-t", pane, OPTION]);
  const owner = `${process.pid}-${randomBytes(8).toString("hex")}`;
  await checked(["set-option", "-p", "-t", pane, OPTION, owner]);
  return {
    enabled: true,
    warnings,
    async dispose() {
      const current = await call(["show-options", "-pqv", "-t", pane, OPTION]);
      if (current.code !== 0 || current.stdout.trim() !== owner) return;
      await checked(previous ? ["set-option", "-p", "-t", pane, OPTION, previous] :
        ["set-option", "-pu", "-t", pane, OPTION]);
    },
  };
}
