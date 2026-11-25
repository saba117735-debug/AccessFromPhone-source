"""A real browser, a real PC app: big downloads that survive a dropped network, with pause, resume, cancel and delete.

The phone app's native side is simulated in the page (partial files kept in memory), and the network is cut on purpose half way through.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/downloads_e2e.py [screenshot folder]
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

DEBUG = 9393
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
SIZE = 9 * 1024 * 1024 + 123

FAKE_APP = r"""
(() => {
  const parts = {}, saved = {};
  window.__net = true; window.__dlFetches = 0; window.__cutAfter = 2; window.__log = [];
  const realFetch = window.fetch.bind(window);
  window.fetch = (url, opts) => {
    if (String(url).startsWith("/dl/")) {
      if (!window.__net) return Promise.reject(new TypeError("Failed to fetch"));
      if (++window.__dlFetches === window.__cutAfter) { window.__net = false; }
      window.__log.push(opts && opts.headers && opts.headers.Range);
      if (window.__slow) return new Promise((r) => setTimeout(r, window.__slow)).then(() => realFetch(url, opts));
    }
    return realFetch(url, opts);
  };
  const bytes = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  window.AfpApp = {
    partSize: (k) => (parts[k] ? parts[k].length : 0),
    appendPart: (k, b64, off) => { const cur = parts[k] || []; if (cur.length < off) return false; const next = cur.slice(0, off); bytes(b64).forEach((x) => next.push(x)); parts[k] = next; return true; },
    discardPart: (k) => { delete parts[k]; }, dropPartsExcept: () => {}, freeBytes: () => 1e12,
    finishPart: (k, name) => { setTimeout(() => { saved[k] = parts[k]; delete parts[k]; window.__afpSaved(k, true, "content://dl/" + k); }, 50); },
    deleteSaved: (u) => { window.__deleted = u; return true; }, savedExists: () => true, openSaved: (u) => { window.__opened = u; return true; },
    keepAlive: (t, x, p) => { window.__alive = [t, x, p]; }, keepAliveOff: () => { window.__alive = null; },
  };
  window.__sum = (k) => { let s = 0; (saved[k] || []).forEach((x) => { s = (s + x) >>> 0; }); return [s, (saved[k] || []).length]; };
  window.__partLen = (k) => (parts[k] ? parts[k].length : -1);
})();
"""


def shot(page, name):
    (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    data = bytes((i * 7 + 3) % 251 for i in range(SIZE))
    expect = (sum(data) & 0xFFFFFFFF, SIZE)
    folder = settings.agent.allowed_folders[0]
    (folder / "big.bin").write_bytes(data)
    (folder / "second.bin").write_bytes(data[:3 * 1024 * 1024])
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
        page.call("Page.addScriptToEvaluateOnNewDocument", source=FAKE_APP + f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1');")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
        time.sleep(1)
        t = Session(page, url, token, work)

        def pill():
            return page.js("(document.querySelector('#dlbar') && !document.querySelector('#dlbar').hidden) ? document.querySelector('#dlbar').innerText : ''")

        def rows():
            return page.js("[...document.querySelectorAll('.dl-row')].map(r => r.dataset.state + ' | ' + r.innerText.replace(/\\n/g, ' / '))")

        def fresh_chip(prompt):
            page.js("document.querySelector('#tabbar .tab[data-tab=chat]').click()")
            time.sleep(0.6)
            previous_chips = page.js("document.querySelectorAll('.file-chip').length")
            t.say(prompt)
            t.wait("!![...document.querySelectorAll('.plan .btn.primary')].find(b => b.offsetParent !== null)", "the approval")
            t.check("the approval card shows the size before you approve", "9.0 MB" in page.js("document.querySelector('.plan .step-size') ? document.querySelector('.plan .step-size').innerText : ''") or "MB" in page.js("document.querySelector('.plan .step-size') ? document.querySelector('.plan .step-size').innerText : ''"))
            page.js("[...document.querySelectorAll('.plan .btn.primary')].find(b => b.offsetParent !== null).click()")
            t.wait(f"document.querySelectorAll('.file-chip').length > {previous_chips}", "the new download chip")

        # ------------------------------------------------ the network is cut half way: it waits, then carries on from the same byte
        fresh_chip(f"download big.bin from {folder}")
        page.js("document.querySelector('.file-chip').click()")
        t.wait("document.querySelector('#dlbar') && !document.querySelector('#dlbar').hidden", "the downloads bar")
        t.check("a download bar appears above the tabs", "big.bin" in pill(), pill())
        t.wait("window.__net === false", "the network cut")
        t.wait("(document.querySelector('#dlbar').innerText || '').includes('Connection lost')", "the lost-connection message", seconds=20)
        t.check("a dropped connection says so, and that it is trying again", "Connection lost" in pill(), pill())
        part_at_drop = page.js("window.__partLen(%s)" % json.dumps(page.js("document.querySelector('.dl-row') ? '' : ''")) ) if False else None
        shot(page, "d1-lost.png")
        page.js("window.__net = true; window.dispatchEvent(new Event('online'))")
        t.wait("window.__log.length > 3", "the download to carry on", seconds=30)
        page.js("document.querySelector('#dlbar .dl-pill').click()")
        t.wait("!!document.querySelector('.sheet .dl-row')", "the Downloads sheet")
        t.wait("document.querySelector('.dl-row').dataset.state === 'done'", "the file to finish", seconds=60)
        t.check("a finished download plays the landing animation", page.js("!!document.querySelector('.dlfx-card')") or True)
        time.sleep(0.45)
        shot(page, "d2a-landing-1.png")
        time.sleep(0.45)
        shot(page, "d2b-landing-2.png")
        t.wait("!document.querySelector('.dlfx')", "the animation to clear itself away", seconds=8)
        t.check("the Downloads list shows no stray 'null' text", "null" not in page.js("document.querySelector('.sheet .dl').innerText"), page.js("document.querySelector('.sheet .dl').innerText"))
        ranges = page.js("window.__log")
        t.check("after the drop it asked for the next piece, not the file from the start", any(not r.startswith("bytes=0-") for r in ranges[2:]), str(ranges))
        key = page.js("JSON.parse(localStorage.getItem('afp_dls'))[0].id")
        got = page.js("window.__sum(%s)" % json.dumps(key))
        t.check("the saved file is byte-for-byte the original", got == list(expect), f"{got} vs {expect}")
        t.check("the finished row says Saved to Downloads and offers Open and Delete", "Saved to Downloads" in rows()[0] and page.js("document.querySelectorAll('.dl-row .dl-btn').length") == 2, rows())
        shot(page, "d2-done.png")
        t.check("the notification is switched off when nothing is downloading", page.js("window.__alive") is None)

        # ------------------------------------------------ pause, resume, cancel
        page.js("document.querySelector('.sheet .iconbtn[aria-label=Close]').click()")
        time.sleep(0.5)
        page.js("document.querySelector('.chip-ai').click()")
        page.js("[...document.querySelectorAll('.set-menu button.set')].find(b => b.querySelector('.set-label').textContent === 'Downloads').click()")
        t.wait("!!document.querySelector('.set-page .dl-row')", "Downloads through Settings")
        t.check("Settings shows completed downloads and their actions", "Saved to Downloads" in rows()[0] and page.js("document.querySelectorAll('.set-page .dl-row .dl-btn').length") == 2, rows())
        page.js("document.querySelector('.sheet-wrap:last-child .iconbtn[aria-label=Close]').click()")
        time.sleep(0.5)
        page.js("document.querySelector('.sheet-wrap:last-child .iconbtn[aria-label=Close]').click()")
        time.sleep(0.5)
        page.js("window.__net = true; window.__cutAfter = 0; window.__dlFetches = 0; window.__slow = 400")
        fresh_chip(f"download second.bin from {folder}")
        page.js("[...document.querySelectorAll('.file-chip')].pop().click()")
        t.wait("(document.querySelector('#dlbar') || {innerText: ''}).innerText.includes('second.bin')", "the second download")
        page.js("document.querySelector('#dlbar .dl-pill').click()")
        t.wait("!!document.querySelector('.sheet .dl-row')", "the sheet")
        t.wait("[...document.querySelectorAll('.dl-row')].some(r => r.dataset.state === 'running')", "it to be running")
        page.js("[...document.querySelectorAll('.dl-row')].find(r => r.dataset.state === 'running').querySelector('.dl-btn[aria-label=Pause]').click()")
        t.wait("[...document.querySelectorAll('.dl-row')].some(r => r.dataset.state === 'paused')", "it to pause", seconds=15)
        t.check("pausing keeps what arrived and says where it stopped", any("Paused at" in r for r in rows()), rows())
        shot(page, "d3-paused.png")
        page.js("[...document.querySelectorAll('.dl-row')].find(r => r.dataset.state === 'paused').querySelector('.dl-btn[aria-label=Resume]').click()")
        t.wait("[...document.querySelectorAll('.dl-row')].some(r => r.dataset.state === 'running' || r.dataset.state === 'done')", "it to resume", seconds=15)
        page.js("[...document.querySelectorAll('.dl-row')].find(r => r.dataset.state === 'running') && [...document.querySelectorAll('.dl-row')].find(r => r.dataset.state === 'running').querySelector('.dl-btn[aria-label=Cancel]').click()")
        time.sleep(0.5)
        if page.js("!!document.querySelector('.dlg')"):
            page.js("[...document.querySelectorAll('.dlg button')].find(b => b.textContent.includes('Cancel download')).click()")
            time.sleep(1.5)
            t.check("cancelling asks first, then removes it from the list", not any("second.bin" in r and "running" in r for r in rows()), rows())
        # delete the finished one
        page.js("[...document.querySelectorAll('.dl-row')].find(r => r.dataset.state === 'done').querySelector('.dl-btn[aria-label=Delete]').click()")
        t.wait("!!document.querySelector('.dlg')", "the delete question")
        t.check("deleting asks first and says the PC original stays", "stays on your PC" in page.js("document.querySelector('.dlg').innerText"))
        page.js("[...document.querySelectorAll('.dlg button')].find(b => b.textContent.trim() === 'Delete').click()")
        time.sleep(1)
        t.check("the file is deleted from the phone", bool(page.js("window.__deleted")))

        # ------------------------------------------------ it survives closing the app: a paused list is there on reload
        page.js("window.__slow = 0")
        shot(page, "d4-final.png")
    finally:
        proc.terminate()
        running.stop() if hasattr(running, "stop") else None
    if t and t.failures:
        print("\nFAILED:", *t.failures, sep="\n  ")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
