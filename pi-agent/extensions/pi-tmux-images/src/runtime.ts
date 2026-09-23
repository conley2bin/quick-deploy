import { mkdir, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
	type Environment,
	isTmux,
	probeTmuxPassthrough,
	type RenderMode,
	renderMode,
	type TmuxProbe,
} from "./capabilities.ts";
import {
	boundedPayload,
	type BoundedPayload,
	coalescedDeleteImages,
	fileTransferCommands,
	geometry,
	grid,
	MAX_TRANSACTION_BASE64,
	MIN_TRANSACTION_INTERVAL_MS,
	transaction,
	type Sink,
} from "./kitty-placeholder.ts";
import { type FileOps, type LoadedImage, loadImage, loadImageBytes } from "./loader.ts";
import type { PreviewEntry } from "./transcript-entry.ts";

/** The only host coupling the engine needs; supplied by src/host.ts in production. */
export interface HostBindings {
	allocateImageId(): number;
	cellDimensions(): { widthPx: number; heightPx: number };
	imageProtocol(): "kitty" | "iterm2" | null;
}

export interface RuntimeDeps {
	env?: Environment;
	host?: HostBindings;
	output?: Sink;
	loader?: typeof loadImage;
	byteLoader?: typeof loadImageBytes;
	fs?: FileOps;
	/** Test/embedding override for the bounded tmux passthrough probe. */
	tmuxProbe?: TmuxProbe;
	/** Enable Kitty regular-file transfer (t=f) once a probe proves support. */
	fileTransfer?: boolean;
	/** Directory for temporary transfer files; required when fileTransfer is on. */
	transferDirectory?: string;
	maxTransactionBase64?: number;
	minIntervalMs?: number;
}

export interface TransactionOutcome {
	bytes: number;
	status: "accepted" | "queued";
}

const DEFAULT_CELL = { widthPx: 10, heightPx: 20 };
const DEFAULT_HOST: HostBindings = {
	allocateImageId: () => Math.floor(Math.random() * 0xfffffffe) + 1,
	cellDimensions: () => DEFAULT_CELL,
	imageProtocol: () => null,
};

/**
 * Fork engine: every image transaction is emitted as exactly one write, placeholder
 * cells are returned only after that write was accepted, and an image whose payload
 * cannot be bounded degrades to a text notice instead of an oversized write.
 */
export class PreviewRuntime {
	private readonly env: Environment;
	private readonly host: HostBindings;
	private readonly output: Sink;
	private readonly loader: typeof loadImage;
	private readonly byteLoader: typeof loadImageBytes;
	private readonly fs?: FileOps;
	private readonly protocol: "kitty" | "iterm2" | null;
	private readonly renderMode: RenderMode;
	private readonly fileTransferEnabled: boolean;
	private readonly transferDirectory: string;
	private readonly maxTransactionBase64: number;
	private readonly minIntervalMs: number;
	private readonly images = new Map<string, LoadedImage>();
	private readonly terminalIds = new Map<string, number>();
	private readonly payloads = new Map<number, BoundedPayload>();
	private readonly payloadFailures = new Map<number, string>();
	private readonly uploaded = new Map<number, string>();
	private readonly placements = new Map<number, string>();
	private readonly lastTransactionAt = new Map<number, number>();
	private readonly transferFiles = new Map<number, string>();
	private withheldReason: string | undefined;
	private probeCount = 0;

	constructor(deps: RuntimeDeps = {}) {
		this.env = deps.env ?? process.env;
		this.host = deps.host ?? DEFAULT_HOST;
		this.output = deps.output ?? { write: (value: string) => process.stdout.write(value) };
		this.loader = deps.loader ?? loadImage;
		this.byteLoader = deps.byteLoader ?? loadImageBytes;
		this.fs = deps.fs;
		this.protocol = this.host.imageProtocol();
		this.fileTransferEnabled = deps.fileTransfer === true;
		this.transferDirectory = deps.transferDirectory ?? join(tmpdir(), "pi-tmux-images-transfers");
		this.maxTransactionBase64 = deps.maxTransactionBase64 ?? MAX_TRANSACTION_BASE64;
		this.minIntervalMs = deps.minIntervalMs ?? MIN_TRANSACTION_INTERVAL_MS;
		// Probe once per runtime; render paths never spawn tmux.
		const probe: TmuxProbe = deps.tmuxProbe ?? probeTmuxPassthrough;
		this.renderMode = renderMode(this.env, this.protocol, (env) => {
			this.probeCount += 1;
			return probe(env);
		});
	}

