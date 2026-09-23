import { test } from "node:test";
import assert from "node:assert/strict";
import { isTmux, probeTmuxPassthrough, renderMode, type TmuxCommandResult } from "../src/capabilities.ts";

const GHOSTTY_TMUX = {
	TMUX: "/tmp/tmux-1000/default,1234,0",
	TERM: "tmux-256color",
	TMUX_PANE: "%7",
	TERM_PROGRAM: "ghostty",
	GHOSTTY_RESOURCES_DIR: "/usr/share/ghostty",
};

function runner(result: TmuxCommandResult, seen?: string[][]) {
	return (command: string, args: string[], _options: { encoding: "utf8"; timeout: number }) => {
		seen?.push([command, ...args]);
		return result;
	};
}

test("passthrough probe accepts every documented affirmative value", () => {
	for (const value of ["on", "all", "yes", "true", "1", "ON", " All "]) {
		assert.equal(
			probeTmuxPassthrough(GHOSTTY_TMUX, runner({ status: 0, stdout: value })),
			true,
			`expected ${JSON.stringify(value)} to be accepted`,
		);
	}
});

test("passthrough probe rejects disabled or unknown values", () => {
	for (const value of ["off", "", "  ", "no", "0", "maybe", "on off"]) {
		assert.equal(
			probeTmuxPassthrough(GHOSTTY_TMUX, runner({ status: 0, stdout: value })),
			false,
			`expected ${JSON.stringify(value)} to be rejected`,
		);
	}
});

test("passthrough probe queries the originating pane's effective option", () => {
	const seen: string[][] = [];
	probeTmuxPassthrough(GHOSTTY_TMUX, runner({ status: 0, stdout: "on" }, seen));
	assert.deepEqual(seen, [["tmux", "show-options", "-Apv", "-t", "%7", "allow-passthrough"]]);
});

test("passthrough probe fails closed without a usable pane or on command failure", () => {
	assert.equal(probeTmuxPassthrough({ ...GHOSTTY_TMUX, TMUX_PANE: undefined }, runner({ status: 0, stdout: "on" })), false);
	assert.equal(probeTmuxPassthrough({ ...GHOSTTY_TMUX, TMUX_PANE: "7" }, runner({ status: 0, stdout: "on" })), false);
	assert.equal(probeTmuxPassthrough({ ...GHOSTTY_TMUX, TMUX_PANE: "%" }, runner({ status: 0, stdout: "on" })), false);
	assert.equal(probeTmuxPassthrough(GHOSTTY_TMUX, runner({ status: 1, stdout: "on" })), false);
	assert.equal(probeTmuxPassthrough(GHOSTTY_TMUX, runner({ status: null, stdout: null })), false);
	assert.equal(
		probeTmuxPassthrough(GHOSTTY_TMUX, () => {
			throw new Error("tmux missing");
		}),
		false,
	);
});

test("renderMode selects kitty placeholders for tmux plus Ghostty plus on|all", () => {
	const probe = (value: string) => (env: typeof GHOSTTY_TMUX) =>
		probeTmuxPassthrough(env, runner({ status: 0, stdout: value }));
	assert.equal(renderMode(GHOSTTY_TMUX, "kitty", probe("on")), "kitty-placeholder");
	assert.equal(renderMode(GHOSTTY_TMUX, "kitty", probe("all")), "kitty-placeholder");
});

test("renderMode degrades to text when the pane policy is disabled", () => {
	assert.equal(
		renderMode(GHOSTTY_TMUX, "kitty", (env) => probeTmuxPassthrough(env, runner({ status: 0, stdout: "off" }))),
		"text",
	);
});

test("renderMode does not probe tmux when the outer terminal is not kitty capable", () => {
	let probed = false;
	const mode = renderMode({ TMUX: "/tmp/x", TERM: "tmux-256color", TMUX_PANE: "%1" }, "kitty", () => {
		probed = true;
		return true;
	});
	assert.equal(mode, "text");
	assert.equal(probed, false, "probe must not run for terminals that cannot render kitty graphics");
});

test("renderMode uses the native image protocol only outside tmux", () => {
	assert.equal(isTmux({ TERM: "xterm-ghostty" }), false);
	assert.equal(renderMode({ TERM: "xterm-ghostty", GHOSTTY_RESOURCES_DIR: "/usr/share/ghostty" }, "iterm2"), "image");
	assert.equal(renderMode({ TERM: "xterm-ghostty" }, null), "text");
});
