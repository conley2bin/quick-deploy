#!/usr/bin/env python3
import argparse, json
from pathlib import Path
from PIL import Image, ImageStat

parser = argparse.ArgumentParser()
parser.add_argument("source")
parser.add_argument("png")
parser.add_argument("--min-colorful-pixels", type=int, default=0)
args = parser.parse_args()
with Image.open(args.source) as source:
    image = source.convert("RGBA")
image.save(args.png)
rgb = image.convert("RGB")
thumbnail = rgb.copy()
thumbnail.thumbnail((320, 240))
colors = thumbnail.getcolors(maxcolors=320 * 240) or []
stat = ImageStat.Stat(thumbnail)
colorful_pixels = sum(max(pixel) - min(pixel) > 40 for pixel in rgb.getdata())
summary = {
    "width": image.width,
    "height": image.height,
    "sampleUniqueColors": len(colors),
    "sampleVariance": [round(value, 2) for value in stat.var],
    "colorfulPixels": colorful_pixels,
}
Path(f"{args.png}.json").write_text(json.dumps(summary, indent=2) + "\n")
if (image.width < 800 or image.height < 600 or len(colors) < 64 or max(stat.var) < 100
        or colorful_pixels < args.min_colorful_pixels):
    raise SystemExit(f"screenshot lacks expected rendered detail: {summary}")
print(json.dumps(summary))
