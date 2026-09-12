import assert from "node:assert/strict";
import { join } from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

const packageRoot = process.env.PI_TMUX_IMAGES_ROOT;
assert.ok(packageRoot, "PI_TMUX_IMAGES_ROOT must point to a pi-tmux-images package root");
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

test("captured output places only initially and on geometry changes", async () => {
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
