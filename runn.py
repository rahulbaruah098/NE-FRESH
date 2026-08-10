"""
One-time regeneration/backfill for existing NE Locals product card thumbnails.

What it does:
- Scans existing MongoDB products.
- Keeps every original image_path unchanged.
- Regenerates every available product thumbnail as an optimized WebP.
- Preserves the COMPLETE original image.
- Preserves the original aspect ratio.
- Never crops, stretches, distorts or adds a blurred background.
- Limits generated thumbnail dimensions for faster catalogue loading.
- Overwrites the previous generated thumbnail file.
- Stores/refreshes product.thumbnail_path.
- Safe to run again.

Run this from the project root:
    python runn.py
"""

import os
from datetime import datetime

from PIL import Image, ImageOps

from app_core import app, mongo

THUMBNAIL_MAX_SIZE = (960, 1440)
THUMBNAIL_QUALITY = 84
THUMBNAIL_SUBFOLDER = "product_thumbnails"


def _normalized_upload_relative_path(image_path):
    normalized = str(image_path or "").replace("\\", "/").lstrip("/")

    if normalized.startswith("uploads/"):
        return normalized[len("uploads/"):]

    return normalized


def _source_file_path(image_path):
    relative_path = _normalized_upload_relative_path(image_path)

    if not relative_path:
        return None

    return os.path.join(
        app.config["UPLOAD_FOLDER"],
        relative_path
    )


def generate_product_thumbnail(image_path):
    """
    Generate one optimized WebP product-card thumbnail.

    The complete source image is preserved.
    No crop, stretch, distortion, blur or generated background is used.
    """
    source_path = _source_file_path(image_path)

    if not source_path or not os.path.isfile(source_path):
        return None

    thumbnail_folder = os.path.join(
        app.config["UPLOAD_FOLDER"],
        THUMBNAIL_SUBFOLDER
    )
    os.makedirs(thumbnail_folder, exist_ok=True)

    source_stem = os.path.splitext(os.path.basename(source_path))[0]
    thumbnail_name = f"{source_stem}_960x600.webp"
    thumbnail_path = os.path.join(
        thumbnail_folder,
        thumbnail_name
    )

    with Image.open(source_path) as source_image:
        image = ImageOps.exif_transpose(source_image)

        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            white_background = Image.new(
                "RGBA",
                rgba.size,
                (255, 255, 255, 255)
            )
            white_background.alpha_composite(rgba)
            image = white_background.convert("RGB")
        else:
            image = image.convert("RGB")

        thumbnail = image.copy()
        thumbnail.thumbnail(
            THUMBNAIL_MAX_SIZE,
            Image.Resampling.LANCZOS
        )

        thumbnail.save(
            thumbnail_path,
            format="WEBP",
            quality=THUMBNAIL_QUALITY,
            method=6
        )

    return f"uploads/{THUMBNAIL_SUBFOLDER}/{thumbnail_name}"


def regenerate_product_thumbnails():
    stats = {
        "scanned": 0,
        "regenerated": 0,
        "missing_image_path": 0,
        "missing_source_file": 0,
        "failed": 0,
    }

    products = mongo.products.find(
        {},
        {
            "_id": 1,
            "name": 1,
            "image_path": 1,
            "thumbnail_path": 1,
        }
    )

    print("")
    print("NE Locals product thumbnail regeneration")
    print("=" * 48)
    print(
        f"Maximum thumbnail size: "
        f"{THUMBNAIL_MAX_SIZE[0]}x{THUMBNAIL_MAX_SIZE[1]} WebP "
        f"(quality {THUMBNAIL_QUALITY})"
    )
    print("Complete image WILL be preserved.")
    print("Original aspect ratio WILL be preserved.")
    print("Existing generated thumbnail files WILL be regenerated.")
    print("Original image_path files WILL NOT be changed.")
    print("")

    for product in products:
        stats["scanned"] += 1

        product_id = product.get("_id")
        product_name = (product.get("name") or "Unnamed Product").strip()
        image_path = product.get("image_path")
        prefix = f"[{stats['scanned']}] {product_name} ({product_id})"

        if not image_path:
            stats["missing_image_path"] += 1
            print(f"SKIP  {prefix} -> no image_path")
            continue

        source_path = _source_file_path(image_path)

        if not source_path or not os.path.isfile(source_path):
            stats["missing_source_file"] += 1
            print(f"SKIP  {prefix} -> source image file not found: {image_path}")
            continue

        try:
            thumbnail_path = generate_product_thumbnail(image_path)

            if not thumbnail_path:
                stats["failed"] += 1
                print(f"FAIL  {prefix} -> thumbnail was not generated")
                continue

            result = mongo.products.update_one(
                {"_id": product_id},
                {
                    "$set": {
                        "thumbnail_path": thumbnail_path,
                        "thumbnail_generated_at": datetime.utcnow(),
                    }
                }
            )

            if result.matched_count != 1:
                stats["failed"] += 1
                print(f"FAIL  {prefix} -> MongoDB product was not matched")
                continue

            stats["regenerated"] += 1
            print(f"DONE  {prefix} -> {thumbnail_path}")

        except Exception as exc:
            stats["failed"] += 1
            print(f"FAIL  {prefix} -> {type(exc).__name__}: {exc}")

    print("")
    print("=" * 48)
    print("Thumbnail regeneration complete")
    print(f"Scanned:             {stats['scanned']}")
    print(f"Regenerated:         {stats['regenerated']}")
    print(f"No image_path:       {stats['missing_image_path']}")
    print(f"Missing source file: {stats['missing_source_file']}")
    print(f"Failed:              {stats['failed']}")
    print("=" * 48)
    print("")

    return stats


if __name__ == "__main__":
    regenerate_product_thumbnails()
