from audit_helper import audit_text
import copy
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from pydantic import ValidationError

from handoff.audit import AuditLog
from handoff.commands import CommandRefused, CommandRunner
from handoff.config import Action, ApprovalConfig, CommandsConfig, LlmConfig
from handoff.jobs import JobManager, Status
from handoff.planner import AnthropicPlanner, DemoPlanner, OllamaPlanner, PlanError, Step, _http_json, build_planner
from handoff.policy import PolicyEngine, ReadOnlyPath

windows_only = pytest.mark.skipif(os.name != "nt", reason="uses PowerShell syntax")


@pytest.fixture
def cmd_settings(ro_settings):
    ro_settings.commands.enabled = True
    return ro_settings


class Script:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, url, payload, timeout):
        self.requests.append((url, copy.deepcopy(payload)))
        return self.replies.pop(0)


# --- config -------------------------------------------------------------------

def test_commands_are_off_by_default_and_run_always_needs_approval():
    assert CommandsConfig().enabled is False
    assert "run" in ApprovalConfig(require_for=["write"]).require_for  # a forgotten entry cannot disable it


def test_a_bad_deny_pattern_is_a_clear_error():
    with pytest.raises(ValidationError, match="invalid pattern"):
        CommandsConfig(deny=["("])


def test_the_hosted_provider_is_a_valid_choice():
    assert LlmConfig(provider="anthropic").hosted_model == "claude-sonnet-5-5"


# --- running commands ---------------------------------------------------------

def test_commands_turned_off_are_refused_before_approval(ro_settings):
    job = JobManager(ro_settings).submit("run echo hi")
    assert job.status is Status.FAILED and "turned off" in job.error


@windows_only
def test_a_command_waits_for_approval_then_runs_and_reports_the_exit_code(cmd_settings):
    manager = JobManager(cmd_settings)
    job = manager.submit("run echo hello-from-pc")
    assert job.status is Status.AWAITING and job.outputs == []
    manager.approve(job.id)
    manager.join_background(20)
    assert job.status is Status.DONE
    assert "hello-from-pc" in job.outputs[0] and "exit code 0" in job.outputs[0]
    assert '"run_command"' in audit_text(cmd_settings)


@windows_only
def test_a_failing_command_is_reported_not_hidden(cmd_settings):
    manager = JobManager(cmd_settings)
    job = manager.submit("run exit 3")
    manager.approve(job.id)
    manager.join_background(20)
    assert job.status is Status.DONE and "exit code 3" in job.outputs[0]


def test_blocked_commands_never_reach_the_phone(cmd_settings):
    job = JobManager(cmd_settings).submit("run Format C: /q")
    assert job.status is Status.FAILED and "blocked" in job.error


def test_allow_only_limits_and_forbids_chaining(cmd_settings):
    cmd_settings.commands.allow_only = ["echo"]
    manager = JobManager(cmd_settings)
    assert manager.submit("run echo ok").status is Status.AWAITING
    assert "allow_only" in manager.submit("run dir").error
    assert "chain" in manager.submit("run echo a; echo b").error


def test_commands_only_run_in_writable_folders(cmd_settings):
    policy = PolicyEngine(cmd_settings.agent.allowed_folders, cmd_settings.approval, cmd_settings.agent.read_only_folders)
    runner = CommandRunner(cmd_settings.commands, policy, AuditLog(cmd_settings.logging.audit_file))
    with pytest.raises(ReadOnlyPath):
        runner.run("j", cmd_settings.agent.read_only_folders[0], "echo hi", approved=True)


def test_running_without_approval_is_refused_by_the_policy(cmd_settings):
    policy = PolicyEngine(cmd_settings.agent.allowed_folders, cmd_settings.approval)
    runner = CommandRunner(cmd_settings.commands, policy, AuditLog(cmd_settings.logging.audit_file))
    with pytest.raises(Exception, match="needs approval"):
        runner.run("j", cmd_settings.agent.allowed_folders[0], "echo hi", approved=False)


@windows_only
def test_a_command_that_runs_too_long_is_stopped(cmd_settings):
    cmd_settings.commands.timeout_seconds = 1
    manager = JobManager(cmd_settings)
    slow = f'run & "{sys.executable}" -c "import time; time.sleep(30)"'
    job = manager.submit(slow)
    started = time.time()
    manager.approve(job.id)
    manager.join_background(20)
    assert time.time() - started < 15
    assert "timed out" in job.outputs[0]


@windows_only
def test_secrets_in_the_environment_are_hidden_from_commands(cmd_settings, monkeypatch):
    monkeypatch.setenv("MY_SECRET_TOKEN", "hunter2")
    manager = JobManager(cmd_settings)
    probe = f'run & "{sys.executable}" -c "import os; print(os.environ.get(\'MY_SECRET_TOKEN\', \'hidden\'))"'
    job = manager.submit(probe)
    manager.approve(job.id)
    manager.join_background(20)
    assert "hidden" in job.outputs[0] and "hunter2" not in job.outputs[0]


