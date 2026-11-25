"""Pictures: how big they are, thumbnails for the gallery, and checking an edited picture before it replaces anything.

The phone's editor sends back a finished picture. Nothing about it is trusted: it must really be a picture, of the kind its file name says,
within the size limits, before a single byte is written.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:  # the app still shows pictures without Pillow; it just can't make thumbnails or check an upload
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

# Kinds a picture can be saved as, and the name Pillow gives each. GIF, icons and SVG can be shown but are not saved over (a copy as PNG is).
EDITABLE = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP"}
VIEW_ONLY = {".gif", ".ico", ".svg"}
IMAGE_EXTS = frozenset(EDITABLE) | VIEW_ONLY
MAX_PIXELS = 100_000_000  # a picture with more pixels than this is refused (it would take the PC's memory)
THUMB_SIZES = (96, 160, 256, 384, 512)

if Image is not None:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def available() -> bool:
    return Image is not None


def is_image(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def size_of(path: Path) -> tuple[int, int] | None:
    """Width and height from the file's header (the picture is not decoded), or None when that can't be told (SVG, damaged, no Pillow)."""
    if Image is None or path.suffix.lower() == ".svg":
        return None
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None


def thumbnail(path: Path, size: int, cache: Path) -> tuple[bytes, str]:
    """A small copy for the gallery: (bytes, media type). Kept on disk so the next look is instant, and renewed when the picture changes."""
    if Image is None:
        raise RuntimeError("thumbnails need the Pillow package")
    size = min(THUMB_SIZES, key=lambda s: abs(s - size))
    stat = path.stat()
    key = hashlib.sha1(f"{path}|{stat.st_mtime_ns}|{stat.st_size}|{size}".encode()).hexdigest()
    for ext, mime in ((".jpg", "image/jpeg"), (".png", "image/png")):
        hit = cache / (key + ext)
        if hit.is_file():
            return hit.read_bytes(), mime
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # a phone photo's sideways flag: show it upright
        img.thumbnail((size, size))
        flat = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        out = io.BytesIO()
        if flat:
            img.convert("RGBA").save(out, "PNG", optimize=True)
            ext, mime = ".png", "image/png"
        else:
            img.convert("RGB").save(out, "JPEG", quality=80, optimize=True)
            ext, mime = ".jpg", "image/jpeg"
    cache.mkdir(parents=True, exist_ok=True)
    data = out.getvalue()
    try:
        (cache / (key + ext)).write_bytes(data)
    except OSError:
        pass  # a full disk only costs the speed-up
    return data, mime


def check_upload(data: bytes, ext: str, max_bytes: int) -> tuple[int, int]:
    """Is this really a finished picture of the kind `ext` says, within the limits? Returns (width, height), or raises ValueError saying why not."""
    if Image is None:
        raise ValueError("This PC can't check pictures (the Pillow package is missing), so it won't save one.")
    ext = ext.lower()
    if ext not in EDITABLE:
        raise ValueError("A picture of this type can't be saved over. Save a copy as PNG, JPEG or WebP instead.")
    if not data:
        raise ValueError("There is no picture in what was sent.")
    if len(data) > max_bytes:
        raise ValueError(f"That picture is {len(data) // 1024 // 1024} MB, over the {max_bytes // 1024 // 1024} MB limit.")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt, (width, height) = img.format, img.size
            if fmt != EDITABLE[ext]:
                raise ValueError(f"What was sent is a {fmt} picture, but the file name ends in {ext}.")
            if width * height > MAX_PIXELS:
                raise ValueError("That picture has too many pixels to save.")
            img.load()  # decode all of it now, so a cut-off or damaged upload is caught here and not after the old file is gone
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("What was sent is not a picture that can be read.") from exc
    return width, height


def unique_name(folder: Path, name: str) -> str:
    """`name` if nothing has it yet, else `name (2)`, `name (3)`... before the extension, so a copy never replaces anything."""
    if not (folder / name).exists():
        return name
    stem, ext = os.path.splitext(name)
    for n in range(2, 1000):
        candidate = f"{stem} ({n}){ext}"
        if not (folder / candidate).exists():
            return candidate
    raise FileExistsError(f"Too many copies of {name}.")
