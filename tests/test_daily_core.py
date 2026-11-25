"""The everyday helpers, one at a time, with the PC and the clock replaced by fakes: quick controls, inbox, notes, reminders, watchers,
notification reading, the briefing, recents and printing."""
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest

from handoff.daily import briefing, controls, places, printing
from handoff.daily.inbox import Inbox, InboxError, is_risky, link_of, safe_name
from handoff.daily.notes import Notes
from handoff.daily.notifications import Mirror, app_name, parse_payload, read_new
from handoff.daily.recents import FavError, Recents
from handoff.daily.schedule import ScheduleError, Scheduler, next_due
from handoff.daily.watch import WatchError, Watchers
from handoff.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "app.db")


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


# ---- quick controls ---------------------------------------------------------------------------------------------------

def test_volume_steps_are_five_key_presses_each():
    pressed = []
    controls.perform("volume_up", 2, press=lambda vk, n: pressed.append((vk, n)))
    controls.perform("mute", press=lambda vk, n: pressed.append((vk, n)))
    assert pressed == [(0xAF, 10), (0xAD, 1)]


def test_an_unknown_control_is_refused():
    with pytest.raises(controls.ControlError):
        controls.perform("format_disk", press=lambda *a: None)


def test_steps_are_kept_between_one_and_ten():
    pressed = []
    controls.perform("volume_down", 99, press=lambda vk, n: pressed.append(n))
    assert pressed == [50]


def test_brightness_moves_ten_percent_and_never_goes_black():
    calls = []

    def shell(script):
        calls.append(script)
        return "30" if "CurrentBrightness" in script else ""

    assert controls.perform("brightness_down", 5, shell=shell) == "Brightness is 5%."
    assert "Brightness=[byte]5" in calls[-1]
    assert controls.perform("brightness_up", shell=shell) == "Brightness is 40%."


def test_a_desktop_monitor_says_brightness_cannot_be_changed():
    with pytest.raises(controls.ControlError, match="desktop"):
        controls.perform("brightness_up", shell=lambda script: "")


def test_brightness_is_refused_while_the_screen_is_blacked_out():
    with pytest.raises(controls.ControlError, match="blacked out"):
        controls.perform("brightness_up", shell=lambda s: "50", screen_dark=True)


def test_a_usual_folder_opens_and_another_name_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(places, "known_folder", lambda name: tmp_path if name == "downloads" else None)
    opened = []
    assert "Downloads" in controls.open_place("downloads", opener=opened.append)
    assert opened == [str(tmp_path)]
    with pytest.raises(controls.ControlError):
        controls.open_place("C:\\Windows", opener=opened.append)
    with pytest.raises(controls.ControlError, match="no Music"):
        controls.open_place("music", opener=opened.append)


def test_the_usual_folders_are_found_on_this_pc():
    found = {p["key"] for p in places.all_places()}
    assert "downloads" in found or "documents" in found  # at least one exists on any Windows user profile
    assert places.known_folder("not-a-folder") is None


# ---- inbox ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("..\\..\\evil.txt", "evil.txt"), ("a/b/c.png", "c.png"), ("CON.txt", "_CON.txt"), ("what?.pdf", "what_.pdf"),
    ("  spaced.  ", "spaced"), ("", "file"), ("...", "file"), ('q"uo:te.txt', "q_uo_te.txt"),
])
def test_file_names_are_made_safe(raw, expected):
    assert safe_name(raw) == expected


def test_a_very_long_name_keeps_its_extension():
    name = safe_name("x" * 300 + ".pdf")
    assert len(name) <= 120 and name.endswith(".pdf")


def test_only_a_plain_web_address_counts_as_a_link():
    assert link_of(" https://example.com/a?b=1 ") == "https://example.com/a?b=1"
    for text in ("javascript:alert(1)", "file:///C:/x", "https://a.com and more", "example.com", "http://", "ftp://x.com"):
        assert link_of(text) is None


def test_programs_and_scripts_are_marked_risky():
    assert is_risky("setup.EXE") and is_risky("run.ps1") and is_risky("x.lnk")
    assert not is_risky("photo.jpg")


@pytest.fixture
def inbox(db, tmp_path):
    return Inbox(db, tmp_path / "daily", 1024, clock=Clock())


