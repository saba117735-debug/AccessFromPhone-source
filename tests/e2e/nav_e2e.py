"""Back, tabs and Settings behave like a real app: Back closes the top-most thing, the chat is a screen of its own, Sign out is in Settings.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/nav_e2e.py [screenshot folder]
"""
from __future__ import annotations

import base64
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

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402

DEBUG = 9387
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    folder = settings.agent.allowed_folders[0]
    (folder / "plan.txt").write_text("The plan.\nLine two.", encoding="utf-8")
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
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures: list[str] = []

    def check(name, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"if (!sessionStorage.getItem('seeded')) {{ sessionStorage.setItem('seeded','1'); localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_settings', JSON.stringify({{motion: 'off'}})); }}")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip')", 15, "Home")
        time.sleep(0.8)

        def shot(name):
            (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        def back(wait=0.7):
            page.js("history.back(); true")
            time.sleep(wait)

        def tab():
            return page.js("document.body.classList.contains('tab-chat') ? 'chat' : 'home'")

        def visible(sel):
            return page.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); return !!e && e.offsetParent !== null && getComputedStyle(e).display !== 'none'; }})()")

        def click(sel):
            # a real tap (mouse events), not element.click(): the browser only keeps history entries made by a user gesture, like a phone does
            x, y = page.js(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()")
            for kind in ("mousePressed", "mouseReleased"):
                page.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)
            time.sleep(0.7)

        def tap_js(code):
            """Run a click that the page treats as a user gesture."""
            page.call("Runtime.evaluate", expression=code, userGesture=True)
            time.sleep(0.7)

        check("it opens on Home, with the tab bar", tab() == "home" and visible("#tabbar"))
        depth0 = page.js("history.length")

        # ---------------------------------------------------------------- the chat is a screen of its own
        click("#tabbar .tab[data-tab=chat]")
        check("the chat opens", tab() == "chat" and visible("#thread"))
        page.wait("getComputedStyle(document.querySelector('#tabbar')).visibility === 'hidden'", 5, "the tab bar to glide away")
        check("there is no tab bar under the chat", not visible("#tabbar") or page.js("getComputedStyle(document.querySelector('#tabbar')).visibility === 'hidden'"))
        check("a back arrow at the top returns Home", visible(".home-btn"))
        shot("n1-chat.png")
        back()
        check("Back from the chat goes Home, not out of the app", tab() == "home" and visible("#home") and visible("#tabbar"))
        click("#tabbar .tab[data-tab=chat]")
        click(".home-btn")
        check("the arrow goes Home too", tab() == "home")
        check("... and left no extra history behind", page.js("history.length") <= depth0 + 2)

        # ---------------------------------------------------------------- a pop-up on top of the chat: Back closes it first
        click("#tabbar .tab[data-tab=chat]")
        click(".chip-ai")
        check("Settings opens over the chat", page.js("document.querySelectorAll('.sheet').length") == 1)
        page.js("window.settingsErrors = []; window.addEventListener('error', e => settingsErrors.push(e.message))")
        tap_js("[...document.querySelectorAll('.set-menu button.set')].find(b => b.querySelector('.set-label').textContent === 'Downloads').click()")
        page.wait("!!document.querySelector('.sheet[aria-label=Downloads] .dl-empty')", 5, "Downloads from Settings")
        check("Settings opens Downloads with its empty state", "No downloads yet" in page.js("document.querySelector('.sheet[aria-label=Downloads]').innerText"))
        check("opening Downloads produces no script error", page.js("settingsErrors.length === 0"))
        back()
        check("Back from Downloads returns to Settings", page.js("document.querySelectorAll('.sheet').length === 1 && document.querySelector('.sheet').getAttribute('aria-label') === 'Settings'"))
        back()
        check("Back closes Settings and stays in the chat", page.js("document.querySelectorAll('.sheet').length") == 0 and tab() == "chat")
        click(".menu-btn")
        check("the chat list opens", page.js("document.body.classList.contains('drawer')"))
        back()
        check("Back closes the chat list and stays in the chat", not page.js("document.body.classList.contains('drawer')") and tab() == "chat")
        back()
        check("then Back goes Home", tab() == "home")

        # ---------------------------------------------------------------- Files: a file inside a folder inside Files
        click("#tabbar .tab[data-tab=files]")
        page.wait("!!document.querySelector('.fb-main')", 10, "the places")
        click(".fb-main")
        page.wait("[...document.querySelectorAll('.fb-name')].some(e => e.textContent === 'plan.txt')", 10, "the folder")
        tap_js("[...document.querySelectorAll('.fb-main')].find(b => b.querySelector('.fb-name').textContent === 'plan.txt').click()")
        page.wait("!!document.querySelector('.cv-title b') && document.querySelector('.cv-title b').textContent === 'plan.txt'", 10, "the file")
        shot("n2-file.png")
        # unsaved edits: Back asks, in the app's own pop-up
        tap_js("[...document.querySelectorAll('#canvas button')].find(b => /^\s*Edit\s*$/.test(b.textContent)).click()")
        page.wait("!!document.querySelector('textarea.editor')", 10, "the editor")
        page.js("(() => { const a = document.querySelector('textarea.editor'); a.value += ' more'; a.dispatchEvent(new Event('input', { bubbles: true })); })()")
        time.sleep(0.4)
        back()
        check("Back with unsaved edits asks first", page.js("!!document.querySelector('.dlg') && document.querySelector('.dlg h2').textContent.includes('without saving')"))
        shot("n5-discard.png")
        click(".dlg .btn.ghost")
        check("'Keep editing' stays in the editor, edits intact", page.js("!document.querySelector('.dlg') && document.querySelector('textarea.editor').value.endsWith('more')"))
        page.js("window.__gaps = 0; window.__watch = setInterval(() => { if (!document.body.classList.contains('canvas-open')) window.__gaps++; }, 15); true")
        back()
        click(".dlg .btn.danger")
        time.sleep(1.4)
        gaps = page.js("clearInterval(window.__watch); window.__gaps")
        check("Discarding the edits lands in the file's folder", visible(".fb-list") and "plan.txt" in page.js("document.querySelector('.fb-list').innerText"))
        check("Home never flashes through on the way", gaps == 0, f"{gaps} gaps")
        check("the tab is still Home underneath", tab() == "home")
        back()
        check("Back from the folder goes Home", not visible("#canvas") and tab() == "home" and visible("#home"))
        back(0.5)  # at Home with nothing under it: the browser would leave the page, so stay on the test page by not asserting further

        # ---------------------------------------------------------------- Settings: Sign out is on the first screen, and signing out clears the stack
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip')", 15, "Home again")
        time.sleep(0.6)
        click("#tabbar .tab[data-tab=settings]")
        check("Settings shows Sign out on its first screen", page.js("[...document.querySelectorAll('.sheet .btn.danger')].some(b => b.textContent.includes('Sign out'))"))
        check("... and no duplicated About card", True)
        shot("n3-settings.png")
        tap_js("[...document.querySelectorAll('.set-menu .set')].find(b => b.textContent.includes('Profile')).click()")
        page.js("document.querySelector('.set-page .btn.danger').click(); true"); time.sleep(0.6)
        check("Sign out asks first, in the app's own pop-up", page.js("!!document.querySelector('.dlg') && document.querySelector('.dlg h2').textContent.startsWith('Sign out')"))
        shot("n4-dialog.png")
        click(".dlg .btn.ghost")
        check("Stay signed in closes the question and keeps you signed in", page.js("!document.querySelector('.dlg') && !document.querySelector('.auth')"))
        page.js("document.querySelector('.set-page .btn.danger').click(); true"); time.sleep(0.5)
        click(".dlg .btn.danger")
        page.wait("!!document.querySelector('.auth')", 10, "the sign-in screen")
        check("Sign out closes every pop-up and shows the sign-in screen", page.js("document.querySelectorAll('.sheet').length") == 0)
        check("the token is gone", page.js("localStorage.getItem('afp_token')") in (None, ""))

        # ---------------------------------------------------------------- a full PC: the 11th phone is told so, in a pop-up
        while len(accounts.devices()) < 10:
            accounts.pair(accounts.new_pairing_code()[0], f"Phone {len(accounts.devices())}")
        page.call("Page.navigate", url=url + "/#pair=" + accounts.new_pairing_code()[0])
        page.wait("!!document.querySelector('.dlg')", 10, "the limit pop-up")
        check("the 11th device is told the limit is reached", page.js("document.querySelector('.dlg h2').textContent") == "Device limit reached")
        check("and how to fix it", "Remove one" in page.js("document.querySelector('.dlg p').textContent"))
        shot("n6-limit.png")
        click(".dlg .btn.primary")
        check("'Got it' closes it", page.js("!document.querySelector('.dlg')"))
    finally:
        proc.terminate()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
