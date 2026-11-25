"""Pause (the panic button), the security list, alerts for guessing, and the plain-language approval cards."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.pause import Burst, PauseSwitch
from handoff.server import create_app, security_line

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
ADMIN = (WEB / "admin.html").read_text(encoding="utf-8")


@pytest.fixture
def client(settings):
    app = create_app(settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    c.app = app
    return c


def test_pause_blocks_every_call_until_resumed_on_the_pc(client):
    assert client.get("/api/ping").status_code == 200
    assert client.post("/api/pause").json() == {"ok": True}
    for call in (client.get("/api/ping"), client.get("/api/jobs"), client.get("/api/info"), client.post("/api/jobs", json={"prompt": "hi"})):
        assert call.status_code == 423 and call.json()["detail"] == "paused"
    client.app.state.manager.pause_switch.resume()  # what the PC page's Resume does: a phone has no way to
    assert client.get("/api/ping").status_code == 200


def test_a_phone_cannot_resume(client):
    client.post("/api/pause")
    for path in ("/api/pause/resume", "/api/resume", "/admin/pause/resume"):
        assert client.post(path).status_code in (404, 405, 423)
    assert client.get("/api/ping").status_code == 423


def test_pause_survives_a_restart(tmp_path):
    flag = tmp_path / "paused.flag"
    PauseSwitch(flag).pause("Asha's phone")
    again = PauseSwitch(flag)
    assert again.paused and again.info()["by"] == "Asha's phone"
    again.resume()
    assert not PauseSwitch(flag).paused


def test_pause_is_written_to_the_activity_log(client):
    client.post("/api/pause")
    client.app.state.manager.pause_switch.resume()
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "paused" in events


def test_security_list_shows_only_what_matters(client):
    manager = client.app.state.manager
    manager.note("device_paired", device="Asha's phone", ip="1.2.3.4")
    manager.note("browsed_folder", path="C:/x")
    manager.note("pair_failed", ip="9.9.9.9")
    body = client.get("/api/security").json()
    kinds = [a["kind"] for a in body["alerts"]]
    assert kinds[:2] == ["pair_failed", "device_paired"]  # newest first
    assert "browsed_folder" not in kinds
    assert body["alerts"][1]["who"] == "Asha's phone"


def test_security_line_ignores_ordinary_events():
    assert security_line({"event": "done"}) is None
    assert security_line({"event": "paused", "by": "x"})["tone"] == "bad"


def test_three_wrong_tries_in_ten_minutes_raise_one_alert():
    t = [0.0]
    burst = Burst(limit=3, window=600, quiet=600, clock=lambda: t[0])
    assert [burst.hit(), burst.hit(), burst.hit()] == [False, False, True]
    assert burst.hit() is False  # one alert, not one per try
    t[0] = 700
    assert [burst.hit(), burst.hit(), burst.hit()] == [False, False, True]  # a new burst later is told again


def test_slow_slips_are_not_an_alert():
    t = [0.0]
    burst = Burst(limit=3, window=600, quiet=600, clock=lambda: t[0])
    results = []
    for _ in range(6):
        t[0] += 400
        results.append(burst.hit())
    assert not any(results)


# ---------------------------------------------------------------- with real phone accounts

from handoff.admin import create_admin_app  # noqa: E402
from handoff.config import load_settings  # noqa: E402
from handoff.planner import DemoPlanner  # noqa: E402

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


@pytest.fixture
def both(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    phone_app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, phone_app.state.accounts, phone_app.state.manager), base_url="http://127.0.0.1:8766")
    assert admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H).status_code == 200
    return TestClient(phone_app), admin, phone_app


def pair(phone, admin, name):
    code = admin.post("/admin/pairing", headers=H).json()["code"]
    return phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]


def test_a_phone_pauses_and_only_the_pc_page_resumes(both):
    phone, admin, app = both
    mine, other = pair(phone, admin, "Asha's phone"), pair(phone, admin, "Old tablet")
    assert phone.post("/api/pause", headers={"x-token": mine}).status_code == 200
    for token in (mine, other):  # every phone is shut out, the one that pressed it included
        assert phone.get("/api/jobs", headers={"x-token": token}).status_code == 423
    assert admin.get("/admin/pause", headers=H).json()["by"] == "Asha's phone"
    assert admin.post("/admin/pause/resume", headers={**H, "X-Admin": "0"}).status_code in (401, 403)  # not without the page's own header and sign-in
    assert admin.post("/admin/pause/resume", headers=H).json()["paused"] is False
    assert phone.get("/api/jobs", headers={"x-token": other}).status_code == 200


def test_the_pc_page_can_pause_too_and_says_who(both):
    phone, admin, _ = both
    token = pair(phone, admin, "Asha's phone")
    admin.post("/admin/pause/start", headers=H)
    assert phone.get("/api/ping", headers={"x-token": token}).status_code == 423
    assert admin.get("/admin/pause", headers=H).json()["by"] == "this PC"


def test_pairing_a_second_phone_and_wrong_codes_show_up_in_the_security_list(both):
    phone, admin, app = both
    first = pair(phone, admin, "Asha's phone")
    pair(phone, admin, "Unknown tablet")
    for _ in range(3):
        phone.post("/pair/claim", json={"code": "wrong-code", "name": "x"})
    body = phone.get("/api/security", headers={"x-token": first}).json()
    kinds = [a["kind"] for a in body["alerts"]]
    assert "device_paired" in kinds and "pair_failed" in kinds
    assert "suspicious" in kinds  # three wrong codes in a row is guessing
    assert body["devices"] == 2


def test_the_page_colours_and_words_approval_cards():
    assert "function planRisk(" in APP and "function plainPlan(" in APP
    assert 'data-risk' in APP and "RISK_TEXT" in APP
    for action, rank in (("delete", 2), ("send", 2), ("run", 2), ("power", 2), ("read", 0), ("write", 1)):
        assert f"{action}: {rank}" in APP  # delete or send is red, read is green, a change is amber


def test_the_page_understands_a_paused_pc_and_keeps_queued_requests():
    assert 'Link.status === 423' in APP and "paused:" in APP
    assert "e.status !== 423" in APP  # a paused PC does not make a queued request 'fail'


def test_the_data_and_control_screen_is_the_first_settings_page():
    assert APP.index('key: "trust"') < APP.index('key: "device"')
    assert '"/pause"' in APP and '"/security"' in APP


def test_only_the_pc_page_can_resume():
    assert "/admin/pause/resume" in ADMIN
    assert "pause/resume" not in (WEB / "app.js").read_text(encoding="utf-8")


def test_one_phones_request_ids_never_return_another_phones_job(both):
    phone, admin, _ = both
    a, b = pair(phone, admin, "Asha's phone"), pair(phone, admin, "Old tablet")
    mine = phone.post("/api/jobs", json={"prompt": "what can you do?", "client_id": "same-id"}, headers={"x-token": a}).json()
    blocked = phone.post("/api/jobs", json={"prompt": "what can you do?", "client_id": "same-id"}, headers={"x-token": b})
    assert blocked.status_code == 409  # only one phone at a time may send requests, so Ben is turned away
    phone.post("/api/session/end", headers={"x-token": a})
    other = phone.post("/api/jobs", json={"prompt": "what can you do?", "client_id": "same-id"}, headers={"x-token": b}).json()
    assert mine["id"] != other["id"]  # and once it is his turn, reusing Asha's id never hands him her job
    phone.post("/api/session/end", headers={"x-token": b})
    again = phone.post("/api/jobs", json={"prompt": "what can you do?", "client_id": "same-id"}, headers={"x-token": a}).json()
    assert again["id"] == mine["id"]  # the same phone retrying still gets its own job back
    changed = phone.post("/api/jobs", json={"prompt": "list my files", "client_id": "same-id"}, headers={"x-token": a}).json()
    assert changed["id"] != mine["id"]  # a different request is never mistaken for the old one
