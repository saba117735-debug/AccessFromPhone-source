"""Unlocking the PC from the phone: what the helper keeps and refuses, how a request is approved with a PIN, and that no secret ever leaks.

The one thing these cannot do is type at a real lock screen: that needs the system account and a locked PC. Everything around it is tested
here with a stand-in for the keyboard; the typed key events themselves are planned and checked in `plan_text` below.
"""
import json
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff import power
from handoff.jobs import JobManager, Status
from handoff.planner import DemoPlanner, quick_for
from handoff.server import create_app
from handoff.unlock import (AgentDown, Locked, NeedsAttention, NotLocked, NotSetUp, StillLocked, UnlockError, WrongPin)
from handoff.unlock import lockstate, setup, typer
from handoff.unlock.agent import Agent, serve
from handoff.unlock.client import AgentClient
from handoff.unlock.store import UnlockStore

PIN = "483920"
WINDOWS_PIN = "7391"
FAKE_KEY = 0x5A


@pytest.fixture(autouse=True)
def _installer_log_in_a_temp_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "LOG", tmp_path / "unlock-setup.log")  # the tests must not write into the real log


def protect(data: bytes) -> bytes:
    return bytes(b ^ FAKE_KEY for b in data)  # a stand-in for DPAPI: not secret, but not the plain text either


@pytest.fixture
def store(tmp_path):
    clock = {"t": 1000.0}
    s = UnlockStore(tmp_path / "unlock.json", protect, protect, now=lambda: clock["t"])
    s.clock = clock
    return s


@pytest.fixture
def ready(store):
    store.configure("pin", WINDOWS_PIN, PIN)
    return store


# -- what is kept ----------------------------------------------------------------------------------

def test_nothing_is_configured_until_it_is_set_up(store):
    assert not store.configured() and store.status()["configured"] is False
    with pytest.raises(NotSetUp):
        store.verify_pin(PIN)


def test_the_saved_sign_in_and_the_unlock_pin_are_not_on_disk_in_plain_text(ready):
    raw = ready.path.read_bytes()
    assert WINDOWS_PIN.encode() not in raw and PIN.encode() not in raw
    assert ready.reveal() == ("pin", WINDOWS_PIN)  # but the helper can still read the sign-in back


def test_the_unlock_pin_is_only_stored_as_a_salted_hash(tmp_path):
    a, b = (UnlockStore(tmp_path / n, protect, protect) for n in ("a.json", "b.json"))
    a.configure("pin", WINDOWS_PIN, PIN)
    b.configure("pin", WINDOWS_PIN, PIN)
    assert json.loads(a.path.read_text())["pin"] != json.loads(b.path.read_text())["pin"]  # the same PIN looks different in each file


def test_the_unlock_pin_must_be_long_enough_and_not_the_sign_in(store):
    with pytest.raises(ValueError, match="6 to 32"):
        store.configure("pin", WINDOWS_PIN, "12345")
    with pytest.raises(ValueError, match="different"):
        store.configure("pin", "123456", "123456")
    with pytest.raises(ValueError):
        store.configure("fingerprint", WINDOWS_PIN, PIN)
    with pytest.raises(ValueError):
        store.configure("pin", "", PIN)


def test_the_right_pin_is_accepted_and_a_wrong_one_is_counted(ready):
    ready.verify_pin(PIN)
    with pytest.raises(WrongPin) as first:
        ready.verify_pin("000000")
    assert first.value.extra["tries_left"] == 2
    with pytest.raises(WrongPin) as second:
        ready.verify_pin("111111")
    assert second.value.extra["tries_left"] == 1
    ready.verify_pin(PIN)  # the right one resets the count
    with pytest.raises(WrongPin) as again:
        ready.verify_pin("000000")
    assert again.value.extra["tries_left"] == 2


def test_three_wrong_pins_pause_it_even_for_the_right_one_until_the_time_is_up(ready):
    for guess in ("000000", "111111"):
        with pytest.raises(WrongPin):
            ready.verify_pin(guess)
    with pytest.raises(Locked) as lockout:
        ready.verify_pin("222222")
    assert 590 <= lockout.value.extra["seconds"] <= 600
    with pytest.raises(Locked):
        ready.verify_pin(PIN)  # even the right PIN waits: a guesser can't tell it was right
    assert ready.status()["locked_for"] > 0
    ready.clock["t"] += 601
    ready.verify_pin(PIN)
    assert ready.status()["locked_for"] == 0


def test_the_pause_survives_the_helper_restarting(ready, tmp_path):
    for guess in ("000000", "111111", "222222"):
        with pytest.raises((WrongPin, Locked)):
            ready.verify_pin(guess)
    restarted = UnlockStore(ready.path, protect, protect, now=lambda: ready.clock["t"])
    with pytest.raises(Locked):
        restarted.verify_pin(PIN)


def test_it_stops_itself_when_the_typed_sign_in_twice_did_not_unlock(ready):
    ready.note_result(False)
    ready.verify_pin(PIN)  # one miss is tolerated
    ready.note_result(False)
    with pytest.raises(NeedsAttention):
        ready.verify_pin(PIN)
    assert ready.status()["attention"] is True
    ready.clear_attention()
    ready.verify_pin(PIN)


def test_a_success_clears_the_misses(ready):
    ready.note_result(False)
    ready.note_result(True)
    ready.note_result(False)
    ready.verify_pin(PIN)  # not "twice in a row"


def test_it_can_be_switched_off_and_removed(ready):
    ready.set_enabled(False)
    with pytest.raises(UnlockError) as off:
        ready.verify_pin(PIN)
    assert off.value.code == "disabled"
    ready.forget()
    assert not ready.path.exists() and not ready.configured()


def test_a_damaged_file_means_not_set_up_not_a_crash(ready):
    ready.path.write_text("{not json", encoding="utf-8")
    assert not ready.configured()
    with pytest.raises(NotSetUp):
        ready.verify_pin(PIN)


# -- the helper's rules ---------------------------------------------------------------------------

class Keys:
    """A stand-in for typing at the lock screen. `unlocks` decides whether the PC then reports itself unlocked."""

    def __init__(self, unlocks=True, code=0):
        self.unlocks, self.code, self.requests = unlocks, code, []

    def __call__(self, request):
        self.requests.append(request)
        if self.unlocks and self.code == 0:
            self.state["locked"] = False
        return self.code


def make_agent(store, locked=True, unlocks=True, code=0):
    keys = Keys(unlocks, code)
    state = {"locked": locked}
    keys.state = state
    log = []
    agent = Agent(store, locked=lambda: state["locked"], type_it=keys, log=log.append, wait_seconds=0.3, sleep=lambda s: None)
    return agent, keys, state, log


def test_the_helper_reports_how_things_stand(ready):
    agent, *_ = make_agent(ready)
    answer = agent.handle({"cmd": "status"})
    assert answer["ok"] and answer["configured"] and answer["enabled"] and answer["locked"] is True and answer["kind"] == "pin"
    assert WINDOWS_PIN not in json.dumps(answer) and PIN not in json.dumps(answer)


