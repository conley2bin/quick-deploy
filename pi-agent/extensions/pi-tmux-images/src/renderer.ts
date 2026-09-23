import { type Component, Image, type ImageTheme } from "@earendil-works/pi-tui";
import type { PreviewRuntime } from "./runtime.ts";
import type { PreviewEntry } from "./transcript-entry.ts";

export function fallback(entry: PreviewEntry, reason?: string): string[] {
	return [`[image] ${reason ?? `${entry.originalMime} ${entry.width}×${entry.height}`}: ${entry.path}`];
}
function imageTheme(theme: unknown): ImageTheme {
	const candidate = theme as { fallbackColor?: unknown } | undefined;
	return {
		fallbackColor:
			typeof candidate?.fallbackColor === "function"
				? (candidate.fallbackColor as (text: string) => string)
				: (text) => text,
	};
}
export function previewComponent(
	entry: PreviewEntry,
	runtime: PreviewRuntime,
	theme: unknown,
	stale?: string,
): Component {
	if (stale) return { render: () => fallback(entry, stale), invalidate() {} };
	const image = runtime.get(entry.logicalId);
	if (!image) return { render: () => fallback(entry, "Image is unavailable in this process."), invalidate() {} };
	if (runtime.mode() === "kitty-placeholder")
		return {
			render: (width) => {
				const rows = runtime.emitPlaceholder(entry.logicalId, width);
				return rows.length > 0 ? rows : fallback(entry, runtime.notice(entry.logicalId));
			},
			invalidate() {},
		};
	if (runtime.mode() === "image")
		return new Image(image.png.toString("base64"), "image/png", imageTheme(theme), {
			maxWidthCells: 80,
			maxHeightCells: 24,
			imageId: runtime.terminalId(entry.logicalId),
		});
	return { render: () => fallback(entry), invalidate() {} };
}
