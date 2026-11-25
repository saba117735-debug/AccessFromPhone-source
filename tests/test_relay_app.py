"""The real app, reached from a phone only through the relay: pair, sign in, use it."""
import httpx
import pytest

from handoff.config import load_settings
from handoff.relay import Registry, create_relay_app
from handoff.server import create_app
from handoff.tunnel import RelayLink
from test_relay import KEY, serve, wait_for


@pytest.fixture
def world(project, monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings)
    port, stop = serve(create_relay_app("localhost", Registry(tmp_path / "relay.db"), register_key=KEY, trust_proxy=True))
    link = RelayLink(app, app.state.manager.db, f"http://127.0.0.1:{port}", KEY)
    link.enable()
    assert wait_for(lambda: link.connected)
    pc_id = link._creds()[0]
    app.state.accounts.setup_owner("Afza", "correct horse")

    def phone(ip="203.0.113.9"):
        return httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Host": f"{pc_id}.localhost:{port}", "X-Forwarded-For": ip}, timeout=20)
    yield {"app": app, "phone": phone, "link": link}
    link._client.stop()
    stop()


def test_the_phone_page_and_its_files_load_through_the_relay(world):
    phone = world["phone"]()
    page = phone.get("/")
    assert page.status_code == 200 and "AccessFromPhone" in page.text
    for asset in ("/static/app.js", "/static/app.css", "/static/manifest.webmanifest"):
        assert phone.get(asset).status_code == 200, asset


def test_a_phone_can_pair_and_use_the_app_from_anywhere(world):
    phone = world["phone"]()
    assert phone.get("/pair/info").json() == {"accounts": True, "ready": True}
    code = world["app"].state.accounts.new_pairing_code()[0]
    claimed = phone.post("/pair/claim", json={"code": code, "name": "Afza's iPhone"})
    assert claimed.status_code == 200
    token = claimed.json()["token"]
    info = phone.get("/api/info", headers={"X-Token": token}).json()
    assert info["owner"] == "Afza" and info["device"]["name"] == "Afza's iPhone"
    assert phone.get("/api/jobs", headers={"X-Token": token}).status_code == 200
    assert phone.get("/api/jobs").status_code == 401  # still needs a paired phone
    job = phone.post("/api/jobs", headers={"X-Token": token}, json={"prompt": "list"}).json()
    assert job["id"]


def test_the_pc_knows_which_phone_is_which_even_though_everything_arrives_via_the_relay(world):
    phone = world["phone"]("203.0.113.9")
    code = world["app"].state.accounts.new_pairing_code()[0]
    token = phone.post("/pair/claim", json={"code": code, "name": "Phone"}).json()["token"]
    phone.get("/api/info", headers={"X-Token": token})
    assert [d["last_ip"] for d in world["app"].state.accounts.devices()] == ["203.0.113.9"]


def test_one_phone_guessing_codes_does_not_lock_out_the_others(world):
    attacker, friend = world["phone"]("198.51.100.1"), world["phone"]("203.0.113.50")
    codes = [attacker.post("/pair/claim", json={"code": "AAAA-BBBB", "name": "x"}).status_code for _ in range(7)]
    assert codes[:5] == [400] * 5 and codes[5:] == [429, 429]
    good = world["app"].state.accounts.new_pairing_code()[0]
    assert friend.post("/pair/claim", json={"code": good, "name": "Friend"}).status_code == 200


def test_a_phone_removed_on_the_pc_is_locked_out_through_the_relay_too(world):
    phone = world["phone"]()
    code = world["app"].state.accounts.new_pairing_code()[0]
    token = phone.post("/pair/claim", json={"code": code, "name": "Phone"}).json()["token"]
    device = world["app"].state.accounts.devices()[0]
    world["app"].state.accounts.remove(device["id"])
    assert phone.get("/api/jobs", headers={"X-Token": token}).status_code == 401
