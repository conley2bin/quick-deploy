import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp, { type Metadata } from "sharp";

/** Maximum encoded source accepted from a file, URL, or data URL. */
export const MAX_INPUT_BYTES = 20 * 1024 * 1024;
export const MAX_PIXELS = 32 * 1024 * 1024;
/** A full-resolution PNG retained by the inline backend. */
export const MAX_FULL_PNG_BYTES = 32 * 1024 * 1024;
export const SUPPORTED_MIME = new Set(["image/png", "image/jpeg", "image/webp"]);

export interface LoadedImage {
  source: string;
  /** Hash of the exact PNG bytes sent to the terminal. */
  hash: string;
  /** Auto-oriented source dimensions used for both layout and pixels. */
  width: number;
  height: number;
  /** The full image: original PNG bytes when it is already suitable. */
  png: Buffer;
}

function resolveLocal(raw: string, cwd: string): string {
  if (raw.startsWith("file:")) {
    const url = new URL(raw);
    if (url.hostname && url.hostname !== "localhost") throw new Error("remote file URL hosts are unsupported");
    return fileURLToPath(url);
  }
  const expanded = raw === "~" || raw.startsWith("~/") ? `${homedir()}${raw.slice(1)}` : raw;
  return normalize(isAbsolute(expanded) ? expanded : resolve(cwd, expanded));
}

async function boundedResponse(url: string): Promise<{ bytes: Buffer; mime?: string }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10_000);
  try {
    const response = await fetch(url, { redirect: "follow", signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const length = Number(response.headers.get("content-length") || 0);
    if (length > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MiB");
    if (!response.body) throw new Error("empty HTTP response");
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const chunk of response.body) {
      const buffer = Buffer.from(chunk);
      size += buffer.length;
      if (size > MAX_INPUT_BYTES) {
        controller.abort();
        throw new Error("encoded input exceeds 20 MiB");
      }
      chunks.push(buffer);
    }
    return { bytes: Buffer.concat(chunks), mime: response.headers.get("content-type")?.split(";", 1)[0].toLowerCase() };
  } finally {
    clearTimeout(timeout);
  }
}

function dataBytes(raw: string): { bytes: Buffer; mime: string } {
  const match = /^data:([^;,]+)(;base64)?,(.*)$/su.exec(raw);
  if (!match) throw new Error("malformed data URL");
  const mime = match[1].toLowerCase();
  if (!SUPPORTED_MIME.has(mime)) throw new Error("unsupported image format (use PNG, JPEG, or WebP)");
  let bytes: Buffer;
  try {
    bytes = match[2] ? Buffer.from(match[3], "base64") : Buffer.from(decodeURIComponent(match[3]), "binary");
  } catch {
    throw new Error("malformed data URL");
  }
  if (bytes.length > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MiB");
  return { bytes, mime };
}

function imageMime(format: string | undefined): string | undefined {
  if (format === "png") return "image/png";
  if (format === "jpeg") return "image/jpeg";
  if (format === "webp") return "image/webp";
  return undefined;
}

async function fullPng(bytes: Buffer, metadata: Metadata): Promise<Buffer> {
  // A PNG with no EXIF rotation is already the most faithful PNG conversion: do
  // not decode/re-encode it, because that could alter its palette, bit depth,
  // colour profile, alpha representation, or byte identity.
  if (metadata.format === "png" && (!metadata.orientation || metadata.orientation === 1)) {
    // metadata() can succeed after reading only the PNG header. Force a full
    // pixel scan so truncated/corrupt IDAT data fails before the byte-exact
    // fast path is admitted to the quiet Kitty transport.
    await sharp(bytes, { limitInputPixels: MAX_PIXELS, failOn: "error" }).stats();
    return bytes;
  }

  // JPEG/WebP are inherently 8-bit in the supported input set. For a rotated
  // higher-depth PNG retain 16-bit RGB(A) rather than silently reducing it.
  const pipeline = sharp(bytes, { limitInputPixels: MAX_PIXELS, failOn: "error" }).autoOrient();
  if (metadata.depth === "ushort") pipeline.toColourspace("rgb16");
  return pipeline.png({ palette: false }).toBuffer();
}

async function decode(bytes: Buffer, source: string, claimedMime?: string): Promise<LoadedImage> {
  if (bytes.length > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MiB");
  try {
    const metadata = await sharp(bytes, { limitInputPixels: MAX_PIXELS, failOn: "error" }).metadata();
    const mime = imageMime(metadata.format);
    if (!mime || !SUPPORTED_MIME.has(mime)) throw new Error("unsupported image format (use PNG, JPEG, or WebP)");
    if (claimedMime && SUPPORTED_MIME.has(claimedMime) && claimedMime !== mime) throw new Error("content type does not match image bytes");
    const width = metadata.autoOrient?.width || metadata.width || 0;
    const height = metadata.autoOrient?.height || metadata.height || 0;
    if (!width || !height || width * height > MAX_PIXELS) throw new Error("decoded image exceeds 32 MP");
    const png = await fullPng(bytes, metadata);
    if (png.length > MAX_FULL_PNG_BYTES) throw new Error("full PNG exceeds 32 MiB");
    return { source, hash: createHash("sha256").update(png).digest("hex"), width, height, png };
  } catch (error) {
    if (error instanceof Error && /unsupported|exceeds|does not match/iu.test(error.message)) throw error;
    throw new Error("invalid image content");
  }
}

export async function loadImage(raw: string, cwd: string): Promise<LoadedImage> {
  const source = raw.trim();
  if (!source) throw new Error("empty image resource");
  if (source.startsWith("data:")) {
    const data = dataBytes(source);
    return decode(data.bytes, "data URL", data.mime);
  }
  if (/^https?:/iu.test(source)) {
    const response = await boundedResponse(source);
    if (response.mime && !SUPPORTED_MIME.has(response.mime)) throw new Error(`unsupported HTTP content type ${response.mime}`);
    return decode(response.bytes, source, response.mime);
  }
  if (/^[a-z][a-z0-9+.-]*:/iu.test(source) && !source.startsWith("file:")) throw new Error("unsupported resource scheme");
  const path = resolveLocal(source, cwd);
  let info;
  try { info = await stat(path); } catch { throw new Error(`cannot read ${path}`); }
  if (!info.isFile()) throw new Error(`not a regular file: ${path}`);
  if (info.size > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MiB");
  let bytes: Buffer;
  try { bytes = await readFile(path); } catch { throw new Error(`cannot read ${path}`); }
  return decode(bytes, path);
}
