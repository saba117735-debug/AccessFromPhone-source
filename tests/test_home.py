"""The Home screen: what the PC tells the page so Home can show the right buttons, and that the page has the places to show them."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

WEB = Path(__file__).resolve().parents[1] / "src" / "handoff" / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "app.css").read_text(encoding="utf-8")


class Helper:
    def __init__(self, **info):
        self.info = {"running": True, "configured": True, "enabled": True, "attention": False, "locked": True, "kind": "pin", "locked_for": 0, **info}
        self.calls = 0

    def status(self):
        self.calls += 1
        return self.info


@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.manager._unlocker = Helper()
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    c.app = app
    return c


def test_the_pcs_status_says_whether_it_is_locked_and_whether_unlocking_is_ready(client):
    body = client.get("/api/status").json()
    assert body["unlock"] == {"running": True, "configured": True, "enabled": True, "attention": False, "locked": True}
    assert "kind" not in body["unlock"] and "locked_for" not in body["unlock"]  # only what Home needs


def test_the_status_still_has_everything_it_had_before(client):
    body = client.get("/api/status").json()
    assert "available" in body and "time" in body


def test_the_helper_is_asked_at_most_every_few_seconds_however_often_the_page_polls(client):
    for _ in range(8):
        client.get("/api/status")
    assert client.app.state.manager._unlocker.calls == 1


def test_a_missing_helper_just_means_no_unlock_button(settings):
    from handoff.unlock.client import AgentClient

    app = create_app(settings)
    app.state.manager._unlocker = AgentClient(1, timeout=1)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    unlock = c.get("/api/status").json()["unlock"]
    assert unlock["running"] is False and unlock["configured"] is False


def test_the_info_lists_the_power_actions_home_may_offer(client, settings):
    assert client.get("/api/info").json()["power_actions"] == list(settings.power.remote_actions)


def test_the_page_has_a_home_screen_and_a_tab_bar():
    assert 'id="home"' in INDEX and 'id="tabbar"' in INDEX
    assert INDEX.index('id="home"') < INDEX.index('id="scroller"') < INDEX.index('id="dock"') < INDEX.index('id="tabbar"')  # tabs at the bottom, below the message box


def test_the_five_tabs_and_what_they_do():
    for word in ('["access", "Access", "monitor"]', '["home", "Home", "home"]', '["chat", "Chat", "chat"]', '["files", "Files", "folder"]', '["settings", "Settings", "sliders"]'):
        assert word in APP
    assert 'if (key === "files") openFolder("")' in APP and "else if (key === \"settings\") openSettings()" in APP and '"PC", "gauge"' not in APP


def test_home_never_shows_an_unlock_button_unless_the_helper_is_ready():
    assert "pcLocked() && unlockReady()" in APP and "u.running && u.configured && u.enabled && !u.attention" in APP


def test_home_offers_only_the_power_actions_the_pc_allows():
    assert 'canPower("lock")' in APP and 'canPower("sleep")' in APP


def test_home_and_the_tabs_are_styled_and_the_tab_bar_is_only_for_phones():
    for cls in (".hm-card", ".hm-btn", ".hm-banner", ".hm-step", ".tabbar", ".sugg-row", ".sugg"):
        assert cls in CSS, cls
    assert ".tabbar { display: grid;" in CSS  # the tab bar is the way around on every screen size


def test_the_saved_chats_page_and_offline_mode_never_start_on_home():
    boot = APP[APP.index("async function bootSaved()"):APP.index("async function bootSaved()") + 200]
    assert 'setTab("chat")' in boot


def test_the_phone_is_told_whether_the_screen_is_blacked_out(client):
    assert client.get("/api/info").json()["screen_dark"] is False