def test_the_right_pin_on_a_locked_pc_types_the_saved_sign_in_and_unlocks(ready):
    agent, keys, state, log = make_agent(ready)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert answer["ok"] and "unlocked" in answer["message"].lower()
    assert len(keys.requests) == 1 and keys.requests[0]["secret"] == WINDOWS_PIN and keys.requests[0]["submit"] is True
    assert state["locked"] is False


def test_a_wrong_pin_types_nothing(ready):
    agent, keys, _, _ = make_agent(ready)
    answer = agent.handle({"cmd": "unlock", "pin": "000000"})
    assert not answer["ok"] and answer["code"] == "wrong_pin" and answer["tries_left"] == 2
    assert keys.requests == []


def test_a_pc_that_is_not_locked_types_nothing(ready):
    agent, keys, _, _ = make_agent(ready, locked=False)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert not answer["ok"] and answer["code"] == "not_locked"
    assert keys.requests == []


def test_when_the_lock_screen_cannot_be_read_the_keys_are_still_sent_and_it_says_so(ready):
    agent, keys, state, _ = make_agent(ready, locked=None, unlocks=False)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert answer["ok"] and "sent" in answer["message"]
    assert len(keys.requests) == 1


def test_typing_that_does_not_unlock_is_reported_and_counted(ready):
    agent, keys, _, log = make_agent(ready, unlocks=False)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert not answer["ok"] and answer["code"] == "still_locked"
    assert ready.status()["attention"] is False
    again = agent.handle({"cmd": "unlock", "pin": PIN})
    assert again["code"] == "still_locked" and ready.status()["attention"] is True  # twice: it stops
    third = agent.handle({"cmd": "unlock", "pin": PIN})
    assert third["code"] == "needs_attention" and len(keys.requests) == 2  # and does not type a third time


def stop_it(store):
    store.note_result(False)
    store.note_result(False)
    assert store.status()["attention"] is True


def test_after_a_wait_it_starts_trying_again_by_itself(ready):
    stop_it(ready)
    ready.clock["t"] += 300
    with pytest.raises(NeedsAttention) as waiting:
        ready.verify_pin(PIN)
    assert "by itself" in str(waiting.value) and 200 < waiting.value.extra["seconds"] <= 300
    assert 0 < ready.status()["auto_resume_in"] <= 300 and ready.status()["attention"] is True
    ready.clock["t"] += 301
    ready.verify_pin(PIN)                                          # the wait is over: the next request from the phone is allowed
    assert ready.status()["attention"] is False and ready.status()["auto_resumes_left"] == 2


def test_the_pc_page_sees_it_running_again_without_any_request(ready):
    stop_it(ready)
    ready.clock["t"] += 601
    assert ready.status()["attention"] is False and ready.status()["auto_resume_in"] == 0


def test_it_restarts_itself_only_a_few_times_then_waits_for_the_owner(ready):
    said = []
    ready.notice = said.append
    for round_ in range(3):
        stop_it(ready)
        ready.clock["t"] += 601
        ready.verify_pin(PIN)
    assert len(said) == 3 and "3 of 3" in said[-1]
    stop_it(ready)                                                  # the saved sign-in is probably wrong: this must not go on for ever
    ready.clock["t"] += 100000
    with pytest.raises(NeedsAttention) as stuck:
        ready.verify_pin(PIN)
    assert "even after it tried again" in str(stuck.value) and ready.status()["attention"] is True and ready.status()["auto_resume_in"] == 0


def test_a_good_unlock_gives_the_automatic_restarts_back(ready):
    stop_it(ready)
    ready.clock["t"] += 601
    ready.verify_pin(PIN)
    ready.note_result(True)
    assert ready.status()["auto_resumes_left"] == 3


def test_the_owner_starting_it_gives_the_automatic_restarts_back_too(ready):
    for _ in range(3):
        stop_it(ready)
        ready.clock["t"] += 601
        ready.verify_pin(PIN)
    stop_it(ready)
    ready.clear_attention()
    assert ready.status()["auto_resumes_left"] == 3 and ready.status()["attention"] is False


def test_the_helper_logs_that_it_restarted_itself(ready):
    agent, _, _, log = make_agent(ready, unlocks=False)
    stop_it(ready)
    ready.clock["t"] += 601
    ready.status()
    assert any("started trying again by itself (1 of 3)" in line for line in agent.recent)


def test_a_keyboard_failure_is_reported_and_counted(ready):
    agent, _, _, _ = make_agent(ready, code=3)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert not answer["ok"] and answer["code"] == "still_locked"


def test_after_three_wrong_pins_the_right_one_is_refused_too(ready):
    agent, keys, _, _ = make_agent(ready)
    for guess in ("000000", "111111", "222222"):
        agent.handle({"cmd": "unlock", "pin": guess})
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert answer["code"] == "locked_out" and keys.requests == []


def test_two_unlocks_at_once_do_not_type_twice(ready):
    gate, release = threading.Event(), threading.Event()
    seen = []

    def slow(request):
        seen.append(request)
        gate.set()
        release.wait(2)
        return 0

    agent = Agent(ready, locked=lambda: False if seen else True, type_it=slow, wait_seconds=0.2, sleep=lambda s: None)
    first = threading.Thread(target=lambda: agent.handle({"cmd": "unlock", "pin": PIN}))
    first.start()
    gate.wait(2)
    second = agent.handle({"cmd": "unlock", "pin": PIN})
    release.set()
    first.join(3)
    assert second["code"] == "busy" and len(seen) == 1


def test_the_app_can_switch_it_off_and_remove_it_but_never_read_the_sign_in(ready):
    agent, *_ = make_agent(ready)
    assert agent.handle({"cmd": "disable"})["ok"] and ready.status()["enabled"] is False
    assert agent.handle({"cmd": "forget"})["ok"] and not ready.configured()
    for asking in ({"cmd": "reveal"}, {"cmd": "secret"}, {"cmd": "get"}, {}):
        assert agent.handle(asking)["ok"] is False


def test_the_helper_log_never_holds_the_pin_or_the_sign_in(ready):
    agent, _, _, log = make_agent(ready)
    agent.handle({"cmd": "unlock", "pin": "000000"})
    agent.handle({"cmd": "unlock", "pin": PIN})
    text = "\n".join(log)
    assert log and PIN not in text and WINDOWS_PIN not in text and "000000" not in text


def test_an_unexpected_error_says_nothing_about_the_secret(ready):
    def explode(request):
        raise RuntimeError(f"boom {request['secret']}")

    agent = Agent(ready, locked=lambda: True, type_it=explode, log=lambda line: None, wait_seconds=0.1, sleep=lambda s: None)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert not answer["ok"] and WINDOWS_PIN not in json.dumps(answer)


# -- the app talks to the helper over a connection that stays on this PC ------------------------------

