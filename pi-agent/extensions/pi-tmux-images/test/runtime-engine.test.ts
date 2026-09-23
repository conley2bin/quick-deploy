import { test } from "node:test";
import assert from "node:assert/strict";
import { PreviewRuntime, type HostBindings } from "../src/runtime.ts";
import type { LoadedImage } from "../src/loader.ts";
import type { TmuxCommandResult } from "../src/capabilities.ts";

const PNG_A = Buffer.from("89504e470d0a1a0a0000000d49484452", "hex");
const PNG_B = Buffer.from("89504e470d0a1a0a0000000d4948445200", "hex");

const GHOSTTY_TMUX = {
	TMUX: "/tmp/tmux-1000/default,1234,0",
	TERM: "tmux-256color",
	TMUX_PANE: "%7",
	TERM_PROGRAM: "ghostty",
	GHOSTTY_RESOURCES_DIR: "/usr/share/ghostty",
};

function host(): HostBindings {
	let next = 0x40000000;
	return {
		allocateImageId: () => (next += 1),
		cellDimensions: () => ({ widthPx: 10, heightPx: 20 }),
		imageProtocol: () => "kitty",
	};
}

type Recorder = {
	writes: string[];
	throwOnWrite: boolean;
	returnValue: boolean | undefined;
	write(value: string): boolean | undefined;
};

function recorder(): Recorder {
	return {
		writes: [],
		throwOnWrite: false,
		returnValue: undefined,
		write(value: string) {
			if (this.throwOnWrite) throw new Error("sink closed");
			this.writes.push(value);
			return this.returnValue;
		},
	};
}

function image(png: Buffer, width = 640, height = 480): LoadedImage {
	return { path: "/fixture.png", hash: `hash-${png.length}-${width}`, originalMime: "image/png", width, height, png };
}

function makeRuntime(options: {
	sink?: Recorder;
	payload?: Buffer;
	maxTransactionBase64?: number;
	minIntervalMs?: number;
	probeResult?: TmuxCommandResult;
	fileTransfer?: boolean;
} = {}) {
	const sink = options.sink ?? recorder();
	const payload = options.payload ?? PNG_A;
	const runtime = new PreviewRuntime({
		env: GHOSTTY_TMUX,
		host: host(),
		output: sink,
		loader: async () => image(payload),
		byteLoader: async () => image(payload),
		tmuxProbe: () => options.probeResult === undefined || options.probeResult.status === 0,
		maxTransactionBase64: options.maxTransactionBase64,
		minIntervalMs: options.minIntervalMs,
		fileTransfer: options.fileTransfer,
	});
	return { runtime, sink };
}

test("one complete transaction per write and cached renders write nothing", async () => {
	const { runtime, sink } = makeRuntime();
	await runtime.add("/fixture.png", "preview-1");
	const first = runtime.emitPlaceholder("preview-1", 100);
	assert.equal(sink.writes.length, 1, "upload plus placement must be a single write");
	assert.ok(sink.writes[0]!.includes("a=t,f=100"));
	assert.ok(sink.writes[0]!.includes("a=p,i="));
	assert.ok(sink.writes[0]!.indexOf("a=t,") < sink.writes[0]!.indexOf("a=p,i="));
	assert.ok(first.length > 0);
	const second = runtime.emitPlaceholder("preview-1", 100);
	assert.equal(sink.writes.length, 1, "an unchanged render must not write again");
	assert.deepEqual(second, first);
});

test("cells are withheld until the upload write was accepted", async () => {
	const sink = recorder();
	const { runtime } = makeRuntime({ sink });
	await runtime.add("/fixture.png", "preview-1");
	sink.throwOnWrite = true;
	assert.deepEqual(runtime.emitPlaceholder("preview-1", 100), [], "a failed write must not produce placeholder cells");
	sink.throwOnWrite = false;
	const rows = runtime.emitPlaceholder("preview-1", 100);
	assert.ok(rows.length > 0, "the next render must retry and then paint cells");
	assert.equal(sink.writes.length, 1);
});