	/** Number of tmux policy probes performed; must stay at most 1 per runtime. */
	probeCountForTest(): number {
		return this.probeCount;
	}

	/** A bound host graphics owner owns the terminal; this engine then withholds. */
	withholdSelfRender(reason: string): void {
		this.withheldReason = reason;
	}

	withheldForTest(): string | undefined {
		return this.withheldReason;
	}

	mode(): RenderMode {
		return this.withheldReason ? "text" : this.renderMode;
	}

	/** Reason to render this preview as text instead of a bitmap. */
	notice(logicalId: string): string | undefined {
		if (this.withheldReason) return this.withheldReason;
		const id = this.terminalIds.get(logicalId);
		return id === undefined ? undefined : this.payloadFailures.get(id);
	}

	private id(logicalId: string): number {
		const existing = this.terminalIds.get(logicalId);
		if (existing) return existing;
		let id = this.host.allocateImageId() >>> 0;
		while (!id || [...this.terminalIds.values()].includes(id)) id = this.host.allocateImageId() >>> 0;
		this.terminalIds.set(logicalId, id);
		return id;
	}

	terminalId(logicalId: string): number | undefined {
		return this.terminalIds.get(logicalId);
	}

	async add(path: string, logicalId: string, cwd?: string): Promise<PreviewEntry> {
		const image = await this.loader(path, { cwd, fs: this.fs });
		this.images.set(logicalId, image);
		this.id(logicalId);
		await this.preparePayload(logicalId);
		return this.entry(logicalId, image);
	}

	get(logicalId: string): LoadedImage | undefined {
		return this.images.get(logicalId);
	}

	async addBytes(
		data: string,
		mimeType: string,
		logicalId: string,
		path = "attached image",
	): Promise<PreviewEntry> {
		const image = await this.byteLoader(data, mimeType, path);
		this.images.set(logicalId, image);
		this.id(logicalId);
		await this.preparePayload(logicalId);
		return this.entry(logicalId, image);
	}

	async rehydrate(
		entries: PreviewEntry[],
		cwd?: string,
		resolve?: (entry: PreviewEntry) => { data: string; mimeType: string } | undefined,
	): Promise<Map<string, string>> {
		await this.releaseAllImages();
		this.images.clear();
		this.terminalIds.clear();
		const state = new Map<string, string>();
		for (const entry of entries) {
			try {
				const source = entry.origin ? resolve?.(entry) : undefined;
				if (entry.origin && !source) throw new Error("origin unavailable");
				const image = source
					? await this.byteLoader(source.data, source.mimeType, entry.path)
					: await this.loader(entry.path, { cwd, fs: this.fs });
				if (image.hash !== entry.hash) {
					state.set(entry.logicalId, "Image changed since this preview was saved.");
					continue;
				}
				this.images.set(entry.logicalId, image);
				this.id(entry.logicalId);
				await this.preparePayload(entry.logicalId);
			} catch {
				state.set(entry.logicalId, "Image is missing or no longer readable.");
			}
		}
		return state;
	}

	/** Bound the payload off the render path; failure means text fallback, never an oversized write. */
	private async preparePayload(logicalId: string): Promise<void> {
		const image = this.images.get(logicalId);
		if (!image) return;
		const id = this.id(logicalId);
		const bounded = await boundedPayload(image.png, this.maxTransactionBase64);
		if (!bounded) {
			this.payloads.delete(id);
			this.uploaded.delete(id);
			this.placements.delete(id);
			this.payloadFailures.set(
				id,
				`Image exceeds the ${this.maxTransactionBase64} byte transaction ceiling; rendering text instead.`,
			);
			return;
		}
		this.payloadFailures.delete(id);
		this.payloads.set(id, bounded);
		if (this.fileTransferEnabled) {
			try {
				await mkdir(this.transferDirectory, { recursive: true });
				const name = createHash("sha256").update(bounded.png).digest("hex").slice(0, 32);
				const file = join(this.transferDirectory, `${name}.png`);
				await writeFile(file, bounded.png);
				this.transferFiles.set(id, file);
			} catch {
				// Regular-file transfer is an optimisation; direct transfer stays available.
				this.transferFiles.delete(id);
			}
		}
	}

