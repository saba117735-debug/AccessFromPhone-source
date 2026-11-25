"""Long-press a file on the picture of the PC's screen: which file is it, may the phone have it, and can a whole folder come as one zip."""
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.admin import create_admin_app
from handoff.config import load_settings
from handoff.planner import DemoPlanner
from handoff.screen import itemat
from handoff.screen.capture import screen_size  # noqa: F401  (the name must exist: the manager asks it for the screen's pixels)
from handoff.server import create_app

H = {"X-Admin": "1", "host": "127.0.0.1:8766"}
JPEG = b"\xff\xd8\xff\xe0fake-picture"


# ---- the part that turns "a name under the finger" into real paths

def test_a_name_is_matched_in_the_folders_the_window_shows(tmp_path):
    (tmp_path / "Report.docx").write_text("x")
    (tmp_path / "Other.txt").write_text("x")
    assert itemat.matches([str(tmp_path)], "Report.docx") == [(tmp_path / "Report.docx").resolve()]
    assert itemat.matches([str(tmp_path)], "report.DOCX") == [(tmp_path / "Report.docx").resolve()]   # Windows names ignore capitals


def test_explorer_may_hide_extensions_and_every_possible_file_is_offered(tmp_path):
    (tmp_path / "Budget.xlsx").write_text("x")
    (tmp_path / "Budget.pdf").write_text("x")
    (tmp_path / "Budget").mkdir()
    found = itemat.matches([str(tmp_path)], "Budget")
    assert found[0] == (tmp_path / "Budget").resolve()                                    # an exact name comes first
    assert {p.name for p in found} == {"Budget", "Budget.xlsx", "Budget.pdf"}


def test_a_desktop_is_two_folders_and_a_virtual_place_or_a_missing_name_finds_nothing(tmp_path):
    mine, shared = tmp_path / "mine", tmp_path / "shared"
    mine.mkdir(); shared.mkdir()
    (shared / "Tool.lnk").write_text("x")
    assert [p.name for p in itemat.matches([str(mine), str(shared)], "Tool.lnk")] == ["Tool.lnk"]
    assert itemat.matches(["::{20D04FE0-3AEA-1069-A2D8-08002B30309D}", str(tmp_path / "gone")], "This PC") == []
    assert itemat.matches([str(mine)], None) == [] and itemat.matches([str(mine)], "") == []


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", '{"label": 5, "folders": 7}'])
def test_a_confusing_answer_from_windows_is_simply_nothing_found(text):
    spot = itemat.parse_spot(text)
    assert spot.label is None and spot.folders == []


def test_a_single_folder_may_come_as_a_plain_string():
    spot = itemat.parse_spot('{"label": " a.txt ", "where": "explorer", "folders": "C:\\\\x"}')
    assert (spot.label, spot.folders, spot.where) == ("a.txt", ["C:\\x"], "explorer")


# ---- over HTTP

class FakeFinder:
    def __init__(self):
        self.asked, self.spot = [], itemat.Spot()

    def find(self, px, py):
        self.asked.append((px, py))
        return self.spot


@pytest.fixture
def world(project, monkeypatch):
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "true")
    monkeypatch.setenv("HANDOFF_ACCOUNTS__SIGN_IN", "password")
    settings = load_settings(project / "config.yaml")
    app = create_app(settings, DemoPlanner())
    app.state.screen._grab = lambda w, q: JPEG
    finder = FakeFinder()
    app.state.screen.items = finder
    monkeypatch.setattr("handoff.screen.manager.capture.screen_size", lambda: (1920, 1080))
    admin = TestClient(create_admin_app(settings, app.state.accounts, app.state.manager), base_url="http://127.0.0.1:8766")
    assert admin.post("/admin/setup", json={"name": "Afza", "password": "correct horse"}, headers=H).status_code == 200
    phone = TestClient(app)

    def pair(name):
        code = admin.post("/admin/pairing", headers=H).json()["code"]
        return {"x-token": phone.post("/pair/claim", json={"code": code, "name": name}).json()["token"]}

    people = {"Asha": pair("Asha's phone")}   # the first phone is the Admin phone: it may look without asking
    return phone, people["Asha"], finder, project, app


def look(phone, who):
    assert phone.post("/api/screen/start", headers=who).json()["state"] == "viewing"


def test_nobody_who_is_not_looking_can_ask_what_is_on_the_screen(world):
    phone, who, finder, *_ = world
    refused = phone.post("/api/screen/item", json={"x": 0.5, "y": 0.5}, headers=who)
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "screen_not_allowed"
    assert finder.asked == []


def test_a_touch_outside_the_screen_is_refused(world):
    phone, who, finder, *_ = world
    look(phone, who)
    for bad in ({"x": 1.2, "y": 0.5}, {"x": 0.5, "y": -0.1}, {"x": "a", "y": 0.5}, {}):
        assert phone.post("/api/screen/item", json=bad, headers=who).status_code == 422
    assert finder.asked == []


