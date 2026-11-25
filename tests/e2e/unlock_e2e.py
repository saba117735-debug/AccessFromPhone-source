"""A real browser, a real PC app, a real unlock helper on the loopback connection (with a stand-in for the lock screen's keyboard):
ask "unlock my PC", see the PIN field on the approval, get a wrong PIN refused with the request still waiting, then unlock; and check the PIN
is nowhere afterwards.

It cannot type at a real lock screen (that needs the system account and a locked PC), which is the one thing it does not cover.
Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/unlock_e2e.py [screenshot folder]
"""
from __future__ import annotations

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

os.environ["HANDOFF_UNLOCK__AGENT_PORT"] = "8795"

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from handoff.unlock.agent import Agent, serve  # noqa: E402
from handoff.unlock.store import UnlockStore  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG, PIN, WINDOWS_PIN = 9371, "483920", "7391"
xor = lambda data: bytes(b ^ 0x5A for b in data)  # noqa: E731


def shot(page, name):
    import base64
    (Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()).joinpath(name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    store = UnlockStore(work / "unlock.json", xor, xor)
    store.configure("pin", WINDOWS_PIN, PIN)
    state, typed = {"locked": True}, []

    def keys(request):
        typed.append(request)
        state["locked"] = False
        return 0

    agent = Agent(store, locked=lambda: state["locked"], type_it=keys, wait_seconds=2, sleep=lambda s: time.sleep(0.05))
    helper = serve(agent, 8795)
    threading.Thread(target=helper.serve_forever, daemon=True).start()

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
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test"}).json()["token"]
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_tab','chat');")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 15, "the app")
        time.sleep(1)
        t = Session(page, url, token, work)

        def type_pin(value):
            t.js("""(() => { const i = document.querySelector('.pin-input'); i.focus(); i.value = %s; i.dispatchEvent(new Event('input', {bubbles: true})); })()""" % json.dumps(value))

        # ---------------------------------------------------------------- ask, in words
        t.say("unlock my PC")
        t.wait("!!document.querySelector('.pin-input')", "the approval card with a PIN field", seconds=20)
        t.check("asking to unlock waits for approval with a PIN field", True)
        t.check("the card says what will happen", "Unlock this PC" in t.js("document.querySelector('.plan').textContent") or "unlock" in t.js("document.querySelector('.plan').textContent").lower())
        t.check("the PIN is hidden as it is typed", t.js("document.querySelector('.pin-input').type") == "password" and t.js("document.querySelector('.pin-input').autocomplete") == "off")
        t.check("Approve is off until a PIN has been typed", t.js("[...document.querySelector('.pin-input').closest('.plan').querySelectorAll('.btn')].find(b => b.textContent.includes('Approve')).disabled"))
        type_pin("123")
        t.check("... and stays off for a PIN that is too short", t.js("[...document.querySelector('.pin-input').closest('.plan').querySelectorAll('.btn')].find(b => b.textContent.includes('Approve')).disabled"))
        shot(page, "u1-card.png")

        # ---------------------------------------------------------------- a wrong PIN
        type_pin("000000")
        t.check("a 6-character PIN switches Approve on", not t.js("[...document.querySelector('.pin-input').closest('.plan').querySelectorAll('.btn')].find(b => b.textContent.includes('Approve')).disabled"))
        t.click_text("Approve", "document.querySelector('.pin-input').closest('.plan')")
        t.wait("!document.querySelector('.pin-err').hidden", "the wrong-PIN message", seconds=15)
        message = t.js("document.querySelector('.pin-err').textContent")
        t.check("a wrong PIN is said on the card, with how many tries are left", "isn't right" in message and "2 more" in message, message)
        t.check("the request is still waiting, and the field is empty again", t.js("document.querySelector('.pin-input').value") == "" and t.js("!!document.querySelector('.plan')"))
        t.check("nothing was typed at the lock screen", typed == [])
        t.check("the PC is still locked", state["locked"] is True)
        shot(page, "u2-wrong.png")

        # ---------------------------------------------------------------- the right PIN
        type_pin(PIN)
        t.click_text("Approve", "document.querySelector('.pin-input').closest('.plan')")
        t.wait("document.body.textContent.includes('The PC is unlocked')", "the result", seconds=20)
        t.check("the right PIN unlocks the PC", state["locked"] is False and len(typed) == 1 and typed[0]["secret"] == WINDOWS_PIN)
        t.check("the chat says so", True)
        t.check("the PIN field is gone", t.js("!document.querySelector('.pin-input')"))
        everywhere = t.js("document.documentElement.outerHTML") + json.dumps(t.js("Object.assign({}, localStorage)")) + t.js("JSON.stringify(Object.keys(sessionStorage).map(k => sessionStorage.getItem(k)))")
        t.check("the PIN is nowhere in the page or the phone's storage", PIN not in everywhere and "000000" not in everywhere)
        server_side = httpx.get(url + "/api/jobs", headers={"X-Token": token}).text + httpx.get(url + "/api/audit", headers={"X-Token": token}).text
        t.check("nor in what the PC keeps (requests, audit log)", PIN not in server_side and WINDOWS_PIN not in server_side and "000000" not in server_side)
        t.check("the audit trail records that it was unlocked", '"unlocked"' in server_side)
        shot(page, "u3-done.png")

        # ---------------------------------------------------------------- when there is nothing to unlock
        t.say("unlock my PC")
        t.wait("document.body.textContent.includes(\"isn't locked\")", "the explanation", seconds=20)
        t.check("on a PC that isn't locked it says so at once and asks nothing", t.js("document.querySelectorAll('.pin-input').length") == 0)

        # ---------------------------------------------------------------- guessing is stopped
        state["locked"] = True
        for guess in ("111111", "222222", "333333"):
            t.say("unlock my PC")
            t.wait("!!document.querySelector('.pin-input')", "a card", seconds=20)
            type_pin(guess)
            t.click_text("Approve", "document.querySelector('.pin-input').closest('.plan')")
            time.sleep(1.5)
            t.js("[...document.querySelector('.pin-input').closest('.plan').querySelectorAll('.btn')].find(b => b.textContent.includes('Reject')).click()")
            time.sleep(0.8)
        t.say("unlock my PC")
        t.wait("document.body.textContent.includes('paused')", "the pause", seconds=20)
        t.check("after three wrong PINs, unlocking is paused (even a right PIN can't be tried)", t.js("document.querySelectorAll('.pin-input').length") == 0 and typed.__len__() == 1)
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
