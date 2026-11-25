"""A real browser as the Admin phone, a second phone over HTTP, a real PC app: PC access, one person at a time.

Walks the story: the PC is free → the Admin starts a turn → another phone asks for one → the Admin is asked and keeps going → the Admin ends the turn,
the other phone starts and the banner and the Access screen say so → the Admin sets that phone to view only.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/access_e2e.py [screenshot folder]
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

    def pair(name):
        return httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": name}).json()["token"]

    admin_token = pair("Asha's phone")  # the first phone paired is the Admin
    ben_token = pair("Ben's phone")
    ben = {"x-token": ben_token}
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(admin_token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_checklist_off','1'); window.__errs=[]; window.addEventListener('error', e => __errs.push(e.message)); window.addEventListener('unhandledrejection', e => __errs.push(String(e.reason)));")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
        time.sleep(1.5)
        t = Session(page, url, admin_token, work)

        def tab(name):
            return f"#tabbar .tab[data-tab={name}]"

        def text(selector):
            return page.js("(() => { const e = document.querySelector(%s); return e ? e.innerText : ''; })()" % json.dumps(selector))

        # ---------------------------------------------------------------- the bar
        t.check("the bottom bar has five tabs, Access in the middle", page.js("[...document.querySelectorAll('#tabbar .tab')].map(b => b.dataset.tab)") == ["home", "chat", "access", "files", "settings"])
        shot(page, "a1-home.png")

        # ---------------------------------------------------------------- a free PC
        t.click(tab("access"))
        t.wait("!!document.querySelector('.acc-hero')", "the Access screen")
        time.sleep(0.6)
        t.check("a free PC says so and offers Start my turn", "free" in text(".acc-title").lower() and "Start my turn" in text(".acc-actions"), text(".acc-hero"))
        t.check("the Admin sees their own role", "Admin" in text(".acc-chips"))
        t.check("the Admin sees the people list with Ben's phone", t.wait("document.querySelector('.acc-people').innerText.includes(\"Ben's phone\")", "people") is None)
        shot(page, "a2-free.png")
        page.js("window.__hero = document.querySelector('.acc-hero'); window.__people = document.querySelector('.acc-people'); window.__fetches = performance.getEntriesByType('resource').filter(e => e.name.includes('/access/people')).length")
        time.sleep(7)
        t.check("the 'This PC is free' card is not redrawn while nothing changes (no blinking)", page.js("document.querySelector('.acc-hero') === window.__hero && document.querySelector('.acc-people') === window.__people"))
        t.check("and the people list is not fetched again and again", page.js("performance.getEntriesByType('resource').filter(e => e.name.includes('/access/people')).length === window.__fetches"))

        # ---------------------------------------------------------------- the Admin starts a turn
        t.click_text("Start my turn")
        t.wait("document.querySelector('.acc-title').innerText.includes('using this PC')", "my turn")
        t.check("my turn says I'm using the PC and offers End my turn", "You're using" in text(".acc-title") and "End my turn" in text(".acc-actions"))
        t.check("the Access icon shows a dot for my turn", page.js("document.querySelector('#tabbar .tab[data-tab=access]').dataset.session") == "mine")
        shot(page, "a3-mine.png")
        page.js("window.__hero = document.querySelector('.acc-hero'); window.__people = document.querySelector('.acc-people')")
        time.sleep(7)
        t.check("the 'You're using this PC' card is not redrawn either while the turn just carries on", page.js("document.querySelector('.acc-hero') === window.__hero && document.querySelector('.acc-people') === window.__people"))

        # ---------------------------------------------------------------- Ben is turned away, then asks
        blocked = httpx.post(url + "/api/jobs", json={"prompt": "what can you do?"}, headers=ben)
        t.check("Ben cannot make changes while Asha has the turn", blocked.status_code == 409 and blocked.json()["detail"]["holder"] == "Asha's phone")
        asked = httpx.post(url + "/api/session/takeover", json={"message": "I need the printer"}, headers=ben)
        t.check("Ben can ask for a turn", asked.status_code == 200 and asked.json()["result"] == "asked")
        t.wait("!!document.querySelector('.acc-ask')", "the question for the Admin", seconds=15)
        t.check("the Admin is asked, with Ben's note", "Ben's phone" in text(".acc-ask") and "printer" in text(".acc-ask"), text(".acc-ask"))
        t.check("the dot on the Access icon turns urgent", page.js("document.querySelector('#tabbar .tab[data-tab=access]').dataset.session") == "ask")
        t.check("the safe answer has focus", page.js("document.activeElement.textContent.includes('still using')"))
        shot(page, "a4-asked.png")

        # ---------------------------------------------------------------- the Admin keeps going
        t.click_text("I'm still using it", "document.querySelector('.acc-ask')")
        t.wait("!document.querySelector('.acc-ask')", "the question to close")
        t.check("Ben is told no, and must wait before asking again", httpx.post(url + "/api/session/takeover", json={}, headers=ben).status_code == 429)
        t.check("Ben's phone is told the Admin is still using it", httpx.get(url + "/api/session", headers=ben).json()["notice"]["kind"] == "kept")

        # ---------------------------------------------------------------- the Admin finishes, Ben starts
        t.click_text("End my turn")
        t.wait("document.querySelector('.acc-title').innerText.toLowerCase().includes('free')", "free again")
        started = httpx.post(url + "/api/jobs", json={"prompt": "what can you do?"}, headers=ben)
        t.check("Ben can start once it is free", started.status_code == 200)
        t.wait("document.querySelector('.acc-title').innerText.includes(\"Ben's phone is using this PC\")", "Ben's turn on the Admin's screen", seconds=15)
        t.check("the Admin's screen says Ben is using it and offers to ask or take over", "Ask for a turn" in text(".acc-actions") and "Take over now" in text(".acc-actions"))
        t.check("the Access icon shows an amber dot", page.js("document.querySelector('#tabbar .tab[data-tab=access]').dataset.session") == "other")
        shot(page, "a5-other.png")
        t.js("document.querySelector('.sheet .iconbtn').click()")
        time.sleep(0.8)
        t.check("a slim banner on Home says who is using the PC", page.js("(() => { const b = document.querySelector('#sessionBar'); return !b.hidden && b.innerText.includes(\"Ben's phone is using this PC\"); })()"))
        shot(page, "a6-banner.png")

        # ---------------------------------------------------------------- the Admin sets Ben to view only
        t.click(tab("access"))
        t.wait("!!document.querySelector('.acc-seg')", "the people list")
        t.js("[...document.querySelectorAll('.acc-seg-b')].find(b => b.textContent === 'View').click()")
        time.sleep(1)
        t.check("Ben is now view only", httpx.get(url + "/api/session", headers=ben).json()["me"]["level"] == "viewer")
        saved = httpx.put(url + "/api/file", params={"path": "x.txt"}, json={"path": "x.txt", "text": "hi"}, headers=ben)
        t.check("and a view-only phone cannot change files", saved.status_code == 403 and saved.json()["detail"]["code"] == "level")
        shot(page, "a7-people.png")

        # ---------------------------------------------------------------- no script errors anywhere
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
