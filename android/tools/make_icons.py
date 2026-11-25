"""Draw the phone app's icons from the same logo as the PC app.   python android/tools/make_icons.py"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "android" / "app" / "src" / "main" / "res"
sys.path.insert(0, str(ROOT / "src"))

from handoff import branding  # noqa: E402

LEGACY = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}  # the launcher icon, in dp * density
LAYER = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}  # adaptive icon layers are 108 dp


def save(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main() -> None:
    save(branding.logo(512, "ok"), RES / "drawable-nodpi" / "afp_logo.png")  # the picture on the first screen

    for density, size in LEGACY.items():
        icon = branding.app_icon(size)
        save(icon, RES / f"mipmap-{density}" / "ic_launcher.png")
        round_mask = Image.new("L", (size * 4, size * 4), 0)
        ImageDraw.Draw(round_mask).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
        round_icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        round_icon.paste(icon, mask=round_mask.resize((size, size), Image.LANCZOS))
        save(round_icon, RES / f"mipmap-{density}" / "ic_launcher_round.png")

    for density, size in LAYER.items():
        # The mark sits in the middle 61% (the part every phone's icon shape keeps); the dark tile comes from the background colour.
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        mark = branding.logo(int(size * 0.61), "ok")
        layer.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        save(layer, RES / f"drawable-{density}" / "ic_launcher_foreground.png")

        # The one-colour version some phones use for "themed icons".
        mono = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        alpha = layer.getchannel("A").point(lambda v: 255 if v > 90 else 0)
        mono.paste(Image.new("RGBA", (size, size), (255, 255, 255, 255)), mask=alpha)
        save(mono, RES / f"drawable-{density}" / "ic_launcher_monochrome.png")

    # The small one-colour mark in the status bar and at the top of a notification: white where the logo is, see-through elsewhere.
    for density, size in {"mdpi": 24, "hdpi": 36, "xhdpi": 48, "xxhdpi": 72, "xxxhdpi": 96}.items():
        alpha = branding.logo(size * 4, "ok").getchannel("A").point(lambda v: 255 if v > 200 else 0).resize((size, size), Image.LANCZOS)
        stat = Image.new("RGBA", (size, size), (255, 255, 255, 0))
        stat.putalpha(alpha)
        save(stat, RES / f"drawable-{density}" / "ic_stat_afp.png")

    adaptive = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@drawable/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />\n'
        '</adaptive-icon>\n'
    )
    folder = RES / "mipmap-anydpi-v26"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ic_launcher.xml").write_text(adaptive, encoding="utf-8")
    (folder / "ic_launcher_round.xml").write_text(adaptive, encoding="utf-8")
    print("icons written under", RES)


if __name__ == "__main__":
    main()
