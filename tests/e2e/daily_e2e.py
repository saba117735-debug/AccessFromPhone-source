"""A real browser, a real PC app: the everyday tools on Home (quick controls, inbox, notes, reminders, watchers, recent and pinned, print, snap, briefing).

The keys that change this PC's volume, the printer and the webcam are replaced, so running it changes nothing on this PC.
Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/daily_e2e.py [screenshot folder]
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.daily import controls  # noqa: E402
from handoff.runtime import start  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG = 9392
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def shot(page, name):
    (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    os.environ["HANDOFF_DAILY__FOLDER"] = str(work / "daily")
    pressed = []
    controls.press_key = lambda vk, n=1: pressed.append((vk, n))
    controls.supported = lambda: True
    settings = base.make_settings(work)
    (settings.agent.allowed_folders[0] / "notes.txt").write_text("hello", encoding="utf-8")
    running = start(settings, quiet=True)

    class FakeVolume:
        level, muted = 64, False

        def get(self):
            return {"available": True, "level": self.level, "muted": self.muted}

        def set_level(self, percent):
            self.level = percent
            return self.get()

        def set_muted(self, muted):
            self.muted = muted
            return self.get()

    fake = FakeVolume()
    running.app.state.daily.volume = fake
    shell_calls = []
    screen = [70]

    def fake_shell(script):
        shell_calls.append(script)
        if "WmiSetBrightness" in script:
            screen[0] = int(script.split("[byte]")[1].split("}")[0])
            return ""
        return str(screen[0])

    running.app.state.daily._brightness_shell = fake_shell   # a laptop screen at 70%, never really changed
    from PIL import Image  # noqa: PLC0415
    import io  # noqa: PLC0415

    picture = io.BytesIO()
    Image.new("RGB", (640, 360), (30, 120, 200)).save(picture, "JPEG")
    running.app.state.screen.frame = lambda *a, **k: picture.getvalue()   # a plain picture, not this PC's real screen
    running.app.state.screen.start = lambda *a, **k: None                 # and no "someone is looking" window on this PC
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    url = f"http://127.0.0.1:{base.PORT}"
    for _ in range(50):
        try:
            httpx.get(url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test"}).json()["token"]
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1');")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
        t = Session(page, url, token, work)
        t.wait("!!document.querySelector('#home .dy-card')", "the everyday tools card", seconds=20)

        def sheet_text():
            return t.js("[...document.querySelectorAll('.sheet')].map(s => s.innerText).join('\\n')")

        def open_tool(name):
            t.click(f'#home .dy-tile[data-tool="{name}"]')
            time.sleep(0.6)

        def close_sheet():
            t.js("document.querySelector('.sheet .iconbtn[aria-label=Close]').click()")
            time.sleep(0.5)

        def click_starting(text):
            t.js(f"""(() => {{ const b = [...document.querySelectorAll('.sheet button')].find(b => b.textContent.trim().startsWith({json.dumps(text)}) && !b.hidden && b.offsetParent !== null); if (!b) throw new Error('no button {text}'); b.click(); }})()""")

        def type_into(selector, value):
            t.js(f"(() => {{ const a = document.querySelector({json.dumps(selector)}); a.value = {json.dumps(value)}; a.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

        def drag_left(selector, fraction):
            """A real mouse drag across a row, as a finger would: the page sees pointer events."""
            box = t.js(f"(() => {{ const r = document.querySelector({json.dumps(selector)}).getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }})()")
            x, y, w, h = box
            start_x, y = x + w - 12, y + h / 2
            page.call("Input.dispatchMouseEvent", type="mouseMoved", x=start_x, y=y)
            page.call("Input.dispatchMouseEvent", type="mousePressed", x=start_x, y=y, button="left", clickCount=1)
            steps = 12
            for i in range(1, steps + 1):
                page.call("Input.dispatchMouseEvent", type="mouseMoved", x=start_x - (w * fraction) * i / steps, y=y, button="left", buttons=1)
                time.sleep(0.02)
            page.call("Input.dispatchMouseEvent", type="mouseReleased", x=start_x - w * fraction, y=y, button="left", clickCount=1)
            time.sleep(0.8)

        def overflow():
            return t.js("document.documentElement.scrollWidth > window.innerWidth + 1 || [...document.querySelectorAll('.sheet, .sheet-body, #home')].some(e => e.scrollWidth > e.clientWidth + 1)")

        # ---------------------------------------------------------------- Home
        t.check("Home has the everyday tools card with its tiles", t.js("[...document.querySelectorAll('#home .dy-tile')].map(b => b.dataset.tool)") == ["inbox", "notes", "tasks", "watch", "recent", "snap", "print", "brief"])
        t.check("... a volume and a brightness slider and three media keys", t.js("document.querySelectorAll('#home .dy-slider').length") == 2 and t.js("document.querySelectorAll('#home .dy-media').length") == 3)
        t.wait("document.querySelector('#home .dy-slider[data-kind=volume] .dy-sv').textContent === '64%'", "the volume slider showing the PC's level", seconds=10)
        t.check("the sliders show the PC's real levels (volume 64%, brightness 70%)", t.js("document.querySelector('#home .dy-slider[data-kind=volume] input').value") == "64" and t.js("document.querySelector('#home .dy-slider[data-kind=brightness] .dy-sv').textContent") == "70%")
        t.check("the card does not push Home sideways", not overflow())
        t.check("every tile, media key and slider is at least 44px (easy to tap)", t.js("[...document.querySelectorAll('#home .dy-media, #home .dy-tile, #home .dy-slider')].every(b => b.getBoundingClientRect().height >= 44 && b.getBoundingClientRect().width >= 44)"))
        t.check("the aurora glow is switched on behind the page and the card is frosted", t.js("getComputedStyle(document.body, '::before').display") == "block" and t.js("(getComputedStyle(document.querySelector('#home .dy-card')).backdropFilter || getComputedStyle(document.querySelector('#home .dy-card')).webkitBackdropFilter || '').includes('blur')"))
        t.js("document.querySelector('#home .dy-card').scrollIntoView({block:'center'})")
        time.sleep(0.4)
        shot(page, "daily-home.png")

        # ---------------------------------------------------------------- the control centre
        t.js("(() => { const i = document.querySelector('#home .dy-slider[data-kind=volume] input'); i.value = '30'; i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true})); })()")
        for _ in range(40):
            if fake.level == 30:
                break
            time.sleep(0.1)
        t.check("dragging the volume slider sets the PC's real volume to 30%", fake.level == 30, str(fake.level))
        t.check("... and the slider shows 30%", t.js("document.querySelector('#home .dy-slider[data-kind=volume] .dy-sv').textContent") == "30%")
        t.click('#home .dy-slider[data-kind=volume] .dy-si')
        for _ in range(40):
            if fake.muted:
                break
            time.sleep(0.1)
        t.check("tapping the speaker icon mutes the PC", fake.muted is True)
        t.check("... and the slider says Muted", t.js("document.querySelector('#home .dy-slider[data-kind=volume] .dy-sv').textContent") == "Muted")
        t.click('#home .dy-slider[data-kind=volume] .dy-si')
        for _ in range(40):
            if not fake.muted:
                break
            time.sleep(0.1)
        t.check("tapping it again unmutes", fake.muted is False)
        t.js("(() => { const i = document.querySelector('#home .dy-slider[data-kind=brightness] input'); i.value = '40'; i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true})); })()")
        for _ in range(60):
            if screen[0] == 40:
                break
            time.sleep(0.1)
        t.check("releasing the brightness slider asks Windows for 40%", screen[0] == 40, str(screen))
        screen[0], fake.level = 58, 22   # the PC changes them itself: automatic brightness, the keyboard's volume keys
        t.wait("document.querySelector('#home .dy-slider[data-kind=brightness] .dy-sv').textContent === '58%' && document.querySelector('#home .dy-slider[data-kind=volume] .dy-sv').textContent === '22%'", "the sliders catching up with the PC's own changes", seconds=25)
        t.check("the sliders follow changes made on the PC itself", True)
        t.click('#home .dy-media[aria-label="Play or pause"]')
        time.sleep(0.8)
        t.check("the play key sends play/pause once to the PC", pressed[-1] == (0xB3, 1), str(pressed))
        t.click('#home .dy-media[aria-label="Next"]')
        time.sleep(0.8)
        t.check("the next key sends next-track", pressed[-1] == (0xB0, 1), str(pressed))

        # ---------------------------------------------------------------- inbox
        open_tool("inbox")
        t.wait("!!document.querySelector('.sheet .dy-text')", "the inbox", seconds=10)
        t.check("an empty inbox shows a picture and says what it is for", "Nothing sent yet" in sheet_text() and t.js("!!document.querySelector('.sheet .art')"))
        type_into(".sheet .dy-text", "https://example.com/hello")
        t.click_text("Send", "document.querySelector('.sheet')")
        t.wait("!!document.querySelector('.sheet .dy-row')", "the sent link in the list", seconds=10)
        t.check("a link sent to the PC is listed as a link with Open here and Open on PC", all(w in sheet_text() for w in ("example.com/hello", "Open here", "Open on PC")))
        t.js("(() => { const f = document.querySelector('.sheet input[type=file]'); const dt = new DataTransfer(); dt.items.add(new File(['hello file'], 'plan.txt')); f.files = dt.files; f.dispatchEvent(new Event('change')); })()")
        t.wait("document.querySelectorAll('.sheet .dy-row:not(.dy-up)').length === 2", "the sent file in the list", seconds=10)
        t.check("a file sent from the phone lands in the PC's Inbox folder", any(p.name.endswith("_plan.txt") and p.read_text() == "hello file" for p in (work / "daily" / "Inbox").iterdir()))
        t.check("the inbox does not overflow the phone", not overflow())
        shot(page, "daily-inbox.png")
        t.js("(() => { const host = document.querySelector('.sheet').closest('.sheet-wrap') || document.querySelector('.sheet'); const dt = new DataTransfer(); dt.items.add(new File(['dropped'], 'dropped.txt')); const e = new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true}); document.querySelector('.sheet').dispatchEvent(new DragEvent('dragenter', {dataTransfer: dt, bubbles: true, cancelable: true})); document.querySelector('.sheet').dispatchEvent(e); })()")
        for _ in range(60):
            if any(p.name.endswith("_dropped.txt") for p in (work / "daily" / "Inbox").iterdir()):
                break
            time.sleep(0.2)
        t.check("a file dropped on the sheet is sent too", any(p.name.endswith("_dropped.txt") and p.read_text() == "dropped" for p in (work / "daily" / "Inbox").iterdir()))
        t.wait("document.querySelectorAll('.sheet .dy-row:not(.dy-up)').length === 3 && !document.querySelector('.sheet .dy-up')", "the list after the upload bars clear", seconds=10)
        (work / "daily" / "To phone").mkdir(parents=True, exist_ok=True)
        (work / "daily" / "To phone" / "menu.pdf").write_bytes(b"%PDF")
        close_sheet()
        open_tool("inbox")
        t.wait("!!document.querySelector('.sheet .seg')", "the inbox again", seconds=10)
        click_starting("To this phone")
        t.wait("document.querySelector('.sheet').innerText.includes('menu.pdf')", "the file waiting for the phone", seconds=10)
        t.check("a file dropped in To phone shows up with a Save button", "menu.pdf" in sheet_text() and "Save" in sheet_text())
        click_starting("To the PC")
        t.wait("document.querySelectorAll('.sheet .swp').length === 3", "the sent items back in the list", seconds=10)
        drag_left(".sheet .swp .swp-fg", 0.9)
        t.wait("document.querySelectorAll('.sheet .swp').length === 2", "the swiped item gone", seconds=10)
        t.check("swiping an item all the way left deletes it on the PC", len(list((work / "daily" / "Inbox").iterdir())) <= 2 and len(httpx.get(url + "/api/daily/inbox", headers={"X-Token": token}).json()["to_pc"]) == 2)
        close_sheet()

        # ---------------------------------------------------------------- notes
        open_tool("notes")
        t.wait("!!document.querySelector('.sheet .dy-text')", "notes", seconds=10)
        type_into(".sheet .dy-text", "call the bank")
        t.click_text("Save", "document.querySelector('.sheet')")
        t.wait("document.querySelector('.sheet').innerText.includes('call the bank')", "the saved note in the list", seconds=10)
        t.check("a note is saved as a text file on the PC", any("call the bank" in p.read_text(encoding="utf-8") for p in (work / "daily" / "Notes").glob("*.md")))
        t.check("the notes sheet groups notes under Today", "today" in sheet_text().lower())
        shot(page, "daily-notes.png")
        close_sheet()

        # ---------------------------------------------------------------- reminders
        open_tool("tasks")
        t.wait("!!document.querySelector('.sheet .dy-form')", "the reminders form", seconds=10)
        t.check("the new-reminder form starts folded away behind a button", not t.js("document.querySelector('.sheet .dy-form').classList.contains('is-open')"))
        t.click_text("New reminder or timer", "document.querySelector('.sheet')")
        time.sleep(0.6)
        t.check("... and opens when the button is tapped", t.js("document.querySelector('.sheet .dy-form').classList.contains('is-open')"))
        type_into(".sheet .dy-form input[type=text]", "stretch your legs")
        t.click_text("In 1 hour", "document.querySelector('.sheet')")
        t.click_text("Add", "document.querySelector('.sheet')")
        t.wait("document.querySelector('.sheet').innerText.includes('stretch your legs')", "the reminder in the list", seconds=10)
        t.check("the reminder is listed with its time", "today" in sheet_text().lower() or "tomorrow" in sheet_text().lower())
        t.check("the PC really has it scheduled", httpx.get(url + "/api/daily/tasks", headers={"X-Token": token}).json()["tasks"][0]["text"] == "stretch your legs")
        t.js("document.querySelector('.sheet .dy-form').classList.add('is-open')")
        chips = t.js("[...document.querySelectorAll('.sheet .dy-form .dy-chip')].map(c => c.textContent.trim()).join('|')")
        t.check("the form offers lock and sleep (what this PC allows) but not shutdown", "Lock" in chips and "Sleep" in chips and "Shut" not in chips, chips)
        shot(page, "daily-tasks.png")
        drag_left(".sheet .swp .swp-fg", 0.9)
        t.wait("!document.querySelector('.sheet').innerText.includes('stretch your legs')", "the reminder removed", seconds=10)
        t.check("swiping the reminder away cancels it on the PC", httpx.get(url + "/api/daily/tasks", headers={"X-Token": token}).json()["tasks"] == [])
        close_sheet()

        # ---------------------------------------------------------------- watchers
        open_tool("watch")
        t.wait("!!document.querySelector('.sheet .dy-form')", "the watcher form", seconds=10)
        t.click_text("Watch something", "document.querySelector('.sheet')")
        time.sleep(0.6)
        t.click_text("PC goes quiet", "document.querySelector('.sheet')")
        time.sleep(0.4)
        t.click_text("Watch", "document.querySelector('.sheet .dy-form')")
        t.wait("document.querySelector('.sheet').innerText.includes('go quiet')", "the watcher in the list", seconds=10)
        t.check("a 'PC goes quiet' watcher is listed", "go quiet" in sheet_text())
        shot(page, "daily-watch.png")
        close_sheet()

        # ---------------------------------------------------------------- recent and pinned
        open_tool("recent")
        t.wait("!!document.querySelector('.sheet .seg')", "recent and pinned", seconds=15)
        t.check("pinned and recent are two lists", "Pinned" in sheet_text() and "Recent" in sheet_text())
        t.check("usual folders can be pinned in one tap", t.js("!!document.querySelector('.sheet .dy-shelf')"))
        t.js("document.querySelector('.sheet .dy-pin').click()")
        t.wait("document.querySelectorAll('.sheet .swp').length >= 1", "the pinned place", seconds=10)
        t.check("a pinned folder shows in the list", t.js("document.querySelectorAll('.sheet .swp').length") >= 1)
        shot(page, "daily-recent.png")
        close_sheet()

        # ---------------------------------------------------------------- briefing
        open_tool("brief")
        t.wait("!!document.querySelector('.sheet .dy-lines p')", "the briefing", seconds=20)
        t.check("the briefing has sentences about the PC", t.js("document.querySelectorAll('.sheet .dy-lines p').length") >= 1)
        t.check("... and mentions what is being watched", "Watching 1 thing" in sheet_text())
        shot(page, "daily-brief.png")
        close_sheet()

        # ---------------------------------------------------------------- snap
        open_tool("snap")
        t.wait("!!document.querySelector('.sheet .btn.primary')", "snap", seconds=10)
        t.check("the webcam is explained as off", "webcam is off" in sheet_text())
        t.click_text("Screenshot of the PC", "document.querySelector('.sheet')")
        t.wait("!!document.querySelector('.sheet .dy-pic')", "the screenshot picture", seconds=10)
        t.check("the owner's own phone gets the picture without asking, with a Save button", t.js("document.querySelector('.sheet .dy-pic').complete && document.querySelector('.sheet .dy-pic').naturalWidth > 0") and "Save to this phone" in sheet_text())
        t.check("a copy is kept in the PC's Screenshots folder", len(list((work / "daily" / "Screenshots").glob("screenshot-*.jpg"))) == 1)
        shot(page, "daily-snap.png")

        # ---------------------------------------------------------------- print
        for _ in range(3):   # the picture and the Snap sheet under it
            if t.js("!!document.querySelector('.sheet-wrap:not(.out) .iconbtn[aria-label=Close]')"):
                t.js("document.querySelector('.sheet-wrap:not(.out) .iconbtn[aria-label=Close]').click()")
                time.sleep(0.6)
        open_tool("print")
        t.wait("!!document.querySelector('.sheet .dy-printer') || !!document.querySelector('.sheet .art')", "print", seconds=30)
        t.check("print says which printer it will use, or that there is none", t.js("!!document.querySelector('.sheet .dy-printer')") and ("Ready" in sheet_text() or "No default printer" in sheet_text() or "Offline" in sheet_text()), sheet_text()[:200])
        t.check("the print sheet does not overflow", not overflow())
        close_sheet()

        # ---------------------------------------------------------------- the same card in a light theme and a narrow phone
        page.call("Emulation.setDeviceMetricsOverride", width=320, height=640, deviceScaleFactor=2, mobile=False)
        t.js("document.documentElement.dataset.theme = 'light'")
        time.sleep(0.5)
        t.js("document.querySelector('#home .dy-card').scrollIntoView({block:'center'})")
        t.check("at 320 pixels wide the everyday tools card fits", t.js("(() => { const c = document.querySelector('#home .dy-card'); return c.scrollWidth <= c.clientWidth + 1 && [...c.querySelectorAll('*')].every(e => e.getBoundingClientRect().right <= c.getBoundingClientRect().right + 1); })()"))
        shot(page, "daily-home-narrow-light.png")
    finally:
        proc.terminate()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    failures = t.failures if t else ["the browser never started"]
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
