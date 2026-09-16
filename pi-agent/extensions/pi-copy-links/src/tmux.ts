import { execFile } from "node:child_process";
import { randomBytes } from "node:crypto";
import { promisify } from "node:util";

const exec = promisify(execFile);
const OPTION = "@pi-copy-links-input";
const CONDITION = `#{&&:#{${OPTION}},#{mouse_any_flag}}`;
export const MOUSE_KEYS = [
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

/** Tmux's default root table drops Ctrl+left presses but forwards releases.
 * Add only missing bindings, conditional on this extension owning the target pane.
 * Guarded bindings stay server-local and inert after unload. Removing shared keys
 * on the last apparent owner would race another Pi starting on the same server.
 */
export async function enableTmuxMouse(env: NodeJS.ProcessEnv = process.env, run: TmuxRun = runTmux) {
  const socket = /^(.*),\d+,\d+$/u.exec(env.TMUX ?? "")?.[1];
  const pane = env.TMUX_PANE;
  if (!socket || !pane || !/^%\d+$/u.test(pane)) return { enabled: false, warnings: [] as string[], dispose: async () => {} };
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
    if (current.code === 0 && current.stdout.trim()) {
      if (!saved || current.stdout.trim() !== saved) warnings.push(key);
      continue; // Never overwrite an existing key, including another owner's customization.
    }
    // A missing key is tmux's documented list-keys exit 1. Other errors are not absence.
    if (current.code !== 1) throw new Error(`tmux list-keys: ${current.stderr.trim()}`);
    await checked(["bind-key", "-T", "root", key, "if-shell", "-F", "-t", "=", CONDITION, "send-keys -M", ""]);
    const canonical = await checked(["list-keys", "-T", "root", key]);
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
