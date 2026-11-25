"""Controlling the PC from the phone: what an event may look like, how it becomes mouse and keyboard actions, and every rule about who may do it."""
import sys

import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.planner import DemoPlanner
from handoff.screen import InputError, Injector
from handoff.screen.inject import clean_event, clean_events
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


class Recorder:
    """Stands in for Windows: remembers what it was asked to do."""

    def __init__(self):
        self.log = []

    def move(self, x, y): self.log.append(("move", round(x, 3), round(y, 3)))
    def button(self, which, down): self.log.append(("button", which, down))
    def wheel(self, notches): self.log.append(("wheel", notches))
    def key(self, vk, down, extended=False): self.log.append(("key", vk, down, extended))
    def char(self, ch): self.log.append(("char", ch))


# ---------------------------------------------------------------- what an event may look like

def test_good_events_are_put_in_a_fixed_shape():
    assert clean_event({"t": "click", "x": 0.5, "y": "0.25"}) == {"t": "click", "x": 0.5, "y": 0.25, "button": "left"}
    assert clean_event({"t": "scroll", "x": 0.1, "y": 0.9, "dy": 3.4}) == {"t": "scroll", "x": 0.1, "y": 0.9, "dy": 3}
    assert clean_event({"t": "key", "key": "C", "mods": ["ctrl", "ctrl"]}) == {"t": "key", "key": "c", "mods": ["ctrl"]}
    assert clean_event({"t": "text", "text": "hello\x00 world\x1b"})["text"] == "hello world"  # control characters are dropped


def test_positions_are_clamped_and_nonsense_is_refused():
    assert clean_event({"t": "move", "x": 1.005, "y": -0.005}) == {"t": "move", "x": 1.0, "y": 0.0}
    for bad in ({"t": "move", "x": 2, "y": 0.5}, {"t": "move", "x": "a", "y": 0.5}, {"t": "move", "x": float("nan"), "y": 0.5}, {"t": "move", "y": 0.5},
                {"t": "explode"}, {"t": "click", "x": 0.5, "y": 0.5, "button": "back"}, {"t": "key", "key": "notakey"}, {"t": "key", "key": "a", "mods": ["super"]},
                {"t": "text", "text": ""}, {"t": "text", "text": "x" * 201}, {"t": "text", "text": "\x00\x01"}, "click", None, 5):
        with pytest.raises(InputError):
            clean_event(bad)


def test_a_batch_is_one_to_forty_events():
    for bad in ([], None, "x", [{"t": "move", "x": 0, "y": 0}] * 41):
        with pytest.raises(InputError):
            clean_events(bad)
    assert len(clean_events([{"t": "move", "x": 0, "y": 0}] * 40)) == 40


def test_scroll_is_limited_to_a_sane_amount():
    assert clean_event({"t": "scroll", "dy": 9999})["dy"] == 20 and clean_event({"t": "scroll", "dy": -9999})["dy"] == -20


# ---------------------------------------------------------------- what they do

def run(*events):
    rec = Recorder()
    Injector(rec).apply(clean_events(list(events)))
    return rec.log


def test_a_click_is_move_then_press_then_release():
    assert run({"t": "click", "x": 0.5, "y": 0.5}) == [("move", 0.5, 0.5), ("button", "left", True), ("button", "left", False)]
    assert run({"t": "click", "x": 0.1, "y": 0.2, "button": "right"})[1:] == [("button", "right", True), ("button", "right", False)]


def test_a_drag_is_press_move_release():
    assert run({"t": "down", "x": 0.1, "y": 0.1}, {"t": "move", "x": 0.6, "y": 0.6}, {"t": "up", "x": 0.6, "y": 0.6}) == [
        ("move", 0.1, 0.1), ("button", "left", True), ("move", 0.6, 0.6), ("move", 0.6, 0.6), ("button", "left", False)]


def test_scrolling_moves_the_wheel_where_the_fingers_are():
    assert run({"t": "scroll", "x": 0.3, "y": 0.4, "dy": 2}) == [("move", 0.3, 0.4), ("wheel", 2)]
    assert run({"t": "scroll", "dy": 0}) == []  # no movement, nothing to do


def test_a_shortcut_holds_the_modifiers_around_the_key():
    assert run({"t": "key", "key": "c", "mods": ["ctrl"]}) == [("key", 0x11, True, False), ("key", 0x43, True, False), ("key", 0x43, False, False), ("key", 0x11, False, False)]
    assert run({"t": "key", "key": "left"}) == [("key", 0x25, True, True), ("key", 0x25, False, True)]  # arrows are "extended" keys
    assert run({"t": "key", "key": "enter"})[0] == ("key", 0x0D, True, False)


