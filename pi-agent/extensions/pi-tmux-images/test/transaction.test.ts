import { test } from "node:test";
import assert from "node:assert/strict";
import {
	coalescedDeleteImages,
	fileTransferCommands,
	grid,
	MAX_TRANSACTION_BASE64,
	transaction,
	upload,
} from "../src/kitty-placeholder.ts";

const IMAGE_ID = 0x1234abcd;

function indexOf(haystack: string, needle: string): number {
	const index = haystack.indexOf(needle);
	assert.notEqual(index, -1, `expected to find ${needle}`);
	return index;
}

test("a transaction is one framed string with teardown, upload and placement in order", () => {
	const payload = Buffer.alloc(4096 * 3 + 16).toString("base64");
	const bytes = transaction({
		imageId: IMAGE_ID,
		base64: payload,
		columns: 40,
		rows: 10,
		inTmux: false,
		replacePlacement: true,
		upload: true,
		purgeImage: true,
	});
	assert.equal(bytes.split("\x1b[?2026h").length - 1, 1);
	assert.equal(bytes.split("\x1b[?2026l").length - 1, 1);
	assert.equal(bytes.endsWith("\x1b[?2026l"), true);
	const purge = indexOf(bytes, `a=d,d=I,i=${IMAGE_ID}`);
	const replace = indexOf(bytes, `a=d,d=i,i=${IMAGE_ID}`);
	const firstUpload = indexOf(bytes, `a=t,f=100,i=${IMAGE_ID}`);
	const place = indexOf(bytes, `a=p,i=${IMAGE_ID},p=`);
	assert.ok(purge < replace, "image purge must precede placement replacement");
	assert.ok(replace < firstUpload, "teardown must precede the upload chunks");
	assert.ok(firstUpload < place, "placement must follow the upload");
	assert.equal(bytes.split("a=p,i=").length - 1, 1, "exactly one placement per transaction");
});

test("uploads are chunked at 4096 base64 characters with m=1 continuations and a final m=0", () => {
	const payload = Buffer.alloc(4096 * 3 + 16).toString("base64");
	const chunks = upload(payload, IMAGE_ID, false);
	assert.equal(chunks.length, Math.ceil(payload.length / 4096));
	assert.match(chunks[0]!, new RegExp("^\\x1b_Ga=t,f=100,i=" + IMAGE_ID + ",q=2,m=1;", "u"));
	assert.ok(chunks.slice(1, -1).every((chunk) => chunk.startsWith("\x1b_Gm=1;")), "middle chunks continue the transfer");
	assert.ok(chunks.at(-1)!.startsWith("\x1b_Gm=0;"), "the final chunk terminates the transfer");
	assert.ok(chunks.every((chunk) => chunk.endsWith("\x1b\\")));
});

test("geometry-only replacement keeps the uploaded image data", () => {
	const bytes = transaction({
		imageId: IMAGE_ID,
		base64: "",
		columns: 20,
		rows: 5,
		inTmux: false,
		replacePlacement: true,
		upload: false,
		purgeImage: false,
	});
	assert.ok(bytes.includes(`a=d,d=i,i=${IMAGE_ID}`), "old placement must be removed");
	assert.ok(!bytes.includes("a=d,d=I"), "image data must not be purged on a resize");
	assert.ok(!bytes.includes("a=t"), "a resize must not re-upload the payload");
	assert.equal(bytes.split("a=p,i=").length - 1, 1);
});

test("tmux mode escapes every payload byte and wraps the transaction in DCS passthrough", async () => {
	const bytes = transaction({
		imageId: IMAGE_ID,
		base64: "AAAA",
		columns: 4,
		rows: 2,
		inTmux: true,
		replacePlacement: false,
		upload: true,
		purgeImage: false,
	});
	const { tmux } = await import("../src/kitty-placeholder.ts");
	assert.ok(bytes.startsWith(tmux("\x1b[?2026h")), "tmux transactions must passthrough-frame the sync start");
	assert.ok(bytes.endsWith(tmux("\x1b[?2026l")), "tmux transactions must passthrough-frame the sync end");
	assert.ok(bytes.includes("\x1b\x1b"), "ESC inside passthrough payloads must be doubled");
	// Every sync marker must be doubled inside the DCS payload, never raw.
	assert.equal(bytes.split("\x1b[?2026h").length - 1, bytes.split("\x1b\x1b[?2026h").length - 1);
	assert.equal(bytes.split("\x1b[?2026l").length - 1, bytes.split("\x1b\x1b[?2026l").length - 1);
});

test("regular-file transfer sends one short command instead of every chunk", () => {
	const bytes = fileTransferCommands({
		imageId: IMAGE_ID,
		filePath: "/run/user/1000/pi-tmux-images/abc.png",
		columns: 30,
		rows: 8,
		inTmux: false,
	});
	assert.equal(bytes.split("a=t,").length - 1, 1, "file transfer must not chunk");
	assert.ok(bytes.includes("t=f"), "file transfer must declare the regular-file medium");
	assert.ok(bytes.includes(Buffer.from("/run/user/1000/pi-tmux-images/abc.png", "utf8").toString("base64")));
	assert.ok(!bytes.includes("m=1;"), "file transfer has no continuation chunks");
	assert.ok(bytes.includes(`a=p,i=${IMAGE_ID}`), "file transfer still needs a placement");
});

test("teardown is coalesced into a single string", () => {
	assert.equal(coalescedDeleteImages([], false), "");
	const bytes = coalescedDeleteImages([1, 2, 3], false);
	assert.equal(bytes.split("a=d,d=I,i=").length - 1, 3);
	assert.equal(bytes.split("\x1b[?2026h").length - 1, 1);
	assert.equal(bytes.split("\x1b[?2026l").length - 1, 1);
});

test("payload ceiling constant is explicit and grid rows stay bounded", () => {
	assert.equal(MAX_TRANSACTION_BASE64, 256 * 1024);
	const rows = grid(3, 2, IMAGE_ID);
	assert.equal(rows.length, 2);
	assert.ok(rows.every((row) => row.includes("\u{10eeee}")));
});

test("geometry clamps hostile inputs instead of producing NaN cells", async () => {
	const { geometry } = await import("../src/kitty-placeholder.ts");
	const size = geometry(640, 480, { maxColumns: 80, cell: { widthPx: 10, heightPx: 20 } });
	assert.deepEqual(size, { columns: 64, rows: 24 });
	assert.deepEqual(geometry(640, 480, { cell: { widthPx: Number.NaN, heightPx: 0 } }), geometry(640, 480));
	assert.deepEqual(geometry(0, 0, {}), { columns: 1, rows: 1 });
	assert.deepEqual(geometry(Number.NaN, 480, {}), { columns: 1, rows: 1 });
	assert.deepEqual(geometry(640, 480, { maxColumns: Number.NaN, maxRows: -5 }), geometry(640, 480));
	for (const rows of [geometry(1, 1, {}).rows, geometry(50_000, 50_000, { maxColumns: 80 }).rows]) {
		assert.ok(rows >= 1 && Number.isFinite(rows));
	}
});

test("grid rows never collapse for a valid geometry", async () => {
	const { geometry, grid } = await import("../src/kitty-placeholder.ts");
	const size = geometry(640, 480, { maxColumns: 78, cell: { widthPx: 10, heightPx: 20 } });
	const rows = grid(size.columns, size.rows, 42);
	assert.equal(rows.length, size.rows);
	assert.ok(rows.length > 0);
});
