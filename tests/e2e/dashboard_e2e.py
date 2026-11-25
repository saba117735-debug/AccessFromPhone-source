"""The PC's own window: a menu on the left and one section at a time (Overview, Phones, Unlock, Connection, Activity, Settings).

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/dashboard_e2e.py [screenshot folder]
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

os.environ["HANDOFF_UNLOCK__AGENT_PORT"] = "8798"

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from handoff.unlock.agent import Agent, serve  # noqa: E402
from handoff.unlock.store import UnlockStore  # noqa: E402

DEBUG = 9383
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
xor = lambda data: bytes(b ^ 0x5A for b in data)  # noqa: E731


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    store = UnlockStore(work / "unlock.json", xor, xor)
    store.configure("pin", "7391", "483920")
    helper = serve(Agent(store, locked=lambda: False, type_it=None, log=lambda s: None), 8798)
    threading.Thread(target=helper.serve_forever, daemon=True).start()
    settings = base.make_settings(work)
    running = start(settings, quiet=True)
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    admin = f"http://127.0.0.1:{base.ADMIN}"
    phone_url = f"http://127.0.0.1:{base.PORT}"
    for _ in range(50):
        try:
            httpx.get(phone_url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    for name in ("Afza's phone", "Tablet"):
        httpx.post(phone_url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": name})
    token = httpx.post(phone_url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test"}).json()["token"]
    httpx.post(phone_url + "/api/jobs", headers={"X-Token": token}, json={"prompt": "battery status", "conversation": "one"})
    time.sleep(2)
    login = httpx.post(admin + "/admin/login", json={"username": "Owner", "password": "correct horse"}, headers={"X-Admin": "1"})
    cookie = login.cookies.get("afp_admin")
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=1300,900",
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
        page.call("Emulation.setDeviceMetricsOverride", width=1280, height=820, deviceScaleFactor=1, mobile=False)
        page.call("Network.enable")
        page.call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('afp_splash_off','1');")
        page.call("Network.setCookie", name="afp_admin", value=cookie, domain="127.0.0.1", path="/")

        def shot(name):
            (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        def go(view):
            page.js("document.querySelector('.rnav[data-view=%s]').click()" % view)
            time.sleep(0.7)

        def text():
            return page.js("document.querySelector('.dmain').innerText")

        page.call("Page.navigate", url=admin + "/")
        page.wait("!!document.querySelector('.shell .rail')", 15, "the dashboard")
        time.sleep(1.5)
        check("the PC's window has a menu on the left", page.js("[...document.querySelectorAll('.rnav')].map(b => b.textContent.trim())") == ["Overview", "Phones", "Unlock", "Connection", "Activity", "Settings"])
        check("it opens on the Overview", page.js("document.querySelector('.rnav.on').dataset.view") == "overview")
        check("the menu sits in a column on the left and the content to its right", page.js("(() => { const r = document.querySelector('.rail').getBoundingClientRect(), m = document.querySelector('.dmain').getBoundingClientRect(); return r.right <= m.left + 2 && r.width < 300; })()"))
        t = text()
        check("a greeting with the PC's name and phones", "Owner" in t and "phones paired" in t, t[:120])
        page.wait("document.querySelector('[data-k=cpu] strong').textContent.endsWith('%')", 15, "the readings")
        check("four live readings: processor, memory, disk, battery", all(w in text() for w in ("PROCESSOR", "MEMORY", "DISK FREE", "BATTERY")) or all(w.lower() in text().lower() for w in ("processor", "memory", "disk free", "battery")))
        check("the phones that are paired are listed", "Afza's phone" in t or "Afza's phone" in text())
        check("the unlock card says where unlocking stands", "Unlock from phone" in text())
        check("recent activity is listed", "Recent activity" in text() and "Asked: battery status" in text(), text()[-300:])
        check("a compact QR code and pairing code to connect a phone", page.js("!!document.querySelector('.mini-qr svg') && /^[A-Z0-9]{4}-?[A-Z0-9]{4}$/.test(document.querySelector('.mini-code').textContent.trim())"))
        shot("d1-overview.png")

        go("phones")
        check("Phones shows the big connect card and every phone", page.js("!!document.querySelector('.connect .qr') && document.querySelectorAll('.devices .device').length") >= 3)
        shot("d2-phones.png")
        go("unlock")
        check("Unlock shows the unlock card", "Unlock by asking" in text() and "Windows Hello PIN" in text())
        go("connection")
        check("Connection shows how phones reach this PC", "Same Wi-Fi" in text() and "Anywhere" in text())
        go("activity")
        check("Activity lists what happened", "Asked: battery status" in text() or "Signed in" in text())
        shot("d3-activity.png")
        go("settings")
        check("Settings shows this PC and the password card", "Sign-in" in text() and "Change password" in text())
        page.call("Page.navigate", url=admin + "/")
        page.wait("!!document.querySelector('.rnav.on')", 15, "the dashboard again")
        check("the section you were on is remembered", page.js("document.querySelector('.rnav.on').dataset.view") == "settings")
        go("overview")

        # ---------------------------------------------------------------- the menu and the connect card keep working
        page.js("[...document.querySelectorAll('.dmain button')].find(b => b.textContent.includes('Manage phones')).click()")
        time.sleep(0.6)
        check("'Manage phones' on the overview opens Phones", page.js("document.querySelector('.rnav.on').dataset.view") == "phones")
        go("overview")
        code_before = page.js("document.querySelector('.mini-code').textContent")
        page.js("[...document.querySelectorAll('.mini-pair button')].find(b => b.textContent.includes('New code')).click()")
        time.sleep(1.2)
        check("'New code' on the overview makes a fresh code", page.js("document.querySelector('.mini-code').textContent") != code_before)

        # ---------------------------------------------------------------- a narrow window: the menu becomes a bar along the top
        page.call("Emulation.setDeviceMetricsOverride", width=420, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.navigate", url=admin + "/")
        page.wait("!!document.querySelector('.rnav.on')", 15, "the narrow dashboard")
        time.sleep(1.5)
        check("on a narrow window the menu is a bar across the top", page.js("(() => { const r = document.querySelector('.rail').getBoundingClientRect(), m = document.querySelector('.dmain').getBoundingClientRect(); return r.bottom <= m.top + 2 && r.width > 380; })()"))
        check("nothing runs off the side of the page", page.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), page.js("document.documentElement.scrollWidth + ' > ' + window.innerWidth"))
        shot("d4-narrow.png")
    finally:
        proc.terminate()
        helper.shutdown()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