@pytest.fixture
def helper(ready):
    agent, keys, state, log = make_agent(ready)
    server = serve(agent, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield AgentClient(server.server_address[1], timeout=5), keys, state, ready
    server.shutdown()
    server.server_close()


def test_the_server_only_listens_on_this_pc(helper):
    client, *_ = helper
    import socket
    assert socket.getaddrinfo("127.0.0.1", client.port)  # (it is bound to 127.0.0.1: see serve())
    from handoff.unlock import agent as module
    assert 'allow_reuse_address' in vars(module._Server) and '("127.0.0.1", port)' in Path(module.__file__).read_text(encoding="utf-8")


def test_the_client_unlocks_and_reports_status(helper):
    client, keys, state, _ = helper
    info = client.status()
    assert info["running"] and info["configured"] and info["locked"] is True
    assert "unlocked" in client.unlock(PIN).lower()
    assert state["locked"] is False


def test_the_client_turns_each_refusal_into_its_own_error(helper):
    client, keys, state, store = helper
    with pytest.raises(WrongPin) as wrong:
        client.unlock("000000")
    assert wrong.value.extra["tries_left"] == 2 and "2 more" in str(wrong.value)
    with pytest.raises(WrongPin):
        client.check_pin("111111")
    client.check_pin(PIN)  # the right one is fine, and unlocks nothing
    assert keys.requests == [] and state["locked"] is True
    state["locked"] = False
    with pytest.raises(NotLocked):
        client.unlock(PIN)


def test_a_helper_that_is_not_there_is_reported_not_crashed(tmp_path):
    client = AgentClient(1, timeout=1)  # nothing listens on port 1
    info = client.status()
    assert info == {"running": False, "configured": False, "enabled": False, "kind": "", "attention": False, "locked": None}
    with pytest.raises(AgentDown) as down:
        client.unlock(PIN)
    assert "setup install" in str(down.value)


def test_a_garbage_request_gets_a_polite_refusal(helper):
    client, *_ = helper
    import socket
    with socket.create_connection(("127.0.0.1", client.port), timeout=3) as conn:
        conn.sendall(b"this is not json\n")
        assert json.loads(conn.makefile().readline())["ok"] is False
    with socket.create_connection(("127.0.0.1", client.port), timeout=3) as conn:
        conn.sendall(b"x" * 9000 + b"\n")  # too big: dropped, no answer needed
        assert conn.recv(10) in (b"", )


# -- asking in words, approving with the PIN -----------------------------------------------------------

class FakeUnlocker:
    """What the job manager uses instead of the real helper."""

    def __init__(self, locked=True, configured=True, enabled=True, attention=False, locked_for=0):
        self.info = {"running": True, "configured": configured, "enabled": enabled, "attention": attention, "locked": locked, "locked_for": locked_for, "kind": "pin"}
        self.checked, self.unlocked, self.fail = [], [], None

    def status(self):
        return self.info

    def check_pin(self, pin):
        self.checked.append(pin)
        if pin != PIN:
            raise WrongPin(2)

    def unlock(self, pin):
        self.unlocked.append(pin)
        if self.fail:
            raise self.fail
        return "The PC is unlocked."


@pytest.fixture
def manager(settings):
    m = JobManager(settings, DemoPlanner(), unlocker=FakeUnlocker())
    return m


def submit(manager, text="unlock my PC"):
    job = manager.submit(text)
    manager.join_background()
    return manager.get(job.id)


@pytest.mark.parametrize("text", ["unlock my pc", "Unlock my PC", "please unlock the computer", "can you unlock my laptop", "unlock the screen",
                                  "unlock my lock screen", "unlock windows", "i want to unlock this pc.", "unlock my machine"])
def test_these_requests_are_about_unlocking_the_pc(text):
    plan = quick_for(text, None)
    assert plan is not None and plan.steps[0].content == "unlock", text


@pytest.mark.parametrize("text", ["unlock the file", "how do I unlock my pc", "what does unlock do", "unlock readme.md", "lock my pc"])
def test_these_are_not(text):
    plan = quick_for(text, None)
    assert plan is None or not plan.steps or plan.steps[0].content != "unlock", text


def test_the_request_waits_for_approval_and_asks_for_the_pin(manager):
    job = submit(manager)
    assert job.status is Status.AWAITING
    data = job.to_dict()
    assert data["steps"][0]["needs_pin"] is True and "unlock" in data["steps"][0]["detail"].lower() or "sign-in" in data["steps"][0]["detail"].lower()
    assert "unlock PIN" in data["steps"][0]["warning"]


def test_it_always_asks_even_if_config_says_power_needs_no_approval(settings):
    settings.approval.require_for = ["write"]
    job = submit(JobManager(settings, DemoPlanner(), unlocker=FakeUnlocker()))
    assert job.status is Status.AWAITING


def test_approving_with_the_right_pin_unlocks_and_the_pin_is_used_once(manager):
    job = submit(manager)
    done = manager.approve(job.id, PIN)
    assert done.status is Status.DONE and "unlocked" in done.outputs[0]
    assert manager._unlocker.unlocked == [PIN] and manager._unlock_pins == {}


def test_approving_without_a_pin_is_refused_and_the_request_keeps_waiting(manager):
    job = submit(manager)
    with pytest.raises(UnlockError) as missing:
        manager.approve(job.id, None)
    assert missing.value.code == "pin_required"
    assert manager.get(job.id).status is Status.AWAITING and manager._unlocker.unlocked == []


def test_a_wrong_pin_is_refused_and_the_request_keeps_waiting_for_another_try(manager):
    job = submit(manager)
    with pytest.raises(WrongPin):
        manager.approve(job.id, "000000")
    assert manager.get(job.id).status is Status.AWAITING and manager._unlocker.unlocked == []
    assert manager.approve(job.id, PIN).status is Status.DONE


def test_a_failure_while_typing_is_shown_as_the_helper_said_it(manager):
    manager._unlocker.fail = StillLocked("I typed the sign-in, but the PC is still locked.")
    job = submit(manager)
    failed = manager.approve(job.id, PIN)
    assert failed.status is Status.FAILED and failed.error == "I typed the sign-in, but the PC is still locked."
    assert manager._unlock_pins == {}


def test_rejecting_never_touches_the_helper(manager):
    job = submit(manager)
    manager.reject(job.id)
    assert manager._unlocker.checked == [] and manager._unlocker.unlocked == []


@pytest.mark.parametrize("info, words", [
    (dict(configured=False), "no saved sign-in"),
    (dict(enabled=False), "switched off"),
    (dict(attention=True), "stopped itself"),
    (dict(locked_for=240), "paused"),
    (dict(locked=False), "isn't locked"),
])
def test_a_request_that_cannot_work_says_why_at_once(settings, info, words):
    job = submit(JobManager(settings, DemoPlanner(), unlocker=FakeUnlocker(**info)))
    assert job.status is Status.FAILED and words in job.error


def test_when_the_helper_is_not_running_the_request_says_how_to_set_it_up(settings):
    job = submit(JobManager(settings, DemoPlanner(), unlocker=AgentClient(1, timeout=1)))
    assert job.status is Status.FAILED and "setup install" in job.error


def test_the_pin_never_reaches_the_audit_log_the_job_or_its_json(settings):
    m = JobManager(settings, DemoPlanner(), unlocker=FakeUnlocker())
    job = submit(m)
    m.approve(job.id, PIN)
    everything = json.dumps(m.get(job.id).to_dict()) + json.dumps(m._audit.read())
    assert PIN not in everything and WINDOWS_PIN not in everything
    events = [e["event"] for e in m._audit.read()]
    assert "unlocked" in events and "approved" in events


def test_a_failed_unlock_is_in_the_log_with_the_reason_but_not_the_pin(settings):
    m = JobManager(settings, DemoPlanner(), unlocker=FakeUnlocker())
    m._unlocker.fail = StillLocked("nope")
    job = submit(m)
    m.approve(job.id, PIN)
    entry = [e for e in m._audit.read() if e["event"] == "unlock_failed"]
    assert entry and entry[0]["reason"] == "still_locked" and PIN not in json.dumps(m._audit.read())


# -- over the phone's own connection --------------------------------------------------------------------

@pytest.fixture
def client(settings):
    app = create_app(settings)
    app.state.manager._unlocker = FakeUnlocker()
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    c.app = app
    return c


def test_the_phone_approves_with_the_pin_in_the_request_body(client):
    job = client.post("/api/jobs", json={"prompt": "unlock my PC"}).json()
    client.app.state.manager.join_background()
    res = client.post(f"/api/jobs/{job['id']}/approve", json={"pin": PIN})
    assert res.status_code == 200 and res.json()["status"] == "done"
    assert PIN not in client.get("/api/jobs").text and PIN not in client.get("/api/audit").text


def test_a_missing_pin_is_a_422_and_a_wrong_one_a_403_with_what_to_say(client):
    job = client.post("/api/jobs", json={"prompt": "unlock my PC"}).json()
    client.app.state.manager.join_background()
    missing = client.post(f"/api/jobs/{job['id']}/approve")
    assert missing.status_code == 422 and missing.json()["detail"]["code"] == "pin_required"
    wrong = client.post(f"/api/jobs/{job['id']}/approve", json={"pin": "000000"})
    assert wrong.status_code == 403 and wrong.json()["detail"]["code"] == "wrong_pin" and "attempt" in wrong.json()["detail"]["message"]
    assert client.get("/api/jobs").json()[0]["status"] == "awaiting_approval"


def test_other_requests_are_approved_exactly_as_before(client, settings):
    client.post("/api/jobs", json={"prompt": "lock my pc"})
    client.app.state.manager.join_background()
    jobs = client.get("/api/jobs").json()
    assert jobs[0]["status"] == "awaiting_approval"
    assert "needs_pin" not in jobs[0]["steps"][0]


# -- the sign-in is typed with real key events, planned here -------------------------------------------------

def test_every_character_becomes_a_down_and_an_up_whatever_the_keyboard_layout():
    events = typer.plan_text("aB1!é")
    assert len(events) == 10
    assert [e[0] for e in events] == ["unicode_down", "unicode_up"] * 5
    assert [chr(events[i][1]) for i in range(0, 10, 2)] == list("aB1!é")


def test_a_character_outside_the_basic_plane_is_sent_as_two_units():
    events = typer.plan_text("😀")
    assert len(events) == 4 and events[0][1] == 0xD83D and events[2][1] == 0xDE00


def test_enter_is_a_real_key_press():
    assert typer.plan_enter() == [("vk_down", 0x0D), ("vk_up", 0x0D)]


def test_the_helper_refuses_to_type_nothing(monkeypatch):
    monkeypatch.setattr(typer, "_send", lambda *a, **k: pytest.fail("typed something"))
    assert typer.run({"secret": ""}) == 2 and typer.run({}) == 2


def test_the_helper_wakes_the_lock_screen_then_types_then_presses_enter(monkeypatch):
    log = []
    monkeypatch.setattr(typer, "_click_middle", lambda: log.append("click"))
    monkeypatch.setattr(typer, "_send", lambda events, delay: log.append(("keys", len(events))))
    monkeypatch.setattr(typer.time, "sleep", lambda s: None)
    assert typer.run({"secret": "1234"}) == 0
    assert log == ["click", ("keys", 8), ("keys", 2)]
    log.clear()
    typer.run({"secret": "1234", "wake": False, "submit": False})
    assert log == [("keys", 8)]


def test_a_keyboard_error_is_an_exit_code_not_a_crash(monkeypatch):
    monkeypatch.setattr(typer, "_click_middle", lambda: None)
    monkeypatch.setattr(typer.time, "sleep", lambda s: None)

    def fail(events, delay):
        raise OSError("blocked")

    monkeypatch.setattr(typer, "_send", fail)
    assert typer.run({"secret": "1234"}) == 3


# -- is the PC locked? -------------------------------------------------------------------------------------

def test_the_pc_is_locked_when_the_sign_in_screen_is_running_in_the_console_session():
    procs = [("explorer.exe", 10), ("LogonUI.exe", 20)]
    sessions = {10: 1, 20: 1}
    assert lockstate.logonui_in_console(lambda: procs, sessions.get, lambda: 1) is True
    assert lockstate.logonui_in_console(lambda: [("explorer.exe", 10)], sessions.get, lambda: 1) is False


def test_a_sign_in_screen_in_another_session_does_not_count():
    assert lockstate.logonui_in_console(lambda: [("LogonUI.exe", 20)], {20: 0}.get, lambda: 1) is False


def test_when_it_cannot_be_told_it_says_so():
    assert lockstate.logonui_in_console(lambda: [], {}.get, lambda: None) is None

    def boom():
        raise PermissionError

    assert lockstate.logonui_in_console(boom, {}.get, lambda: 1) is None


# -- setting it up ----------------------------------------------------------------------------------------------

class Calls:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")


def answers(*values):
    it = iter(values)
    return lambda prompt="": next(it)


def run_install(tmp_path, typed, secrets, pin_default=True, check=lambda u, p: True):
    run, said = Calls(), []
    code = setup.install(ask=answers(*secrets), say=said.append, folder=tmp_path / "pd", run=run, port=8769, check_password=check, pin_default=pin_default,
                         read=answers(*typed))
    return code, run, said


def test_install_saves_the_sign_in_locks_the_folder_and_starts_the_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.dpapi, "protect", protect)
    monkeypatch.setattr(setup.dpapi, "unprotect", protect)
    code, run, said = run_install(tmp_path, ["YES", "y"], [WINDOWS_PIN, WINDOWS_PIN, PIN, PIN])
    assert code == 0
    saved = UnlockStore(tmp_path / "pd" / "unlock.json", protect, protect)
    assert saved.configured() and saved.reveal() == ("pin", WINDOWS_PIN)
    flat = [" ".join(map(str, c)) for c in run.calls]
    assert any("icacls" in c and "S-1-5-18" in c and "S-1-5-32-544" in c and "/inheritance:r" in c for c in flat)  # system and administrators only
    task = [c for c in run.calls if c[0] == "schtasks" and "/Create" in c][0]
    xml = (tmp_path / "pd" / "task.xml").read_text(encoding="utf-16")
    assert "/XML" in task and "S-1-5-18" in xml and "<BootTrigger>" in xml and "handoff.unlock.agent" in xml  # the system, at boot, running the helper
    assert "<DisallowStartIfOnBatteries>false" in xml and "<StopIfGoingOnBatteries>false" in xml  # and not switched off when the laptop is unplugged
    assert not any(WINDOWS_PIN in c or PIN in c for c in flat)  # nothing secret on any command line
    assert not any(WINDOWS_PIN in line or PIN in line for line in said)


