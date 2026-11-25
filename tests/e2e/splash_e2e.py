"""The opening animation: it plays for about two and a half seconds on every launch, then gives way to the app. Not part of the normal test run
(needs Edge or Chrome):  python tests/e2e/splash_e2e.py [screenshot folder]
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

DEBUG = 9385
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
    url, admin = f"http://127.0.0.1:{base.PORT}", f"http://127.0.0.1:{base.ADMIN}"
    for _ in range(50):
        try:
            httpx.get(url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test"}).json()["token"]
    cookie = httpx.post(admin + "/admin/login", json={"username": "Owner", "password": "correct horse"}, headers={"X-Admin": "1"}).cookies.get("afp_admin")
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
        page.call("Network.enable")
        page.call("Network.setCookie", name="afp_admin", value=cookie, domain="127.0.0.1", path="/")
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_tab','home');")

        def shot(name):
            (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        def opacity():
            return page.js("(() => { const s = document.getElementById('splash'); return s ? Number(getComputedStyle(s).opacity) : -1; })()")

        # ---------------------------------------------------------------- the phone page
        t0 = time.time()
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.getElementById('splash')", 10, "the opening")
        t0 = time.time()  # the clock starts when the opening is on screen, so a slow page load (a busy PC) doesn't skew the timings
        seen, glows = {}, {}
        marks = [0.1, 0.4]
        shots = {0.4: "sp1.png"}
        for mark in marks:
            time.sleep(max(0, mark - (time.time() - t0)))
            seen[mark] = (opacity(), page.js("document.getElementById('splash') ? document.getElementById('splash').innerText : ''"))
            glows[mark] = page.js("(() => { const m = document.querySelector('.sp-mark'), w = document.querySelector('.sp-word span'); return m && w ? [getComputedStyle(m).filter, getComputedStyle(w).textShadow] : ['', '']; })()")
            if mark in shots:
                shot(shots[mark])
        check("the opening is on screen as the page opens", seen[0.1][0] == 1.0)
        check("it names the app and says what it is for", "AccessFromPhone" in seen[0.4][1] and "Your PC, from your pocket." in seen[0.4][1], seen[0.4][1])
        check("the introduction does not block interaction", page.js("!document.getElementById('splash') || getComputedStyle(document.getElementById('splash')).pointerEvents === 'none'"))
        check("there are no distracting glow effects", glows[0.4][0] == "none" and glows[0.4][1] == "none", glows[0.4])
        page.wait("!document.getElementById('splash')", 5, "the opening to finish")
        total = time.time() - t0
        check("the introduction finishes within 1.5 seconds", total <= 1.5, f"{total:.2f}s")
        page.wait("!!document.querySelector('#home .hm-strip')", 10, "Home")
        check("it hands over to Home", page.js("document.getElementById('home').offsetParent !== null"))
        check("nothing is left behind it to block taps", page.js("!document.getElementById('splash')"))
        shot("sp4-home.png")

        # ---------------------------------------------------------------- it can be switched off, and it respects 'reduce motion'
        page.call("Page.navigate", url=url + "/")
        time.sleep(0.8)
        check("a reload in the same session does not replay it", page.js("!document.getElementById('splash') || getComputedStyle(document.getElementById('splash')).display === 'none'"))
        page.js("sessionStorage.removeItem('afp_splash_seen'); localStorage.setItem('afp_splash_off', '1'); true")
        page.call("Page.navigate", url=url + "/")
        time.sleep(0.8)
        check("the switch afp_splash_off skips it", page.js("!document.getElementById('splash') || getComputedStyle(document.getElementById('splash')).display === 'none'"))
        page.js("localStorage.removeItem('afp_splash_off'); sessionStorage.removeItem('afp_splash_seen'); true")
        page.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
        page.call("Page.navigate", url=url + "/")
        t1 = time.time()
        page.wait("!!document.querySelector('#home .hm-strip')", 10, "Home with reduced motion")
        page.wait("!document.getElementById('splash')", 2, "the introduction to be skipped")
        quick = time.time() - t1
        check("with 'reduce motion' the introduction is skipped", quick < 2.0, f"{quick:.2f}s")

        # ---------------------------------------------------------------- the PC's window opens the same way
        page.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "no-preference"}])
        page.js("localStorage.removeItem('afp_splash_off'); true")
        page.call("Page.navigate", url=admin + "/")
        page.wait("!!document.getElementById('splash')", 10, "the opening on the PC window")
        time.sleep(0.1)
        check("the PC's window plays it too", opacity() == 1.0 and "Your PC, from your pocket." in page.js("document.getElementById('splash').innerText"))
        page.wait("!document.getElementById('splash')", 6, "it to finish")
        page.wait("!!document.querySelector('.shell .rail')", 15, "the dashboard")
        check("and then shows the dashboard", True)
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
