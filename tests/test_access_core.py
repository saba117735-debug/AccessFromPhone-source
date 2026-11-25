"""The access rules (levels), who is Admin, and the one-person-at-a-time session with its timeouts. All timing uses a fake clock: nothing waits."""
import pytest

from handoff.access import Access, AccessDenied, SessionRefused, Settings, levels
from handoff.accounts import AccountStore
from handoff.db import Database


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def world(tmp_path):
    db = Database(tmp_path / "app.db")
    accounts = AccountStore(db, pairing_minutes=2, max_devices=5, mode="password")
    accounts.setup_owner("Owner", "correct horse")
    people = {}
    for name in ("Asha", "Ben", "Chen"):
        people[name], _ = accounts.pair(accounts.new_pairing_code()[0], name, "")
    clock, events = Clock(), []
    access = Access(db, Settings(lease=60, idle=300, ask=60, cooldown=120), on_event=lambda kind, **d: events.append((kind, d)), clock=clock)
    return access, people, clock, events


def who(people, name):
    return people[name].id, people[name].name


# ---------------------------------------------------------------- the rules

def steps(*actions):
    return [{"action": x} for x in actions]


def test_levels_are_ordered_and_a_plan_needs_its_most_serious_step():
    assert levels.at_least("editor", "viewer") and not levels.at_least("viewer", "editor")
    assert levels.level_for_steps(steps("read")) == "viewer"
    assert levels.level_for_steps(steps("read", "write")) == "editor"
    assert levels.level_for_steps(steps("write", "delete", "send")) == "editor"
    assert levels.level_for_steps(steps("write", "run")) == "operator"
    assert levels.level_for_steps(steps("app")) == "operator"
    assert levels.level_for_steps(steps("mystery")) == "operator"  # a step nobody named counts as the serious kind


def test_unlock_and_restart_need_the_admin_but_lock_and_sleep_do_not():
    power = lambda text: [{"action": "power", "detail": text}]  # noqa: E731
    assert levels.level_for_steps(power("lock this PC")) == "operator"
    assert levels.level_for_steps(power("sleep")) == "operator"
    for text in ("unlock", "restart this PC", "shut down: shutdown", "hibernate"):
        assert levels.level_for_steps(power(text)) == "admin", text


def test_which_requests_need_a_session():
    assert not levels.needs_session("GET", "/api/jobs")
    assert levels.needs_session("POST", "/api/jobs") and levels.needs_session("POST", "/api/jobs/ab12/approve") and levels.needs_session("PUT", "/api/file")
    for path in ("/api/session/start", "/api/pause", "/api/device/rename", "/api/push/subscribe", "/api/title", "/api/browse/stream", "/api/access/level"):
        assert not levels.needs_session("POST", path), path


# ---------------------------------------------------------------- the Admin and the levels

def test_the_first_paired_phone_is_the_admin_until_the_owner_picks_another(world):
    access, people, *_ = world
    assert access.store.role(people["Asha"].id) == "admin" and access.store.level(people["Asha"].id) == "admin"
    assert access.store.role(people["Ben"].id) == "user" and access.store.level(people["Ben"].id) == "operator"
    access.store.set_admin(people["Ben"].id)
    assert access.store.role(people["Ben"].id) == "admin" and access.store.role(people["Asha"].id) == "user"
    access.store.set_admin(None)
    assert not any(p["role"] == "admin" for p in access.store.people())  # the Owner can choose to have no Admin phone


def test_a_removed_admin_leaves_nobody_as_admin_until_one_is_picked(world):
    access, people, *_ = world
    access.store.set_admin(people["Ben"].id)
    access.store.forget(people["Ben"].id)
    assert not any(p["role"] == "admin" for p in access.store.people())


def test_admin_is_a_role_never_a_level_you_can_hand_out(world):
    access, people, *_ = world
    with pytest.raises(ValueError):
        access.store.set_level(people["Ben"].id, "admin")
    access.store.set_level(people["Ben"].id, "viewer")
    assert access.store.level(people["Ben"].id) == "viewer"


# ---------------------------------------------------------------- the gate

def test_a_viewer_cannot_change_things_and_is_told_why(world):
    access, people, *_ = world
    ben, name = who(people, "Ben")
    access.store.set_level(ben, "viewer")
    with pytest.raises(AccessDenied) as err:
        access.gate.check("PUT", "/api/file", ben, name)
    assert err.value.status == 403 and err.value.code == "level" and "view only" in err.value.message.lower()
    access.gate.check("GET", "/api/file", ben, name)  # looking is fine
    with pytest.raises(AccessDenied):
        access.gate.check_plan([{"action": "write"}], ben)
    access.gate.check_plan([{"action": "read"}], ben)