def test_install_does_nothing_unless_told_yes(tmp_path):
    code, run, _ = run_install(tmp_path, ["no"], [])
    assert code == 1 and run.calls == [] and not (tmp_path / "pd" / "unlock.json").exists()


def test_install_stops_when_the_two_entries_differ(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.dpapi, "protect", protect)
    code, run, said = run_install(tmp_path, ["YES", "y"], [WINDOWS_PIN, "9999"])
    assert code == 1 and "didn't match" in said[-1] and run.calls == []


def test_install_refuses_a_password_windows_says_is_wrong(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.dpapi, "protect", protect)
    code, run, said = run_install(tmp_path, ["YES", "n"], ["hunter22", "hunter22"], pin_default=False, check=lambda u, p: False)
    assert code == 1 and "isn't right" in said[-1] and not (tmp_path / "pd" / "unlock.json").exists()


def test_install_refuses_an_unlock_pin_equal_to_the_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.dpapi, "protect", protect)
    code, run, said = run_install(tmp_path, ["YES", "y"], ["123456", "123456", "123456", "123456"])
    assert code == 1 and "different" in said[-1]


def test_the_task_runs_the_helper_as_the_system_from_the_project_folder():
    command = setup.task_command(r"C:\x\python.exe", Path(r"C:\proj"), 8769, Path(r"C:\data\agent.out"))
    assert command == r'cmd.exe /c cd /d "C:\proj" && "C:\x\python.exe" -m handoff.unlock.agent --port 8769 >> "C:\data\agent.out" 2>&1'  # what it prints if it dies is kept


