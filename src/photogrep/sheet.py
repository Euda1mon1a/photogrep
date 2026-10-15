"""Contact sheets from the small previews Photos keeps on disk (present even for iCloud-only photos)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .library import Photo

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:  # HEIC originals are skipped without it
    pass


def contact_sheet(photos: list[Photo], out: Path, cols: int = 5, tw: int = 320, th: int = 220) -> None:
    rows = (len(photos) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 6), rows * (th + 22)), "white")
    draw = ImageDraw.Draw(sheet)
    for k, p in enumerate(photos):
        x, y = (k % cols) * (tw + 6), (k // cols) * (th + 22)
        src = p.preview or p.original
        if src:
            im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
            im.thumbnail((tw, th))
            sheet.paste(im, (x + (tw - im.width) // 2, y))
        draw.text((x + 2, y + th + 3), f"#{k + 1} {p.date} {p.uuid[:8]} faces {p.faces}", fill="black")
    sheet.save(out, quality=85)
