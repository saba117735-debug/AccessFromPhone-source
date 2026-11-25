import json

from fastapi.testclient import TestClient

from audit_helper import audit_text
from handoff.audit import AuditLog
from handoff.db import Database
from handoff.jobs import JobManager, Status
from handoff.server import create_app


def run(manager, prompt, conversation="chat-1"):
    job = manager.submit(prompt, conversation)
    manager.join_background()
    return job


def test_one_database_file_holds_everything(settings):
    manager = JobManager(settings)
    run(manager, "list")
    tables = {r["name"] for r in Database(settings.storage.database).rows("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"audit", "owner", "devices", "grants", "jobs"} <= tables


def test_requests_and_their_chat_survive_a_restart(settings):
    first = JobManager(settings)
    done = run(first, "list", "chat-1")
    again = JobManager(settings)
    restored = {j.id: j for j in again.list()}
    assert restored[done.id].status is Status.DONE
    assert restored[done.id].to_dict()["conversation"] == "chat-1"
    assert restored[done.id].to_dict()["steps"] == done.to_dict()["steps"]


def test_a_request_waiting_for_approval_when_the_pc_stopped_is_shown_as_expired_not_stuck(settings):
    first = JobManager(settings)
    job = run(first, "create hi.txt with hello")
    assert job.status is Status.AWAITING
    again = JobManager(settings)
    restored = next(j for j in again.list() if j.id == job.id)
    assert restored.status is Status.EXPIRED and "restarted" in restored.error
    assert not (settings.agent.allowed_folders[0] / "hi.txt").exists()  # and nothing was done behind anyone's back


def test_files_a_finished_request_offered_can_still_be_downloaded_after_a_restart(settings):
    root = settings.agent.allowed_folders[0]
    (root / "report.txt").write_text("numbers", encoding="utf-8")
    first = create_app(settings)
    client = TestClient(first, headers={"X-Token": first.state.token})
    job = client.post("/api/jobs", json={"prompt": "download report.txt"}).json()
    client.post(f"/api/jobs/{job['id']}/approve")
    assert client.get("/api/jobs").json()[0]["files"]
    second = create_app(settings)
    again = TestClient(second, headers={"X-Token": second.state.token})
    name = again.get("/api/jobs").json()[0]["files"][0]
    assert again.get("/api/files", params={"path": name}).content == b"numbers"


def test_an_older_audit_log_is_imported_once_and_damaged_lines_are_skipped(settings):
    old = settings.logging.audit_file
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text(json.dumps({"time": "2026-01-01T00:00:00+00:00", "job": "abc", "event": "submitted", "prompt": "hi"}) + "\nnot json\n"
                   + json.dumps({"time": "2026-01-01T00:00:01+00:00", "job": "abc", "event": "done"}) + "\n", encoding="utf-8")
    manager = JobManager(settings)
    events = manager.audit_events()
    assert [e["event"] for e in events] == ["submitted", "done"] and events[0]["prompt"] == "hi"
    assert not old.exists() and old.with_name(old.name + ".migrated").exists()
    assert [e["event"] for e in JobManager(settings).audit_events()] == ["submitted", "done"]  # not imported a second time


def test_the_newest_entries_come_back_oldest_first(tmp_path):
    log = AuditLog(Database(tmp_path / "app.db"))
    for i in range(5):
        log.record("-", f"e{i}")
    assert [e["event"] for e in log.read(3)] == ["e2", "e3", "e4"]


def test_the_whole_log_exports_as_json_lines(settings):
    run(JobManager(settings), "list")
    lines = audit_text(settings).splitlines()
    assert lines and all(json.loads(line)["event"] for line in lines)


def test_two_threads_writing_at_once_lose_nothing(tmp_path):
    import threading
    log = AuditLog(Database(tmp_path / "app.db"))
    workers = [threading.Thread(target=lambda n=n: [log.record(str(n), "tick") for _ in range(50)]) for n in range(4)]
    [w.start() for w in workers]
    [w.join() for w in workers]
    assert len(log.read(1000)) == 200
