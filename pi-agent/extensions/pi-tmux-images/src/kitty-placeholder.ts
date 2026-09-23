const ESC = "\x1b";
const GLYPH = "\u{10eeee}";
const ROW_COLUMN_DIACRITICS = [
	"\u{305}",
	"\u{30D}",
	"\u{30E}",
	"\u{310}",
	"\u{312}",
	"\u{33D}",
	"\u{33E}",
	"\u{33F}",
	"\u{346}",
	"\u{34A}",
	"\u{34B}",
	"\u{34C}",
	"\u{350}",
	"\u{351}",
	"\u{352}",
	"\u{357}",
	"\u{35B}",
	"\u{363}",
	"\u{364}",
	"\u{365}",
	"\u{366}",
	"\u{367}",
	"\u{368}",
	"\u{369}",
	"\u{36A}",
	"\u{36B}",
	"\u{36C}",
	"\u{36D}",
	"\u{36E}",
	"\u{36F}",
	"\u{483}",
	"\u{484}",
	"\u{485}",
	"\u{486}",
	"\u{487}",
	"\u{592}",
	"\u{593}",
	"\u{594}",
	"\u{595}",
	"\u{597}",
	"\u{598}",
	"\u{599}",
	"\u{59C}",
	"\u{59D}",
	"\u{59E}",
	"\u{59F}",
	"\u{5A0}",
	"\u{5A1}",
	"\u{5A8}",
	"\u{5A9}",
	"\u{5AB}",
	"\u{5AC}",
	"\u{5AF}",
	"\u{5C4}",
	"\u{610}",
	"\u{611}",
	"\u{612}",
	"\u{613}",
	"\u{614}",
	"\u{615}",
	"\u{616}",
	"\u{617}",
	"\u{657}",
	"\u{658}",
	"\u{659}",
	"\u{65A}",
	"\u{65B}",
	"\u{65D}",
	"\u{65E}",
	"\u{6D6}",
	"\u{6D7}",
	"\u{6D8}",
	"\u{6D9}",
	"\u{6DA}",
	"\u{6DB}",
	"\u{6DC}",
	"\u{6DF}",
	"\u{6E0}",
	"\u{6E1}",
	"\u{6E2}",
	"\u{6E4}",
	"\u{6E7}",
	"\u{6E8}",
	"\u{6EB}",
	"\u{6EC}",
	"\u{730}",
	"\u{732}",
	"\u{733}",
	"\u{735}",
	"\u{736}",
	"\u{73A}",
	"\u{73D}",
	"\u{73F}",
	"\u{740}",
	"\u{741}",
	"\u{743}",
	"\u{745}",
	"\u{747}",
	"\u{749}",
	"\u{74A}",
	"\u{7EB}",
	"\u{7EC}",
	"\u{7ED}",
	"\u{7EE}",
	"\u{7EF}",
	"\u{7F0}",
	"\u{7F1}",
	"\u{7F3}",
	"\u{816}",
	"\u{817}",
	"\u{818}",
	"\u{819}",
	"\u{81B}",
	"\u{81C}",
	"\u{81D}",
	"\u{81E}",
	"\u{81F}",
	"\u{820}",
	"\u{821}",
	"\u{822}",
	"\u{823}",
	"\u{825}",
	"\u{826}",
	"\u{827}",
	"\u{829}",
	"\u{82A}",
	"\u{82B}",
	"\u{82C}",
	"\u{82D}",
	"\u{951}",
	"\u{953}",
	"\u{954}",
	"\u{F82}",
	"\u{F83}",
	"\u{F86}",
	"\u{F87}",
	"\u{135D}",
	"\u{135E}",
	"\u{135F}",
	"\u{17DD}",
	"\u{193A}",
	"\u{1A17}",
	"\u{1A75}",
	"\u{1A76}",
	"\u{1A77}",
	"\u{1A78}",
	"\u{1A79}",
	"\u{1A7A}",
	"\u{1A7B}",
	"\u{1A7C}",
	"\u{1B6B}",
	"\u{1B6D}",
	"\u{1B6E}",
	"\u{1B6F}",
	"\u{1B70}",
	"\u{1B71}",
	"\u{1B72}",
	"\u{1B73}",
	"\u{1CD0}",
	"\u{1CD1}",
	"\u{1CD2}",
	"\u{1CDA}",
	"\u{1CDB}",
	"\u{1CE0}",
	"\u{1DC0}",
	"\u{1DC1}",
	"\u{1DC3}",
	"\u{1DC4}",
	"\u{1DC5}",
	"\u{1DC6}",
	"\u{1DC7}",
	"\u{1DC8}",
	"\u{1DC9}",
	"\u{1DCB}",
	"\u{1DCC}",
	"\u{1DD1}",
	"\u{1DD2}",
	"\u{1DD3}",
	"\u{1DD4}",
	"\u{1DD5}",
	"\u{1DD6}",
	"\u{1DD7}",
	"\u{1DD8}",
	"\u{1DD9}",
	"\u{1DDA}",
	"\u{1DDB}",
	"\u{1DDC}",
	"\u{1DDD}",
	"\u{1DDE}",
	"\u{1DDF}",
	"\u{1DE0}",
	"\u{1DE1}",
	"\u{1DE2}",
	"\u{1DE3}",
	"\u{1DE4}",
	"\u{1DE5}",
	"\u{1DE6}",
	"\u{1DFE}",
	"\u{20D0}",
	"\u{20D1}",
	"\u{20D4}",
	"\u{20D5}",
	"\u{20D6}",
	"\u{20D7}",
	"\u{20DB}",
	"\u{20DC}",
	"\u{20E1}",
	"\u{20E7}",
	"\u{20E9}",
	"\u{20F0}",
	"\u{2CEF}",
	"\u{2CF0}",
	"\u{2CF1}",
	"\u{2DE0}",
	"\u{2DE1}",
	"\u{2DE2}",
	"\u{2DE3}",
	"\u{2DE4}",
	"\u{2DE5}",
	"\u{2DE6}",
	"\u{2DE7}",
	"\u{2DE8}",
	"\u{2DE9}",
	"\u{2DEA}",
	"\u{2DEB}",
	"\u{2DEC}",
	"\u{2DED}",
	"\u{2DEE}",
	"\u{2DEF}",
	"\u{2DF0}",
	"\u{2DF1}",
	"\u{2DF2}",
	"\u{2DF3}",
	"\u{2DF4}",
	"\u{2DF5}",
	"\u{2DF6}",
	"\u{2DF7}",
	"\u{2DF8}",
	"\u{2DF9}",
	"\u{2DFA}",
	"\u{2DFB}",
	"\u{2DFC}",
	"\u{2DFD}",
	"\u{2DFE}",
	"\u{2DFF}",
	"\u{A66F}",
	"\u{A67C}",
	"\u{A67D}",
	"\u{A6F0}",
	"\u{A6F1}",
	"\u{A8E0}",
	"\u{A8E1}",
	"\u{A8E2}",
	"\u{A8E3}",
	"\u{A8E4}",
	"\u{A8E5}",
] as const;