def test_uninstall_removes_the_task(tmp_path):
    run = Calls()
    setup.remove_task(run)
    assert [c[1] for c in run.calls] == ["/End", "/Delete"]


def test_the_choice_of_pin_or_password_follows_how_windows_last_signed_in():
    assert setup.sign_in_looks_like_pin(lambda: "{d6886603-9d2f-4eb2-b667-1971041fa96b}") is True
    assert setup.sign_in_looks_like_pin(lambda: "{60B78E88-EAD8-445C-9CFD-0B87F74EA6CD}") is False


# -- the text the person reads ----------------------------------------------------------------------------------

def test_unlocking_has_its_own_plain_wording():
    action = power.ACTIONS["unlock"]
    assert action.summary == "Unlock this PC" and not action.ends_connection
    assert "PIN" in action.warning


# -- the PC's own page ---------------------------------------------------------------------------------------------

@pytest.fixture
def admin(project, monkeypatch):
    from handoff.admin import create_admin_app
    from handoff.config import load_settings

    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    phone_app = create_app(settings, DemoPlanner())
    phone_app.state.manager._unlocker = FakeAdminUnlocker()
    client = TestClient(create_admin_app(settings, phone_app.state.accounts, phone_app.state.manager), base_url="http://127.0.0.1:8766")
    headers = {"X-Admin": "1", "host": "127.0.0.1:8766"}
    assert client.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=headers).status_code == 200
    return client, headers, phone_app.state.manager._unlocker, phone_app.state.manager


class FakeAdminUnlocker(FakeUnlocker):
    def __init__(self):
        super().__init__()
        self.off = self.gone = False

    def disable(self):
        self.off = True

    def forget(self):
        self.gone = True


def test_the_pc_page_shows_whether_unlocking_is_on_but_never_the_sign_in(admin):
    client, headers, helper, _ = admin
    got = client.get("/admin/unlock", headers=headers).json()
    assert got == {"running": True, "configured": True, "enabled": True, "kind": "pin", "attention": False, "locked_for": 0, "locked": True, "auto_resume_in": None, "auto_resumes_left": None}


def test_the_pc_page_can_switch_it_off_and_remove_it(admin):
    client, headers, helper, manager = admin
    assert client.post("/admin/unlock/disable", headers=headers).json() == {"ok": True} and helper.off
    assert client.post("/admin/unlock/forget", headers=headers).json() == {"ok": True} and helper.gone
    events = [e["event"] for e in manager._audit.read()]
    assert "unlock_off" in events and "unlock_removed" in events


def test_the_pc_page_cannot_take_a_pin_or_a_sign_in(admin):
    client, headers, *_ = admin
    for path in ("/admin/unlock/install", "/admin/unlock/pin", "/admin/unlock/secret"):  # (the sign-in is only ever typed in the setup window)
        assert client.post(path, headers=headers, json={"pin": PIN}).status_code in (404, 405)


def test_the_unlock_endpoints_need_the_owner_signed_in(project, monkeypatch):
    from handoff.admin import create_admin_app
    from handoff.config import load_settings

    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    phone_app = create_app(settings, DemoPlanner())
    stranger = TestClient(create_admin_app(settings, phone_app.state.accounts, phone_app.state.manager), base_url="http://127.0.0.1:8766")
    headers = {"X-Admin": "1", "host": "127.0.0.1:8766"}
    assert stranger.get("/admin/unlock", headers=headers).status_code == 401
    assert stranger.post("/admin/unlock/disable", headers=headers).status_code == 401
    assert stranger.post("/admin/unlock/forget", headers=headers).status_code == 401


def test_a_windows_refusal_is_told_to_the_person_with_the_step_that_failed(ready):
    def refuse(request):
        raise OSError("Couldn't point the token at the screen's session (Windows error 1314).")

    log = []
    agent = Agent(ready, locked=lambda: True, type_it=refuse, log=log.append, wait_seconds=0.1, sleep=lambda s: None)
    answer = agent.handle({"cmd": "unlock", "pin": PIN})
    assert answer["code"] == "still_locked" and "1314" in answer["message"] and WINDOWS_PIN not in json.dumps(answer) + " ".join(log)


def test_an_error_in_the_helper_is_logged_by_place_not_by_message(ready):
    def explode(request):
        raise RuntimeError(f"boom {request['secret']}")

    log = []
    agent = Agent(ready, locked=lambda: True, type_it=explode, log=log.append, wait_seconds=0.1, sleep=lambda s: None)
    agent.handle({"cmd": "unlock", "pin": PIN})
    text = " ".join(log)
    assert "RuntimeError" in text and "test_unlock.py" in text and WINDOWS_PIN not in text


def test_the_windows_calls_have_their_argument_types_spelled_out():
    from handoff.unlock import winsession

    api = winsession._api()
    for name in ("OpenProcessToken", "DuplicateTokenEx", "SetTokenInformation", "CreateProcessAsUserW", "CreatePipe", "WriteFile", "CloseHandle", "GetCurrentProcess"):
        fn = getattr(api, name)
        assert fn.restype is not None and (name == "GetCurrentProcess" or fn.argtypes), name
    handle = api.GetCurrentProcess()
    assert handle in (-1, 0xFFFFFFFFFFFFFFFF, 2 ** 64 - 1)  # the pseudo-handle for this process fits through the prototypes without an overflow


def test_a_normal_user_gets_the_clear_privilege_message_not_a_crash():
    from handoff.unlock import winsession

    with pytest.raises(OSError) as why:
        winsession.run_on(winsession.WINLOGON, {"secret": "x"})
    assert "must run as the system" in str(why.value) or "no one at the PC" in str(why.value)


def test_the_key_structures_are_built_once_so_the_pointer_types_agree():
    assert typer._structures() is typer._structures()


