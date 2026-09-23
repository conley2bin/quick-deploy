import { allocateImageId, getCapabilities, getCellDimensions } from "@earendil-works/pi-tui";
import type { HostBindings } from "./runtime.ts";

/**
 * The single module that touches Pi host internals. Everything else in src/ is free of
 * bare imports so the engine can be unit-tested without a Pi installation.
 */
export function hostBindings(): HostBindings {
	return {
		allocateImageId,
		cellDimensions: () => getCellDimensions(),
		imageProtocol: () => getCapabilities().images,
	};
}

/** Bridge events published by the in-repo pi-inline-images graphics owner. */
export const GRAPHICS_OWNER_REQUEST = "pi-inline-images:graphics-owner:request";
export const GRAPHICS_OWNER_REPLY = "pi-inline-images:graphics-owner:reply";
export const READ_PREVIEW_COORDINATION = "pi-inline-images:read-preview-coordination";

export interface GraphicsOwnerReply {
	version?: unknown;
	owner?: unknown;
	requestId?: unknown;
	handle?: unknown;
}

export interface CoordinationUpdate {
	version?: unknown;
	ready?: unknown;
	reason?: unknown;
}

/** True when a graphics owner answered our request or announced readiness. */
export function isBridgeActive(
	value: unknown,
	requestId: string,
	kind: "reply" | "coordination",
): boolean {
	if (!value || typeof value !== "object") return false;
	if (kind === "reply") {
		const reply = value as GraphicsOwnerReply;
		return reply.version === 1 && reply.owner === "read" && reply.requestId === requestId && typeof reply.handle === "object";
	}
	const update = value as CoordinationUpdate;
	return update.version === 1 && update.ready === true;
}

export function bridgeReason(value: unknown): string {
	const update = value as CoordinationUpdate | undefined;
	const reason = typeof update?.reason === "string" && update.reason ? update.reason : "host graphics owner is active";
	return `Native image ownership active: ${reason}`;
}
