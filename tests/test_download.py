import copy

import pytest
from fastapi.testclient import TestClient

from handoff.config import Action, LlmConfig
from handoff.jobs import JobManager, Status
from handoff.planner import OllamaPlanner, quick_download
from handoff.server import create_app


def path_of(prompt):
    plan = quick_download(prompt)
    return plan and plan.steps[0].path


def test_the_phrase_from_the_phone_is_understood_without_a_model():
    got = path_of(r"can u please download .gitignore which is present in C:\PageLogin\login-page")
    assert got == r"C:\PageLogin\login-page\.gitignore"


@pytest.mark.parametrize("prompt, expected", [
    ("download notes.txt", "notes.txt"),
    ("Please download the file report.xlsx.", "report.xlsx"),
    (r"download C:\PageLogin\a.bak", r"C:\PageLogin\a.bak"),
    (r'download "my file.txt" from "C:\My Folder"', r"C:\My Folder\my file.txt"),
    (r"could you download app.apk from C:\build\out?", r"C:\build\out\app.apk"),
])
def test_quick_download_variants(prompt, expected):
    assert path_of(prompt) == expected


@pytest.mark.parametrize("prompt", [
    "hi", "list", "download it", "download", "what does download mean in networking and why is it so slow today please explain",
    "download *.bak",
])
def test_other_requests_go_to_the_model(prompt):
    assert quick_download(prompt) is None


def test_quick_download_is_a_send_step():
    assert quick_download("download a.txt").steps[0].action is Action.SEND


def test_download_from_a_read_only_folder_needs_approval_then_works(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    manager = JobManager(ro_settings)
    job = manager.submit(f"download a.bak from {ref}")
    assert job.status is Status.AWAITING and job.files == []
    with pytest.raises(Exception):
        manager.file_for_download(str(ref / "a.bak"))  # not offered yet
    manager.approve(job.id)
    assert job.status is Status.DONE and job.files == [str((ref / "a.bak").resolve())]
    assert manager.file_for_download(job.files[0]).read_text() == "1"
    assert (ref / "a.bak").exists()  # the original is untouched


def test_files_nobody_offered_cannot_be_downloaded(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    with pytest.raises(Exception):
        JobManager(ro_settings).file_for_download(str(ref / "a.bak"))


def test_download_over_http_and_size_limit(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    app = create_app(ro_settings)
    c = TestClient(app, headers={"X-Token": app.state.token})
    assert c.get("/api/files", params={"path": str(ref / "a.bak")}).status_code == 403
    job = c.post("/api/jobs", json={"prompt": f"download a.bak from {ref}"}).json()
    done = c.post(f"/api/jobs/{job['id']}/approve").json()
    assert done["files"] and c.get("/api/files", params={"path": done["files"][0]}).text == "1"
    (ref / "big.bin").write_bytes(b"x" * (1024 * 1024 + 1))
    ro_settings.agent.max_download_mb = 1
    job = c.post("/api/jobs", json={"prompt": f"download big.bin from {ref}"}).json()
    failed = c.post(f"/api/jobs/{job['id']}/approve").json()
    assert failed["status"] == "failed" and "1 MB" in failed["error"]


def test_missing_file_is_explained_before_asking_for_approval(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"download nope.txt from {ref}")
    assert job.status is Status.DONE and job.steps == []  # nothing to approve
    assert "no file called nope.txt" in job.outputs[0]


def test_outside_the_allowed_folders_is_refused_at_plan_time(ro_settings, tmp_path):
    ro_settings.agent.ask_for_access = False  # strict mode: nothing outside the list is ever offered
    job = JobManager(ro_settings).submit(f"download secret.txt from {tmp_path.parent}")
    assert job.status is Status.FAILED and "outside the allowed folders" in job.error


class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def test_the_model_can_propose_a_download(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    call = {"tool_calls": [{"function": {"name": "download_file", "arguments": {"path": str(ref / "a.bak")}}}]}
    script = Script(call, {"content": "Ready to send."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("could you send me the a.bak backup please")
    manager.join_background()
    assert job.status is Status.AWAITING and job.steps[0].action is Action.SEND
    assert script.requests[0]["keep_alive"] == "30m"
    assert any(t["function"]["name"] == "download_file" for t in script.requests[0]["tools"])


def test_a_quick_request_never_calls_the_model(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script()  # any model call would raise IndexError
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit(f"download a.bak from {ref}")
    manager.join_background()
    assert job.status is Status.AWAITING and script.requests == []


def test_no_size_limit_by_default_and_big_files_stream(ro_settings):
    assert ro_settings.agent.max_download_mb == 0
    ref = ro_settings.agent.read_only_folders[0]
    big = ref / "huge.bin"
    big.write_bytes(b"0123456789" * (3 * 1024 * 1024))  # 30 MB, and no limit
    app = create_app(ro_settings)
    c = TestClient(app, headers={"X-Token": app.state.token})
    job = c.post("/api/jobs", json={"prompt": f"download huge.bin from {ref}"}).json()
    card = job["steps"][0]
    assert card["action"] == "send" and card["size"] == big.stat().st_size and card["kind"] == "file"  # the approval shows the size
    done = c.post(f"/api/jobs/{job['id']}/approve").json()
    assert done["status"] == "done"
    link = c.post("/api/files/link", params={"path": done["files"][0]}).json()
    assert link["size"] == big.stat().st_size and link["name"] == "huge.bin"
    plain = TestClient(app)  # the link is the key: no token needed
    part = plain.get(link["url"], headers={"Range": "bytes=5-14"})
    assert part.status_code == 206 and part.content == b"5678901234"
    assert "attachment" in plain.get(link["url"], headers={"Range": "bytes=0-1"}).headers["content-disposition"]
    assert plain.get("/dl/not-a-real-link").status_code == 404
    assert c.post("/api/files/link", params={"path": str(ref / "a.bak")}).status_code in (200, 403)


def test_an_unoffered_file_gets_no_download_link(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    app = create_app(ro_settings)
    c = TestClient(app, headers={"X-Token": app.state.token})
    assert c.post("/api/files/link", params={"path": str(ref / "a.bak")}).status_code == 403


def test_a_folder_is_sent_as_one_zip_and_shows_its_size(ro_settings):
    import zipfile
    ref = ro_settings.agent.read_only_folders[0]
    folder = ref / "proj"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("hello")
    (folder / "sub" / "b.txt").write_text("world!")
    manager = JobManager(ro_settings)
    job = manager.submit(f"download a.bak from {ref}")
    from handoff.planner import Step
    job.steps = [Step(Action.SEND, str(folder))]
    d = job.to_dict()["steps"][0]
    assert d["kind"] == "folder" and d["files"] == 2 and d["size"] == 11
    manager.approve(job.id)
    assert job.status is Status.DONE and job.files[0].endswith("proj.zip")
    with zipfile.ZipFile(job.files[0]) as z:
        assert sorted(z.namelist()) == ["proj/a.txt", "proj/sub/b.txt"]
    assert manager.file_for_download(job.files[0]).exists()