def test_text_is_typed_as_characters_and_newline_is_enter():
    assert run({"t": "text", "text": "hé\n"}) == [("char", "h"), ("char", "é"), ("key", 0x0D, True, False), ("key", 0x0D, False, False)]
    assert run({"t": "key", "key": "@"}) == [("char", "@")]  # a symbol has no key code of its own


def test_without_a_backend_it_says_so_clearly():
    injector = Injector(Recorder())
    injector.backend = None  # a PC that cannot be controlled (not Windows)
    assert injector.available is False
    with pytest.raises(InputError):
        injector.apply([{"t": "move", "x": 0.1, "y": 0.1}])


# ---------------------------------------------------------------- who may do it, over HTTP

@pytest.fixture
def world(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    rec = Recorder()
    app.state.screen.injector = Injector(rec)
    app.state.screen._grab = lambda w, q: b"\xff\xd8\xff\xe0pic"
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    assert admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H).status_code == 200
    phone = TestClient(app)

    def pair(name):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        return {"x-token": phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]}

    people = {"Asha": pair("Asha's phone"), "Ben": pair("Ben's phone"), "Chen": pair("Chen's phone")}
    return phone, admin, people, app, rec


def device_id(admin, name):
    return next(d["id"] for d in admin.get("/admin/devices", headers=H).json() if d["name"] == name)


CLICK = {"events": [{"t": "click", "x": 0.5, "y": 0.5}]}


def test_the_admin_phone_can_look_then_take_control_and_every_event_is_applied(world):
    phone, _, people, app, rec = world
    asha = people["Asha"]
    assert phone.post("/api/screen/start", headers=asha).json()["control"]["allowed"] is True
    assert phone.post("/api/screen/input", json=CLICK, headers=asha).status_code == 403  # looking is the default: control is off until asked for
    assert rec.log == []
    on = phone.post("/api/screen/control", json={"on": True}, headers=asha).json()
    assert on["control"]["on"] is True
    done = phone.post("/api/screen/input", json=CLICK, headers=asha)
    assert done.status_code == 200 and done.json() == {"ok": True, "done": 1}
    assert rec.log == [("move", 0.5, 0.5), ("button", "left", True), ("button", "left", False)]
    assert app.state.access.sessions.holder_id() is not None  # taking control started the turn
    off = phone.post("/api/screen/control", json={"on": False}, headers=asha).json()
    assert off["control"]["on"] is False and phone.post("/api/screen/input", json=CLICK, headers=asha).status_code == 403


def test_looking_is_not_control_another_phone_needs_the_owners_separate_yes(world):
    phone, admin, people, _, rec = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)  # may look
    phone.post("/api/screen/start", headers=people["Ben"])
    assert phone.get("/api/screen", headers=people["Ben"]).json()["control"]["allowed"] is False
    refused = phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"])
    assert refused.status_code == 403 and "may look" in refused.json()["detail"]["message"]
    assert phone.post("/api/screen/input", json=CLICK, headers=people["Ben"]).status_code == 403 and rec.log == []
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60, "control": True}, headers=H)  # now the Owner allows control, on the PC page
    assert phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"]).status_code == 200
    assert phone.post("/api/screen/input", json=CLICK, headers=people["Ben"]).status_code == 200 and rec.log


def test_control_needs_operate_level(world):
    phone, admin, people, _, rec = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60, "control": True}, headers=H)
    admin.post("/admin/access/level", json={"device_id": ben, "level": "editor"}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    denied = phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"])
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "level" and denied.json()["detail"]["need"] == "operator"
    assert rec.log == []


def test_control_needs_your_turn_and_the_phone_that_loses_it_loses_control(world):
    phone, admin, people, app, rec = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60, "control": True}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"])
    assert phone.post("/api/screen/input", json=CLICK, headers=people["Ben"]).status_code == 200
    app.state.access.sessions.take_over(device_id(admin, "Asha's phone"), "Asha's phone", "admin", "Asha's phone")  # the Admin takes the turn
    gone = phone.post("/api/screen/input", json=CLICK, headers=people["Ben"])
    assert gone.status_code == 409 and gone.json()["detail"]["code"] == "session_held"
    assert phone.get("/api/screen", headers=people["Ben"]).json()["control"]["on"] is False  # control was handed back at once
    count = len(rec.log)
    assert phone.post("/api/screen/input", json=CLICK, headers=people["Ben"]).status_code in (403, 409) and len(rec.log) == count


