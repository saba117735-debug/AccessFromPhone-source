"""The file browser: listing, paging, safety, streaming and downloading, and asking for it in chat."""
import os
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff import browse
from handoff.planner import quick_for
from handoff.server import create_app

WEB = Path(__file__).resolve().parents[1] / "src" / "handoff" / "web"


@pytest.fixture
def workspace(settings):
    return settings.agent.allowed_folders[0]


@pytest.fixture
def client(settings):
    app = create_app(settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    c.app = app
    return c


@pytest.fixture
def tree(workspace):
    (workspace / "Reports" / "2024").mkdir(parents=True)
    (workspace / "Reports" / "2024" / "q1.txt").write_text("hello", encoding="utf-8")
    (workspace / "Reports" / "summary.pdf").write_bytes(b"%PDF-1.4")
    (workspace / "Photos").mkdir()
    (workspace / "readme.md").write_text("# hi", encoding="utf-8")
    (workspace / "big.bin").write_bytes(b"x" * 5000)
    (workspace / "movie.mp4").write_bytes(bytes(range(256)) * 40)
    (workspace / ".secret").write_text("hidden", encoding="utf-8")
    now = time.time()
    os.utime(workspace / "readme.md", (now - 500, now - 500))
    os.utime(workspace / "big.bin", (now - 100, now - 100))
    return workspace


def names(data):
    return [e["name"] for e in data["entries"]]


# -- listing --------------------------------------------------------------------------------

def test_a_folder_lists_folders_first_then_files_by_name(client, tree):
    data = client.get("/api/browse", params={"path": str(tree)}).json()
    assert names(data) == ["Photos", "Reports", "big.bin", "movie.mp4", "readme.md"]
    assert (data["folders"], data["files"], data["total"]) == (2, 3, 5)
    assert data["entries"][0]["kind"] == "folder" and data["entries"][4]["type"] == "text"
    assert data["entries"][3]["type"] == "video"


def test_hidden_files_are_left_out_unless_asked_for(client, tree):
    assert ".secret" not in names(client.get("/api/browse", params={"path": str(tree)}).json())
    assert ".secret" in names(client.get("/api/browse", params={"path": str(tree), "hidden": "true"}).json())


def test_sorting_by_date_and_size_and_reversing(client, tree):
    by_date = names(client.get("/api/browse", params={"path": str(tree), "sort": "modified", "order": "desc"}).json())
    assert by_date.index("movie.mp4") < by_date.index("big.bin") < by_date.index("readme.md")  # newest file first; folders still lead
    assert by_date[:2] == ["Photos", "Reports"] or set(by_date[:2]) == {"Photos", "Reports"}
    by_size = names(client.get("/api/browse", params={"path": str(tree), "sort": "size", "order": "desc"}).json())
    assert by_size[2:] == ["movie.mp4", "big.bin", "readme.md"]  # 10,240 bytes, 5,000 bytes, a few
    reverse = names(client.get("/api/browse", params={"path": str(tree), "order": "desc"}).json())
    assert reverse[2:] == ["readme.md", "movie.mp4", "big.bin"]


def test_searching_inside_a_folder(client, tree):
    found = client.get("/api/browse", params={"path": str(tree), "q": "REPORT"}).json()
    assert names(found) == ["Reports"]
    assert client.get("/api/browse", params={"path": str(tree), "q": "zzz"}).json()["total"] == 0


def test_a_big_folder_is_paged_and_nothing_is_lost(client, workspace):
    big = workspace / "many"
    big.mkdir()
    for i in range(530):
        (big / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    seen, offset = [], 0
    while True:
        page = client.get("/api/browse", params={"path": str(big), "offset": offset, "limit": 200}).json()
        seen += names(page)
        if not page["more"]:
            break
        offset += 200
    assert len(seen) == 530 and seen == sorted(seen) and page["total"] == 530
    assert len(client.get("/api/browse", params={"path": str(big), "limit": 99999}).json()["entries"]) == browse.PAGE_MAX  # a page is capped


def test_a_folder_over_the_scan_limit_says_so(workspace, monkeypatch):
    monkeypatch.setattr(browse, "SCAN_LIMIT", 10)
    for i in range(25):
        (workspace / f"n{i}.txt").write_text("x", encoding="utf-8")
    data = browse.list_folder(workspace)
    assert data["truncated"] and data["total"] <= 10


def test_a_vanished_or_unreadable_entry_does_not_break_the_listing(workspace, monkeypatch):
    (workspace / "a.txt").write_text("x", encoding="utf-8")
    (workspace / "b.txt").write_text("x", encoding="utf-8")
    real = os.DirEntry.stat if hasattr(os.DirEntry, "stat") else None
    scan = os.scandir

    class Flaky:
        def __init__(self, entry):
            self._e = entry
            self.name = entry.name

        def is_dir(self):
            return self._e.is_dir()

        def stat(self):
            if self.name == "a.txt":
                raise OSError("gone")
            return self._e.stat()

    class Scan:
        def __init__(self, path):
            self._s = scan(path)

        def __enter__(self):
            return (Flaky(e) for e in self._s.__enter__())

        def __exit__(self, *a):
            return self._s.__exit__(*a)

    monkeypatch.setattr(browse.os, "scandir", Scan)
    assert [e["name"] for e in browse.list_folder(workspace)["entries"]] == ["b.txt"]
    assert real is not None or True


# -- where you are ----------------------------------------------------------------------------

def test_the_start_lists_the_places_browsing_may_begin(client, workspace, settings):
    data = client.get("/api/browse").json()
    assert data["root"] and data["parent"] is None
    assert [Path(e["path"]) for e in data["entries"]] == [workspace.resolve()]
    assert data["entries"][0]["access"] == "writable" and data["entries"][0]["type"] == "root"


def test_breadcrumbs_and_the_way_up(client, tree):
    deep = client.get("/api/browse", params={"path": str(tree / "Reports" / "2024")}).json()
    assert [c["name"] for c in deep["crumbs"]] == [tree.name, "Reports", "2024"]
    assert Path(deep["parent"]) == (tree / "Reports").resolve()
    top = client.get("/api/browse", params={"path": str(tree)}).json()
    assert len(top["crumbs"]) == 1 and top["parent"] == ""  # up from a place goes back to the list of places


def test_a_relative_path_means_the_workspace(client, tree):
    assert "Reports" in names(client.get("/api/browse", params={"path": "."}).json())
    assert "q1.txt" in names(client.get("/api/browse", params={"path": "Reports/2024"}).json())


# -- safety -----------------------------------------------------------------------------------

def test_dot_dot_cannot_leave_the_allowed_folders(client, tree):
    res = client.get("/api/browse", params={"path": str(tree / "Reports" / ".." / ".." / "..")})
    assert res.status_code == 403 and res.json()["detail"]["code"] == "needs_access"
    assert client.get("/api/browse", params={"path": "../.."}).status_code == 403


def test_a_folder_that_is_not_allowed_asks_for_access_and_lists_nothing(client, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "private.txt").write_text("no", encoding="utf-8")
    res = client.get("/api/browse", params={"path": str(other)})
    assert res.status_code == 403 and res.json()["detail"]["folder"]
    assert "private.txt" not in res.text


def test_after_the_folder_is_allowed_it_can_be_browsed_and_it_is_a_start_place(client, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "now.txt").write_text("ok", encoding="utf-8")
    assert client.post("/api/access/grant", json={"path": str(other), "change": False}).status_code == 200
    assert names(client.get("/api/browse", params={"path": str(other)}).json()) == ["now.txt"]
    assert str(other.resolve()) in [e["path"] for e in client.get("/api/browse").json()["entries"]]


def test_a_missing_folder_and_a_file_are_not_folders(client, tree):
    assert client.get("/api/browse", params={"path": str(tree / "nope")}).status_code == 404
    assert client.get("/api/browse", params={"path": str(tree / "readme.md")}).status_code == 404


def test_it_needs_the_phones_token(settings, tree):
    assert TestClient(create_app(settings)).get("/api/browse").status_code == 401


def test_opening_folders_is_recorded_in_the_audit_log(client, tree):
    client.get("/api/browse", params={"path": str(tree)})
    client.get("/api/browse", params={"path": str(tree), "offset": 200})  # a second page of the same visit: not a second entry
    client.post("/api/browse/stream", params={"path": str(tree / "movie.mp4")})
    events = [e["event"] for e in client.get("/api/audit").json()]
    assert events.count("browsed_folder") == 1 and "streamed" in events


# -- streaming ----------------------------------------------------------------------------------

def test_a_video_is_played_through_a_link_that_supports_ranges(client, tree):
    link = client.post("/api/browse/stream", params={"path": str(tree / "movie.mp4")}).json()
    assert link["mime"] == "video/mp4" and re.fullmatch(r"/stream/[\w-]{20,}", link["url"])
    plain = TestClient(client.app)  # a player sends no token
    whole = plain.get(link["url"])
    assert whole.status_code == 200 and len(whole.content) == 256 * 40 and whole.headers["content-type"].startswith("video/mp4")
    part = plain.get(link["url"], headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == bytes(range(10, 20))  # seeking works


def test_only_video_and_audio_can_be_streamed(client, tree):
    assert client.post("/api/browse/stream", params={"path": str(tree / "readme.md")}).status_code == 403


def test_a_made_up_or_expired_link_is_refused(client, tree):
    plain = TestClient(client.app)
    assert plain.get("/stream/not-a-real-link").status_code == 404
    link = client.post("/api/browse/stream", params={"path": str(tree / "movie.mp4")}).json()["url"]
    manager = client.app.state.manager
    key = link.rsplit("/", 1)[1]
    path, mime, _ = manager._streams[key]
    manager._streams[key] = (path, mime, time.monotonic() - 1)
    assert plain.get(link).status_code == 404


def test_a_link_stops_working_when_its_folder_is_taken_back(client, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "clip.mp3").write_bytes(b"abc")
    client.post("/api/access/grant", json={"path": str(other), "change": False})
    link = client.post("/api/browse/stream", params={"path": str(other / "clip.mp3")}).json()["url"]
    plain = TestClient(client.app)
    assert plain.get(link).status_code == 200
    client.app.state.manager.revoke_access(str(other))
    assert plain.get(link).status_code == 404


def test_streaming_a_folder_outside_the_allowed_ones_asks_first(client, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "clip.mp4").write_bytes(b"abc")
    assert client.post("/api/browse/stream", params={"path": str(other / "clip.mp4")}).status_code == 403


def test_the_number_of_open_links_is_capped(client, tree):
    manager = client.app.state.manager
    for _ in range(manager.STREAM_MAX + 20):
        manager.stream_link(str(tree / "movie.mp4"))
    assert len(manager._streams) <= manager.STREAM_MAX


# -- downloading ---------------------------------------------------------------------------------

def test_tapping_download_offers_that_one_file(client, tree):
    assert client.get("/api/files", params={"path": str(tree / "big.bin")}).status_code == 403  # not offered yet
    assert client.post("/api/browse/offer", params={"path": str(tree / "big.bin")}).status_code == 200
    got = client.get("/api/files", params={"path": str(tree / "big.bin")})
    assert got.status_code == 200 and len(got.content) == 5000
    assert client.get("/api/files", params={"path": str(tree / "movie.mp4")}).status_code == 403  # only that one


def test_a_file_over_the_download_limit_is_refused_with_the_reason(client, tree, settings):
    settings.agent.max_download_mb = 1
    (tree / "huge.dat").write_bytes(b"0" * (1024 * 1024 + 1))
    res = client.post("/api/browse/offer", params={"path": str(tree / "huge.dat")})
    assert res.status_code == 413 and "limit" in res.json()["detail"]


# -- asking for it in chat ----------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "open the folder Reports", "browse my folders", "browse Reports", "explore the folder Photos",
    "please open folder Reports", "let me browse my files", "can you open my folders",
])
def test_these_requests_open_the_browser(text, tree):
    plan = quick_for(text, None)
    assert plan is not None and len(plan.steps) == 1 and plan.steps[0].browse and plan.steps[0].editor, text


def test_a_named_folder_is_what_gets_opened(tree):
    plan = quick_for(f'open the folder "{tree}"', None)
    assert plan.steps[0].path == str(tree)
    assert quick_for(f"open {tree}", None).steps[0].path == str(tree)  # a bare path that is a folder
    assert quick_for(f"open the folder Reports in {tree}", None).steps[0].path == str(tree / "Reports")  # a folder inside another
    assert quick_for(f"open readme.md in {tree}", None) is None or not quick_for(f"open readme.md in {tree}", None).steps[0].browse  # a file inside it


@pytest.mark.parametrize("text", [
    "open readme.md", "open notes.txt in the editor", "show me the photos in Photos", "open photo.png",
    "list the files in Reports", "what is in Reports", "open the file report.pdf full screen",
])
def test_these_stay_with_the_other_requests(text, tree):
    plan = quick_for(text, None)
    assert plan is None or not plan.steps or not plan.steps[0].browse, text


def test_a_browse_request_ends_with_a_button_that_opens_the_browser(settings, tree):
    from handoff.jobs import JobManager
    from handoff.planner import DemoPlanner
    manager = JobManager(settings, DemoPlanner())
    job = manager.submit("open the folder Reports")
    manager.join_background()
    done = manager.get(job.id) if hasattr(manager, "get") else job
    data = done.to_dict()
    assert data["open_mode"] == "folder" and data["open"].endswith("Reports")


# -- the screen itself ----------------------------------------------------------------------------

UI = (WEB / "files.js").read_text(encoding="utf-8")
CSS = (WEB / "files.css").read_text(encoding="utf-8")
APP_CSS = (WEB / "app.css").read_text(encoding="utf-8")
APP = (WEB / "app.js").read_text(encoding="utf-8")


def test_the_browser_files_are_served_and_loaded_by_the_page(settings):
    c = TestClient(create_app(settings))
    for name in ("files.js", "files.css"):
        res = c.get(f"/static/{name}")
        assert res.status_code == 200 and len(res.content) > 1000 and res.headers["cache-control"] == "no-cache", name
    assert '"/static/files.js"' in APP and '"/static/files.css"' in APP


def test_every_icon_the_browser_uses_is_drawn():
    block = UI[UI.index("const ICONS = {"):UI.index("function icon(")]
    drawn = set(re.findall(r"^\s{4}(\w+):", block, re.M))
    names_in_calls = re.findall(r'\bicon\(([^()]*)\)', UI) + re.findall(r'\bitem\("(\w+)"', UI)  # icon("x"), icon(a ? "x" : "y"), item("x", ...)
    used = {w for call in names_in_calls for w in re.findall(r'"(\w+)"', call)} | browse_types()
    assert used and used <= drawn, f"icons used but not drawn: {sorted(used - drawn)}"


def browse_types():
    """Every type the PC can name for a file (`type_of`) must have an icon, or that file would show a blank one."""
    return {browse.type_of(n) for n in ("a.png", "a.pdf", "a.mp4", "a.mp3", "a.zip", "a.xlsx", "a.docx", "a.exe", "a.txt", "a.zzz")} | {"folder", "root"}


def test_every_style_class_the_browser_uses_has_a_style():
    used = set()
    for found in re.findall(r'class: "([^"]+)"', UI):
        used.update(c for c in found.split() if c.startswith("fb-"))
    used.update(re.findall(r'"(fb-[\w-]+)"', UI))
    used.discard("fb-ic t-")
    missing = sorted(c for c in used if not re.search(r"\." + re.escape(c) + r"(?![\w-])", CSS + APP_CSS))
    assert not missing, f"classes with no style: {missing}"


def test_file_names_only_reach_the_page_as_text():
    assert UI.count("innerHTML") == 1  # only the fixed icon drawings
    assert "insertAdjacentHTML" not in UI and "document.write" not in UI and "eval(" not in UI
    assert "fetch(" not in UI and "XMLHttpRequest" not in UI and "localStorage" not in UI  # every request goes through ctx, which carries the token
