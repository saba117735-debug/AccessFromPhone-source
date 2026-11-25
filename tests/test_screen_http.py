"""Looking at the PC's screen over real HTTP: ask, the Owner says yes on the PC, look, stop, and every way it must refuse."""
import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.planner import DemoPlanner
from handoff.screen import ScreenUnavailable
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}
JPEG = b"\xff\xd8\xff\xe0fake-picture"


@pytest.fixture
def world(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    app.state.screen._grab = lambda w, q: JPEG  # no real screen in a test
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    assert admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H).status_code == 200
    phone = TestClient(app)

    def pair(name):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        return {"x-token": phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]}

    return phone, admin, {"Asha": pair("Asha's phone"), "Ben": pair("Ben's phone")}, app


def device_id(admin, name):
    return next(d["id"] for d in admin.get("/admin/devices", headers=H).json() if d["name"] == name)


def test_without_a_yes_there_is_no_picture_and_no_way_in(world):
    phone, _, people, _ = world
    assert phone.get("/api/screen", headers=people["Ben"]).json()["state"] == "none"
    assert phone.post("/api/screen/start", headers=people["Ben"]).status_code == 403
    refused = phone.get("/api/screen/frame", headers=people["Ben"])
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "screen_not_allowed"


def test_the_whole_flow_ask_the_owner_says_yes_look_stop(world):
    phone, admin, people, _ = world
    asked = phone.post("/api/screen/request", json={"note": "fixing the printer"}, headers=people["Ben"])
    assert asked.json()["state"] == "pending"
    waiting = admin.get("/admin/screen", headers=H).json()["pending"]
    assert [(w["name"], w["note"]) for w in waiting] == [("Ben's phone", "fixing the printer")]  # the PC page shows it
    assert phone.get("/api/screen/frame", headers=people["Ben"]).status_code == 403  # still no picture until the Owner answers
    ben = device_id(admin, "Ben's phone")
    assert admin.post("/admin/screen/decide", json={"device_id": ben, "allow": True, "minutes": 15}, headers=H).status_code == 200
    state = phone.get("/api/screen", headers=people["Ben"]).json()
    assert state["state"] == "granted" and 800 < state["left"] <= 900
    assert phone.post("/api/screen/start", headers=people["Ben"]).json()["state"] == "viewing"
    frame = phone.get("/api/screen/frame", params={"w": 800, "q": 40}, headers=people["Ben"])
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg" and frame.content == JPEG and frame.headers["cache-control"] == "no-store"
    assert admin.get("/admin/screen", headers=H).json()["viewing"]["name"] == "Ben's phone"  # and the PC page shows who is looking
    assert phone.post("/api/screen/stop", headers=people["Ben"]).json()["state"] == "granted"
    assert phone.get("/api/screen/frame", headers=people["Ben"]).status_code == 403


def test_a_denied_request_gives_nothing(world):
    phone, admin, people, _ = world
    phone.post("/api/screen/request", json={}, headers=people["Ben"])
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/decide", json={"device_id": ben, "allow": False}, headers=H)
    assert phone.get("/api/screen", headers=people["Ben"]).json()["state"] == "none"
    assert phone.post("/api/screen/start", headers=people["Ben"]).status_code == 403
    assert admin.post("/admin/screen/decide", json={"device_id": ben, "allow": True}, headers=H).status_code == 409  # nothing was waiting any more


def test_a_phone_cannot_approve_itself_or_anyone(world):
    phone, admin, people, _ = world
    ben = device_id(admin, "Ben's phone")
    phone.post("/api/screen/request", json={}, headers=people["Ben"])
    for path in ("/api/screen/decide", "/api/screen/grant", "/admin/screen/decide", "/admin/screen/grant"):
        assert phone.post(path, json={"device_id": ben, "allow": True, "minutes": 60}, headers=people["Asha"]).status_code in (403, 404, 405), path  # not even the Admin phone
    assert phone.get("/api/screen", headers=people["Ben"]).json()["state"] == "pending"


def test_the_owner_can_pre_approve_a_phone_for_when_nobody_is_at_the_pc(world):
    phone, admin, people, _ = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)
    assert phone.post("/api/screen/request", json={}, headers=people["Ben"]).json()["state"] == "granted"  # no waiting
    assert [g["name"] for g in admin.get("/admin/screen", headers=H).json()["grants"]] == ["Ben's phone"]
    admin.post("/admin/screen/revoke", json={"device_id": ben}, headers=H)
    assert phone.get("/api/screen", headers=people["Ben"]).json()["state"] == "none"


