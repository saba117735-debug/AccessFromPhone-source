"""A real browser as a phone: the depth redesign. Graphics and motion in Settings (Full 3D / Simple / Off) and what each does to the page, the 3D PC on Home in
both themes, and approving a request with the fingerprint (or the PIN) when the app lock is on.

The phone's fingerprint reader is a stand-in in the page (window.AfpApp), so the test never needs real hardware.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/design_e2e.py [screenshot folder]
"""
from __future__ import annotations

import base64
import hashlib
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
from handoff.runtime import start  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG = 9394
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
PIN, SALT = "4821", "e2esalt"


def pin_hash(pin: str, salt: str) -> str:
    """The same slow hash the page uses (a SHA-256 chain), so a test can set up an app lock without typing it in."""
    v = f"{salt}:{pin}"
    for _ in range(2000):
        v = hashlib.sha256(v.encode()).hexdigest()
    return v


def shot(page, name):
    (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
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
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Design phone"}).json()["token"]
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_checklist_off','1'); window.__errs=[]; window.addEventListener('error', e => __errs.push(e.message)); window.addEventListener('unhandledrejection', e => __errs.push(String(e.reason)));")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
        time.sleep(1.5)
        t = Session(page, url, token, work)
        css = lambda sel, prop: page.js("getComputedStyle(document.querySelector(%s))[%s]" % (json.dumps(sel), json.dumps(prop)))  # noqa: E731
        motion = lambda: page.js("document.documentElement.dataset.motion || 'full'")  # noqa: E731
        theme = lambda: page.js("document.documentElement.dataset.theme")  # noqa: E731

        # ---------------------------------------------------------------- Home: the 3D PC
        t.wait("!!document.querySelector('.pcstage')", "the PC on Home")
        t.check("Home shows the PC as an object", page.js("!!document.querySelector('#home .pcstage .pcobj .pcf-front')"))
        t.check("it has a state (and an accessible name)", page.js("['ok','locked','offline'].includes(document.querySelector('#home .pcstage').dataset.state)") and " is " in page.js("document.querySelector('#home .pcstage').getAttribute('aria-label')"))
        t.check("by default on this browser it is Full 3D", motion() == "full", motion())
        t.check("Full: the monitor really is in 3D space and floats", "matrix3d" in css("#home .pcobj", "transform") and css("#home .pcfloat", "animationName") == "pc-float", css("#home .pcobj", "transform"))
        t.check("Full: the sides of the monitor are there", css("#home .pcf-l", "display") != "none")
        time.sleep(1.2)
        shot(page, "d1-home-full-dark.png")

        # ---------------------------------------------------------------- Settings: Graphics and motion
        t.click("#tabbar .tab[data-tab=settings]")
        t.wait("!!document.querySelector('.set-row, .set, .sheet')", "Settings")
        t.wait("!document.querySelector('.pnt')", "the paint that opens Settings to finish", seconds=6)   # its own test is paint_e2e.py
        time.sleep(0.6)
        # the Look page is one of Settings' pages
        t.js("""(() => { const b = [...document.querySelectorAll('.sheet button, .sheet .set-row, .sheet [role=button]')].find(e => /Look|Appearance|Theme/i.test(e.textContent)); if (b) b.click(); })()""")
        t.wait("!!document.querySelector('.gfx')", "Graphics and motion in Settings", seconds=15)
        t.check("Settings has a Graphics and motion control with three choices", page.js("[...document.querySelectorAll('.gfx .seg button')].map(b => b.textContent).join('|')") == "Full 3D|Simple|Off")
        t.check("the old on/off 'Reduce motion' switch is gone (it is the third choice now)", "Reduce motion" not in page.js("document.querySelector('.sheet').textContent"))
        t.check("it shows a live preview of the monitor", page.js("!!document.querySelector('.gfx .pcstage.mini')"))
        t.check("and says which level is on and that it was chosen automatically", "Full" in page.js("document.querySelector('.gfx-note').textContent") or "Depth" in page.js("document.querySelector('.gfx-note').textContent"))
        time.sleep(1.0)
        page.js("document.querySelector('.gfx').scrollIntoView({block: 'center'})")
        time.sleep(0.5)
        shot(page, "d2-settings-full.png")

        def choose(label):
            t.js("[...document.querySelectorAll('.gfx .seg button')].find(b => b.textContent === %s).click()" % json.dumps(label))
            time.sleep(2.0)   # the monitor's own 0.35 s transition has to finish before its transform is read

        choose("Simple")
        t.check("Simple: the page switches level at once", motion() == "simple")
        t.check("Simple: the monitor is flat (no 3D transform, no sides)", "matrix3d" not in css(".gfx .pcobj", "transform") and css(".gfx .pcf-l", "display") == "none", f"{css('.gfx .pcobj', 'transform')} | {css('.gfx .pcf-l', 'display')} | motion={motion()}")
        t.check("Simple: it still animates gently (glow and scan line keep going)", css(".gfx .pcglow", "animationName") == "pc-glow" and css(".gfx .pcscan", "animationName") == "pc-scan")
        t.check("Simple: but it no longer floats", css(".gfx .pcfloat", "animationName") == "none")
        t.check("Simple: the choice is remembered on the phone", "simple" in page.js("localStorage.getItem('afp_settings')"))
        time.sleep(0.4)
        shot(page, "d3-settings-simple.png")
        choose("Off")
        t.check("Off: nothing animates", motion() == "off" and css(".gfx .pcglow", "animationName") == "none" and css(".gfx .pcring", "display") == "none")
        t.check("the old Reduce-motion flag follows (so older code paths calm down too)", "\"reduceMotion\":true" in page.js("localStorage.getItem('afp_settings')"))
        shot(page, "d4-settings-off.png")
        choose("Full 3D")
        t.check("Full 3D brings the depth back", motion() == "full" and "matrix3d" in css(".gfx .pcobj", "transform"), f"{css('.gfx .pcobj', 'transform')} | motion={motion()}")

        # ---------------------------------------------------------------- the monitor in every level and both themes
        t.js("document.querySelector('.sheet .iconbtn').click()")
        time.sleep(0.6)
        for level, label in (("simple", "Simple"), ("off", "Off"), ("full", "Full 3D")):
            t.js("(() => { const c = JSON.parse(localStorage.getItem('afp_settings') || '{}'); c.motion = %s; c.reduceMotion = %s; localStorage.setItem('afp_settings', JSON.stringify(c)); })()" % (json.dumps(level), "true" if level == "off" else "false"))
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .pcstage')", 20, "Home again")
        time.sleep(1.4)
        t.check("the choice survives a reload", motion() == "full")
        for th in ("light", "dark"):
            for level in ("full", "simple", "off"):
                page.js("(() => { const c = JSON.parse(localStorage.getItem('afp_settings') || '{}'); c.motion = %s; c.reduceMotion = %s; c.theme = %s; localStorage.setItem('afp_settings', JSON.stringify(c)); })()" % (json.dumps(level), "true" if level == "off" else "false", json.dumps(th)))
                page.call("Page.navigate", url=url + "/")
                page.wait("!!document.querySelector('#home .pcstage')", 20, "Home")
                time.sleep(1.5)
                t.check(f"{th} theme, {level}: the page uses that theme and level", theme() == th and motion() == level, f"{theme()} {motion()}")
                t.check(f"{th} theme, {level}: the monitor is visible", page.js("(() => { const r = document.querySelector('#home .pcfront, #home .pcf-front').getBoundingClientRect(); return r.width > 100 && r.height > 60; })()"))
                shot(page, f"d5-home-{th}-{level}.png")
                if th == "light" and level == "full":
                    light_front = css("#home .pcf-front", "backgroundImage")
        page.js("(() => { const c = JSON.parse(localStorage.getItem('afp_settings') || '{}'); c.theme = 'dark'; c.motion = 'full'; c.reduceMotion = false; localStorage.setItem('afp_settings', JSON.stringify(c)); })()")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .pcstage')", 20, "Home")
        time.sleep(1.2)
        t.check("the monitor's body changes with the theme (not one fixed colour)", css("#home .pcf-front", "backgroundImage") != light_front)

        # ---------------------------------------------------------------- approving with the fingerprint (app lock on)
        (work / "workspace" / "note1.txt").write_text("one")
        (work / "workspace" / "note2.txt").write_text("two")
        (work / "workspace" / "note3.txt").write_text("three")
        (work / "workspace" / "note4.txt").write_text("four")
        page.js("""window.AfpApp = { canBiometric: () => true, biometric: (title) => { window.__bioAsked = title; setTimeout(() => window.__afpBio && window.__afpBio(window.__bioOk), 60); } }; window.__bioOk = false;""")
        page.js("localStorage.setItem('afp_lock', JSON.stringify({ salt: %s, hash: %s, len: 4, bio: true, after: 900 }))" % (json.dumps(SALT), json.dumps(pin_hash(PIN, SALT))))
        t.js("document.querySelector('#tabbar .tab[data-tab=chat]').click()")
        t.wait("!!document.querySelector('.field')", "the chat box")
        t.say("delete note1.txt")
        t.wait("!!document.querySelector('.plan .btn.approve')", "an approval card", seconds=25)
        t.check("with the app lock and fingerprint on, the button says how it will be confirmed", "Approve with fingerprint" in page.js("document.querySelector('.plan .btn.approve').textContent"), page.js("document.querySelector('.plan .btn.approve').textContent"))
        t.check("and carries the fingerprint icon", page.js("!!document.querySelector('.plan .btn.approve.secure svg')"))
        shot(page, "d6-approve-card.png")
        t.js("document.querySelector('.plan .btn.approve').click()")
        t.wait("!!document.querySelector('.cfm .cfm-card')", "the confirm sheet")
        t.check("tapping Approve opens the confirm sheet instead of approving", (work / "workspace" / "note1.txt").exists() and "Confirm it's you" in page.js("document.querySelector('.cfm').textContent"))
        time.sleep(0.5)
        t.check("it asked the phone for the fingerprint, saying what it is for", "Approve" in str(page.js("window.__bioAsked")), str(page.js("window.__bioAsked")))
        t.check("a fingerprint that doesn't match approves nothing and says so", "didn't match" in page.js("document.querySelector('.cfm-msg').textContent") and (work / "workspace" / "note1.txt").exists(), page.js("document.querySelector('.cfm-msg').textContent"))
        shot(page, "d7-confirm-sheet.png")

        def press(digits):
            for d in digits:
                t.js("[...document.querySelectorAll('.cfm .lk-key')].find(k => k.textContent.trim() === %s).click()" % json.dumps(d))
                time.sleep(0.05)

        press("0000")
        time.sleep(0.6)
        t.check("a wrong PIN is refused and approves nothing", "not right" in page.js("document.querySelector('.cfm-msg').textContent") and (work / "workspace" / "note1.txt").exists() and page.js("!!document.querySelector('.cfm')"), page.js("document.querySelector('.cfm-msg').textContent"))
        press(PIN)
        t.wait("!document.querySelector('.cfm')", "the sheet to close after the right PIN", seconds=6)
        deadline = time.time() + 15
        while time.time() < deadline and (work / "workspace" / "note1.txt").exists():
            time.sleep(0.3)
        t.check("the right PIN approves it, and the PC does it", not (work / "workspace" / "note1.txt").exists())

        # a matching fingerprint approves at once, with no PIN
        page.js("window.__bioOk = true; window.__bioAsked = '';")
        t.say("delete note2.txt")
        t.wait("document.querySelectorAll('.plan .btn.approve').length > 0 && [...document.querySelectorAll('.plan .btn.approve')].some(b => !b.disabled)", "the second approval card", seconds=25)
        t.js("[...document.querySelectorAll('.plan .btn.approve')].filter(b => !b.disabled).pop().click()")
        deadline = time.time() + 15
        while time.time() < deadline and (work / "workspace" / "note2.txt").exists():
            time.sleep(0.3)
        t.check("a matching fingerprint approves with no PIN typed", not (work / "workspace" / "note2.txt").exists() and "Approve" in str(page.js("window.__bioAsked")))
        time.sleep(0.8)
        t.check("and the sheet is gone afterwards", not page.js("!!document.querySelector('.cfm')"))

        # cancelling approves nothing
        page.js("window.__bioOk = false")
        t.say("delete note3.txt")
        t.wait("[...document.querySelectorAll('.plan .btn.approve')].some(b => !b.disabled)", "the third approval card", seconds=25)
        t.js("[...document.querySelectorAll('.plan .btn.approve')].filter(b => !b.disabled).pop().click()")
        t.wait("!!document.querySelector('.cfm')", "the confirm sheet again")
        t.js("document.querySelector('.cfm-cancel').click()")
        t.wait("!document.querySelector('.cfm')", "the sheet to close on Cancel", seconds=5)
        time.sleep(1.0)
        t.check("Cancel approves nothing and the request is still waiting", (work / "workspace" / "note3.txt").exists() and page.js("[...document.querySelectorAll('.plan .btn.approve')].some(b => !b.disabled)"))

        # turning "Confirm approvals" off goes back to a plain tap
        page.js("(() => { const c = JSON.parse(localStorage.getItem('afp_lock')); c.approve = false; localStorage.setItem('afp_lock', JSON.stringify(c)); })()")
        t.say("delete note4.txt")
        t.wait("document.querySelectorAll('.plan .btn.approve').length > 1", "a fourth approval card", seconds=25)
        t.check("with Confirm approvals off the button is a plain Approve", page.js("[...document.querySelectorAll('.plan .btn.approve')].pop().textContent.trim()") == "Approve")
        t.js("[...document.querySelectorAll('.plan .btn.approve')].pop().click()")
        deadline = time.time() + 15
        while time.time() < deadline and (work / "workspace" / "note4.txt").exists():
            time.sleep(0.3)
        t.check("and a tap approves straight away, as before", not (work / "workspace" / "note4.txt").exists() and not page.js("!!document.querySelector('.cfm')"))

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