export interface Sink {
	write(sequence: string): void;
}
export function tmux(sequence: string): string {
	return `${ESC}Ptmux;${sequence.replaceAll(ESC, ESC + ESC)}${ESC}\\`;
}
export function kitty(command: string, inTmux: boolean): string {
	const sequence = `${ESC}_G${command}${ESC}\\`;
	return inTmux ? tmux(sequence) : sequence;
}
export function upload(base64: string, imageId: number, inTmux: boolean): string[] {
	const chunks = base64.match(/.{1,4096}/gu) ?? [""];
	return chunks.map((chunk, index) =>
		kitty(`${index ? "" : `a=t,f=100,i=${imageId},q=2,`}m=${index + 1 < chunks.length ? 1 : 0};${chunk}`, inTmux),
	);
}
function placementId(imageId: number): number {
	return imageId & 0xffffff || 1;
}
export function placement(imageId: number, columns: number, rows: number, inTmux: boolean): string {
	return kitty(`a=p,i=${imageId},p=${placementId(imageId)},U=1,c=${columns},r=${rows},q=2;`, inTmux);
}
/** Delete one virtual placement while retaining its uploaded image data. */
export function deletePlacement(imageId: number, inTmux: boolean): string {
	return kitty(`a=d,d=i,i=${imageId},p=${placementId(imageId)},q=2;`, inTmux);
}
export function deleteImage(imageId: number, inTmux: boolean): string {
	return kitty(`a=d,d=I,i=${imageId},q=2;`, inTmux);
}
function mark(index: number): string {
	const value = ROW_COLUMN_DIACRITICS[index];
	if (!value) throw new Error(`Kitty placeholder index ${index} exceeds supported diacritic table`);
	return value;
}
function rgb(code: 38 | 58, value: number): string {
	return `${ESC}[${code};2;${(value >>> 16) & 255};${(value >>> 8) & 255};${value & 255}m`;
}
/** Kitty Unicode placeholder: foreground is image ID, underline is placement ID. */
export function cell(column: number, row: number, imageId: number): string {
	const high = imageId >>> 24;
	return `${rgb(38, imageId & 0xffffff)}${rgb(58, imageId & 0xffffff || 1)}${GLYPH}${mark(row)}${mark(column)}${high ? mark(high) : ""}${ESC}[39;59m`;
}
export function grid(columns: number, rows: number, imageId: number): string[] {
	return Array.from({ length: rows }, (_, row) =>
		Array.from({ length: columns }, (_, column) => cell(column, row, imageId)).join(""),
	);
}
/**
 * Fork note: upstream derived this from the host TUI (`visibleWidth`). The engine is
 * host-free here, so it measures the printable payload itself.
 */
