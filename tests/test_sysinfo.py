import pytest
from fastapi.testclient import TestClient

from handoff import sysinfo
from handoff.planner import quick_for, quick_status
from handoff.server import create_app
from handoff.sysinfo import Sampler, describe, mbps


class Net:
    """Counters that grow by a known amount each second."""

    def __init__(self, down_per_s=1_250_000, up_per_s=125_000):  # 10 Mbps down, 1 Mbps up
        self.t, self.recv, self.sent, self.d, self.u = 0, 0, 0, down_per_s, up_per_s

    def tick(self):
        self.t += 1
        self.recv += self.d
        self.sent += self.u

    def read(self, ip=""):
        return {"adapter": "Wi-Fi", "recv": self.recv, "sent": self.sent, "link_mbps": 866}


def sampler(net, battery=None, system=None):
    return Sampler(network=net.read, battery=lambda: battery, system=lambda: system or SYSTEM)


SYSTEM = {"cpu_percent": 12, "memory": {"percent": 40, "used": 8 * 1024 ** 3, "total": 16 * 1024 ** 3},
          "disk": {"percent": 50, "free": 200 * 1024 ** 3, "total": 400 * 1024 ** 3}, "uptime_s": 7200}


def run(s, net, seconds):
    for _ in range(seconds + 1):
        net.tick()
        s.sample(now=float(net.t))


@pytest.fixture(autouse=True)
def _psutil_is_there(monkeypatch):
    monkeypatch.setattr(sysinfo, "psutil", object())  # the readers are faked, only "installed or not" matters here


def test_network_speed_is_the_change_in_the_counters_per_second():
    net = Net()
    s = sampler(net)
    run(s, net, 5)
    n = s.snapshot()["network"]
    assert n["down_bps"] == 10_000_000 and n["up_bps"] == 1_000_000
    assert mbps(n["down_bps"]) == "10.0 Mbps" and mbps(n["up_bps"]) == "1.0 Mbps"
    assert n["adapter"] == "Wi-Fi" and n["link_mbps"] == 866


def test_the_first_reading_has_no_speed_instead_of_a_huge_one():
    net = Net()
    net.recv = 5_000_000_000  # counters have been running for a long time
    s = sampler(net)
    s.sample(now=1.0)
    assert s.snapshot()["network"]["down_bps"] == 0


def test_a_counter_that_resets_never_shows_a_negative_speed():
    net = Net()
    s = sampler(net)
    run(s, net, 3)
    net.recv = 0  # an adapter was restarted
    s.sample(now=100.0)
    assert s.snapshot()["network"]["down_bps"] >= 0


def test_the_last_minute_is_kept_for_the_graph_and_no_more():
    net = Net()
    s = sampler(net)
    run(s, net, 90)
    history = s.snapshot()["network"]["history"]
    assert len(history["down"]) == 60 and len(history["up"]) == 60


def test_quiet_speeds_are_shown_in_kbps():
    assert mbps(54_000) == "54 Kbps" and mbps(250_000) == "0.2 Mbps" and mbps(12_300_000) == "12.3 Mbps"


def test_the_snapshot_carries_battery_processor_memory_and_disk():
    net = Net()
    battery = {"percent": 64, "plugged": False, "state": "discharging", "minutes_left": 130}
    s = sampler(net, battery=battery)
    run(s, net, 2)
    snap = s.snapshot()
    assert snap["available"] and snap["battery"] == battery and snap["cpu_percent"] == 12 and snap["memory"]["percent"] == 40 and snap["disk"]["free"] == 200 * 1024 ** 3


def test_a_desktop_pc_has_no_battery():
    net = Net()
    s = sampler(net, battery=None)
    run(s, net, 2)
    snap = s.snapshot()
    assert snap["battery"] is None
    assert "no battery" in describe(snap)


@pytest.mark.parametrize("battery, expected", [
    ({"percent": 100, "plugged": True, "state": "full", "minutes_left": None}, "full"),
    ({"percent": 55, "plugged": True, "state": "charging", "minutes_left": None}, "55% and charging"),
    ({"percent": 40, "plugged": False, "state": "discharging", "minutes_left": 135}, "2 h 15 min left"),
])
def test_the_chat_answer_says_what_the_battery_is_doing(battery, expected):
    net = Net()
    s = sampler(net, battery=battery)
    run(s, net, 4)
    assert expected in describe(s.snapshot())


def test_without_psutil_it_says_what_to_install(monkeypatch):
    monkeypatch.setattr(sysinfo, "psutil", None)
    snap = Sampler().snapshot()
    assert snap["available"] is False and "psutil" in snap["reason"] and "psutil" in describe(snap)


def test_a_failing_reading_does_not_kill_the_sampler():
    net = Net()
    s = sampler(net)
    run(s, net, 2)
    boom = {"n": 0}

    def flaky(ip=""):
        boom["n"] += 1
        if boom["n"] == 1:
            raise OSError("adapter vanished")
        return net.read()
    s._network = flaky
    with pytest.raises(OSError):
        s.sample(now=50.0)  # one call fails on its own...
    s.sample(now=51.0)  # ...and the next works (the loop catches the first)
    assert s.snapshot()["available"]


# ---------------------------------------------------------------- asked in the chat

@pytest.mark.parametrize("text", ["what's my battery?", "battery", "how much charge is left", "is my laptop charging", "network speed", "what is my internet speed",
                                  "pc status", "how is my pc doing", "cpu usage", "how much disk space is left"])
def test_plain_status_questions_are_answered_from_the_pc(text, monkeypatch):
    monkeypatch.setattr(sysinfo, "current", lambda: {"available": True, "battery": None, "cpu_percent": 5, "memory": {"percent": 10, "used": 1, "total": 2},
                                                      "disk": None, "network": {"adapter": "", "down_bps": 0, "up_bps": 0, "link_mbps": 0}})
    plan = quick_status(text)
    assert plan is not None and plan.reply and not plan.steps


@pytest.mark.parametrize("text", ["how do I check the battery in windows", "write a script that logs battery level every minute", "read battery.log",
                                  "explain how memory usage works in python", "create battery.txt with 100", "list", "hello"])
def test_other_requests_that_mention_the_words_go_to_the_model(text):
    assert quick_status(text) is None


def test_it_is_part_of_the_quick_requests(monkeypatch):
    monkeypatch.setattr(sysinfo, "current", lambda: {"available": False, "reason": "x"})
    assert quick_for("what's my battery", None).reply == "x"


# ---------------------------------------------------------------- over HTTP

def test_the_phone_can_read_the_status(settings, monkeypatch):
    import psutil
    monkeypatch.setattr(sysinfo, "psutil", psutil)  # the real one, for this test
    app = create_app(settings)
    client = TestClient(app, headers={"X-Token": app.state.token})
    data = client.get("/api/status").json()
    assert "available" in data
    assert TestClient(app).get("/api/status").status_code == 401  # not for anyone without a paired phone