def test_the_admin_phone_may_look_without_asking_but_the_pc_still_shows_it(world):
    phone, admin, people, app = world
    view = phone.get("/api/screen", headers=people["Asha"]).json()
    assert view["state"] == "granted" and view["trusted"] is True and view["forever"] is True  # the Owner's own phone: no second yes
    assert phone.post("/api/screen/start", headers=people["Asha"]).json()["state"] == "viewing"
    assert phone.get("/api/screen/frame", headers=people["Asha"]).status_code == 200
    assert admin.get("/admin/screen", headers=H).json()["viewing"]["name"] == "Asha's phone"  # still visible on the PC page (and the desktop window)
    admin.post("/admin/screen/stop", headers=H)  # and still stoppable there
    assert phone.get("/api/screen/frame", headers=people["Asha"]).status_code == 403
    assert phone.get("/api/screen", headers=people["Ben"]).json()["trusted"] is False  # everyone else still needs the Owner's yes
    assert admin.get("/admin/screen", headers=H).json()["admin_id"] == device_id(admin, "Asha's phone")


def test_a_phone_that_stops_being_the_admin_needs_a_yes_again(world):
    phone, admin, people, _ = world
    asha, ben = device_id(admin, "Asha's phone"), device_id(admin, "Ben's phone")
    admin.post("/admin/access/admin", json={"device_id": ben}, headers=H)
    assert phone.get("/api/screen", headers=people["Asha"]).json()["state"] == "none"
    assert phone.get("/api/screen", headers=people["Ben"]).json()["state"] == "granted"
    assert asha != ben


