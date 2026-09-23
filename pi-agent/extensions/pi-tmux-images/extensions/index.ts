import { randomUUID } from "node:crypto";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { automaticBlocks, type BranchEntry, findOrigin, hasPersistedOrigin, originToken } from "../src/automatic.ts";
import {
	bridgeReason,
	GRAPHICS_OWNER_REPLY,
	GRAPHICS_OWNER_REQUEST,
	hostBindings,
	isBridgeActive,
	READ_PREVIEW_COORDINATION,
} from "../src/host.ts";
import { previewComponent } from "../src/renderer.ts";
import { PreviewRuntime } from "../src/runtime.ts";
import { CLEAR_TYPE, type ClearEntry, ENTRY_TYPE, isPreview, type PreviewEntry } from "../src/transcript-entry.ts";

function branch(ctx: ExtensionContext): BranchEntry[] {
	return ctx.sessionManager.getBranch() as BranchEntry[];
}
function activeEntries(ctx: ExtensionContext): PreviewEntry[] {
	const entries = branch(ctx);
	let latestClear = -1;
	entries.forEach((entry, index) => {
		if (entry.type === "custom" && entry.customType === CLEAR_TYPE) latestClear = index;
	});
	return entries
		.slice(latestClear + 1)
		.flatMap((entry) =>
			entry.type === "custom" && entry.customType === ENTRY_TYPE && isPreview(entry.data) ? [entry.data] : [],
		)
		.slice(-16);
}

export function registerInlineImages(pi: ExtensionAPI, runtime = new PreviewRuntime({ host: hostBindings() })) {
	// The in-repo pi-inline-images bridge owns terminal graphics when it is active.
	// This fork then withholds its own rendering so two owners never share image IDs.
	const bridgeRequestId = randomUUID();
	const removeBridgeReply = pi.events.on(GRAPHICS_OWNER_REPLY, (value) => {
		if (isBridgeActive(value, bridgeRequestId, "reply")) runtime.withholdSelfRender(bridgeReason(value));
	});
	const removeBridgeCoordination = pi.events.on(READ_PREVIEW_COORDINATION, (value) => {
		if (isBridgeActive(value, bridgeRequestId, "coordination")) runtime.withholdSelfRender(bridgeReason(value));
	});
	pi.events.emit(GRAPHICS_OWNER_REQUEST, { version: 1, owner: "read", requestId: bridgeRequestId });

	let stale = new Map<string, string>();
	let serial = Promise.resolve();
	let generation = 0;
	const pending = new Map<string, NonNullable<PreviewEntry["origin"]>>();
	let messageOrdinals = new WeakMap<object, number>();
	const reservedOrdinals = new Set<number>();
	function enqueue(operation: () => Promise<void>): Promise<void> {
		const next = serial.then(operation, operation);
		serial = next.catch(() => undefined);
		return next;
	}
	function invalidate(): number {
		generation++;
		pending.clear();
		messageOrdinals = new WeakMap();
		reservedOrdinals.clear();
		return generation;
	}
	pi.registerEntryRenderer<PreviewEntry>(ENTRY_TYPE, (entry, _options, theme) => {
		const data = isPreview(entry.data) ? entry.data : undefined;
		return data
			? previewComponent(data, runtime, theme, stale.get(data.logicalId))
			: { render: () => ["[image] Invalid saved preview entry."], invalidate() {} };
	});
	pi.registerEntryRenderer<ClearEntry>(CLEAR_TYPE, () => ({
		render: () => ["[image previews cleared]"],
		invalidate() {},
	}));
	function rebuild(ctx: ExtensionContext, expected: number) {
		return enqueue(async () => {
			if (expected !== generation) return;
			runtime.clear();
			const entries = activeEntries(ctx);
			const restored = await runtime.rehydrate(entries, ctx.cwd, (entry) => {
				const block = findOrigin(branch(ctx), entry);
				return block ? { data: block.data, mimeType: block.mimeType } : undefined;
			});
			if (expected !== generation) {
				runtime.clear();
				return;
			}
			stale = restored;
		});
	}
	function automatic(message: unknown, ctx: ExtensionContext, expected: number) {
		return enqueue(async () => {
			if (expected !== generation || runtime.mode() !== "kitty-placeholder" || !message || typeof message !== "object")
				return;
			const entries = branch(ctx);
			const persistedMessageCount = entries.filter((entry) => entry.type === "message").length;
			for (const ordinal of reservedOrdinals) if (ordinal < persistedMessageCount) reservedOrdinals.delete(ordinal);
			let messageOrdinal = messageOrdinals.get(message);
			if (messageOrdinal === undefined) {
				messageOrdinal = persistedMessageCount;
				while (reservedOrdinals.has(messageOrdinal)) messageOrdinal++;
				messageOrdinals.set(message, messageOrdinal);
				reservedOrdinals.add(messageOrdinal);
			}
			for (const [token, origin] of pending) if (hasPersistedOrigin(entries, origin)) pending.delete(token);
			for (const { block, origin } of automaticBlocks(message, messageOrdinal)) {
				if (activeEntries(ctx).length >= 16) return;
				const token = originToken(origin);
				if (pending.has(token) || hasPersistedOrigin(entries, origin)) continue;
				pending.set(token, origin);
				try {
					const logicalId = randomUUID().replaceAll("-", "");
					const entry = await runtime.addBytes(block.data, block.mimeType, logicalId, "attached image");
					if (expected !== generation) {
						runtime.clear();
						return;
					}
					// message_end precedes persistence, so custom metadata intentionally comes first.
					pi.appendEntry<PreviewEntry>(ENTRY_TYPE, { ...entry, origin });
				} catch {
					pending.delete(token);
					// Keep Pi's original image block and native/error rendering unchanged.
				}
			}
		});
	}
	pi.on("session_start", async (_event, ctx) => rebuild(ctx, invalidate()));
	pi.on("session_tree", async (_event, ctx) => rebuild(ctx, invalidate()));
	pi.on("session_shutdown", async () => {
		removeBridgeReply();
		removeBridgeCoordination();
		const expected = invalidate();
		return enqueue(async () => {
			if (expected === generation) runtime.clear();
		});
	});
	pi.on("message_end", async (event, ctx) => automatic(event.message, ctx, generation));
	pi.registerCommand("image", {
		description: "Display a local image in the TUI without model context. Use /image clear to remove previews.",
		handler: async (args, ctx) => {
			if (args.trim() === "clear") {
				const expected = invalidate();
				return enqueue(async () => {
					if (expected !== generation) return;
					runtime.clear();
					pi.appendEntry<ClearEntry>(CLEAR_TYPE, { marker: true });
					stale.clear();
				});
			}
			const expected = generation;
			return enqueue(async () => {
				if (expected !== generation) return;
				if (activeEntries(ctx).length >= 16) {
					ctx.ui.notify("Image preview limit reached (16). Use /image clear before adding another image.", "error");
					return;
				}
				try {
					const entry = await runtime.add(args, randomUUID().replaceAll("-", ""), ctx.cwd);
					if (expected !== generation) {
						runtime.clear();
						return;
					}
					pi.appendEntry<PreviewEntry>(ENTRY_TYPE, entry);
				} catch (error) {
					ctx.ui.notify(error instanceof Error ? error.message : "Unable to load image.", "error");
				}
			});
		},
	});
}
export default function inlineImages(pi: ExtensionAPI) {
	registerInlineImages(pi);
}