def test_the_model_is_only_offered_run_command_when_enabled(cmd_settings):
    off = Script({"message": {"content": "ok"}})
    cmd_settings.commands.enabled = False
    first = JobManager(cmd_settings, OllamaPlanner(LlmConfig(provider="ollama"), off))
    first.submit("hi")
    first.join_background()  # wait for the model to have been asked, or the check below can run before it was
    on = Script({"message": {"content": "ok"}})
    cmd_settings.commands.enabled = True
    manager = JobManager(cmd_settings, OllamaPlanner(LlmConfig(provider="ollama"), on))
    manager.submit("hi")
    manager.join_background()
    names = lambda s: {t["function"]["name"] for t in s.requests[0][1]["tools"]}  # noqa: E731
    assert "run_command" not in names(off) and "run_command" in names(on)


# --- move and copy ------------------------------------------------------------

def test_move_waits_for_approval_and_can_be_undone(ro_settings):
    ws = ro_settings.agent.allowed_folders[0]
    (ws / "a.txt").write_text("hi", encoding="utf-8")
    manager = JobManager(ro_settings)
    job = manager.submit("move a.txt to sub/b.txt")
    assert job.status is Status.AWAITING and (ws / "a.txt").exists()
    manager.approve(job.id)
    assert job.status is Status.DONE and not (ws / "a.txt").exists() and (ws / "sub" / "b.txt").read_text() == "hi"
    manager.undo(job.id)
    assert (ws / "a.txt").read_text() == "hi" and not (ws / "sub" / "b.txt").exists()


