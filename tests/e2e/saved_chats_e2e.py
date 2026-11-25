"""A real browser, a real server: chats are saved on the phone, the PC goes away, the saved chats can be read (and nothing else can be done),
and when the PC comes back the page is live again. Also: switching it off, signing out, and the app's own copy of the page.

Not part of the normal test run (needs Edge or Chrome, takes about two minutes):  python tests/e2e/saved_chats_e2e.py [screenshot folder]
"""
from __future__ import annotations

import http.server
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402

WEB = ROOT / "src" / "handoff" / "web"
DEBUG, STATIC_PORT = 9361, 8793
IDB = """(async () => {
  const db = await new Promise((ok, bad) => { const r = indexedDB.open('afp-offline', 1); r.onupgradeneeded = () => r.result.createObjectStore('kv'); r.onsuccess = () => ok(r.result); r.onerror = () => bad(r.error); });
  const v = await new Promise((ok) => { const q = db.transaction('kv').objectStore('kv').get('chats'); q.onsuccess = () => ok(q.result || null); q.onerror = () => ok(null); });
  db.close();
  return v ? JSON.stringify(v) : '';
})()"""


class Static(http.server.BaseHTTPRequestHandler):
    """The app's own copy of the page: the same files, with nothing behind them (the PC is off)."""

    def do_GET(self):
        path = self.path.split("?")[0].split("#")[0]
        name = "index.html" if path in ("/", "/index.html") else path.removeprefix("/static/")
        file = WEB / name
        if not file.is_file() or ".." in name:
            self.send_response(404); self.end_headers(); return
        body = file.read_bytes()
        kind = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}.get(file.suffix, "application/octet-stream")
        self.send_response(200); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


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

    def wait_up():
        for _ in range(60):
            try:
                httpx.get(url + "/healthz"); return
            except httpx.HTTPError:
                time.sleep(0.2)

    wait_up()
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test phone"}).json()["token"]
    headers = {"X-Token": token}
    for conv, prompt in (("alpha", "battery status"), ("alpha", "network speed"), ("beta", "pc status")):
        httpx.post(url + "/api/jobs", headers=headers, json={"prompt": prompt, "conversation": conv})
    time.sleep(2)

    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=420,900",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    static = http.server.ThreadingHTTPServer(("127.0.0.1", STATIC_PORT), Static)
    threading.Thread(target=static.serve_forever, daemon=True).start()
    failures: list[str] = []

    def check(name, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)  # a phone-sized screen
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"if (location.port === '{base.PORT}') {{ localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_tab','chat'); }}")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 15, "the app")
        time.sleep(1)

        # ---------------------------------------------------------------- saved while the PC is there
        page.wait("document.querySelectorAll('.conv').length >= 2", 20, "the chats")
        page.wait("(async () => { const s = await " + IDB.strip().rstrip(";") + "; return s.length > 0 && JSON.parse(s).jobs.length >= 3; })()", 25, "the saved copy")
        snap = json.loads(page.js(IDB))
        check("recent chats are saved on the phone while the PC answers", len(snap["jobs"]) >= 3 and snap["audit"], len(snap["jobs"]))
        check("the copy knows which PC it is from and when it was made", bool(snap["pc"]) and snap["savedAt"] > 0 and snap["v"] == 1)
        check("the copy holds the chats' own words", {"battery status", "network speed", "pc status"} <= {j["prompt"] for j in snap["jobs"]})
        check("the page offers no saved-chat button while the PC is fine", page.js("!document.querySelector('#lost')"))
        before_chats = page.js("[...document.querySelectorAll('.conv-title')].map(e => e.textContent).sort()")

        # ---------------------------------------------------------------- the PC goes away
        running.stop()
        page.wait("!!document.querySelector('#lost')", 30, "the popup")
        time.sleep(1.2)
        check("the popup now offers 'View saved chats'", page.js("(() => { const b = document.querySelector('#lostSaved'); return !!b && !b.hidden && b.textContent.includes('View saved chats'); })()"))
        check("Try again and Switch PC are still there", page.js("!!document.querySelector('#lostRetry') && !!document.querySelector('#lostSwitch')"))
        page.shot("s1-popup.png")
        page.js("document.querySelector('#lostSaved').click()")
        page.wait("!document.querySelector('#lost')", 5, "the popup to close")
        time.sleep(0.5)
        check("it opens the saved chats and the popup closes", page.js("document.body.classList.contains('is-offline')"))
        check("a banner says the PC is offline and how old the copy is", page.js("(() => { const b = document.querySelector('#savedBar'); return !b.hidden && b.textContent.includes('Your PC is offline') && /saved .* ago/.test(b.textContent); })()"), page.js("document.querySelector('#savedBar').textContent"))
        for width in (320, 375, 412, 520, 900):
            page.call("Emulation.setDeviceMetricsOverride", width=width, height=860, deviceScaleFactor=1, mobile=False)
            check(f"saved-chat notice fits {width}px", page.js("""(() => {
                const bar=document.querySelector('#savedBar');
                return bar.scrollWidth<=bar.clientWidth+1 && document.documentElement.scrollWidth<=window.innerWidth;
            })()"""))
            if width <= 520:
                check(f"saved-chat notice stays compact at {width}px", page.js("""(() => {
                    const bar=document.querySelector('#savedBar'), retry=bar.querySelector('.saved-retry');
                    return bar.getBoundingClientRect().height<=56 && retry.getBoundingClientRect().height>=44
                        && getComputedStyle(bar.querySelector('.saved-track')).animationName==='saved-marquee';
                })()"""))
        page.call("Emulation.setDeviceMetricsOverride", width=320, height=860, deviceScaleFactor=1, mobile=False)
        check("marquee repeats only decorative content", page.js("""(() => {
            const copies=document.querySelectorAll('.saved-copy');
            return copies.length===2 && copies[0].textContent===copies[1].textContent && copies[1].getAttribute('aria-hidden')==='true';
        })()"""))
        check("marquee actually moves", page.js("""(async () => {
            const track=document.querySelector('.saved-track');
            await new Promise(resolve=>setTimeout(resolve, 3000));
            const before=getComputedStyle(track).transform;
            await new Promise(resolve=>setTimeout(resolve, 600));
            return before!==getComputedStyle(track).transform;
        })()"""))
        page.js("document.querySelector('.saved-text').focus()")
        check("keyboard focus pauses the marquee", page.js("getComputedStyle(document.querySelector('.saved-track')).animationPlayState==='paused'"))
        page.js("document.querySelector('.saved-text').blur()")
        for app_motion in (False, True):
            if app_motion:
                page.js("document.documentElement.dataset.motion='off'")
            else:
                page.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion", "value": "reduce"}])
            check(f"{'app' if app_motion else 'system'} reduced motion keeps the notice compact and readable", page.js("""(() => {
                const bar=document.querySelector('#savedBar'), text=bar.querySelector('.saved-text');
                text.scrollLeft=text.scrollWidth;
                return getComputedStyle(bar.querySelector('.saved-track')).animationName==='none'
                    && getComputedStyle(bar.querySelector('[aria-hidden=true].saved-copy')).display==='none'
                    && text.scrollLeft>0 && text.textContent.includes('wait on this phone')
                    && bar.getBoundingClientRect().height<=56;
            })()"""))
            page.js("delete document.documentElement.dataset.motion; document.querySelector('.saved-text').scrollLeft=0")
            page.call("Emulation.setEmulatedMedia", features=[])
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        check("the app is not blocked: nothing is inert", page.js("!document.querySelector('#app').hasAttribute('inert') && !document.body.classList.contains('is-lost')"))
        check("the chat list is the saved one", page.js("[...document.querySelectorAll('.conv-title')].map(e => e.textContent).sort()") == before_chats, page.js("[...document.querySelectorAll('.conv-title')].map(e => e.textContent)"))
        check("a saved chat's words can be read", any(w in page.js("document.querySelector('#thread').textContent") for w in ("battery status", "network speed", "pc status")))
        check("the message box stays usable and says requests will be queued", page.js("(() => { const f = document.querySelector('.field'); return !f.disabled && f.placeholder.includes('queued'); })()"))
        check("there is a send button to press", page.js("!!document.querySelector('.send')"))
        check("buttons that need the PC are not offered in the chat", page.js("[...document.querySelectorAll('#thread .actions, #thread .files')].every(e => e.offsetParent === null)"))
        check("New chat cannot be tapped", page.js("getComputedStyle(document.querySelector('.newchat')).pointerEvents") == "none")
        page.shot("s2-reading.png")
        page.js("document.querySelector('#tabbar .tab[data-tab=files]').click()")
        time.sleep(0.8)
        check("anything that needs the PC says so instead of failing silently", "offline" in page.js("document.querySelector('#toasts').textContent").lower(), page.js("document.querySelector('#toasts').textContent"))
        check("and no file browser opened", page.js("!document.querySelector('.fb-list')"))
        if page.js("[...document.querySelectorAll('.conv-open')].length > 1"):
            page.js("document.querySelectorAll('.conv-open')[1].click()")
            time.sleep(0.4)
            check("another saved chat can be opened", page.js("document.body.classList.contains('is-offline')"))

        # ---------------------------------------------------------------- trying again while it is still away
        page.js("document.querySelector('.saved-retry').click()")
        time.sleep(4)
        check("a failed retry keeps the saved chats, and the popup stays away", page.js("document.body.classList.contains('is-offline') && !document.querySelector('#lost')"))
        check("the banner is back to 'Try again' afterwards", page.js("(() => { const b = document.querySelector('.saved-retry'); return !!b && !b.disabled && b.textContent.includes('Try again'); })()"), page.js("document.querySelector('#savedBar').textContent"))

        # ---------------------------------------------------------------- the app's own copy of the page, with no PC behind it
        snap_text = page.js(IDB)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=(
            f"if (location.port === '{STATIC_PORT}') {{ const snap = {snap_text}; window.AfpApp = {{ savedChats: () => JSON.stringify(snap), saveChats() {{}}, "
            "clearChats() { window.__cleared = true; }, retryConnect() { window.__retried = true; } }; }"))
        page.call("Page.navigate", url=f"http://127.0.0.1:{STATIC_PORT}/#/offline")
        page.wait("!!document.querySelector('#thread')", 15, "the built-in page")
        time.sleep(1.5)
        check("the app's built-in page shows the saved chats (not the connect screen)", page.js("document.body.classList.contains('is-offline') && !document.querySelector('.auth')"), page.js("document.body.className"))
        check("... with the same chats", sorted(page.js("[...document.querySelectorAll('.conv-title')].map(e => e.textContent)")) == before_chats)
        check("... and the message box is usable there too", page.js("!document.querySelector('.field').disabled"))
        page.js("document.querySelector('.saved-retry').click()")
        time.sleep(0.4)
        check("its Try again asks the app to look for the PC again", page.js("window.__retried === true"))
        page.shot("s3-builtin.png")

        # ---------------------------------------------------------------- the PC comes back
        running = start(settings, quiet=True)
        wait_up()
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 20, "the app again")
        page.wait("document.querySelectorAll('.conv').length >= 2", 20, "live chats")
        check("when the PC is there the page is live and not read-only", page.js("!document.body.classList.contains('is-offline') && !document.querySelector('.field').disabled"))

        # the same, without reloading: go away while open, read, come back
        running.stop()
        page.wait("!!document.querySelector('#lost')", 30, "the popup again")
        time.sleep(1)
        page.js("document.querySelector('#lostSaved').click()")
        time.sleep(0.6)
        check("reading again after a second outage", page.js("document.body.classList.contains('is-offline')"))
        running = start(settings, quiet=True)
        wait_up()
        t0 = time.time()
        page.wait("!document.body.classList.contains('is-offline')", 40, "the page to go live by itself")
        check("the page goes live by itself when the PC is back", True)
        check("it took a sensible time", time.time() - t0 < 25, f"{round(time.time() - t0, 1)}s")
        time.sleep(1)
        check("the banner is gone and the message box works again", page.js("document.querySelector('#savedBar').hidden && !document.querySelector('.field').disabled"))
        check("'Back online' was shown", True if page.js("document.querySelector('#toasts').textContent.includes('Back online')") else False)
        check("live chats came back", page.js("document.querySelectorAll('.conv').length") >= 2)
        page.shot("s4-live.png")

        # ---------------------------------------------------------------- switching it off in Settings, and on again
        page.call("Page.navigate", url=url + "/?settings=1#/settings")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 20, "the app for settings")
        page.wait("[...document.querySelectorAll('.set')].some(b => b.textContent.includes('Chats and privacy'))", 10, "the settings list")
        page.js("[...document.querySelectorAll('.set')].find(b => b.textContent.includes('Chats and privacy')).click()")
        page.wait("!!document.querySelector('.toggle[aria-label=\"Keep recent chats on this phone\"]')", 10, "the setting")
        page.js("document.querySelector('.toggle[aria-label=\"Keep recent chats on this phone\"]').click()")
        page.wait("!document.querySelector('.toggle[aria-label=\"Keep recent chats on this phone\"]') || document.querySelector('.toggle[aria-label=\"Keep recent chats on this phone\"]').getAttribute('aria-checked') === 'false'", 5, "the switch to turn off")
        time.sleep(1)
        check("switching it off removes the saved copy at once", page.js(IDB) == "")
        httpx.post(url + "/api/jobs", headers=headers, json={"prompt": "battery status", "conversation": "gamma"})
        time.sleep(9)
        check("and nothing is saved while it is off", page.js(IDB) == "")
        page.js("document.querySelector('.toggle[aria-label=\"Keep recent chats on this phone\"]').click()")
        page.wait("(async () => { const s = await " + IDB.strip().rstrip(";") + "; return s.length > 0; })()", 20, "saving again")
        check("switching it on again saves again", True)
        page.js("[...document.querySelectorAll('.set')].find(b => b.textContent.includes('Clear saved chats')).click()")
        time.sleep(1)
        check("'Clear saved chats' removes it", page.js(IDB) == "")
        page.js("document.querySelector('.toggle[aria-label=\"Keep recent chats on this phone\"]').getAttribute('aria-checked')")

        # ---------------------------------------------------------------- removed on the PC: the saved copy goes too
        page.js("history.back(); true")
        time.sleep(1)
        httpx.post(url + "/api/jobs", headers=headers, json={"prompt": "pc status", "conversation": "delta"})
        page.wait("(async () => { const s = await " + IDB.strip().rstrip(";") + "; return s.length > 0; })()", 25, "a saved copy again")
        for d in accounts.devices():
            accounts.remove(d["id"])
        page.wait("!!document.querySelector('.auth')", 25, "the signed-out screen")
        time.sleep(1)
        check("a phone removed on the PC loses its saved chats", page.js(IDB) == "")
        page.shot("s5-signed-out.png")
    finally:
        proc.terminate()
        static.shutdown()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
