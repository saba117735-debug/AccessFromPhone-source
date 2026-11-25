"""A real browser: tapping a tab paints over the page in that tab's own style, opens the tab's screen underneath, then scrapes the paint away.
    Chat      thick waves in your accent colour            (the chat is switched on under the paint)
    PC        a screen being scanned from the top          (the Access screen)
    Files     slanted slabs like a stack of papers         (the file browser)
    Settings  spirals winding out like a dial being turned (the settings sheet)
The page's frame clock is driven by hand, so every frame can be measured and saved as a picture.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/paint_e2e.py [screenshot folder] [width=390]
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

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG = 9394
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None
WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 else 390

# tab, style class, is the paint above the tab bar, how to tell the tab's screen is open, the title shown on the paint
TABS = [
    ("chat", "k-waves", False, "document.body.classList.contains('tab-chat')", "Chat"),
    ("access", "k-scan", True, "!!document.querySelector('.sheet-wrap[data-route=access]')", "Your PC"),
    ("files", "k-stack", True, "document.body.classList.contains('canvas-open')", "Files"),
    ("settings", "k-swirl", True, "!!document.querySelector('.sheet-wrap[data-route=settings]')", "Settings"),
]


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    os.environ["HANDOFF_DAILY__FOLDER"] = str(work / "daily")
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
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", f"--window-size={WIDTH + 70},980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=844, deviceScaleFactor=1, mobile=False)

        def load(settings_json):
            page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_tab','home'); localStorage.setItem('afp_settings', JSON.stringify({settings_json}));")
            page.call("Page.navigate", url=url + "/")
            page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
            time.sleep(1.5)

        def tap(tab):
            t.js(f"document.querySelector('#tabbar .tab[data-tab={tab}]').click()")

        clock = 1

        def step_to(ms):
            nonlocal clock
            while clock < ms:
                clock = min(ms, clock + 24)
                t.js(f"window.__step({clock})")

        def coverage():
            return t.js("""(() => { const c = document.querySelector('.pnt-cv'); const x = c.getContext('2d'); let on = 0, n = 0; for (let gy = 0; gy < 40; gy++) for (let gx = 0; gx < 24; gx++) { const p = x.getImageData(Math.floor((gx + 0.5) * c.width / 24), Math.floor((gy + 0.5) * c.height / 40), 1, 1).data; n++; if (p[3] > 230) on++; } return on / n; })()""")

        def snap(tab, name):
            time.sleep(0.9)   # the CSS tilt and title settle in real time
            data = base64.b64decode(page.call("Page.captureScreenshot", format="jpeg", quality=82)["data"])
            if OUT:
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / f"{tab}-{name}.jpg").write_bytes(data)

        load("{motion: 'full', theme: 'dark'}")
        t = Session(page, url, token, work)
        t.check("the page has the paint helper", t.js("typeof AfpUx.paint === 'function'"))

        for tab, klass, above, opened, label in TABS:
            # ---------------------------------------------------------------- frame by frame (the page's frame clock is driven by hand)
            load("{motion: 'full', theme: 'dark'}")
            t.js("window.__q = []; window.__raf = window.requestAnimationFrame.bind(window); window.requestAnimationFrame = (cb) => { window.__q.push(cb); return window.__q.length; }; window.__step = (ms) => { const q = window.__q; window.__q = []; q.forEach((cb) => cb(ms)); };")
            t.js("const real = AfpUx.paint; AfpUx.paint = (o) => real({...o, safety: 120000});")   # slow hand-stepping must not trip the paint's own safety timer
            tap(tab)
            t.wait("!!document.querySelector('.pnt')", f"the {tab} paint", seconds=3)
            clock = 1
            t.check(f"{tab}: it has its own style ({klass})", t.js(f"document.querySelector('.pnt').classList.contains('{klass}')"))
            t.check(f"{tab}: the title says {label!r}", t.js("document.querySelector('.pnt-title b').textContent") == label)
            t.check(f"{tab}: the screen is not opened before the paint covers the page (nothing flashes)", not t.js(opened))
            step_to(180); snap(tab, "1-first-strokes"); early = coverage()
            on_top = t.js("(() => { const r = document.querySelector('#tabbar').getBoundingClientRect(); const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!(e && e.closest('.pnt')); })()")
            t.check(f"{tab}: the paint is {'over' if above else 'under'} the tab bar", on_top == above)
            step_to(380); snap(tab, "2-painting"); mid = coverage()
            step_to(600); snap(tab, "3-covered")
            covered = coverage()
            t.check(f"{tab}: the paint grows stroke by stroke", early < 0.9 and early <= mid <= covered, f"{early:.2f} {mid:.2f} {covered:.2f}")
            t.check(f"{tab}: by the time the screen opens the paint covers the whole page", covered >= 0.97, f"{covered:.3f}")
            t.wait(opened, f"the {tab} screen to open underneath", seconds=5)
            t.check(f"{tab}: its screen is open underneath once the page is covered", True)
            step_to(900); snap(tab, "4-scraping")
            step_to(1100); scraped = coverage(); snap(tab, "5-scraping-more")
            step_to(1250); snap(tab, "6-nearly-clear")
            step_to(1600)
            t.check(f"{tab}: the paint is scraped away again", scraped < covered - 0.15, f"{covered:.2f} -> {scraped:.2f}")
            t.wait("!document.querySelector('.pnt')", "the paint to leave", seconds=5)
            t.js("window.requestAnimationFrame = window.__raf")
            t.check(f"{tab}: its screen is showing afterwards and nothing is left on top", t.js(opened) and not t.js("!!document.querySelector('.pnt')"))

            # ---------------------------------------------------------------- real timing
            load("{motion: 'full', theme: 'dark'}")
            t.js(f"window.__t0 = performance.now(); window.__seen = {{covered: null, overlay: null, gone: null}}; (function poll() {{ const now = performance.now() - window.__t0; const o = document.querySelector('.pnt'); if (o && window.__seen.overlay === null) window.__seen.overlay = now; if ({opened} && window.__seen.covered === null) window.__seen.covered = now; if (window.__seen.overlay !== null && !o && window.__seen.gone === null) window.__seen.gone = now; if (window.__seen.gone === null && now < 6000) requestAnimationFrame(poll); }})(); document.querySelector('#tabbar .tab[data-tab={tab}]').click();")
            t.wait("window.__seen.gone !== null", "the paint to finish", seconds=10)
            seen = t.js("window.__seen")
            print(f"     {tab} timings (ms):", {k: round(v) for k, v in seen.items()})
            t.check(f"{tab}: the paint appears the moment the tab is tapped", seen["overlay"] is not None and seen["overlay"] < 400, str(seen))
            t.check(f"{tab}: the screen opens while the paint covers the page", seen["covered"] is not None and seen["overlay"] <= seen["covered"] < seen["gone"], str(seen))
            t.check(f"{tab}: the whole thing is over in under 2.5 seconds", seen["gone"] < 2500, str(seen))

            # ---------------------------------------------------------------- a tap skips it, and still opens the screen
            load("{motion: 'full', theme: 'dark'}")
            tap(tab)
            t.wait("!!document.querySelector('.pnt')", "the paint", seconds=3)
            t.js("document.querySelector('.pnt').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, cancelable: true}))")
            t.wait(f"!document.querySelector('.pnt') && {opened}", "the skipped paint to clear with the screen showing", seconds=3)
            t.check(f"{tab}: tapping the paint skips it and lands on the screen", True)

            # ---------------------------------------------------------------- calm motion: no paint at all
            load("{motion: 'off'}")
            t.js(f"window.__pnt = 0; new MutationObserver(() => {{ if (document.querySelector('.pnt')) window.__pnt += 1; }}).observe(document.body, {{childList: true, subtree: true}}); document.querySelector('#tabbar .tab[data-tab={tab}]').click();")
            t.wait(opened, f"{tab} with animations off", seconds=4)
            time.sleep(0.5)
            t.check(f"{tab}: with motion off, it opens at once and no paint is ever drawn", t.js("window.__pnt") == 0)

            # ---------------------------------------------------------------- simple motion: flat paint
            load("{motion: 'simple'}")
            t.js(f"window.__flat = false; new MutationObserver(() => {{ const o = document.querySelector('.pnt'); if (o && o.classList.contains('is-flat')) window.__flat = true; }}).observe(document.body, {{childList: true, subtree: true}}); document.querySelector('#tabbar .tab[data-tab={tab}]').click();")
            t.wait(f"{opened} && !document.querySelector('.pnt')", f"{tab} with simple motion", seconds=6)
            t.check(f"{tab}: with simple motion the paint is drawn flat (no 3D tilt)", t.js("window.__flat"))

        # ---------------------------------------------------------------- the paints do not stack up
        load("{motion: 'full', theme: 'dark'}")
        tap("files")
        t.wait("!!document.querySelector('.pnt.k-stack')", "the files paint", seconds=3)
        tap("settings")
        t.wait("!!document.querySelector('.pnt.k-swirl')", "the settings paint after the files one", seconds=3)
        t.wait("document.querySelectorAll('.pnt').length === 1 || !document.querySelector('.pnt')", "only one paint", seconds=3)
        t.wait("!document.querySelector('.pnt') && !!document.querySelector('.sheet-wrap[data-route=settings]')", "both to finish", seconds=8)
        t.check("tapping another tab while one is painting finishes the first (its screen opens) and then paints the second", t.js("document.body.classList.contains('canvas-open') && !!document.querySelector('.sheet-wrap[data-route=settings]')"))
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