def test_a_free_session_starts_by_itself_and_blocks_the_next_person(world):
    access, people, *_ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    access.gate.check("POST", "/api/jobs", a, an)
    assert access.sessions.holder_id() == a
    with pytest.raises(AccessDenied) as err:
        access.gate.check("POST", "/api/jobs", b, bn)
    assert err.value.status == 409 and err.value.code == "session_held" and err.value.extra["holder"] == "Asha"
    access.gate.check("GET", "/api/jobs", b, bn)  # B can still look
    access.gate.check("POST", "/api/push/subscribe", b, bn)  # and manage their own phone


# ---------------------------------------------------------------- takeover and the timeouts

def test_a_takeover_the_holder_agrees_to(world):
    access, people, clock, events = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    access.sessions.start(a, an, "admin")
    assert access.sessions.request_takeover(b, bn, "operator", "need the printer") == "asked"
    assert access.sessions.view(a)["ask"]["name"] == "Ben" and access.sessions.view(b)["mine_ask"]["holder"] == "Asha"
    assert access.sessions.answer(a, end_session=True) == "handed over"
    assert access.sessions.holder_id() == b and access.sessions.view(b)["notice"]["kind"] == "granted"
    assert [k for k, _ in events] == ["session_started", "takeover_requested", "session_ended", "session_started", "takeover_answered"]


def test_a_holder_who_continues_keeps_it_and_the_asker_waits_before_asking_again(world):
    access, people, clock, _ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    access.sessions.start(a, an, "admin")
    access.sessions.request_takeover(b, bn, "operator")
    assert access.sessions.answer(a, end_session=False) == "kept"
    assert access.sessions.holder_id() == a and access.sessions.view(b)["notice"]["kind"] == "kept"
    with pytest.raises(SessionRefused):
        access.sessions.request_takeover(b, bn, "operator")
    clock.advance(121)
    access.sessions.touch(a)
    assert access.sessions.request_takeover(b, bn, "operator") == "asked"


def test_an_active_holder_who_does_not_answer_keeps_the_session(world):
    access, people, clock, _ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    access.sessions.start(a, an, "admin")
    access.sessions.request_takeover(b, bn, "operator")
    for _ in range(7):  # Asha keeps working, and does not look at the question
        clock.advance(10)
        access.gate.check("POST", "/api/jobs", a, an)
    assert access.sessions.holder_id() == a and access.sessions.view(a)["ask"] is None
    assert access.sessions.view(b)["notice"]["kind"] == "kept"


def test_a_holder_who_has_gone_loses_the_session_to_the_one_waiting(world):
    access, people, clock, _ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    access.sessions.start(a, an, "admin")
    access.sessions.request_takeover(b, bn, "operator")
    clock.advance(61)  # Asha's phone stopped checking in
    assert access.sessions.holder_id() == b


def test_an_idle_holder_loses_the_session_after_five_minutes(world):
    access, people, clock, _ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    access.sessions.start(a, an, "admin")
    for _ in range(31):  # the page keeps checking in (so it is not gone) but nothing is changed
        clock.advance(10)
        access.sessions.touch(a)
    assert access.sessions.holder_id() is None
    access.gate.check("POST", "/api/jobs", b, bn)  # so Ben simply gets it
    assert access.sessions.holder_id() == b


def test_the_admin_can_end_or_take_over_and_the_holder_is_told(world):
    access, people, clock, _ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    chen = who(people, "Chen")[0]
    access.sessions.start(b, bn, "operator")
    access.sessions.request_takeover(chen, "Chen", "operator")
    assert access.sessions.release("Asha") is True
    assert access.sessions.holder_id() is None and access.sessions.view(chen)["mine_ask"] is None  # Asha decides what happens next
    access.sessions.start(b, bn, "operator")
    access.sessions.take_over(a, an, "admin", "Asha")
    assert access.sessions.holder_id() == a and access.sessions.view(b)["notice"] == {"kind": "taken", "by": "Asha"}


def test_pause_ends_everything_and_only_one_phone_waits_at_a_time(world):
    access, people, clock, _ = world
    (a, an), (b, bn), (c, cn) = who(people, "Asha"), who(people, "Ben"), who(people, "Chen")
    access.sessions.start(a, an, "admin")
    access.sessions.request_takeover(b, bn, "operator")
    with pytest.raises(SessionRefused):
        access.sessions.request_takeover(c, cn, "operator")  # one waiting at a time
    access.sessions.end_all("paused")
    assert access.sessions.holder_id() is None and access.sessions.view(b)["mine_ask"] is None


def test_asking_when_it_is_free_just_starts_it_and_asking_for_your_own_is_a_no_op(world):
    access, people, *_ = world
    (a, an), (b, bn) = who(people, "Asha"), who(people, "Ben")
    assert access.sessions.request_takeover(a, an, "admin") == "started"
    assert access.sessions.request_takeover(a, an, "admin") == "mine"
    assert access.sessions.answer(b, True) == "nothing to answer"
