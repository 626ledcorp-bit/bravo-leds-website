"""Add the BRAVO LEDS watermark (bottom-right, light gray caps) to hero images,
matching the existing 474 banners. Usage:
    python3 watermark_heroes.py <file1.jpg> [file2.jpg ...]
    python3 watermark_heroes.py --dir static/img/kits --since 2026-10-02
Idempotent: skips files that already carry the watermark (checked via sentinel
pixel region is unreliable, so we track done files in a sidecar JSON).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

KITS = Path(__file__).resolve().parent / "static" / "img" / "kits"
SIDECAR = KITS / ".watermarked.json"
TEXT = "BRAVO LEDS"
# Match existing banners: light gray, bold, bottom-right with generous padding.
COLOR = (205, 205, 205)
PADDING = 48


def load_done():
    if SIDECAR.exists():
        return set(json.loads(SIDECAR.read_text()))
    return set()


def save_done(done):
    SIDECAR.write_text(json.dumps(sorted(done)))


def font_for(width):
    size = max(36, width // 30)
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def watermark(path: Path):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    draw = ImageDraw.Draw(im)
    font = font_for(w)
    bbox = draw.textbbox((0, 0), TEXT, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x, y = w - tw - PADDING, h - th - PADDING
    # subtle dark halo for readability on bright paint
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((x + dx, y + dy), TEXT, font=font, fill=(30, 30, 30))
    draw.text((x, y), TEXT, font=font, fill=COLOR)
    im.save(path, "JPEG", quality=90)


def main(argv):
    done = load_done()
    if "--dir" in argv:
        files = sorted(KITS.glob("hero-*.jpg"))
        if "--since" in argv:
            since = datetime.fromisoformat(argv[argv.index("--since") + 1]).timestamp()
            files = [f for f in files if f.stat().st_mtime >= since]
    else:
        files = [Path(a) for a in argv if not a.startswith("--")]
    new = 0
    for f in files:
        if f.name in done:
            continue
        watermark(f)
        done.add(f.name)
        new += 1
        print("watermarked", f.name)
    save_done(done)
    print(f"done: {new} new, {len(done)} total tracked")


if __name__ == "__main__":
    main(sys.argv[1:])
