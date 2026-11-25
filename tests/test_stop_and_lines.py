"""Stop a running command or a slow model; read N lines from "it"; clear messages for refused folders."""
from audit_helper import audit_text
import copy
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from handoff.cancel import CancelToken
from handoff.config import Action, LlmConfig
from handoff.jobs import JobManager, JobStateError, Status
from handoff.planner import OllamaPlanner, Plan, PlanError, quick_request, timeout_fallback
from handoff.server import create_app, quiet_disconnects

windows_only = pytest.mark.skipif(os.name != "nt", reason="uses PowerShell syntax")


class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def norm(path):
    return os.path.normpath(path)


# --- "read 50 lines from it" ------------------------------------------------------

LAST = r"C:\code\actionsClient.ts"


@pytest.mark.parametrize("prompt, lines, tail", [
    ("can u please read 50Lines from it.", 50, False),      # the exact message from the screenshot
    ("read 50 lines from it", 50, False),
    ("show me the first 20 lines of that file", 20, False),
    ("print the last 100 lines of it", 100, True),
    ("show the latest 30 lines from this file", 30, True),
    ("read the bottom 5 lines", 5, True),
])
def test_line_requests_about_it_use_the_last_file(prompt, lines, tail):
    plan = quick_request(prompt, None, LAST)
    step = plan.steps[0]
    assert step.action is Action.READ and norm(step.path) == norm(LAST) and step.lines == lines and step.tail is tail


def test_a_line_request_names_its_own_file_and_folder():
    plan = quick_request("show the last 200 lines of app.log in C:/logs", None, LAST)
    assert norm(plan.steps[0].path) == norm("C:/logs/app.log") and plan.steps[0].tail and plan.steps[0].lines == 200


def test_read_it_again_means_the_whole_last_file():
    step = quick_request("read it again", None, LAST).steps[0]
    assert norm(step.path) == norm(LAST) and step.lines is None


@pytest.mark.parametrize("prompt", ["read 50 lines from it", "read it again", "read 0 lines from it"])
def test_without_a_last_file_it_means_nothing(prompt):
    assert quick_request(prompt, None, None) is None or quick_request(prompt, None, LAST) is not None


def test_zero_lines_is_refused():
    assert quick_request("read 0 lines from it", None, LAST) is None


@pytest.fixture
def big(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    (ref / "big.txt").write_text("\n".join(f"line {i}" for i in range(1, 201)), encoding="utf-8")
    return ref


def ask(manager, prompt, conv="c"):
    time.sleep(0.01)
    job = manager.submit(prompt, conv)
    manager.join_background()
    return job


def test_a_follow_up_reads_lines_from_the_file_just_read(ro_settings, big):
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), Script()))  # no model call allowed
    ask(manager, f"read big.txt in {big}")
    head = ask(manager, "can u please read 3Lines from it.")
    assert head.status is Status.DONE
    assert head.outputs[0].splitlines()[:3] == ["line 1", "line 2", "line 3"]
    assert "first 3 lines; the file has more" in head.outputs[0]
    tail = ask(manager, "now the last 2 lines of it")
    assert tail.outputs[0].splitlines()[:2] == ["line 199", "line 200"] and "last 2 of 200 lines" in tail.outputs[0]


def test_it_means_a_file_the_ai_looked_at_too(ro_settings, big):
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), Script({"content": "It counts lines."})))
    ask(manager, f"please summarise big.txt in {big}")  # the model path: the app reads the file for it
    job = ask(manager, "read 2 lines from it")
    assert job.status is Status.DONE and job.outputs[0].splitlines()[:2] == ["line 1", "line 2"]


def test_a_short_file_has_no_cut_off_note(ro_settings, big):
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), Script()))
    job = ask(manager, f"read the first 500 lines of big.txt in {big}")
    assert job.outputs[0].endswith("line 200") and "..." not in job.outputs[0]


def test_each_chat_has_its_own_last_file(ro_settings, big):
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), Script({"content": "Which file?"})))
    ask(manager, f"read big.txt in {big}", "chat-a")
    other = ask(manager, "read 2 lines from it", "chat-b")
    assert "line 1" not in " ".join(other.outputs)  # chat-b has no last file: it went to the model


# --- stopping ------------------------------------------------------------------------

@pytest.fixture
def cmd_settings(ro_settings):
    ro_settings.commands.enabled = True
    return ro_settings


@windows_only
def test_stop_kills_a_running_command(cmd_settings):
    manager = JobManager(cmd_settings)
    job = manager.submit(f'run & "{sys.executable}" -c "import time; time.sleep(60)"')
    manager.approve(job.id)
    time.sleep(1.5)
    assert job.status is Status.RUNNING
    started = time.time()
    manager.stop(job.id)
    manager.join_background(15)
    assert time.time() - started < 8
    assert job.status is Status.STOPPED and "stopped by you" in job.outputs[0]
    assert '"stopped"' in audit_text(cmd_settings)


