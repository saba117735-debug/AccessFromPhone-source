"""A question about files must be answered from the files, not from the chat or from imagination."""
import copy
import time

import pytest

from handoff.config import Action, LlmConfig
from handoff.jobs import JobManager, Status
from handoff.planner import NUDGE, UNVERIFIED, AnthropicPlanner, OllamaPlanner, needs_look, quick_request

PHRASE = r"Read .gitignore file in this path C:\PageLogin\login-page"


# --- plain requests never reach the model -------------------------------------

def test_the_phrase_that_went_wrong_is_now_a_direct_read():
    plan = quick_request(PHRASE)
    assert plan.steps[0].action is Action.READ
    assert plan.steps[0].path == r"C:\PageLogin\login-page\.gitignore"


@pytest.mark.parametrize("prompt, path", [
    (r"show me notes.txt in C:\Work", r"C:\Work\notes.txt"),
    (r"Can you open the file report.csv from C:\Data\2024?", r"C:\Data\2024\report.csv"),
    (r"read C:\PageLogin\login-page\package.json", r"C:\PageLogin\login-page\package.json"),
    (r'view "my notes.txt" in "C:\My Folder"', r"C:\My Folder\my notes.txt"),
    (r"cat README.md at C:\proj", r"C:\proj\README.md"),
])
def test_direct_reads(prompt, path):
    plan = quick_request(prompt)
    assert plan.steps[0].action is Action.READ and plan.steps[0].path == path


@pytest.mark.parametrize("prompt", [
    "Read AskCore.tsx file about 20lines",     # which folder? let the model use the chat
    "tell me about AskCore.tsx",
    "read it", "show me", "open the file",
    "show files in my workspace",
    "what does read mean?",
    "hi",
])
def test_vague_requests_still_go_to_the_model(prompt):
    assert quick_request(prompt) is None


def test_the_direct_read_runs_at_once_and_shows_the_real_file(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"Read a.bak file in this path {ref}")
    assert job.status is Status.DONE and job.outputs == ["1"]


def test_a_direct_read_of_a_missing_file_says_so_instead_of_inventing(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"Read nothing.txt file in this path {ref}")
    assert job.status is Status.DONE and "no file called nothing.txt" in job.outputs[0]


# --- a model that does not look is sent back to look ---------------------------

class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def call(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_needs_look_spots_questions_about_files():
    for text in ("summarise AskCore.tsx", "is there a .env file?", r"what is in C:\x", "list my folders", "check notes.txt"):
        assert needs_look(text), text
    for text in ("hi", "what can you do?", "tell me a joke", "thanks!"):
        assert not needs_look(text), text


def test_answering_about_files_without_looking_triggers_one_nudge(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "AskCore.tsx is a React component for the orb."},      # a guess
                    call("read_file", path=str(ref / "a.bak")),                         # after the nudge it looks
                    {"content": "It contains: 1"})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("tell me about a.bak")
    manager.join_background()
    assert job.status is Status.DONE and job.outputs == ["It contains: 1"] and UNVERIFIED not in job.outputs[0]
    second = script.requests[1]["messages"]
    assert second[-1] == {"role": "user", "content": NUDGE}
    assert second[-2]["role"] == "assistant"
    assert job.activity and job.activity[0].startswith("read ")


def test_a_model_that_still_will_not_look_is_flagged_not_trusted(ro_settings):
    script = Script({"content": "It is a component."}, {"content": "It is definitely a component."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("tell me about AskCore.tsx")
    manager.join_background()
    assert job.status is Status.DONE and job.outputs[0].endswith(UNVERIFIED)
    assert len(script.requests) == 2  # one nudge, not a loop


def test_chat_is_never_nudged(ro_settings):
    script = Script({"content": "Hello! I can work with your files."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("hi, what can you do?")
    manager.join_background()
    assert job.outputs == ["Hello! I can work with your files."] and len(script.requests) == 1


def test_a_model_that_looked_is_not_nudged_or_flagged(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script(call("list_files", path=str(ref)), {"content": "Two items."})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), script))
    job = manager.submit("what files are in the reference folder?")
    manager.join_background()
    assert job.outputs == ["Two items."] and len(script.requests) == 2


def test_the_hosted_planner_gets_the_same_nudge(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": [{"type": "text", "text": "Probably a config file."}]},
                    {"content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": str(ref / "a.bak")}}]},
                    {"content": [{"type": "text", "text": "It holds: 1"}]})
    seen = []

    def transport(url, payload, timeout):
        seen.append(copy.deepcopy(payload))
        return script(url, payload, timeout)["message"] if False else {"content": script.messages.pop(0)["content"]}

    manager = JobManager(ro_settings, AnthropicPlanner(LlmConfig(provider="anthropic"), "k", transport))
    job = manager.submit("what is in a.bak?")
    manager.join_background()
    assert job.outputs == ["It holds: 1"]
    assert seen[1]["messages"][-1] == {"role": "user", "content": NUDGE}


