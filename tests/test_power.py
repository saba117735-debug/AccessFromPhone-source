import time

import pytest

from handoff import power
from handoff.config import Action, ApprovalConfig
from handoff.jobs import JobManager, Status
from handoff.planner import quick_for, quick_power


def kind_of(text):
    plan = quick_power(text)
    return plan.steps[0].content if plan else None


@pytest.mark.parametrize("text, expected", [
    ("put my pc to sleep", "sleep"),
    ("Put the computer to sleep", "sleep"),
    ("please put my laptop to sleep.", "sleep"),
    ("can you put my PC to sleep?", "sleep"),
    ("sleep my pc", "sleep"),
    ("I want to put this computer into sleep", "sleep"),
    ("send my pc to sleep", "sleep"),
    ("suspend the computer", "sleep"),
    ("lock my pc", "lock"),
    ("lock the screen", "lock"),
    ("could you lock this computer", "lock"),
    ("hibernate my laptop", "hibernate"),
    ("restart my pc", "restart"),
    ("reboot the computer", "restart"),
    ("shut down my pc", "shutdown"),
    ("shutdown the computer", "shutdown"),
    ("turn off my laptop", "shutdown"),
    ("turn off my screen", "screen_off"),
    ("please switch off the monitor", "screen_off"),
    ("black out the display", "screen_off"),
])
def test_plain_power_requests_are_understood_without_a_model(text, expected):
    assert kind_of(text) == expected


@pytest.mark.parametrize("text", [
    "how do I put my pc to sleep?",
    "why does my computer go to sleep so fast",
    "what does restart do",
    "read sleep.txt",
    "delete the shutdown log",
    "write a script that will lock the screen after 5 minutes",
    "put the file in the sleep folder",
    "list",
    "lock",
    "how do I turn off my screen",
    "turn off the screen saver settings file",
])
def test_questions_and_other_requests_are_left_alone(text):
    assert quick_power(text) is None


def test_it_comes_first_in_the_quick_requests():
    assert quick_for("put my pc to sleep", None).steps[0].action is Action.POWER


def test_power_always_needs_approval_even_if_config_forgets_it():
    assert "power" in ApprovalConfig(require_for=["write"]).require_for


@pytest.fixture
def fake(monkeypatch):
    calls = []
    monkeypatch.setattr(power, "supported", lambda: True)
    for key in list(power.DOERS):
        monkeypatch.setitem(power.DOERS, key, lambda key=key: calls.append(key))
    monkeypatch.setattr(power, "DELAY_SECONDS", 0.05)
    real = power.schedule
    monkeypatch.setattr(power, "schedule", lambda key, delay=0.05, doer=None: real(key, 0.05, doer))
    return calls


def ask(manager, text):
    job = manager.submit(text)
    manager.join_background()
    return job


def test_nothing_happens_until_the_person_approves(settings, fake):
    manager = JobManager(settings)
    job = ask(manager, "put my pc to sleep")
    assert job.status is Status.AWAITING and job.steps[0].action is Action.POWER
    time.sleep(0.3)
    assert fake == []  # planned, not done
    shown = job.to_dict()["steps"][0]
    assert shown["action"] == "power" and "wake" in shown["warning"].lower()


def test_approving_does_it_once_and_after_telling_the_phone(settings, fake):
    manager = JobManager(settings)
    job = ask(manager, "put my pc to sleep")
    done = manager.approve(job.id)
    assert done.status is Status.DONE and "going to sleep" in done.outputs[0]
    assert fake == []  # not before the answer has gone back to the phone
    time.sleep(0.4)
    assert fake == ["sleep"]
    assert "power" in [e["event"] for e in manager.audit_events() if e["job"] == job.id]


def test_rejecting_does_nothing(settings, fake):
    manager = JobManager(settings)
    job = ask(manager, "lock my pc")
    manager.reject(job.id)
    time.sleep(0.3)
    assert fake == [] and job.status is Status.REJECTED