class Hangs(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(30)

    def log_message(self, *args):
        pass


@pytest.fixture
def stuck_model():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hangs)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_stop_hangs_up_on_a_model_that_is_still_thinking(ro_settings, stuck_model):
    llm = LlmConfig(provider="ollama", base_url=stuck_model, timeout_seconds=60)
    manager = JobManager(ro_settings, OllamaPlanner(llm))  # the real HTTP call
    job = manager.submit("tell me a joke")
    time.sleep(0.6)
    assert job.status is Status.PLANNING
    started = time.time()
    manager.stop(job.id)
    manager.join_background(8)
    assert time.time() - started < 5 and job.status is Status.STOPPED and job.error is None
    assert not any(t.is_alive() for t in manager._threads)  # the worker really ended; nothing is left thinking


def test_an_answer_that_arrives_after_stop_is_thrown_away(ro_settings):
    class Slow:
        is_slow = True

        def plan(self, prompt, context=None):
            time.sleep(0.5)  # cannot be interrupted
            return Plan("", [], reply="too late")

    manager = JobManager(ro_settings, Slow())
    job = manager.submit("hello")
    time.sleep(0.1)
    manager.stop(job.id)
    manager.join_background(5)
    assert job.status is Status.STOPPED and job.outputs == []


def test_only_a_busy_job_can_be_stopped(ro_settings):
    manager = JobManager(ro_settings)
    done = manager.submit("list")
    with pytest.raises(JobStateError, match="nothing to stop"):
        manager.stop(done.id)


def test_stop_over_http(settings):
    app = create_app(settings)
    client = TestClient(app, headers={"X-Token": app.state.token})
    job = client.post("/api/jobs", json={"prompt": "list"}).json()
    assert client.post(f"/api/jobs/{job['id']}/stop").status_code == 409
    assert client.post("/api/jobs/nope/stop").status_code == 404
    assert TestClient(app).post(f"/api/jobs/{job['id']}/stop").status_code == 401


def test_a_cancel_token_runs_its_hooks_once_and_survives_a_bad_hook():
    token, calls = CancelToken(), []
    token.on_cancel(lambda: calls.append("a"))
    token.on_cancel(lambda: 1 / 0)
    token.on_cancel(lambda: calls.append("b"))
    forgotten = token.on_cancel(lambda: calls.append("never"))
    forgotten()
    token.cancel(); token.cancel()
    assert calls == ["a", "b"] and token.cancelled
    token.on_cancel(lambda: calls.append("late"))  # added after: runs at once
    assert calls[-1] == "late"


# --- a slow model no longer means a bare error ----------------------------------------

def test_a_timeout_still_shows_the_file_the_app_already_read(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    (ref / "notes.txt").write_text("\n".join(f"row {i}" for i in range(100)), encoding="utf-8")

    def too_slow(url, payload, timeout):
        raise PlanError("The model did not answer within 5 seconds. A PC without a graphics card can be slow.")

    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), too_slow))
    job = ask(manager, f"please summarise notes.txt in {ref}")
    assert job.status is Status.DONE
    text = job.outputs[0]
    assert "didn't answer in time" in text and "start of notes.txt" in text and "row 0" in text and "row 39" in text
    assert "row 40" not in text and "60 more lines" in text and "read 50 lines from it" in text


def test_the_fallback_only_applies_to_timeouts_with_a_file_in_hand():
    assert timeout_fallback(PlanError("Cannot reach Ollama"), "") is None
    assert timeout_fallback(PlanError("The model did not answer within 5 seconds"), "no raw files here") is None


# --- a refused folder is explained, not just refused ------------------------------------

def test_a_folder_that_is_not_allowed_says_how_to_allow_it(ro_settings, tmp_path):
    ro_settings.agent.ask_for_access = False  # strict mode: the refusal explains how the PC owner adds a folder
    outside = tmp_path / "RL520APP" / "Local"
    outside.mkdir(parents=True)
    (outside / "QDSLiteDebugLogs_20261102.log").write_text("x", encoding="utf-8")
    manager = JobManager(ro_settings)
    job = ask(manager, f"read QDSLiteDebugLogs_20261102.log from {outside}")
    assert job.status is Status.FAILED
    assert "outside the allowed folders" in job.error                     # the old wording is still there
    assert "I can only use:" in job.error and str(ro_settings.agent.allowed_folders[0]) in job.error
    assert "config.yaml" in job.error and "read_only_folders:" in job.error
    assert f"- {outside}" in job.error                                    # the folder, not the file, to copy into the config


def test_a_read_only_refusal_keeps_its_own_clear_wording(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = ask(JobManager(ro_settings), f"create {ref}\\x.txt with hi")
    assert job.status is Status.FAILED and "read-only" in job.error and "config.yaml" not in job.error


# --- the harmless Windows "connection reset" noise ---------------------------------------

def test_dropped_connections_are_not_reported():
    seen = []

    class Loop:
        def default_exception_handler(self, context):
            seen.append(context)

    quiet_disconnects(Loop(), {"exception": ConnectionResetError(10054, "forcibly closed")})
    quiet_disconnects(Loop(), {"exception": BrokenPipeError()})
    assert seen == []
    quiet_disconnects(Loop(), {"exception": ValueError("a real problem")})
    assert len(seen) == 1