def test_text_and_links_arrive_and_are_listed_newest_first(db, tmp_path):
    clock = Clock()
    inbox = Inbox(db, tmp_path / "daily", 1024, clock=clock)
    first = inbox.add_text("buy milk", "Asha's phone")
    clock.now += 5
    second = inbox.add_text("https://example.com", "Asha's phone")
    assert (first["kind"], second["kind"]) == ("text", "link")
    assert [i["id"] for i in inbox.items()] == [second["id"], first["id"]]


def test_empty_and_huge_text_are_refused(inbox):
    for text in ("", "   \n "):
        with pytest.raises(InboxError):
            inbox.add_text(text)
    with pytest.raises(InboxError, match="too long"):
        inbox.add_text("x" * 20_001)


def test_a_file_is_kept_whole_and_can_be_fetched_and_removed(inbox):
    up = inbox.open_upload("report.pdf", "Asha")
    up.write(b"abc")
    up.write(b"def")
    item = up.commit()
    assert item["size"] == 6 and item["name"] == "report.pdf" and not item["risky"]
    path, name = inbox.file_of(item["id"])
    assert path.read_bytes() == b"abcdef" and name == "report.pdf" and path.parent == inbox.to_pc_dir
    inbox.remove(item["id"])
    assert not path.exists() and inbox.items() == []


def test_a_file_over_the_limit_is_thrown_away(inbox):
    up = inbox.open_upload("big.bin")
    up.write(b"x" * 1000)
    with pytest.raises(InboxError, match="larger"):
        up.write(b"x" * 100)
    assert list(inbox.to_pc_dir.glob("*")) == [] and inbox.items() == []


def test_an_empty_file_is_refused(inbox):
    up = inbox.open_upload("empty.txt")
    with pytest.raises(InboxError, match="empty"):
        up.commit()
    assert list(inbox.to_pc_dir.glob("*")) == []


def test_a_name_that_climbs_out_of_the_folder_stays_inside(inbox, tmp_path):
    up = inbox.open_upload("..\\..\\..\\Windows\\evil.exe")
    up.write(b"1")
    item = up.commit()
    path, _ = inbox.file_of(item["id"])
    assert path.parent == inbox.to_pc_dir and item["name"] == "evil.exe" and item["risky"]


def test_the_inbox_only_keeps_the_newest_items(db, tmp_path):
    clock = Clock()
    small = Inbox(db, tmp_path / "d", 1024, clock=clock)
    for i in range(305):
        clock.now += 1
        small.add_text(f"note {i}")
    assert len(small.items()) == 100 and db.one("SELECT COUNT(*) AS n FROM daily_inbox")["n"] == 300


def test_text_goes_to_the_clipboard_and_a_file_does_not(db, tmp_path):
    class Clip:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    clip = Clip()
    box = Inbox(db, tmp_path / "d", 1024, clipboard=clip)
    item = box.add_text("hello there")
    assert box.copy_to_clipboard(item["id"]) == 11 and clip.text == "hello there"
    up = box.open_upload("a.txt")
    up.write(b"1")
    file_item = up.commit()
    with pytest.raises(InboxError):
        box.copy_to_clipboard(file_item["id"])
    with pytest.raises(InboxError, match="clipboard"):
        Inbox(db, tmp_path / "d", 1024, clipboard=None).copy_to_clipboard(item["id"])


def test_only_a_stored_link_can_be_opened_in_the_browser(db, tmp_path):
    opened = []
    box = Inbox(db, tmp_path / "d", 1024, opener=opened.append)
    link = box.add_text("https://example.com/x")
    text = box.add_text("just words")
    assert box.open_link(link["id"]) == "https://example.com/x" and opened == ["https://example.com/x"]
    with pytest.raises(InboxError):
        box.open_link(text["id"])
    with pytest.raises(InboxError):
        box.open_link("not-an-id")


def test_item_ids_cannot_be_used_to_reach_other_files(inbox):
    for bad in ("../../x", "..", "0" * 15, "G" * 16, "' OR 1=1 --"):
        with pytest.raises(InboxError):
            inbox.file_of(bad)


