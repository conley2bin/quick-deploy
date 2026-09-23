import { createHash } from "node:crypto";
import { validateEncodedImageData, validateEncodedImageInput } from "./loader.ts";
import type { PreviewEntry } from "./transcript-entry.ts";

export interface ImageBlock {
	type: "image";
	mimeType: string;
	data: string;
}
export interface AutomaticOrigin {
	/** Ordinal of the regular message in the active public branch. */
	messageOrdinal: number;
	key: string;
	blockIndex: number;
	mimeType: string;
	contentHash: string;
}
export type BranchEntry = { type?: unknown; customType?: unknown; data?: unknown; message?: unknown };

export function imageBlocks(message: unknown): Array<{ block: ImageBlock; index: number }> {
	if (!message || typeof message !== "object") return [];
	const content = (message as { content?: unknown }).content;
	if (!Array.isArray(content)) return [];
	return content.flatMap((value, index) =>
		value &&
		typeof value === "object" &&
		(value as Partial<ImageBlock>).type === "image" &&
		typeof (value as Partial<ImageBlock>).mimeType === "string" &&
		typeof (value as Partial<ImageBlock>).data === "string"
			? [{ block: value as ImageBlock, index }]
			: [],
	);
}

/** Build persisted metadata only after the encoded bytes pass the pre-hash guard. */
export function originFor(
	message: unknown,
	messageOrdinal: number,
	blockIndex: number,
	data: string,
): AutomaticOrigin | undefined {
	if (!message || typeof message !== "object" || !Number.isSafeInteger(messageOrdinal) || messageOrdinal < 0)
		return undefined;
	validateEncodedImageData(data);
	const value = message as { role?: unknown; toolCallId?: unknown };
	const mimeType = imageBlocks(message).find(({ index }) => index === blockIndex)?.block.mimeType;
	if (typeof mimeType !== "string") return undefined;
	const contentHash = createHash("sha256").update(data).digest("hex");
	if (value.role === "toolResult" && typeof value.toolCallId === "string" && value.toolCallId)
		return { messageOrdinal, key: `tool:${value.toolCallId}`, blockIndex, mimeType, contentHash };
	if (value.role === "user") return { messageOrdinal, key: "user", blockIndex, mimeType, contentHash };
	return undefined;
}

/** Return only blocks whose encoded input is safe to hash and decode. */
export function automaticBlocks(
	message: unknown,
	messageOrdinal: number,
): Array<{ block: ImageBlock; index: number; origin: AutomaticOrigin }> {
	return imageBlocks(message).flatMap(({ block, index }) => {
		try {
			validateEncodedImageInput(block.data, block.mimeType);
			const origin = originFor(message, messageOrdinal, index, block.data);
			return origin ? [{ block, index, origin }] : [];
		} catch {
			return [];
		}
	});
}

function sameOrigin(left: AutomaticOrigin, right: AutomaticOrigin): boolean {
	return (
		left.messageOrdinal === right.messageOrdinal &&
		left.key === right.key &&
		left.blockIndex === right.blockIndex &&
		left.mimeType === right.mimeType &&
		left.contentHash === right.contentHash
	);
}

/** Resolve only the persisted regular-message ordinal; never search for a later collision. */
export function findOrigin(entries: BranchEntry[], preview: PreviewEntry): ImageBlock | undefined {
	if (!preview.origin) return undefined;
	const source = entries.filter((entry) => entry.type === "message")[preview.origin.messageOrdinal]?.message;
	const block = imageBlocks(source).find(({ index }) => index === preview.origin?.blockIndex)?.block;
	if (!block) return undefined;
	try {
		const origin = originFor(source, preview.origin.messageOrdinal, preview.origin.blockIndex, block.data);
		return origin && sameOrigin(origin, preview.origin) ? block : undefined;
	} catch {
		return undefined;
	}
}

export function hasPersistedOrigin(entries: BranchEntry[], origin: AutomaticOrigin): boolean {
	return entries.some((entry) => {
		if (entry.type !== "custom" || !entry.data || typeof entry.data !== "object") return false;
		const preview = entry.data as PreviewEntry;
		return Boolean(preview.origin && sameOrigin(preview.origin, origin) && findOrigin(entries, preview));
	});
}

export function originToken(origin: AutomaticOrigin): string {
	return `${origin.messageOrdinal}:${origin.key}:${origin.blockIndex}:${origin.mimeType}:${origin.contentHash}`;
}
