#!/usr/bin/env python3
import base64, hashlib, json, sys
from pathlib import Path
from PIL import Image, ImageDraw

work = Path(sys.argv[1]).resolve()
(work / "images").mkdir(parents=True, exist_ok=True)
(work / "sessions").mkdir(parents=True, exist_ok=True)
(work / "agent").mkdir(parents=True, exist_ok=True)
width, height = 800, 300
image = Image.new("RGB", (width, height), (15, 18, 28))
d = ImageDraw.Draw(image)
d.rectangle((0, 0, width - 1, height - 1), fill=(255, 0, 180))
d.rectangle((18, 18, width - 19, height - 19), fill=(0, 220, 255))
d.rectangle((400, 18, width - 19, height - 19), fill=(255, 230, 0))
d.rectangle((55, 65, 350, 235), fill=(30, 60, 220))
d.rectangle((450, 65, 745, 235), fill=(30, 220, 80))
d.ellipse((325, 55, 475, 205), fill=(255, 50, 35), outline=(255, 255, 255), width=12)
source = work / "images" / "duplicate-source.png"
image.save(source, format="PNG", compress_level=0)
raw = source.read_bytes(); data = base64.b64encode(raw).decode("ascii")
raw_hash = hashlib.sha256(raw).hexdigest(); content_hash = hashlib.sha256(data.encode()).hexdigest()
calls = ["duplicate-read-call-0001", "duplicate-read-call-0002"]
usage = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "totalTokens": 0,
         "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}}
entries = []; parent = None; sequence = 0
def append(entry):
    global parent, sequence
    sequence += 1; ident = f"duplicate{sequence:04d}"
    entries.append({**entry, "id": ident, "parentId": parent, "timestamp": "2026-09-16T00:00:00.000Z"}); parent = ident
append({"type": "message", "message": {"role": "assistant", "content": [
    {"type": "toolCall", "id": call, "name": "read", "arguments": {"path": str(source)}} for call in calls
], "api": "fixture", "provider": "none", "model": "none", "usage": usage, "stopReason": "toolUse", "timestamp": 0}})
for call in calls:
    append({"type": "message", "message": {"role": "toolResult", "toolCallId": call, "toolName": "read",
        "content": [{"type": "text", "text": "Read image file [image/png]"}, {"type": "image", "mimeType": "image/png", "data": data}],
        "isError": False, "timestamp": 0}})
for index, call in enumerate(calls):
    preview = {"path": "attached image", "hash": raw_hash, "originalMime": "image/png", "width": width, "height": height,
        "logicalId": f"duplicatepreview{index:016d}",
        "origin": {"messageOrdinal": index + 1, "key": f"tool:{call}", "blockIndex": 1, "mimeType": "image/png", "contentHash": content_hash},
        "readProvenance": {"version": 1, "status": "unverified", "toolCallId": call, "blockIndex": 1,
            "blockHash": content_hash, "receivedHash": raw_hash, "receivedMime": "image/png", "reason": "tool-source-unverified"}}
    append({"type": "custom", "customType": "pi-tmux-images.preview", "data": preview})
append({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "DUPLICATE-READ-END"}],
    "api": "fixture", "provider": "none", "model": "none", "usage": usage, "stopReason": "stop", "timestamp": 0}})
session = [{"type": "session", "version": 3, "id": "66666666-6666-4666-8666-666666666666",
            "timestamp": "2026-09-16T00:00:00.000Z", "cwd": str(work)}, *entries]
session_path = work / "sessions" / "duplicate.jsonl"
session_path.write_text("\n".join(json.dumps(x, separators=(",", ":")) for x in session) + "\n")
(work / "agent" / "settings.json").write_text("{}\n"); (work / "agent" / "models-store.json").write_text("{}\n")
(work / "manifest.json").write_text(json.dumps({"sessionPath": str(session_path), "sourcePath": str(source), "toolCallIds": calls,
    "blockIndex": 1, "expectedVisibleOccurrences": len(calls), "bytes": len(raw), "sha256": raw_hash,
    "contentHash": content_hash, "width": width, "height": height}, indent=2) + "\n")
print(session_path)
