"""Saved chats: the files that make it work are served, listed where they must be, and its rules pass their own tests."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
SW = (WEB / "sw.js").read_text(encoding="utf-8")
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")


def test_offline_js_is_served_and_loaded_before_the_page_script(settings):
    res = TestClient(create_app(settings)).get("/static/offline.js")
    assert res.status_code == 200 and len(res.content) > 2000 and res.headers["cache-control"] == "no-cache"
    assert INDEX.index("/static/offline.js") < INDEX.index("/static/app.js")  # the page reads AfpOffline when it starts


def test_the_page_keeps_offline_js_for_opening_without_the_pc():
    assert '"/static/offline.js"' in SW  # a browser that opens the page with the PC off still has it
    assert '"offline.js"' in GRADLE  # and the app's built-in copy of the page, for the same reason


def test_the_banner_has_a_place_in_the_page():
    assert 'id="savedBar"' in INDEX


def test_nothing_reaches_the_pc_while_saved_chats_are_shown():
    api = APP[APP.index("async function api("):]
    api = api[:api.index("const res = await fetch")]
    assert "S.offline" in api  # every call through api() is refused first
    assert "if (S.offline) return;" in APP[APP.index("async function refresh()"):APP.index("async function refresh()") + 400]  # and so is the poll that would replace them
    assert "S.offline ||" in APP[APP.index("async function send("):APP.index("async function send(") + 800]  # a request typed then is held on the phone, not sent


def test_the_saved_copy_is_wiped_on_every_way_out():
    assert APP.count("Saved.clear()") >= 4  # sign out, the PC removing the phone, the switch, the clear button


def test_chat_text_is_never_written_into_the_page_as_markup_by_the_saved_chats_code():
    offline = (WEB / "offline.js").read_text(encoding="utf-8")
    assert "innerHTML" not in offline and "eval(" not in offline and "document." not in offline  # it only stores and returns data


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_saved_chat_rules_pass_their_own_tests():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "offline_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
