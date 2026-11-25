"""Each phone has its own allowed folders; the Admin sees and manages them all and can share a folder with chosen phones; and sees who asked what, but not inside chats."""
import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.db import Database
from handoff.grants import GrantStore
from handoff.planner import DemoPlanner
from handoff.policy import OutsideAllowed, PolicyEngine, viewing
from handoff.config import ApprovalConfig
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}


# ---------------------------------------------------------------- the rules (no phones, no HTTP)

@pytest.fixture
def engine(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("hello")
    return PolicyEngine([tmp_path / "work"], ApprovalConfig()), tmp_path / "docs"


def test_a_folder_one_phone_allowed_is_closed_to_the_others(engine):
    policy, docs = engine
    with viewing("ben"):
        policy.grant(docs, None)
        assert policy.resolve(docs / "a.txt")
        assert policy.granted() == [docs.resolve()]
    with viewing("chen"):
        with pytest.raises(OutsideAllowed):
            policy.resolve(docs / "a.txt")
        assert policy.granted() == [] and policy.folders_for_phone() == []
        assert docs.resolve() not in [f for f, _ in policy.visible_roots()]


def test_the_admin_and_the_pc_see_every_phones_folders(engine):
    policy, docs = engine
    with viewing("ben"):
        policy.grant(docs, None)
    with viewing("asha", admin=True):
        assert policy.resolve(docs / "a.txt") and policy.folders_for_phone()[0]["mine"] is True
    assert policy.resolve(docs / "a.txt")                                   # the PC itself (nobody asking)
    assert [(e["device"], e["path"]) for e in policy.entries()] == [("ben", docs.resolve())]


def test_sharing_gives_chosen_phones_the_folder_read_or_edit(engine):
    policy, docs = engine
    with viewing("ben"):
        policy.grant(docs, None, change=True)
    assert policy.share(docs, "ben", {"chen": False, "dev": True}) is True
    with viewing("chen"):
        assert policy.resolve(docs / "a.txt")
        with pytest.raises(Exception):
            policy.resolve(docs / "a.txt", for_write=True)                     # shared to read only
        row = policy.folders_for_phone()[0]
        assert row["shared"] is True and row["mine"] is False and row["change"] is False
    with viewing("dev"):
        assert policy.resolve(docs / "a.txt", for_write=True)                  # shared to edit (the owner may edit)
    with viewing("eve"):
        with pytest.raises(OutsideAllowed):
            policy.resolve(docs / "a.txt")                                     # not shared with her


def test_a_share_never_gives_more_than_the_owner_had(engine):
    policy, docs = engine
    with viewing("ben"):
        policy.grant(docs, None, change=False)
    policy.share(docs, "ben", {"chen": True})
    with viewing("chen"):
        with pytest.raises(Exception):
            policy.resolve(docs / "a.txt", for_write=True)


def test_a_phone_takes_back_only_its_own_folder_and_the_admin_takes_back_any(engine):
    policy, docs = engine
    with viewing("ben"):
        policy.grant(docs, None)
    policy.share(docs, "ben", {"chen": False})
    with viewing("chen"):
        assert policy.revoke(docs) is False                                     # shared with her: not hers to remove
        assert policy.granted() == [docs.resolve()]
    with viewing("asha", admin=True):
        assert policy.revoke(docs) is True
    assert policy.entries() == []


def test_removing_a_phone_takes_its_folders_and_its_shares(engine):
    policy, docs = engine
    with viewing("ben"):
        policy.grant(docs, None)
    policy.share(docs, "ben", {"chen": False})
    policy.forget_device("chen")
    assert policy.entries()[0]["shared"] == {}
    policy.forget_device("ben")
    assert policy.entries() == []


def test_the_pc_owners_own_folders_stay_open_to_every_phone(engine):
    policy, _ = engine
    with viewing("chen"):
        assert policy.resolve(policy._writable[0] / "x.txt")


def test_owners_and_shares_survive_a_restart_and_old_folders_belong_to_no_phone(tmp_path):
    db = Database(tmp_path / "a.db")
    db._conn.execute("INSERT INTO grants (path, change) VALUES (?, 1)", (str(tmp_path / "old"),))   # kept before phones had their own list
    store = GrantStore(db)
    entries = store.load_entries()
    assert [(e["device"], e["change"]) for e in entries] == [("", True)]                              # carried over, owned by nobody
    store.save_entries([{"path": tmp_path / "x", "device": "ben", "change": False, "shared": {"chen": True}}])
    again = GrantStore(Database(tmp_path / "a.db")).load_entries()
    assert again == [{"path": tmp_path / "x", "device": "ben", "change": False, "shared": {"chen": True}}]


# ---------------------------------------------------------------- over HTTP, with real phones

@pytest.fixture
def world(project, monkeypatch, tmp_path):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H)
    phone = TestClient(app)
    ids = {}

    def pair(name):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        r = phone.post("/pair/claim", json={"code": code, "name": name}).json()
        ids[name] = r["device"]["id"]
        return {"x-token": r["token"]}

    people = {"Asha": pair("Asha's phone"), "Ben": pair("Ben's phone"), "Chen": pair("Chen's phone")}   # the first phone paired is the Admin
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("hello", encoding="utf-8")
    return phone, admin, people, ids, docs


