import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { join, resolve } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const packageRoot = process.env.PI_TMUX_IMAGES_ROOT;
assert.ok(packageRoot, "PI_TMUX_IMAGES_ROOT must point to a pi-tmux-images package root");
const replay = resolve("patches/pi-tmux-images-0.2.0/replay-stage-c.sh");
const state = execFileSync(replay, ["check", packageRoot], { encoding: "utf8" }).trim();
const { PreviewRuntime } = await import(pathToFileURL(join(packageRoot, "src/runtime.ts")).href);

const hash = "a".repeat(64);
const loader = async (path: string) => ({
	path,
	hash,
	originalMime: "image/png" as const,
	width: 1600,
	height: 800,
	png: Buffer.from("png"),
});

function command(sequence: string): string {
	const match = sequence.match(/a=[tpd],[^;]*/u);
	assert.ok(match, `expected Kitty command in ${JSON.stringify(sequence)}`);
	return match[0];
}

if (state === "pristine") {
	test("capability-only runtime places only initially and on geometry changes", async () => {
		const writes: string[] = [];
		let next = 100;
		const runtime = new PreviewRuntime({
			env: { TMUX: "yes", TERM_PROGRAM: "ghostty" },
			tmuxProbe: () => true,
			imageProtocol: null,
			output: { write: (sequence: string) => writes.push(sequence) },
			loader: loader as never,
			allocateImageId: () => next++,
		});

		await runtime.add("first", "logical-id-0000001");
		await runtime.add("second", "logical-id-0000002");
		const firstGrid = runtime.emitPlaceholder("logical-id-0000001", 20);
		const secondGrid = runtime.emitPlaceholder("logical-id-0000002", 20);
		assert.ok(firstGrid.length > 0);
		assert.ok(secondGrid.length > 0);
		assert.deepEqual(writes.map(command), [
			"a=t,f=100,i=100,q=2,m=0",
			`a=p,i=100,p=100,U=1,c=18,r=${firstGrid.length},q=2`,
			"a=t,f=100,i=101,q=2,m=0",
			`a=p,i=101,p=101,U=1,c=18,r=${secondGrid.length},q=2`,
		]);

		writes.length = 0;
		for (let render = 0; render < 1_000; render++) {
			runtime.emitPlaceholder("logical-id-0000001", 20);
			runtime.emitPlaceholder("logical-id-0000002", 20);
		}
		assert.deepEqual(writes, [], "unchanged renders must not bypass the TUI diff with direct writes");

		const resizedGrid = runtime.emitPlaceholder("logical-id-0000002", 10);
		assert.deepEqual(writes.map(command), [
			"a=d,d=i,i=101,p=101,q=2",
			`a=p,i=101,p=101,U=1,c=8,r=${resizedGrid.length},q=2`,
		]);
		assert.ok(writes.every((sequence) => !sequence.includes("i=100")), "resizing one image must preserve other IDs");
		writes.length = 0;
		for (let render = 0; render < 1_000; render++) runtime.emitPlaceholder("logical-id-0000002", 10);
		assert.deepEqual(writes, [], "stable geometry after resize must remain silent");
		runtime.clear();
		assert.deepEqual(writes.map(command), ["a=d,d=I,i=100,q=2", "a=d,d=I,i=101,q=2"]);
		assert.deepEqual(runtime.activeIds(), []);
	});
} else {
	test("deployed Stage C runtime delegates every graphic to its shared handle", async () => {
		const runtime = new PreviewRuntime({ loader: loader as never });
		const calls: string[] = [];
		await runtime.add("first", "logical-id-0000001");
		assert.deepEqual(calls, [], "loading before bridge bind emits no independent graphics");
		await runtime.setShared({
			prepare: async (logicalId: string) => { calls.push(`prepare:${logicalId}`); },
			render: (logicalId: string, width: number) => [`grid:${logicalId}:${width}`],
			failure: () => undefined,
			release: async (logicalId: string) => { calls.push(`release:${logicalId}`); },
			reset: async () => { calls.push("reset"); },
		});
		assert.deepEqual(calls, ["prepare:logical-id-0000001"]);
		assert.deepEqual(runtime.emitPlaceholder("logical-id-0000001", 20), ["grid:logical-id-0000001:20"]);
		await runtime.clear();
		assert.deepEqual(calls, ["prepare:logical-id-0000001", "reset"]);
	});
}
