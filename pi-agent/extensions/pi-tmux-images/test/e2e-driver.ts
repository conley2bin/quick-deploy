import { PreviewRuntime } from "../src/runtime.ts";
import type { LoadedImage } from "../src/loader.ts";

const PNG = Buffer.from("89504e470d0a1a0a0000000d4948445200000040000000200806000000", "hex");
const fixture = (): LoadedImage => ({
	path: "/e2e-fixture.png",
	hash: "e2e-hash",
	originalMime: "image/png",
	width: 64,
	height: 32,
	png: PNG,
});

let writes = 0;
const sink = {
	write(value: string) {
		writes += 1;
		process.stdout.write(value);
		return true;
	},
};

const runtime = new PreviewRuntime({
	output: sink,
	loader: async () => fixture(),
	byteLoader: async () => fixture(),
});
await runtime.add("/e2e-fixture.png", "e2e");
const rows = runtime.emitPlaceholder("e2e", Number(process.env.E2E_WIDTH ?? 80));
// The TUI frame writer owns cells; emulate that here so the pane grid shows them.
if (rows.length > 0) process.stdout.write("\r\n" + rows.join("\r\n"));
process.stdout.write(
	`\r\nE2E_RESULT mode=${runtime.mode()} writes=${writes} rows=${rows.length} withheld=${runtime.withheldForTest() ?? "-"} notice=${runtime.notice("e2e") ?? "-"}\r\n`,
);