	emitPlaceholder(logicalId: string, availableWidth: number): string[] {
		// Fail closed: without a proven kitty+passthrough policy the engine emits nothing
		// and the caller renders text, so a disabled pane can never receive graphics bytes.
		if (this.mode() !== "kitty-placeholder") return [];
		const image = this.images.get(logicalId);
		if (!image || this.withheldReason) return [];
		const id = this.id(logicalId);
		const payload = this.payloads.get(id);
		if (!payload) return [];
		const cell = this.cellDimensionsForTest();
		const size = geometry(image.width, image.height, {
			maxColumns: Math.max(1, Math.min(80, availableWidth - 2)),
			cell,
		});
		const inTmux = isTmux(this.env);
		const geometryKey = `${size.columns}:${size.rows}`;
		const previousGeometry = this.placements.get(id);
		const needsUpload = this.uploaded.get(id) !== payload.base64;
		const geometryChanged = previousGeometry !== geometryKey;
		if (needsUpload || geometryChanged) {
			const since = Date.now() - (this.lastTransactionAt.get(id) ?? 0);
			const throttled = needsUpload && !geometryChanged && since < this.minIntervalMs;
			if (throttled) return [];
			const transferFile = this.fileTransferEnabled ? this.transferFiles.get(id) : undefined;
			const commands = transferFile
				? fileTransferCommands({
						imageId: id,
						filePath: transferFile,
						columns: size.columns,
						rows: size.rows,
						inTmux,
					})
				: transaction({
						imageId: id,
						base64: payload.base64,
						columns: size.columns,
						rows: size.rows,
						inTmux,
						replacePlacement: previousGeometry !== undefined,
						upload: needsUpload,
						// Geometry-only replacement must keep the uploaded image data intact.
						purgeImage: needsUpload && this.uploaded.has(id),
					});
			const accepted = this.write(commands);
			if (!accepted) return [];
			if (needsUpload) this.uploaded.set(id, payload.base64);
			this.placements.set(id, geometryKey);
			this.lastTransactionAt.set(id, Date.now());
		}
		// Cells are returned only when the terminal stream has been told about this payload.
		if (this.uploaded.get(id) !== payload.base64) return [];
		return grid(size.columns, size.rows, id);
	}

	private cellDimensionsForTest() {
		try {
			return this.host.cellDimensions();
		} catch {
			return DEFAULT_CELL;
		}
	}

	/** One sink write per transaction; false means queued behind backpressure, throws mean failure. */
	private write(commands: string): boolean {
		try {
			this.output.write(commands);
			return true;
		} catch {
			return false;
		}
	}

	async release(logicalId: string): Promise<void> {
		const id = this.terminalIds.get(logicalId);
		if (id !== undefined) {
			if (this.uploaded.has(id)) this.write(coalescedDeleteImages([id], isTmux(this.env)));
			this.uploaded.delete(id);
			this.placements.delete(id);
			this.payloads.delete(id);
			this.payloadFailures.delete(id);
			this.lastTransactionAt.delete(id);
			this.terminalIds.delete(logicalId);
			const file = this.transferFiles.get(id);
			this.transferFiles.delete(id);
			if (file) await rm(file, { force: true }).catch(() => undefined);
		}
		this.images.delete(logicalId);
	}

	/** Coalesced teardown: one write covers every owned image. */
	async clear(): Promise<void> {
		await this.releaseAllImages();
		this.images.clear();
		this.terminalIds.clear();
	}

	private async releaseAllImages(): Promise<void> {
		const ids = [...this.uploaded.keys()];
		if (ids.length > 0) this.write(coalescedDeleteImages(ids, isTmux(this.env)));
		for (const id of ids) {
			const file = this.transferFiles.get(id);
			if (file) await rm(file, { force: true }).catch(() => undefined);
		}
		this.uploaded.clear();
		this.placements.clear();
		this.payloads.clear();
		this.payloadFailures.clear();
		this.lastTransactionAt.clear();
		this.transferFiles.clear();
	}

	activeIds(): number[] {
		return [...this.terminalIds.values()];
	}

	private entry(logicalId: string, image: LoadedImage): PreviewEntry {
		return {
			path: image.path,
			hash: image.hash,
			originalMime: image.originalMime,
			width: image.width,
			height: image.height,
			logicalId,
		};
	}
}
