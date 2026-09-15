import fs from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";

function recordPrompt() {
	const queueDir = process.env.MOCK_PI_QUEUE_DIR;
	if (!queueDir) throw new Error("MOCK_PI_QUEUE_DIR is required by the startup fixture");
	fs.mkdirSync(queueDir, { recursive: true, mode: 0o700 });
	fs.writeFileSync(path.join(queueDir, `call-${randomUUID()}.json`), "{}\n", { mode: 0o600 });
}

export default {
	async create(launch) {
		let listener;
		return {
			subscribe(next) {
				listener = next;
				return () => { listener = undefined; };
			},
			async prompt() {
				recordPrompt();
				listener?.({
					type: "message_end",
					message: { role: "assistant", content: [{ type: "text", text: "fixture completed" }], stopReason: "stop" },
				});
			},
			async steer() {},
			async followUp() {},
			async abort() {},
			async dispose() {},
			messages: [],
			sessionFile: launch.storage.kind === "file" ? launch.storage.sessionFile : undefined,
			sessionId: `fixture-${randomUUID()}`,
			modelId: undefined,
		};
	},
	async dispose() {},
};