def test_files_dropped_in_the_to_phone_folder_are_listed_and_fetched_by_exact_name(inbox):
    inbox.to_phone_dir.mkdir(parents=True)
    (inbox.to_phone_dir / "menu.pdf").write_bytes(b"pdf")
    (inbox.to_phone_dir / ".hidden").write_bytes(b"x")
    (inbox.to_phone_dir / "sub").mkdir()
    assert [f["name"] for f in inbox.to_phone()] == ["menu.pdf"]
    assert inbox.to_phone_file("menu.pdf").read_bytes() == b"pdf"


@pytest.mark.parametrize("name", ["../secret.txt", "..\\secret.txt", "sub", "missing.txt", "", "..", "menu.pdf:stream", "C:\\Windows\\win.ini"])
def test_nothing_outside_the_to_phone_folder_can_be_fetched(inbox, name):
    inbox.to_phone_dir.mkdir(parents=True)
    (inbox.to_phone_dir / "sub").mkdir()
    (inbox.to_phone_dir.parent / "secret.txt").write_text("no")
    with pytest.raises(InboxError):
        inbox.to_phone_file(name)


# ---- notes --------------------------------------------------------------------------------------------------------------

def test_notes_are_appended_to_one_file_a_day_and_read_back(tmp_path):
    clock = Clock(datetime(2026, 5, 4, 9, 30).timestamp())
    notes = Notes(tmp_path, clock)
    notes.add("call the bank")
    clock.now += 3600
    notes.add("buy milk\nand eggs", todo=True)
    text = (tmp_path / "Notes" / "Notes 2026-05-04.md").read_text(encoding="utf-8")
    assert text == "- 09:30 call the bank\n- [ ] 10:30 buy milk\n  and eggs\n"
    back = notes.recent()
    assert [(n["time"], n["text"], n["todo"]) for n in back] == [("10:30", "buy milk\nand eggs", True), ("09:30", "call the bank", False)]


def test_empty_or_enormous_notes_are_refused(tmp_path):
    notes = Notes(tmp_path)
    with pytest.raises(InboxError):
        notes.add("  ")
    with pytest.raises(InboxError):
        notes.add("x" * 5001)
    assert notes.recent() == []


# ---- reminders and scheduled actions ---------------------------------------------------------------------------------------

class Rig:
    """The things a scheduler and the watchers call out to, recorded."""

    def __init__(self, allowed=("lock", "sleep")):
        self.told, self.powered, self.noted = [], [], []
        self.allowed = set(allowed)

    def kwargs(self, clock):
        return dict(clock=clock, notify=lambda t, b, k: self.told.append((t, b, k)), run_power=lambda key: self.powered.append(key) or "ok",
                    allowed_power=lambda: self.allowed, note=lambda event, **d: self.noted.append(event))


@pytest.fixture
def clock():
    return Clock()


def test_a_reminder_fires_once_at_its_time_and_goes_away(db, clock):
    rig = Rig()
    tasks = Scheduler(db, **rig.kwargs(clock))
    tasks.add(kind="remind", due=clock.now + 60, text="stand up")
    assert tasks.tick() == [] and rig.told == []
    clock.now += 61
    assert [r["result"] for r in tasks.tick()] == ["done"]
    assert rig.told == [("Reminder", "stand up", "reminder")]
    assert tasks.list() == [] and tasks.tick() == []


def test_a_scheduled_lock_runs_but_one_switched_off_is_refused_up_front(db, clock):
    rig = Rig(allowed=("lock",))
    tasks = Scheduler(db, **rig.kwargs(clock))
    with pytest.raises(ScheduleError, match="switched off"):
        tasks.add(kind="power", due=clock.now + 60, action="shutdown")
    tasks.add(kind="power", due=clock.now + 60, action="lock")
    clock.now += 61
    tasks.tick()
    assert rig.powered == ["lock"] and "scheduled_power" in rig.noted


def test_a_power_action_the_config_removed_after_scheduling_is_not_run(db, clock):
    rig = Rig(allowed=("lock", "sleep"))
    tasks = Scheduler(db, **rig.kwargs(clock))
    tasks.add(kind="power", due=clock.now + 60, action="sleep")
    rig.allowed = {"lock"}
    clock.now += 61
    assert tasks.tick()[0]["result"] == "switched off" and rig.powered == []


