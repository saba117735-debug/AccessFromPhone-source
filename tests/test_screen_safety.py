"""Safety and comfort of controlling the PC: nothing stays held down when a phone goes away, the panic key, the clipboard, the sound, and the extra keys."""
import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.db import Database
from handoff.planner import DemoPlanner
from handoff.screen import InputError, Injector, ScreenManager, ScreenNotAllowed, Settings
from handoff.screen.clipboard import ClipboardError
from handoff.screen.hotkey import describe, parse_combo
from handoff.screen.inject import clean_event
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


class Recorder:
    def __init__(self):
        self.log = []

    def move(self, x, y): self.log.append(("move", round(x, 3), round(y, 3)))
    def button(self, which, down): self.log.append(("button", which, down))
    def wheel(self, notches): self.log.append(("wheel", notches))
    def key(self, vk, down, extended=False): self.log.append(("key", vk, down))
    def char(self, ch): self.log.append(("char", ch))


class FakeClipboard:
    def __init__(self, text=""):
        self.text, self.fail = text, False

    def get_text(self):
        if self.fail:
            raise ClipboardError("busy")
        return self.text

    def set_text(self, text):
        if self.fail:
            raise ClipboardError("busy")
        self.text = text


class FakeAudio:
    def __init__(self, ok=True):
        self.ok = ok

    def available(self):
        return self.ok

    def chunks(self, keep_going):
        n = 0
        while keep_going() and n < 1000:
            n += 1
            yield b"\x01\x00" * 1600


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def mgr(tmp_path):
    clock, rec = Clock(), Recorder()
    events = []
    m = ScreenManager(Database(tmp_path / "a.db"), Settings(), clock=clock, grab=lambda w, q: b"jpg", on_event=lambda kind, **d: events.append((kind, d)))
    m.injector, m.clipboard, m.audio = Injector(rec), FakeClipboard("on the PC"), FakeAudio()
    m.grant("ben", 60, control=True)
    m.start("ben", "Ben's phone")
    m.set_control("ben", True)
    return m, clock, rec, events


DRAG = [{"t": "down", "x": 0.2, "y": 0.2}, {"t": "move", "x": 0.4, "y": 0.4}]


# ---------------------------------------------------------------- nothing stays held down

def test_a_drag_that_never_ends_is_let_go_after_a_few_quiet_seconds(mgr):
    m, clock, rec, events = mgr
    m.send_input("ben", DRAG)
    assert m.injector.holding()
    clock.t += 3
    m.view("ben")
    assert m.injector.holding()          # a short pause is just a pause
    clock.t += 3
    m.view("ben")
    assert not m.injector.holding() and ("button", "left", False) in rec.log
    assert any(kind == "screen_released" for kind, _ in events)


def test_a_phone_that_keeps_sending_keeps_its_drag(mgr):
    m, clock, rec, _ = mgr
    m.send_input("ben", DRAG)
    for _ in range(4):
        clock.t += 3
        m.send_input("ben", [{"t": "move", "x": 0.5, "y": 0.5}])   # the phone's heartbeat while a finger is held still
        m.view("ben")
    assert m.injector.holding() and ("button", "left", False) not in rec.log


def test_stopping_or_handing_control_back_lets_go_of_everything(mgr):
    m, _, rec, _ = mgr
    m.send_input("ben", DRAG)
    m.set_control("ben", False)
    assert not m.injector.holding() and ("button", "left", False) in rec.log
    m.set_control("ben", True)
    m.send_input("ben", DRAG)
    m.stop_all("stopped on this PC")
    assert not m.injector.holding()


def test_a_shortcut_never_leaves_ctrl_held_even_when_windows_refuses_the_key():
    class Refuses(Recorder):
        def key(self, vk, down, extended=False):
            if vk == 0x43 and down:
                raise InputError("refused")
            super().key(vk, down, extended)

    rec = Refuses()
    injector = Injector(rec)
    with pytest.raises(InputError):
        injector.apply([clean_event({"t": "key", "key": "c", "mods": ["ctrl"]})])
    assert ("key", 0x11, False) in rec.log and not injector.holding()


