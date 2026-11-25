"""The "Connect your phone" page: a big QR code on the PC's screen instead of one squeezed into the terminal."""
from __future__ import annotations

import html
import io
import webbrowser
from pathlib import Path

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect your phone</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #060b16; color: #e6f1ff; font: 16px/1.5 system-ui, sans-serif; }}
  main {{ width: min(92vw, 520px); padding: 32px; text-align: center; }}
  h1 {{ margin: 0 0 6px; font-size: 1.6rem; }}
  p {{ margin: 0 0 18px; color: #9db2cf; }}
  .qr {{ display: inline-block; padding: 18px; background: #fff; border-radius: 18px; }}
  .qr svg {{ display: block; width: min(70vw, 320px); height: auto; }}
  ol {{ text-align: left; margin: 22px auto 0; padding-left: 22px; color: #c9d8ee; }}
  li {{ margin: 6px 0; }}
  code {{ display: block; margin-top: 18px; padding: 10px 12px; background: #0d1626; border-radius: 10px; color: #7fd4ff; word-break: break-all; font-size: .8rem; text-align: left; }}
  .warn {{ margin-top: 18px; font-size: .85rem; color: #f0b86a; }}
</style></head>
<body><main>
  <h1>Connect your phone</h1>
  <p>Open your phone's camera and point it at this code.</p>
  <div class="qr">{svg}</div>
  <ol>
    <li>Phone and PC must be on the same Wi-Fi.</li>
    <li>Tap the link the camera shows.</li>
    <li>{cert_step}</li>
  </ol>
  <code>{link}</code>
  <p class="warn">This code signs in to your PC. Keep it private and close this page when you are done.</p>
</main></body></html>
"""


def qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=1)
    buf = io.BytesIO()
    img.save(buf)
    out = buf.getvalue().decode("utf-8")
    return out[out.index("<svg"):]


def write_connect_page(link: str, folder: Path, https: bool) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    step = (
        'The phone warns once that the connection is "not private": choose Advanced, then Continue.'
        if https else "The page opens and you are signed in."
    )
    target = folder / "connect.html"
    target.write_text(_PAGE.format(svg=qr_svg(link), link=html.escape(link), cert_step=step), encoding="utf-8")
    return target


def open_connect_page(link: str, folder: Path, https: bool) -> Path | None:
    """Write the page and show it in the PC's browser. Never raises: the terminal link still works."""
    try:
        target = write_connect_page(link, folder, https)
        webbrowser.open(target.as_uri())
        return target
    except Exception:
        return None
