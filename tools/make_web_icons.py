"""Draw the phone page's PNG icons from the same logo as the PC app.   python tools/make_web_icons.py

Home-screen install needs real PNG icons (an SVG alone is not enough on every phone, and iPhone ignores SVG completely).
The files land in src/handoff/web/ and are committed, so this only has to be run again if the logo changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
sys.path.insert(0, str(ROOT / "src"))

from handoff import branding  # noqa: E402


def full_bleed(size: int, mark_share: float) -> Image.Image:
    """A square with no rounded corners and the mark in the middle: what a phone masks into its own icon shape."""
    tile = branding._gradient(size, branding.NAVY_TOP, branding.NAVY_BOTTOM, diagonal=False).convert("RGBA")
    mark = branding.logo(int(size * mark_share), "ok")
    tile.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    return tile


def badge(size: int) -> Image.Image:
    """The small one-colour mark Android shows in the status bar: white where the logo is, see-through elsewhere."""
    # Drawn at twice the size and cut at a firm brightness, so the soft glow becomes a clean edge, then shrunk for smooth edges.
    alpha = branding.logo(size * 2, "ok").getchannel("A").point(lambda v: 255 if v > 200 else 0).resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    out.putalpha(alpha)
    return out


def main() -> None:
    branding.app_icon(192).save(WEB / "icon-192.png")
    branding.app_icon(512).save(WEB / "icon-512.png")
    full_bleed(512, 0.61).save(WEB / "icon-maskable-512.png")  # 61%: inside the middle 80% every phone's icon shape keeps
    full_bleed(180, 0.70).convert("RGB").save(WEB / "apple-touch-icon.png")
    badge(96).save(WEB / "badge-96.png")
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png", "badge-96.png"):
        print(name, (WEB / name).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