def test_a_power_action_the_pc_missed_is_never_run_late(db, clock):
    rig = Rig()
    tasks = Scheduler(db, **rig.kwargs(clock))
    tasks.add(kind="power", due=clock.now + 60, action="sleep")
    clock.now += 3 * 3600  # the PC was off
    out = tasks.tick()
    assert out[0]["late"] and out[0]["result"] == "missed" and rig.powered == []
    assert rig.told and "missed" in rig.told[0][0].lower()


def test_a_missed_reminder_still_tells_the_phone_marked_late(db, clock):
    rig = Rig()
    tasks = Scheduler(db, **rig.kwargs(clock))
    tasks.add(kind="remind", due=clock.now + 60, text="pay rent")
    clock.now += 7200
    tasks.tick()
    assert rig.told == [("Reminder (late)", "pay rent", "reminder")]


def test_a_daily_task_comes_round_again_at_the_same_time(db, clock):
    rig = Rig()
    tasks = Scheduler(db, **rig.kwargs(clock))
    first = clock.now + 60
    tasks.add(kind="remind", due=first, repeat="daily", text="pills")
    clock.now += 61
    tasks.tick()
    (left,) = tasks.list()
    assert left["last_result"] == "done" and datetime.fromtimestamp(left["due"]).time() == datetime.fromtimestamp(first).time()
    assert left["due"] > clock.now


def test_a_repeating_task_that_was_missed_for_days_skips_to_the_next_future_time(db, clock):
    rig = Rig()
    tasks = Scheduler(db, **rig.kwargs(clock))
    tasks.add(kind="remind", due=clock.now + 60, repeat="daily", text="x")
    clock.now += 5 * 86400
    tasks.tick()
    assert tasks.list()[0]["due"] > clock.now
    assert len(rig.told) == 1  # one late reminder, not five


def test_weekdays_skip_the_weekend():
    friday = datetime(2026, 5, 1, 9, 0).timestamp()
    assert datetime(2026, 5, 1).weekday() == 4
    assert datetime.fromtimestamp(next_due(friday, "weekdays", friday)).weekday() == 0
    assert datetime.fromtimestamp(next_due(friday, "weekly", friday)).date() == datetime(2026, 5, 8).date()
    assert next_due(friday, "", friday) is None


@pytest.mark.parametrize("kwargs, message", [
    (dict(kind="remind", due=-1, text="x"), "passed"),
    (dict(kind="remind", due=float("nan"), text="x"), "real time"),
    (dict(kind="remind", due=float("inf"), text="x"), "real time"),
    (dict(kind="remind", due=10**12, text="x"), "within the next year"),
    (dict(kind="remind", due=1_800_000_100, text=""), "Say what"),
    (dict(kind="remind", due=1_800_000_100, text="x", repeat="hourly"), "repeat"),
    (dict(kind="nuke", due=1_800_000_100), "kind"),
    (dict(kind="power", due=1_800_000_100, action="format"), "switched off"),
])
def test_bad_tasks_are_refused_with_a_reason(db, clock, kwargs, message):
    with pytest.raises(ScheduleError, match=message):
        Scheduler(db, **Rig().kwargs(clock)).add(**kwargs)


def test_there_is_a_limit_on_how_many_tasks_can_wait(db, clock):
    tasks = Scheduler(db, **Rig().kwargs(clock))
    for _ in range(50):
        tasks.add(kind="remind", due=clock.now + 60, text="x")
    with pytest.raises(ScheduleError, match="50"):
        tasks.add(kind="remind", due=clock.now + 60, text="x")


def test_one_task_failing_does_not_stop_the_next(db, clock):
    rig = Rig()
    kwargs = rig.kwargs(clock)

    def boom(key):
        raise RuntimeError("no")

    tasks = Scheduler(db, **{**kwargs, "run_power": boom})
    tasks.add(kind="power", due=clock.now + 10, action="lock")
    tasks.add(kind="remind", due=clock.now + 20, text="still here")
    clock.now += 30
    results = [r["result"] for r in tasks.tick()]
    assert results[0].startswith("failed") and results[1] == "done"


# ---- watchers --------------------------------------------------------------------------------------------------------------

