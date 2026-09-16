#!/usr/bin/env python3
import argparse, base64, hashlib, io, json, re
from pathlib import Path
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("log")
parser.add_argument("--count", action="store_true")
parser.add_argument("--out")
parser.add_argument("--manifest")
args = parser.parse_args()
data = Path(args.log).read_bytes() if Path(args.log).exists() else b""
# Direct Kitty ends with ESC\\; tmux doubles that ESC inside its DCS wrapper.
pattern = re.compile(rb"\x1b_G([^;]*);([A-Za-z0-9+/=]*)\x1b(?:\x1b)?\\")
commands = []
for match in pattern.finditer(data):
    controls = {}
    for field in match.group(1).decode("ascii", "replace").split(","):
        if "=" in field:
            key, value = field.split("=", 1)
            controls[key] = value
    commands.append((controls, match.group(2)))

groups, errors, current = [], [], None
for index, (controls, payload) in enumerate(commands):
    if controls.get("a") == "t":
        if current is not None:
            errors.append(f"upload at command {index} started before prior m=0")
        current = {"id": controls.get("i"), "start": index, "chunks": 0, "payload": bytearray()}
    elif current is not None and set(controls) - {"m", "q"}:
        errors.append(f"continuation at command {index} has controls {sorted(controls)}")
    if current is not None:
        current["chunks"] += 1
        current["payload"].extend(payload)
        if controls.get("m") == "0":
            current["end"] = index
            groups.append(current)
            current = None
if current is not None:
    errors.append("final upload has no m=0")
if args.count:
    print(len(groups))
    raise SystemExit(0)

out = Path(args.out) if args.out else None
if out:
    out.mkdir(parents=True, exist_ok=True)
manifest = json.loads(Path(args.manifest).read_text()) if args.manifest else {}
summary_groups = []
large_matches = 0
for occurrence, group in enumerate(groups):
    try:
        png = base64.b64decode(group.pop("payload"), validate=True)
        digest = hashlib.sha256(png).hexdigest()
        with Image.open(io.BytesIO(png)) as image:
            width, height = image.size
            mode = image.mode
            pixel_digest = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
        if digest == manifest.get("largeSha256"):
            large_matches += 1
            if pixel_digest != manifest.get("largePixelSha256"):
                errors.append(f"large upload {occurrence} pixel/alpha hash mismatch")
        if out:
            (out / f"upload-{occurrence:03d}-id-{group['id']}.png").write_bytes(png)
        summary_groups.append({**group, "bytes": len(png), "sha256": digest, "pixelSha256": pixel_digest, "width": width, "height": height, "mode": mode})
    except Exception as error:
        errors.append(f"upload {occurrence} decode failed: {error}")
summary = {
    "commands": len(commands),
    "uploads": len(groups),
    "errors": errors,
    "largeMatches": large_matches,
    "expectedUploadsPerViewer": manifest.get("expectedUploadsPerViewer"),
    "groups": summary_groups,
}
if out:
    (out / "wire-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary))
if errors:
    raise SystemExit(1)
