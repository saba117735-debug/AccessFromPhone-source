"""The PC tells the phone BEFORE it goes away (sleep, restart, shutdown, the app closing, low battery), and answers the phone's heartbeat."""
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from handoff import pcwatch, power
from handoff.config import load_settings
from handoff.jobs import JobManager
from handoff.pcwatch import BatteryAlert, kind_for
from handoff.push import Message
from handoff.server import create_app

from test_push import ENDPOINT, Phone, make_notifier, vapid_pair


# -- battery ------------------------------------------------------------------------------

def battery(percent, plugged=False):
    return {"percent": percent, "plugged": plugged}


def test_a_low_battery_is_announced_once_then_again_at_critical():
    alert = BatteryAlert(low=15, critical=5)
    assert alert.check(battery(40)) is None
    assert alert.check(battery(16)) is None
    assert alert.check(battery(15)) == 15      # just got low
    assert alert.check(battery(14)) is None    # not again every minute
    assert alert.check(battery(9)) is None
    assert alert.check(battery(5)) == 5        # but critical is worth one more
    assert alert.check(battery(4)) is None


def test_charging_resets_it_so_the_next_discharge_warns_again():
    alert = BatteryAlert(low=15, critical=5)
    assert alert.check(battery(12)) == 12
    assert alert.check(battery(12, plugged=True)) is None
    assert alert.check(battery(12)) == 12      # unplugged and low again: a new warning
    alert.check(battery(30))                    # charged well past it (without being plugged in, e.g. a reading jump)
    assert alert.check(battery(14)) == 14


def test_a_desktop_pc_and_a_plugged_in_laptop_are_never_warned():
    alert = BatteryAlert()
    assert alert.check(None) is None
    assert alert.check(battery(3, plugged=True)) is None


def test_starting_already_critical_warns_straight_away():
    assert BatteryAlert(low=15, critical=5).check(battery(4)) == 4


# -- what Windows is saying ------------------------------------------------------------------

def test_windows_messages_become_the_right_notice():
    assert kind_for(pcwatch.WM_POWERBROADCAST, pcwatch.PBT_APMSUSPEND, 0) == "pc_sleep"
    assert kind_for(pcwatch.WM_POWERBROADCAST, 0x12, 0) is None                 # waking up: nothing to say
    assert kind_for(pcwatch.WM_ENDSESSION, 0, 0) is None                        # a shutdown that was cancelled
    assert kind_for(pcwatch.WM_ENDSESSION, 1, 0) == "pc_shutdown"
    assert kind_for(pcwatch.WM_ENDSESSION, 1, pcwatch.ENDSESSION_LOGOFF) == "pc_closing"   # signed out: the PC stays on
    assert kind_for(pcwatch.WM_ENDSESSION, 1, pcwatch.ENDSESSION_CLOSEAPP) == "pc_closing"
    assert kind_for(0x0001, 1, 0) is None


@pytest.mark.skipif(not power.supported(), reason="the power listener is Windows only")
def test_the_real_windows_listener_hears_a_sleep_and_a_shutdown():
    import ctypes

    heard = []
    watch = pcwatch.PowerWatch(heard.append)
    assert watch.start()
    send = ctypes.WinDLL("user32").SendMessageW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    try:
        send(watch.hwnd, pcwatch.WM_POWERBROADCAST, pcwatch.PBT_APMSUSPEND, 0)
        send(watch.hwnd, pcwatch.WM_ENDSESSION, 1, 0)
        assert send(watch.hwnd, pcwatch.WM_QUERYENDSESSION, 0, 0) == 1   # it never holds up a shutdown
    finally:
        watch.stop()
    assert heard == ["pc_sleep", "pc_shutdown"]


# -- the notice itself ----------------------------------------------------------------------------

def subscribed(tmp_path, **cfg):
    seen = []
    notifier, accounts, device = make_notifier(tmp_path, lambda r: seen.append(r) or httpx.Response(201), **cfg)
    phone = Phone()
    notifier.subscribe_web(device.id, ENDPOINT, phone.subscription["p256dh"], phone.subscription["auth"])
    return notifier, phone, seen