def paths(phone, who):
    return [g["path"] for g in phone.get("/api/info", headers=who).json()["folders"]["granted_details"]]


def test_a_folder_ben_allows_is_not_seen_or_usable_by_chen(world):
    phone, admin, people, ids, docs = world
    assert phone.post("/api/access/grant", json={"path": str(docs)}, headers=people["Ben"]).status_code == 200
    assert paths(phone, people["Ben"]) == [str(docs.resolve())]
    assert paths(phone, people["Chen"]) == []
    assert phone.get("/api/file", params={"path": str(docs / "a.txt")}, headers=people["Ben"]).status_code == 200
    assert phone.get("/api/file", params={"path": str(docs / "a.txt")}, headers=people["Chen"]).status_code == 403
    phone.post("/api/session/end", headers=people["Ben"])
    assert phone.post("/api/access/revoke", json={"path": str(docs)}, headers=people["Chen"]).json()["granted_details"] == []
    assert paths(phone, people["Ben"]) == [str(docs.resolve())]                                       # Chen could not take it from Ben


def test_the_admin_phone_sees_every_phones_folders_with_their_owners(world):
    phone, admin, people, ids, docs = world
    phone.post("/api/access/grant", json={"path": str(docs)}, headers=people["Ben"])
    seen = phone.get("/api/folders", headers=people["Asha"]).json()
    assert seen["admin"] is True and [(f["path"], f["owner_name"], f["shared"]) for f in seen["folders"]] == [(str(docs.resolve()), "Ben's phone", [])]
    assert {p["name"] for p in seen["phones"]} == {"Asha's phone", "Ben's phone", "Chen's phone"}
    other = phone.get("/api/folders", headers=people["Chen"]).json()
    assert other["admin"] is False and other["folders"] == [] and other["phones"] == []                # a normal phone is not shown who else has what


def test_the_admin_shares_a_folder_with_a_chosen_phone_and_it_shows_as_shared(world):
    phone, admin, people, ids, docs = world
    phone.post("/api/access/grant", json={"path": str(docs)}, headers=people["Ben"])
    share = {"path": str(docs), "owner": ids["Ben's phone"], "targets": [{"id": ids["Chen's phone"], "change": False}]}
    assert phone.post("/api/folders/share", json=share, headers=people["Ben"]).status_code == 403    # only the Admin shares
    done = phone.post("/api/folders/share", json=share, headers=people["Asha"])
    assert done.status_code == 200 and done.json()["folders"][0]["shared"] == [{"id": ids["Chen's phone"], "name": "Chen's phone", "change": False}]
    row = phone.get("/api/info", headers=people["Chen"]).json()["folders"]["granted_details"][0]
    assert (row["path"], row["shared"], row["mine"]) == (str(docs.resolve()), True, False)
    assert phone.get("/api/file", params={"path": str(docs / "a.txt")}, headers=people["Chen"]).status_code == 200
    phone.post("/api/folders/share", json={**share, "targets": []}, headers=people["Asha"])            # unshare
    assert paths(phone, people["Chen"]) == []
    assert phone.post("/api/folders/share", json={**share, "targets": [{"id": "nope", "change": False}]}, headers=people["Asha"]).status_code == 404


