"""Owner, Admin and users over real HTTP: levels, one person at a time, taking a turn, Pause, and who can change what."""
import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.planner import DemoPlanner
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


@pytest.fixture
def world(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    assert admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H).status_code == 200
    phone = TestClient(app)

    def pair(name):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        return {"x-token": phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]}

    people = {"Asha": pair("Asha's phone"), "Ben": pair("Ben's phone"), "Chen": pair("Chen's phone")}
    return phone, admin, people, app


def device_id(admin, name):
    return next(d["id"] for d in admin.get("/admin/devices", headers=H).json() if d["name"] == name)


def send(phone, who, text="what can you do?"):
    return phone.post("/api/jobs", json={"prompt": text}, headers=who)


def test_the_first_phone_is_admin_and_the_others_are_operators(world):
    phone, admin, people, _ = world
    me = phone.get("/api/session", headers=people["Asha"]).json()["me"]
    assert me["role"] == "admin" and me["level"] == "admin"
    me = phone.get("/api/session", headers=people["Ben"]).json()["me"]
    assert me["role"] == "user" and me["level"] == "operator"


def test_one_person_at_a_time_and_reading_stays_open(world):
    phone, _, people, _ = world
    assert send(phone, people["Ben"]).status_code == 200  # free: Ben simply starts
    blocked = send(phone, people["Chen"])
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "session_held" and blocked.json()["detail"]["holder"] == "Ben's phone"
    assert phone.get("/api/jobs", headers=people["Chen"]).status_code == 200  # Chen can still look
    assert phone.get("/api/session", headers=people["Chen"]).json()["state"] == "other"
    phone.post("/api/session/end", headers=people["Ben"])
    assert send(phone, people["Chen"]).status_code == 200


def test_start_my_turn_really_starts_it_and_a_second_phone_cannot_start_over_it(world):
    phone, _, people, app = world
    started = phone.post("/api/session/start", headers=people["Ben"])
    assert started.status_code == 200 and started.json()["state"] == "mine"
    assert app.state.access.sessions.holder_id() is not None
    again = phone.post("/api/session/start", headers=people["Chen"])
    assert again.status_code == 409 and again.json()["detail"]["holder"] == "Ben's phone"
    assert phone.post("/api/session/start", headers=people["Ben"]).status_code == 200  # starting your own again is harmless


def test_a_view_only_phone_never_takes_a_turn_and_never_blocks_anyone(world):
    phone, admin, people, app = world
    admin.post("/admin/access/level", json={"device_id": device_id(admin, "Ben's phone"), "level": "viewer"}, headers=H)
    assert send(phone, people["Ben"]).status_code == 200  # a read-only request goes through...
    assert app.state.access.sessions.holder_id() is None  # ...without taking the turn
    assert send(phone, people["Chen"]).status_code == 200  # so Chen is not blocked by it
    phone.post("/api/session/end", headers=people["Chen"])
    for path in ("/api/session/start", "/api/session/takeover"):
        refused = phone.post(path, json={}, headers=people["Ben"])
        assert refused.status_code == 403 and "view-only" in refused.json()["detail"]["message"]


def test_the_holders_level_shown_is_the_current_one(world):
    phone, admin, people, _ = world
    send(phone, people["Ben"])
    assert phone.get("/api/session", headers=people["Chen"]).json()["holder"]["level"] == "operator"
    admin.post("/admin/access/level", json={"device_id": device_id(admin, "Ben's phone"), "level": "editor"}, headers=H)
    assert phone.get("/api/session", headers=people["Chen"]).json()["holder"]["level"] == "editor"


def test_a_view_only_phone_can_read_but_not_change_or_approve(world):
    phone, admin, people, _ = world
    ben = device_id(admin, "Ben's phone")
    assert admin.post("/admin/access/level", json={"device_id": ben, "level": "viewer"}, headers=H).status_code == 200
    assert phone.get("/api/info", headers=people["Ben"]).status_code == 200
    assert phone.get("/api/session", headers=people["Ben"]).json()["me"]["level"] == "viewer"
    saved = phone.put("/api/file", params={"path": "x.txt"}, json={"path": "x.txt", "text": "hi"}, headers=people["Ben"])
    assert saved.status_code == 403 and saved.json()["detail"]["code"] == "level" and "view only" in saved.json()["detail"]["message"].lower()
    # a plan that would write, asked by Ben himself: he can read it but never approve it
    job = send(phone, people["Ben"], "create notes.txt with buy milk").json()
    phone.post("/api/session/end", headers=people["Ben"])
    assert job["status"] == "awaiting_approval"
    refused = phone.post(f"/api/jobs/{job['id']}/approve", headers=people["Ben"])
    assert refused.status_code == 403 and refused.json()["detail"]["need"] == "editor"


def test_the_takeover_you_described_user_b_asks_user_a_decides(world):
    phone, _, people, _ = world
    send(phone, people["Ben"])  # Ben is using the PC
    asked = phone.post("/api/session/takeover", json={"message": "need the printer"}, headers=people["Chen"])
    assert asked.status_code == 200 and asked.json()["result"] == "asked" and asked.json()["mine_ask"]["holder"] == "Ben's phone"
    status = phone.get("/api/status", headers=people["Ben"]).json()["session"]  # Ben's regular check carries the question
    assert status["ask"]["name"] == "Chen's phone" and status["ask"]["message"] == "need the printer"
    assert phone.get("/api/status", headers=people["Chen"]).json()["session"]["ask"] is None  # only the holder is asked
    done = phone.post("/api/session/answer", json={"decision": "end"}, headers=people["Ben"]).json()
    assert done["result"] == "handed over" and done["state"] == "other"
    mine = phone.get("/api/session", headers=people["Chen"]).json()
    assert mine["state"] == "mine" and mine["notice"]["kind"] == "granted"


