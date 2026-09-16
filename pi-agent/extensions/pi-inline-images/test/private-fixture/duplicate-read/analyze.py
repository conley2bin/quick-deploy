#!/usr/bin/env python3
import json, sys
from pathlib import Path
from PIL import Image

screenshot = Path(sys.argv[1]); session_path = Path(sys.argv[2]); manifest_path = Path(sys.argv[3]); output = Path(sys.argv[4])
manifest = json.loads(manifest_path.read_text()); calls = manifest["toolCallIds"]
messages, previews = [], []
for line in session_path.read_text().splitlines():
    entry = json.loads(line)
    if entry.get("type") == "message": messages.append(entry["message"])
    if entry.get("type") == "custom" and entry.get("customType") == "pi-tmux-images.preview": previews.append(entry["data"])
assistant_calls = [part["id"] for message in messages if message.get("role") == "assistant" for part in message.get("content", []) if part.get("type") == "toolCall" and part.get("name") == "read"]
result_calls = [message.get("toolCallId") for message in messages if message.get("role") == "toolResult" and any(part.get("type") == "image" for part in message.get("content", []))]
preview_calls = [preview.get("origin", {}).get("key", "")[5:] for preview in previews if preview.get("origin", {}).get("blockIndex") == manifest["blockIndex"]]
if assistant_calls != calls or result_calls != calls or preview_calls != calls:
    raise SystemExit(f"fixture chain mismatch: {assistant_calls=} {result_calls=} {preview_calls=}")

image = Image.open(screenshot).convert("RGB"); width, height = image.size; pixels = image.load(); mask = bytearray(width * height)
for y in range(50, height):
    for x in range(width):
        pixel = pixels[x, y]
        if max(pixel) > 170 and max(pixel) - min(pixel) > 90: mask[y * width + x] = 1
seen = bytearray(width * height); regions = []
for start, value in enumerate(mask):
    if not value or seen[start]: continue
    queue = [start]; seen[start] = 1; count = 0; min_x = width; max_x = 0; min_y = height; max_y = 0
    for point in queue:
        y, x = divmod(point, width); count += 1; min_x = min(min_x, x); max_x = max(max_x, x); min_y = min(min_y, y); max_y = max(max_y, y)
        for neighbor in (point - 1, point + 1, point - width, point + width):
            if 0 <= neighbor < width * height and not seen[neighbor] and mask[neighbor] and (neighbor // width == y or neighbor % width == x):
                seen[neighbor] = 1; queue.append(neighbor)
    if max_x - min_x > 200 and max_y - min_y > 80 and count > 10000:
        regions.append({"colorfulPixels": count, "bounds": [min_x, min_y, max_x, max_y]})
summary = {"toolCallIds": calls, "blockIndex": manifest["blockIndex"], "assistantReadCalls": len(assistant_calls),
           "resultImageBlocks": len(result_calls), "matchingPreviewOrigins": len(preview_calls),
           "visibleBitmapOccurrences": len(regions), "expectedVisibleOccurrences": manifest["expectedVisibleOccurrences"], "regions": regions}
output.write_text(json.dumps(summary, indent=2) + "\n")
if summary["visibleBitmapOccurrences"] != summary["expectedVisibleOccurrences"]:
    raise SystemExit(f"visible bitmap count mismatch: {summary}")
print(json.dumps(summary))
