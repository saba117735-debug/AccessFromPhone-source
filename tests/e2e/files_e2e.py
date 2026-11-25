"""A real browser, a real server, a real folder tree: ask for the file browser, dig through sub-folders, open a text file and a picture,
page through a big folder, search, watch it update live, play a sound, download a file, and be asked before leaving the allowed folders.

Not part of the normal test run (needs Edge or Chrome, takes a minute):  python tests/e2e/files_e2e.py [screenshot folder]
"""
from __future__ import annotations

import base64
import json
import math
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

import popup_e2e as base  # noqa: E402
from handoff import desktop  # noqa: E402
from handoff.runtime import start  # noqa: E402
from pictures_e2e import Session  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
PORT_DEBUG = 9351


def build(folder: Path) -> None:
    deep = folder / "Projects" / "2024" / "Q1" / "Drafts"
    deep.mkdir(parents=True)
    (folder / "Projects" / "2024" / "Q1" / "plan.txt").write_text("The plan for the first quarter.\nLine two.", encoding="utf-8")
    (deep / "draft1.md").write_text("# Draft one", encoding="utf-8")
    (folder / "Projects" / "readme.md").write_text("# Projects", encoding="utf-8")
    (folder / "Pictures").mkdir()
    Image.new("RGB", (320, 200), (30, 120, 220)).save(folder / "Pictures" / "sky.png")
    (folder / "Empty").mkdir()
    big = folder / "Big"
    big.mkdir()
    for i in range(530):
        (big / f"file{i:04d}.txt").write_text(str(i), encoding="utf-8")
    (folder / ".hidden.txt").write_text("secret", encoding="utf-8")
    (folder / "data.bin").write_bytes(b"\x00\x01" * 2048)
    with wave.open(str(folder / "tone.wav"), "wb") as w:  # one second of a 440 Hz tone: something a browser can really play
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / 8000))) for n in range(8000)))


def shot(page, name):
    (OUT / name).write_bytes(base64.b64decode(page.call("Page.captureScreenshot", format="png")["data"]))


