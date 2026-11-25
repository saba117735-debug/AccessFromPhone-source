import pytest
from fastapi.testclient import TestClient

from handoff.accounts import AccountError, AccountStore, Locked
from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.db import Database
from handoff.planner import DemoPlanner
from handoff.server import create_app

BS = chr(92)  # a backslash, for DOMAIN-slash-user
PASSWORD = "correct horse"
H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


@pytest.fixture
def store(tmp_path):
    return AccountStore(Database(tmp_path / "app.db"), pairing_minutes=2, max_devices=3)


def test_the_owner_is_set_up_once_with_a_recovery_code(store):
    assert not store.owner_exists
    recovery = store.setup_owner("Afza", PASSWORD)
    assert store.owner_exists and store.owner_name == "Afza" and len(recovery) == 19
    with pytest.raises(AccountError):
        store.setup_owner("Someone else", PASSWORD)


def test_a_short_password_is_refused(store):
    with pytest.raises(AccountError, match="at least 8"):
        store.setup_owner("Afza", "short")


def everything_on_disk(folder):
    """The database file and its write-ahead log, as bytes: what anyone who copied the PC's data would have."""
    return b"".join(p.read_bytes() for p in sorted(folder.glob("app.db*")))


def test_the_password_is_not_stored_in_the_file(store, tmp_path):
    store.setup_owner("Afza", PASSWORD)
    assert PASSWORD.encode() not in everything_on_disk(tmp_path)


def test_wrong_passwords_lock_the_door_for_a_minute(store):
    store.setup_owner("Afza", PASSWORD)
    for _ in range(5):
        assert not store.verify_owner("nope", "pc")
    with pytest.raises(Locked):
        store.verify_owner(PASSWORD, "pc")  # even the right one waits
    assert store.verify_owner(PASSWORD, "another place")


def test_the_recovery_code_resets_the_password_and_is_replaced(store):
    recovery = store.setup_owner("Afza", PASSWORD)
    new_recovery = store.reset_password(recovery.lower(), "a brand new one", "pc")
    assert new_recovery != recovery
    assert store.verify_owner("a brand new one", "x") and not store.verify_owner(PASSWORD, "y")
    with pytest.raises(AccountError):
        store.reset_password(recovery, "third password", "pc")  # the old code is spent


def test_a_pairing_code_works_once_and_gives_the_phone_its_own_token(store):
    store.setup_owner("Afza", PASSWORD)
    code, seconds = store.new_pairing_code()
    assert seconds == 120
    device, token = store.pair(code.lower().replace("-", " "), "Afza's iPhone", "10.0.0.7")
    assert device.name == "Afza's iPhone" and len(token) >= 30
    assert store.authenticate(token, "10.0.0.7").id == device.id
    with pytest.raises(AccountError):
        store.pair(code, "Second try", "10.0.0.8")  # one phone per code


def test_a_wrong_or_expired_code_is_refused_and_repeated_guessing_is_stopped(store):
    store.setup_owner("Afza", PASSWORD)
    for _ in range(5):
        with pytest.raises(AccountError):
            store.pair("AAAA-BBBB", "x", "10.0.0.9")
    with pytest.raises(Locked):
        store.pair("AAAA-BBBB", "x", "10.0.0.9")


def test_a_code_cannot_pair_before_setup(store):
    code, _ = store.new_pairing_code()
    with pytest.raises(AccountError, match="setting up"):
        store.pair(code, "x")


def test_tokens_are_kept_only_as_hashes_and_survive_a_restart(store, tmp_path):
    store.setup_owner("Afza", PASSWORD)
    _, token = store.pair(store.new_pairing_code()[0], "Phone", "")
    assert token.encode() not in everything_on_disk(tmp_path)
    again = AccountStore(Database(tmp_path / "app.db"))
    assert again.authenticate(token).name == "Phone"


