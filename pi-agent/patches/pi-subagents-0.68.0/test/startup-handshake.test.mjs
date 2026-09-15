import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const PROTOCOL = "lease-ack-confirm-proceed-v1";
const testDir = path.dirname(fileURLToPath(import.meta.url));
const fixtureFactory = path.join(testDir, "runner-child-session-factory.mjs");
const require = createRequire(import.meta.url);
const sourceArg = process.argv.indexOf("--source");
const caseArg = process.argv.indexOf("--case");
const source = sourceArg >= 0 ? path.resolve(process.argv[sourceArg + 1] ?? "") : "";
const selectedCase = caseArg >= 0 ? process.argv[caseArg + 1] : undefined;
if (!source || !fs.existsSync(path.join(source, "src/runs/background/subagent-runner.ts"))) {
	throw new Error("usage: node startup-handshake.test.mjs --source /path/to/pi-subagents [--case name]");
}

const roots = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function atomicJson(file, value) {
	fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
	const temp = `${file}.test-${process.pid}-${randomUUID()}`;
	fs.writeFileSync(temp, `${JSON.stringify(value)}\n`, { mode: 0o600 });
	fs.renameSync(temp, file);
}

// Cold Jiti runners need several seconds to compile the TypeScript graph before they can write `ready`.
async function waitFor(read, description, timeoutMs = 12_000) {
	const deadline = Date.now() + timeoutMs;
	for (;;) {
		const value = read();
		if (value !== undefined) return value;
		if (Date.now() >= deadline) throw new Error(`timed out waiting for ${description}`);
		await sleep(20);
	}
}

function readJson(file) {
	try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return undefined; }
}

function promptCount(queueDir) {
	try { return fs.readdirSync(queueDir).filter((name) => name.startsWith("call-") && name.endsWith(".json")).length; }
	catch { return 0; }
}

function stage(name, options = {}) {
	const root = options.root ?? fs.mkdtempSync(path.join(os.tmpdir(), `pi-subagents-startup-${name}-`));
	if (!options.root) roots.push(root);
	const runId = options.runId ?? `${name}-${randomUUID()}`;
	const asyncDir = path.join(root, "runs", runId);
	const queueDir = path.join(root, "queues", runId);
	const agentDir = path.join(root, "agent");
	const sessionFile = options.sessionFile ?? path.join(root, "session.jsonl");
	fs.mkdirSync(asyncDir, { recursive: true, mode: 0o700 });
	fs.mkdirSync(queueDir, { recursive: true, mode: 0o700 });
	fs.mkdirSync(agentDir, { recursive: true, mode: 0o700 });
	if (!fs.existsSync(sessionFile)) fs.writeFileSync(sessionFile, "", { mode: 0o600 });
	fs.writeFileSync(path.join(queueDir, "default-response.json"), JSON.stringify({ output: "fixture completed" }), { mode: 0o600 });
	const config = {
		id: runId,
		sessionId: `parent-${name}`,
		steps: [{
			agent: "worker",
			task: "No-provider startup fixture",
			systemPrompt: "Fixture",
			systemPromptMode: "replace",
			inheritProjectContext: false,
			inheritSkills: false,
			completionGuard: false,
			sessionFile,
		}],
		resultPath: path.join(asyncDir, "result.json"),
		cwd: root,
		placeholder: "{previous}",
		artifactConfig: { enabled: false },
		asyncDir,
		resultMode: "single",
		childSessionFactoryModule: fixtureFactory,
		revivalLease: { sessionFile, runId, sourceRunId: `source-${name}`, parentSessionId: `parent-${name}` },
		...(options.protocol === false ? {} : { revivalStartupProtocol: options.protocol ?? PROTOCOL }),
	};
	const configPath = path.join(root, `${runId}-config.json`);
	fs.writeFileSync(configPath, JSON.stringify(config), { mode: 0o600 });
	const runnerPath = path.join(source, "src/runs/background/subagent-runner.ts");
	const sourceUnderNodeModules = source.split(path.sep).some((segment) => segment.toLowerCase() === "node_modules");
	let jitiPackageJson;
	try {
		jitiPackageJson = require.resolve("jiti/package.json", { paths: [source] });
	} catch {
		// A source checkout with Node's native TypeScript support does not need Jiti.
	}
	const jitiPackage = jitiPackageJson ? JSON.parse(fs.readFileSync(jitiPackageJson, "utf8")) : undefined;
	const jitiBin = typeof jitiPackage?.bin === "string" ? jitiPackage.bin : jitiPackage?.bin?.jiti;
	const jitiCli = jitiPackageJson && jitiBin
		? path.resolve(path.dirname(jitiPackageJson), jitiBin)
		: undefined;
	if (sourceUnderNodeModules && !jitiCli) throw new Error("installed pi-subagents startup test requires upstream jiti");
	const runnerArgs = jitiCli
		? [jitiCli, runnerPath, configPath]
		: ["--experimental-strip-types", runnerPath, configPath];
	const child = spawn(process.execPath, runnerArgs, {
		cwd: source,
		stdio: ["ignore", "ignore", "pipe"],
		env: {
			...process.env,
			PI_CODING_AGENT_DIR: agentDir,
			PI_SUBAGENTS_TEMP_ROOT: path.join(root, "runtime"),
			MOCK_PI_QUEUE_DIR: queueDir,
		},
	});
	let stderr = "";
	child.stderr.setEncoding("utf8");
	child.stderr.on("data", (chunk) => { stderr = `${stderr}${chunk}`.slice(-16_384); });
	const exited = new Promise((resolve) => child.once("close", (code, signal) => resolve({ code, signal })));
	return {
		root, runId, asyncDir, queueDir, sessionFile, child, exited,
		startup: path.join(asyncDir, "runner-startup.json"),
		ack: path.join(asyncDir, "runner-startup-ack.json"),
		proceed: path.join(asyncDir, "runner-startup-proceed.json"),
		stderr: () => stderr,
	};
}

