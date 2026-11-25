"""When a request needs a folder that is not listed, the user is asked. Read only, revocable, never for secrets."""
import copy
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from audit_helper import audit_text
from handoff.config import LlmConfig
from handoff.db import Database
from handoff.grants import GrantStore, protected_locations
from handoff.jobs import JobManager, JobStateError, Status
from handoff.planner import OllamaPlanner
from handoff.policy import OutsideAllowed, ProtectedPath
from handoff.server import create_app


def kept(settings):
    return GrantStore(Database(settings.storage.database)).load()


@pytest.fixture
def elsewhere(tmp_path):
    """A folder that is NOT in the config: it holds a log, and a sibling folder that must stay closed."""
    root = tmp_path / "elsewhere"
    (root / "logs").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "logs" / "app.log").write_text("line one\nline two\nline three", encoding="utf-8")
    (root / "other" / "private.txt").write_text("not for you", encoding="utf-8")
    return root


def ask(manager, prompt, conv="c"):
    time.sleep(0.01)
    job = manager.submit(prompt, conv)
    manager.join_background()
    return job


class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def tool(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


# --- being asked ------------------------------------------------------------------------

def test_a_folder_that_is_not_listed_is_asked_about_not_refused(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    assert job.status is Status.NEEDS_ACCESS and job.access == [str(elsewhere / "logs")]
    assert job.outputs == []  # nothing was read before you said yes
    assert '"access_requested"' in audit_text(ro_settings)


def test_asking_names_the_folder_not_the_file(ro_settings, elsewhere):
    job = ask(JobManager(ro_settings), f"read {elsewhere / 'logs' / 'app.log'}")
    assert job.access == [str(elsewhere / "logs")]


def test_allow_once_runs_the_request_then_closes_the_folder_again(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    manager.decide_access(job.id, "once")
    manager.join_background()
    assert job.status is Status.DONE and "line two" in job.outputs[0]
    assert manager.granted_folders() == []  # one-off: nothing kept
    again = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    assert again.status is Status.NEEDS_ACCESS  # it asks again


def test_allow_and_keep_means_it_does_not_ask_again(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    manager.decide_access(job.id, "always")
    manager.join_background()
    assert job.status is Status.DONE
    assert manager.granted_folders() == [str((elsewhere / "logs").resolve())]
    again = ask(manager, f"read the last 1 lines of app.log in {elsewhere / 'logs'}")
    assert again.status is Status.DONE and again.outputs[0].startswith("line three")


def test_kept_access_survives_a_restart_and_can_be_taken_back(ro_settings, elsewhere):
    first = JobManager(ro_settings)
    job = ask(first, f"read app.log in {elsewhere / 'logs'}")
    first.decide_access(job.id, "always")
    first.join_background()
    assert kept(ro_settings) == [((elsewhere / "logs").resolve(), False)]

    restarted = JobManager(ro_settings)  # a new run of the app
    assert ask(restarted, f"read app.log in {elsewhere / 'logs'}").status is Status.DONE
    assert restarted.revoke_access(str(elsewhere / "logs")) == []
    assert kept(ro_settings) == []
    assert ask(restarted, f"read app.log in {elsewhere / 'logs'}").status is Status.NEEDS_ACCESS


def test_saying_no_reads_nothing_and_says_so(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    manager.decide_access(job.id, "deny")
    assert job.status is Status.REJECTED and "didn't allow access" in job.error and job.outputs == []
    assert manager.granted_folders() == []
    assert '"access_denied"' in audit_text(ro_settings)


def test_only_the_asked_folder_opens_not_its_neighbours(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    manager.decide_access(job.id, "always")
    manager.join_background()
    sibling = ask(manager, f"read private.txt in {elsewhere / 'other'}")
    assert sibling.status is Status.NEEDS_ACCESS and sibling.access == [str(elsewhere / "other")]


def test_a_typo_is_still_fixed_once_the_folder_is_allowed(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.lgo in {elsewhere / 'logs'}")
    assert job.status is Status.NEEDS_ACCESS
    manager.decide_access(job.id, "once")
    manager.join_background()
    assert job.status is Status.DONE and "you typed app.lgo" in job.summary and "line one" in job.outputs[0]


def test_a_download_from_an_allowed_once_folder_still_works_afterwards(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"download app.log from {elsewhere / 'logs'}")
    manager.decide_access(job.id, "once")
    manager.join_background()
    assert job.status is Status.AWAITING  # sending a copy still needs the usual approval
    manager.approve(job.id)
    assert job.status is Status.DONE and manager.granted_folders() == []
    assert manager.file_for_download(job.files[0]).read_text(encoding="utf-8").startswith("line one")


def test_the_model_asking_for_a_closed_folder_triggers_the_question_then_starts_over(ro_settings, elsewhere):
    log = str(elsewhere / "logs" / "app.log")
    script = Script(
        tool("read_file", path=log), {"content": "I am waiting for permission."},   # first try: refused, user is asked
        tool("read_file", path=log), {"content": "It has three lines."},             # after "allow": read for real
    )
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = ask(manager, "how many lines does the app log have?")
    assert job.status is Status.NEEDS_ACCESS and job.outputs == []       # the model's guess is thrown away
    manager.decide_access(job.id, "once")
    manager.join_background()
    assert job.status is Status.DONE and job.outputs == ["It has three lines."]
    refused = script.requests[1]["messages"][-1]["content"]
    assert "being asked for permission" in refused and "Do not try other paths" in refused


# --- what stays off limits ------------------------------------------------------------------

def test_access_is_read_only_whatever_the_user_allows(ro_settings, elsewhere):
    manager = JobManager(ro_settings)
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    manager.decide_access(job.id, "always")
    manager.join_background()
    write = ask(manager, f"create {elsewhere / 'logs' / 'new.txt'} with hi")
    assert write.status is Status.FAILED and "read-only" in write.error
    assert not (elsewhere / "logs" / "new.txt").exists()
    delete = ask(manager, f"delete {elsewhere / 'logs' / 'app.log'}")
    assert delete.status is Status.FAILED and (elsewhere / "logs" / "app.log").exists()


def test_changing_something_outside_the_writable_folders_is_refused_not_asked(ro_settings, elsewhere):
    job = ask(JobManager(ro_settings), f"create {elsewhere / 'x.txt'} with hi")
    assert job.status is Status.FAILED and "outside the allowed folders for changes" in job.error
    assert job.access == []


def test_the_apps_own_secrets_can_never_be_opened(ro_settings):
    data = ro_settings.server.token_file.parent
    data.mkdir(parents=True, exist_ok=True)
    ro_settings.server.token_file.write_text("super-secret-token", encoding="utf-8")
    manager = JobManager(ro_settings)
    job = ask(manager, f"read token.txt in {data}")
    assert job.status is Status.FAILED and "protected location" in job.error and job.access == []
    assert "super-secret-token" not in " ".join(job.outputs) + (job.error or "")


def test_a_protected_folder_stays_closed_even_if_it_is_granted_or_listed(ro_settings, tmp_path):
    secret = tmp_path / "keys"
    secret.mkdir()
    (secret / "id_test").write_text("PRIVATE KEY", encoding="utf-8")
    ro_settings.agent.protected_folders = [secret]
    manager = JobManager(ro_settings)
    manager._policy.grant(secret)  # even a grant cannot open it
    with pytest.raises(ProtectedPath):
        manager._policy.resolve(secret / "id_test")
    assert ask(manager, f"read id_test in {secret}").status is Status.FAILED


def test_the_built_in_protected_list_covers_keys_and_the_apps_data(ro_settings):
    from pathlib import Path

    places = {str(p).lower() for p in protected_locations(ro_settings)}
    home = Path.home()
    assert str((home / ".ssh").resolve()).lower() in places and str((home / ".aws").resolve()).lower() in places
    assert str(ro_settings.server.token_file.parent.resolve()).lower() in places
    assert str(ro_settings.server.tls_dir.parent.resolve()).lower() in places  # the https private key lives here


def test_a_whole_drive_or_a_missing_folder_is_not_something_to_ask_about(ro_settings, tmp_path):
    manager = JobManager(ro_settings)
    missing = ask(manager, f"read a.log in {tmp_path / 'no-such-folder'}")
    assert missing.status is Status.FAILED and "can't find the folder" in missing.error
    drive = ask(manager, f"read boot.ini in {tmp_path.anchor}")
    assert drive.status is Status.FAILED and ("whole drive" in drive.error or "protected" in drive.error)


def test_strict_mode_never_asks(ro_settings, elsewhere):
    ro_settings.agent.ask_for_access = False
    job = ask(JobManager(ro_settings), f"read app.log in {elsewhere / 'logs'}")
    assert job.status is Status.FAILED and "outside the allowed folders" in job.error and job.access == []


def test_the_folders_listed_in_config_still_need_no_question(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = ask(JobManager(ro_settings), f"read a.bak in {ref}")
    assert job.status is Status.DONE


def test_an_unanswered_question_expires(ro_settings, elsewhere):
    clock = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    manager = JobManager(ro_settings, now=lambda: clock["now"])
    job = ask(manager, f"read app.log in {elsewhere / 'logs'}")
    clock["now"] += timedelta(minutes=ro_settings.approval.timeout_minutes + 1)
    manager.decide_access(job.id, "always")
    assert job.status is Status.EXPIRED and manager.granted_folders() == []


def test_only_a_waiting_question_can_be_answered(ro_settings):
    manager = JobManager(ro_settings)
    done = ask(manager, "list")
    with pytest.raises(JobStateError):
        manager.decide_access(done.id, "always")


# --- the kept list ----------------------------------------------------------------------------

def test_a_damaged_or_stale_grants_file_is_harmless(ro_settings, tmp_path):
    file = ro_settings.storage.grants_file
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("{not json", encoding="utf-8")
    assert JobManager(ro_settings).granted_folders() == []
    file.write_text(json.dumps({"folders": [str(tmp_path / "gone"), 5, str(tmp_path.anchor)]}), encoding="utf-8")
    assert JobManager(ro_settings).granted_folders() == []  # missing folder, junk, and a bare drive are all ignored


def test_an_older_grants_file_is_moved_into_the_database_once(ro_settings, elsewhere):
    file = ro_settings.storage.grants_file
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"folders": [str(elsewhere / "logs")]}), encoding="utf-8")  # the first version: plain paths
    manager = JobManager(ro_settings)
    assert manager.granted_folders() == [str((elsewhere / "logs").resolve())]
    assert not file.exists() and file.with_name(file.name + ".migrated").exists()  # kept, renamed, never imported twice
    assert kept(ro_settings) == [((elsewhere / "logs").resolve(), False)]


def test_the_kept_list_is_stored_and_replaced_as_a_whole(tmp_path):
    store = GrantStore(Database(tmp_path / "app.db"))
    store.save([(tmp_path / "b", True), (tmp_path / "a", False)])
    assert store.load() == [(tmp_path / "a", False), (tmp_path / "b", True)]
    store.save([(tmp_path / "c", False)])
    assert store.load() == [(tmp_path / "c", False)]


def test_the_policy_reports_what_is_outside(ro_settings, tmp_path):
    with pytest.raises(OutsideAllowed) as caught:
        JobManager(ro_settings)._policy.resolve(tmp_path / "elsewhere")
    assert caught.value.path == (tmp_path / "elsewhere").resolve()


# --- over HTTP --------------------------------------------------------------------------------

def test_the_question_and_the_answer_over_http(ro_settings, elsewhere):
    app = create_app(ro_settings)
    client = TestClient(app, headers={"X-Token": app.state.token})
    job = client.post("/api/jobs", json={"prompt": f"read app.log in {elsewhere / 'logs'}"}).json()
    assert job["status"] == "needs_access" and job["access"] == [str(elsewhere / "logs")]

    assert client.post(f"/api/jobs/{job['id']}/access", json={"decision": "maybe"}).status_code == 422
    assert client.post("/api/jobs/nope/access", json={"decision": "once"}).status_code == 404
    assert TestClient(app).post(f"/api/jobs/{job['id']}/access", json={"decision": "once"}).status_code == 401

    answered = client.post(f"/api/jobs/{job['id']}/access", json={"decision": "always"}).json()
    app.state.manager.join_background()
    final = client.get(f"/api/jobs/{answered['id']}").json()
    assert final["status"] == "done" and "line two" in final["outputs"][0]
    assert client.post(f"/api/jobs/{job['id']}/access", json={"decision": "once"}).status_code == 409  # already answered

    info = client.get("/api/info").json()
    assert info["folders"]["granted"] == [str((elsewhere / "logs").resolve())] and info["ask_for_access"] is True
    gone = client.post("/api/access/revoke", json={"path": str(elsewhere / "logs")}).json()
    assert gone == {"granted": [], "granted_details": []}
    assert client.get("/api/info").json()["folders"]["granted"] == []
    assert TestClient(app).post("/api/access/revoke", json={"path": "x"}).status_code == 401