export function placeholderWidth(line: string): number {
	const printable = line.replace(/\u001b\[[0-9;?]*[A-Za-z]/gu, "");
	return [...printable].length;
}

/** Hard ceiling for one transaction's base64 payload. */
export const MAX_TRANSACTION_BASE64 = 256 * 1024;
/** Minimum spacing between two transactions for the same image. */
export const MIN_TRANSACTION_INTERVAL_MS = 250;
export const KITTY_CHUNK_BASE64_CHARS = 4_096;
const SYNC_START = `${ESC}[?2026h`;
const SYNC_END = `${ESC}[?2026l`;
const DEFAULT_CELL = { widthPx: 10, heightPx: 20 };

export interface BoundedPayload {
	base64: string;
	png: Buffer;
}

export interface GeometryOptions {
	maxColumns?: number;
	maxRows?: number;
	cell?: { widthPx: number; heightPx: number };
}

/**
 * Cell geometry for one image. Non-finite or non-positive inputs fall back to a
 * single cell instead of producing NaN, which would silently render nothing.
 */
export function geometry(width: number, height: number, options: GeometryOptions = {}): { columns: number; rows: number } {
	const cell = options.cell ?? DEFAULT_CELL;
	const maxColumns = Number.isFinite(options.maxColumns) && (options.maxColumns ?? 0) > 0 ? options.maxColumns! : 80;
	const maxRows = Number.isFinite(options.maxRows) && (options.maxRows ?? 0) > 0 ? options.maxRows! : 24;
	const cellWidth = Number.isFinite(cell.widthPx) && cell.widthPx > 0 ? cell.widthPx : DEFAULT_CELL.widthPx;
	const cellHeight = Number.isFinite(cell.heightPx) && cell.heightPx > 0 ? cell.heightPx : DEFAULT_CELL.heightPx;
	if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return { columns: 1, rows: 1 };
	const scale = Math.min((maxColumns * cellWidth) / width, (maxRows * cellHeight) / height, 1);
	const columns = Math.max(1, Math.ceil((width * scale) / cellWidth));
	const rows = Math.max(1, Math.ceil((height * scale) / cellHeight));
	return { columns, rows };
}

function wrapSync(sequence: string, inTmux: boolean): string {
	return inTmux ? tmux(sequence) : sequence;
}

/**
 * One complete upload + placement transaction as a single string, framed by
 * synchronized output so a terminal (or tmux) never observes a half-applied image.
 * Callers must perform exactly one write() with the result.
 */
export function transaction(options: {
	imageId: number;
	base64: string;
	columns: number;
	rows: number;
	inTmux: boolean;
	/** Replace an existing virtual placement for this image. */
	replacePlacement: boolean;
	/** Emit the image payload again. */
	upload: boolean;
	/** Free the previous image data; only valid together with a re-upload. */
	purgeImage: boolean;
}): string {
	const { imageId, base64, columns, rows, inTmux, replacePlacement, upload: send, purgeImage } = options;
	const parts: string[] = [wrapSync(SYNC_START, inTmux)];
	if (purgeImage) parts.push(deleteImage(imageId, inTmux));
	if (replacePlacement) parts.push(deletePlacement(imageId, inTmux));
	if (send) parts.push(...upload(base64, imageId, inTmux));
	parts.push(placement(imageId, columns, rows, inTmux));
	parts.push(wrapSync(SYNC_END, inTmux));
	return parts.join("");
}

/**
 * Regular-file transfer (Kitty t=f): the terminal reads the PNG from disk, so the
 * wire carries only one short command instead of every base64 chunk.
 */
export function fileTransferCommands(options: {
	imageId: number;
	filePath: string;
	columns: number;
	rows: number;
	inTmux: boolean;
}): string {
	const { imageId, filePath, columns, rows, inTmux } = options;
	const encodedPath = Buffer.from(filePath, "utf8").toString("base64");
	return [
		wrapSync(SYNC_START, inTmux),
		kitty(`a=t,f=100,t=f,i=${imageId},q=2;${encodedPath}`, inTmux),
		placement(imageId, columns, rows, inTmux),
		wrapSync(SYNC_END, inTmux),
	].join("");
}

/** Coalesced teardown: one string covers every owned image id. */
export function coalescedDeleteImages(ids: readonly number[], inTmux: boolean): string {
	if (ids.length === 0) return "";
	return [wrapSync(SYNC_START, inTmux), ...ids.map((id) => deleteImage(id, inTmux)), wrapSync(SYNC_END, inTmux)].join("");
}

/**
 * Strict payload bounding: returns undefined when the encoded PNG cannot be brought
 * under the ceiling. It never returns the oversized original, so callers fall back to
 * text instead of emitting a transaction that would be truncated in transit.
 */
export async function boundedPayload(png: Buffer, maxBase64 = MAX_TRANSACTION_BASE64): Promise<BoundedPayload | undefined> {
	if (png.length === 0) return undefined;
	if (png.toString("base64").length <= maxBase64) return { base64: png.toString("base64"), png };
	let sharp: typeof import("sharp").default | undefined;
	try {
		sharp = (await import("sharp")).default;
	} catch {
		return undefined;
	}
	let candidate = png;
	for (let attempt = 0; attempt < 8; attempt += 1) {
		const meta = await sharp(candidate).metadata();
		const width = Math.max(1, Math.floor((meta.width ?? 1) / 2));
		const height = Math.max(1, Math.floor((meta.height ?? 1) / 2));
		candidate = await sharp(candidate).resize(width, height).png({ palette: false }).toBuffer();
		if (candidate.toString("base64").length <= maxBase64) return { base64: candidate.toString("base64"), png: candidate };
	}
	return undefined;
}
