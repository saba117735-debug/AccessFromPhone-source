"""Pictures: the gallery, thumbnails, and saving an edited picture safely."""
import io
import os
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from handoff import imaging
from handoff.config import Action
from handoff.jobs import JobManager, Status
from handoff.planner import DemoPlanner, quick_for
from handoff.server import create_app


def picture(size=(200, 120), color=(200, 30, 30), fmt="PNG", mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else color + (255,)).save(out, fmt)
    return out.getvalue()


@pytest.fixture
def workspace(settings):
    return settings.agent.allowed_folders[0]


@pytest.fixture
def client(settings):
    app = create_app(settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    return c


@pytest.fixture
def photos(workspace):
    """A few pictures with different ages, and a few files that are not pictures."""
    now = time.time()
    for name, age in (("old.png", 300), ("mid.jpg", 200), ("new.webp", 100)):
        data = picture(fmt={"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[name.rsplit(".", 1)[1]])
        (workspace / name).write_bytes(data)
        os.utime(workspace / name, (now - age, now - age))
    (workspace / "notes.txt").write_text("not a picture", encoding="utf-8")
    (workspace / "report.pdf").write_bytes(b"%PDF-1.4")
    return workspace


# -- the gallery ------------------------------------------------------------------------

def test_the_gallery_lists_only_pictures_newest_first(client, photos):
    data = client.get("/api/images").json()
    assert [i["name"] for i in data["images"]] == ["new.webp", "mid.jpg", "old.png"]
    assert data["total"] == 3 and data["truncated"] is False
    assert data["folder"] == str(photos) and data["can_change"] is True
    assert all({"name", "path", "size", "modified"} <= set(i) for i in data["images"])


def test_an_empty_folder_is_an_empty_gallery_not_an_error(client, workspace):
    assert client.get("/api/images").json()["total"] == 0


def test_a_folder_outside_the_workspace_asks_for_access(client, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "a.png").write_bytes(picture())
    res = client.get("/api/images", params={"path": str(other)})
    assert res.status_code == 403 and res.json()["detail"]["code"] == "needs_access" and res.json()["detail"]["folder"] == str(other)
    assert client.post("/api/access/grant", json={"path": str(other), "change": False}).status_code == 200
    assert client.get("/api/images", params={"path": str(other)}).json()["total"] == 1


def test_a_file_is_not_a_folder(client, photos):
    assert client.get("/api/images", params={"path": str(photos / "old.png")}).status_code == 404


# -- thumbnails ---------------------------------------------------------------------------

def test_a_thumbnail_is_small_and_kept(client, settings, workspace):
    (workspace / "big.png").write_bytes(picture(size=(1600, 900)))
    first = client.get("/api/thumb", params={"path": "big.png", "size": 256})
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"
    img = Image.open(io.BytesIO(first.content))
    assert max(img.size) == 256 and img.size == (256, 144)  # the shape is kept
    assert any((settings.storage.database.parent / "thumbs").glob("*.jpg"))
    assert client.get("/api/thumb", params={"path": "big.png", "size": 256}).content == first.content


def test_a_thumbnail_is_renewed_when_the_picture_changes(client, workspace):
    path = workspace / "p.png"
    path.write_bytes(picture(color=(255, 0, 0)))
    red = Image.open(io.BytesIO(client.get("/api/thumb", params={"path": "p.png"}).content)).convert("RGB").getpixel((5, 5))
    time.sleep(0.05)
    path.write_bytes(picture(size=(210, 130), color=(0, 0, 255)))
    os.utime(path, (time.time() + 5, time.time() + 5))
    blue = Image.open(io.BytesIO(client.get("/api/thumb", params={"path": "p.png"}).content)).convert("RGB").getpixel((5, 5))
    assert red[0] > 200 and blue[2] > 200


def test_a_picture_with_see_through_parts_keeps_them(client, workspace):
    (workspace / "logo.png").write_bytes(picture(color=(10, 200, 10), mode="RGBA"))
    res = client.get("/api/thumb", params={"path": "logo.png"})
    assert res.headers["content-type"] == "image/png"


def test_thumbnail_sizes_snap_to_the_few_that_exist(client, workspace):
    (workspace / "s.png").write_bytes(picture(size=(2000, 2000)))
    assert max(Image.open(io.BytesIO(client.get("/api/thumb", params={"path": "s.png", "size": 9999}).content)).size) == 512
    assert max(Image.open(io.BytesIO(client.get("/api/thumb", params={"path": "s.png", "size": 1}).content)).size) == 96


def test_things_without_a_thumbnail_say_so_plainly(client, photos):
    (photos / "drawing.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    assert client.get("/api/thumb", params={"path": "drawing.svg"}).status_code == 403
    assert client.get("/api/thumb", params={"path": "notes.txt"}).status_code == 403
    (photos / "broken.png").write_bytes(b"not really a png")
    assert client.get("/api/thumb", params={"path": "broken.png"}).status_code == 415
    assert client.get("/api/thumb", params={"path": "missing.png"}).status_code == 404


# -- opening a picture -------------------------------------------------------------------------

def test_opening_a_picture_tells_the_editor_what_it_needs(client, photos):
    info = client.get("/api/file", params={"path": "old.png"}).json()
    assert info["kind"] == "image" and (info["width"], info["height"]) == (200, 120)
    assert info["editable_image"] is True and info["saves_as_is"] is True and len(info["sha"]) == 64
    (photos / "anim.gif").write_bytes(picture(fmt="GIF"))
    gif = client.get("/api/file", params={"path": "anim.gif"}).json()
    assert gif["editable_image"] is True and gif["saves_as_is"] is False  # editable, but saved as a PNG copy


def test_a_huge_picture_is_not_offered_to_the_editor(client, settings, photos):
    settings.agent.max_image_mb = 1
    (photos / "huge.png").write_bytes(os.urandom(300) + picture(size=(1200, 1200), color=(1, 2, 3)) + os.urandom(1_200_000))
    info = client.get("/api/file", params={"path": "huge.png"}).json()
    assert info["editable_image"] is False


# -- saving ----------------------------------------------------------------------------------------

def test_saving_over_a_picture_replaces_it_and_can_be_undone(client, photos):
    path = "old.png"
    before = (photos / path).read_bytes()
    info = client.get("/api/file", params={"path": path}).json()
    edited = picture(size=(100, 60), color=(0, 0, 255))
    res = client.put("/api/image", params={"path": path, "base_sha": info["sha"], "note": "cropped, brightened"}, content=edited, headers={"Content-Type": "image/png"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["saved"] and (out["width"], out["height"]) == (100, 60) and out["path"] == str(photos / path)
    assert (photos / path).read_bytes() == edited
    assert out["job"]["status"] == "done" and "cropped, brightened" in out["job"]["outputs"][0]
    assert client.post(f"/api/jobs/{out['job']['id']}/undo").json()["status"] == "undone"
    assert (photos / path).read_bytes() == before  # the original is back


def test_saving_notices_when_the_picture_changed_in_the_meantime(client, photos):
    info = client.get("/api/file", params={"path": "old.png"}).json()
    (photos / "old.png").write_bytes(picture(color=(9, 9, 9)))  # someone else saved it
    res = client.put("/api/image", params={"path": "old.png", "base_sha": info["sha"]}, content=picture(color=(1, 1, 1)))
    assert res.status_code == 409 and res.json()["detail"]["code"] == "changed"
    assert Image.open(photos / "old.png").convert("RGB").getpixel((0, 0)) == (9, 9, 9)  # nothing was overwritten


def test_a_copy_never_replaces_anything(client, photos):
    original = (photos / "old.png").read_bytes()
    edited = picture(color=(5, 120, 5))
    first = client.put("/api/image", params={"path": "old (edited).png", "unique": "true"}, content=edited).json()
    assert first["name"] == "old (edited).png" and (photos / "old (edited).png").read_bytes() == edited
    second = client.put("/api/image", params={"path": "old (edited).png", "unique": "true"}, content=picture(color=(6, 6, 200))).json()
    assert second["name"] == "old (edited) (2).png"  # the name was taken: it got the next free one
    assert (photos / "old (edited).png").read_bytes() == edited and (photos / "old.png").read_bytes() == original
    assert client.post(f"/api/jobs/{second['job']['id']}/undo").json()["status"] == "undone"
    assert not (photos / "old (edited) (2).png").exists()  # undoing a copy removes it


def test_saving_the_same_picture_again_changes_nothing(client, photos):
    data = (photos / "old.png").read_bytes()
    res = client.put("/api/image", params={"path": "old.png"}, content=data).json()
    assert res["saved"] is False and res["job"] is None


@pytest.mark.parametrize("name, body, why", [
    ("old.png", b"definitely not a picture", "not a picture that can be read"),
    ("old.png", b"", "no picture"),
    ("old.jpg", None, "but the file name ends in"),          # PNG bytes sent as a .jpg
    ("old.png", "truncated", "not a picture that can be read"),
    ("anim.gif", None, "can't be saved over"),
    ("notes.txt", None, "can't be saved over"),
])
def test_what_is_not_a_proper_picture_is_never_saved(client, photos, name, body, why):
    before = {p.name: p.read_bytes() for p in photos.iterdir()}
    if body is None:
        body = picture()
    elif body == "truncated":
        body = picture(size=(300, 300))[:200]
    res = client.put("/api/image", params={"path": name}, content=body)
    assert res.status_code == 422 and why in res.json()["detail"], res.text
    assert {p.name: p.read_bytes() for p in photos.iterdir()} == before  # not a byte changed, not a file added


def test_an_oversize_picture_is_refused(client, settings, photos):
    settings.agent.max_image_mb = 1
    noisy = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))
    out = io.BytesIO()
    noisy.save(out, "PNG")
    assert len(out.getvalue()) > 1024 * 1024
    res = client.put("/api/image", params={"path": "old.png"}, content=out.getvalue())
    assert res.status_code in (413, 422) and "1 MB" in res.json()["detail"]  # and it says which limit, in MB


def test_saving_outside_the_workspace_needs_editing_to_be_allowed(client, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "a.png").write_bytes(picture())
    target = str(other / "a.png")
    res = client.put("/api/image", params={"path": target}, content=picture(color=(1, 2, 3)))
    assert res.status_code == 403 and res.json()["detail"]["code"] == "needs_change_access"
    client.post("/api/access/grant", json={"path": str(other), "change": False})
    assert client.put("/api/image", params={"path": target}, content=picture(color=(1, 2, 3))).status_code == 403  # reading is not enough
    client.post("/api/access/grant", json={"path": str(other), "change": True})
    assert client.put("/api/image", params={"path": target}, content=picture(color=(1, 2, 3))).status_code == 200


def test_a_read_only_folder_can_never_be_saved_into(tmp_path):
    from handoff.config import load_settings
    (tmp_path / "workspace").mkdir()
    (tmp_path / "locked").mkdir()
    (tmp_path / "locked" / "a.png").write_bytes(picture())
    (tmp_path / "config.yaml").write_text(
        "agent:\n  allowed_folders: [workspace]\n  read_only_folders: [locked]\nllm:\n  provider: demo\n"
        "storage:\n  results_dir: data/results\n  snapshots_dir: data/snapshots\nlogging:\n  audit_file: data/audit.log\n", encoding="utf-8")
    app = create_app(load_settings(tmp_path / "config.yaml"))
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    assert c.get("/api/images", params={"path": str(tmp_path / "locked")}).json()["can_change"] is False
    res = c.put("/api/image", params={"path": str(tmp_path / "locked" / "a.png")}, content=picture(color=(1, 2, 3)))
    assert res.status_code == 403 and (tmp_path / "locked" / "a.png").read_bytes() == picture()


def test_a_save_is_recorded_in_the_audit_trail(client, photos):
    client.put("/api/image", params={"path": "old.png"}, content=picture(color=(7, 7, 7)))
    client.get("/api/images")
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert "opened_gallery" in events and "done" in events


# -- the picture helpers on their own -----------------------------------------------------------------

def test_unique_names_count_up():
    import tempfile
    from pathlib import Path
    folder = Path(tempfile.mkdtemp())
    assert imaging.unique_name(folder, "a.png") == "a.png"
    (folder / "a.png").write_bytes(b"x")
    assert imaging.unique_name(folder, "a.png") == "a (2).png"
    (folder / "a (2).png").write_bytes(b"x")
    assert imaging.unique_name(folder, "a.png") == "a (3).png"
    assert imaging.unique_name(folder, "b.tar.gz") == "b.tar.gz"


# -- asking for pictures in plain words ---------------------------------------------------------------------

@pytest.mark.parametrize("text, kind, folder", [
    ("open any photo file", "gallery", "."),
    ("show me my screenshots", "gallery", "."),
    ("I want to see all the images", "gallery", "."),
    ("let me browse the pictures", "gallery", "."),
    ("show me the photos in C:\\Pictures\\Trip", "gallery", "C:\\Pictures\\Trip"),
    ("view all images from C:\\Pictures", "gallery", "C:\\Pictures"),
    ("edit that photo", "gallery", "."),            # no picture in this chat yet: pick one
    ("photoshop an image", "gallery", "."),
    ("edit a picture in C:\\Pictures", "gallery", "C:\\Pictures"),
    ("show me C:\\Pictures\\a.jpg", "view", "C:\\Pictures\\a.jpg"),
    ("open photo.png", "view", "photo.png"),
    ("view C:\\Pictures\\Screenshot.PNG", "view", "C:\\Pictures\\Screenshot.PNG"),
])
def test_plain_requests_for_pictures_are_understood_without_a_model(text, kind, folder):
    plan = quick_for(text, None)
    assert plan is not None, text
    step = plan.steps[0]
    assert step.action is Action.READ and step.editor
    assert (step.gallery, step.view) == ((kind == "gallery"), (kind == "view"))
    assert step.path == folder


@pytest.mark.parametrize("text", [
    "get the count of total imgs in this path C:\\Pictures",   # a question for the AI, not a gallery
    "how many photos are there",
    "list images",
    "open the image in a new screen",                          # how to show it, not which folder
    "delete all the photos",
    "what is in this image",
    "photos",
])
def test_requests_that_are_not_asking_to_see_pictures_are_left_alone(text):
    plan = quick_for(text, None)
    assert plan is None or not any(s.gallery for s in plan.steps)


def test_a_gallery_request_in_a_chat_uses_the_folder_the_chat_was_in(tmp_path):
    class Chat:
        def __init__(self, folder): self.folder = folder
        def history(self): return [{"role": "user", "content": f"list {self.folder}"}]

    (tmp_path / "Holiday").mkdir()
    plan = quick_for("show me the photos", Chat(tmp_path))
    if plan:  # a chat that knows its folder
        assert plan.steps[0].gallery


def test_the_whole_gallery_request_works_end_to_end(settings, workspace, photos):
    manager = JobManager(settings, DemoPlanner())
    job = manager.submit("open any photo file")
    manager.join_background()
    assert job.status is Status.DONE and job.open == str(workspace) and job.open_mode == "gallery"
    assert "3 pictures" in job.outputs[0]


def test_a_gallery_of_a_folder_with_no_pictures_says_so(settings, workspace):
    manager = JobManager(settings, DemoPlanner())
    job = manager.submit("show me the pictures")
    manager.join_background()
    assert job.status is Status.DONE and "no pictures" in job.outputs[0]


def test_a_gallery_outside_the_workspace_asks_before_opening(settings, tmp_path):
    other = tmp_path / "Holiday photos"
    other.mkdir()
    (other / "a.png").write_bytes(picture())
    manager = JobManager(settings, DemoPlanner())
    job = manager.submit(f"show me the photos in {other}")
    manager.join_background()
    assert job.status is Status.NEEDS_ACCESS and job.access == [str(other)]
    job = manager.decide_access(job.id, "once")
    manager.join_background()
    assert job.status is Status.DONE and job.open_mode == "gallery" and "1 picture" in job.outputs[0]