def test_restart_and_shutdown_are_off_unless_config_allows_them(settings, fake):
    manager = JobManager(settings)
    for text in ("restart my pc", "shut down my pc"):
        job = ask(manager, text)
        assert job.status is Status.FAILED and "power.remote_actions" in job.error
    assert fake == []


def test_they_work_once_allowed(settings, fake):
    settings.power.remote_actions = ["lock", "sleep", "restart", "shutdown"]
    manager = JobManager(settings)
    job = ask(manager, "restart my pc")
    assert job.status is Status.AWAITING
    manager.approve(job.id)
    time.sleep(0.4)
    assert fake == ["restart"]


def test_by_default_only_lock_and_sleep_are_allowed(settings):
    assert settings.power.remote_actions == ["lock", "screen_off", "sleep"]


def test_a_power_change_has_nothing_to_undo(settings, fake):
    manager = JobManager(settings)
    job = ask(manager, "lock my pc")
    manager.approve(job.id)
    assert job.changed == []


def test_other_systems_say_so_instead_of_failing_silently(settings, monkeypatch):
    monkeypatch.setattr(power, "supported", lambda: False)
    manager = JobManager(settings)
    job = ask(manager, "put my pc to sleep")
    done = manager.approve(job.id)
    assert done.status is Status.FAILED and "Windows only" in done.error


def test_a_shutdown_counts_down_so_a_mistake_can_be_cancelled_at_the_pc():
    assert power.GRACE_SECONDS >= 10 and power.ACTIONS["shutdown"].ends_connection and not power.ACTIONS["lock"].ends_connection


def test_the_blackout_keeps_the_connection_and_never_switches_the_display_off(settings):
    assert power.ACTIONS["screen_off"].ends_connection is False and power.ACTIONS["screen_on"].ends_connection is False
    assert "screen_off" in settings.power.remote_actions
    source = open(power.__file__, encoding="utf-8").read()
    assert "SC_MONITORPOWER" not in source and "0xF170" not in source   # a switched-off display freezes a Modern Standby PC


@pytest.mark.parametrize("text, expected", [
    ("turn on my screen", "screen_on"), ("turn my screen back on", "screen_on"), ("wake the screen", "screen_on"),
    ("please switch the monitor on", "screen_on"), ("bring back my display", "screen_on"),
    ("black out my screen", "screen_off"), ("turn the screen off", "screen_off"),
])
def test_the_screen_phrases_both_ways(text, expected):
    assert kind_of(text) == expected


def test_black_out_and_back_happen_at_once_after_approval(settings, fake):
    manager = JobManager(settings)
    job = ask(manager, "black out my screen")
    assert job.status is Status.AWAITING and fake == []
    done = manager.approve(job.id)
    assert done.status is Status.DONE and fake == ["screen_off"] and "black" in done.outputs[0]   # not after a delay: the person is told the truth
    back = ask(manager, "turn on my screen")
    manager.approve(back.id)
    assert fake == ["screen_off", "screen_on"]


def test_turning_it_back_on_is_allowed_whenever_blacking_out_is(settings, fake):
    settings.power.remote_actions = ["screen_off"]
    manager = JobManager(settings)
    assert ask(manager, "turn on my screen").status is Status.AWAITING
    settings.power.remote_actions = ["lock"]
    assert ask(manager, "turn on my screen").status is Status.FAILED


def test_if_the_cover_cannot_be_made_the_person_is_told_not_told_done(settings, monkeypatch):
    monkeypatch.setattr(power, "supported", lambda: True)
    def refuse():
        raise RuntimeError("This PC's Windows cannot hide the black cover from the phone's picture, so the screen was left as it is.")
    monkeypatch.setitem(power.DOERS, "screen_off", refuse)
    manager = JobManager(settings)
    job = ask(manager, "black out my screen")
    done = manager.approve(job.id)
    assert done.status is Status.FAILED and "left as it is" in (done.error or "")
