import { spawnSync } from "node:child_process";

export type RenderMode = "kitty-placeholder" | "image" | "text";
export type Environment = Record<string, string | undefined>;
export type TmuxProbe = (env: Environment) => boolean;

export function isTmux(env: Environment): boolean {
	return Boolean(env.TMUX || env.TERM?.startsWith("tmux"));
}

const PASSTHROUGH_ENABLED = /^(on|all|yes|true|1)$/iu;
const PANE_ID = /^%\d+$/u;

/**
 * Fail closed unless the originating pane's effective policy enables passthrough.
 * `all` is accepted exactly like `on` (Kitty graphics are visible-pane traffic, so
 * both permit the pane this process actually runs in). Probed once per runtime.
 */
export type TmuxCommandResult = { status: number | null; stdout?: string | null };
export type TmuxCommand = (command: string, args: string[], options: { encoding: "utf8"; timeout: number }) => TmuxCommandResult;

/** Query the originating pane's effective policy; injectable runner keeps tests hermetic. */
export function probeTmuxPassthrough(env: Environment, run: TmuxCommand = spawnSync as unknown as TmuxCommand): boolean {
	const pane = env.TMUX_PANE?.trim();
	if (!pane || !PANE_ID.test(pane)) return false;
	try {
		const result = run("tmux", ["show-options", "-Apv", "-t", pane, "allow-passthrough"], {
			encoding: "utf8",
			timeout: 1_000,
		});
		return result.status === 0 && PASSTHROUGH_ENABLED.test(result.stdout?.trim() ?? "");
	} catch {
		return false;
	}
}

export function outerKittyCapable(env: Environment): boolean {
	const program = env.TERM_PROGRAM?.toLowerCase();
	const emulator = env.TERMINAL_EMULATOR?.toLowerCase();
	return Boolean(
		env.KITTY_WINDOW_ID ||
			env.GHOSTTY_RESOURCES_DIR ||
			env.WEZTERM_PANE ||
			program === "kitty" ||
			program === "ghostty" ||
			program === "wezterm" ||
			emulator === "ghostty" ||
			emulator === "wezterm",
	);
}

export function renderMode(
	env: Environment,
	imageProtocol: "kitty" | "iterm2" | null | undefined,
	tmuxProbe: TmuxProbe = probeTmuxPassthrough,
): RenderMode {
	let passthrough = false;
	if (isTmux(env) && outerKittyCapable(env)) {
		try {
			passthrough = tmuxProbe(env);
		} catch {
			// Capability probe errors must leave a readable text fallback.
		}
	}
	if (passthrough) return "kitty-placeholder";
	if (!isTmux(env) && imageProtocol) return "image";
	return "text";
}
