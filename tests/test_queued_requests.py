"""Requests held on the phone while the PC was unreachable: sending one twice must never run it twice, and the page wires the queue in."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
SW = (WEB / "sw.js").read_text(encoding="utf-8")
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")


@pytest.fixture
def client(settings):
    app = create_app(settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    return c


def test_the_same_client_id_gives_back_the_first_job(client):
    first = client.post("/api/jobs", json={"prompt": "what can you do?", "conversation": "c1", "client_id": "q1abc"}).json()
    again = client.post("/api/jobs", json={"prompt": "what can you do?", "conversation": "c1", "client_id": "q1abc"}).json()
    assert again["id"] == first["id"]
    assert len(client.get("/api/jobs").json()) == 1


def test_different_client_ids_are_different_jobs(client):
    a = client.post("/api/jobs", json={"prompt": "what can you do?", "client_id": "q1"}).json()
    b = client.post("/api/jobs", json={"prompt": "what can you do?", "client_id": "q2"}).json()
    assert a["id"] != b["id"]


def test_a_request_without_a_client_id_still_works_as_before(client):
    a = client.post("/api/jobs", json={"prompt": "what can you do?"}).json()
    b = client.post("/api/jobs", json={"prompt": "what can you do?"}).json()
    assert a["id"] != b["id"]


def test_a_bad_client_id_is_refused(client):
    assert client.post("/api/jobs", json={"prompt": "hi", "client_id": "bad id!"}).status_code == 422


def test_queue_js_is_served_and_loaded_before_the_page_script(client):
    res = client.get("/static/queue.js")
    assert res.status_code == 200 and len(res.content) > 1500
    assert INDEX.index("/static/queue.js") < INDEX.index("/static/app.js")
    assert '"/static/queue.js"' in SW and '"queue.js"' in GRADLE


def test_the_page_sends_held_requests_with_their_id_and_keeps_power_requests_out():
    send = APP[APP.index("async function flushQueue("):]
    assert "client_id" in send[:1500]
    assert "neverQueue" in APP[APP.index("async function queueRequest("):APP.index("async function queueRequest(") + 600]
    assert "queueRequest(text)" in APP[APP.index("async function send("):APP.index("async function send(") + 400]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_queue_rules_pass_their_own_tests():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "queue_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_wifi_chip_sits_beside_the_battery_everywhere_the_battery_chip_is():
    assert "function netChip(" in APP and "function netState(" in APP
    assert APP.count("netChip(") >= 3 and "healthCard" not in APP  # the top bar, the drawer, Home, and no separate section
    for place in ('netChip("only-collapsed"), pcChip("only-collapsed")', "netChip(), pcChip()"):
        assert place in APP
    assert 'pcChip("mini")' not in APP  # and not in the chat-list panel, where it only crowded the name
    chip = APP[APP.index("function netState("):APP.index("function renderHome(")]
    assert "down_bps" in chip and "wifiOff" in chip and "Paused" in chip  # speed, offline icon, paused word
    assert netkey_in_signatures()


def netkey_in_signatures():
    return APP.count("netKey()") >= 2  # the bars redraw when the link or its speed changes


def test_status_words_are_one_consistent_set_and_changes_get_an_undo_toast():
    labels = APP[APP.index("const LABEL = {"):APP.index("const LABEL = {") + 400]
    assert '"Waiting for approval"' in labels and '"Running"' in labels and '"Done"' in labels and '"Failed"' in labels
    observe = APP[APP.index("function observe("):APP.index("function observe(") + 1600]
    assert 'label: "Undo"' in observe and "S.mine.has(j.id)" in observe  # only for what this visit asked for, never on reload
    assert ".chip.done::before" in (WEB / "app.css").read_text(encoding="utf-8")  # a shape as well as a colour


def test_the_wifi_and_battery_chips_refresh_every_two_seconds_without_flicker():
    assert '}, 2000);' in APP[APP.index("The Wi-Fi speed and battery"):APP.index("The Wi-Fi speed and battery") + 400]
    status = APP[APP.index("async function loadPcStatus("):APP.index("/* the little chip in the top bar")]
    assert "statusBusy" in status  # never two questions at once
    assert 'S.topSig = ""' not in status.split("catch (e)")[0]  # no forced redraw on every refresh