def test_blocking_removing_and_renaming(store):
    store.setup_owner("Afza", PASSWORD)
    device, token = store.pair(store.new_pairing_code()[0], "Old name", "")
    store.rename(device.id, "Work phone")
    assert store.authenticate(token).name == "Work phone"
    store.set_blocked(device.id, True)
    assert store.authenticate(token) is None
    store.set_blocked(device.id, False)
    assert store.authenticate(token) is not None
    store.remove(device.id)
    assert store.authenticate(token) is None and store.devices() == []


def test_there_is_a_limit_on_phones(store):
    store.setup_owner("Afza", PASSWORD)
    for i in range(3):
        store.pair(store.new_pairing_code()[0], f"p{i}", "")
    code, _ = store.new_pairing_code()
    with pytest.raises(AccountError, match="already has 3 devices"):
        store.pair(code, "one too many", "")


# ---------------------------------------------------------------- over HTTP

@pytest.fixture
def both(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    phone_app = create_app(settings, DemoPlanner())
    admin_app = create_admin_app(settings, phone_app.state.accounts, phone_app.state.manager)
    return TestClient(phone_app), TestClient(admin_app, base_url="http://127.0.0.1:8766"), phone_app


def sign_up(admin):
    r = admin.post("/admin/setup", json={"name": "Afza", "password": PASSWORD}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["recovery_code"]


def pair(phone, admin, name="Phone"):
    code = admin.post("/admin/pairing", headers=H).json()["code"]
    r = phone.post("/pair/claim", json={"code": code, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_the_old_shared_token_no_longer_opens_anything(both):
    phone, admin, app = both
    assert phone.get("/api/jobs", headers={"x-token": app.state.token}).status_code == 401
    assert phone.get("/api/jobs").status_code == 401


def test_the_phone_page_can_tell_if_the_pc_is_ready(both):
    phone, admin, _ = both
    assert phone.get("/pair/info").json()["ready"] is False
    sign_up(admin)
    info = phone.get("/pair/info").json()
    assert info["ready"] is True


def test_nothing_about_the_pc_is_shown_before_a_phone_has_paired(both):
    phone, admin, app = both
    sign_up(admin)
    shown = phone.get("/pair/info").json()
    assert set(shown) == {"accounts", "ready"}  # no PC name, no user, no version
    assert phone.get("/healthz").json() == {"ok": True}
    for path in ("/api/info", "/api/jobs", "/api/audit"):
        assert phone.get(path).status_code == 401


def test_pairing_end_to_end(both):
    phone, admin, _ = both
    sign_up(admin)
    token = pair(phone, admin, "Afza's iPhone")
    info = phone.get("/api/info", headers={"x-token": token}).json()
    assert info["device"]["name"] == "Afza's iPhone" and info["owner"] == "Afza" and info["accounts"] is True
    listed = admin.get("/admin/devices").json()
    assert [d["name"] for d in listed] == ["Afza's iPhone"]
    assert "token" not in str(listed)


def test_blocking_a_phone_signs_it_out_at_once_and_unblocking_brings_it_back(both):
    phone, admin, _ = both
    sign_up(admin)
    token = pair(phone, admin)
    device_id = admin.get("/admin/devices").json()[0]["id"]
    admin.post(f"/admin/devices/{device_id}/block", headers=H)
    assert phone.get("/api/jobs", headers={"x-token": token}).status_code == 401
    admin.post(f"/admin/devices/{device_id}/unblock", headers=H)
    assert phone.get("/api/jobs", headers={"x-token": token}).status_code == 200
    admin.delete(f"/admin/devices/{device_id}", headers=H)
    assert phone.get("/api/jobs", headers={"x-token": token}).status_code == 401


def test_a_phone_can_rename_and_sign_itself_out(both):
    phone, admin, _ = both
    sign_up(admin)
    token = pair(phone, admin)
    h = {"x-token": token}
    assert phone.post("/api/device/rename", json={"name": "Kitchen tablet"}, headers=h).json()["name"] == "Kitchen tablet"
    assert phone.delete("/api/device", headers=h).status_code == 200
    assert phone.get("/api/jobs", headers=h).status_code == 401
    assert [d["signed_out"] for d in admin.get("/admin/devices").json()] == [True]


def test_pairing_events_reach_the_audit_trail(both):
    phone, admin, _ = both
    sign_up(admin)
    token = pair(phone, admin, "Afza's iPhone")
    events = {e["event"] for e in phone.get("/api/audit", headers={"x-token": token}).json()}
    assert "device_paired" in events   # this phone's own pairing; the PC owner's setup is not its business


def test_the_pc_page_needs_the_password_and_the_page_header(both):
    phone, admin, _ = both
    assert admin.get("/admin/state").json()["setup_needed"] is True
    sign_up(admin)
    admin.post("/admin/logout", headers=H)
    assert admin.get("/admin/devices").status_code == 401
    assert admin.post("/admin/login", json={"password": "wrong password"}, headers=H).status_code == 401
    assert admin.post("/admin/login", json={"password": PASSWORD}).status_code == 403  # no X-Admin header: not from our page
    assert admin.post("/admin/login", json={"password": PASSWORD}, headers=H).status_code == 200
    assert admin.get("/admin/devices").status_code == 200


def test_the_pc_page_refuses_other_hosts(both):
    _, admin, _ = both
    assert admin.get("/admin/state", headers={"host": "evil.example:8766"}).status_code == 403


def test_the_password_can_be_changed_and_reset(both):
    _, admin, _ = both
    recovery = sign_up(admin)
    assert admin.post("/admin/password", json={"old_password": "wrong one!", "new_password": "another good one"}, headers=H).status_code == 400
    assert admin.post("/admin/password", json={"old_password": PASSWORD, "new_password": "another good one"}, headers=H).status_code == 200
    admin.post("/admin/logout", headers=H)
    r = admin.post("/admin/reset", json={"recovery_code": recovery, "new_password": "reset by code"}, headers=H)
    assert r.status_code == 200 and r.json()["recovery_code"] != recovery


def test_pairing_returns_a_qr_code_and_a_link_with_the_code(both):
    _, admin, _ = both
    sign_up(admin)
    r = admin.post("/admin/pairing", headers=H).json()
    assert "<svg" in r["svg"] and r["link"].endswith("/#pair=" + r["code"]) and r["expires_in"] == 120


def test_an_older_accounts_file_moves_into_the_database_once(tmp_path):
    import json
    old = tmp_path / "accounts.json"
    old.write_text(json.dumps({"owner": {"name": "Afza", "password": "x$y", "recovery": "x$y", "created": "2026-01-01T00:00:00+00:00"},
                               "devices": [{"id": "d1", "name": "Old phone", "token_hash": "abc", "created": "2026-01-01T00:00:00+00:00",
                                            "last_seen": "2026-01-02T00:00:00+00:00", "last_ip": "1.2.3.4", "blocked": False}]}), encoding="utf-8")
    store = AccountStore(old)
    assert store.owner_name == "Afza" and [d["name"] for d in store.devices()] == ["Old phone"]
    assert not old.exists() and (tmp_path / "accounts.json.migrated").exists()
    assert AccountStore(Database(tmp_path / "app.db")).owner_name == "Afza"  # now it is the database that holds it


def test_a_lost_password_and_recovery_code_can_be_cleared_from_the_pc(store):
    store.setup_owner("Afza", PASSWORD)
    store.pair(store.new_pairing_code()[0], "Phone", "")
    assert store.reset_everything() == 1
    assert not store.owner_exists and store.devices() == []
    store.setup_owner("Afza", "a new password")  # setup works again


# ---------------------------------------------------------------- signing in with the Windows user name and password

@pytest.fixture
def windows(monkeypatch, project):
    """Windows says: the user is afza on the CORP domain and the password is 'windows pw'."""
    from handoff import winauth
    attempts = []
    monkeypatch.setattr(winauth, "available", lambda: True)
    monkeypatch.setattr(winauth, "current_user", lambda: ("CORP", "afza"))
    monkeypatch.setattr(winauth, "check", lambda domain, user, pw: attempts.append((domain, user)) or (pw == "windows pw"))
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "auto")
    settings = load_settings(project / "config.yaml")
    phone_app = create_app(settings, DemoPlanner())
    admin_app = create_admin_app(settings, phone_app.state.accounts, phone_app.state.manager)
    return TestClient(phone_app), TestClient(admin_app, base_url="http://127.0.0.1:8766"), phone_app, attempts


def test_on_windows_there_is_no_setup_step_and_no_new_password(windows):
    phone, admin, app, _ = windows
    state = admin.get("/admin/state").json()
    assert state["mode"] == "windows" and state["setup_needed"] is False and state["windows_user"] == "CORP" + BS + "afza"
    assert admin.post("/admin/setup", json={"name": "Afza", "password": "something else"}, headers=H).status_code == 400


def test_the_windows_user_name_and_password_sign_in_and_make_that_user_the_owner(windows):
    phone, admin, _, attempts = windows
    assert phone.get("/pair/info").json()["ready"] is False  # nobody has signed in on the PC yet
    assert admin.post("/admin/login", json={"username": "CORP" + BS + "afza", "password": "windows pw"}, headers=H).status_code == 200
    assert admin.get("/admin/state").json()["owner"] == "afza" and attempts == [("CORP", "afza")]
    assert phone.get("/pair/info").json()["ready"] is True
    assert admin.post("/admin/pairing", headers=H).status_code == 200


@pytest.mark.parametrize("typed", ["afza", "AFZA", "CORP" + BS + "afza", "corp" + BS + "Afza", "afza@corp.example.com"])
def test_the_usual_ways_of_typing_the_user_name_all_work(windows, typed):
    _, admin, _, _ = windows
    assert admin.post("/admin/login", json={"username": typed, "password": "windows pw"}, headers=H).status_code == 200


def test_another_windows_user_cannot_become_the_owner(windows):
    _, admin, _, attempts = windows
    for typed in ("someone_else", "OTHER" + BS + "afza", "."):
        assert admin.post("/admin/login", json={"username": typed, "password": "windows pw"}, headers=H).status_code == 401
    assert attempts == []  # Windows was not even asked about them


def test_a_wrong_windows_password_is_refused_and_stops_after_three_tries(windows):
    _, admin, _, attempts = windows
    for _ in range(3):
        r = admin.post("/admin/login", json={"username": "afza", "password": "nope"}, headers=H)
        assert r.status_code == 401 and "not right" in r.json()["detail"]
    r = admin.post("/admin/login", json={"username": "afza", "password": "windows pw"}, headers=H)
    assert r.status_code == 429 and len(attempts) == 3  # the real Windows account is not hammered further


def test_there_is_no_separate_password_to_change_or_recover_in_windows_mode(windows):
    _, admin, _, _ = windows
    admin.post("/admin/login", json={"username": "afza", "password": "windows pw"}, headers=H)
    assert admin.post("/admin/password", json={"old_password": "a", "new_password": "bbbbbbbb"}, headers=H).status_code == 400
    assert admin.post("/admin/reset", json={"recovery_code": "x", "new_password": "bbbbbbbb"}, headers=H).status_code == 400


def test_pairing_works_the_same_after_a_windows_sign_in(windows):
    phone, admin, _, _ = windows
    admin.post("/admin/login", json={"username": "afza", "password": "windows pw"}, headers=H)
    code = admin.post("/admin/pairing", headers=H).json()["code"]
    token = phone.post("/pair/claim", json={"code": code, "name": "Phone"}).json()["token"]
    assert phone.get("/api/info", headers={"x-token": token}).json()["owner"] == "afza"


def test_a_phone_can_list_devices_and_remove_another_with_the_owner_password(both):
    phone, admin, app = both
    sign_up(admin)
    mine, other = pair(phone, admin, "Mine"), pair(phone, admin, "Other")
    listing = phone.get("/api/devices", headers={"x-token": mine}).json()
    assert listing["limit"] == 10 and [d["name"] for d in listing["devices"]] == [d["name"] for d in listing["devices"]]
    assert {d["name"] for d in listing["devices"]} == {"Mine", "Other"} and [d["current"] for d in listing["devices"] if d["name"] == "Mine"] == [True]
    other_id = next(d["id"] for d in listing["devices"] if d["name"] == "Other")
    mine_id = next(d["id"] for d in listing["devices"] if d["name"] == "Mine")
    refused = phone.post(f"/api/devices/{other_id}/remove", headers={"x-token": mine}, json={"password": "not it"})
    assert refused.status_code == 403  # (a 403, not a 401: a wrong password must not sign this phone out)
    assert phone.get("/api/devices", headers={"x-token": mine}).status_code == 200
    assert phone.post(f"/api/devices/{mine_id}/remove", headers={"x-token": mine}, json={"password": PASSWORD}).status_code == 400  # not itself
    assert phone.post(f"/api/devices/{other_id}/remove", headers={"x-token": mine}, json={"password": PASSWORD}).status_code == 200
    assert phone.get("/api/devices", headers={"x-token": other}).status_code == 401  # the removed phone is out
    assert [d["name"] for d in phone.get("/api/devices", headers={"x-token": mine}).json()["devices"]] == ["Mine"]


def test_a_phone_can_prove_the_owner_password_to_reset_its_app_pin(both):
    phone, admin, _ = both
    sign_up(admin)
    token = pair(phone, admin, "Mine")
    assert phone.post("/api/owner/check", headers={"x-token": token}, json={"password": "nope nope"}).status_code == 403
    assert phone.post("/api/owner/check", headers={"x-token": token}, json={"password": PASSWORD}).json() == {"ok": True}
    assert phone.post("/api/owner/check", json={"password": PASSWORD}).status_code == 401  # a phone that isn't signed in can't ask


def test_each_phone_sees_only_its_own_requests_and_record(both):
    phone, admin, _ = both
    sign_up(admin)
    a, b = pair(phone, admin, "Phone A"), pair(phone, admin, "Phone B")
    job = phone.post("/api/jobs", json={"prompt": "list my files"}, headers={"x-token": a}).json()
    assert [j["id"] for j in phone.get("/api/jobs", headers={"x-token": a}).json()] == [job["id"]]
    assert phone.get("/api/jobs", headers={"x-token": b}).json() == []
    assert phone.get(f"/api/jobs/{job['id']}", headers={"x-token": b}).status_code == 404
    assert phone.post(f"/api/jobs/{job['id']}/stop", headers={"x-token": b}).status_code in (404, 409)   # 409: it is not B's turn at all
    assert phone.post(f"/api/jobs/{job['id']}/approve", headers={"x-token": b}).status_code in (404, 409)
    mine = {e.get("job") for e in phone.get("/api/audit", headers={"x-token": a}).json()}
    theirs = {e.get("job") for e in phone.get("/api/audit", headers={"x-token": b}).json()}
    assert job["id"] in mine and job["id"] not in theirs


def test_the_same_phone_signing_out_and_back_in_is_not_a_new_phone(both):
    phone, admin, _ = both
    sign_up(admin)

    def claim(install, name="Afza's phone"):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        return phone.post("/pair/claim", json={"code": code, "name": name, "install": install}).json()

    first = claim("install-A")
    phone.delete("/api/device", headers={"x-token": first["token"]})
    again = claim("install-A")
    assert again["device"]["id"] == first["device"]["id"] and again["device"]["name"] == "Afza's phone"
    assert len(admin.get("/admin/devices").json()) == 1
    assert phone.get("/api/jobs", headers={"x-token": first["token"]}).status_code == 401   # the old token stays dead
    other = claim("install-B", "Someone else")
    assert other["device"]["id"] != first["device"]["id"] and len(admin.get("/admin/devices").json()) == 2