test("a queued write (false) still counts as accepted", async () => {
	const sink = recorder();
	sink.returnValue = false;
	const { runtime } = makeRuntime({ sink });
	await runtime.add("/fixture.png", "preview-1");
	const rows = runtime.emitPlaceholder("preview-1", 100);
	assert.ok(rows.length > 0, "backpressure queues the transaction, which is still enough for cells");
	assert.equal(sink.writes.length, 1);
});

test("payload ceiling never falls back to an oversized write", async () => {
	const sink = recorder();
	const { runtime } = makeRuntime({ sink, maxTransactionBase64: 8 });
	await runtime.add("/fixture.png", "preview-1");
	assert.deepEqual(runtime.emitPlaceholder("preview-1", 100), []);
	assert.equal(sink.writes.length, 0, "no transaction may be written for an over-limit payload");
	assert.match(runtime.notice("preview-1") ?? "", /ceiling/u);
});

test("geometry-only resizes replace the placement without purging image data", async () => {
	const sink = recorder();
	const { runtime } = makeRuntime({ sink });
	await runtime.add("/fixture.png", "preview-1");
	runtime.emitPlaceholder("preview-1", 100);
	const resize = runtime.emitPlaceholder("preview-1", 40);
	assert.equal(sink.writes.length, 2);
	const bytes = sink.writes[1]!;
	assert.ok(bytes.includes("a=d,d=i"), "resize must remove the previous placement");
	assert.ok(!bytes.includes("a=d,d=I"), "resize must keep the uploaded image");
	assert.ok(!bytes.includes("a=t,"), "resize must not re-upload the payload");
	assert.ok(resize.length > 0);
});

test("repeat uploads are throttled and self-heal after the interval", async () => {
	const sink = recorder();
	let payload = PNG_A;
	const throttled = new PreviewRuntime({
		env: GHOSTTY_TMUX,
		host: host(),
		output: sink,
		loader: async () => image(payload),
		byteLoader: async () => image(payload),
		tmuxProbe: () => true,
		minIntervalMs: 60_000,
	});
	await throttled.add("/fixture.png", "preview-1");
	assert.ok(throttled.emitPlaceholder("preview-1", 100).length > 0);
	assert.equal(sink.writes.length, 1);
	assert.equal(throttled.emitPlaceholder("preview-1", 100).length, 24, "an unchanged render stays cached");
	assert.equal(sink.writes.length, 1);
	// Payload change inside the throttle window: suppressed, no write, no cells.
	payload = PNG_B;
	await throttled.add("/fixture.png", "preview-1");
	assert.deepEqual(throttled.emitPlaceholder("preview-1", 100), [], "a throttled re-upload returns no cells");
	assert.equal(sink.writes.length, 1, "a throttled re-upload writes nothing");
	// The next render after the interval self-heals with the new payload.
	payload = PNG_A;
	const permissive = new PreviewRuntime({
		env: GHOSTTY_TMUX,
		host: host(),
		output: recorder(),
		loader: async () => image(payload),
		byteLoader: async () => image(payload),
		tmuxProbe: () => true,
		minIntervalMs: 0,
	});
	const permissiveSink = (permissive as unknown as { output: Recorder }).output;
	await permissive.add("/fixture.png", "preview-2");
	permissive.emitPlaceholder("preview-2", 100);
	payload = PNG_B;
	await permissive.add("/fixture.png", "preview-2");
	assert.ok(permissive.emitPlaceholder("preview-2", 100).length > 0, "self-heal must paint once the interval allows it");
	assert.equal(permissiveSink.writes.length, 2, "the changed payload is uploaded on the next allowed render");
	assert.ok(permissiveSink.writes[1]!.includes(PNG_B.toString("base64")));
});

test("clear coalesces every owned image into exactly one write", async () => {
	const sink = recorder();
	const { runtime } = makeRuntime({ sink });
	await runtime.add("/fixture.png", "preview-1");
	await runtime.add("/fixture.png", "preview-2");
	runtime.emitPlaceholder("preview-1", 100);
	runtime.emitPlaceholder("preview-2", 100);
	assert.equal(sink.writes.length, 2);
	const before = sink.writes.length;
	await runtime.clear();
	assert.equal(sink.writes.length, before + 1, "teardown must be coalesced");
	const teardown = sink.writes[before]!;
	assert.equal(teardown.split("a=d,d=I").length - 1, 2);
	assert.deepEqual(runtime.activeIds(), []);
});

