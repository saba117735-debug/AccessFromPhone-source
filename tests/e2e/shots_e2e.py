"""Takes pictures of every main screen of the phone page (Home, Chat, Files, Settings, Access, each Everyday tool) in a real browser, so a design can be looked at.

Not part of the normal test run (needs Edge or Chrome):
    python tests/e2e/shots_e2e.py <folder> [width=390] [dark|light|both]
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
from handoff.daily import controls  # noqa: E402
from handoff.runtime import start  # noqa: E402

DEBUG = 9393
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
WIDTH = int(sys.argv[2]) if len(sys.argv) > 2 else 390
THEMES = ["dark", "light"] if (sys.argv[3] if len(sys.argv) > 3 else "both") == "both" else [sys.argv[3]]


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp())
    os.environ["HANDOFF_DAILY__FOLDER"] = str(work / "daily")
    controls.press_key = lambda vk, n=1: None
    controls.supported = lambda: True
    settings = base.make_settings(work)
    folder = settings.agent.allowed_folders[0]
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    (folder / "report.pdf").write_bytes(b"%PDF-1.4")
    (folder / "holiday.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (folder / "sub").mkdir(exist_ok=True)
    running = start(settings, quiet=True)
    daily = running.app.state.daily

    class FakeVolume:
        level, muted = 64, False

        def get(self):
            return {"available": True, "level": self.level, "muted": self.muted}

        def set_level(self, percent):
            self.level = percent
            return self.get()

        def set_muted(self, muted):
            self.muted = muted
            return self.get()

    daily.volume = FakeVolume()
    daily._brightness_shell = lambda script: "70" if "CurrentBrightness" in script else ""   # a laptop screen at 70%, never really changed
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    url = f"http://127.0.0.1:{base.PORT}"
    for _ in range(50):
        try:
            httpx.get(url + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    token = httpx.post(url + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Afza's phone"}).json()["token"]
    headers = {"X-Token": token}
    for text in ("list", "what can you do?", "create plan.txt with buy milk"):
        httpx.post(url + "/api/jobs", json={"prompt": text}, headers=headers, timeout=30)
    httpx.post(url + "/api/daily/inbox/text", json={"text": "https://example.com/menu"}, headers=headers)
    httpx.post(url + "/api/daily/inbox/text", json={"text": "Flat 4B, Green Park, call before 6"}, headers=headers)
    httpx.put(url + "/api/daily/inbox/file?name=boarding-pass.pdf", content=b"x" * 250_000, headers=headers)
    httpx.post(url + "/api/daily/notes", json={"text": "Call the bank about the card"}, headers=headers)
    httpx.post(url + "/api/daily/notes", json={"text": "Send the invoice", "todo": True}, headers=headers)
    due = time.time() + 3 * 3600
    httpx.post(url + "/api/daily/tasks", json={"kind": "remind", "due": due, "text": "Stretch your legs", "repeat": "daily"}, headers=headers)
    httpx.post(url + "/api/daily/tasks", json={"kind": "power", "due": due + 7200, "action": "lock"}, headers=headers)
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={work / 'profile'}", f"--window-size={WIDTH + 70},980",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2.5)
        page = base.Page(DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=844, deviceScaleFactor=2, mobile=False)
        for theme in THEMES:
            page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_settings', JSON.stringify({{theme: '{theme}'}}));")
            page.call("Page.navigate", url=url + "/")
            page.wait("!!document.querySelector('#home .hm-strip') && !document.querySelector('.auth')", 20, "Home")
            time.sleep(2.5)

            def shot(name):
                time.sleep(0.9)
                (OUT / f"{theme}-{WIDTH}-{name}.png").write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))

            def click(sel):
                page.js(f"document.querySelector({json.dumps(sel)}).click()")

            def close_sheets():
                for _ in range(3):
                    if page.js("!!document.querySelector('.sheet-wrap:not(.out) .iconbtn[aria-label=Close]')"):
                        click(".sheet-wrap:not(.out) .iconbtn[aria-label=Close]")
                        time.sleep(0.5)

            shot("home-top")
            page.js("document.querySelector('#home').scrollTo(0, 560)")
            shot("home-mid")
            page.js("document.querySelector('#home').scrollTo(0, 99999)")
            shot("home-bottom")
            page.js("document.querySelector('#home').scrollTo(0, 0)")
            for tool in ("inbox", "notes", "tasks", "watch", "recent", "snap", "print", "brief"):
                if page.js(f"!!document.querySelector('#home .dy-tile[data-tool=\"{tool}\"]')"):
                    page.js(f"document.querySelector('#home .dy-tile[data-tool=\"{tool}\"]').scrollIntoView({{block: 'center'}})")
                    click(f'#home .dy-tile[data-tool="{tool}"]')
                    time.sleep(4.5 if tool in ("brief", "recent", "print") else 1.4)
                    shot("tool-" + tool)
                    close_sheets()
            click("#tabbar .tab[data-tab=chat]")
            time.sleep(1.5)
            shot("chat")
            click("#tabbar .tab[data-tab=files]")
            time.sleep(1.5)
            shot("files")
            close_sheets()
            page.js("document.querySelector('#canvas .cv-head .iconbtn') && document.querySelector('#canvas .cv-head .iconbtn').click()")
            time.sleep(0.8)
            click("#tabbar .tab[data-tab=settings]")
            time.sleep(1.2)
            shot("settings")
            close_sheets()
            click("#tabbar .tab[data-tab=access]")
            time.sleep(1.2)
            shot("access")
            close_sheets()
            click("#tabbar .tab[data-tab=home]")
            time.sleep(0.5)
        print("saved to", OUT)
    finally:
        proc.terminate()
        try:
            running.stop()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