def test_windows_accepts_the_arguments_for_sending_input():
    """Sends zero events, so nothing is typed or clicked: this only proves the call is made with types Windows and ctypes agree on."""
    import ctypes

    _, INPUT, _, _ = typer._structures()
    user32 = typer._user32()
    assert user32.SendInput(0, ctypes.byref(INPUT()), ctypes.sizeof(INPUT)) == 0
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_the_helper_can_say_where_it_is_standing():
    text = typer.diagnose()
    assert "desktop" in text and "session" in text


def test_every_input_path_builds_its_structures_through_the_same_door():
    source = Path(typer.__file__).read_text(encoding="utf-8")
    assert source.count("_structures()") >= 3 and "windll.user32" not in source  # no call without argument types


# -- getting the sign-in box showing, then typing in it -------------------------------------------------------------

class Desktops:
    """A stand-in for the two helpers: which desktop is getting the keyboard changes as the lock screen is woken."""

    def __init__(self, sequence, type_code=0, wake_code=0):
        self.sequence, self.calls, self.type_code, self.wake_code = list(sequence), [], type_code, wake_code

    def __call__(self, desktop, request, timeout=25):
        from handoff.unlock.winsession import Result

        self.calls.append((desktop.rsplit("\\", 1)[-1], dict(request)))
        if request.get("probe"):
            return Result(0, self.sequence.pop(0) if len(self.sequence) > 1 else self.sequence[0])
        if request.get("wake_only"):
            return Result(self.wake_code, "" if self.wake_code == 0 else "wake failed: Windows error 5")
        return Result(self.type_code, "" if self.type_code == 0 else "typing failed: Windows error 5")


def go(desktops, **kw):
    from handoff.unlock import winsession

    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds

    return winsession.type_at_lock_screen({"secret": WINDOWS_PIN, "submit": True}, run=desktops, sleep=sleep, clock=lambda: clock["t"], **kw)


def test_when_the_sign_in_box_is_already_up_it_types_there_without_waking_anything():
    desktops = Desktops(["Winlogon"])
    assert go(desktops).code == 0
    assert [(d, "probe" in r or "wake_only" in r or "secret" in r) for d, r in desktops.calls] == [("Winlogon", True), ("Winlogon", True)]
    typed = desktops.calls[-1][1]
    assert typed["secret"] == WINDOWS_PIN and typed["wake"] is False  # the typing step does not click again


def test_the_picture_and_clock_lock_screen_is_woken_from_the_normal_desktop_first():
    desktops = Desktops(["Default", "Default", "Winlogon"])
    result = go(desktops)
    assert result.code == 0
    steps = [(d, "probe" if r.get("probe") else "wake" if r.get("wake_only") else "type") for d, r in desktops.calls]
    assert steps == [("Winlogon", "probe"), ("Default", "wake"), ("Winlogon", "probe"), ("Default", "wake"), ("Winlogon", "probe"), ("Winlogon", "type")]


def test_it_never_types_while_the_keyboard_is_on_the_normal_desktop():
    desktops = Desktops(["Default"])
    result = go(desktops)
    assert result.code == 4 and "never appeared" in result.output
    assert not any("secret" in r for _, r in desktops.calls)  # nothing typed into whatever was on screen
    assert sum(1 for _, r in desktops.calls if r.get("wake_only")) == 3  # and it woke it a few times, not for ever


def test_a_failed_typing_step_reports_what_it_saw_on_the_way():
    desktops = Desktops(["Default", "Winlogon"], type_code=3)
    result = go(desktops)
    assert result.code == 3 and "keyboard is on 'Default'" in result.output and "Windows error 5" in result.output


def test_a_failed_wake_is_noted_and_tried_again():
    desktops = Desktops(["Default", "Default", "Default", "Default"], wake_code=3)
    result = go(desktops)
    assert result.code == 4 and "waking failed" in result.output


def test_the_helper_can_say_which_desktop_gets_the_keyboard(capsys):
    assert typer.run({"probe": True}) == 0
    assert capsys.readouterr().out.strip() in ("Default", "Winlogon", "")


def test_a_probe_types_nothing_and_a_wake_only_clicks_nothing_else(monkeypatch):
    monkeypatch.setattr(typer, "_send", lambda *a, **k: pytest.fail("typed something"))
    log = []
    monkeypatch.setattr(typer, "_click_middle", lambda: log.append("click"))
    assert typer.run({"wake_only": True}) == 0 and log == ["click"]


def test_repair_waits_for_the_helper_to_come_up_and_gives_up_politely():
    seen = iter([{}, {}, {"running": True}])
    assert setup.wait_until_running(ask=lambda: next(seen), sleep=lambda s: None) is True
    assert setup.wait_until_running(seconds=3, ask=lambda: {}, sleep=lambda s: None) is False


def test_when_the_helper_will_not_start_the_person_is_shown_what_windows_and_the_helper_say(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "data_dir", lambda: tmp_path)
    (tmp_path / "agent.out").write_text("Traceback...\nModuleNotFoundError: No module named 'psutil'\n", encoding="utf-8")
    (tmp_path / "unlock.log").write_text("2026-11-12 00:00:00 unlock helper starting\n", encoding="utf-8")

    def schtasks(args, **kw):
        return subprocess.CompletedProcess(args, 0, "Status: Ready\nLast Result: 1\nLast Run Time: 12/11/2026 00:00:00\nIrrelevant: x\n", "")

    said = []
    setup.explain_not_running(said.append, run=schtasks)
    text = "\n".join(said)
    assert "Last Result: 1" in text and "ModuleNotFoundError" in text and "unlock helper starting" in text and "Irrelevant" not in text


def test_the_status_command_does_not_crash_when_the_folder_is_locked_to_administrators(tmp_path, monkeypatch, capsys):
    class Locked:
        def exists(self):
            raise PermissionError

        def __truediv__(self, other):
            return self

    monkeypatch.setattr(setup, "data_dir", lambda: Locked())
    monkeypatch.setattr(setup.AgentClient, "status", lambda self: {"running": False, "configured": False})
    monkeypatch.setattr(setup.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1))
    assert setup.status() == 1
    assert "only administrators can look inside" in capsys.readouterr().out


def test_the_task_is_described_in_full_so_a_laptop_on_battery_still_runs_the_helper():
    import xml.etree.ElementTree as ET

    text = setup.task_xml("C:/x/python.exe", Path("C:/proj"), 8769, Path("C:/d/agent.out"))
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(text.split("?>", 1)[1])
    value = lambda tag: root.find(f".//t:{tag}", ns).text  # noqa: E731
    assert value("DisallowStartIfOnBatteries") == "false" and value("StopIfGoingOnBatteries") == "false"
    assert value("ExecutionTimeLimit") == "PT0S"  # never stopped for running too long
    assert value("MultipleInstancesPolicy") == "IgnoreNew" and value("StartWhenAvailable") == "true"
    assert value("Interval") == "PT1M" and value("Count") == "999"  # started again a minute after it dies
    assert value("UserId") == "S-1-5-18" and root.find(".//t:BootTrigger", ns) is not None
    assert ">> " in value("Arguments") and " 2>&1" in value("Arguments") and "&&" in value("Arguments")  # the & and > survive being written into XML