def test_a_program_finishing_is_noticed_after_it_stays_gone(db, clock):
    rig = Rig()
    running = {"render.exe": True}
    watch = Watchers(db, **rig.kwargs(clock), running=lambda n: running.get(n, False))
    watch.add(kind="process", target="render.exe")
    assert watch.tick() == []
    running["render.exe"] = False
    assert watch.tick() == []          # gone once: could be a restart of the program
    running["render.exe"] = True
    assert watch.tick() == []          # back again: the count starts over
    running["render.exe"] = False
    watch.tick()
    assert watch.tick()[0]["kind"] == "process" and rig.told == [("Finished", "render.exe has finished.", "watch")]
    assert watch.list() == []


def test_the_list_of_running_programs_is_quick_and_biggest_first():
    import time as clock_time

    from handoff.daily.watch import list_processes

    started = clock_time.time()
    programs = list_processes(8)
    assert clock_time.time() - started < 2.0   # the general route through psutil took 5 to 13 seconds on a busy PC
    assert programs and all({"name", "count", "memory_mb"} == set(p) and p["count"] >= 1 for p in programs)
    assert [p["memory_mb"] for p in programs] == sorted((p["memory_mb"] for p in programs), reverse=True)
    assert sum(p["memory_mb"] for p in programs) > 0


def test_watching_a_program_that_is_not_running_is_refused(db, clock):
    watch = Watchers(db, **Rig().kwargs(clock), running=lambda n: False)
    with pytest.raises(WatchError, match="not running"):
        watch.add(kind="process", target="ghost.exe")
    for bad in ("", "..\\x.exe", "a*b", "x" * 101):
        with pytest.raises(WatchError):
            watch.add(kind="process", target=bad)


def test_a_finished_download_is_noticed_only_when_it_stops_growing(db, clock, tmp_path):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / "old.zip").write_bytes(b"1")
    rig = Rig()
    watch = Watchers(db, **rig.kwargs(clock), resolve_folder=lambda p: Path(p))
    watch.add(kind="folder", target=str(folder))
    assert watch.tick() == []                              # what was there is not news
    (folder / "movie.mp4.crdownload").write_bytes(b"1")
    assert watch.tick() == []                              # a browser's half-file never counts
    (folder / "movie.mp4").write_bytes(b"12")
    assert watch.tick() == []                              # seen once
    (folder / "movie.mp4").write_bytes(b"123")
    assert watch.tick() == []                              # still growing
    assert watch.tick()[0]["message"] == "movie.mp4 has arrived in Downloads."
    assert watch.list() == []


def test_a_folder_that_does_not_exist_cannot_be_watched(db, clock, tmp_path):
    watch = Watchers(db, **Rig().kwargs(clock), resolve_folder=lambda p: Path(p))
    with pytest.raises(WatchError, match="does not exist"):
        watch.add(kind="folder", target=str(tmp_path / "nope"))


def test_a_quiet_processor_is_noticed_after_half_a_minute(db, clock):
    busy = {"cpu": 80.0}
    watch = Watchers(db, **Rig().kwargs(clock), cpu=lambda: busy["cpu"])
    watch.add(kind="quiet", target="15")
    for _ in range(10):
        assert watch.tick() == []
    busy["cpu"] = 3.0
    for _ in range(5):
        assert watch.tick() == []
    assert watch.tick()[0]["kind"] == "quiet"


def test_the_quiet_level_must_be_a_sensible_number(db, clock):
    watch = Watchers(db, **Rig().kwargs(clock))
    for bad in ("0", "99", "lots"):
        with pytest.raises(WatchError):
            watch.add(kind="quiet", target=bad)


def test_a_watcher_can_put_the_pc_to_sleep_afterwards_only_if_allowed(db, clock):
    rig = Rig(allowed=("lock",))
    state = {"on": True}
    watch = Watchers(db, **rig.kwargs(clock), running=lambda n: state["on"])
    with pytest.raises(WatchError, match="switched off"):
        watch.add(kind="process", target="setup.exe", then="sleep")
    watch.add(kind="process", target="setup.exe", then="lock")
    state["on"] = False
    watch.tick()
    watch.tick()
    assert rig.powered == ["lock"] and "Now: lock." in rig.told[0][1]


