"""The phone and PC pages for screen viewing: files registered everywhere, nothing a phone could use to approve itself, and nothing left running."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
SCREEN = (WEB / "screen.js").read_text(encoding="utf-8")
ACCESS = (WEB / "access.js").read_text(encoding="utf-8")
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
SW = (WEB / "sw.js").read_text(encoding="utf-8")
ADMIN = (WEB / "admin.html").read_text(encoding="utf-8")
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
LOCAL = (ROOT / "android" / "app" / "src" / "main" / "java" / "app" / "accessfromphone" / "LocalPage.kt").read_text(encoding="utf-8")


def test_screen_js_is_served_and_registered_everywhere(settings):
    res = TestClient(create_app(settings)).get("/static/screen.js")
    assert res.status_code == 200 and len(res.content) > 4000
    assert INDEX.index("/static/screen.js") < INDEX.index("/static/app.js")
    assert '"/static/screen.js"' in SW and '"screen.js"' in GRADLE and '"static/screen.js"' in LOCAL


def test_a_phone_can_only_ask_look_and_stop_never_approve():
    assert "/admin/" not in SCREEN and "/decide" not in SCREEN and "/grant" not in SCREEN and "/revoke" not in SCREEN
    for route in ('"/request"', '"/start"', '"/stop"', '"/cancel"'):
        assert route in SCREEN or route.replace('"', "'") in SCREEN
    assert "/screen/frame" in SCREEN


def test_the_pc_page_is_where_the_owner_answers():
    for route in ("/admin/screen", "/admin/screen/decide", "/admin/screen/grant", "/admin/screen/revoke", "/admin/screen/stop"):
        assert route in ADMIN
    assert "Screen viewing" in ADMIN and "screenTimer" in ADMIN


def test_leaving_the_screen_ends_the_viewing_and_the_request():
    close = SCREEN[SCREEN.index("function close()"):SCREEN.index("function render()")]
    assert '"/stop"' in close and '"/cancel"' in close and "endLoop()" in close  # nothing is left running on the PC


def test_the_picture_loop_stops_when_the_phone_is_not_looking():
    loop = SCREEN[SCREEN.index("function beginLoop()"):SCREEN.index("function endLoop()")]
    assert "document.hidden" in loop and "token !== st.loop" in loop and "isConnected" in loop


def test_chat_shortcut_and_the_access_entry_exist_and_never_send_the_words_to_the_assistant():
    send = APP[APP.index("async function send("):APP.index("async function send(") + 900]
    assert "Screen.wants(text)" in send and send.index("Screen.wants(text)") < send.index("queueRequest(text)")
    assert "openScreen" in APP and "d.openScreen" in ACCESS


def test_the_desktop_banner_is_only_made_by_the_real_app():
    runtime = (ROOT / "src" / "handoff" / "runtime.py").read_text(encoding="utf-8")
    server = (ROOT / "src" / "handoff" / "server.py").read_text(encoding="utf-8")
    assert "DesktopBanner" in runtime and "DesktopBanner" not in server  # tests build the app without ever opening a window


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_screen_wording_and_chat_shortcut_pass_their_own_tests():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "screen_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def test_control_is_a_separate_switch_sent_in_order_and_never_approved_by_the_phone():
    assert '"/control"' in SCREEN and '"/screen/input"' in SCREEN
    flush = SCREEN[SCREEN.index("function flush()"):SCREEN.index("function coalesce(")]
    assert "st.chain" in flush and "session_held" in flush  # one batch at a time, in order; losing the turn ends control on the phone too
    assert "/admin/" not in SCREEN and "/grant" not in SCREEN


def test_the_cards_are_redrawn_only_when_something_shown_changes():
    assert "st.sig" in SCREEN and "patchText" in SCREEN  # the PC screen sheet
    render = ACCESS[ACCESS.index("function render(body)"):ACCESS.index("function build(body)")]
    assert "sig === st.sig" in render and "build(body)" in render  # the Access screen
    assert "st.peopleFresh" in ACCESS and "st.barSig" in ACCESS  # the people list is fetched once per visit, and the banner is not rebuilt for the same words


def test_the_picture_is_never_cropped_by_its_own_box_and_full_screen_uses_the_visible_area():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    stage = css[css.index(".scr-stage { position: relative;"):css.index("\n", css.index(".scr-stage { position: relative;"))]
    assert "aspect-ratio: var(--ar" in stage and "62dvh * var(--arn" in stage  # the box takes the picture's own shape and shrinks to fit the height limit, instead of keeping 16:9 and being clipped
    assert "max-height: 62dvh" not in stage
    assert "object-fit: contain" in css[css.index(".scr-img {"):css.index("}", css.index(".scr-img {"))]
    assert "function layoutFull(" in SCREEN and "visualViewport" in SCREEN  # full screen is sized to what can be seen, not to the page
    assert "var(--vw" in css and "var(--vh" in css
    turn = SCREEN[SCREEN.index("function onTurn("):SCREEN.index("window.addEventListener(\"resize\", onTurn)")]
    assert "enterFull()" in turn and "exitFull()" in turn and "noAuto" in turn  # sideways goes to full screen, upright comes back, and Exit is respected


def test_zoom_asks_the_pc_for_the_part_instead_of_enlarging_the_picture():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert "&rx=" in SCREEN and "&rw=" in SCREEN and "function zoomTo(" in SCREEN and "function createPinch(" in SCREEN
    assert "scr-zoomchip" in SCREEN and "scr-zoomchip" in css and ".scr-stage.grab { touch-action: none" in css
    assert ".scr-stage.zoom" not in css and "width: 220%" not in css and "width: 200%" not in css  # the old blurry "enlarge it" zoom is gone
    assert 'dockButton("zoom"' in SCREEN and "zoomin:" in APP and "zoomout:" in APP


def test_the_control_buttons_and_keyboard_exist_with_labels():
    for word in ("Control", "Keys", "Ask the PC owner", "Mouse pad", "Type on the PC", "aria-pressed", "Pointer speed"):
        assert word in SCREEN
    assert 'pointer:' in APP  # the icon