def test_copy_from_a_read_only_folder_into_the_workspace(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    ws = ro_settings.agent.allowed_folders[0]
    manager = JobManager(ro_settings)
    job = manager.submit(f"copy {ref}/a.bak to copied.bak")
    manager.approve(job.id)
    assert job.status is Status.DONE and (ws / "copied.bak").read_text() == "1" and (ref / "a.bak").exists()
    manager.undo(job.id)
    assert not (ws / "copied.bak").exists()


def test_nothing_can_be_moved_or_copied_into_a_read_only_folder(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    ws = ro_settings.agent.allowed_folders[0]
    (ws / "a.txt").write_text("x", encoding="utf-8")
    manager = JobManager(ro_settings)
    assert "read-only" in manager.submit(f"move a.txt to {ref}/a.txt").error
    assert "read-only" in manager.submit(f"copy a.txt to {ref}/a.txt").error
    assert "read-only" in manager.submit(f"move {ref}/a.bak to gone.bak").error  # moving removes the original
    assert (ref / "a.bak").exists()


def test_move_will_not_overwrite_an_existing_file(ro_settings):
    ws = ro_settings.agent.allowed_folders[0]
    (ws / "a.txt").write_text("a", encoding="utf-8")
    (ws / "b.txt").write_text("b", encoding="utf-8")
    manager = JobManager(ro_settings)
    job = manager.submit("move a.txt to b.txt")
    manager.approve(job.id)
    assert job.status is Status.FAILED and "already exists" in job.error
    assert (ws / "a.txt").read_text() == "a" and (ws / "b.txt").read_text() == "b"


# --- conversation memory ------------------------------------------------------

def test_earlier_requests_are_sent_to_the_model_so_follow_ups_work(ro_settings):
    script = Script({"message": {"content": "Found notes.txt."}}, {"message": {"content": "Done."}})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    manager.submit("find notes")
    manager.join_background()
    time.sleep(0.01)
    manager.submit("now delete that one")
    manager.join_background()
    second = script.requests[1][1]["messages"]
    roles = [m["role"] for m in second]
    assert roles == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == "find notes" and "Found notes.txt." in second[2]["content"]
    assert second[3]["content"] == "now delete that one"


def test_memory_can_be_turned_off(ro_settings):
    ro_settings.llm.memory_turns = 0
    script = Script({"message": {"content": "one"}}, {"message": {"content": "two"}})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    manager.submit("first")
    manager.join_background()
    time.sleep(0.01)
    manager.submit("second")
    manager.join_background()
    assert [m["role"] for m in script.requests[1][1]["messages"]] == ["system", "user"]


# --- the hosted model ---------------------------------------------------------

def tool_use(name, **args):
    return {"type": "tool_use", "id": "tu_1", "name": name, "input": args}


def test_hosted_planner_speaks_the_messages_api(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script(
        {"content": [{"type": "text", "text": "Looking."}, tool_use("find_files", pattern="*.bak", path=str(ref))]},
        {"content": [{"type": "text", "text": "Two backup files."}]},
    )
    llm = LlmConfig(provider="anthropic")
    manager = JobManager(ro_settings, AnthropicPlanner(llm, "key", script))
    job = manager.submit("do we have .bak files?")
    manager.join_background()
    assert job.status is Status.DONE and job.outputs == ["Two backup files."]
    url, first = script.requests[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert first["model"] == "claude-sonnet-5-5" and first["max_tokens"] == 2048 and "stream" not in first
    assert first["messages"][-1] == {"role": "user", "content": "do we have .bak files?"}
    assert all({"name", "description", "input_schema"} <= set(t) for t in first["tools"])
    result = script.requests[1][1]["messages"][-1]
    assert result["role"] == "user" and result["content"][0]["type"] == "tool_result"
    assert result["content"][0]["tool_use_id"] == "tu_1" and "a.bak" in result["content"][0]["content"]


def test_hosted_planner_can_propose_a_change(ro_settings):
    script = Script({"content": [tool_use("write_file", path="n.txt", content="hi")]}, {"content": [{"type": "text", "text": "Ready."}]})
    manager = JobManager(ro_settings, AnthropicPlanner(LlmConfig(provider="anthropic"), "key", script))
    job = manager.submit("make a note")
    manager.join_background()
    assert job.status is Status.AWAITING and job.steps[0].action is Action.WRITE


def test_a_rejected_api_key_gives_a_helpful_message():
    def reject(url, payload, timeout):
        raise PlanError("Anthropic returned HTTP 401: invalid x-api-key")

    with pytest.raises(PlanError, match="ANTHROPIC_API_KEY"):
        AnthropicPlanner(LlmConfig(provider="anthropic"), "bad", reject).plan("hello")


def test_hosted_provider_needs_a_key_and_says_so(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("handoff.planner.read_key", lambda name: "")  # this PC may really have a key saved
    planner, note = build_planner(LlmConfig(provider="anthropic"))
    assert isinstance(planner, DemoPlanner) and "ANTHROPIC_API_KEY" in note


def test_hosted_provider_warns_that_data_leaves_the_pc(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    planner, note = build_planner(LlmConfig(provider="anthropic"))
    assert isinstance(planner, AnthropicPlanner) and "HOSTED" in note and "sent to the provider" in note


def test_auto_never_picks_the_hosted_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def no_ollama(url, payload, timeout):
        raise PlanError("Cannot reach Ollama")

    planner, _ = build_planner(LlmConfig(provider="auto"), no_ollama)
    assert isinstance(planner, DemoPlanner)


def test_http_helper_sends_the_headers_it_is_given():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["key"] = self.headers.get("x-api-key")
            body = json.dumps({"content": []}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/messages"
        assert _http_json(url, {"a": 1}, 5, headers={"x-api-key": "secret"}, service="Anthropic") == {"content": []}
    finally:
        server.shutdown()
    assert seen["key"] == "secret"


def test_unreachable_hosted_service_names_the_right_service():
    with pytest.raises(PlanError, match="Cannot reach Anthropic"):
        _http_json("http://127.0.0.1:9/v1/messages", {}, 2, service="Anthropic", hint="Check the internet connection.")


def test_run_step_shows_the_exact_command_and_warns():
    detail = Step(Action.RUN, ".", "npm test").detail()
    assert detail.startswith("$ npm test") and "cannot be undone" in detail


def test_a_pasted_key_in_api_key_env_is_never_echoed(monkeypatch):
    pasted = "sk-ant-pasted-by-mistake"
    planner, note = build_planner(LlmConfig(provider="anthropic", api_key_env=pasted))
    assert isinstance(planner, DemoPlanner) and "NAME of an environment variable" in note and pasted not in note


@pytest.mark.parametrize("command", ["Get-Date -Format yyyy-MM-dd", "Get-Process | Format-Table Name", "git log --format=%H"])
def test_everyday_commands_that_mention_format_are_not_blocked(cmd_settings, command):
    policy = PolicyEngine(cmd_settings.agent.allowed_folders, cmd_settings.approval)
    CommandRunner(cmd_settings.commands, policy, AuditLog(cmd_settings.logging.audit_file)).check(command)


@pytest.mark.parametrize("command", ["format C:", "Format d: /q", "echo hi; format E:", "Format-Volume -DriveLetter D"])
def test_formatting_a_disk_is_still_blocked(cmd_settings, command):
    policy = PolicyEngine(cmd_settings.agent.allowed_folders, cmd_settings.approval)
    with pytest.raises(CommandRefused, match="blocked"):
        CommandRunner(cmd_settings.commands, policy, AuditLog(cmd_settings.logging.audit_file)).check(command)
