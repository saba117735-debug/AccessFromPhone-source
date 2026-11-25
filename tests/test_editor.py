"""Editing a file by hand from the phone: open, change, review, save, undo. Safe by default."""
from audit_helper import audit_text
from handoff.db import Database
from handoff.grants import GrantStore
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from handoff.config import Action
from handoff.jobs import FileChanged, JobManager, Status
from handoff.planner import quick_open_editor, quick_request
from handoff.server import create_app


@pytest.fixture
def ws(ro_settings):
    folder = ro_settings.agent.allowed_folders[0]
    (folder / "app.cfg").write_bytes(b"name=demo\r\nport=8080\r\nmode=dev\r\n")  # a Windows file: CRLF line endings
    return folder


@pytest.fixture
def client(ro_settings):
    app = create_app(ro_settings)
    return TestClient(app, headers={"X-Token": app.state.token})


@pytest.fixture
def elsewhere(tmp_path):
    folder = tmp_path / "RL520DAT" / "LoaderInputFiles"
    folder.mkdir(parents=True)
    (folder / "00007888.inuse").write_text('<Batch id="7"/>\n<Row v="1"/>\n', encoding="utf-8")
    return folder


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- opening -------------------------------------------------------------------------------------------

def test_open_returns_the_text_and_what_can_be_done_with_it(client, ws):
    info = client.get("/api/file", params={"path": str(ws / "app.cfg")}).json()
    assert info["editable"] and info["can_change"] and info["text"].startswith("name=demo")
    assert info["sha"] == sha((ws / "app.cfg").read_bytes()) and info["lines"] == 4 and info["name"] == "app.cfg"


