import { createHash } from "node:crypto";
import { readFile, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { isAbsolute, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

export const MAX_INPUT_BYTES = 20 * 1024 * 1024;
export const MAX_PIXELS = 32 * 1024 * 1024;
export const SUPPORTED_MIME = new Set(["image/png", "image/jpeg", "image/webp"]);

export interface LoadedImage {
  source: string;
  hash: string;
  width: number;
  height: number;
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
    if (length > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MB");
    if (!response.body) throw new Error("empty HTTP response");
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const chunk of response.body) {
      const buffer = Buffer.from(chunk);
      size += buffer.length;
      if (size > MAX_INPUT_BYTES) {
        controller.abort();
        throw new Error("encoded input exceeds 20 MB");
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
  if (bytes.length > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MB");
  return { bytes, mime };
}

async function decode(bytes: Buffer, source: string, claimedMime?: string): Promise<LoadedImage> {
  if (bytes.length > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MB");
  try {
    const input = sharp(bytes, { limitInputPixels: MAX_PIXELS, failOn: "error" });
    const metadata = await input.metadata();
    const mime = metadata.format === "png" ? "image/png" : metadata.format === "jpeg" ? "image/jpeg" : metadata.format === "webp" ? "image/webp" : undefined;
    if (!mime || !SUPPORTED_MIME.has(mime)) throw new Error("unsupported image format (use PNG, JPEG, or WebP)");
    if (claimedMime && SUPPORTED_MIME.has(claimedMime) && claimedMime !== mime) throw new Error("content type does not match image bytes");
    const width = metadata.width || 0;
    const height = metadata.height || 0;
    if (!width || !height || width * height > MAX_PIXELS) throw new Error("decoded image exceeds 32 MP");
    const png = await input.rotate().png().toBuffer();
    const rotated = await sharp(png).metadata();
    return { source, hash: createHash("sha256").update(bytes).digest("hex"), width: rotated.width || width, height: rotated.height || height, png };
  } catch (error) {
    if (error instanceof Error && /unsupported|exceeds|does not match/i.test(error.message)) throw error;
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
  if (info.size > MAX_INPUT_BYTES) throw new Error("encoded input exceeds 20 MB");
  let bytes: Buffer;
  try { bytes = await readFile(path); } catch { throw new Error(`cannot read ${path}`); }
  return decode(bytes, path);
}