def test_a_file_under_the_finger_is_named_with_its_real_path_and_may_be_downloaded(world):
    phone, who, finder, project, _ = world
    (project / "workspace" / "Report.docx").write_text("hello")
    finder.spot = itemat.Spot("Report.docx", [str(project / "workspace")], "explorer")
    look(phone, who)
    out = phone.post("/api/screen/item", json={"x": 0.5, "y": 0.25}, headers=who).json()
    assert finder.asked == [(int(0.5 * 1919), int(0.25 * 1079))]   # the touch became real pixels of the PC's screen
    assert out["label"] == "Report.docx" and len(out["items"]) == 1
    item = out["items"][0]
    assert item["name"] == "Report.docx" and item["folder"] is False and item["size"] == 5 and item["allowed"] is True and item["needs_access"] == ""
    assert Path(item["path"]).samefile(project / "workspace" / "Report.docx")


def test_a_file_in_a_folder_that_is_not_allowed_says_which_folder_to_ask_for(world, tmp_path_factory):
    phone, who, finder, *_ = world
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "Photo.jpg").write_text("x")
    finder.spot = itemat.Spot("Photo.jpg", [str(outside)], "explorer")
    look(phone, who)
    item = phone.post("/api/screen/item", json={"x": 0.1, "y": 0.1}, headers=who).json()["items"][0]
    assert item["allowed"] is False and Path(item["needs_access"]).samefile(outside) and item["reason"]


def test_a_protected_place_cannot_even_be_asked_for(world):
    phone, who, finder, project, _ = world
    (project / "data").mkdir(exist_ok=True)
    (project / "data" / "audit.log").write_text("secret")
    finder.spot = itemat.Spot("audit.log", [str(project / "data")], "explorer")
    look(phone, who)
    item = phone.post("/api/screen/item", json={"x": 0.1, "y": 0.1}, headers=who).json()["items"][0]
    assert item["allowed"] is False and item["needs_access"] == "" and "protected" in item["reason"]


def test_something_that_is_not_a_file_gives_an_empty_list(world):
    phone, who, finder, *_ = world
    finder.spot = itemat.Spot(None, [], "")
    look(phone, who)
    assert phone.post("/api/screen/item", json={"x": 0.3, "y": 0.3}, headers=who).json() == {"label": None, "items": []}


def test_windows_being_slow_is_a_clear_message_not_a_crash(world):
    phone, who, finder, *_ = world

    def slow(px, py):
        raise itemat.FinderError("Windows took too long to say what is there. Try again.")

    finder.find = slow
    look(phone, who)
    out = phone.post("/api/screen/item", json={"x": 0.3, "y": 0.3}, headers=who)
    assert out.status_code == 422 and "took too long" in out.json()["detail"]["message"]


# ---- a folder as one zip

def test_a_folder_comes_as_one_zip_the_phone_can_then_download(world):
    phone, who, _, project, _ = world
    folder = project / "workspace" / "Trip"
    (folder / "photos").mkdir(parents=True)
    (folder / "plan.txt").write_text("rome")
    (folder / "photos" / "a.jpg").write_text("pic")
    made = phone.post("/api/browse/offer_folder", params={"path": str(folder)}, headers=who)
    assert made.status_code == 200
    info = made.json()
    assert info["name"] == "Trip.zip" and info["size"] > 0
    with zipfile.ZipFile(info["path"]) as z:
        assert sorted(z.namelist()) == ["Trip/photos/a.jpg", "Trip/plan.txt"]
    link = phone.post("/api/files/link", params={"path": info["path"]}, headers=who)
    assert link.status_code == 200 and link.json()["name"] == "Trip.zip"
    got = phone.get(link.json()["url"])
    assert got.status_code == 200 and got.content[:2] == b"PK"


def test_a_folder_outside_the_allowed_ones_is_not_packed(world, tmp_path_factory):
    phone, who, *_ = world
    outside = tmp_path_factory.mktemp("elsewhere")
    (outside / "x.txt").write_text("x")
    refused = phone.post("/api/browse/offer_folder", params={"path": str(outside)}, headers=who)
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "needs_access"


def test_a_file_is_not_a_folder(world):
    phone, who, _, project, _ = world
    (project / "workspace" / "a.txt").write_text("x")
    assert phone.post("/api/browse/offer_folder", params={"path": str(project / "workspace" / "a.txt")}, headers=who).status_code == 404


def test_a_folder_over_the_download_limit_is_refused_and_leaves_nothing_behind(world):
    phone, who, _, project, app = world
    folder = project / "workspace" / "Big"
    folder.mkdir()
    (folder / "one.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    app.state.manager._settings.agent.max_download_mb = 1
    refused = phone.post("/api/browse/offer_folder", params={"path": str(folder)}, headers=who)
    assert refused.status_code == 413 and "larger than" in refused.json()["detail"]