def test_there_is_a_limit_on_watchers(db, clock):
    watch = Watchers(db, **Rig().kwargs(clock), running=lambda n: True)
    for i in range(20):
        watch.add(kind="process", target=f"p{i}.exe")
    with pytest.raises(WatchError, match="20"):
        watch.add(kind="process", target="more.exe")


# ---- the PC's notifications ------------------------------------------------------------------------------------------------

TOAST = ('<toast launch="x"><visual><binding template="ToastGeneric"><text id="1">Asha</text><text id="2">Are you coming?</text>'
         '<text id="3">tonight</text></binding></visual></toast>')


def test_a_toast_gives_a_title_and_a_body():
    assert parse_payload(TOAST) == ("Asha", "Are you coming? tonight")
    assert parse_payload(TOAST.encode("utf-8")) == ("Asha", "Are you coming? tonight")


@pytest.mark.parametrize("payload", ["", "not xml", "<toast>", '<!DOCTYPE x [<!ENTITY a "b">]><toast><text>&a;</text></toast>', "x" * 30000, None])
def test_odd_toast_payloads_give_nothing(payload):
    assert parse_payload(payload) == ("", "")


def test_app_names_are_readable():
    assert app_name("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App") == "Whats App Desktop"
    assert app_name("Windows.SystemToast.WiFiNetworkManager") == "Wi Fi Network Manager"
    assert app_name("") == "Windows"
    assert app_name("Microsoft.Teams_8wekyb3d8bbwe!MSTeams") == "Teams"


def make_wpn(path, rows):
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT);"
                       "CREATE TABLE Notification (Id INTEGER PRIMARY KEY, HandlerId INTEGER, Type TEXT, Payload BLOB, ArrivalTime INTEGER);")
    conn.execute("INSERT INTO NotificationHandler VALUES (1, 'Contoso.Chat_abcdefghijklm!App')")
    for ident, kind, payload, when in rows:
        conn.execute("INSERT INTO Notification (Id, HandlerId, Type, Payload, ArrivalTime) VALUES (?, 1, ?, ?, ?)", (ident, kind, payload, int((when + 11644473600) * 10_000_000)))
    conn.commit()
    conn.close()


def test_windows_notification_database_is_read_from_a_copy(tmp_path):
    now = time.time()
    source = tmp_path / "wpndatabase.db"
    make_wpn(source, [(1, "toast", TOAST, now - 60), (2, "badge", "<badge/>", now - 50), (3, "toast", TOAST, now - 10), (4, "toast", TOAST, now - 99999)])
    before = source.read_bytes()
    items, top = read_new(source, 0, now - 3600)
    assert top == 4 and [i["id"] for i in items] == [1, 3]  # badges and old ones are left out
    assert items[0]["app"] == "Chat" and items[0]["title"] == "Asha"
    assert source.read_bytes() == before                    # the PC's own database is untouched
    assert [i["id"] for i in read_new(source, 3, 0)[0]] == [4]


def test_the_mirror_backfills_once_then_reports_only_what_is_new(tmp_path):
    now = time.time()
    source = tmp_path / "wpndatabase.db"
    make_wpn(source, [(1, "toast", TOAST, now - 60)])
    news = []
    mirror = Mirror(database=source, on_new=news.append)
    assert [i["id"] for i in mirror.poll()] == [1] and news == []   # first look: shown, but not "new"
    assert mirror.poll() == []
    conn = sqlite3.connect(source)
    conn.execute("INSERT INTO Notification (Id, HandlerId, Type, Payload, ArrivalTime) VALUES (2, 1, 'toast', ?, ?)", (TOAST, int((now + 11644473600) * 10_000_000)))
    conn.commit()
    conn.close()
    assert [i["id"] for i in mirror.poll()] == [2] and len(news) == 1
    assert [i["id"] for i in mirror.items()] == [2, 1]
    assert mirror.dismiss(2) == 1 and [i["id"] for i in mirror.items()] == [1]
    assert mirror.dismiss() == 2 and mirror.items() == []


def test_a_missing_or_broken_database_is_reported_not_raised(tmp_path):
    assert Mirror(database=tmp_path / "none.db").poll() == []
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database")
    mirror = Mirror(database=broken)
    assert mirror.poll() == [] and "could not be read" in mirror.problem