def test_someone_elses_turn_stops_you_taking_control(world):
    phone, admin, people, _, _ = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60, "control": True}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/session/start", headers=people["Chen"])  # Chen has the turn
    blocked = phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"])
    assert blocked.status_code == 409 and blocked.json()["detail"]["holder"] == "Chen's phone"


def test_a_phone_that_is_not_looking_cannot_control(world):
    phone, _, people, _, rec = world
    assert phone.post("/api/screen/control", json={"on": True}, headers=people["Asha"]).status_code == 403  # not viewing yet
    assert phone.post("/api/screen/input", json=CLICK, headers=people["Asha"]).status_code == 403 and rec.log == []


def test_bad_events_are_refused_and_nothing_happens(world):
    phone, _, people, _, rec = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha); phone.post("/api/screen/control", json={"on": True}, headers=asha)
    for events in ([{"t": "move", "x": 9, "y": 9}], [{"t": "run", "cmd": "calc"}], [{"t": "text", "text": "x" * 500}]):
        res = phone.post("/api/screen/input", json={"events": events}, headers=asha)
        assert res.status_code == 422 and res.json()["detail"]["code"] == "bad_input"
    assert phone.post("/api/screen/input", json={"events": []}, headers=asha).status_code == 422
    assert rec.log == []


def test_too_many_events_too_fast_is_refused(world):
    phone, _, people, _, _ = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha); phone.post("/api/screen/control", json={"on": True}, headers=asha)
    batch = {"events": [{"t": "move", "x": 0.1, "y": 0.1}] * 40}
    codes = [phone.post("/api/screen/input", json=batch, headers=asha).status_code for _ in range(16)]
    assert 200 in codes and 422 in codes  # a hand cannot do 600 events in two seconds


def test_stopping_or_pausing_or_the_owners_stop_ends_control(world):
    phone, admin, people, app, rec = world
    asha = people["Asha"]
    for ender in (lambda: phone.post("/api/screen/stop", headers=asha), lambda: admin.post("/admin/screen/stop", headers=H), lambda: phone.post("/api/pause", headers=asha)):
        phone.post("/api/screen/start", headers=asha); phone.post("/api/screen/control", json={"on": True}, headers=asha)
        ender()
        assert phone.post("/api/screen/input", json=CLICK, headers=asha).status_code in (403, 423)
        admin.post("/admin/pause/resume", headers=H)
    assert rec.log == []


def test_the_owner_can_switch_control_off_for_the_whole_pc(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_SCREEN__CONTROL", "false")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    phone = TestClient(app)
    code = admin.post("/admin/pairing", headers=H).json()["code"]
    token = {"x-token": phone.post("/pair/claim", json={"code": code, "name": "A"}).json()["token"]}
    phone.post("/api/screen/start", headers=token)
    assert phone.get("/api/screen", headers=token).json()["control"]["allowed"] is False
    assert phone.post("/api/screen/control", json={"on": True}, headers=token).status_code == 403


def test_control_events_reach_the_activity_log(world):
    phone, admin, people, _, _ = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha)
    phone.post("/api/screen/control", json={"on": True}, headers=asha)
    phone.post("/api/screen/control", json={"on": False}, headers=asha)
    events = [e["event"] for e in phone.get("/api/audit", headers=asha).json()]
    assert "screen_control_started" in events and "screen_control_stopped" in events


def test_an_older_database_gets_the_new_column(tmp_path):
    import sqlite3

    from handoff.db import Database
    from handoff.screen import ScreenManager

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE screen_grants (device_id TEXT PRIMARY KEY, until REAL NOT NULL)")
    old.execute("INSERT INTO screen_grants VALUES ('a', 0)")
    old.commit(); old.close()
    manager = ScreenManager(Database(path))
    assert manager.allowed("a") and manager.control_allowed("a") is False  # a permission from before control existed is a looking-only permission
    manager.grant("b", 0, control=True)
    assert manager.control_allowed("b")


@pytest.mark.skipif(sys.platform != "win32" or __import__("os").environ.get("AFP_DESKTOP_TESTS") != "1", reason="moves the real mouse pointer for a moment: set AFP_DESKTOP_TESTS=1 to run")
def test_the_real_windows_pointer_really_moves_to_where_the_phone_says_and_is_put_back():
    import ctypes
    from ctypes import wintypes

    from handoff.screen import capture  # makes the process DPI-aware, so the pointer's position and the screen's size are in the same units

    def pointer():
        point = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    width, height = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    original = pointer()
    try:
        injector = Injector()
        assert injector.available
        for fx, fy in ((0.5, 0.5), (0.1, 0.9)):
            injector.apply(clean_events([{"t": "move", "x": fx, "y": fy}]))
            x, y = pointer()
            assert abs(x - fx * width) <= 3 and abs(y - fy * height) <= 3, ((x, y), (fx * width, fy * height))
        assert capture.pointer_position() is not None
    finally:
        ctypes.windll.user32.SetCursorPos(*original)


