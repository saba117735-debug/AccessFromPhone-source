"""The everyday helpers over real HTTP: who may do what, what is written where, and what is refused."""
import json

import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.daily import controls, printing
from handoff.planner import DemoPlanner
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


@pytest.fixture
def client(settings):
    app = create_app(settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    c.app = app
    c.daily = app.state.daily
    return c


def audit(client):
    return [e["event"] for e in client.app.state.manager.audit_events()]


# ---- switched on and off --------------------------------------------------------------------------------------------------

def test_the_info_says_what_the_helpers_offer(client, settings):
    daily = client.get("/api/info").json()["daily"]
    assert daily["enabled"] and daily["printing"] and not daily["notifications"] and not daily["webcam"]
    assert daily["folders"]["inbox"].startswith(str(settings.daily.folder))
    assert set(daily["power"]) == {"lock", "screen_off", "sleep"}


def test_switching_the_helpers_off_removes_them_all(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_DAILY__ENABLED", "false")
    app = create_app(load_settings(project / "config.yaml"))
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    assert c.get("/api/info").json()["daily"] == {"enabled": False}
    for method, path in (("get", "/api/daily/state"), ("get", "/api/daily/inbox"), ("get", "/api/daily/tasks"), ("get", "/api/daily/briefing")):
        res = getattr(c, method)(path)
        assert res.status_code == 404 and res.json()["detail"]["code"] == "daily_off"
    assert c.post("/api/daily/control", json={"action": "mute"}).status_code == 404


def test_every_route_needs_a_paired_phone(client):
    anon = TestClient(client.app)
    for path in ("/api/daily/state", "/api/daily/inbox", "/api/daily/notes", "/api/daily/tasks", "/api/daily/watch", "/api/daily/briefing",
                 "/api/daily/recent", "/api/daily/favourites", "/api/daily/alerts", "/api/daily/notifications"):
        assert anon.get(path).status_code == 401, path
    assert anon.post("/api/daily/control", json={"action": "mute"}).status_code == 401
    assert anon.put("/api/daily/inbox/file?name=a.txt", content=b"x").status_code == 401


def test_pause_stops_the_helpers_too(client):
    client.app.state.manager.pause_switch.pause("test")
    assert client.get("/api/daily/state").status_code == 423
    assert client.post("/api/daily/control", json={"action": "mute"}).status_code == 423


# ---- quick controls ------------------------------------------------------------------------------------------------------

def test_a_quick_control_presses_the_key_and_is_logged(client, monkeypatch):
    pressed = []
    monkeypatch.setattr(controls, "press_key", lambda vk, n=1: pressed.append((vk, n)))
    monkeypatch.setattr(controls, "supported", lambda: True)
    res = client.post("/api/daily/control", json={"action": "volume_up", "steps": 2})
    assert res.status_code == 200 and res.json()["ok"] and pressed == [(0xAF, 10)]
    assert "quick_control" in audit(client)


def test_a_made_up_control_or_step_is_refused(client):
    assert client.post("/api/daily/control", json={"action": "format_c"}).status_code == 422
    assert client.post("/api/daily/control", json={"action": "mute", "steps": 99}).status_code == 422
    assert client.post("/api/daily/control", json={}).status_code == 422


def test_only_the_usual_folders_can_be_opened_on_the_pc(client, monkeypatch, tmp_path):
    from handoff.daily import places

    opened = []
    monkeypatch.setattr(places, "known_folder", lambda name: tmp_path if name == "downloads" else None)
    monkeypatch.setattr(controls, "supported", lambda: True)
    monkeypatch.setattr(controls.os, "startfile", opened.append, raising=False)
    assert client.post("/api/daily/open-place", json={"key": "downloads"}).status_code == 200 and opened == [str(tmp_path)]
    for bad in ("C:\\Windows", "..", "music", ""):
        assert client.post("/api/daily/open-place", json={"key": bad}).status_code == 422


# ---- the inbox ------------------------------------------------------------------------------------------------------------

class FakeVolume:
    def __init__(self):
        self.level, self.muted = 40, False

    def get(self):
        return {"available": True, "level": self.level, "muted": self.muted}

    def set_level(self, percent):
        self.level = percent
        self.muted = False if percent > 0 else self.muted
        return self.get()

    def set_muted(self, muted):
        self.muted = muted
        return self.get()


def test_the_volume_slider_reads_and_sets_the_real_level(client):
    client.daily.volume = FakeVolume()
    assert client.get("/api/daily/volume").json() == {"available": True, "level": 40, "muted": False}
    assert client.post("/api/daily/volume", json={"level": 75}).json() == {"available": True, "level": 75, "muted": False}
    assert client.post("/api/daily/volume", json={"muted": True}).json()["muted"] is True
    assert client.post("/api/daily/volume", json={"level": 20}).json() == {"available": True, "level": 20, "muted": False}   # moving it up un-mutes
    events = audit(client)
    assert "quick_control" in events


def test_the_volume_slider_refuses_nonsense(client):
    client.daily.volume = FakeVolume()
    for body in ({}, {"level": 101}, {"level": -1}, {"level": "loud"}, {"muted": "maybe"}):
        assert client.post("/api/daily/volume", json=body).status_code == 422, body
    assert client.daily.volume.level == 40


def test_a_pc_without_sound_says_so_instead_of_failing(client):
    class NoSound:
        def get(self):
            raise controls.ControlError("This PC has no sound output to control.")

    client.daily.volume = NoSound()
    state = client.get("/api/daily/volume").json()
    assert state["available"] is False and "no sound" in state["why"]
    res = client.post("/api/daily/volume", json={"level": 10})
    assert res.status_code == 422 and "no sound" in res.json()["detail"]["message"]


def test_the_brightness_slider_reads_and_sets(client):
    shell_calls = []

    def shell(script):
        shell_calls.append(script)
        return "60" if "CurrentBrightness" in script else ""

    client.daily._brightness_shell = shell
    assert client.get("/api/daily/brightness").json() == {"available": True, "level": 60}
    assert client.post("/api/daily/brightness", json={"level": 35}).json() == {"available": True, "level": 35}
    assert client.get("/api/daily/brightness").json()["level"] == 35   # what was just set is what is shown, without asking Windows again
    assert any("Brightness=[byte]35" in s for s in shell_calls)
    assert client.post("/api/daily/brightness", json={"level": 101}).status_code == 422


def test_brightness_on_a_desktop_says_it_cannot(client):
    client.daily._brightness_shell = lambda script: ""
    assert client.get("/api/daily/brightness").json() == {"available": False, "level": 0}
    assert client.post("/api/daily/brightness", json={"level": 50}).status_code == 422


def test_the_sliders_follow_the_levels_and_pause(client):
    client.daily.volume = FakeVolume()
    client.app.state.manager.pause_switch.pause("test")
    assert client.get("/api/daily/volume").status_code == 423
    assert client.post("/api/daily/volume", json={"level": 10}).status_code == 423


# ---- the inbox ------------------------------------------------------------------------------------------------------------

def test_text_sent_from_the_phone_is_kept_and_listed(client):
    sent = client.post("/api/daily/inbox/text", json={"text": "remember the milk"})
    assert sent.status_code == 200 and sent.json()["kind"] == "text"
    listed = client.get("/api/daily/inbox").json()
    assert [i["text"] for i in listed["to_pc"]] == ["remember the milk"]
    assert client.post("/api/daily/inbox/text", json={"text": "  "}).status_code == 422
    assert "inbox_received" in audit(client)


def test_a_link_is_recognised_and_opened_in_the_browser_only_when_asked(client):
    opened = []
    client.daily.inbox._opener = opened.append
    link = client.post("/api/daily/inbox/text", json={"text": "https://example.com/page"}).json()
    assert link["kind"] == "link" and opened == []
    assert client.post(f"/api/daily/inbox/{link['id']}/open").status_code == 200 and opened == ["https://example.com/page"]
    text = client.post("/api/daily/inbox/text", json={"text": "javascript:alert(1)"}).json()
    assert text["kind"] == "text"
    assert client.post(f"/api/daily/inbox/{text['id']}/open").status_code == 422


def test_text_can_go_on_the_pcs_clipboard_and_the_log_never_holds_it(client):
    class Clip:
        text = None

        def set_text(self, text):
            self.text = text

    clip = Clip()
    client.daily.inbox.clipboard = clip
    item = client.post("/api/daily/inbox/text", json={"text": "secret-looking words"}).json()
    assert client.post(f"/api/daily/inbox/{item['id']}/clipboard").json()["characters"] == 20 and clip.text == "secret-looking words"
    assert "secret-looking" not in json.dumps(client.app.state.manager.audit_events())


def test_a_file_arrives_streamed_and_comes_back_byte_for_byte(client):
    data = bytes(range(256)) * 400
    res = client.put("/api/daily/inbox/file?name=photo.bin", content=data, headers={"Content-Type": "application/octet-stream"})
    assert res.status_code == 200 and res.json()["size"] == len(data)
    back = client.get(f"/api/daily/inbox/{res.json()['id']}/file")
    assert back.status_code == 200 and back.content == data and "photo.bin" in back.headers["content-disposition"]
    assert back.headers["x-content-type-options"] == "nosniff"


def test_a_file_over_the_limit_is_refused_and_nothing_is_kept(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_DAILY__MAX_UPLOAD_MB", "1")
    app = create_app(load_settings(project / "config.yaml"))
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    big = b"x" * (1024 * 1024 + 10)
    assert c.put("/api/daily/inbox/file?name=big.bin", content=big).status_code == 413
    streamed = c.put("/api/daily/inbox/file?name=big2.bin", content=(chunk for chunk in [b"x" * 600_000, b"y" * 600_000]))
    assert streamed.status_code == 413 and "limit" in streamed.json()["detail"]["message"]
    assert c.get("/api/daily/inbox").json()["to_pc"] == []
    inbox_dir = app.state.daily.inbox.to_pc_dir
    assert not inbox_dir.exists() or list(inbox_dir.iterdir()) == []


def test_an_empty_upload_is_refused(client):
    assert client.put("/api/daily/inbox/file?name=a.txt", content=b"").status_code == 422


def test_a_file_name_cannot_reach_outside_the_inbox_folder(client):
    res = client.put("/api/daily/inbox/file?name=..%5C..%5C..%5Cevil.bat", content=b"x")
    item = res.json()
    assert res.status_code == 200 and item["name"] == "evil.bat" and item["risky"]
    stored = list(client.daily.inbox.to_pc_dir.iterdir())
    assert len(stored) == 1 and stored[0].parent == client.daily.inbox.to_pc_dir


def test_items_can_be_removed_and_a_wrong_id_is_not_found(client):
    item = client.post("/api/daily/inbox/text", json={"text": "x"}).json()
    assert client.delete(f"/api/daily/inbox/{item['id']}").status_code == 200
    assert client.delete(f"/api/daily/inbox/{item['id']}").status_code == 404
    assert client.get("/api/daily/inbox/..%2F..%2Fx/file").status_code == 404
    assert client.get("/api/daily/inbox/0123456789abcdef/file").status_code == 404


def test_files_the_person_drops_for_the_phone_can_be_fetched_by_name_only(client):
    folder = client.daily.inbox.to_phone_dir
    folder.mkdir(parents=True)
    (folder / "menu.pdf").write_bytes(b"pdf!")
    (folder.parent / "secret.txt").write_text("no")
    assert [f["name"] for f in client.get("/api/daily/inbox").json()["to_phone"]] == ["menu.pdf"]
    assert client.get("/api/daily/tophone/file", params={"name": "menu.pdf"}).content == b"pdf!"
    for bad in ("../secret.txt", "..\\secret.txt", "missing.txt", "menu.pdf:x"):
        assert client.get("/api/daily/tophone/file", params={"name": bad}).status_code == 404, bad


def test_the_to_phone_download_limit_is_kept(project, monkeypatch):
    app_settings = load_settings(project / "config.yaml")
    app_settings.agent.max_download_mb = 1
    app = create_app(app_settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    folder = app.state.daily.inbox.to_phone_dir
    folder.mkdir(parents=True)
    (folder / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    assert c.get("/api/daily/tophone/file", params={"name": "big.bin"}).status_code == 413


# ---- notes ----------------------------------------------------------------------------------------------------------------

def test_notes_and_todos_are_saved_as_text_files(client):
    assert client.post("/api/daily/notes", json={"text": "ring the dentist"}).status_code == 200
    assert client.post("/api/daily/notes", json={"text": "send invoice", "todo": True}).status_code == 200
    notes = client.get("/api/daily/notes").json()
    assert [n["text"] for n in notes["notes"]] == ["send invoice", "ring the dentist"] and notes["notes"][0]["todo"]
    files = list(client.daily.notes.dir.glob("*.md"))
    assert len(files) == 1 and "ring the dentist" in files[0].read_text(encoding="utf-8")
    assert client.post("/api/daily/notes", json={"text": " "}).status_code == 422
    assert "note_saved" in audit(client) and "ring the dentist" not in json.dumps(client.app.state.manager.audit_events())


# ---- reminders, watchers --------------------------------------------------------------------------------------------------

def test_a_reminder_can_be_set_listed_and_cancelled(client):
    import time

    due = time.time() + 3600
    task = client.post("/api/daily/tasks", json={"kind": "remind", "due": due, "text": "call mum", "repeat": "daily"})
    assert task.status_code == 200 and task.json()["repeat"] == "daily"
    assert [t["text"] for t in client.get("/api/daily/tasks").json()["tasks"]] == ["call mum"]
    assert client.delete(f"/api/daily/tasks/{task.json()['id']}").status_code == 200
    assert client.delete(f"/api/daily/tasks/{task.json()['id']}").status_code == 404
    assert "task_scheduled" in audit(client) and "task_removed" in audit(client)


def test_a_reminder_that_comes_due_shows_up_in_the_phones_alerts(client):
    import time

    client.post("/api/daily/tasks", json={"kind": "remind", "due": time.time() + 30, "text": "stand up"})
    assert client.get("/api/daily/alerts").json()["alerts"] == []
    client.daily.scheduler._clock = lambda: time.time() + 60
    client.daily.tick()
    alerts = client.get("/api/daily/alerts").json()["alerts"]
    assert [(a["title"], a["body"]) for a in alerts] == [("Reminder", "stand up")]
    assert client.get("/api/daily/alerts", params={"after": alerts[0]["id"]}).json()["alerts"] == []


def test_bad_times_and_actions_are_refused(client):
    import time

    assert client.post("/api/daily/tasks", json={"kind": "remind", "due": 5, "text": "x"}).status_code == 422
    assert client.post("/api/daily/tasks", json={"kind": "power", "due": time.time() + 99, "action": "shutdown"}).status_code == 422  # not in power.remote_actions
    assert client.post("/api/daily/tasks", json={"kind": "remind", "due": time.time() + 99, "text": ""}).status_code == 422
    assert client.post("/api/daily/tasks", json={"kind": "bogus", "due": time.time() + 99}).status_code == 422


def test_a_scheduled_lock_goes_through_the_pc_power_path(client):
    import time

    done = []
    client.daily._power = lambda key: done.append(key) or "ok"
    client.post("/api/daily/tasks", json={"kind": "power", "due": time.time() + 30, "action": "lock"})
    client.daily.scheduler._clock = lambda: time.time() + 60
    client.daily.tick()
    assert done == ["lock"] and "scheduled_power" in audit(client)


def test_a_program_watcher_fires_when_it_finishes(client):
    state = {"on": True}
    client.daily.watchers._running = lambda name: state["on"]
    assert client.post("/api/daily/watch", json={"kind": "process", "target": "render.exe"}).status_code == 200
    assert client.post("/api/daily/watch", json={"kind": "process", "target": "..\\x"}).status_code == 422
    state["on"] = False
    client.daily.tick()
    client.daily.tick()
    assert client.get("/api/daily/watch").json()["watchers"] == []
    assert [a["title"] for a in client.get("/api/daily/alerts").json()["alerts"]] == ["Finished"]


def test_a_folder_watcher_uses_the_usual_folders_or_what_the_phone_may_read(client, tmp_path, monkeypatch):
    from handoff.daily import places

    downloads = tmp_path / "dl"
    downloads.mkdir()
    monkeypatch.setattr(places, "all_places", lambda: [{"key": "downloads", "name": "Downloads", "path": str(downloads)}])
    assert client.post("/api/daily/watch", json={"kind": "folder", "target": str(downloads)}).status_code == 200
    outside = tmp_path / "private"
    outside.mkdir()
    refused = client.post("/api/daily/watch", json={"kind": "folder", "target": str(outside)})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "needs_access"
    inside = client.app.state.manager.roots()[0][0] if hasattr(client.app.state.manager, "roots") else None
    workspace = [p for p in (client.daily.manager._policy.visible_roots()) if p[1] != "ask"]
    assert workspace  # the allowed workspace itself may be watched
    assert client.post("/api/daily/watch", json={"kind": "folder", "target": str(workspace[0][0])}).status_code == 200


def test_the_programs_list_is_for_operators_and_has_names_only(client):
    processes = client.get("/api/daily/processes").json()["processes"]
    assert processes and set(processes[0]) == {"name", "count", "memory_mb"}


# ---- the briefing, recents, pins -------------------------------------------------------------------------------------------

def test_the_briefing_has_lines_and_counts(client):
    client.daily.updates._counter = lambda: 0
    body = client.get("/api/daily/briefing").json()
    assert body["lines"] and "updates" in body and "inbox" in body and isinstance(body["top_programs"], list)


def test_recent_files_are_filtered_by_the_folder_rules(client, tmp_path):
    allowed = client.daily.manager._policy.visible_roots()[0][0]
    mine = allowed / "plan.txt"
    mine.write_text("x")
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "x.txt").write_text("x")
    client.daily.recents._source = lambda: [{"path": str(mine), "time": 5}, {"path": str(other / "x.txt"), "time": 4}]
    names = {f["name"]: f for f in client.get("/api/daily/recent").json()["files"]}
    assert "plan.txt" in names and "x.txt" in names   # a file outside is listed (tapping it asks for the folder), never opened here


def test_places_can_be_pinned_and_removed(client, tmp_path):
    folder = tmp_path / "Projects"
    folder.mkdir()
    assert client.post("/api/daily/favourites", json={"path": str(folder), "label": "Work"}).json()["label"] == "Work"
    listed = client.get("/api/daily/favourites").json()
    assert [f["label"] for f in listed["favourites"]] == ["Work"] and isinstance(listed["suggestions"], list)
    assert client.delete("/api/daily/favourites", params={"path": str(folder)}).json() == {"removed": True}
    assert client.post("/api/daily/favourites", json={"path": str(tmp_path / "nope")}).status_code == 422


# ---- printing -------------------------------------------------------------------------------------------------------------

def test_a_file_in_an_allowed_folder_is_printed_after_a_logged_request(client, monkeypatch):
    printed = []
    monkeypatch.setattr(printing, "print_file", lambda path: printed.append(path.name) or "Office")
    workspace = client.daily.manager._policy.visible_roots()[0][0]
    (workspace / "ticket.pdf").write_bytes(b"%PDF")
    res = client.post("/api/daily/print", json={"path": str(workspace / "ticket.pdf")})
    assert res.status_code == 200 and res.json() == {"ok": True, "printer": "Office", "name": "ticket.pdf"} and printed == ["ticket.pdf"]
    assert "print" in audit(client)


def test_a_file_outside_the_allowed_folders_is_not_printed_but_the_phone_is_asked_to_allow_it(client, tmp_path, monkeypatch):
    monkeypatch.setattr(printing, "print_file", lambda path: pytest.fail("must not print"))
    secret = tmp_path / "private" / "diary.pdf"
    secret.parent.mkdir()
    secret.write_bytes(b"x")
    res = client.post("/api/daily/print", json={"path": str(secret)})
    assert res.status_code == 403 and res.json()["detail"]["code"] == "needs_access" and res.json()["detail"]["folder"] == str(secret.parent)


def test_a_missing_file_a_program_and_a_switched_off_printer_are_refused(client, monkeypatch):
    workspace = client.daily.manager._policy.visible_roots()[0][0]
    assert client.post("/api/daily/print", json={"path": str(workspace / "nope.pdf")}).status_code == 404
    (workspace / "run.exe").write_bytes(b"MZ")
    assert client.post("/api/daily/print", json={"path": str(workspace / "run.exe")}).status_code == 422
    monkeypatch.setattr(client.daily.cfg, "printing", False)
    off = client.post("/api/daily/print", json={"path": str(workspace / "run.exe")})
    assert off.status_code == 404 and off.json()["detail"]["code"] == "daily_off"
    assert client.get("/api/daily/print/info").json() == {"enabled": False}


# ---- snapshots ------------------------------------------------------------------------------------------------------------

def test_the_webcam_is_refused_unless_it_was_switched_on(client):
    res = client.post("/api/daily/snap/webcam")
    assert res.status_code == 403 and res.json()["detail"]["code"] == "webcam_off"


def test_a_screenshot_needs_a_paired_phone(client):
    assert client.post("/api/daily/snap/screen").status_code == 404


def test_the_webcam_photo_is_logged_first_kept_and_every_phone_is_told(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_DAILY__WEBCAM", "true")
    app = create_app(load_settings(project / "config.yaml"))
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    told = []
    app.state.daily.tell = told.append
    monkeypatch.setattr(printing, "webcam_photo", lambda: b"\xff\xd8jpeg")
    res = c.post("/api/daily/snap/webcam")
    assert res.status_code == 200 and res.content == b"\xff\xd8jpeg" and res.headers["content-type"] == "image/jpeg"
    saved = app.state.daily.folder / "Screenshots" / res.headers["x-saved-as"]
    assert saved.read_bytes() == b"\xff\xd8jpeg"
    assert [m.kind for m in told] == ["security"] and "snap_webcam" in [e["event"] for e in app.state.manager.audit_events()]


def test_a_webcam_that_cannot_be_used_says_why_and_still_leaves_a_log_line(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_DAILY__WEBCAM", "true")
    app = create_app(load_settings(project / "config.yaml"))
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})

    def broken():
        raise printing.PrintError("This PC's camera could not be opened.")

    monkeypatch.setattr(printing, "webcam_photo", broken)
    res = c.post("/api/daily/snap/webcam")
    assert res.status_code == 503 and "camera" in res.json()["detail"]["message"]
    assert "snap_webcam" in [e["event"] for e in app.state.manager.audit_events()]


# ---- access levels and the one-at-a-time rule (real accounts) -----------------------------------------------------------------

@pytest.fixture
def world(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_POWER__REMOTE_ACTIONS", '["lock","sleep","shutdown"]')
    monkeypatch.setenv("HANDOFF_DAILY__WEBCAM", "true")
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


def set_level(admin, name, level):
    device = next(d["id"] for d in admin.get("/admin/devices", headers=H).json() if d["name"] == name)
    assert admin.post("/admin/access/level", json={"device_id": device, "level": level}, headers=H).status_code == 200


def test_a_view_only_phone_can_read_but_not_send_or_control(world):
    phone, admin, people, _ = world
    set_level(admin, "Ben's phone", "viewer")
    ben = people["Ben"]
    for path in ("/api/daily/state", "/api/daily/inbox", "/api/daily/notes", "/api/daily/tasks", "/api/daily/briefing", "/api/daily/watch"):
        assert phone.get(path, headers=ben).status_code == 200, path
    for path, body in (("/api/daily/inbox/text", {"text": "x"}), ("/api/daily/notes", {"text": "x"}), ("/api/daily/control", {"action": "mute"}),
                       ("/api/daily/volume", {"level": 10}), ("/api/daily/brightness", {"level": 10}),
                       ("/api/daily/tasks", {"kind": "remind", "due": 9e12, "text": "x"}), ("/api/daily/print", {"path": "x"})):
        res = phone.post(path, json=body, headers=ben)
        assert res.status_code == 403 and res.json()["detail"]["code"] == "level", path
    assert phone.put("/api/daily/inbox/file?name=a.txt", content=b"x", headers=ben).status_code == 403
    assert phone.get("/api/daily/processes", headers=ben).status_code == 403


def test_a_read_and_write_phone_may_send_and_note_but_not_control_the_pc(world, monkeypatch):
    phone, admin, people, _ = world
    set_level(admin, "Ben's phone", "editor")
    ben = people["Ben"]
    assert phone.post("/api/daily/inbox/text", json={"text": "hi"}, headers=ben).status_code == 200
    assert phone.post("/api/daily/notes", json={"text": "hi"}, headers=ben).status_code == 200
    for path, body in (("/api/daily/control", {"action": "mute"}), ("/api/daily/open-place", {"key": "downloads"})):
        assert phone.post(path, json=body, headers=ben).status_code == 403, path
    item = phone.get("/api/daily/inbox", headers=ben).json()["to_pc"][0]
    assert phone.post(f"/api/daily/inbox/{item['id']}/clipboard", headers=ben).status_code == 403
    assert phone.post(f"/api/daily/inbox/{item['id']}/open", headers=ben).status_code == 403


def test_a_scheduled_shutdown_needs_the_admin_phone_but_a_lock_does_not(world):
    phone, _, people, _ = world
    import time

    due = time.time() + 600
    assert phone.post("/api/daily/tasks", json={"kind": "power", "due": due, "action": "lock"}, headers=people["Ben"]).status_code == 200
    phone.post("/api/session/end", headers=people["Ben"])
    refused = phone.post("/api/daily/tasks", json={"kind": "power", "due": due, "action": "shutdown"}, headers=people["Ben"])
    assert refused.status_code == 403 and refused.json()["detail"]["need"] == "admin"
    phone.post("/api/session/end", headers=people["Ben"])
    assert phone.post("/api/daily/tasks", json={"kind": "power", "due": due, "action": "shutdown"}, headers=people["Asha"]).status_code == 200
    phone.post("/api/session/end", headers=people["Asha"])
    refused_watch = phone.post("/api/daily/watch", json={"kind": "quiet", "target": "10", "then": "shutdown"}, headers=people["Ben"])
    assert refused_watch.status_code == 403


def test_changes_need_your_turn_but_reading_never_does(world):
    phone, _, people, _ = world
    assert phone.post("/api/daily/inbox/text", json={"text": "from Ben"}, headers=people["Ben"]).status_code == 200  # Ben starts a turn
    blocked = phone.post("/api/daily/inbox/text", json={"text": "from Chen"}, headers=people["Chen"])
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "session_held"
    assert phone.get("/api/daily/inbox", headers=people["Chen"]).status_code == 200


def test_only_the_admin_phone_may_use_the_webcam(world, monkeypatch):
    phone, _, people, app = world
    monkeypatch.setattr(printing, "webcam_photo", lambda: b"\xff\xd8x")
    app.state.daily.tell = lambda message: None
    refused = phone.post("/api/daily/snap/webcam", headers=people["Ben"])
    assert refused.status_code == 403 and refused.json()["detail"]["need"] == "admin"
    assert app.state.access.sessions.holder_id() is None   # refused for its level: asking did not take Ben's turn
    assert phone.post("/api/daily/snap/webcam", headers=people["Asha"]).status_code == 200


def test_a_screenshot_needs_the_owners_yes_first(world):
    phone, _, people, _ = world
    res = phone.post("/api/daily/snap/screen", headers=people["Ben"])
    assert res.status_code == 403 and res.json()["detail"]["code"] == "screen_not_allowed" and res.json()["detail"]["state"] == "none"


def test_a_screenshot_is_taken_for_a_phone_that_is_looking_and_a_granted_one_stops_afterwards(world, monkeypatch):
    phone, _, people, app = world
    screen = app.state.screen
    ben_id = next(d["id"] for d in app.state.accounts.devices() if d["name"] == "Ben's phone")
    screen.grant(ben_id, 15)
    monkeypatch.setattr(screen, "frame", lambda *a, **k: b"\xff\xd8shot")
    res = phone.post("/api/daily/snap/screen", headers=people["Ben"])
    assert res.status_code == 200 and res.content == b"\xff\xd8shot"
    assert (app.state.daily.folder / "Screenshots" / res.headers["x-saved-as"]).read_bytes() == b"\xff\xd8shot"
    assert screen.view(ben_id)["state"] == "granted"   # looking started for the picture and stopped again