# ---- the briefing ----------------------------------------------------------------------------------------------------------

STATUS = {"available": True, "battery": {"percent": 80, "plugged": False, "state": "discharging"}, "memory": {"percent": 55},
          "disk": {"free": 120 * 1024 ** 3}, "uptime_s": 2 * 86400 + 5 * 3600}


def test_the_briefing_says_what_matters_in_a_few_lines():
    out = briefing.build(STATUS, updates={"count": 3, "reboot": False}, inbox={"to_phone": 2}, tasks=[{"due": 1000}, {"due": 10**12}], watchers=[{}],
                         alerts=0, notifications=4, processes=lambda n: [], now=500)
    text = " ".join(out["lines"])
    assert "Battery 80% and on battery" in text and "120 GB free" in text and "Up for 2 d 5 h" in text
    assert "3 Windows updates waiting" in text and "1 reminder or task in the next 24 hours" in text
    assert "Watching 1 thing" in text and "2 files waiting" in text and "4 PC notifications" in text


def test_a_quiet_pc_says_so():
    out = briefing.build({"available": False}, updates={"count": 0, "reboot": False}, inbox={"to_phone": 0}, tasks=[], watchers=[], alerts=0, notifications=None, processes=lambda n: [])
    assert out["lines"] == ["Nothing needs you right now."]


def test_a_pending_restart_is_said_before_the_update_count():
    out = briefing.build({"available": False}, updates={"count": 2, "reboot": True}, inbox={}, tasks=[], watchers=[], alerts=0, notifications=None, processes=lambda n: [])
    assert out["lines"] == ["Windows is waiting for a restart to finish updating."]


def test_windows_is_asked_about_updates_in_the_background_and_the_answer_is_remembered():
    asked, clock = [], Clock()
    updates = briefing.Updates(counter=lambda: asked.append(1) or 4, clock=clock, reboot=lambda: False)
    first = updates.state(wait=True)
    assert first["count"] == 4 and not first["checking"]
    updates.state()
    assert len(asked) == 1               # remembered
    clock.now += 3600
    updates.state(wait=True)
    assert len(asked) == 2               # and asked again after half an hour


# ---- recent files and pinned places ---------------------------------------------------------------------------------------

