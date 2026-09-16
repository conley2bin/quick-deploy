import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import sharp from "sharp";

const work = process.argv[2];
if (!work) throw new Error("usage: node build-session.mjs WORK_DIR");
mkdirSync(resolve(work, "images"), { recursive: true });
mkdirSync(resolve(work, "sessions"), { recursive: true });
mkdirSync(resolve(work, "agent"), { recursive: true });
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const entries = [];
let parentId = null;
let sequence = 0;
const append = (entry) => {
  const id = `fixture${String(++sequence).padStart(4, "0")}`;
  entries.push({ ...entry, id, parentId, timestamp: "2026-09-16T00:00:00.000Z" });
  parentId = id;
};

const previews = [];
for (let index = 0; index < 20; index++) {
  const width = 80, height = 8;
  const raw = Buffer.alloc(width * height * 4);
  for (let pixel = 0; pixel < width * height; pixel++) {
    raw[pixel * 4] = (index * 41 + pixel * 3) & 0xff;
    raw[pixel * 4 + 1] = (index * 83 + pixel * 5) & 0xff;
    raw[pixel * 4 + 2] = (index * 127 + pixel * 7) & 0xff;
    raw[pixel * 4 + 3] = 255;
  }
  const png = await sharp(raw, { raw: { width, height, channels: 4 } }).png({ compressionLevel: 0 }).toBuffer();
  const data = png.toString("base64");
  const contentHash = sha256(data);
  const rawHash = sha256(png);
  const toolCallId = `fixture-read-${String(index).padStart(2, "0")}`;
  append({
    type: "message",
    message: {
      role: "toolResult",
      toolCallId,
      toolName: "read",
      content: [{ type: "text", text: `fixture read ${index}` }, { type: "image", mimeType: "image/png", data }],
      isError: false,
      timestamp: 0,
    },
  });
  previews.push({
    path: "attached image",
    hash: rawHash,
    originalMime: "image/png",
    width,
    height,
    logicalId: `fixturepreview${String(index).padStart(4, "0")}`,
    origin: { messageOrdinal: index, key: `tool:${toolCallId}`, blockIndex: 1, mimeType: "image/png", contentHash },
    ...(index === 19 ? {
      readProvenance: {
        version: 1,
        status: "unverified",
        toolCallId,
        blockIndex: 1,
        blockHash: contentHash,
        receivedHash: rawHash,
        receivedMime: "image/png",
        reason: "tool-source-unverified",
      },
    } : {}),
  });
}

const width = 1920, height = 1080;
const largeRaw = Buffer.allocUnsafe(width * height * 4);
let state = 0x51f15e5d;
for (let index = 0; index < largeRaw.length; index++) {
  state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
  largeRaw[index] = state >>> 24;
}
const largePng = await sharp(largeRaw, { raw: { width, height, channels: 4 } }).png({ compressionLevel: 0 }).toBuffer();
const largePath = resolve(work, "images", "large-1920x1080.png");
writeFileSync(largePath, largePng);
for (const preview of previews) append({ type: "custom", customType: "pi-tmux-images.preview", data: preview });
append({
  type: "message",
  message: {
    role: "assistant",
    content: [{ type: "text", text: `NATIVE-MARKDOWN-BEGIN\n\n![1920x1080 alpha fixture](${largePath})\n\nNATIVE-MARKDOWN-END` }],
    api: "fixture",
    provider: "none",
    model: "none",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    stopReason: "stop",
    timestamp: 0,
  },
});

const session = [
  { type: "session", version: 3, id: "55555555-5555-4555-8555-555555555555", timestamp: "2026-09-16T00:00:00.000Z", cwd: work },
  ...entries,
];
const sessionPath = resolve(work, "sessions", "fixture.jsonl");
writeFileSync(sessionPath, `${session.map((entry) => JSON.stringify(entry)).join("\n")}\n`);
writeFileSync(resolve(work, "agent", "settings.json"), "{}\n");
writeFileSync(resolve(work, "agent", "models-store.json"), "{}\n");
writeFileSync(resolve(work, "manifest.json"), `${JSON.stringify({
  sessionPath,
  largePath,
  largeBytes: largePng.length,
  largeSha256: sha256(largePng),
  largePixelSha256: sha256(largeRaw),
  expectedUploadsPerViewer: 17,
  previewEntries: 20,
  retainedReadEntries: 16,
}, null, 2)}\n`);
console.log(sessionPath);