# --- chat memory cannot be copied in place of looking ----------------------------

def test_old_file_listings_are_shortened_in_the_chat_memory():
    from datetime import datetime, timezone

    from handoff.jobs import Job
    from handoff.planner import Step

    listing = ", ".join(f"a-very-long-file-name-number-{i:03d}.txt" for i in range(60))
    read = Job("a", "list", datetime.now(timezone.utc), status=Status.DONE, steps=[Step(Action.READ, ".")], outputs=[listing])
    chat = Job("b", "hi", datetime.now(timezone.utc), status=Status.DONE, outputs=["Hello! " * 100])
    remembered = JobManager._describe(read)
    assert "shortened" in remembered and "number-059" not in remembered and len(remembered) < 500
    assert 400 < len(JobManager._describe(chat)) <= 600  # the AI's own replies are kept, but short: reading costs time


# --- the app reads the named file itself, so the model cannot guess --------------

from handoff.planner import last_folder  # noqa: E402


def ask(settings, script, prompt, conv="c1"):
    manager = ASK.setdefault(id(settings), JobManager(settings, OllamaPlanner(LlmConfig(provider="ollama"), script)))
    job = manager.submit(prompt, conv)
    manager.join_background()
    time.sleep(0.01)
    return job


ASK: dict = {}


def last_user_message(script, index=0):
    return script.requests[index]["messages"][-1]["content"]


def test_a_named_file_is_read_by_the_app_and_handed_to_the_model(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "It holds the number 1."})
    job = ask(ro_settings, script, f"Please summarise a.bak in {ref}")
    message = last_user_message(script)
    assert "FILE" in message and "a.bak" in message and "\n1" in message and "do not guess" in message
    assert job.outputs == ["It holds the number 1."] and UNVERIFIED not in job.outputs[0]
    assert any(a.startswith("read ") for a in job.activity)  # visible on the phone as "Looked at"
    assert len(script.requests) == 1  # no nudge needed


def test_a_missing_file_is_reported_as_a_fact(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "That file is not there."})
    ask(ro_settings, script, f"Please check nothing.txt is present in {ref}")
    assert 'there is no file named "nothing.txt"' in last_user_message(script)


def test_a_named_folder_is_listed_for_the_model(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "Two things."})
    ask(ro_settings, script, f"What is in {ref}?")
    message = last_user_message(script)
    assert f"FOLDER {ref}" in message and "a.bak" in message and "deep" in message


@pytest.mark.parametrize("verb", ["find", "delete", "create", "move", "search for"])
def test_changes_and_searches_are_left_to_the_models_tools(ro_settings, verb):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "ok"})
    prompt = f"{verb} a.bak in {ref}"
    ask(ro_settings, script, prompt)
    assert last_user_message(script) == prompt  # nothing was added: the model's tools handle it


def test_a_kind_of_file_is_not_mistaken_for_one_file(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "Two."})
    ask(ro_settings, script, f"which .bak files are in {ref}")
    assert "could not be read" not in last_user_message(script)


def test_follow_ups_reuse_the_folder_from_earlier_in_the_chat(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "Noted."}, {"content": "It holds 1."})
    ask(ro_settings, script, f"my project is in {ref}", "chat-x")
    ask(ro_settings, script, "summarise a.bak", "chat-x")
    assert "FILE" in last_user_message(script, 1) and "\n1" in last_user_message(script, 1)


def test_a_bare_read_follow_up_is_answered_directly_from_that_folder(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    script = Script({"content": "Noted."})  # a second model call would raise
    ask(ro_settings, script, f"my project is in {ref}", "chat-y")
    job = ask(ro_settings, script, "read a.bak", "chat-y")
    assert job.status is Status.DONE and job.outputs == ["1"] and len(script.requests) == 1


def test_last_folder_picks_the_latest_one_the_user_named():
    history = [
        {"role": "user", "content": r"look in C:\One\a"}, {"role": "assistant", "content": r"C:\Not\Me"},
        {"role": "user", "content": r"now summarise C:\Two\b\notes.txt please"},
    ]
    assert last_folder(history) == r"C:\Two\b"
    assert last_folder([{"role": "user", "content": "hello"}]) is None


def test_a_request_for_some_lines_reads_only_those_lines_directly():
    step = quick_request("Read x.tsx about 20 lines in C:/a").steps[0]
    assert step.lines == 20 and step.path.replace("\\", "/") == "C:/a/x.tsx"