def test_each_notice_says_what_is_about_to_happen(tmp_path):
    notifier, phone, seen = subscribed(tmp_path)
    for kind, title in (("pc_sleep", "Your PC is going to sleep"), ("pc_restart", "Your PC is restarting"), ("pc_shutdown", "Your PC is shutting down"),
                        ("pc_closing", "The app is closing on your PC")):
        notifier._pc_last.clear()
        assert notifier.notify_pc(kind, wait=True)
        message = phone.read(seen[-1].content)
        assert message["title"] == title and message["kind"] == kind and message["job"] == ""
    notifier._pc_last.clear()
    assert notifier.notify_pc("pc_battery", percent=14, wait=True)
    assert phone.read(seen[-1].content)["title"] == "Your PC's battery is low (14%)"


def test_a_sleep_notice_expires_quickly_so_it_cannot_arrive_after_the_pc_has_woken(tmp_path):
    notifier, phone, seen = subscribed(tmp_path)
    notifier.notify_pc("pc_sleep", wait=True)
    assert seen[-1].headers["ttl"] == "120"
    notifier.notify_pc("pc_battery", percent=10, wait=True)
    assert seen[-1].headers["ttl"] == "1800"


def test_the_same_notice_is_not_sent_twice_in_a_row(tmp_path):
    notifier, phone, seen = subscribed(tmp_path)
    clock = [1000.0]
    notifier._clock = lambda: clock[0]
    assert notifier.notify_pc("pc_sleep", wait=True)
    clock[0] += 5
    assert not notifier.notify_pc("pc_sleep", wait=True)   # Windows can announce it more than once
    clock[0] += 60
    assert notifier.notify_pc("pc_sleep", wait=True)
    assert len(seen) == 2


def test_shutting_down_does_not_also_announce_the_app_closing(tmp_path):
    notifier, phone, seen = subscribed(tmp_path)
    assert notifier.notify_pc("pc_shutdown", wait=True)
    assert not notifier.notify_pc("pc_closing", wait=True)  # the app stops a moment later: one notice is enough
    assert len(seen) == 1
    notifier2, phone2, seen2 = subscribed(tmp_path / "other")
    assert notifier2.notify_pc("pc_closing", wait=True)     # closed on its own: that is news


def test_only_the_notices_that_are_switched_on_are_sent(tmp_path):
    notifier, phone, seen = subscribed(tmp_path, notify_on=["awaiting", "pc_sleep"])
    assert not notifier.notify_pc("pc_battery", percent=9, wait=True)
    assert not notifier.notify_pc("pc_shutdown", wait=True)
    assert notifier.notify_pc("pc_sleep", wait=True)
    assert len(seen) == 1
    assert not notifier.notify_pc("pc_unknown")


def test_nothing_is_sent_when_notifications_are_off(tmp_path):
    from handoff.config import PushConfig
    from handoff.db import Database
    from handoff.push import Notifier

    off = Notifier(PushConfig(enabled=False), Database(tmp_path / "x.db"))
    assert not off.notify_pc("pc_sleep", wait=True)
    assert pcwatch.start(off)() is None  # watching costs nothing, and stopping does nothing


def test_a_waiting_notice_holds_the_pc_up_only_a_moment(tmp_path):
    def slow(request):
        time.sleep(3)
        return httpx.Response(201)

    notifier, _, device = make_notifier(tmp_path, slow)
    phone = Phone()
    notifier.subscribe_web(device.id, ENDPOINT, phone.subscription["p256dh"], phone.subscription["auth"])
    began = time.monotonic()
    notifier.notify_pc("pc_sleep", wait=True, timeout=0.5)
    assert time.monotonic() - began < 1.5  # a slow push service must never keep a shutdown waiting


def test_the_battery_watcher_sends_the_notice(tmp_path):
    notifier, phone, seen = subscribed(tmp_path)
    readings = iter([battery(40), battery(14), battery(14), battery(4)])
    stop = pcwatch.start(notifier, battery_reader=lambda: next(readings, battery(4)), interval=0.05)
    deadline = time.time() + 3
    while len(seen) < 2 and time.time() < deadline:
        time.sleep(0.05)
    stop()
    notifier.wait()
    titles = [phone.read(r.content)["title"] for r in seen]
    assert titles[:2] == ["Your PC's battery is low (14%)", "Your PC's battery is low (4%)"]  # once when low, once more at critical


# -- a phone asks the PC to sleep: every phone hears it first --------------------------------------