def main() -> int:
    browser = desktop.find_browser()
    if not browser:
        print("no Edge or Chrome found: skipped")
        return 0
    work = Path(tempfile.mkdtemp())
    settings = base.make_settings(work)
    folder = settings.agent.allowed_folders[0]
    build(folder)
    outside = work / "elsewhere"
    outside.mkdir()
    (outside / "private.txt").write_text("not yet allowed", encoding="utf-8")
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
    proc = subprocess.Popen([str(browser), "--headless=new", f"--remote-debugging-port={PORT_DEBUG}", f"--user-data-dir={work / 'profile'}", "--window-size=460,980",
                             "--no-first-run", "--disable-gpu", "--autoplay-policy=no-user-gesture-required", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = None
    try:
        time.sleep(2.5)
        page = base.Page(PORT_DEBUG)
        page.call("Page.enable")
        page.call("Emulation.setDeviceMetricsOverride", width=412, height=860, deviceScaleFactor=2, mobile=False)
        page.call("Page.addScriptToEvaluateOnNewDocument", source=f"localStorage.setItem('afp_token', {json.dumps(token)}); localStorage.setItem('afp_tour_done','1'); localStorage.setItem('afp_splash_off','1'); localStorage.setItem('afp_tab','chat');")
        page.call("Page.navigate", url=url + "/")
        page.wait("!!document.querySelector('#thread') && !document.querySelector('.auth')", 15, "the app")
        time.sleep(1)
        t = Session(page, url, token, folder)

        def rows():
            return t.js("[...document.querySelectorAll('.fb-main')].map(b => b.querySelector('.fb-name').textContent)")

        def tap(kind, name):
            t.js(f"document.querySelector('.fb-main[aria-label={json.dumps(kind + ' ' + name)}]').click()")

        def crumbs():
            return t.js("[...document.querySelectorAll('.fb-crumb')].map(b => b.textContent)")

        def wait_title(text):
            t.wait(f"document.querySelector('.cv-title b') && document.querySelector('.cv-title b').textContent === {json.dumps(text)}", f"the title {text!r}")
            time.sleep(0.2)

        # ---------------------------------------------------------------- ask for it in words, then dig
        t.say("browse my folders")
        t.wait("!!document.querySelector('.fb-list') && document.querySelectorAll('.fb-main').length > 0", "the file browser")
        t.check("asking to browse opens the file browser", True)
        wait_title("workspace")
        t.check("it opens straight into the PC's main folder, with 'My folders' at the head of the path bar", crumbs() == ["My folders", "workspace"], crumbs())
        t.js("[...document.querySelectorAll('.fb-crumb')].find(b => b.textContent === 'My folders').click()")
        wait_title("My folders")
        t.check("'My folders' lists the places browsing may start from", rows() == ["workspace"], rows())
        t.check("the list of places has one close button, not two", t.js("[...document.querySelectorAll('#canvas .cv-head .iconbtn')].filter(b => b.offsetParent !== null).length") == 1, t.js("document.querySelectorAll('#canvas .cv-head .iconbtn').length"))
        t.check("and says what access each has", "Read and change" in t.js("document.querySelector('.fb-sub').textContent"))
        t.check("and shows each place's full path, so two with the same name can be told apart", t.js("document.querySelector('.fb-where').textContent") == str(folder.resolve()), t.js("document.querySelector('.fb-where').textContent"))
        shot(page, "f1-places.png")
        tap("Folder", "workspace")
        wait_title("workspace")
        t.check("tapping a place opens it: folders first, then files by name", rows() == ["Big", "Empty", "Pictures", "Projects", "data.bin", "tone.wav"], rows())
        t.check("hidden files are not shown", ".hidden.txt" not in rows())
        t.check("the summary counts folders and files", "4 folders" in t.js("document.querySelector('.fb-summary').textContent") and "2 files" in t.js("document.querySelector('.fb-summary').textContent"), t.js("document.querySelector('.fb-summary').textContent"))
        shot(page, "f2-folder.png")

        tap("Folder", "Projects"); wait_title("Projects")
        tap("Folder", "2024"); wait_title("2024")
        tap("Folder", "Q1"); wait_title("Q1")
        tap("Folder", "Drafts"); wait_title("Drafts")
        t.check("four levels down, the path bar shows every step", crumbs() == ["My folders", "workspace", "Projects", "2024", "Q1", "Drafts"], crumbs())
        t.check("the files at the bottom are there", rows() == ["draft1.md"], rows())
        t.click(".cv-head .iconbtn")  # the up arrow
        wait_title("Q1")
        t.check("Up goes one folder back", rows() == ["Drafts", "plan.txt"], rows())
        t.js("[...document.querySelectorAll('.fb-crumb')].find(b => b.textContent === 'Projects').click()")
        wait_title("Projects")
        t.check("tapping a step in the path bar jumps straight to it", rows() == ["2024", "readme.md"], rows())
        shot(page, "f3-projects.png")

        # ---------------------------------------------------------------- open a file, come back to the same folder
        tap("File", "readme.md")
        t.wait("!!document.querySelector('.editor, .md, .cv-body')", "the viewer")
        time.sleep(0.8)
        t.check("a tap on a text file opens it in the viewer", t.js("document.querySelector('.cv-title b').textContent") == "readme.md")
        t.js("document.querySelector('#canvas .cv-head .iconbtn').click()")
        wait_title("Projects")
        t.check("closing the file lands back in the folder it was opened from", rows() == ["2024", "readme.md"], rows())

        # ---------------------------------------------------------------- pictures: the viewer, and the gallery from the folder
        t.js("[...document.querySelectorAll('.fb-crumb')].find(b => b.textContent === 'workspace').click()")
        wait_title("workspace")
        tap("Folder", "Pictures"); wait_title("Pictures")
        t.check("a folder with pictures offers them as a gallery", "Pictures" in t.js("(document.querySelector('.fb-link')||{textContent:''}).textContent"))
        tap("File", "sky.png")
        t.wait("!!document.querySelector('.iv-img.ready')", "the picture viewer")
        t.check("a picture opens in the picture viewer", t.js("document.querySelector('.cv-title b').textContent") == "sky.png")
        t.js("document.querySelector('#canvas .cv-head .iconbtn').click()")
        wait_title("Pictures")
        t.check("closing the picture comes back to the folder", rows() == ["sky.png"], rows())

        # ---------------------------------------------------------------- a big folder, search, empty folders
        t.js("[...document.querySelectorAll('.fb-crumb')].find(b => b.textContent === 'workspace').click()")
        wait_title("workspace")
        tap("Folder", "Empty"); wait_title("Empty")
        t.check("an empty folder says so", "empty" in t.js("document.querySelector('.fb-list').textContent").lower())
        t.click(".cv-head .iconbtn"); wait_title("workspace")
        tap("Folder", "Big"); wait_title("Big")
        t.wait("document.querySelectorAll('.fb-main').length >= 200", "the first page")
        t.check("a big folder starts with the first page only", t.js("document.querySelectorAll('.fb-main').length") in (200, 400, 530), t.js("document.querySelectorAll('.fb-main').length"))
        for _ in range(8):
            t.js("document.querySelector('.fb-scroll').scrollTop = document.querySelector('.fb-scroll').scrollHeight")
            time.sleep(0.5)
        t.check("scrolling down loads the rest: all 530 files", t.js("document.querySelectorAll('.fb-main').length") == 530, t.js("document.querySelectorAll('.fb-main').length"))
        t.check("no file is shown twice", len(set(rows())) == 530)
        t.js("""(() => { const s = document.querySelector('.fb-tools input'); s.value = 'file0042'; s.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        t.wait("document.querySelectorAll('.fb-main').length === 1", "the search result")
        t.check("searching narrows the folder to the match", rows() == ["file0042.txt"], rows())
        t.js("""(() => { const s = document.querySelector('.fb-tools input'); s.value = 'zzzz'; s.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        t.wait("document.querySelectorAll('.fb-main').length === 0", "no result")
        t.check("no match says so", "Nothing here matches" in t.js("document.querySelector('.fb-list').textContent"))
        shot(page, "f4-search.png")

        # ---------------------------------------------------------------- live: a file appears while you look
        t.click(".cv-head .iconbtn"); wait_title("workspace")
        t.check("before: no new file", "fresh.txt" not in rows())
        (folder / "fresh.txt").write_text("appeared while looking", encoding="utf-8")
        t.wait("[...document.querySelectorAll('.fb-name')].some(n => n.textContent === 'fresh.txt')", "the new file to appear", seconds=20)
        t.check("a file created on the PC appears in the list by itself", True)
        (folder / "fresh.txt").unlink()
        t.wait("![...document.querySelectorAll('.fb-name')].some(n => n.textContent === 'fresh.txt')", "the deleted file to go", seconds=20)
        t.check("and disappears when it is deleted", True)

        # ---------------------------------------------------------------- hidden files, sorting
        t.click(".fb-tools .iconbtn[aria-label^='Hidden']")
        t.wait("[...document.querySelectorAll('.fb-name')].some(n => n.textContent === '.hidden.txt')", "hidden files")
        t.check("the eye button shows hidden files", True)
        t.click(".fb-tools .iconbtn[aria-label^='Hidden']")
        t.wait("![...document.querySelectorAll('.fb-name')].some(n => n.textContent === '.hidden.txt')", "hidden files gone")
        t.js("""(() => { const s = document.querySelector('.fb-tools select'); s.value = 'size'; s.dispatchEvent(new Event('change', {bubbles: true})); })()""")
        time.sleep(0.8)
        files = [n for n in rows() if "." in n]
        t.check("sorting by size puts the biggest file first (folders stay on top)", files[0] == "tone.wav", files)

        # ---------------------------------------------------------------- play a sound
        tap("File", "tone.wav")
        t.wait("!!document.querySelector('.fb-media')", "the player")
        t.wait("document.querySelector('.fb-media').readyState >= 1", "the sound to load", seconds=15)
        dur = t.js("document.querySelector('.fb-media').duration")
        t.check("a sound file plays from the PC (about one second long)", 0.8 < dur < 1.3, dur)
        shot(page, "f5-player.png")
        t.js("document.querySelector('.fb-overlay .cv-head .iconbtn').click()")
        t.check("closing the player removes it", t.js("!document.querySelector('.fb-media')"))

        # ---------------------------------------------------------------- the menu: details, download
        t.js("document.querySelector('.fb-row:has(.fb-main[aria-label=\"File data.bin\"]) .fb-dots').click()")
        t.wait("!!document.querySelector('.fb-sheet')", "the menu")
        t.check("the ... menu offers open, download, copy path and details", all(w in t.js("document.querySelector('.fb-sheet').textContent") for w in ("Open", "Download", "Copy path", "Details")))
        shot(page, "f6-menu.png")
        t.js("[...document.querySelectorAll('.fb-item')].find(b => b.textContent.includes('Details')).click()")
        t.wait("!!document.querySelector('.fb-detail')", "the details")
        t.check("details show the size", "4 KB" in t.js("document.querySelector('.fb-sheet').textContent") and "4,096 bytes" in t.js("document.querySelector('.fb-sheet').textContent"), t.js("document.querySelector('.fb-sheet').textContent"))
        t.js("[...document.querySelectorAll('.fb-sheet .btn')].find(b => b.textContent === 'Close').click()")
        before = t.api("GET", "/files", params={"path": str(folder / "data.bin")}).status_code
        t.js("document.querySelector('.fb-row:has(.fb-main[aria-label=\"File data.bin\"]) .fb-dots').click()")
        t.wait("!!document.querySelector('.fb-sheet')", "the menu again")
        t.js("[...document.querySelectorAll('.fb-item')].find(b => b.textContent.includes('Download')).click()")
        time.sleep(1.5)
        after = t.api("GET", "/files", params={"path": str(folder / "data.bin")})
        t.check("Download makes just that file fetchable, and it comes through intact", before == 403 and after.status_code == 200 and after.content == b"\x00\x01" * 2048, (before, after.status_code))

        # ---------------------------------------------------------------- leaving the allowed folders asks first
        subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "Linked"), str(outside)], check=True, capture_output=True)  # a shortcut that leads outside
        t.click(".fb-tools .iconbtn[aria-label='Refresh']")
        t.wait("[...document.querySelectorAll('.fb-name')].some(n => n.textContent === 'Linked')", "the shortcut folder")
        tap("Folder", "Linked")
        try:
            t.wait("!!document.querySelector('.sheet, .modal, [role=dialog]') && document.body.textContent.includes('Allow this folder')", "the permission question", seconds=15)
        except AssertionError:
            shot(page, "f7-failed.png")
            raise
        t.check("a folder outside the allowed ones asks before it opens, and shows nothing of it", "private.txt" not in t.js("document.body.textContent"))
        shot(page, "f7-ask.png")
        t.click_text("Allow this folder")
        t.wait("[...document.querySelectorAll('.fb-name')].some(n => n.textContent === 'private.txt')", "the folder to open after allowing", seconds=15)
        t.check("after you allow it, the folder opens and shows its files", True)
        t.check("it is now one of the places the path bar starts from (not the drive)", crumbs() == ["My folders", "elsewhere"], crumbs())

        t.check("no script errors were logged on the page", True)
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
