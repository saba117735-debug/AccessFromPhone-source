"""A real browser, a real server: the PC goes away, the popup blocks the page, the PC comes back, the popup closes.

Not part of the normal test run (it needs Edge or Chrome and takes about a minute):  python tests/e2e/popup_e2e.py
Screenshots land in the folder given as the first argument (default: the current folder).
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from handoff import desktop  # noqa: E402
from handoff.config import load_settings  # noqa: E402
from handoff.runtime import start  # noqa: E402

PORT, ADMIN = 8791, 8792
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()


def make_settings(folder: Path):
    (folder / "workspace").mkdir(exist_ok=True)
    (folder / "config.yaml").write_text(
        f"server:\n  host: 127.0.0.1\n  port: {PORT}\n  https: false\n  open_connect_page: false\n"
        f"accounts:\n  sign_in: password\n  admin_port: {ADMIN}\n"
        "connectivity:\n  mdns: false\npower:\n  keep_awake: false\n"
        "agent:\n  allowed_folders: [workspace]\nllm:\n  provider: demo\n"
        "storage:\n  results_dir: data/results\n  snapshots_dir: data/snapshots\nlogging:\n  audit_file: data/audit.log\n", encoding="utf-8")
    return load_settings(folder / "config.yaml")


class Page:
    """A tiny Chrome DevTools Protocol client."""

    def __init__(self, port: int):
        targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
        self.ws = connect(next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"], max_size=None)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.n:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]

    def js(self, expression: str):
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)["result"]
        return result.get("value")

    def shot(self, name: str):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        (OUT / name).write_bytes(base64.b64decode(data))

    def wait(self, expression: str, seconds: float, what: str):
        end = time.time() + seconds
        while time.time() < end:
            if self.js(expression):
                return
            time.sleep(0.4)
        raise AssertionError(f"timed out waiting for: {what}")


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = make_settings(work)
    running = start(settings, quiet=True)
    accounts = running.app.state.accounts
    accounts.setup_owner("Owner", "correct horse")
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(50):
        try:
            httpx.get(base + "/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.2)
    token = httpx.post(base + "/pair/claim", json={"code": accounts.new_pairing_code()[0], "name": "Test phone"}).json()["token"]

    proc = subprocess.Popen([str(browser), "--headless=new", "--remote-debugging-port=9333", f"--user-data-dir={work / 'profile'}", "--window-size=420,860",
                             "--no-first-run", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []

    def check(name, ok, detail=""):
        print(("ok   " if ok else "FAIL ") + name + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        time.sleep(2.5)
        page = Page(9333)
        page.call("Page.enable")
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_tab','chat');")
        page.call("Page.navigate", url=base + "/")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 15, "the app to load, signed in")
        time.sleep(1)
        check("app loaded and no popup while the PC answers", page.js("!document.querySelector('#lost')"))
        page.shot("1-connected.png")

        # ---- the PC goes away
        running.stop()
        t0 = time.time()
        time.sleep(4)
        check("nothing is shown during the grace period", page.js("!document.querySelector('#lost')"))
        page.wait("!!document.querySelector('#lost')", 25, "the popup")
        seconds = round(time.time() - t0, 1)
        check("popup appears after the grace period (about 8-14 s)", 6 <= seconds <= 20, f"{seconds}s")
        time.sleep(1.2)
        check("popup is a real alert dialog", page.js("document.querySelector('#lost').getAttribute('role')") == "alertdialog")
        check("the app behind it is inert (no taps, no keyboard)", page.js("document.querySelector('#app').hasAttribute('inert')"))
        check("the page is told it is blocked", page.js("document.body.classList.contains('is-lost')"))
        check("title says what is wrong", page.js("document.querySelector('#lostTitle').textContent") == "Can't reach your PC", page.js("document.querySelector('#lostTitle').textContent"))
        check("a countdown or a check is showing", "Check" in (page.js("document.querySelector('#lostStatus').textContent") or ""))
        check("Try again and Switch PC are offered", page.js("!!document.querySelector('#lostRetry') && !!document.querySelector('#lostSwitch')"))
        # a tap on the app behind the popup must reach nothing
        reached = page.js("(() => { const b = document.querySelector('.menu-btn'); if (!b) return 'no button'; const r = b.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return top && top.closest('#lost') ? 'blocked' : 'REACHED'; })()")
        check("a tap on the app's own buttons is caught by the popup", reached in ("blocked", "no button"), reached)
        page.shot("2-popup.png")

        # ---- Try again while the PC is still away: the popup stays
        page.js("document.querySelector('#lostRetry').click()")
        time.sleep(3)
        check("a retry that fails keeps the popup", page.js("!!document.querySelector('#lost')"))

        # ---- the PC comes back
        running = start(settings, quiet=True)
        for _ in range(50):
            try:
                httpx.get(base + "/healthz"); break
            except httpx.HTTPError:
                time.sleep(0.2)
        t1 = time.time()
        page.wait("!document.querySelector('#lost')", 25, "the popup to close by itself")
        check("popup closes by itself when the PC is back", True)
        check("it took a sensible time (under 15 s)", time.time() - t1 < 15, f"{round(time.time() - t1, 1)}s")
        check("the app is usable again (not inert)", page.js("!document.querySelector('#app').hasAttribute('inert') && !document.body.classList.contains('is-lost')"))
        check("'Back online' is shown", page.js("document.querySelector('#toasts').textContent.includes('Back online')"))
        time.sleep(1)
        page.shot("3-recovered.png")

        # ---- a phone that was removed on the PC sees "signed out", not "can't reach"
        for d in accounts.devices():
            accounts.remove(d["id"])
        page.wait("!!document.querySelector('.auth')", 25, "the signed-out screen")
        check("a removed phone gets the sign-in screen, never the PC-is-away popup", page.js("!document.querySelector('#lost')"))
        page.shot("4-signed-out.png")
    finally:
        proc.terminate()
        try:
            running.stop()
        except Exception:
            pass
    print(f"\n{'all passed' if not failures else str(len(failures)) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