def test_if_windows_refuses_the_full_description_a_plain_task_is_made_and_its_battery_settings_fixed(tmp_path):
    calls = []

    def run(args, **kw):
        calls.append(args)
        if "/XML" in args:
            raise subprocess.CalledProcessError(1, args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    setup.create_task("py", Path("C:/p"), 8769, run, out=tmp_path / "o", xml_path=tmp_path / "t.xml")
    assert any("/SC" in c and "ONSTART" in c for c in calls)
    assert any(c[0] == "powershell" and "AllowStartIfOnBatteries" in " ".join(c) and "DontStopIfGoingOnBatteries" in " ".join(c) for c in calls)
    assert calls[-1][1] == "/Run"


def test_the_recent_helper_log_can_be_read_through_status_and_never_holds_a_secret(ready):
    agent, keys, state, log = make_agent(ready)
    agent.handle({"cmd": "unlock", "pin": "000000"})
    agent.handle({"cmd": "unlock", "pin": PIN})
    shown = agent.handle({"cmd": "status"})
    text = "\n".join(shown["log"])
    assert "typing the saved pin" in text and "the PC is unlocked" in text
    assert PIN not in text and WINDOWS_PIN not in text and "000000" not in text


def test_the_status_command_prints_the_recent_log(monkeypatch, capsys):
    monkeypatch.setattr(setup.AgentClient, "status", lambda self: {"running": True, "configured": True, "enabled": True, "kind": "pin", "locked": False,
                                                                  "attention": True, "locked_for": 0, "log": ["00:01:02 typed the sign-in but the PC is still locked"]})
    assert setup.status() == 0
    out = capsys.readouterr().out
    assert "still locked" in out and "STOPPED itself" in out


# -- fewer trips to an administrator window ---------------------------------------------------------------------------

def test_the_helper_leaves_for_a_restart_when_its_own_files_change():
    from handoff.unlock import agent as module

    states = iter([{"agent.py": 1.0, "store.py": 1.0}, {"agent.py": 1.0, "store.py": 1.0}, {"agent.py": 2.0, "store.py": 1.0}])
    left, log = [], []
    module.restart_when_updated(log.append, stamps=lambda: next(states), leave=left.append, sleep=lambda s: None, rounds=5)
    assert left == [3] and "agent.py" in log[0] and "restarting" in log[0]


def test_it_does_not_leave_while_nothing_changes():
    from handoff.unlock import agent as module

    left = []
    module.restart_when_updated(lambda line: None, stamps=lambda: {"agent.py": 1.0}, leave=left.append, sleep=lambda s: None, rounds=4)
    assert left == []


def test_a_new_or_removed_file_counts_as_a_change():
    from handoff.unlock import agent as module

    assert module.changed_since({"a.py": 1.0}, {"a.py": 1.0, "b.py": 1.0}) == ["b.py"]
    assert module.changed_since({"a.py": 1.0, "b.py": 1.0}, {"a.py": 1.0}) == ["b.py"]


def test_the_real_helper_watches_its_own_package_files():
    from handoff.unlock import agent as module

    assert {"agent.py", "typer.py", "store.py", "winsession.py"} <= set(module.source_stamps())


def test_the_task_restarts_a_helper_that_leaves_with_a_failure_code():
    xml = setup.task_xml("py", Path("C:/p"), 8769, Path("C:/o"))
    assert "<RestartOnFailure>" in xml and "<Count>999</Count>" in xml


def test_the_owner_can_start_it_trying_again_from_the_pcs_page_after_it_stopped(ready):
    agent, keys, state, log = make_agent(ready)
    ready.note_result(False)
    ready.note_result(False)
    assert agent.handle({"cmd": "unlock", "pin": PIN})["code"] == "needs_attention"
    assert agent.handle({"cmd": "resume"})["ok"] is True
    assert agent.handle({"cmd": "unlock", "pin": PIN})["ok"] is True


def test_the_pc_page_has_a_start_trying_again_button_that_works(admin):
    client, headers, helper, manager = admin
    helper.resumed = False
    helper.resume = lambda: setattr(helper, "resumed", True)
    assert client.post("/admin/unlock/resume", headers=headers).json() == {"ok": True} and helper.resumed
    assert "unlock_resumed" in [e["event"] for e in manager._audit.read()]


def test_the_message_for_a_stopped_helper_points_at_the_button_not_a_console_command(settings):
    job = submit(JobManager(settings, DemoPlanner(), unlocker=FakeUnlocker(attention=True)))
    assert "Start trying again" in job.error and "python -m" not in job.error


# -- setting it up from the PC's page: one button, one window, no terminal ------------------------------------------------

from handoff.unlock import setup_gui  # noqa: E402


@pytest.mark.parametrize("args, words", [
    (("pin", "", "", "483920", "483920"), "Type your Windows Hello PIN"),
    (("password", "", "", "483920", "483920"), "Type your Windows password"),
    (("pin", "7391", "7392", "483920", "483920"), "don't match"),
    (("pin", "7391", "7391", "12345", "12345"), "6 to 32"),
    (("pin", "7391", "7391", "483920", "483921"), "don't match"),
    (("pin", "123456", "123456", "123456", "123456"), "different"),
])
def test_the_setup_form_says_what_is_wrong_in_plain_words(args, words):
    assert words in setup_gui.check_form(*args)


def test_a_good_form_has_nothing_wrong():
    assert setup_gui.check_form("pin", WINDOWS_PIN, WINDOWS_PIN, PIN, PIN) is None


_TK = {}


def _tk_root():
    """One hidden Tk root for the whole run (making and destroying several in one process is flaky on Windows); each test gets its own window on it."""
    import tkinter

    if "root" not in _TK:
        try:
            _TK["root"] = tkinter.Tk()
        except tkinter.TclError:
            pytest.skip("no display for a window")
        _TK["root"].withdraw()
    return _TK["root"]


class _Form(dict):
    pass


def make_form(**kw):
    import tkinter

    top = tkinter.Toplevel(_tk_root())
    top.withdraw()
    form = setup_gui.run(root=top, mainloop=False, default_pin=True, **kw)
    return top, form


def fill(form, secret=WINDOWS_PIN, again=None, pin=PIN, pin_again=None):
    f = form["fields"]
    f["secret"].set(secret); f["secret_again"].set(secret if again is None else again)
    f["pin"].set(pin); f["pin_again"].set(pin if pin_again is None else pin_again)


def test_the_setup_window_starts_on_the_hello_pin_and_hides_what_is_typed():
    root, form = make_form(perform=lambda *a: None)
    try:
        assert form["kind"].get() == "pin"
        assert len(form["boxes"]) == 4 and all(box.cget("show") == "•" for box in form["boxes"].values())
    finally:
        if root.winfo_exists():
            root.destroy()


def test_the_setup_window_checks_as_you_type_that_entries_match_and_the_pin_is_long_enough():
    root, form = make_form(perform=lambda *a: None)
    try:
        f, hints = form["fields"], form["hints"]
        f["secret"].set(WINDOWS_PIN); f["secret_again"].set("73")
        assert "don't match" in hints["secret"].cget("text")
        f["secret_again"].set(WINDOWS_PIN)
        assert "match" in hints["secret"].cget("text") and "don't" not in hints["secret"].cget("text")
        f["pin"].set("123")
        assert "3 characters" in hints["length"].cget("text") and "needs 6 to 32" in hints["length"].cget("text")
        f["pin"].set(PIN)
        assert "6 characters" in hints["length"].cget("text") and "needs" not in hints["length"].cget("text")
    finally:
        if root.winfo_exists():
            root.destroy()


def test_choosing_a_password_changes_what_the_second_step_asks_for():
    root, form = make_form(perform=lambda *a: None)
    try:
        form["pick"]("password")
        assert form["kind"].get() == "password"
        assert any("Your Windows password" == w.cget("text") for w in _all_labels(root))
        form["pick"]("pin")
        assert any("Your Windows Hello PIN" == w.cget("text") for w in _all_labels(root))
    finally:
        if root.winfo_exists():
            root.destroy()


def _all_canvases(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Canvas":
            found.append(child)
        found += _all_canvases(child)
    return found


def _all_labels(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label":
            found.append(child)
        found += _all_labels(child)
    return found


def test_the_setup_window_does_the_setup_with_what_was_typed_and_says_when_it_is_done():
    done = []
    root, form = make_form(perform=lambda kind, secret, pin: done.append((kind, secret, pin)))
    try:
        fill(form)
        form["submit"]()
        root.update()
        assert done == [("pin", WINDOWS_PIN, PIN)] and form["state"]["done"] is True
        drawn = " ".join(c.itemcget(i, "text") for c in _all_canvases(root) for i in c.find_all() if c.type(i) == "text")
        assert "Unlocking is set up" in drawn and "unlock my PC" in drawn
        assert form["state"]["close"].cget("text") == "Done"
    finally:
        if root.winfo_exists():
            root.destroy()


def test_the_setup_window_refuses_a_bad_form_without_doing_anything():
    done = []
    root, form = make_form(perform=lambda *a: done.append(a))
    try:
        fill(form, pin="123")
        form["submit"]()
        assert done == [] and "6 to 32" in form["message"].cget("text")
        fill(form, again="9999")
        form["submit"]()
        assert done == [] and "don't match" in form["message"].cget("text")
    finally:
        if root.winfo_exists():
            root.destroy()


def test_the_setup_window_shows_a_failure_and_lets_the_person_try_again():
    def fail(kind, secret, pin):
        raise RuntimeError("Windows refused the task")

    root, form = make_form(perform=fail)
    try:
        fill(form)
        form["submit"]()
        root.update()
        assert "didn't work" in form["message"].cget("text") and "Windows refused" in form["message"].cget("text")
        assert WINDOWS_PIN not in form["message"].cget("text") and not form["state"]["done"]
    finally:
        if root.winfo_exists():
            root.destroy()


def test_a_password_that_windows_rejects_is_not_saved():
    done = []
    root, form = make_form(perform=lambda *a: done.append(a), check_password=lambda user, password: False)
    try:
        form["kind"].set("password")
        fill(form, secret="hunter22")
        form["submit"]()
        assert done == [] and "isn't right" in form["message"].cget("text")
    finally:
        if root.winfo_exists():
            root.destroy()


def test_the_setup_steps_save_lock_down_and_start_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.dpapi, "protect", protect)
    monkeypatch.setattr(setup.dpapi, "unprotect", protect)
    run = Calls()
    setup.perform_install("pin", WINDOWS_PIN, PIN, folder=tmp_path / "pd", run=run, port=8769)
    assert UnlockStore(tmp_path / "pd" / "unlock.json", protect, protect).reveal() == ("pin", WINDOWS_PIN)
    names = [c[0] for c in run.calls]
    assert names.index("icacls") < names.index("schtasks")  # the folder is locked before the helper starts
    assert (tmp_path / "pd" / "task.xml").exists()


def test_the_page_button_opens_the_setup_window_with_administrator_rights(admin, monkeypatch):
    client, headers, helper, manager = admin
    helper.info.update(configured=False)
    asked = []
    monkeypatch.setattr(setup, "relaunch_elevated", lambda args, windowed=False: asked.append((args, windowed)) or True)
    assert client.post("/admin/unlock/setup", headers=headers).json() == {"ok": True}
    assert asked == [(["gui"], True)]  # a window, and only the window: no sign-in passes through this request
    assert "unlock_setup_opened" in [e["event"] for e in manager._audit.read()]


def test_the_page_button_says_so_when_windows_would_not_give_administrator_rights(admin, monkeypatch):
    client, headers, helper, _ = admin
    helper.info.update(configured=False)
    monkeypatch.setattr(setup, "relaunch_elevated", lambda args, windowed=False: False)
    res = client.post("/admin/unlock/setup", headers=headers)
    assert res.status_code == 409 and "administrator" in res.json()["detail"]


def test_it_will_not_open_the_setup_window_again_when_it_is_already_set_up(admin, monkeypatch):
    client, headers, *_ = admin
    monkeypatch.setattr(setup, "relaunch_elevated", lambda *a, **k: pytest.fail("opened a window"))
    assert client.post("/admin/unlock/setup", headers=headers).status_code == 409


def test_it_can_be_switched_back_on_from_the_pc_page_and_the_pin_is_still_needed(ready):
    agent, keys, state, _ = make_agent(ready)
    agent.handle({"cmd": "disable"})
    assert agent.handle({"cmd": "unlock", "pin": PIN})["code"] == "disabled"
    assert agent.handle({"cmd": "enable"})["ok"] is True
    assert agent.handle({"cmd": "unlock", "pin": "000000"})["code"] == "wrong_pin"
    assert agent.handle({"cmd": "unlock", "pin": PIN})["ok"] is True


def test_the_setup_window_command_asks_for_administrator_rights_first(monkeypatch):
    asked = []
    monkeypatch.setattr(setup, "is_admin", lambda: False)
    monkeypatch.setattr(setup, "relaunch_elevated", lambda args, windowed=False: asked.append((args, windowed)) or True)
    assert setup.main(["gui"]) == 0 and asked == [(["gui"], True)]


# -- why Windows said no to the administrator request

def test_a_policy_that_refuses_non_administrators_is_named_as_the_reason():
    why = setup.elevation_refusal_reason(read_policy=lambda: 0, groups=lambda: "Mandatory Label\Medium Mandatory Level   Label  S-1-16-8192")
    assert "isn't an administrator" in why and "IT team" in why and "setup status" in why


@pytest.mark.parametrize("mode,groups", [(1, ""), (3, ""), (None, ""), (0, "BUILTIN\Administrators  Group used for deny only  S-1-5-32-544")])
def test_no_reason_is_given_when_it_was_probably_a_declined_prompt(mode, groups):
    assert setup.elevation_refusal_reason(read_policy=lambda: mode, groups=lambda: groups) == ""


def test_the_pc_page_says_why_when_windows_refuses_the_setup_window(admin, monkeypatch):
    client, headers, helper, _ = admin
    helper.info.update(configured=False)
    monkeypatch.setattr(setup, "relaunch_elevated", lambda args, windowed=False: False)
    monkeypatch.setattr(setup, "elevation_refusal_reason", lambda *a, **k: "This Windows account isn't an administrator on this PC.")
    refused = client.post("/admin/unlock/setup", headers=headers)
    assert refused.status_code == 409 and "isn't an administrator" in refused.json()["detail"]
    monkeypatch.setattr(setup, "elevation_refusal_reason", lambda *a, **k: "")
    assert "declined or blocked" in client.post("/admin/unlock/setup", headers=headers).json()["detail"]
