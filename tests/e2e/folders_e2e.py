"""The Admin phone browses every folder phones asked for, long-presses one, taps Share, picks phones, and they get it; another phone sees only its own and what was shared with it.

Not part of the normal test run (needs Edge or Chrome):  python tests/e2e/folders_e2e.py [screenshot folder]
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
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from pictures_e2e import Session  # noqa: E402

DEBUG = 9396
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    docs = work / "Project notes"
    docs.mkdir()
    (docs / "plan.txt").write_text("the plan", encoding="utf-8")
    reports = work / "Reports 2026" / "Quarter one"
    reports.mkdir(parents=True)
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
    ids, tokens = {}, {}
    for name in ("Asha's phone", "Ben's phone", "Chen's phone"):  # the first phone paired is the Admin
        r = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": name}).json()
        ids[name], tokens[name] = r["device"]["id"], {"X-Token": r["token"]}
    for who, folder in (("Ben's phone", docs), ("Chen's phone", reports)):
        httpx.post(url + "/api/access/grant", json={"path": str(folder)}, headers=tokens[who])
        httpx.post(url + "/api/session/end", headers=tokens[who])
    httpx.post(url + "/api/jobs", json={"prompt": "how much space is free on the disk?", "conversation": "one"}, headers=tokens["Ben's phone"])
    httpx.post(url + "/api/session/end", headers=tokens["Ben's phone"])
    httpx.post(url + "/api/jobs", json={"prompt": "show me the newest report", "conversation": "two"}, headers=tokens["Chen's phone"])
    httpx.post(url + "/api/session/end", headers=tokens["Chen's phone"])
    admin_token = tokens["Asha's phone"]["X-Token"]
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
        t = Session(page, url, tokens["Asha's phone"]["X-Token"], work)

        def shot(name):
            (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

        def text(selector):
            return page.js("(() => { const e = document.querySelector(%s); return e ? e.innerText : ''; })()" % json.dumps(selector))

        def row_of(name):
            return "[...document.querySelectorAll('.fb-row')].find(r => r.querySelector('.fb-name').innerText === %s)" % json.dumps(name)

        # Z opens "My files" and sees every folder the phones asked for
        t.click("#tabbar .tab[data-tab=files]")
        t.wait("!!document.querySelector('.fb-row')", "the list of places", seconds=15)
        time.sleep(0.8)
        names = page.js("[...document.querySelectorAll('.fb-row .fb-name')].map(b => b.innerText).sort().join('|')")
        t.check("the Admin sees the folders every phone asked for", "Project notes" in names and "Quarter one" in names, names)
        t.check("each says which phone allowed it, and that it is not shared yet", "Allowed by Ben's phone · Not shared" in text(".fb-list") and "Allowed by Chen's phone · Not shared" in text(".fb-list"), text(".fb-list"))
        shot("f1-places.png")

        # long press: the menu has the usual items and Share
        page.js(f"{row_of('Project notes')}.querySelector('.fb-dots').click()")
        t.wait("!!document.querySelector('.fb-sheet')", "the folder menu")
        items = page.js("[...document.querySelectorAll('.fb-item span')].map(s => s.innerText).filter(Boolean).join('|')")
        t.check("the menu has Open folder, Pictures, Copy path, Details and Share", items == "Open folder|Pictures in this folder|Copy path|Details|Share", items)
        shot("f2-menu.png")
        page.js("[...document.querySelectorAll('.fb-item')].find(b => b.innerText.trim() === 'Share').click()")
        t.wait("!!document.querySelector('.acc-share .acc-share-row')", "the Share sheet")
        time.sleep(0.6)
        t.check("the Share sheet lists the other phones, but not the owner or the Admin phone", page.js("[...document.querySelectorAll('.acc-share-top b')].map(b => b.innerText).sort().join('|')") == "Chen's phone")
        t.check("Share starts disabled until someone is chosen", page.js("document.querySelector('.acc-share .btn.primary').disabled"))
        shot("f3-share.png")
        page.js("[...document.querySelectorAll('.acc-share-row')].find(r => r.innerText.includes('Chen')).querySelector('.acc-share-top').click()")
        time.sleep(0.4)
        t.check("choosing a phone shows Read only / Can edit, and the Share button wakes up", page.js("(() => { const r = [...document.querySelectorAll('.acc-share-row')].find(x => x.innerText.includes('Chen')); return r.classList.contains('on') && r.querySelector('.acc-seg-b.on').innerText.startsWith('Read only') && !document.querySelector('.acc-share .btn.primary').disabled; })()"))
        t.check("a folder its owner only let be read cannot be shared as editable", page.js("[...document.querySelectorAll('.acc-share-row')].find(r => r.innerText.includes('Chen')).querySelectorAll('.acc-seg-b')[1].disabled"))
        shot("f4-share-on.png")

        # a failed share: clear message, the choice is kept, and trying again works
        page.js("window.__failShare = true; window.__realFetch = window.fetch; window.fetch = (u, o) => (window.__failShare && String(u).includes('/folders/share')) ? Promise.reject(new TypeError('Failed to fetch')) : window.__realFetch(u, o);")
        page.js("document.querySelector('.acc-share .btn.primary').click()")
        t.wait("!document.querySelector('.acc-share-error').hidden", "the failure message")
        t.check("a failed share says so, keeps the choice, and offers Try again", "Not shared" in text(".acc-share-error") and "choices are kept" in text(".acc-share-error") and "Try again" in text(".acc-share .btn.primary") and page.js("[...document.querySelectorAll('.acc-share-row')].find(r => r.innerText.includes('Chen')).classList.contains('on')"), text(".acc-share-error"))
        t.check("and nothing was shared", [g for g in httpx.get(url + "/api/info", headers=tokens["Chen's phone"]).json()["folders"]["granted_details"] if g["shared"]] == [])
        shot("f5-share-failed.png")
        page.js("window.__failShare = false")
        page.js("document.querySelector('.acc-share .btn.primary').click()")
        t.wait("!!document.querySelector('.acc-share-state.ok')", "the success message")
        t.check("a good share says who got it and what they can do", "Shared with Chen's phone" in text(".acc-share-state.ok") and "Read only" in text(".acc-share-state.ok"), text(".acc-share-state.ok"))
        shot("f6-share-done.png")
        shared = httpx.get(url + "/api/info", headers=tokens["Chen's phone"]).json()["folders"]["granted_details"]
        t.check("Chen's phone now has Ben's folder, marked as shared with it", sorted((g["mine"], g["shared"]) for g in shared) == [(False, True), (True, False)], str(shared))
        t.check("and Ben still cannot see Chen's folder", [g["path"] for g in httpx.get(url + "/api/info", headers=tokens["Ben's phone"]).json()["folders"]["granted_details"]] == [str(docs.resolve())])
        page.js("document.querySelector('.acc-share .btn.primary').click()")
        time.sleep(0.9)
        t.check("the sheet closes and the list now says who it is shared with", "Shared with Chen's phone" in text(".fb-list") and not page.js("!!document.querySelector('.acc-share')"), text(".fb-list"))

        # Details shows it as well; and Share can stop it again
        page.js(f"{row_of('Project notes')}.querySelector('.fb-dots').click()")
        time.sleep(0.4)
        page.js("[...document.querySelectorAll('.fb-item')].find(b => b.innerText.trim() === 'Details').click()")
        time.sleep(0.4)
        t.check("Details shows who allowed it and who it is shared with", "Chen's phone" in text(".fb-sheet") and "Ben's phone" in text(".fb-sheet"), text(".fb-sheet"))
        page.js("[...document.querySelectorAll('.fb-sheet .btn')].find(b => b.innerText.trim() === 'Close').click()")
        time.sleep(0.4)
        page.js(f"{row_of('Project notes')}.querySelector('.fb-dots').click()")
        time.sleep(0.4)
        page.js("[...document.querySelectorAll('.fb-item')].find(b => b.innerText.trim() === 'Share').click()")
        t.wait("!!document.querySelector('.acc-share .acc-share-row')", "the Share sheet again")
        time.sleep(0.5)
        t.check("reopening Share shows Chen's phone already on", page.js("[...document.querySelectorAll('.acc-share-row')].find(r => r.innerText.includes('Chen')).classList.contains('on')"))
        page.js("[...document.querySelectorAll('.acc-share-row')].find(r => r.innerText.includes('Chen')).querySelector('.acc-share-top').click()")
        time.sleep(0.3)
        page.js("document.querySelector('.acc-share .btn.primary').click()")
        t.wait("!!document.querySelector('.acc-share-state.ok')", "the stopped message")
        t.check("stopping says so, and Chen's phone no longer has it", "Sharing stopped" in text(".acc-share-state.ok") and all(g["mine"] for g in httpx.get(url + "/api/info", headers=tokens["Chen's phone"]).json()["folders"]["granted_details"]))
        page.js("document.querySelector('.acc-share .btn.primary').click()")
        time.sleep(0.8)
        t.check("no script errors on the page", page.js("window.__errs.length === 0"), page.js("JSON.stringify(window.__errs)"))

        # a normal phone: its own folder, and no Share anywhere
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(tokens['Chen' + chr(39) + 's phone']['X-Token'])});")
        httpx.post(url + "/api/folders/share", json={"path": str(docs), "targets": [{"id": ids["Chen's phone"], "change": False}]}, headers=tokens["Asha's phone"])
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Chen's Home")
        time.sleep(1.2)
        t.click("#tabbar .tab[data-tab=files]")
        t.wait("!!document.querySelector('.fb-row')", "Chen's list of places", seconds=15)
        time.sleep(0.6)
        listing = text(".fb-list")
        t.check("Chen's phone sees its own folder and the one shared with it, marked", "Quarter one" in listing and "Project notes" in listing and "Shared with you" in listing, listing)
        page.js(f"{row_of('Project notes')} && {row_of('Project notes')}.querySelector('.fb-main').dispatchEvent(new MouseEvent('contextmenu', {{bubbles: true, cancelable: true}}))")
        time.sleep(0.5)
        items = page.js("[...document.querySelectorAll('.fb-item span')].map(s => s.innerText).filter(Boolean).join('|')")
        t.check("but a normal phone has no Share option", "Share" not in items.split("|"), items)
        shot("f7-chen.png")
    finally:
        proc.terminate()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    failures = t.failures if t else ["the browser never started"]
    print("\n" + ("ALL PASSED" if not failures else "FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
