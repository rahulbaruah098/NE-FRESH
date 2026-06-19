from pathlib import Path
from PIL import Image, ImageOps

IMAGE_DIR = Path("static/images")

FILES = [
    ("just a click away.png", "hero-just-click-thumb.webp"),
    ("veg and fruits near you.png", "hero-veg-fruits-thumb.webp"),
    ("get your daily needs.png", "hero-daily-needs-thumb.webp"),
]

TARGET_SIZE = (420, 300)

for src_name, out_name in FILES:
    src_path = IMAGE_DIR / src_name
    out_path = IMAGE_DIR / out_name

    if not src_path.exists():
        print(f"Missing source image: {src_path}")
        continue

    img = Image.open(src_path).convert("RGBA")
    img.thumbnail(TARGET_SIZE, Image.LANCZOS)

    canvas = Image.new("RGBA", TARGET_SIZE, (255, 255, 255, 0))
    x = (TARGET_SIZE[0] - img.width) // 2
    y = (TARGET_SIZE[1] - img.height) // 2
    canvas.paste(img, (x, y), img)

    canvas.save(
        out_path,
        "WEBP",
        quality=72,
        method=6,
        lossless=False
    )

    size_kb = out_path.stat().st_size / 1024
    print(f"Created: {out_path} | {size_kb:.1f} KB")