# ---------------------------------------------------------------- asking the Owner for control, and the pointer

def test_a_phone_that_looks_can_ask_the_owner_for_control_and_gets_it_when_answered(world):
    phone, admin, people, app, rec = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)  # may look
    phone.post("/api/screen/start", headers=people["Ben"])
    assert phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"]).status_code == 403
    asked = phone.post("/api/screen/control/request", headers=people["Ben"]).json()
    assert asked["control"]["pending"] is True and asked["control"]["allowed"] is False
    waiting = admin.get("/admin/screen", headers=H).json()["pending"]
    assert [(w["name"], w["kind"]) for w in waiting] == [("Ben's phone", "control")]  # the PC page shows it, marked as control
    assert admin.post("/admin/screen/decide", json={"device_id": ben, "allow": True, "minutes": 15, "kind": "control"}, headers=H).status_code == 200
    view = phone.get("/api/screen", headers=people["Ben"]).json()
    assert (view["control"]["allowed"], view["control"]["on"], view["control"]["pending"]) == (True, False, False)
    assert phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"]).status_code == 200
    assert phone.post("/api/screen/input", json=CLICK, headers=people["Ben"]).status_code == 200 and rec.log


def test_a_denied_control_request_changes_nothing_and_looking_carries_on(world):
    phone, admin, people, app, _ = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/screen/control/request", headers=people["Ben"])
    admin.post("/admin/screen/decide", json={"device_id": ben, "allow": False, "kind": "control"}, headers=H)
    view = phone.get("/api/screen", headers=people["Ben"]).json()
    assert view["state"] == "viewing" and view["control"]["allowed"] is False and view["control"]["pending"] is False


def test_asking_for_control_needs_operate_level_and_a_viewing_and_is_not_repeated(world):
    phone, admin, people, app, _ = world
    ben = device_id(admin, "Ben's phone")
    assert phone.post("/api/screen/control/request", headers=people["Ben"]).status_code == 403  # not looking yet
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/screen/control/request", headers=people["Ben"]); phone.post("/api/screen/control/request", headers=people["Ben"])
    assert len([w for w in app.state.screen.pending() if w["kind"] == "control"]) == 1  # asking twice is one request
    admin.post("/admin/access/level", json={"device_id": ben, "level": "editor"}, headers=H)
    refused = phone.post("/api/screen/control/request", headers=people["Ben"])
    assert refused.status_code == 403 and refused.json()["detail"]["need"] == "operator"


def test_the_admin_phone_asking_for_control_is_granted_at_once(world):
    phone, _, people, _, _ = world
    phone.post("/api/screen/start", headers=people["Asha"])
    view = phone.post("/api/screen/control/request", headers=people["Asha"]).json()
    assert view["control"]["allowed"] is True and view["control"]["pending"] is False


def test_a_control_request_dies_with_the_viewing(world):
    phone, admin, people, app, _ = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    phone.post("/api/screen/control/request", headers=people["Ben"])
    phone.post("/api/screen/stop", headers=people["Ben"])
    assert app.state.screen.pending() == []


def test_a_control_grant_never_shortens_what_the_phone_already_had(tmp_path):
    from handoff.db import Database
    from handoff.screen import ScreenManager

    manager = ScreenManager(Database(tmp_path / "app.db"), clock=lambda: 1000.0)
    manager.grant("a", 0)           # looking, until taken back
    manager.grant_control("a", 15)  # control for 15 minutes must not turn that into 15 minutes of looking
    assert manager.view("a")["forever"] is True and manager.control_allowed("a")
    manager.grant("b", 120)
    manager.grant_control("b", 15)
    assert manager.view("b")["left"] == 120 * 60


def test_the_frame_says_where_the_pc_pointer_is_and_can_leave_it_out_of_the_picture(world):
    phone, admin, people, app, _ = world
    seen = []
    app.state.screen._grab = lambda w, q, pointer=True: seen.append(pointer) or b"JPEG-for-test"
    phone.post("/api/screen/start", headers=people["Asha"])
    one = phone.get("/api/screen/frame", headers=people["Asha"])
    two = phone.get("/api/screen/frame", params={"pointer": 0}, headers=people["Asha"])
    assert seen == [True, False]  # the phone that draws its own pointer asks for a picture without one
    if "x-pointer" in one.headers:
        x, y = (float(v) for v in one.headers["x-pointer"].split(","))
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def test_a_two_argument_grab_still_works(world):
    phone, _, people, app, _ = world
    app.state.screen._grab = lambda w, q: b"JPEG-for-test"  # what the older tests use
    phone.post("/api/screen/start", headers=people["Asha"])
    assert phone.get("/api/screen/frame", params={"pointer": 0}, headers=people["Asha"]).status_code == 200


