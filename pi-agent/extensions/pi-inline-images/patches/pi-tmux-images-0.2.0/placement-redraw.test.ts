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

test("same-geometry redraw re-emits only the owned placement", async () => {
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
	await runtime.add("middle", "logical-id-0000002");
	runtime.emitPlaceholder("logical-id-0000001", 20);
	runtime.emitPlaceholder("logical-id-0000002", 20);
	const initialMiddlePlacement = writes.findLast((sequence) => /a=p,i=101,/.test(sequence));
	assert.ok(initialMiddlePlacement);
	writes.length = 0;

	runtime.emitPlaceholder("logical-id-0000002", 20);
	assert.equal(writes.length, 1, "a cached same-geometry redraw must re-emit one command");
	assert.equal(writes[0], initialMiddlePlacement);
	assert.doesNotMatch(writes[0] ?? "", /a=t|a=d/);
	assert.doesNotMatch(writes[0] ?? "", /i=100/, "redrawing the middle image must not touch the older image ID");
});

test("resize still deletes the old placement before creating and later refreshing the new one", async () => {
	const writes: string[] = [];
	const runtime = new PreviewRuntime({
		env: { TMUX: "yes", TERM_PROGRAM: "ghostty" },
		tmuxProbe: () => true,
		imageProtocol: null,
		output: { write: (sequence: string) => writes.push(sequence) },
		loader: loader as never,
		allocateImageId: () => 200,
	});

	await runtime.add("image", "logical-id-0000003");
	runtime.emitPlaceholder("logical-id-0000003", 20);
	writes.length = 0;

	runtime.emitPlaceholder("logical-id-0000003", 10);
	assert.equal(writes.length, 2);
	assert.match(writes[0] ?? "", /a=d,d=i,i=200,p=200,q=2/);
	assert.match(writes[1] ?? "", /a=p,i=200,p=200,U=1,c=8,r=\d+,q=2/);
	const resizedPlacement = writes[1];
	writes.length = 0;

	runtime.emitPlaceholder("logical-id-0000003", 10);
	assert.equal(writes.length, 1);
	assert.equal(writes[0], resizedPlacement);
	assert.doesNotMatch(writes[0] ?? "", /a=t|a=d/);
});
