"""Draw the program icon and the installer's pictures, using the same logo as the app. Run by build.ps1.

    python packaging/make_assets.py          writes packaging/build/{app.ico, wizard-*.bmp}
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "build"
sys.path.insert(0, str(HERE.parent / "src"))

from handoff import branding  # noqa: E402

TOP, BOTTOM = (13, 27, 52), (3, 7, 14)
FONTS = Path("C:/Windows/Fonts")


def font(bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in (("segoeuib.ttf" if bold else "segoeui.ttf"), "arialbd.ttf" if bold else "arial.ttf"):
        try:
            return ImageFont.truetype(str(FONTS / name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def spaced(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fnt, fill, spacing: float, anchor_center: bool = True) -> None:
    """Text with extra space between the letters (the wordmark's look)."""
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = xy[0] - total / 2 if anchor_center else xy[0]
    for ch, w in zip(text, widths):
        draw.text((x, xy[1]), ch, font=fnt, fill=fill)
        x += w + spacing


def backdrop(w: int, h: int) -> Image.Image:
    """The dark blue field with a faint grid and a glow, like the app's own background."""
    img = Image.new("RGB", (w, h))
    px = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        px.line([(0, y), (w, y)], fill=tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    grid = Image.new("L", (w, h), 0)
    g = ImageDraw.Draw(grid)
    step = max(10, w // 8)
    for x in range(0, w, step):
        g.line([(x, 0), (x, h)], fill=22)
    for y in range(0, h, step):
        g.line([(0, y), (w, y)], fill=22)
    mask = Image.radial_gradient("L").resize((w, h))  # fade the grid towards the edges
    grid = ImageChops.multiply(grid, ImageChops.invert(mask))
    img.paste(Image.new("RGB", (w, h), (90, 150, 230)), mask=grid)
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([w * 0.05, h * 0.12, w * 0.95, h * 0.62], fill=(20, 90, 150))
    return ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(w * 0.22)))


def wizard_left(w: int, h: int) -> Image.Image:
    img = backdrop(w, h)
    mark = branding.logo(int(w * 0.86), "ok")
    img.paste(mark, (int(w * 0.07), int(h * 0.13)), mark)
    d = ImageDraw.Draw(img)
    spaced(d, (w / 2, h * 0.56), "ACCESSFROMPHONE", font(True, int(w * 0.07)), (120, 215, 245), w * 0.004)
    d.line([(w * 0.38, h * 0.655), (w * 0.62, h * 0.655)], fill=(59, 130, 246), width=max(1, w // 80))
    small = font(False, int(w * 0.082))
    for i, line in enumerate(("Your PC, from", "any device.")):
        d.text((w / 2 - d.textlength(line, font=small) / 2, h * 0.69 + i * w * 0.12), line, font=small, fill=(190, 205, 228))
    tiny = font(False, int(w * 0.062))
    for i, line in enumerate(("Private  ·  You approve", "every change")):
        d.text((w / 2 - d.textlength(line, font=tiny) / 2, h * 0.88 + i * w * 0.09), line, font=tiny, fill=(110, 130, 160))
    return img


def wizard_small(size: int) -> Image.Image:
    img = backdrop(size, size)
    mark = branding.logo(int(size * 0.82), "ok")
    img.paste(mark, (int(size * 0.09), int(size * 0.09)), mark)
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    icon = branding.app_icon(256)
    icon.save(OUT / "app.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
    for w, h in ((164, 314), (246, 471), (328, 628)):  # sharp at 100%, 150% and 200% display scaling
        wizard_left(w, h).save(OUT / f"wizard-left-{w}.bmp")
    for s in (55, 83, 110):
        wizard_small(s).save(OUT / f"wizard-small-{s}.bmp")
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
