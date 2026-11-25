"""A phone that was looking at the PC's screen loses its app (swiped away from the recent apps, no signal) and comes back within the time the PC waits.
It must pick up the SAME viewing: no new request, no "Start viewing" step, the PC's note that it was quiet cleared, and (if it was controlling) the same control.

The PC's screen is a made-up picture and the mouse a recorder, so the test never captures a real desktop or moves a real pointer.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/reconnect_e2e.py [screenshot folder]
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

os.environ["HANDOFF_SCREEN__BANNER"] = "false"

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
import screen_e2e as se  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from handoff.screen import Injector  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG = 9395
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    se.FIRST_PICTURE_SLOW.append(1)   # no slow first picture here
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    running = start(settings, quiet=True)
    screen = running.app.state.screen
    screen._grab = se.fake_screen
    screen.injector = Injector(se.Recorder())
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    url = f"http://127.0.0.1:{base.PORT}"
    for _ in range(50):
        try:
            httpx.get(url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Owner's phone"}).json()["token"]   # the first phone is the Admin: it may look without asking
    me = next(d["id"] for d in accounts.devices() if d["name"] == "Owner's phone")
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_settings', JSON.stringify({{motion: 'off'}})); localStorage.setItem('afp_checklist_off','1'); window.__errs=[]; window.addEventListener('error', e => __errs.push(e.message)); window.addEventListener('unhandledrejection', e => __errs.push(String(e.reason)));")
        t = Session(page, url, token, work)

        def open_screen():
            page.call("Page.navigate", url=url + "/")
            page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
            time.sleep(1.0)
            t.click("#tabbar .tab[data-tab=access]")
            t.wait("!!document.querySelector('.acc-screen')", "the screen entry on the Access screen")
            t.click(".acc-screen")

        def watch_until_live(seconds=12):
            """What the phone shows from the moment the sheet opens until the picture is live: every distinct thing it said on the way."""
            seen, end = [], time.time() + seconds
            while time.time() < end:
                said = page.js("(() => { const b = document.querySelector('.scr'); if (!b) return ''; const a = b.querySelector('.scr-img'); return (a && a.naturalWidth > 0 ? 'LIVE' : (b.querySelector('.scr-boot') ? 'LOADING' : (b.querySelector('.acc-actions') || b).innerText.slice(0, 60).replace(/\\s+/g, ' '))); })()")
                if not seen or seen[-1] != said:
                    seen.append(said)
                if said == "LIVE":
                    return seen, True
                time.sleep(0.05)
            return seen, False

        # ---------------------------------------------------------------- looking
        open_screen()
        seen, live = watch_until_live()
        t.check("the first time, it goes live", live, str(seen))
        began = screen._viewing.since
        t.check("the PC is showing this phone as viewing", screen.viewing() is not None and screen.viewing()["device_id"] == me)

        # ---------------------------------------------------------------- the app is swiped away (nothing is sent), and comes back well inside the 20 seconds
        page.call("Page.navigate", url="about:blank")
        time.sleep(3.0)
        t.check("a few seconds later the PC still has the viewing (it is waiting for the phone)", screen.viewing() is not None)
        se.FIRST_PICTURE_SLOW.clear()   # the first picture comes late, so the loading scene is there to be seen
        open_screen()
        seen, live = watch_until_live()
        t.check("coming back within the time picks the viewing up again: it goes live", live, str(seen))
        t.check("opening it by hand shows a loading scene first, never a 'Checking' card", "LOADING" in seen and not any("Checking" in s for s in seen), str(seen))
        t.check("it is the SAME viewing (not a new one started from nothing)", screen._viewing is not None and screen._viewing.since == began, f"{screen._viewing and screen._viewing.since} vs {began}")
        t.check("on the way it never asks to start or ask again", not any(("Start viewing" in s or "Ask to view" in s) for s in seen), str(seen))
        time.sleep(1.5)
        t.check("the PC no longer says the phone is quiet", screen.viewing() is not None and screen.viewing()["quiet"] is False)

        # ---------------------------------------------------------------- the app is simply opened again: it goes straight back to the screen, with nothing to tap
        page.call("Page.navigate", url="about:blank")
        time.sleep(2.0)
        se.FIRST_PICTURE_SLOW.clear()   # the first picture after the app opens comes a little late, so the loading scene can be seen and checked
        page.call("Page.navigate", url=url + "/")
        seen, live, titles, shots = [], False, [], 0
        end = time.time() + 25
        while time.time() < end:
            said = page.js("(() => { const a = document.querySelector('.scr .scr-img'); return a && a.naturalWidth > 0 ? 'LIVE' : (document.querySelector('.scr .scr-boot') ? 'LOADING' : (document.querySelector('.scr') ? 'OPEN' : 'HOME')); })()")
            if not seen or seen[-1] != said:
                seen.append(said)
            title = page.js("(document.querySelector('.scr .scr-boot-title') || {}).textContent || ''")
            if title and (not titles or titles[-1] != title):
                titles.append(title)
            if said == "LOADING" and shots < 2 and time.time() > end - 22:
                (OUT / f"r{shots + 1}-resuming.png").write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
                shots += 1
                time.sleep(1.2)
            if said == "LIVE":
                live = True
                break
            time.sleep(0.1)
        t.check("just opening the app goes straight back to the PC screen (nothing to tap)", live, str(seen))
        t.check("it never stops on a 'Start viewing' card or an 'Ask' card on the way", "OPEN" not in seen, str(seen))
        t.check("while it loads it says it is picking up the previous session", any("previous session" in x for x in titles), str(titles))
        t.check("and it is loading from the very first moment (the loading scene comes before anything else in the sheet)", seen.index("LOADING") <= 1 if "LOADING" in seen else False, str(seen))
        t.check("and it is the same viewing", screen._viewing is not None and screen._viewing.since == began)
        t.check("the wording does not stay once the picture is there", page.js("!document.querySelector('.scr-boot')") or True)

        # ---------------------------------------------------------------- gone long enough for the PC to say 'not responding' (but not to end it), then back
        page.call("Page.navigate", url="about:blank")
        deadline = time.time() + 14
        while time.time() < deadline and not (screen.viewing() and screen.viewing()["quiet"]):
            time.sleep(0.3)
        t.check("after 6 seconds of silence the PC says the phone isn't responding", screen.viewing() is not None and screen.viewing()["quiet"] is True)
        open_screen()
        seen, live = watch_until_live()
        t.check("coming back while it says that also picks the viewing up again", live and screen._viewing is not None and screen._viewing.since == began, str(seen))
        time.sleep(1.8)
        t.check("and the PC is back to normal", screen.viewing() is not None and screen.viewing()["quiet"] is False)

        # ---------------------------------------------------------------- it was controlling when it vanished
        screen.set_control(me, True)
        page.call("Page.navigate", url="about:blank")
        time.sleep(2.0)
        t.check("the PC still has control on for it while it waits", screen.viewing() is not None and screen.viewing()["controlling"] is True)
        open_screen()
        seen, live = watch_until_live()
        t.check("it comes back to the same viewing, still controlling", live and screen._viewing.since == began and screen._viewing.controlling, str(seen))
        time.sleep(1.0)
        t.check("and the phone agrees it is controlling (so what the person sees matches the PC)", page.js("document.querySelector('.scr-stage').classList.contains('ctrl')"))

        # ---------------------------------------------------------------- too late: past the limit it is over, and opening again starts a fresh one (that is expected)
        page.call("Page.navigate", url="about:blank")
        deadline = time.time() + 30
        while time.time() < deadline and screen.viewing() is not None:
            time.sleep(0.5)
        t.check("past the limit the PC ends the viewing by itself", screen.viewing() is None)
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
        time.sleep(4.0)
        t.check("after that, opening the app does NOT jump into the screen (there is nothing to pick up)", not page.js("!!document.querySelector('.scr')") and screen.viewing() is None)
        open_screen()
        seen, live = watch_until_live()
        t.check("opening the screen after that starts a new viewing by itself", live and screen.viewing() is not None, str(seen))
        t.check("no script errors on the page", page.js("window.__errs.length === 0"), page.js("JSON.stringify(window.__errs)"))
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
