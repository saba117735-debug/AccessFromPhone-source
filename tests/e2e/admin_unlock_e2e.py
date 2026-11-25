"""The PC's own page (the desktop window): the Unlock card shows whether unlocking is set up, on, stopped or paused, and offers the right button for each.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/admin_unlock_e2e.py [screenshot folder]
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

os.environ["HANDOFF_UNLOCK__AGENT_PORT"] = "8797"

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from handoff.unlock.agent import Agent, serve  # noqa: E402
from handoff.unlock.store import UnlockStore  # noqa: E402

DEBUG = 9381
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
xor = lambda data: bytes(b ^ 0x5A for b in data)  # noqa: E731


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    running = start(settings, quiet=True)
    running.app.state.accounts.setup_owner("Owner", "correct horse")
    admin = f"http://127.0.0.1:{base.ADMIN}"
    for _ in range(50):
        try:
            httpx.get(admin + "/", headers={"host": f"127.0.0.1:{base.ADMIN}"}); break
        except httpx.HTTPError:
            time.sleep(0.2)
    login = httpx.post(admin + "/admin/login", json={"username": "Owner", "password": "correct horse"}, headers={"X-Admin": "1"})
    cookie = login.cookies.get("afp_admin")
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=1100,1200",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures: list[str] = []
    helper = None

    def check(name, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=1000, height=1300, deviceScaleFactor=1, mobile=False)
        page.call("Network.enable")
        page.call("Page.addScriptToEvaluateOnNewDocument", source="localStorage.setItem('afp_splash_off','1');")
        page.call("Network.setCookie", name="afp_admin", value=cookie, domain="127.0.0.1", path="/")

        def look(name):
            page.call("Page.navigate", url=admin + "/")
            page.wait("!!document.querySelector('.rnav[data-view=unlock]')", 15, "the menu")
            page.js("document.querySelector('.rnav[data-view=unlock]').click()")
            page.wait("!!document.querySelector('.dmain .conn') && document.querySelector('.dmain .conn').textContent.length > 20", 15, "the Unlock section")
            time.sleep(0.6)
            (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))
            return page.js("document.querySelector('.dmain .conn').innerText")

        def button(text):
            return page.js("[...document.querySelectorAll('.dmain .conn button')].some(b => b.textContent.trim() === %s)" % json.dumps(text))

        # ---------------------------------------------------------------- not set up
        text = look("a1-not-set-up.png")
        check("a card about unlocking is on the PC's page", "Unlock this PC from your phone" in text)
        check("it says it is not set up", "Not set up" in text, text)
        check("it explains what the feature does in plain words", "unlock my PC" in text)
        page.js("document.querySelector('.rnav[data-view=overview]').click()")
        time.sleep(0.6)
        check("the overview has a summary card for unlocking too", "Unlock from phone" in page.js("document.querySelector('.dmain').innerText"))
        page.js("document.querySelector('.rnav[data-view=unlock]').click()")
        time.sleep(0.4)
        check("it offers a button to set it up", button("Set up unlocking"), text)
        check("no command has to be typed: the page does not tell the person to open a terminal", "python -m" not in text and "PowerShell" not in text, text)

        # ---------------------------------------------------------------- set up and on
        store = UnlockStore(work / "unlock.json", xor, xor)
        store.configure("pin", "7391", "483920")
        agent = Agent(store, locked=lambda: False, type_it=None, log=lambda s: None)
        helper = serve(agent, 8797)
        threading.Thread(target=helper.serve_forever, daemon=True).start()
        text = look("a2-on.png")
        check("once set up it says On", "On" in text and "Not set up" not in text, text)
        check("it says which sign-in is saved", "Windows Hello PIN" in text)
        check("it says whether the PC is locked right now", "unlocked" in text.lower(), text)
        check("it offers Switch off and Remove, and no setup button", button("Switch off") and button("Remove saved sign-in") and not button("Set up unlocking"))

        # ---------------------------------------------------------------- it stopped itself
        store.note_result(False)
        store.note_result(False)
        text = look("a3-stopped.png")
        check("a helper that stopped itself says so, and that it will start again by itself", "Stopped" in text and "paused" in text and "start again by itself" in text, text)
        check("and offers Start trying again", button("Start trying again"))
        page.js("[...document.querySelectorAll('.dmain .conn button')].find(b => b.textContent.trim() === 'Start trying again').click()")
        time.sleep(1.5)
        text = page.js("document.querySelector('.dmain .conn').innerText")
        check("tapping it starts it again", "Stopped" not in text and "On" in text, text)

        # ---------------------------------------------------------------- switch off
        page.js("[...document.querySelectorAll('.dmain .conn button')].find(b => b.textContent.trim() === 'Switch off').click()")
        time.sleep(1.5)
        text = page.js("document.querySelector('.dmain .conn').innerText")
        check("Switch off turns it off", "Off" in text and "On" != text and not button("Switch off"), text)
        check("and a Switch on button takes its place", button("Switch on"))
        page.js("[...document.querySelectorAll('.dmain .conn button')].find(b => b.textContent.trim() === 'Switch on').click()")
        time.sleep(1.5)
        text = page.js("document.querySelector('.dmain .conn').innerText")
        check("Switch on turns it back on", button("Switch off") and "Off" not in text.replace("Switch off", ""), text)
    finally:
        proc.terminate()
        if helper:
            helper.shutdown()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
