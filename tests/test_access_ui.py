"""The phone side of PC access: the file is served and registered everywhere, the Access tab is in the middle, and the page uses it the right way."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
ACCESS = (WEB / "access.js").read_text(encoding="utf-8")
CSS = (WEB / "app.css").read_text(encoding="utf-8")
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
SW = (WEB / "sw.js").read_text(encoding="utf-8")
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
LOCAL = (ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "accessfromphone" / "LocalPage.kt").read_text(encoding="utf-8")
ADMIN = (WEB / "admin.html").read_text(encoding="utf-8")


def test_access_js_is_served_and_registered_everywhere_the_other_scripts_are(settings):
    res = TestClient(create_app(settings)).get("/static/access.js")
    assert res.status_code == 200 and len(res.content) > 3000
    assert INDEX.index("/static/access.js") < INDEX.index("/static/app.js")  # the page creates it when it starts
    assert '"/static/access.js"' in SW and '"access.js"' in GRADLE and '"static/access.js"' in LOCAL


def test_the_access_tab_sits_in_the_middle_of_five():
    tabs = APP[APP.index("const TAB_DEFS = ["):APP.index("const TAB_DEFS = [") + 300]
    order = [tabs.index(f'"{key}"') for key in ("home", "chat", "access", "files", "settings")]
    assert order == sorted(order)  # Home, Chat, Access, Files, Settings: Access is the third of five
    assert "--tabs: 5" in CSS and '"monitor"' in tabs
    assert "monitor:" in APP  # the icon exists


def test_the_status_check_carries_the_session_and_the_page_hands_it_to_the_component():
    assert "Access.update(d.session || null)" in APP[APP.index("async function loadPcStatus("):APP.index("function pcChip(")]


def test_a_viewer_sees_a_plan_but_not_the_approve_button():
    card = APP[APP.index("function planCard("):APP.index("function liveBot(")]
    assert "Access.isViewer()" in card and card.index("Access.isViewer()") < card.index('"Approve"')


def test_someone_else_using_the_pc_keeps_what_was_typed_and_keeps_the_queue_waiting():
    send = APP[APP.index("async function send("):APP.index("function queueRequest") if "function queueRequest" in APP else APP.index("async function send(") + 3000]
    assert 'session_held' in send and "D.field.value = text" in send
    flush = APP[APP.index("async function flushQueue("):APP.index("function queueTick(")]
    assert "session_held" in flush  # a held request waits for the turn to be free instead of failing


def test_the_component_asks_and_never_decides():
    assert "/session" in ACCESS and "/access/level" in ACCESS
    for word in ("/answer", "/takeover", "/take", "/release"):
        assert word in ACCESS
    assert "localStorage" not in ACCESS and "innerHTML" not in ACCESS  # nothing about turns or levels is kept or trusted on the phone


def test_the_banner_and_the_pc_page_have_their_places():
    assert 'id="sessionBar"' in INDEX
    assert "/admin/access" in ADMIN and "/admin/access/admin" in ADMIN and "/admin/session/end" in ADMIN
    assert "access/admin" not in APP and "/admin/" not in ACCESS  # a phone cannot pick the Admin: that route exists only on the PC page


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_wording_rules_pass_their_own_tests():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "access_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