def test_the_owner_can_require_a_yes_even_from_the_admin_phone(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_SCREEN__ADMIN_NEEDS_YES", "true")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    phone = TestClient(app)
    code = admin.post("/admin/pairing", headers=H).json()["code"]
    token = {"x-token": phone.post("/pair/claim", json={"code": code, "name": "A"}).json()["token"]}
    assert phone.get("/api/screen", headers=token).json()["state"] == "none"
    assert phone.post("/api/screen/start", headers=token).status_code == 403


def test_one_viewer_at_a_time(world):
    phone, admin, people, _ = world
    for name in ("Asha's phone", "Ben's phone"):
        admin.post("/admin/screen/grant", json={"device_id": device_id(admin, name), "minutes": 60}, headers=H)
    assert phone.post("/api/screen/start", headers=people["Asha"]).status_code == 200
    busy = phone.post("/api/screen/start", headers=people["Ben"])
    assert busy.status_code == 409 and busy.json()["detail"]["viewer"] == "Asha's phone"
    assert phone.get("/api/screen", headers=people["Ben"]).json() ["state"] == "busy"
    assert phone.get("/api/screen/frame", headers=people["Ben"]).status_code == 403


def test_the_owner_can_stop_it_from_the_pc(world):
    phone, admin, people, _ = world
    admin.post("/admin/screen/grant", json={"device_id": device_id(admin, "Ben's phone"), "minutes": 60}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    admin.post("/admin/screen/stop", headers=H)
    assert phone.get("/api/screen/frame", headers=people["Ben"]).status_code == 403
    assert phone.get("/api/screen", headers=people["Ben"]).json()["state"] == "granted"  # the permission stays, the viewing ended


def test_pause_ends_the_viewing_and_removing_a_phone_takes_its_permission(world):
    phone, admin, people, app = world
    for name in ("Asha's phone", "Ben's phone"):
        admin.post("/admin/screen/grant", json={"device_id": device_id(admin, name), "minutes": 60}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/pause", headers=people["Asha"])
    assert app.state.screen.viewing() is None
    admin.post("/admin/pause/resume", headers=H)
    ben = device_id(admin, "Ben's phone")
    admin.delete(f"/admin/devices/{ben}", headers=H)
    assert not app.state.screen.allowed(ben)


def test_a_screen_that_cannot_be_captured_says_so_clearly(world):
    phone, admin, people, app = world
    admin.post("/admin/screen/grant", json={"device_id": device_id(admin, "Ben's phone"), "minutes": 60}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])

    def locked(w, q):
        raise ScreenUnavailable("This PC's screen can't be captured right now. It may be locked, or nobody is signed in.")

    app.state.screen._grab = locked
    res = phone.get("/api/screen/frame", headers=people["Ben"])
    assert res.status_code == 503 and "locked" in res.json()["detail"]["message"]


def test_a_view_only_phone_may_ask_to_see_the_screen_and_never_takes_a_turn(world):
    phone, admin, people, app = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/access/level", json={"device_id": ben, "level": "viewer"}, headers=H)
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)
    assert phone.post("/api/screen/start", headers=people["Ben"]).status_code == 200
    assert app.state.access.sessions.holder_id() is None  # looking at the screen is not a turn


def test_the_events_reach_the_activity_log(world):
    phone, admin, people, _ = world
    ben = device_id(admin, "Ben's phone")
    phone.post("/api/screen/request", json={}, headers=people["Ben"])
    admin.post("/admin/screen/decide", json={"device_id": ben, "allow": True, "minutes": 15}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/screen/stop", headers=people["Ben"])
    events = [e["event"] for e in phone.get("/api/audit", headers=people["Asha"]).json()]
    for kind in ("screen_requested", "screen_allowed", "screen_started", "screen_stopped"):
        assert kind in events


def test_it_can_be_switched_off(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_SCREEN__ENABLED", "false")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    phone = TestClient(app)
    code = admin.post("/admin/pairing", headers=H).json()["code"]
    token = {"x-token": phone.post("/pair/claim", json={"code": code, "name": "A"}).json()["token"]}
    assert phone.get("/api/screen", headers=token).json()["state"] == "off"
    assert phone.post("/api/screen/request", json={}, headers=token).status_code == 403


def test_the_picture_request_passes_the_zoomed_region_on(world):
    phone, admin, people, app = world
    ben = device_id(admin, "Ben's phone")
    phone.post("/api/screen/request", json={}, headers=people["Ben"])
    admin.post("/admin/screen/decide", json={"device_id": ben, "allow": True, "minutes": 15}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    seen = {}

    def grab(width, quality, pointer=True, pointer_size=1.0, region=None):
        seen.update(width=width, region=region)
        return JPEG

    app.state.screen._grab = grab
    ok = phone.get("/api/screen/frame?w=900&q=60&rx=0.25&ry=0.5&rw=0.4&rh=0.4", headers=people["Ben"])
    assert ok.status_code == 200 and seen["region"] == (0.25, 0.5, 0.4, 0.4)
    phone.get("/api/screen/frame?w=900&q=60", headers=people["Ben"])
    assert seen["region"] is None  # no region asked for: the whole screen, as before
    old = lambda w, q: JPEG  # a grab that knows nothing of regions (and of the pointer) still works
    app.state.screen._grab = old
    assert phone.get("/api/screen/frame?w=900&q=60&rx=0.1&ry=0.1&rw=0.5&rh=0.5", headers=people["Ben"]).status_code == 200


# -- the pointer's shape, for the phone's own pointer -----------------------------------------------------------------------------------------------

def test_the_picture_says_which_pointer_shape_the_pc_shows_and_the_phone_can_fetch_it(world):
    from PIL import Image

    from handoff.screen import capture

    phone, admin, people, app = world
    asha = people["Asha"]                                                # the first phone is the Admin: it may look without asking
    capture._shapes.clear()
    capture._shapes[0x1234] = (Image.new("RGBA", (10, 12), (255, 0, 0, 255)), 3, 4)
    app.state.screen.cursor_id = lambda: "c1234"
    assert phone.post("/api/screen/start", headers=asha).json()["state"] == "viewing"
    frame = phone.get("/api/screen/frame", headers=asha)
    assert frame.headers["x-cursor"] == "c1234"
    shape = phone.get("/api/screen/cursor", params={"id": "c1234"}, headers=asha)
    assert shape.status_code == 200 and shape.headers["content-type"] == "image/png"
    assert shape.headers["x-hotspot"] == "3,4" and shape.headers["x-size"] == "10,12"
    assert shape.content[:4] == b"\x89PNG"
    for unknown in ("c9999", "zzzz", "", "c", "c-1", "../../x"):         # only shapes the PC has shown: nothing else is looked up
        assert phone.get("/api/screen/cursor", params={"id": unknown}, headers=asha).status_code == 404, unknown


def test_a_hidden_pointer_is_said_to_be_hidden_and_an_unreadable_one_says_nothing(world):
    phone, admin, people, app = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha)
    app.state.screen.cursor_id = lambda: "none"
    assert phone.get("/api/screen/frame", headers=asha).headers["x-cursor"] == "none"
    app.state.screen.cursor_id = lambda: ""
    assert "x-cursor" not in phone.get("/api/screen/frame", headers=asha).headers


def test_nobody_who_is_not_looking_can_fetch_a_pointer_shape(world):
    from PIL import Image

    from handoff.screen import capture

    phone, admin, people, app = world
    capture._shapes[0x1234] = (Image.new("RGBA", (10, 12)), 3, 4)
    refused = phone.get("/api/screen/cursor", params={"id": "c1234"}, headers=people["Ben"])
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "screen_not_allowed"


def test_the_picture_says_when_the_screen_is_still(world, monkeypatch):
    import handoff.server as server_module

    phone, admin, people, _ = world
    phone.post("/api/screen/request", json={"note": "look"}, headers=people["Ben"])
    assert admin.post("/admin/screen/decide", json={"device_id": device_id(admin, "Ben's phone"), "allow": True, "minutes": 15}, headers=H).status_code == 200
    assert phone.post("/api/screen/start", headers=people["Ben"]).json()["state"] == "viewing"
    monkeypatch.setattr(server_module, "capture_still", lambda: True)
    still = phone.get("/api/screen/frame", params={"w": 800, "q": 40}, headers=people["Ben"])
    assert still.status_code == 200 and still.headers.get("x-still") == "1"
    monkeypatch.setattr(server_module, "capture_still", lambda: False)
    moving = phone.get("/api/screen/frame", params={"w": 800, "q": 40}, headers=people["Ben"])
    assert moving.status_code == 200 and "x-still" not in moving.headers