test("the tmux policy probe runs once per runtime, never on a render", async () => {
	let probes = 0;
	const runtime = new PreviewRuntime({
		env: GHOSTTY_TMUX,
		host: host(),
		output: recorder(),
		loader: async () => image(PNG_A),
		byteLoader: async () => image(PNG_A),
		tmuxProbe: () => {
			probes += 1;
			return true;
		},
	});
	await runtime.add("/fixture.png", "preview-1");
	for (let i = 0; i < 5; i += 1) runtime.emitPlaceholder("preview-1", 100);
	assert.equal(probes, 1);
	assert.equal(runtime.probeCountForTest(), 1);
	assert.equal(runtime.mode(), "kitty-placeholder");
});

test("a live host graphics owner withholds this engine", async () => {
	const sink = recorder();
	const { runtime } = makeRuntime({ sink });
	await runtime.add("/fixture.png", "preview-1");
	runtime.withholdSelfRender("Native image ownership active: read owner");
	assert.equal(runtime.mode(), "text");
	assert.deepEqual(runtime.emitPlaceholder("preview-1", 100), []);
	assert.equal(sink.writes.length, 0);
	assert.match(runtime.notice("preview-1") ?? "", /ownership/u);
});

test("passthrough=all and on both enable kitty placeholders end to end", async () => {
	for (const value of ["on", "all"]) {
		const runtime = new PreviewRuntime({
			env: GHOSTTY_TMUX,
			host: host(),
			output: recorder(),
			loader: async () => image(PNG_A),
			byteLoader: async () => image(PNG_A),
			tmuxProbe: () => /^(on|all)$/u.test(value),
		});
		await runtime.add("/fixture.png", `preview-${value}`);
		assert.equal(runtime.mode(), "kitty-placeholder", `passthrough=${value} must be supported`);
	}
});

test("file transfer mode emits a single short command with a temporary file", async () => {
	const sink = recorder();
	const runtime = new PreviewRuntime({
		env: GHOSTTY_TMUX,
		host: host(),
		output: sink,
		loader: async () => image(PNG_A),
		byteLoader: async () => image(PNG_A),
		tmuxProbe: () => true,
		fileTransfer: true,
	});
	await runtime.add("/fixture.png", "preview-1");
	const rows = runtime.emitPlaceholder("preview-1", 100);
	assert.ok(rows.length > 0);
	assert.equal(sink.writes.length, 1);
	assert.ok(sink.writes[0]!.includes("t=f"), "file transfer medium must be requested");
	assert.ok(!sink.writes[0]!.includes("m=1;"), "file transfer must not chunk the payload");
});

test("payload changes use a new payload and re-upload once", async () => {
	const sink = recorder();
	const { runtime } = makeRuntime({ sink, payload: PNG_B, minIntervalMs: 0 });
	await runtime.add("/fixture.png", "preview-1");
	runtime.emitPlaceholder("preview-1", 100);
	assert.equal(sink.writes.length, 1);
	const bytes = sink.writes[0]!;
	assert.ok(bytes.includes(PNG_B.toString("base64")), "the bounded payload must appear in the transaction");
});

test("text mode never renders, even if a caller asks directly", async () => {
	const sink = recorder();
	const runtime = new PreviewRuntime({
		env: { ...GHOSTTY_TMUX },
		host: host(),
		output: sink,
		loader: async () => image(PNG_A),
		byteLoader: async () => image(PNG_A),
		tmuxProbe: () => false,
	});
	await runtime.add("/fixture.png", "preview-1");
	assert.equal(runtime.mode(), "text");
	assert.deepEqual(runtime.emitPlaceholder("preview-1", 100), []);
	assert.equal(sink.writes.length, 0, "a disabled pane policy must never receive graphics bytes");
});
