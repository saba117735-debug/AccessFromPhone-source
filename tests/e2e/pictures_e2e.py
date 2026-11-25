"""A real browser, a real server, real pictures: open a folder as a gallery, view a picture, edit it, save it, and check the saved pixels.

Not part of the normal test run (needs Edge or Chrome, takes a couple of minutes):  python tests/e2e/pictures_e2e.py [screenshot folder]
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
RED, GREEN, BLUE, YELLOW = (220, 30, 30), (30, 200, 60), (40, 60, 220), (240, 220, 30)


def make_pictures(folder: Path) -> None:
    """Pictures with known pixels, so what the editor does can be checked afterwards."""
    quadrants = Image.new("RGB", (1200, 800))
    for (x, y), colour in {(0, 0): RED, (600, 0): GREEN, (0, 400): BLUE, (600, 400): YELLOW}.items():
        quadrants.paste(Image.new("RGB", (600, 400), colour), (x, y))
    quadrants.save(folder / "photo.png")
    checker = Image.new("RGB", (600, 400), "white")
    for cy in range(0, 400, 20):
        for cx in range(0, 600, 20):
            if (cx // 20 + cy // 20) % 2 == 0:
                checker.paste((0, 0, 0), (cx, cy, cx + 20, cy + 20))
    checker.save(folder / "checker.png")
    Image.new("RGB", (600, 400), (128, 128, 128)).save(folder / "flat.png")
    Image.new("RGB", (640, 480), (60, 120, 200)).save(folder / "trip.jpg", quality=92)
    Image.new("P", (200, 150), 3).save(folder / "anim.gif")
    for n in range(1, 9):
        Image.new("RGB", (300 + n * 10, 200), (n * 28, 90, 255 - n * 25)).save(folder / f"Screenshot ({n}).png")
    (folder / "notes.txt").write_text("not a picture", encoding="utf-8")


class Session:
    def __init__(self, page, base_url, token, folder):
        self.page, self.base_url, self.token, self.folder = page, base_url, token, folder
        self.failures: list[str] = []

    def check(self, name, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            self.failures.append(name)

    def js(self, expression):
        return self.page.js(expression)

    def wait(self, expression, what, seconds=15):
        self.page.wait(expression, seconds, what)

    def click(self, selector):
        self.js(f"document.querySelector({json.dumps(selector)}).click()")

    def click_text(self, text, scope="document"):
        found = self.js(f"""(() => {{ const b = [...{scope}.querySelectorAll('button')].find(b => b.textContent.trim() === {json.dumps(text)} && !b.hidden && b.offsetParent !== null); if (!b) return false; b.click(); return true; }})()""")
        if not found:
            raise AssertionError(f"no visible button called {text!r}")

    def tool(self, name):
        self.click(f'.ed-tool[data-tool="{name}"]')
        time.sleep(0.25)

    def mouse(self, kind, x, y, buttons=1):
        self.page.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left" if kind != "mouseMoved" else "none", buttons=buttons if kind != "mouseReleased" else 0, clickCount=1)

    def drag(self, p1, p2, steps=8):
        self.mouse("mouseMoved", *p1, buttons=0)
        self.mouse("mousePressed", *p1)
        for i in range(1, steps + 1):
            self.mouse("mouseMoved", p1[0] + (p2[0] - p1[0]) * i / steps, p1[1] + (p2[1] - p1[1]) * i / steps)
            time.sleep(0.01)
        self.mouse("mouseReleased", *p2)
        time.sleep(0.25)

    def tap(self, p):
        self.mouse("mouseMoved", *p, buttons=0)
        self.mouse("mousePressed", *p)
        self.mouse("mouseReleased", *p)
        time.sleep(0.3)

    def size(self):
        s = self.js("document.querySelector('.ed-canvas').dataset.size")
        return tuple(int(v) for v in s.split("x"))

    def to_screen(self, x, y):
        """Where a point of the picture (in its own pixels) is on the screen, with the picture fitted to the stage."""
        r = self.js("(() => { const r = document.querySelector('.ed-canvas').getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; })()")
        iw, ih = self.size()
        s = min((r[2] - 28) / iw, (r[3] - 28) / ih)  # the picture is fitted with a 14 px margin all round
        return r[0] + (r[2] - iw * s) / 2 + x * s, r[1] + (r[3] - ih * s) / 2 + y * s

    def api(self, method, path, **kw):
        return httpx.request(method, self.base_url + "/api" + path, headers={"X-Token": self.token}, timeout=30, **kw)

    def open_editor(self, name):
        """Open a picture straight into the editor by asking in plain words, the way a person would."""
        self.say(f"edit {self.folder / name}")
        self.wait("!!document.querySelector('.ed-canvas')", f"the editor for {name}")
        self.wait("!!document.querySelector('.ed-canvas').dataset.size", "the picture to be ready")
        time.sleep(0.4)

    def say(self, text):
        self.js("""(() => { const f = document.querySelector('.field'); f.focus(); f.value = %s; f.dispatchEvent(new Event('input', {bubbles: true})); })()""" % json.dumps(text))
        time.sleep(0.3)
        self.js("""(() => { const b = document.querySelector('.send, .send-btn, button[aria-label*="Send"]'); if (b) b.click(); })()""")

    def close_panel(self):
        self.js("document.querySelector('#canvas .cv-head .iconbtn').click()")
        time.sleep(0.5)

    def save_copy(self, expected):
        """Save a copy and wait for the notice that names exactly this file (an older notice may still be on screen)."""
        self.click(".ed-save")
        self.wait("!!document.querySelector('.ed-save-card')", "the save sheet")
        time.sleep(0.5)
        self.click_text("Save a copy", "document.querySelector('.ed-save-card')")
        self.wait(f"document.querySelector('#toasts').textContent.includes({json.dumps('Saved ' + expected)})", f"{expected} to be saved")
        time.sleep(0.4)


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    folder = settings.agent.allowed_folders[0]
    make_pictures(folder)
    originals = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    running = start(settings, quiet=True)
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    url = f"http://127.0.0.1:{base.PORT}"
    for _ in range(50):
        try:
            httpx.get(url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test"}).json()["token"]
    proc = subprocess.Popen([str(browser), "--headless=new", "--remote-debugging-port=9343", f"--user-data-dir={work / 'profile'}", "--window-size=460,980", "--no-first-run", "--disable-gpu", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(9343)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)  # a phone-sized screen
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_tab','chat');")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 15, "the app")
        time.sleep(1)
        t = Session(page, url, token, folder)

        # ---------------------------------------------------------------- the gallery
        t.say("open any photo file")
        t.wait("document.querySelectorAll('.gl-tile').length > 0", "the gallery")
        t.check("'open any photo file' opens a gallery of the pictures (and only pictures)", t.js("document.querySelectorAll('.gl-tile').length") == 13, t.js("document.querySelectorAll('.gl-tile').length"))
        t.check("the gallery says how many there are", "13 pictures" in t.js("document.querySelector('.im-count').textContent"))
        t.wait("document.querySelectorAll('.gl-tile.ready').length >= 8", "thumbnails to load")
        t.check("thumbnails load", True)
        t.js("""(() => { const s = document.querySelector('.gl-bar input'); s.value = 'photo'; s.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        time.sleep(0.3)
        t.check("searching by name narrows it down", t.js("document.querySelectorAll('.gl-tile').length") == 1)
        t.js("""(() => { const s = document.querySelector('.gl-bar input'); s.value = ''; s.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        t.js("""(() => { const s = document.querySelector('.im-select'); s.value = 'name'; s.dispatchEvent(new Event('change', {bubbles: true})); })()""")
        time.sleep(0.3)
        names = t.js("[...document.querySelectorAll('.gl-tile')].map(b => b.getAttribute('aria-label'))")
        expected = ["anim.gif", "checker.png", "flat.png", "photo.png"] + [f"Screenshot ({n}).png" for n in range(1, 9)] + ["trip.jpg"]
        t.check("sorting by name puts them in order", names == expected, names)
        (OUT / "g1-gallery.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        # ---------------------------------------------------------------- the viewer
        t.click('.gl-tile[aria-label="photo.png"]')
        t.wait("!!document.querySelector('.iv-img.ready')", "the viewer")
        time.sleep(0.8)
        t.check("a tap on a picture opens the viewer", t.js("document.querySelector('.cv-title b').textContent") == "photo.png")
        t.check("it shows the picture's size and place in the folder", "1200×800" in t.js("document.querySelector('.cv-title .cv-path').textContent") and " of 13" in t.js("document.querySelector('.cv-title .cv-path').textContent"), t.js("document.querySelector('.cv-title .cv-path').textContent"))
        (OUT / "v1-viewer.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        before = t.js("document.querySelector('.iv-img').style.transform")
        t.click(".iv-zoom .iconbtn:nth-child(1)")
        time.sleep(0.5)
        zoomed = t.js("document.querySelector('.iv-img').style.transform")
        t.check("zoom in changes the picture's scale", zoomed != before and float(zoomed.split("scale(")[1].rstrip(")")) > float(before.split("scale(")[1].rstrip(")")))
        t.click(".iv-zoom .iconbtn:nth-child(3)")
        time.sleep(0.5)
        t.check("'fit' brings it back", t.js("document.querySelector('.iv-img').style.transform") == before)
        t.click(".iv-next")  # this is the oldest picture, so there is no next one
        time.sleep(0.5)
        t.check("... and stays where it is", t.js("document.querySelector('.cv-title b').textContent") == "photo.png" and "last picture" in t.js("document.querySelector('#toasts').textContent"))
        t.click(".iv-prev")
        t.wait("document.querySelector('.cv-title b').textContent !== 'photo.png'", "the previous picture")
        t.check("previous moves to a newer picture", t.js("document.querySelector('.cv-title b').textContent") != "photo.png")
        t.click(".iv-next")
        t.wait("document.querySelector('.cv-title b').textContent === 'photo.png'", "back to the first picture")
        t.check("next comes back", True)
        t.check("a strip of the other pictures is shown", t.js("document.querySelectorAll('.iv-thumb').length") == 13)
        t.click('.iv-act[aria-label="Details"]')
        time.sleep(0.4)
        t.check("details show the size and the folder", "1200 × 800" in t.js("document.querySelector('.im-card').textContent") and str(folder) in t.js("document.querySelector('.im-card').textContent"))
        t.click_text("Done", "document.querySelector('.im-card')")

        # ---------------------------------------------------------------- the editor: turn, crop, draw, write, hide
        t.click(".iv-edit")
        t.wait("!!document.querySelector('.ed-canvas') && !!document.querySelector('.ed-canvas').dataset.size", "the editor")
        time.sleep(0.5)
        t.check("Edit opens the editor on the same picture", t.size() == (1200, 800) and "Edit photo.png" in t.js("document.querySelector('.ed-head .cv-title b').textContent"))
        t.check("nothing is changed yet, so Undo is off and it says so", t.js("document.querySelector('.ed-head .chip').textContent") == "No changes" and t.js("document.querySelector('.ed-head .iconbtn[aria-label=\"Undo\"]').disabled"))
        (OUT / "e1-editor.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        t.tool("rotate")
        t.click_text("Right", "document.querySelector('.ed-panel')")
        time.sleep(0.4)
        t.check("rotating right turns 1200×800 into 800×1200", t.size() == (800, 1200), t.size())
        t.check("and the editor says there are unsaved changes", t.js("document.querySelector('.ed-head .chip').textContent") == "Unsaved")

        t.tool("crop")
        t.click_text("Square", "document.querySelector('.ed-panel')")
        time.sleep(0.3)
        (OUT / "e2-crop.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        t.click_text("Apply crop", "document.querySelector('.ed-panel')")
        time.sleep(0.4)
        t.check("a square crop of the turned picture is 800×800", t.size() == (800, 800), t.size())

        t.tool("pen")
        t.click('.im-swatch[data-c="#ffffff"]')
        t.js("""(() => { const r = document.querySelector('.ed-panel input[type=range]'); r.value = 40; r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); })()""")
        t.drag(t.to_screen(120, 400), t.to_screen(680, 400))
        t.check("drawing adds a stroke (the stroke is undoable)", not t.js("document.querySelector('.ed-head .iconbtn[aria-label=\"Undo\"]').disabled"))

        t.tool("text")
        t.tap(t.to_screen(400, 150))
        t.wait("!!document.querySelector('.ed-text')", "the text box")
        t.js("""(() => { const a = document.querySelector('.ed-text'); a.value = 'HELLO'; a.dispatchEvent(new Event('input', {bubbles: true})); a.dispatchEvent(new Event('change', {bubbles: true})); })()""")
        time.sleep(0.4)
        t.check("tapping with the Text tool puts words on the picture and selects them", t.js("document.querySelector('.ed-panel').textContent").find("Text selected") >= 0)
        (OUT / "e3-annotated.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        # undo and redo
        t.click('.ed-head .iconbtn[aria-label="Undo"]')
        time.sleep(0.3)
        t.click('.ed-head .iconbtn[aria-label="Redo"]')
        time.sleep(0.3)
        t.check("undo then redo leaves the picture as it was (the redo is used up, the undo is there)",
                t.size() == (800, 800) and t.js("document.querySelector('.ed-head .iconbtn[aria-label=\"Redo\"]').disabled") and not t.js("document.querySelector('.ed-head .iconbtn[aria-label=\"Undo\"]').disabled"))

        t.save_copy("photo (edited).png")
        saved = folder / "photo (edited).png"
        t.check("Save a copy writes 'photo (edited).png' beside the original", saved.is_file())
        t.check("the original is untouched, byte for byte", hashlib.sha256((folder / "photo.png").read_bytes()).hexdigest() == originals["photo.png"])
        out = Image.open(saved).convert("RGB")
        t.check("the saved picture is 800×800", out.size == (800, 800), out.size)
        near = lambda a, b: all(abs(x - y) <= 6 for x, y in zip(a, b))
        t.check("turned right and cropped: blue top-left, red top-right", near(out.getpixel((60, 60)), BLUE) and near(out.getpixel((740, 60)), RED), (out.getpixel((60, 60)), out.getpixel((740, 60))))
        t.check("turned right and cropped: yellow bottom-left, green bottom-right", near(out.getpixel((60, 740)), YELLOW) and near(out.getpixel((740, 740)), GREEN), (out.getpixel((60, 740)), out.getpixel((740, 740))))
        t.check("the white stroke is in the picture", near(out.getpixel((400, 400)), (255, 255, 255)), out.getpixel((400, 400)))
        pixels = [out.getpixel((x, y)) for x in range(330, 480, 3) for y in range(95, 215, 3)]
        t.check("the words are in the picture", sum(1 for p in pixels if not any(near(p, c) for c in (BLUE, RED))) > 40)
        t.check("saving again as a copy would never overwrite (the name was free this time)", [p.name for p in folder.iterdir() if "edited" in p.name] == ["photo (edited).png"])
        (OUT / "e4-after-save.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        t.check("the editor says everything is saved", t.js("document.querySelector('.ed-head .chip').textContent") == "No changes")

        # closing with unsaved changes asks first
        t.tool("rotate")
        t.click_text("Left", "document.querySelector('.ed-panel')")
        time.sleep(0.3)
        t.js("document.querySelector('.ed-head .iconbtn[aria-label=\"Close the editor\"]').click()")
        t.wait("!!document.querySelector('.im-modal')", "the question")
        t.check("closing with unsaved changes asks what to do", "Close without saving?" in t.js("document.querySelector('.im-card h3').textContent"))
        t.click_text("Discard", "document.querySelector('.im-card')")
        t.wait("!!document.querySelector('.iv-img')", "back at the viewer")
        t.check("Discard goes back to the picture, without asking twice", True)
        t.close_panel()

        # ---------------------------------------------------------------- hiding, colours, filters, replacing the original
        t.open_editor("checker.png")
        t.tool("blur")
        t.drag(t.to_screen(150, 100), t.to_screen(450, 300))
        t.wait("document.querySelector('.ed-panel').textContent.includes('Hidden area selected')", "the hidden area to be selected")
        t.save_copy("checker (edited).png")
        blurred = Image.open(folder / "checker (edited).png").convert("RGB")
        inside = [blurred.getpixel((x, 200)) for x in range(200, 400, 5)]
        outside = [blurred.getpixel((x, 40)) for x in range(10, 590, 5)]
        t.check("a hidden area is pixelated: no pure black or white inside it", sum(1 for p in inside if p in ((0, 0, 0), (255, 255, 255))) < len(inside) * 0.3, inside[:6])
        t.check("and the rest of the picture is untouched", sum(1 for p in outside if p in ((0, 0, 0), (255, 255, 255))) == len(outside))
        t.close_panel()

        t.open_editor("flat.png")
        t.tool("adjust")
        t.js("""(() => { const r = document.querySelector('.ed-panel input[type=range]'); r.value = 60; r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); })()""")
        time.sleep(0.5)
        t.save_copy("flat (edited).png")
        bright = Image.open(folder / "flat (edited).png").convert("RGB").getpixel((100, 100))
        t.check("raising the brightness makes the picture lighter", bright[0] > 180, bright)
        t.close_panel()

        t.open_editor("photo.png")
        t.tool("filters")
        t.click_text("Mono", "document.querySelector('.ed-panel')")
        time.sleep(0.4)
        t.save_copy("photo (edited) (2).png")
        mono = Image.open(folder / "photo (edited) (2).png").convert("RGB")
        pts = [mono.getpixel(p) for p in ((100, 100), (900, 100), (100, 600), (900, 600))]
        t.check("the Mono filter makes every pixel grey, and a second copy gets a new name", all(abs(a - b) <= 2 and abs(b - c) <= 2 for a, b, c in pts), pts)
        t.close_panel()

        t.open_editor("flat.png")
        t.tool("adjust")
        t.js("""(() => { const r = document.querySelector('.ed-panel input[type=range]'); r.value = -50; r.dispatchEvent(new Event('input', {bubbles: true})); r.dispatchEvent(new Event('change', {bubbles: true})); })()""")
        time.sleep(0.4)
        t.click(".ed-save")
        t.wait("!!document.querySelector('.ed-save-card')", "the save sheet")
        time.sleep(0.4)
        t.click_text("Replace the original", "document.querySelector('.ed-save-card')")
        t.wait("document.querySelector('#toasts').textContent.includes('over the original')", "the original to be replaced")
        dark = Image.open(folder / "flat.png").convert("RGB").getpixel((100, 100))
        t.check("'Replace the original' changes the original file", dark[0] < 100 and hashlib.sha256((folder / "flat.png").read_bytes()).hexdigest() != originals["flat.png"], dark)
        job = [j for j in t.api("GET", "/jobs").json() if j["prompt"].startswith("Edited picture flat.png")][0]
        t.check("the save is recorded, with what was done", "colours adjusted" in job["outputs"][0], job["outputs"])
        undone = t.api("POST", f"/jobs/{job['id']}/undo").json()
        t.check("Undo puts the original back, byte for byte", undone["status"] == "undone" and hashlib.sha256((folder / "flat.png").read_bytes()).hexdigest() == originals["flat.png"])
        t.close_panel()

        # a saved-over file elsewhere is noticed
        t.open_editor("trip.jpg")
        t.tool("rotate")
        t.click_text("Right", "document.querySelector('.ed-panel')")
        Image.new("RGB", (640, 480), (9, 9, 9)).save(folder / "trip.jpg")  # someone else changes it meanwhile
        t.click(".ed-save")
        t.wait("!!document.querySelector('.ed-save-card')", "the save sheet")
        time.sleep(0.4)
        t.click_text("Replace the original", "document.querySelector('.ed-save-card')")
        t.wait("!!document.querySelector('.im-modal h3') && [...document.querySelectorAll('.im-modal h3')].some(h => h.textContent.includes('changed on your PC'))", "the warning")
        t.check("a picture changed on the PC meanwhile is not silently overwritten", Image.open(folder / "trip.jpg").convert("RGB").getpixel((5, 5)) == (9, 9, 9))
        (OUT / "e5-conflict.png").write_bytes(__import__("base64").b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
        t.click_text("Keep editing", "[...document.querySelectorAll('.im-modal')].pop()")
        time.sleep(0.3)
    finally:
        proc.terminate()
        try:
            running.stop()
        except Exception:
            pass
    failures = t.failures if t else ["could not start"]
    print(f"\n{'all passed' if not failures else str(len(failures)) + ' FAILED: ' + '; '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
