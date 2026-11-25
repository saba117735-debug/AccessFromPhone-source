"""Guards against a small model answering about the wrong folder."""
import copy

from handoff.config import LlmConfig
from handoff.jobs import JobManager, Status
from handoff.planner import OllamaPlanner


class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def find(**args):
    return {"tool_calls": [{"function": {"name": "find_files", "arguments": args}}]}


def tool_result(script, request_index):
    return script.requests[request_index]["messages"][-1]["content"]


def test_search_without_a_folder_is_an_error_the_model_must_fix(ro_settings):
    ref = str(ro_settings.agent.read_only_folders[0])
    script = Script(find(pattern="*.bak"), find(pattern="*.bak", path=ref), {"content": "Two."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("do we have .bak files in the reference folder?")
    manager.join_background()
    assert tool_result(script, 1).startswith("error: 'path' is required")
    assert job.status is Status.DONE and job.outputs == ["Two."]


def test_search_results_name_the_folder_that_was_searched(ro_settings):
    ref = str(ro_settings.agent.read_only_folders[0])
    script = Script(find(pattern="*.zip", path=ref), {"content": "None."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    manager.submit("any zip files?")
    manager.join_background()
    assert tool_result(script, 1).startswith(f"Searched {ref}")


def test_a_search_of_the_wrong_folder_is_visible_in_the_result(ro_settings):
    workspace = str(ro_settings.agent.allowed_folders[0])
    script = Script(find(pattern="*.bak", path="."), {"content": "None."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    manager.submit("do we have .bak files in the reference folder?")
    manager.join_background()
    assert tool_result(script, 1).startswith(f"Searched {workspace}")  # not the reference folder


def test_the_job_records_what_the_model_looked_at(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script(find(pattern="*.bak", path=str(ref)), {"content": "Two."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("check")
    manager.join_background()
    assert job.to_dict()["activity"] == [f"searched {ref} for *.bak: 2 found"]


def test_activity_is_kept_when_the_job_fails(ro_settings):
    def broken(url, payload, timeout):
        raise RuntimeError("boom")

    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), broken))
    job = manager.submit("hi")
    manager.join_background()
    assert job.status is Status.FAILED and job.to_dict()["activity"] == []