def test_the_pointer_size_setting_reaches_the_picture_and_is_kept_in_range(world):
    from handoff.screen import capture

    phone, _, people, app, _ = world
    seen = []
    app.state.screen._grab = lambda w, q, pointer=True, pointer_size=1.0: seen.append((pointer, pointer_size)) or b"JPEG-for-test"
    phone.post("/api/screen/start", headers=people["Asha"])
    phone.get("/api/screen/frame", params={"ps": 1.8}, headers=people["Asha"])
    phone.get("/api/screen/frame", headers=people["Asha"])
    assert seen == [(True, 1.8), (True, 1.0)]  # asked for when given, normal when not
    assert capture.pointer_pixels(1.0) == 22 and capture.pointer_pixels(2.0) == 44
    assert capture.pointer_pixels(99) == 66 and capture.pointer_pixels(0.01) == 11 and capture.pointer_pixels("x") == 22  # never huge, never invisible, never an error


# ---------------------------------------------------------------- the fast lane (a WebSocket) obeys the same rules as the request

def socket_say(ws, message):
    ws.send_text(message if isinstance(message, str) else __import__("json").dumps(message))
    return __import__("json").loads(ws.receive_text())


def test_the_fast_lane_applies_events_exactly_like_the_request(world):
    phone, _, people, _, rec = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha)
    phone.post("/api/screen/control", json={"on": True}, headers=asha)
    with phone.websocket_connect("/api/screen/ws") as ws:
        assert socket_say(ws, {"token": asha["x-token"]}) == {"ready": True}
        answer = socket_say(ws, {"events": CLICK["events"], "ts": 12345})
        assert answer == {"ok": True, "done": 1, "ts": 12345}
        assert rec.log == [("move", 0.5, 0.5), ("button", "left", True), ("button", "left", False)]
        assert socket_say(ws, {"events": [{"t": "move", "x": 0.25, "y": 0.75}]})["ok"] is True   # and the next, on the same connection


def test_the_fast_lane_refuses_a_wrong_token_and_does_nothing(world):
    phone, _, people, _, rec = world
    with phone.websocket_connect("/api/screen/ws") as ws:
        ws.send_text('{"token": "not-a-real-token"}')
        with pytest.raises(Exception):
            ws.receive_text()   # closed without a word
    assert rec.log == []


def test_the_fast_lane_needs_control_to_be_on_like_the_request(world):
    phone, _, people, _, rec = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha)           # looking only: control is off
    with phone.websocket_connect("/api/screen/ws") as ws:
        socket_say(ws, {"token": asha["x-token"]})
        answer = socket_say(ws, {"events": CLICK["events"]})
        assert answer["status"] == 403 and answer["error"]["code"] == "screen_not_allowed"
    assert rec.log == []


def test_the_fast_lane_holds_a_phone_to_operate_level(world):
    phone, admin, people, _, rec = world
    ben = device_id(admin, "Ben's phone")
    admin.post("/admin/screen/grant", json={"device_id": ben, "minutes": 60, "control": True}, headers=H)
    phone.post("/api/screen/start", headers=people["Ben"])
    assert phone.post("/api/screen/control", json={"on": True}, headers=people["Ben"]).status_code == 200      # allowed to control...
    admin.post("/admin/access/level", json={"device_id": ben, "level": "editor"}, headers=H)                   # ...until the level is lowered meanwhile
    with phone.websocket_connect("/api/screen/ws") as ws:
        socket_say(ws, {"token": people["Ben"]["x-token"]})
        answer = socket_say(ws, {"events": CLICK["events"]})
        assert answer["status"] == 403 and answer["error"]["code"] == "level" and answer["error"]["need"] == "operator"
    assert rec.log == []


def test_the_fast_lane_answers_nonsense_with_an_error_and_stays_open(world):
    phone, _, people, _, rec = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha)
    phone.post("/api/screen/control", json={"on": True}, headers=asha)
    with phone.websocket_connect("/api/screen/ws") as ws:
        socket_say(ws, {"token": asha["x-token"]})
        assert socket_say(ws, "not json")["status"] == 422
        assert socket_say(ws, {"events": []})["status"] == 422
        assert socket_say(ws, {"events": [{"t": "explode"}]})["status"] == 422
        assert socket_say(ws, {"events": CLICK["events"]})["ok"] is True        # still working afterwards