def test_a_file_in_a_read_only_folder_opens_for_reading_but_not_changing(client, ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    info = client.get("/api/file", params={"path": str(ref / "a.bak")}).json()
    assert info["editable"] and info["can_change"] is False


def test_binary_files_are_not_offered_for_editing_and_big_text_is_read_only(client, ro_settings, ws):
    (ws / "data.bin").write_bytes(b"\x89ABC\x00\x00\x01")
    (ws / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    ro_settings.agent.max_edit_kb = 16
    (ws / "big.txt").write_text("x" * 20000, encoding="utf-8")
    for name in ("data.bin", "latin.txt"):
        info = client.get("/api/file", params={"path": str(ws / name)}).json()
        assert info["kind"] == "binary" and info["editable"] is False and "only be downloaded" in info["reason"] and info["text"] == ""
    big = client.get("/api/file", params={"path": str(ws / "big.txt")}).json()  # too big to change, but it can be read
    assert big["kind"] == "text" and big["editable"] is False and "16 KB" in big["reason"] and len(big["text"]) == 20000


def test_a_file_too_big_even_to_show_says_so(client, ro_settings, ws):
    ro_settings.agent.max_view_kb = 64
    (ws / "huge.log").write_text("y" * 70000, encoding="utf-8")
    info = client.get("/api/file", params={"path": str(ws / "huge.log")}).json()
    assert info["kind"] == "toobig" and info["text"] == "" and "up to 64 KB" in info["reason"]


def test_a_folder_that_is_not_allowed_is_reported_so_the_phone_can_ask(client, elsewhere):
    reply = client.get("/api/file", params={"path": str(elsewhere / "00007888.inuse")})
    assert reply.status_code == 403
    assert reply.json()["detail"]["code"] == "needs_access" and reply.json()["detail"]["folder"] == str(elsewhere)


def test_secrets_and_missing_files_never_open(client, ro_settings):
    token = ro_settings.server.token_file
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("secret", encoding="utf-8")
    assert client.get("/api/file", params={"path": str(token)}).status_code == 403
    assert client.get("/api/file", params={"path": str(token.parent / "nope.txt")}).status_code in (403, 404)
    assert TestClient(create_app(ro_settings)).get("/api/file", params={"path": "x"}).status_code == 401


# --- saving ---------------------------------------------------------------------------------------------

def test_save_changes_only_what_was_edited_and_keeps_the_files_own_line_endings(client, ws):
    info = client.get("/api/file", params={"path": str(ws / "app.cfg")}).json()
    edited = info["text"].replace("port=8080", "port=9090")  # the browser's box holds plain \n
    reply = client.put("/api/file", json={"path": info["path"], "text": edited, "base_sha": info["sha"]}).json()
    assert reply["saved"] and reply["job"]["status"] == "done"
    assert (ws / "app.cfg").read_bytes() == b"name=demo\r\nport=9090\r\nmode=dev\r\n"  # still CRLF, one line different
    assert reply["sha"] == sha((ws / "app.cfg").read_bytes())
    assert "1 line added, 1 removed" in reply["job"]["outputs"][0]


def test_a_saved_edit_is_a_job_in_the_chat_with_undo_and_an_audit_trail(ro_settings, ws):
    manager = JobManager(ro_settings)
    info = manager.open_file(str(ws / "app.cfg"))
    saved = manager.save_file(info["path"], info["text"].replace("dev", "prod"), info["sha"], "my-chat")
    job = manager.get(saved["job"]["id"])
    assert job.status is Status.DONE and job.conversation == "my-chat" and job.changed and job.prompt == "Edited app.cfg by hand"
    manager.undo(job.id)
    assert (ws / "app.cfg").read_bytes() == b"name=demo\r\nport=8080\r\nmode=dev\r\n"  # exactly as before, bytes and all
    log = audit_text(ro_settings)
    assert '"opened_in_editor"' in log and '"write_file"' in log and '"submitted"' in log


def test_saving_nothing_changes_nothing(client, ws):
    info = client.get("/api/file", params={"path": str(ws / "app.cfg")}).json()
    reply = client.put("/api/file", json={"path": info["path"], "text": info["text"], "base_sha": info["sha"]}).json()
    assert reply["saved"] is False and reply["job"] is None


def test_a_bom_and_utf8_text_survive(client, ws):
    (ws / "u.txt").write_bytes(b"\xef\xbb\xbfcaf\xc3\xa9\n")
    info = client.get("/api/file", params={"path": str(ws / "u.txt")}).json()
    assert info["text"] == "café\n"
    client.put("/api/file", json={"path": info["path"], "text": "café au lait\n", "base_sha": info["sha"]})
    assert (ws / "u.txt").read_bytes() == b"\xef\xbb\xbfcaf\xc3\xa9 au lait\n"


def test_a_file_changed_on_the_pc_meanwhile_is_not_overwritten_by_mistake(client, ws):
    info = client.get("/api/file", params={"path": str(ws / "app.cfg")}).json()
    (ws / "app.cfg").write_bytes(b"changed=elsewhere\r\n")  # someone edits it on the PC
    clash = client.put("/api/file", json={"path": info["path"], "text": "my edit", "base_sha": info["sha"]})
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "changed"
    assert (ws / "app.cfg").read_bytes() == b"changed=elsewhere\r\n"
    forced = client.put("/api/file", json={"path": info["path"], "text": "my edit", "base_sha": None}).json()  # "save anyway"
    assert forced["saved"] and (ws / "app.cfg").read_bytes() == b"my edit"


def test_saving_in_a_read_only_folder_is_refused_and_says_what_to_do(client, ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    reply = client.put("/api/file", json={"path": str(ref / "a.bak"), "text": "hacked", "base_sha": None})
    assert reply.status_code == 403 and reply.json()["detail"]["code"] == "needs_change_access"
    assert (ref / "a.bak").read_text() == "1"


def test_saving_needs_the_token_and_a_valid_chat_id(client, ws, ro_settings):
    body = {"path": str(ws / "app.cfg"), "text": "x"}
    assert TestClient(create_app(ro_settings)).put("/api/file", json=body).status_code == 401
    assert client.put("/api/file", json={**body, "conversation": "../bad"}).status_code == 422


def test_the_secret_folders_cannot_be_written_even_by_hand(client, ro_settings):
    token = ro_settings.server.token_file
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("secret", encoding="utf-8")
    assert client.put("/api/file", json={"path": str(token), "text": "pwned", "base_sha": None}).status_code == 403
    assert token.read_text() == "secret"


# --- allowing changes in a folder -----------------------------------------------------------------------

def test_the_user_can_allow_editing_in_a_folder_then_save_there_and_take_it_back(client, elsewhere):
    path = str(elsewhere / "00007888.inuse")
    assert client.put("/api/file", json={"path": path, "text": "x", "base_sha": None}).status_code == 403  # not allowed yet
    assert client.get("/api/file", params={"path": path}).status_code == 403

    granted = client.post("/api/access/grant", json={"path": str(elsewhere), "change": True}).json()["granted"]
    assert [(g["path"], g["change"]) for g in granted] == [(str(elsewhere.resolve()), True)]
    info = client.get("/api/file", params={"path": path}).json()
    assert info["can_change"] is True
    saved = client.put("/api/file", json={"path": path, "text": info["text"].replace("7", "8"), "base_sha": info["sha"]}).json()
    assert saved["saved"] and (elsewhere / "00007888.inuse").read_text().startswith('<Batch id="8"/>')
    assert [(g["path"], g["change"]) for g in client.get("/api/info").json()["folders"]["granted_details"]] == [(str(elsewhere.resolve()), True)]

    client.post("/api/access/revoke", json={"path": str(elsewhere)})
    assert client.put("/api/file", json={"path": path, "text": "again", "base_sha": None}).status_code == 403


def test_allowing_to_read_does_not_allow_changing(client, elsewhere):
    client.post("/api/access/grant", json={"path": str(elsewhere), "change": False})
    path = str(elsewhere / "00007888.inuse")
    assert client.get("/api/file", params={"path": path}).json()["can_change"] is False
    assert client.put("/api/file", json={"path": path, "text": "x", "base_sha": None}).status_code == 403


def test_a_folder_marked_read_only_in_config_can_never_be_made_changeable(client, ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    reply = client.post("/api/access/grant", json={"path": str(ref), "change": True})
    assert reply.status_code == 403 and "config.yaml marks" in reply.json()["detail"]
    assert client.put("/api/file", json={"path": str(ref / "a.bak"), "text": "x", "base_sha": None}).status_code == 403


def test_a_change_grant_can_be_forbidden_in_config_and_protected_folders_never_qualify(client, ro_settings, elsewhere):
    ro_settings.agent.allow_edit_grants = False
    assert client.post("/api/access/grant", json={"path": str(elsewhere), "change": True}).status_code == 403
    ro_settings.agent.allow_edit_grants = True
    data = ro_settings.server.token_file.parent
    data.mkdir(parents=True, exist_ok=True)
    assert client.post("/api/access/grant", json={"path": str(data), "change": True}).status_code == 403
    assert client.post("/api/access/grant", json={"path": str(elsewhere.anchor), "change": True}).status_code == 403  # a whole drive


def test_kept_change_access_survives_a_restart(ro_settings, elsewhere):
    first = JobManager(ro_settings)
    first.grant_folder(str(elsewhere), True)
    assert GrantStore(Database(ro_settings.storage.database)).load() == [(elsewhere.resolve(), True)]
    again = JobManager(ro_settings)
    assert again.open_file(str(elsewhere / "00007888.inuse"))["can_change"] is True


# --- asking for the editor in the chat ---------------------------------------------------------------------

LAST = r"C:\data\00007888.inuse"


@pytest.mark.parametrize("prompt", [
    "i want to edit that file", "let me edit it", "ok now i will edit it live", "can i edit this file on my phone",
    "edit the file", "I need to modify that file manually",
])
def test_these_open_the_editor_on_the_file_the_chat_was_about(prompt):
    step = quick_open_editor(prompt, None, LAST).steps[0]
    assert step.action is Action.READ and step.editor and step.path == LAST


def test_a_named_file_opens_the_editor(ro_settings, ws):
    job = JobManager(ro_settings).submit(f"edit app.cfg in {ws}")
    assert job.status is Status.DONE and job.open == str((ws / "app.cfg").resolve()) and "editor" in job.outputs[0]
    assert job.to_dict()["open"] == job.open


def test_a_typo_in_the_name_still_finds_the_file(ro_settings, ws):
    job = JobManager(ro_settings).submit(f"let me edit app.cgf in {ws}")
    assert job.open.endswith("app.cfg") and "you typed app.cgf" in job.summary


@pytest.mark.parametrize("prompt", [
    "change the port to 9090 in app.cfg", "edit the config to set the mode to prod", "update app.cfg with the new port",
    "replace x with y in that file", "add a line to that file", "edit this so that it is shorter",
])
def test_a_request_that_says_what_to_change_goes_to_the_ai_not_the_editor(prompt):
    assert quick_open_editor(prompt, None, LAST) is None


def test_it_completely_means_the_whole_last_file(ro_settings, ws):
    manager = JobManager(ro_settings)
    manager.submit(f"read app.cfg in {ws}")
    time.sleep(0.01)
    plan = quick_request("show it completely", None, str(ws / "app.cfg"))
    assert plan.steps[0].lines is None and plan.steps[0].path.endswith("app.cfg")


def test_the_file_the_phone_was_showing_is_what_it_means(ro_settings, ws):
    (ws / "other.txt").write_text("other", encoding="utf-8")
    manager = JobManager(ro_settings)
    manager.submit(f"read other.txt in {ws}", "c")
    time.sleep(0.01)
    job = manager.submit("let me edit it", "c", focus=str(ws / "app.cfg"))  # the editor was last showing app.cfg
    assert job.open.endswith("app.cfg")


def test_the_phone_sends_the_file_it_was_showing(client, ro_settings, ws):
    job = client.post("/api/jobs", json={"prompt": "let me edit that file", "file": str(ws / "app.cfg")}).json()
    assert job["status"] == "done" and job["open"].endswith("app.cfg")


def test_an_edit_the_ai_proposes_shows_old_and_new_text_for_the_approval_card(ro_settings, ws):
    from handoff.planner import Plan, Step

    class Proposes:
        is_slow = False

        def plan(self, prompt, context=None):
            return Plan("", [Step(Action.WRITE, str(ws / "app.cfg"), "x", edit=("port=8080", "port=9090"))])

    step = JobManager(ro_settings, Proposes()).submit("go").to_dict()["steps"][0]
    assert step["edit"] == {"old": "port=8080", "new": "port=9090"}


def test_the_editor_gets_plain_line_breaks_so_an_untouched_file_is_not_changed(client, ws):
    """A Windows file has \r\n; the browser's text box only holds \n. They must compare equal, or every file looks edited."""
    info = client.get("/api/file", params={"path": str(ws / "app.cfg")}).json()
    assert "\r" not in info["text"] and info["lines"] == 4
    again = client.put("/api/file", json={"path": info["path"], "text": info["text"], "base_sha": info["sha"]}).json()
    assert again["saved"] is False and (ws / "app.cfg").read_bytes() == b"name=demo\r\nport=8080\r\nmode=dev\r\n"


# --- pictures, PDFs and the full-screen viewer ---------------------------------------------------------------

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


def test_a_picture_is_described_as_one_and_served_for_viewing(client, ws):
    (ws / "logo.png").write_bytes(PNG)
    info = client.get("/api/file", params={"path": str(ws / "logo.png")}).json()
    assert info["kind"] == "image" and info["mime"] == "image/png" and info["editable"] is False and info["text"] == ""
    raw = client.get("/api/file/raw", params={"path": str(ws / "logo.png")})
    assert raw.status_code == 200 and raw.content == PNG and raw.headers["content-type"] == "image/png"
    assert raw.headers["x-content-type-options"] == "nosniff" and raw.headers["cache-control"] == "no-store"


def test_a_pdf_is_a_pdf(client, ws):
    (ws / "doc.pdf").write_bytes(b"%PDF-1.4\n%fake")
    assert client.get("/api/file", params={"path": str(ws / "doc.pdf")}).json()["kind"] == "pdf"
    assert client.get("/api/file/raw", params={"path": str(ws / "doc.pdf")}).headers["content-type"] == "application/pdf"


def test_only_pictures_and_pdfs_are_served_raw_never_html_or_scripts(client, ws):
    for name in ("page.html", "run.js", "notes.txt", "x.exe", "app.cfg"):
        (ws / name).write_bytes(b"<script>alert(1)</script>")
        assert client.get("/api/file/raw", params={"path": str(ws / name)}).status_code == 403, name


def test_the_raw_endpoint_keeps_every_rule_of_reading(client, ro_settings, elsewhere):
    assert TestClient(create_app(ro_settings)).get("/api/file/raw", params={"path": "x.png"}).status_code == 401
    (elsewhere / "p.png").write_bytes(PNG)
    outside = client.get("/api/file/raw", params={"path": str(elsewhere / "p.png")})
    assert outside.status_code == 403 and outside.json()["detail"]["code"] == "needs_access"
    secret = ro_settings.server.token_file.parent / "key.png"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(PNG)
    assert client.get("/api/file/raw", params={"path": str(secret)}).status_code == 403  # the app's own data
    assert client.get("/api/file/raw", params={"path": str(elsewhere / "missing.png")}).status_code in (403, 404)


def test_a_picture_over_the_size_limit_is_refused(client, ro_settings, ws):
    ro_settings.agent.max_download_mb = 1
    (ws / "big.png").write_bytes(b"0" * (1024 * 1024 + 1))
    assert client.get("/api/file/raw", params={"path": str(ws / "big.png")}).status_code == 413
    assert "over the 1 MB limit" in client.get("/api/file", params={"path": str(ws / "big.png")}).json()["reason"]


@pytest.mark.parametrize("prompt", [
    "open it in a new screen", "show that file full screen", "view this in a new window", "open it in the viewer",
    "please open the file in canvas", "view that file in the viewer", "can you show it in full screen",
])
def test_these_open_the_full_screen_viewer_on_the_last_file(prompt):
    from handoff.planner import quick_open_view

    step = quick_open_view(prompt, None, LAST).steps[0]
    assert step.editor and step.view and step.path == LAST and step.detail() == "open full screen"


def test_a_named_file_opens_full_screen_and_the_phone_is_told_which_mode(ro_settings, ws):
    job = JobManager(ro_settings).submit(f"view app.cfg in a new screen in {ws}")
    assert job.status is Status.DONE and job.open.endswith("app.cfg") and job.to_dict()["open_mode"] == "view"
    edit = JobManager(ro_settings).submit(f"let me edit app.cfg in {ws}")
    assert edit.to_dict()["open_mode"] == "edit"


def test_pictures_and_pdfs_always_open_in_the_viewer_never_as_text(ro_settings, ws):
    (ws / "logo.png").write_bytes(PNG)
    job = JobManager(ro_settings).submit(f"open logo.png in {ws}")
    assert job.status is Status.DONE and job.to_dict()["open_mode"] == "view" and job.open.endswith("logo.png")
    from handoff.planner import quick_request

    assert quick_request(r"show C:\docs\report.pdf").steps[0].view
    assert not quick_request(r"show C:\docs\report.txt").steps[0].editor  # text still prints into the chat as before


def test_a_big_log_can_be_read_in_the_viewer_even_if_it_cannot_be_edited(client, ro_settings, ws):
    ro_settings.agent.max_edit_kb = 16
    (ws / "debug.log").write_text("\n".join(f"line {i} ERROR" for i in range(3000)), encoding="utf-8")
    info = client.get("/api/file", params={"path": str(ws / "debug.log")}).json()
    assert info["kind"] == "text" and info["editable"] is False and info["lines"] == 3000 and "line 2999" in info["text"]
    assert client.put("/api/file", json={"path": info["path"], "text": "x", "base_sha": info["sha"]}).status_code in (200, 413)
