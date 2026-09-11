import assert from "node:assert/strict";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const packageRoot = process.env.PI_TMUX_IMAGES_ROOT;
assert.ok(packageRoot, "PI_TMUX_IMAGES_ROOT must point to a pi-tmux-images package root");
const { probeTmuxPassthrough } = await import(pathToFileURL(join(packageRoot, "src/capabilities.ts")).href);

const env = { TMUX: "socket", TMUX_PANE: "%42" };

test("probe accepts effective on/all from the originating pane", () => {
	const calls: string[][] = [];
	const result = (stdout: string | null, status = 0) =>
		probeTmuxPassthrough(env, (_command: string, args: string[]) => {
			calls.push(args);
			return { status, stdout };
		});

	for (const enabled of ["on", "all", "yes", "true", "1"]) assert.equal(result(enabled), true, enabled);
	assert.deepEqual(calls[0], ["show-options", "-Apv", "-t", "%42", "allow-passthrough"]);
});

test("probe rejects disabled, unknown, failed, and unaddressable policies", () => {
	const result = (stdout: string | null, status: number | null = 0) =>
		probeTmuxPassthrough(env, () => ({ status, stdout }));

	for (const disabled of ["off", "unknown", "", null]) assert.equal(result(disabled), false, String(disabled));
	assert.equal(result("all", 1), false);
	assert.equal(result("all", null), false);

	let invoked = false;
	const run = () => {
		invoked = true;
		return { status: 0, stdout: "all" };
	};
	assert.equal(probeTmuxPassthrough({ TMUX: "socket" }, run), false);
	assert.equal(probeTmuxPassthrough({ TMUX: "socket", TMUX_PANE: "not-a-pane" }, run), false);
	assert.equal(invoked, false, "missing or invalid pane identity must not fall back to global policy");
	assert.equal(
		probeTmuxPassthrough(env, () => {
			throw new Error("tmux unavailable");
		}),
		false,
	);
});