# ---------------------------------------------------------------- the panic key

def test_the_panic_key_cuts_everything_and_control_must_be_asked_for_again(mgr):
    m, clock, rec, events = mgr
    m.send_input("ben", DRAG)
    assert m.panic() is True
    assert m.viewing() is None and not m.injector.holding()
    assert ("button", "left", False) in rec.log and any(kind == "screen_panic" for kind, _ in events)
    assert m.allowed("ben") and not m.control_allowed("ben")          # looking is still allowed; control is not
    m.start("ben", "Ben's phone")
    with pytest.raises(ScreenNotAllowed):
        m.set_control("ben", True)
    assert m.view("ben")["control"]["blocked"] > 0
    clock.t += 121
    m.grant("ben", 60, control=True)
    m.start("ben", "Ben's phone")   # (the long wait ended the looking too)
    m.set_control("ben", True)


def test_the_panic_key_stops_even_the_trusted_admin_phone(mgr):
    m, clock, _, _ = mgr
    m.trusted = lambda device_id: device_id == "ben"
    m.panic()
    assert not m.control_allowed("ben")
    clock.t += 121
    assert m.control_allowed("ben")


def test_the_panic_key_with_nobody_looking_is_harmless(tmp_path):
    m = ScreenManager(Database(tmp_path / "b.db"), Settings(), grab=lambda w, q: b"x")
    m.injector = Injector(Recorder())
    assert m.panic() is False


def test_panic_combinations_are_read_carefully():
    assert parse_combo("ctrl+alt+shift+x") == (0x0002 | 0x0001 | 0x0004, ord("X"))
    assert parse_combo("Ctrl+F12") == (0x0002, 0x6F + 12)
    for bad in ("", "x", "ctrl", "banana+x", "ctrl+alt+", "ctrl+??"):
        assert parse_combo(bad) is None
    assert describe("ctrl+alt+shift+x") == "Ctrl+Alt+Shift+X"


# ---------------------------------------------------------------- the clipboard

def test_the_clipboard_works_only_for_the_phone_that_controls_it(mgr):
    m, _, _, events = mgr
    assert m.clipboard_get("ben") == "on the PC"
    assert m.clipboard_set("ben", "from the phone ✓") == 16 and m.clipboard.text == "from the phone ✓"
    kinds = [k for k, _ in events]
    assert "screen_clipboard_read" in kinds and "screen_clipboard_written" in kinds
    assert "from the phone" not in str(events) and "on the PC" not in str(events)   # the log says that it happened, never what was in it
    m.set_control("ben", False)
    for call in (lambda: m.clipboard_get("ben"), lambda: m.clipboard_set("ben", "x")):
        with pytest.raises(ScreenNotAllowed):
            call()


def test_clipboard_limits_and_trouble_are_explained(mgr):
    m, _, _, _ = mgr
    with pytest.raises(InputError):
        m.clipboard_set("ben", "x" * 100_001)
    m.clipboard.fail = True
    with pytest.raises(InputError):
        m.clipboard_get("ben")


def test_the_clipboard_can_be_switched_off(tmp_path):
    m = ScreenManager(Database(tmp_path / "c.db"), Settings(clipboard=False), grab=lambda w, q: b"x")
    m.injector, m.clipboard = Injector(Recorder()), FakeClipboard()
    m.grant("ben", 60, control=True)
    m.start("ben", "Ben")
    m.set_control("ben", True)
    with pytest.raises(ScreenNotAllowed):
        m.clipboard_get("ben")


# ---------------------------------------------------------------- the sound

def test_sound_flows_while_looking_and_stops_when_the_looking_stops(mgr):
    m, _, _, events = mgr
    stream = m.audio_stream("ben")
    assert next(stream) == b"\x01\x00" * 1600 and m.viewing()["listening"] is True
    m.stop("ben")
    assert list(stream) == []                      # the looking ended: the stream ends with it
    assert any(kind == "screen_audio_started" for kind, _ in events)


def test_switching_the_sound_off_ends_the_stream_at_once(mgr):
    m, _, _, _ = mgr
    stream = m.audio_stream("ben")
    next(stream)
    m.audio_stop("ben")
    assert list(stream) == [] and m.viewing()["listening"] is False