def test_recent_files_skip_missing_duplicate_and_refused_ones(db, tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("1"), b.write_text("2")
    source = lambda: [{"path": str(a), "time": 3}, {"path": str(a).upper(), "time": 2}, {"path": str(tmp_path / "gone.txt"), "time": 1}, {"path": str(b), "time": 0}]
    recents = Recents(db, source=source, allowed=lambda p: not p.endswith("b.txt"))
    assert [r["name"] for r in recents.recent_files()] == ["a.txt"]


def test_the_recent_list_is_not_re_read_on_every_request(db, tmp_path):
    calls, clock = [], Clock()
    recents = Recents(db, clock=clock, source=lambda: calls.append(1) or [])
    recents.recent_files()
    recents.recent_files()
    assert len(calls) == 1
    clock.now += 31
    recents.recent_files()
    assert len(calls) == 2


def test_places_can_be_pinned_renamed_and_unpinned(db, tmp_path):
    recents = Recents(db)
    pin = recents.pin(str(tmp_path), "  My   stuff ")
    assert pin["label"] == "My stuff" and pin["exists"]
    recents.pin(str(tmp_path), "Renamed")
    assert [f["label"] for f in recents.favourites()] == ["Renamed"]
    assert recents.unpin(str(tmp_path)) and not recents.unpin(str(tmp_path)) and recents.favourites() == []


def test_a_place_that_does_not_exist_cannot_be_pinned_and_there_is_a_limit(db, tmp_path):
    recents = Recents(db)
    with pytest.raises(FavError):
        recents.pin(str(tmp_path / "nope"))
    with pytest.raises(FavError):
        recents.pin("")
    for i in range(30):
        folder = tmp_path / f"f{i}"
        folder.mkdir()
        recents.pin(str(folder))
    extra = tmp_path / "extra"
    extra.mkdir()
    with pytest.raises(FavError, match="30"):
        recents.pin(str(extra))


# ---- printing --------------------------------------------------------------------------------------------------------------

PRINTERS = '[{"Name":"Office","Default":true,"WorkOffline":false},{"Name":"PDF","Default":false,"WorkOffline":false}]'


def test_printers_are_listed_and_the_default_is_found():
    assert [p["name"] for p in printing.printers(lambda s: PRINTERS)] == ["Office", "PDF"]
    assert printing.default_printer(lambda s: PRINTERS)["name"] == "Office"
    single = '{"Name":"Only","Default":true,"WorkOffline":false}'
    assert printing.default_printer(lambda s: single)["name"] == "Only"
    assert printing.printers(lambda s: "") == [] and printing.printers(lambda s: "garbage") == []


def test_a_document_is_sent_to_the_default_printer(tmp_path):
    doc = tmp_path / "ticket.pdf"
    doc.write_bytes(b"%PDF")
    started = []
    name = printing.print_file(doc, shell=lambda s: PRINTERS, starter=lambda target, verb: started.append((target, verb)))
    assert name == "Office" and started == [(str(doc), "print")]


@pytest.mark.parametrize("name", ["setup.exe", "run.ps1", "script.bat", "noextension", "link.lnk", "page.html"])
def test_programs_and_unknown_files_are_never_printed(tmp_path, name):
    f = tmp_path / name
    f.write_bytes(b"x")
    with pytest.raises(printing.PrintError):
        printing.print_file(f, shell=lambda s: PRINTERS, starter=lambda *a: pytest.fail("must not start anything"))


def test_no_default_printer_or_an_offline_one_is_explained(tmp_path):
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"x")
    with pytest.raises(printing.PrintError, match="no default printer"):
        printing.print_file(doc, shell=lambda s: "[]", starter=lambda *a: None)
    offline = '[{"Name":"Office","Default":true,"WorkOffline":true}]'
    with pytest.raises(printing.PrintError, match="offline"):
        printing.print_file(doc, shell=lambda s: offline, starter=lambda *a: None)


def test_a_file_with_no_print_program_is_explained(tmp_path):
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"x")

    def starter(target, verb):
        raise OSError("no association")

    with pytest.raises(printing.PrintError, match="no program"):
        printing.print_file(doc, shell=lambda s: PRINTERS, starter=starter)


def test_the_webcam_is_off_the_table_without_its_package():
    if printing.webcam_available():
        pytest.skip("opencv is installed here")
    with pytest.raises(printing.PrintError, match="opencv"):
        printing.webcam_photo()


# ---- live levels for the sliders -------------------------------------------------------------------------------------------

def test_brightness_for_a_slider_says_whether_it_can_be_changed():
    assert controls.brightness_state(lambda s: "55") == {"available": True, "level": 55}
    assert controls.brightness_state(lambda s: "") == {"available": False, "level": 0}


def test_setting_brightness_goes_to_the_level_asked_but_never_below_the_floor():
    calls = []

    def shell(script):
        calls.append(script)
        return "40" if "CurrentBrightness" in script else ""

    assert controls.set_brightness(70, shell=shell) == {"available": True, "level": 70} and "Brightness=[byte]70" in calls[-1]
    assert controls.set_brightness(0, shell=shell)["level"] == 5
    assert controls.set_brightness(500, shell=shell)["level"] == 100


def test_brightness_cannot_be_set_on_a_desktop_or_while_the_screen_is_blacked_out():
    with pytest.raises(controls.ControlError, match="desktop"):
        controls.set_brightness(50, shell=lambda s: "")
    with pytest.raises(controls.ControlError, match="blacked out"):
        controls.set_brightness(50, shell=lambda s: "50", screen_dark=True)


def test_windows_audio_can_be_read_and_set_to_what_it_already_is():
    from handoff.daily.audio_volume import WindowsVolume

    volume = WindowsVolume()
    try:
        before = volume.get()
    except controls.ControlError as exc:
        pytest.skip(f"no usable sound device here: {exc}")
    assert 0 <= before["level"] <= 100 and isinstance(before["muted"], bool)
    after = volume.set_level(before["level"]) if not before["muted"] else before   # a no-change write; a muted PC is left alone
    assert after["level"] == before["level"] and after["muted"] == before["muted"]