def test_a_holder_who_says_continue_keeps_it_and_the_asker_must_wait(world):
    phone, _, people, _ = world
    send(phone, people["Ben"])
    phone.post("/api/session/takeover", json={}, headers=people["Chen"])
    assert phone.post("/api/session/answer", json={"decision": "continue"}, headers=people["Ben"]).json()["result"] == "kept"
    again = phone.post("/api/session/takeover", json={}, headers=people["Chen"])
    assert again.status_code == 429 and again.json()["detail"]["code"] == "wait"
    assert phone.get("/api/session", headers=people["Chen"]).json()["notice"]["kind"] == "kept"


def test_only_the_admin_phone_can_end_someone_elses_turn_or_set_levels(world):
    phone, admin, people, _ = world
    send(phone, people["Ben"])
    assert phone.post("/api/session/release", headers=people["Chen"]).status_code == 403
    assert phone.post("/api/session/take", headers=people["Chen"]).status_code == 403
    assert phone.get("/api/access/people", headers=people["Chen"]).status_code == 403
    chen = device_id(admin, "Chen's phone")
    assert phone.post("/api/access/level", json={"device_id": chen, "level": "operator"}, headers=people["Ben"]).status_code == 403
    took = phone.post("/api/session/take", headers=people["Asha"]).json()
    assert took["state"] == "mine"
    assert phone.get("/api/session", headers=people["Ben"]).json()["notice"] == {"kind": "taken", "by": "Asha's phone"}


def test_the_admin_phone_can_set_levels_up_to_operator_and_never_its_own(world):
    phone, admin, people, _ = world
    listing = phone.get("/api/access/people", headers=people["Asha"]).json()
    assert [lv["id"] for lv in listing["levels"]] == ["viewer", "editor", "operator"]  # "admin" is never on offer
    ben, asha = device_id(admin, "Ben's phone"), device_id(admin, "Asha's phone")
    assert phone.post("/api/access/level", json={"device_id": ben, "level": "editor"}, headers=people["Asha"]).status_code == 200
    assert phone.get("/api/session", headers=people["Ben"]).json()["me"]["level"] == "editor"
    assert phone.post("/api/access/level", json={"device_id": ben, "level": "admin"}, headers=people["Asha"]).status_code == 422
    assert phone.post("/api/access/level", json={"device_id": asha, "level": "viewer"}, headers=people["Asha"]).status_code == 400


def test_the_owner_on_the_pc_picks_the_admin_and_a_phone_cannot(world):
    phone, admin, people, _ = world
    ben = device_id(admin, "Ben's phone")
    assert phone.post("/admin/access/admin", json={"device_id": ben}).status_code in (403, 404)  # not reachable from a phone at all
    state = admin.post("/admin/access/admin", json={"device_id": ben}, headers=H).json()
    assert {p["name"]: p["role"] for p in state["people"]}["Ben's phone"] == "admin"
    assert phone.get("/api/session", headers=people["Ben"]).json()["me"]["role"] == "admin"
    assert phone.get("/api/session", headers=people["Asha"]).json()["me"]["role"] == "user"
    assert admin.post("/admin/access/admin", json={"device_id": "nope"}, headers=H).status_code == 404


def test_the_owner_can_end_a_turn_from_the_pc_and_pause_ends_every_turn(world):
    phone, admin, people, app = world
    send(phone, people["Ben"])
    assert admin.get("/admin/access", headers=H).json()["session"]["holder"]["name"] == "Ben's phone"
    admin.post("/admin/session/end", headers=H)
    assert admin.get("/admin/access", headers=H).json()["session"]["state"] == "free"
    send(phone, people["Chen"])
    phone.post("/api/pause", headers=people["Asha"])
    assert app.state.access.sessions.holder_id() is None
    assert phone.get("/api/ping", headers=people["Chen"]).status_code == 423


def test_a_removed_phone_lets_go_of_its_turn(world):
    phone, admin, people, app = world
    send(phone, people["Ben"])
    ben = device_id(admin, "Ben's phone")
    assert admin.delete(f"/admin/devices/{ben}", headers=H).status_code == 200
    assert app.state.access.sessions.holder_id() is None


def test_a_plan_waiting_is_cancelled_when_the_next_person_starts(world):
    phone, _, people, _ = world
    plan = send(phone, people["Ben"], "create notes.txt with buy milk").json()
    assert plan["status"] == "awaiting_approval"
    phone.post("/api/session/end", headers=people["Ben"])  # Ben leaves with the plan unanswered
    assert phone.get("/api/jobs", headers=people["Chen"]).json() == []  # Chen never sees Ben's request at all
    mine = phone.get("/api/jobs", headers=people["Ben"]).json()
    assert next(j for j in mine if j["id"] == plan["id"])["status"] in ("rejected", "expired")  # and nobody can answer Ben's question for him


def test_the_whole_thing_can_be_switched_off(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_ACCESS__ENABLED", "false")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    phone, tokens = TestClient(app), []
    for name in ("A", "B"):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        tokens.append({"x-token": phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]})
    assert all(send(phone, t).status_code == 200 for t in tokens)  # as before: nobody is turned away
    assert phone.get("/api/session", headers=tokens[0]).status_code == 404