def test_the_admin_can_take_a_folder_back_from_a_phone(world):
    phone, admin, people, ids, docs = world
    phone.post("/api/access/grant", json={"path": str(docs)}, headers=people["Ben"])
    take = {"path": str(docs), "owner": ids["Ben's phone"]}
    assert phone.post("/api/folders/revoke", json=take, headers=people["Ben"]).status_code == 403
    assert phone.post("/api/folders/revoke", json=take, headers=people["Asha"]).status_code == 200
    assert paths(phone, people["Ben"]) == []


def test_sharing_is_done_on_the_admin_phone_not_on_the_pc_page(world):
    phone, admin, people, ids, docs = world
    assert admin.get("/admin/folders", headers=H).status_code == 404


def test_removing_a_phone_removes_its_folders_but_signing_out_keeps_them(world):
    phone, admin, people, ids, docs = world
    phone.post("/api/access/grant", json={"path": str(docs)}, headers=people["Ben"])
    phone.delete("/api/device", headers=people["Ben"])                                                 # signs out: the entry stays
    assert [f["owner"] for f in phone.get("/api/folders", headers=people["Asha"]).json()["folders"]] == [ids["Ben's phone"]]
    ben = ids["Ben's phone"]
    admin.delete(f"/admin/devices/{ben}", headers=H)                                                    # removed by the Owner
    assert phone.get("/api/folders", headers=people["Asha"]).json()["folders"] == []


def places(phone, who):
    return {p["path"]: p for p in phone.get("/api/browse", headers=who).json()["entries"]}


def test_the_admins_list_of_places_says_who_allowed_each_folder_and_who_else_has_it(world):
    phone, admin, people, ids, docs = world
    phone.post("/api/access/grant", json={"path": str(docs)}, headers=people["Ben"])
    row = places(phone, people["Asha"])[str(docs.resolve())]
    assert row["shareable"] is True and row["owners"] == ["Ben's phone"] and row["shared_with"] == []
    phone.post("/api/folders/share", json={"path": str(docs), "targets": [{"id": ids["Chen's phone"], "change": False}]}, headers=people["Asha"])
    assert places(phone, people["Asha"])[str(docs.resolve())]["shared_with"] == ["Chen's phone"]
    mine = places(phone, people["Chen"])[str(docs.resolve())]
    assert mine["access"] == "shared" and "shareable" not in mine                                       # the other phone is told it was shared with it, and gets no Share option
    assert "shareable" not in places(phone, people["Ben"])[str(docs.resolve())]


def test_a_folder_two_phones_asked_for_is_shared_as_one_and_the_owners_are_not_offered_it(world):
    phone, admin, people, ids, docs = world
    for who in ("Ben", "Chen"):
        phone.post("/api/access/grant", json={"path": str(docs)}, headers=people[who])
        phone.post("/api/session/end", headers=people[who])
    done = phone.post("/api/folders/share", json={"path": str(docs), "targets": [{"id": ids["Ben's phone"], "change": False}, {"id": ids["Asha's phone"], "change": False}]}, headers=people["Asha"])
    assert done.status_code == 200
    assert phone.post("/api/folders/share", json={"path": str(docs), "targets": []}, headers=people["Asha"]).status_code == 200
    assert phone.post("/api/folders/share", json={"path": str(docs / "nowhere"), "targets": []}, headers=people["Asha"]).status_code == 404   # not a folder on the list: a clear failure


def test_the_admin_sees_who_asked_what_but_not_inside_the_chat(world):
    phone, admin, people, ids, docs = world
    phone.post("/api/session/start", headers=people["Ben"])
    job = phone.post("/api/jobs", json={"prompt": "how much disk space is free", "conversation": "one"}, headers=people["Ben"]).json()
    seen = phone.get("/api/overview", headers=people["Asha"]).json()["requests"]
    row = next(r for r in seen if r["id"] == job["id"])
    assert row["phone"] == "Ben's phone" and row["prompt"] == "how much disk space is free" and row["created_at"]
    assert not ({"steps", "outputs", "summary", "files", "activity", "changed"} & set(row))             # the conversation stays on Ben's phone
    assert phone.get("/api/overview", headers=people["Chen"]).status_code == 403                        # and only the Admin may look
    assert phone.get(f"/api/jobs/{job['id']}", headers=people["Asha"]).status_code == 404               # opening Ben's chat is still not possible
