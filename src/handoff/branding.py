"""The logo, drawn in code: a ring that is not quite closed, around a glowing core.

The same drawing is the tray icon (changing with the app's state), the program icon and the installer's pictures,
so they always match the app. Everything is drawn at 4x and scaled down, which keeps the edges smooth at 16 pixels.
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

CYAN, BLUE, VIOLET = (34, 211, 238), (59, 130, 246), (139, 92, 246)
NAVY_TOP, NAVY_BOTTOM = (12, 24, 46), (4, 8, 16)
GREEN, AMBER, GREY = (52, 211, 153), (245, 184, 97), (112, 128, 156)
SCALE = 4


def _gradient(size: int, a: tuple, b: tuple, diagonal: bool = True) -> Image.Image:
    """A smooth blend from colour a (top left) to colour b (bottom right)."""
    horizontal = Image.linear_gradient("L").rotate(90).resize((size, size))
    vertical = Image.linear_gradient("L").resize((size, size))
    mask = ImageChops.add(horizontal, vertical, scale=2) if diagonal else vertical
    return Image.composite(Image.new("RGB", (size, size), b), Image.new("RGB", (size, size), a), mask)


def _radial(size: int, centre: tuple, edge: tuple) -> Image.Image:
    mask = Image.radial_gradient("L").resize((size, size))  # black in the middle, white at the edge
    return Image.composite(Image.new("RGB", (size, size), edge), Image.new("RGB", (size, size), centre), mask)


def logo(size: int = 256, state: str = "ok", dot: str | None = None) -> Image.Image:
    """The mark on a transparent background. `state`: ok (cyan/blue), idle (muted), alert (amber). `dot`: a colour for a status dot."""
    big = size * SCALE
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ring_colours = {"ok": (CYAN, BLUE), "idle": (GREY, (84, 98, 124)), "alert": (AMBER, (249, 115, 22))}[state]
    core_colours = {"ok": ((190, 240, 255), (22, 118, 230)), "idle": ((190, 200, 218), (84, 98, 124)), "alert": ((255, 236, 200), (240, 130, 40))}[state]
    small = size <= 64  # a heavier ring and bigger core so it still reads at 16 px
    c, w = big / 2, big * (0.115 if small else 0.075)

    # a soft glow behind everything
    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([big * 0.18, big * 0.18, big * 0.82, big * 0.82], fill=ring_colours[0] + (70,))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(big * 0.07)))

    # the ring: three quarters of a circle, so it reads as a "scanner"
    box = [big * 0.08, big * 0.08, big * 0.92, big * 0.92]  # the stroke grows inward from this box
    arc = Image.new("L", (big, big), 0)
    ImageDraw.Draw(arc).arc(box, start=-60, end=205, fill=255, width=int(w))
    for angle in (-60, 205):  # round the two ends
        import math

        r = (box[2] - box[0]) / 2 - w / 2  # the middle of the stroke
        x, y = c + r * math.cos(math.radians(angle)), c + r * math.sin(math.radians(angle))
        ImageDraw.Draw(arc).ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=255)
    canvas.paste(_gradient(big, *ring_colours), mask=arc)

    # a faint complete ring inside it
    thin = Image.new("L", (big, big), 0)
    ImageDraw.Draw(thin).ellipse([big * 0.27, big * 0.27, big * 0.73, big * 0.73], outline=0 if small else 110, width=max(2, int(big * 0.012)))
    canvas.paste(Image.new("RGB", (big, big), ring_colours[0]), mask=thin)

    # the core, with a highlight
    core_r = big * (0.21 if small else 0.17)
    core_mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(core_mask).ellipse([c - core_r, c - core_r, c + core_r, c + core_r], fill=255)
    canvas.paste(_radial(big, *core_colours), mask=core_mask)
    shine = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(shine).ellipse([c - core_r * 0.62, c - core_r * 0.7, c - core_r * 0.05, c - core_r * 0.2], fill=(255, 255, 255, 150))
    canvas.alpha_composite(shine.filter(ImageFilter.GaussianBlur(big * 0.006)))

    if dot:
        r, cx, cy = big * 0.15, big * 0.80, big * 0.80
        d = ImageDraw.Draw(canvas)
        d.ellipse([cx - r * 1.35, cy - r * 1.35, cx + r * 1.35, cy + r * 1.35], fill=(6, 12, 24, 255))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill={"green": GREEN, "amber": AMBER, "grey": GREY}[dot] + (255,))
    return canvas.resize((size, size), Image.LANCZOS)


def app_icon(size: int = 256) -> Image.Image:
    """The program icon: the mark on a dark rounded square."""
    big = size * SCALE
    tile = _gradient(big, NAVY_TOP, NAVY_BOTTOM, diagonal=False).convert("RGBA")
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=255)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    out.paste(tile, mask=mask)
    edge = Image.new("L", (big, big), 0)
    ImageDraw.Draw(edge).rounded_rectangle([2, 2, big - 3, big - 3], radius=int(big * 0.22), outline=70, width=max(2, big // 90))
    out.paste(Image.new("RGB", (big, big), (120, 160, 220)), mask=edge)
    mark = logo(int(size * 0.74), "ok").resize((int(big * 0.74), int(big * 0.74)), Image.LANCZOS)
    out.alpha_composite(mark, (int(big * 0.13), int(big * 0.13)))
    return out.resize((size, size), Image.LANCZOS)


def tray_icon(state: str = "idle", online: bool = False) -> Image.Image:
    """64 px for the notification area. ok = a phone or the relay is connected; idle = running; alert = needs setup."""
    return logo(64, state, dot="green" if online else None)