async function startupState(s, state) {
	try {
		return await waitFor(() => {
			const value = readJson(s.startup);
			return value?.state === state ? value : value?.state === "error" ? value : undefined;
		}, `startup state ${state}`);
	} catch (error) {
		throw new Error(`${error instanceof Error ? error.message : String(error)}; runner stderr: ${s.stderr()}`);
	}
}

async function stop(s) {
	if (s.child.exitCode === null && s.child.signalCode === null) s.child.kill("SIGTERM");
	return Promise.race([s.exited, sleep(2_000).then(() => ({ code: undefined, signal: "timeout" }))]);
}

function define(name, fn) {
	if (!selectedCase || selectedCase === name) test(name, fn);
}

process.on("exit", () => {
	for (const root of roots) fs.rmSync(root, { recursive: true, force: true });
});

define("mixed-generation", async () => {
	const s = stage("mixed", { protocol: false });
	try {
		const first = await startupState(s, "ready");
		if (first.state === "ready") {
			atomicJson(s.ack, { action: "ack", token: first.token });
			const acknowledged = await startupState(s, "acknowledged");
			assert.equal(acknowledged.state, "acknowledged");
			atomicJson(s.proceed, { action: "proceed", token: first.token });
			await sleep(400);
			assert.equal(promptCount(s.queueDir), 0, "mixed-generation runner must not execute a prompt");
			throw new Error("runner accepted an unstamped revival protocol and stalled after legacy ack/proceed routing");
		}
		const exit = await s.exited;
		assert.equal(exit.code, 1);
		assert.match(first.error ?? "", /revival startup protocol mismatch/);
		assert.equal(promptCount(s.queueDir), 0);
	} finally {
		await stop(s);
	}
});

define("aligned-authority", async () => {
	const s = stage("aligned");
	try {
		const ready = await startupState(s, "ready");
		assert.equal(ready.state, "ready");
		atomicJson(s.ack, { action: "ack", token: ready.token });
		assert.equal((await startupState(s, "acknowledged")).state, "acknowledged");
		atomicJson(s.ack, { action: "confirm", token: ready.token });
		assert.equal((await startupState(s, "confirmed")).state, "confirmed");
		assert.equal(promptCount(s.queueDir), 0, "confirmation is not permission to prompt");
		atomicJson(s.proceed, { action: "proceed", token: ready.token });
		const exit = await s.exited;
		assert.equal(exit.code, 0, s.stderr());
		assert.equal(promptCount(s.queueDir), 1);
		assert.equal(readJson(path.join(s.asyncDir, "result.json"))?.success, true);
	} finally {
		await stop(s);
	}
});

define("wrong-confirmation", async () => {
	const s = stage("wrong-confirm");
	try {
		const ready = await startupState(s, "ready");
		atomicJson(s.ack, { action: "ack", token: ready.token });
		assert.equal((await startupState(s, "acknowledged")).state, "acknowledged");
		atomicJson(s.ack, { action: "confirm", token: "wrong-control-token" });
		const exit = await s.exited;
		assert.equal(exit.code, 1);
		assert.match(readJson(s.startup)?.error ?? "", /token does not match/);
		assert.equal(promptCount(s.queueDir), 0);
	} finally {
		await stop(s);
	}
});

define("missing-controls-and-cancel", async () => {
	for (const point of ["confirm", "proceed"]) {
		const s = stage(`missing-${point}`);
		try {
			const ready = await startupState(s, "ready");
			atomicJson(s.ack, { action: "ack", token: ready.token });
			assert.equal((await startupState(s, "acknowledged")).state, "acknowledged");
			if (point === "proceed") {
				atomicJson(s.ack, { action: "confirm", token: ready.token });
				assert.equal((await startupState(s, "confirmed")).state, "confirmed");
			}
			await sleep(250);
			assert.equal(promptCount(s.queueDir), 0, `missing ${point} must prevent prompt execution`);
			const exit = await stop(s);
			assert.equal(exit.signal, "SIGTERM");
			assert.equal(promptCount(s.queueDir), 0);
		} finally {
			await stop(s);
		}
	}
});

define("lease-identity-and-release", async () => {
	const root = fs.mkdtempSync(path.join(os.tmpdir(), "pi-subagents-startup-lease-"));
	roots.push(root);
	const sessionFile = path.join(root, "shared-session.jsonl");
	const first = stage("lease-first", { root, sessionFile });
	const ready = await startupState(first, "ready");
	assert.equal(ready.state, "ready");
	const second = stage("lease-second", { root, sessionFile });
	try {
		const secondState = await startupState(second, "ready");
		assert.equal(secondState.state, "error");
		assert.match(secondState.error ?? "", /already owned by run/);
		assert.equal((await second.exited).code, 1);
		assert.equal(promptCount(second.queueDir), 0);
		assert.equal(promptCount(first.queueDir), 0);
		await stop(first);
		const canonical = fs.realpathSync.native(sessionFile);
		const leaseKey = createHash("sha256").update(canonical).digest("hex");
		const leaseDir = path.join(root, "runtime", "session-leases", leaseKey);
		assert.equal(fs.existsSync(leaseDir), true, "signal cancellation leaves conservative owner evidence");
		// A later runner may reclaim only after the recorded PID is demonstrably gone.
		const third = stage("lease-third", { root, sessionFile });
		try {
			assert.equal((await startupState(third, "ready")).state, "ready");
			assert.equal(promptCount(third.queueDir), 0);
		} finally {
			await stop(third);
		}
	} finally {
		await stop(second);
		await stop(first);
	}
});
