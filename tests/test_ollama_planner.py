import copy

import pytest
from pydantic import ValidationError

from handoff.config import Action, LlmConfig
from handoff.planner import DemoPlanner, OllamaPlanner, PlanError, Step, build_planner


def llm(**overrides):
    return LlmConfig(provider="ollama", model="qwen2.5:7b", **overrides)


class Script:
    """Fake Ollama: hands back scripted assistant messages and records requests."""

    def __init__(self, *messages):
        self.messages = list(messages)
        self.requests = []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))  # the planner keeps extending its list
        return {"message": self.messages.pop(0)}


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


class FakeContext:
    def __init__(self, files=None):
        self.files = files or {}

    def list_dir(self, path):
        return sorted(self.files)

    def read_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(f"{path} not found")
        return self.files[path]


def test_plain_chat_becomes_a_reply():
    plan = OllamaPlanner(llm(), Script({"content": "Hello! How can I help?"})).plan("hi")
    assert plan.reply == "Hello! How can I help?"
    assert plan.steps == []


def test_write_is_only_proposed_never_run():
    script = Script(
        {"tool_calls": [call("write_file", path="todo.txt", content="milk")]},
        {"content": "I will create todo.txt."},
    )
    plan = OllamaPlanner(llm(), script).plan("make a todo file")
    assert plan.steps == [Step(Action.WRITE, "todo.txt", "milk")]
    assert plan.summary == "I will create todo.txt."
    assert plan.reply is None


def test_model_can_read_before_proposing():
    script = Script(
        {"tool_calls": [call("read_file", path="a.txt")]},
        {"tool_calls": [call("write_file", path="a.txt", content="one\ntwo")]},
        {"content": "Added a line."},
    )
    plan = OllamaPlanner(llm(), script).plan("add a line", FakeContext({"a.txt": "one"}))
    fed_back = script.requests[1]["messages"][-1]
    assert fed_back == {"role": "tool", "tool_name": "read_file", "content": "one"}
    assert plan.steps == [Step(Action.WRITE, "a.txt", "one\ntwo")]


def test_read_errors_go_back_to_the_model_not_the_user():
    script = Script(
        {"tool_calls": [call("read_file", path="missing.txt")]},
        {"content": "That file does not exist."},
    )
    plan = OllamaPlanner(llm(), script).plan("read it", FakeContext())
    assert script.requests[1]["messages"][-1]["content"].startswith("error:")
    assert plan.reply == "That file does not exist."


def test_arguments_sent_as_a_json_string_are_accepted():
    script = Script(
        {"tool_calls": [{"function": {"name": "delete_file", "arguments": '{"path": "old.txt"}'}}]},
        {"content": "Deleting old.txt."},
    )
    plan = OllamaPlanner(llm(), script).plan("remove old.txt")
    assert plan.steps == [Step(Action.DELETE, "old.txt")]


def test_reading_without_a_context_is_an_error_for_the_model():
    script = Script({"tool_calls": [call("read_file", path="a.txt")]}, {"content": "Cannot read."})
    OllamaPlanner(llm(), script).plan("read a.txt")
    assert "not available" in script.requests[1]["messages"][-1]["content"]


def test_unknown_tool_and_bad_content_are_errors_for_the_model():
    script = Script(
        {"tool_calls": [call("format_disk", path="C:"), call("write_file", path="a.txt", content=5)]},
        {"content": "Sorry."},
    )
    plan = OllamaPlanner(llm(), script).plan("go")
    tool_results = [m["content"] for m in script.requests[1]["messages"] if m["role"] == "tool"]
    assert all(r.startswith("error:") for r in tool_results) and len(tool_results) == 2
    assert plan.steps == []


def test_a_model_that_never_finishes_is_stopped():
    loop = {"tool_calls": [call("list_files")]}
    script = Script(loop, loop, loop)
    with pytest.raises(PlanError, match="did not finish within 2 steps"):
        OllamaPlanner(llm(max_rounds=2), script).plan("loop forever", FakeContext({"a": "1"}))


def test_missing_model_gives_the_pull_command():
    def not_found(url, payload, timeout):
        raise PlanError("Ollama returned HTTP 404: model 'qwen2.5:7b' not found")

    with pytest.raises(PlanError, match="ollama pull qwen2.5:7b"):
        OllamaPlanner(llm(), not_found).plan("hi")


def test_request_carries_model_tools_and_no_streaming():
    script = Script({"content": "ok"})
    OllamaPlanner(llm(temperature=0.5), script).plan("hi")
    sent = script.requests[0]
    assert sent["model"] == "qwen2.5:7b" and sent["stream"] is False
    import os

    threads = max(1, (os.cpu_count() or 4) - 2)  # "auto": two cores are left for Windows
    assert sent["options"] == {"temperature": 0.5, "num_ctx": 4096, "num_predict": 700, "num_thread": threads}
    assert {t["function"]["name"] for t in sent["tools"]} == {
        "list_files", "read_file", "find_files", "write_file", "edit_file", "delete_file", "download_file",
            "move_file", "copy_file"
    }


# --- choosing a planner from the config -------------------------------------

def tags(*names):
    return lambda url, payload, timeout: {"models": [{"name": n} for n in names]}


def unreachable(url, payload, timeout):
    raise PlanError("Cannot reach Ollama at http://127.0.0.1:11434.")


def test_demo_provider_never_contacts_ollama():
    planner, note = build_planner(LlmConfig(provider="demo"), unreachable)
    assert isinstance(planner, DemoPlanner) and "demo" in note


def test_auto_uses_ollama_when_the_model_is_installed():
    planner, note = build_planner(LlmConfig(provider="auto"), tags("qwen2.5:7b"))
    assert isinstance(planner, OllamaPlanner) and "qwen2.5:7b" in note


def test_auto_matches_a_model_name_without_a_tag():
    cfg = LlmConfig(provider="auto", model="llama3.1")
    planner, _ = build_planner(cfg, tags("llama3.1:latest"))
    assert isinstance(planner, OllamaPlanner)


def test_auto_falls_back_when_ollama_is_not_running():
    planner, note = build_planner(LlmConfig(provider="auto"), unreachable)
    assert isinstance(planner, DemoPlanner) and "Cannot reach Ollama" in note


def test_auto_falls_back_with_the_pull_hint_when_the_model_is_missing():
    planner, note = build_planner(LlmConfig(provider="auto"), tags("llama3.1:8b"))
    assert isinstance(planner, DemoPlanner) and "ollama pull qwen2.5:7b" in note


def test_ollama_provider_is_used_even_if_unreachable():
    planner, _ = build_planner(LlmConfig(provider="ollama"), unreachable)
    assert isinstance(planner, OllamaPlanner)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValidationError):
        LlmConfig(provider="skynet")
