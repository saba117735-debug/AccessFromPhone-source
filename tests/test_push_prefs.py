"""What each phone chose to hear about: types switched off, quiet hours on the phone's own clock, and the safety rules around them."""
from datetime import datetime, timezone

from handoff.push import Message, Notifier, allows, clean_prefs, default_prefs
from handoff.config import PushConfig
from handoff.db import Database


def at(hour, minute=0):
    return datetime(2026, 11, 16, hour, minute, tzinfo=timezone.utc)


def prefs(**quiet):
    p = default_prefs()
    p["quiet"].update(quiet)
    return p


def test_everything_is_on_by_default():
    for kind in ("awaiting", "needs_access", "done", "failed", "pc_sleep", "pc_battery", "security", "test"):
        assert allows(default_prefs(), kind, at(3))


def test_a_switched_off_type_is_not_sent_and_the_others_still_are():
    p = default_prefs()
    p["types"]["done"] = False
    assert not allows(p, "done", at(12)) and allows(p, "awaiting", at(12)) and allows(p, "failed", at(12))
    p["types"]["pc"] = False
    assert not allows(p, "pc_sleep", at(12)) and not allows(p, "pc_battery", at(12))


def test_quiet_hours_cross_midnight():
    p = prefs(on=True, start="22:00", end="07:00", tz=0)
    assert not allows(p, "awaiting", at(23)) and not allows(p, "awaiting", at(2)) and not allows(p, "awaiting", at(6, 59))
    assert allows(p, "awaiting", at(7)) and allows(p, "awaiting", at(12)) and allows(p, "awaiting", at(21, 59))


def test_quiet_hours_use_the_phones_own_clock():
    p = prefs(on=True, start="22:00", end="07:00", tz=330)  # India: UTC+5:30
    assert not allows(p, "done", at(17, 0))   # 22:30 there
    assert allows(p, "done", at(2, 0))        # 07:30 there


def test_quiet_hours_within_one_day():
    p = prefs(on=True, start="13:00", end="14:00", tz=0)
    assert not allows(p, "done", at(13, 30)) and allows(p, "done", at(14, 0)) and allows(p, "done", at(12, 59))


def test_security_alerts_and_tests_ignore_quiet_hours_but_not_their_own_switch():
    p = prefs(on=True, start="00:00", end="23:59", tz=0)
    assert allows(p, "security", at(3)) and allows(p, "test", at(3))
    p["types"]["security"] = False
    assert not allows(p, "security", at(3)) and allows(p, "test", at(3))


def test_bad_input_falls_back_to_safe_defaults():
    p = clean_prefs({"types": {"done": "no", "bogus": False, "failed": False}, "quiet": {"on": "yes", "start": "25:99", "end": "nope", "tz": 99999}})
    assert p["types"]["done"] is True and p["types"]["failed"] is False and "bogus" not in p["types"]
    assert p["quiet"] == default_prefs()["quiet"]
    assert clean_prefs(None) == default_prefs() and clean_prefs("x") == default_prefs()


def test_the_choice_is_stored_per_phone(tmp_path):
    notifier = Notifier(PushConfig(enabled=False), Database(tmp_path / "app.db"))
    notifier.set_prefs("a", {"types": {"done": False}})
    assert notifier.prefs("a")["types"]["done"] is False and notifier.prefs("b") == default_prefs()
    notifier.set_prefs("a", {"types": {"done": True}})
    assert notifier.prefs("a")["types"]["done"] is True


def test_the_page_sends_and_loads_the_choice(tmp_path):
    from pathlib import Path
    app = (Path(__file__).resolve().parents[1] / "src" / "handoff" / "web" / "app.js").read_text(encoding="utf-8")
    assert '"/push/prefs"' in app and "quiet" in app
