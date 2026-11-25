"""A real browser, a real PC app: the Home screen and the tab bar.

Home shows the PC at a glance, one-tap actions (Unlock when the PC is locked), what needs you, a first-run checklist and recent requests; the
tab bar switches between Home and Chat and opens Files and the PC's status; the chat has suggestion chips.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/home_e2e.py [screenshot folder]
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["HANDOFF_UNLOCK__AGENT_PORT"] = "8799"

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from handoff.unlock.agent import Agent, serve  # noqa: E402
from handoff.unlock.store import UnlockStore  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG = 9391
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
xor = lambda data: bytes(b ^ 0x5A for b in data)  # noqa: E731


def shot(page, name):
    (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    store = UnlockStore(work / "unlock.json", xor, xor)
    store.configure("pin", "7391", "483920")
    state = {"locked": True}
    helper = serve(Agent(store, locked=lambda: state["locked"], type_it=None, log=lambda s: None), 8799)
    threading.Thread(target=helper.serve_forever, daemon=True).start()
    settings = base.make_settings(work)
    (settings.agent.allowed_folders[0] / "notes.txt").write_text("hello", encoding="utf-8")
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
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1');")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
        time.sleep(1.5)
        t = Session(page, url, token, work)

        def visible(selector):
            return page.js("(() => { const e = document.querySelector(%s); return !!e && e.offsetParent !== null; })()" % json.dumps(selector))

        def button(text, scope="#home"):
            return page.js("[...document.querySelectorAll(%s + ' button')].some(b => b.textContent.trim() === %s && b.offsetParent !== null)" % (json.dumps(scope), json.dumps(text)))

        def press(text, scope="#home"):
            page.js("[...document.querySelectorAll(%s + ' button')].find(b => b.textContent.trim() === %s).click()" % (json.dumps(scope), json.dumps(text)))

        # ---------------------------------------------------------------- Home
        t.check("the app opens on Home, not on the chat box", visible("#home") and not visible(".field") and not visible("#thread"))
        t.check("the PC's name and that it is online are at the top", "Online" in page.js("document.querySelector('.hm-strip').innerText"))
        t.check("a tab bar offers Home, Chat, Access, Files and Settings", page.js("[...document.querySelectorAll('#tabbar .tab')].map(b => b.textContent.trim())") == ["Home", "Chat", "Access", "Files", "Settings"])
        t.check("Home is the highlighted tab", page.js("document.querySelector('#tabbar .tab.on').dataset.tab") == "home")
        t.wait("!!document.querySelector('.hm-tiles')", "the PC's readings", seconds=15)
        t.check("the PC card shows processor, memory and disk", all(w in page.js("document.querySelector('.hm-tiles').innerText") for w in ("Processor", "Memory", "Disk free")))
        t.wait("document.querySelector('#home .hm-card').innerText.includes('Locked')", "the lock state", seconds=15)
        t.check("a locked PC says Locked", True)
        t.check("... and offers Unlock as the big button (and Sleep beside it)", button("Unlock") and button("Sleep"))
        t.check("there is no grid of quick actions: Home stays short", "quick actions" not in page.js("document.querySelector('#home').innerText").lower() and not visible(".hm-grid"))
        t.check("the PC card names its state, Unlocked or Locked", "Locked" in page.js("document.querySelector('#home .hm-card').innerText"))
        t.check("the header does not duplicate the PC card's lock status", page.js("!document.querySelector('.hm-strip .lockchip') && !/locked/i.test(document.querySelector('.hm-strip').innerText)"))
        t.check("the header battery has no pill background or border", page.js("(() => { const s = getComputedStyle(document.querySelector('.hm-strip .chip-pc')); return s.backgroundColor === 'rgba(0, 0, 0, 0)' && s.backgroundImage === 'none' && s.borderTopWidth === '0px'; })()"))
        t.check("Home without the new card still fits a phone screen without much scrolling", page.js("document.querySelector('#home').scrollHeight - (document.querySelector('#home .dy-card') ? document.querySelector('#home .dy-card').offsetHeight + 14 : 0)") < 1300, page.js("document.querySelector('#home').scrollHeight"))  # (the limit includes the room left under the floating bar; the everyday tools card is extra and is kept to two short rows)
        t.check("a first-run checklist shows what is left to do", "Get set up" in page.js("document.querySelector('#home').innerText") and "Set up unlocking" in page.js("document.querySelector('#home').innerText"))
        t.check("unlocking is ticked off, because the helper is set up", page.js("document.querySelectorAll('.hm-step.done').length") >= 2)
        shot(page, "h1-home.png")

        # ---------------------------------------------------------------- an action from Home opens the chat and shows what happens
        press("Unlock")
        t.wait("!!document.querySelector('.pin-input')", "the approval with the PIN field", seconds=20)
        t.check("tapping Unlock moves to the chat and asks for approval with the PIN", visible(".field") and not visible("#home") and page.js("document.querySelector('#tabbar .tab.on').dataset.tab") == "chat")
        t.check("the Chat tab shows one request waiting", page.js("document.querySelector('#tabbar .tab[data-tab=chat] em').textContent") == "1")
        shot(page, "h2-approval.png")

        # ---------------------------------------------------------------- back on Home, the waiting request is the first thing shown
        page.js("document.querySelector('#tabbar .tab[data-tab=home]').click()")
        t.wait("!!document.querySelector('.hm-banner')", "the banner", seconds=10)
        t.check("Home puts the request that needs you at the top", "1 request needs you" in page.js("document.querySelector('.hm-banner').innerText"))
        t.check("the banner stays inside the screen", page.js("(() => { const b = document.querySelector('.hm-banner').getBoundingClientRect(); return b.left >= 0 && b.right <= window.innerWidth + 1; })()"))
        page.js("document.querySelector('.hm-banner').click()")
        t.wait("!!document.querySelector('.sheet .pin-input') && document.querySelector('.sheet .pin-input').offsetParent !== null", "the approvals sheet", seconds=10)
        t.check("tapping the banner opens the approval right there, as a sheet over Home", page.js("document.querySelectorAll('.sheet .appr-item').length") == 1 and page.js("document.querySelector('#tabbar .tab.on').dataset.tab") == "home")
        shot(page, "h2b-approvals.png")

        # ---------------------------------------------------------------- the chat's suggestions
        page.js("[...document.querySelectorAll('.plan .btn')].find(b => b.textContent.includes('Reject')).click()")
        time.sleep(1.5)
        t.check("rejecting from the sheet closes it when nothing else is waiting", page.js("document.querySelectorAll('.sheet').length") == 0)
        page.js("document.querySelector('#tabbar .tab[data-tab=chat]').click()")
        time.sleep(0.8)
        t.check("the chat offers suggestions above the message box", visible(".sugg-row") and page.js("document.querySelectorAll('.sugg').length") >= 3, page.js("document.querySelector('.sugg-row') && document.querySelector('.sugg-row').innerText"))
        t.check("... starting with Unlock, because the PC is locked", "Unlock my PC" in page.js("document.querySelector('.sugg').innerText"))
        t.js("(() => { const f = document.querySelector('.field'); f.value = 'hi'; f.dispatchEvent(new Event('input', {bubbles: true})); })()")
        t.check("suggestions hide while you type", not visible(".sugg-row"))
        t.js("(() => { const f = document.querySelector('.field'); f.value = ''; f.dispatchEvent(new Event('input', {bubbles: true})); })()")
        page.js("[...document.querySelectorAll('.sugg')].find(b => b.textContent.includes('Battery')).click()")
        t.wait("document.body.textContent.includes('Battery is at') || document.body.textContent.includes('No battery') || document.body.textContent.includes('battery')", "the answer", seconds=20)
        t.check("tapping a suggestion asks it straight away", True)
        shot(page, "h3-chat.png")

        # ---------------------------------------------------------------- the other tabs
        page.js("document.querySelector('#tabbar .tab[data-tab=files]').click()")
        t.wait("!!document.querySelector('.fb-list')", "the file browser", seconds=15)
        t.check("the Files tab opens the file browser", True)
        t.js("document.querySelector('#canvas .cv-head .iconbtn').click()")
        time.sleep(0.8)
        page.js("document.querySelector('.chip-pc:not(.chip-net)').click()")
        t.wait("!!document.querySelector('.pcs')", "the PC status", seconds=15)
        t.check("the battery chip opens the PC's status", True)
        t.js("document.querySelector('.sheet .iconbtn, .sheet-close, [aria-label=Close]').click()")
        time.sleep(0.8)

        # ---------------------------------------------------------------- Home's shortcuts and the checklist
        page.js("document.querySelector('#tabbar .tab[data-tab=home]').click()")
        time.sleep(0.6)
        page.js("document.querySelector('#tabbar .tab[data-tab=files]').click()")
        t.wait("!!document.querySelector('.fb-list')", "Files from Home", seconds=15)
        t.check("the Files tab opens the file browser from Home too", True)
        t.js("document.querySelector('#canvas .cv-head .iconbtn').click()")
        time.sleep(0.8)
        press("Hide this")
        time.sleep(0.5)
        t.check("the checklist can be hidden", "Get set up" not in page.js("document.querySelector('#home').innerText"))
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip')", 15, "Home again")
        time.sleep(1.5)
        t.check("... and stays hidden, and the app opens on the screen you left it", "Get set up" not in page.js("document.querySelector('#home').innerText"))
        t.check("the checklist is not shown once everything is done or hidden, but recent requests are", "recent" in page.js("document.querySelector('#home').innerText").lower())

        # ---------------------------------------------------------------- once unlocked, the big button becomes Lock
        state["locked"] = False
        time.sleep(5)
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip')", 15, "Home once more")
        t.wait("document.querySelector('#home .hm-card').innerText.includes('Ready')", "the unlocked state", seconds=20)
        t.check("an unlocked PC says Ready and offers Lock", button("Lock") and not button("Unlock"))
        t.check("Unlocked stays in the PC card, not the header", page.js("document.querySelector('#home .hm-card').innerText.includes('Unlocked') && !/unlocked/i.test(document.querySelector('.hm-strip').innerText)"))
        shot(page, "h4-unlocked.png")
    finally:
        proc.terminate()
        helper.shutdown()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    failures = t.failures if t else ["the browser never started"]
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