@pytest.fixture
def fake_power(monkeypatch):
    calls = []
    monkeypatch.setattr(power, "supported", lambda: True)
    for key in power.DOERS:
        monkeypatch.setitem(power.DOERS, key, lambda key=key: calls.append(key))
    real = power.schedule
    monkeypatch.setattr(power, "schedule", lambda key, delay=0.05, doer=None: real(key, 0.05, doer))
    return calls


def test_a_power_request_notifies_before_it_happens(settings, fake_power):
    settings.power.remote_actions = ["lock", "sleep", "restart", "shutdown"]
    told = []
    manager = JobManager(settings)
    manager.on_pc_event = told.append
    for text, expected in (("put my pc to sleep", "pc_sleep"), ("restart my pc", "pc_restart"), ("shut down my pc", "pc_shutdown")):
        job = manager.submit(text)
        manager.join_background()
        manager.approve(job.id)
        assert told[-1] == expected
    count = len(told)
    job = manager.submit("lock my pc")
    manager.join_background()
    manager.approve(job.id)
    assert len(told) == count  # locking keeps the connection: nothing to warn about
    time.sleep(0.4)


def test_a_failing_notice_never_stops_the_power_request(settings, fake_power):
    manager = JobManager(settings)
    manager.on_pc_event = lambda kind: (_ for _ in ()).throw(RuntimeError("push is down"))
    job = manager.submit("put my pc to sleep")
    manager.join_background()
    assert manager.approve(job.id).status.value == "done"
    time.sleep(0.4)
    assert fake_power == ["sleep"]


# -- the phone's heartbeat and the app closing ------------------------------------------------------

@pytest.fixture
def paired(project, monkeypatch):
    private, _ = vapid_pair()
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    monkeypatch.setenv("HANDOFF_PUSH__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_PUSH__VAPID_PRIVATE_KEY", private)
    app = create_app(load_settings(project / "config.yaml"))
    app.state.accounts.setup_owner("Owner", "correct horse")
    client = TestClient(app)
    reply = client.post("/pair/claim", json={"code": app.state.accounts.new_pairing_code()[0], "name": "Phone"}).json()
    client.headers.update({"X-Token": reply["token"]})
    sent = []
    app.state.notifier.web._http = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(201)))
    return client, app, sent, reply["device"]["id"]


def test_the_heartbeat_answers_a_paired_phone_with_the_version(paired):
    client, app, sent, device_id = paired
    answer = client.get("/api/ping")
    assert answer.status_code == 200 and answer.json()["ok"] is True and answer.json()["v"]


def test_the_heartbeat_tells_a_removed_phone_it_is_no_longer_known(paired):
    client, app, sent, device_id = paired
    assert TestClient(app).get("/api/ping").status_code == 401         # never paired
    app.state.accounts.remove(device_id)
    assert client.get("/api/ping").status_code == 401                  # paired once, removed since: the page then shows "signed out"


def test_the_heartbeat_tells_a_blocked_phone_too(paired):
    client, app, sent, device_id = paired
    app.state.accounts.set_blocked(device_id, True)
    assert client.get("/api/ping").status_code == 401


def test_stopping_the_app_tells_the_phones_first(paired):
    client, app, sent, device_id = paired
    phone = Phone()
    assert client.post("/api/push/subscribe", json={"kind": "web", "endpoint": ENDPOINT, "keys": phone.subscription}).status_code == 200
    with TestClient(app):   # starting and stopping the server runs its start-up and shut-down steps
        pass
    kinds = [phone.read(r.content)["kind"] for r in sent]
    assert "pc_closing" in kinds


def test_plugging_in_and_unplugging_are_both_noticed_but_not_the_first_look():
    alert = pcwatch.BatteryAlert()
    assert alert.power_change({"percent": 8, "plugged": False}) is None
    assert alert.power_change({"percent": 8, "plugged": True}) == "pc_plugged"
    assert alert.power_change({"percent": 9, "plugged": True}) is None
    assert alert.power_change({"percent": 9, "plugged": False}) == "pc_unplugged"
    assert alert.power_change(None) is None


def test_the_plugged_and_unplugged_notices_say_so_with_the_charge(tmp_path):
    notifier, phone, seen = subscribed(tmp_path)
    assert notifier.notify_pc("pc_plugged", percent=8, wait=True)
    assert phone.read(seen[-1].content)["title"] == "Your PC is charging (8%)"
    assert notifier.notify_pc("pc_unplugged", percent=80, wait=True)
    assert phone.read(seen[-1].content)["title"] == "Your PC is on battery (80%)"