def test_a_newer_sound_stream_replaces_the_older_one(mgr):
    m, _, _, _ = mgr
    first = m.audio_stream("ben")
    next(first)
    second = m.audio_stream("ben")
    assert list(first) == [] and next(second)


def test_sound_needs_looking_and_can_be_switched_off(tmp_path):
    m = ScreenManager(Database(tmp_path / "d.db"), Settings(audio=False), grab=lambda w, q: b"x")
    m.audio = FakeAudio()
    assert m.audio_available() is False
    m.grant("ben", 60)
    with pytest.raises(ScreenNotAllowed):
        m.audio_stream("ben")
    m2 = ScreenManager(Database(tmp_path / "e.db"), Settings(), grab=lambda w, q: b"x")
    m2.audio = FakeAudio()
    with pytest.raises(ScreenNotAllowed):
        m2.audio_stream("nobody")


# ---------------------------------------------------------------- the extra keys, and the new answers over HTTP

def test_the_extra_keys_are_accepted():
    for key in ("f1", "f12", "home", "end", "pageup", "pagedown", "insert", "printscreen", "pause", "delete"):
        assert clean_event({"t": "key", "key": key})["key"] == key
    assert clean_event({"t": "key", "key": "tab", "mods": ["alt"]})["mods"] == ["alt"]            # Alt+Tab
    assert clean_event({"t": "key", "key": "d", "mods": ["win"]})["mods"] == ["win"]              # Win+D
    assert clean_event({"t": "key", "key": "esc", "mods": ["ctrl", "shift"]})["mods"] == ["ctrl", "shift"]


@pytest.fixture
def world(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    rec = Recorder()
    screen = app.state.screen
    screen.injector, screen.clipboard, screen.audio = Injector(rec), FakeClipboard("PC text"), FakeAudio()
    screen._grab = lambda w, q: b"\xff\xd8\xff\xe0pic"
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    phone = TestClient(app)

    def pair(name):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        return {"x-token": phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]}

    return phone, {"Asha": pair("Asha's phone"), "Ben": pair("Ben's phone")}, screen


def test_clipboard_over_http_needs_control_first(world):
    phone, people, screen = world
    asha = people["Asha"]
    phone.post("/api/screen/start", headers=asha)
    assert phone.get("/api/screen/clipboard", headers=asha).status_code in (403, 409)      # looking only: no clipboard
    phone.post("/api/screen/control", json={"on": True}, headers=asha)
    assert phone.get("/api/screen/clipboard", headers=asha).json() == {"text": "PC text"}
    done = phone.post("/api/screen/clipboard", json={"text": "hello"}, headers=asha)
    assert done.status_code == 200 and done.json()["characters"] == 5 and screen.clipboard.text == "hello"
    assert phone.post("/api/screen/clipboard", json={"text": "x" * 100_001}, headers=asha).status_code == 422


def test_sound_over_http_streams_and_says_when_it_cannot(world):
    phone, people, screen = world
    asha = people["Asha"]
    assert phone.get("/api/screen/audio", headers=asha).status_code == 403                   # not looking yet
    phone.post("/api/screen/start", headers=asha)
    with phone.stream("GET", "/api/screen/audio", headers=asha) as r:
        assert r.status_code == 200 and r.headers["x-audio-rate"] == "16000"
        assert next(r.iter_bytes()).startswith(b"\x01\x00")
        phone.post("/api/screen/stop", headers=asha)
    screen.audio = FakeAudio(ok=False)
    phone.post("/api/screen/start", headers=asha)
    assert phone.get("/api/screen/audio", headers=asha).status_code == 503


def test_the_screen_answer_tells_the_phone_about_sound_panic_and_clipboard(world):
    phone, people, screen = world
    screen.panic_hint = "Panic key: Ctrl+Alt+Shift+X stops this at once."
    v = phone.get("/api/screen", headers=people["Asha"]).json()
    assert v["audio"] is True and "Ctrl+Alt+Shift+X" in v["panic"] and v["control"]["clipboard"] is True and v["control"]["blocked"] == 0
