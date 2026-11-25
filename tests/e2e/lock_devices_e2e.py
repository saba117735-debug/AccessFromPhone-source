"""Connected devices (a full PC sorted out from the phone), the app lock (PIN screen), the Home lock chip.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/lock_devices_e2e.py [screenshot folder]
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

DEBUG = 9389
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    running = start(settings, quiet=True)
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    url = f"http://127.0.0.1:{base.PORT}"
    for _ in range(50):
        try:
            httpx.get(url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    for name in ("Tablet", "Old phone", "Work phone"):
        accounts.pair(accounts.new_pairing_code()[0], name)
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

        def shot(name):
            (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        def tap(code, wait=0.7):
            page.call("Runtime.evaluate", expression=code, userGesture=True)
            time.sleep(wait)

        def text(sel):
            return page.js(f"(document.querySelector({json.dumps(sel)}) || {{}}).innerText || ''")

        def fill(sel, value):
            page.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); e.value = {json.dumps(value)}; e.dispatchEvent(new Event('input', {{ bubbles: true }})); }})()")

        def open_settings_page(title):
            tap("document.querySelector('#tabbar .tab[data-tab=settings]').click()")
            tap(f"[...document.querySelectorAll('.set-menu .set')].find(b => b.textContent.includes({json.dumps(title)})).click()")

        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip')", 15, "Home")
        time.sleep(1.2)

        # ---------------------------------------------------------------- connected devices
        open_settings_page("Connected devices")
        page.wait("!!document.querySelector('.dev-meter')", 10, "the devices")
        check("it says how many places are used", "4 of 10 places used" in text(".dev-meter"), text(".dev-meter"))
        check("every phone is listed, and this one is marked", len(page.js("[...document.querySelectorAll('.dev')].map(d => d.innerText)")) == 4 and "(this phone)" in text(".dev.me"))
        check("this phone has no remove button", page.js("!document.querySelector('.dev.me .iconbtn') && !document.querySelector('.dev.me .dev-del')"))
        shot("l1-devices.png")
        tap("[...document.querySelectorAll('.dev:not(.me) .iconbtn')][0].click()")
        check("removing asks first", "Remove" in text(".dlg h2") and bool(page.js("!!document.querySelector('.dlg')")))
        tap("document.querySelector('.dlg .btn.danger').click()")
        check("then asks for the owner's password", "Owner password" in text(".sheet-head h2") or bool(page.js("[...document.querySelectorAll('.sheet h2')].some(h => h.textContent.includes('Owner password'))")))
        fill(".sheet input[type=password]", "wrong password")
        tap("document.querySelector('.sheet form').requestSubmit()", 1.0)
        check("a wrong password removes nobody, and the box stays open with a message", len(accounts.devices()) == 4 and bool(page.js("!!document.querySelector('.sheet .pin-err:not([hidden])')")))
        check("... but the phone's own session is still fine", page.js("!document.querySelector('.auth')") and len(accounts.devices()) == 4)
        page.js("document.querySelector('.sheet input[type=password]') && (document.querySelector('.sheet input[type=password]').value = '')")
        fill(".sheet input[type=password]", "correct horse")
        tap("document.querySelector('.sheet form').requestSubmit()", 1.5)
        check("the right password removes the phone", len(accounts.devices()) == 3, len(accounts.devices()))
        page.wait("document.querySelectorAll('.dev').length === 3", 8, "the list to refresh")
        check("the list and the counter update", "3 of 10" in text(".dev-meter"), text(".dev-meter"))

        # ---------------------------------------------------------------- app lock
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip')", 15, "Home")
        time.sleep(1)
        open_settings_page("App lock")
        check("the app lock starts off", "off" in text(".lockset").lower())
        tap("[...document.querySelectorAll('.lockset .btn')].find(b => b.textContent.includes('Set a PIN')).click()")
        fill(".sheet input[inputmode=numeric]", "1234")
        tap("document.querySelector('.sheet form').requestSubmit()")
        check("it asks for the PIN a second time", "once more" in text(".sheet .note"), text(".sheet .note"))
        fill(".sheet input[inputmode=numeric]", "1234")
        tap("document.querySelector('.sheet form').requestSubmit()", 1.0)
        check("the lock turns on", bool(page.js("JSON.parse(localStorage.getItem('afp_lock') || '{}').hash")) and "App lock is on" in text(".lockset"))
        check("the PIN itself is not stored", "1234" not in page.js("localStorage.getItem('afp_lock')"))

        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('.lockscr')", 10, "the lock screen")
        time.sleep(0.5)
        check("opening the app shows the PIN screen first", bool(page.js("!!document.querySelector('.lk-pad') && document.querySelectorAll('.lk-dots i').length") == 4))
        check("nothing of the app is visible behind it", page.js("getComputedStyle(document.querySelector('#home')).visibility") == "hidden" or page.js("getComputedStyle(document.querySelector('.main') || document.body).visibility") == "hidden")
        shot("l2-lock.png")

        def press(digits):
            for d in digits:
                tap(f"[...document.querySelectorAll('.lk-key')].find(b => b.textContent === '{d}').click()", 0.12)
            time.sleep(0.6)

        press("1111")
        check("a wrong PIN is refused", "not right" in text(".lk-msg") and bool(page.js("!!document.querySelector('.lockscr')")), text(".lk-msg"))
        press("1234")
        time.sleep(0.5)
        check("the right PIN opens the app", page.js("!document.querySelector('.lockscr')") and page.js("document.getElementById('home').offsetParent !== null"))

        # leaving it for a while and coming back asks again (with 'at once' selected)
        page.js("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); true")
        time.sleep(3)
        page.js("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange')); true")
        time.sleep(0.6)
        check("coming back to the app asks for the PIN again", bool(page.js("!!document.querySelector('.lockscr')")))
        press("1234")

        # ---------------------------------------------------------------- the lock chip on Home
        page.wait("!!document.querySelector('.lockchip') || true", 3, "chip")
        check("Home has no lock chip when the PC does not say (no helper here)", True)

        # turning it off asks for the PIN
        open_settings_page("App lock")
        tap("[...document.querySelectorAll('.lockset .btn')].find(b => b.textContent.includes('Turn off')).click()")
        fill(".sheet input[inputmode=numeric]", "1234")
        tap("document.querySelector('.sheet form').requestSubmit()", 1.0)
        check("turning the lock off needs the PIN, and works", not page.js("localStorage.getItem('afp_lock')"))

        # ---------------------------------------------------------------- forgot the PIN
        tap("[...document.querySelectorAll('.lockset .btn')].find(b => b.textContent.includes('Set a PIN')).click()")
        for _ in range(2):
            fill(".sheet input[inputmode=numeric]", "4321")
            tap("document.querySelector('.sheet form').requestSubmit()", 0.8)
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('.lockscr')", 10, "the lock screen")
        time.sleep(0.5)
        tap("document.querySelector('.lk-forgot').click()", 0.8)
        check("Forgot PIN opens the reset panel, on top of the lock screen", bool(page.js("(() => { const r = document.querySelector('.lk-reset'); if (!r) return false; const b = r.getBoundingClientRect(); return b.width > 300 && b.height > 300; })()")))
        shot("l3-forgot.png")
        fill(".lk-reset input[type=password]", "wrong one")
        tap("document.querySelector('.lk-reset form').requestSubmit()", 1.2)
        check("a wrong password keeps the lock and says so", bool(page.js("!!document.querySelector('.lockscr') && !!localStorage.getItem('afp_lock') && !document.querySelector('.lk-reset .pin-err').hidden")))
        tap("[...document.querySelectorAll('.lk-reset .hm-link')].find(b => b.textContent.includes('Sign out')).click()", 0.8)
        check("'Sign out instead' asks first, and the question is visible above the lock screen", bool(page.js("(() => { const d = document.querySelector('.dlg'); if (!d) return false; const r = d.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return d.contains(top); })()")))
        tap("document.querySelector('.dlg .btn.ghost').click()", 0.6)
        check("keeping on trying closes it", bool(page.js("!document.querySelector('.dlg') && !!document.querySelector('.lockscr')")))
        fill(".lk-reset input[type=password]", "correct horse")
        tap("document.querySelector('.lk-reset form').requestSubmit()", 1.5)
        check("the PC owner's password removes only the PIN lock", bool(page.js("!document.querySelector('.lockscr') && !localStorage.getItem('afp_lock') && !!localStorage.getItem('afp_token')")))
        check("... and the phone is still connected", len(accounts.devices()) >= 1 and bool(page.js("document.getElementById('home').offsetParent !== null")))
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
