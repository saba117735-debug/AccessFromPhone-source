"""Making a slow model cheaper to use: small edits, small prompts, capped answers, and no needless model calls."""
import copy
import os
import time

import pytest

from handoff.config import LlmConfig
from handoff.jobs import JobManager, Status
from handoff.planner import CUT_OFF, OllamaPlanner, mentioned_paths


class Script:
    def __init__(self, *messages, **extra):
        self.messages, self.requests, self.extra = list(messages), [], extra

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0), **self.extra}


def tool(name, **args):
    return {"tool_calls": [{"function": {"name": name, "arguments": args}}]}


def manager_with(settings, *messages, **extra):
    script = Script(*messages, **extra)
    return JobManager(settings, OllamaPlanner(LlmConfig(provider="ollama"), script)), script


def ask(manager, prompt, conv="c"):
    time.sleep(0.01)
    job = manager.submit(prompt, conv)
    manager.join_background()
    return job


@pytest.fixture
def ws(ro_settings):
    folder = ro_settings.agent.allowed_folders[0]
    (folder / "app.cfg").write_text("name=demo\nport=8080\nmode=dev\n", encoding="utf-8")
    return folder


# --- editing by saying what changes, not by retyping the file -------------------------------

def test_the_model_edits_with_old_and_new_text_only(ro_settings, ws):
    manager, script = manager_with(ro_settings, tool("edit_file", path=str(ws / "app.cfg"), old="port=8080", new="port=9090"), {"content": "Ready."})
    job = ask(manager, "change the port in app.cfg to 9090")
    assert job.status is Status.AWAITING and job.steps[0].detail() == "replace “port=8080” with “port=9090”"
    assert (ws / "app.cfg").read_text() == "name=demo\nport=8080\nmode=dev\n"  # nothing changes until approval
    manager.approve(job.id)
    assert (ws / "app.cfg").read_text() == "name=demo\nport=9090\nmode=dev\n"  # everything else is untouched
    manager.undo(job.id)
    assert "port=8080" in (ws / "app.cfg").read_text()


def test_an_edit_that_does_not_match_is_explained_to_the_model(ro_settings, ws):
    manager, script = manager_with(ro_settings,
        tool("edit_file", path=str(ws / "app.cfg"), old="port=1234", new="x"), {"content": "I could not find it."})
    ask(manager, "change the port in app.cfg")
    assert "not in the file" in script.requests[1]["messages"][-1]["content"]


def test_an_edit_that_matches_twice_asks_for_more_context(ro_settings, ws):
    (ws / "twice.txt").write_text("a=1\na=1\n", encoding="utf-8")
    manager, script = manager_with(ro_settings, tool("edit_file", path=str(ws / "twice.txt"), old="a=1", new="a=2"), {"content": "ok"})
    ask(manager, "change a in twice.txt")
    assert "appears 2 times" in script.requests[1]["messages"][-1]["content"]


def test_the_model_is_told_to_edit_not_to_retype(ro_settings, ws):
    manager, script = manager_with(ro_settings, {"content": "ok"})
    ask(manager, "hello")
    system = script.requests[0]["messages"][0]["content"]
    assert "edit_file" in system and "Never rewrite a whole big file" in system and "ask what change" in system


# --- "replace X with Y in <file>" needs no model at all --------------------------------------

def test_replace_x_with_y_is_done_by_the_app(ro_settings, ws):
    manager, script = manager_with(ro_settings)  # a model call would raise
    job = ask(manager, f'replace "mode=dev" with "mode=prod" in app.cfg in {ws}')
    assert job.status is Status.AWAITING and "Replace “mode=dev” with “mode=prod” in app.cfg" in job.summary
    manager.approve(job.id)
    assert (ws / "app.cfg").read_text().endswith("mode=prod\n") and script.requests == []


@pytest.mark.parametrize("prompt", [
    "please change port=8080 to port=9000 in {ws}/app.cfg",
    "replace port=8080 with port=9000 in app.cfg in {ws}",
    "Can you replace 'port=8080' by 'port=9000' in {ws}\\app.cfg?",
])
def test_ways_of_asking_for_a_replacement(ro_settings, ws, prompt):
    manager, _ = manager_with(ro_settings)
    job = ask(manager, prompt.format(ws=ws))
    assert job.status is Status.AWAITING and job.steps[0].edit == ("port=8080", "port=9000")


def test_a_replacement_in_it_means_the_last_file(ro_settings, ws):
    manager, _ = manager_with(ro_settings)
    ask(manager, f"read app.cfg in {ws}")
    job = ask(manager, 'replace "demo" with "live" in that file')
    assert job.status is Status.AWAITING and job.steps[0].path.endswith("app.cfg")
    manager.approve(job.id)
    assert "name=live" in (ws / "app.cfg").read_text()


def test_a_replacement_with_a_file_name_typo_still_works(ro_settings, ws):
    manager, _ = manager_with(ro_settings)
    job = ask(manager, f"replace demo with live in app.cgf in {ws}")
    assert job.status is Status.AWAITING and job.steps[0].path.endswith("app.cfg")


@pytest.mark.parametrize("prompt, words", [
    ("replace nothing-like-this with x in app.cfg in {ws}", "not in the file"),
    ("replace a with b in missing.txt in {ws}", "no file called missing.txt"),
])
def test_a_replacement_that_cannot_be_made_says_why(ro_settings, ws, prompt, words):
    job = ask(manager_with(ro_settings)[0], prompt.format(ws=ws))
    assert job.status is Status.DONE and job.steps == [] and words in job.outputs[0]


def test_a_replacement_in_a_read_only_folder_is_refused_before_approval(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = ask(manager_with(ro_settings)[0], f"replace 1 with 2 in a.bak in {ref}")
    assert job.status is Status.FAILED and "read-only" in job.error and (ref / "a.bak").read_text() == "1"


# --- a runaway answer is cut off and never used ------------------------------------------------------

def test_a_change_cut_off_mid_answer_is_never_applied(ro_settings, ws):
    manager, _ = manager_with(ro_settings, tool("write_file", path=str(ws / "app.cfg"), content="half a fi"), done_reason="length")
    job = ask(manager, "rewrite app.cfg completely")
    assert job.status is Status.FAILED and job.error == CUT_OFF
    assert "name=demo" in (ws / "app.cfg").read_text()


def test_half_a_tool_call_written_as_text_is_not_shown(ro_settings, ws):
    manager, _ = manager_with(ro_settings, {"content": '{"name": "write_file", "arguments": {"path": "x", "con'}, done_reason="length")
    job = ask(manager, "hello there")
    assert job.status is Status.FAILED and job.error == CUT_OFF


def test_a_long_plain_answer_is_kept_with_a_note(ro_settings):
    manager, _ = manager_with(ro_settings, {"content": "Here is a very long explanation that"}, done_reason="length")
    job = ask(manager, "explain things")
    assert job.status is Status.DONE and job.outputs[0].startswith("Here is a very long") and "cut off" in job.outputs[0]


# --- the speed settings reach the model ----------------------------------------------------------------

@pytest.mark.parametrize("setting, expected", [
    ("auto", max(1, (os.cpu_count() or 4) - 2)), (6, 6), (None, None),
])
def test_threads_are_chosen_not_left_to_ollama(ro_settings, setting, expected):
    script = Script({"content": "hi"})
    planner = OllamaPlanner(LlmConfig(provider="ollama", num_thread=setting), script)
    planner.plan("hello")
    options = script.requests[0]["options"]
    assert options.get("num_thread") == expected and options["num_ctx"] == 4096 and options["num_predict"] == 700


def test_the_prompt_the_model_must_read_stays_small(ro_settings):
    import json

    from handoff.planner import SYSTEM_PROMPT, TOOLS

    assert len(SYSTEM_PROMPT) < 1100 and len(json.dumps(TOOLS)) < 2400  # about 800 tokens in all, read once then cached


def test_only_a_slice_of_a_big_file_is_handed_to_the_model(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    (ref / "big.txt").write_text("x" * 20000, encoding="utf-8")
    manager, script = manager_with(ro_settings, {"content": "It is all x."})
    ask(manager, f"please summarise big.txt in {ref}")
    sent = script.requests[0]["messages"][-1]["content"]
    assert "(truncated)" in sent and sent.count("x") <= LlmConfig().file_chars + 50


# --- "that file", and no wasted model calls --------------------------------------------------------------

def test_a_question_about_that_file_gives_the_model_the_file_from_the_chat(ro_settings, ws):
    manager, script = manager_with(ro_settings, {"content": "The port is 8080."})
    ask(manager, f"read app.cfg in {ws}")
    ask(manager, "what port does that file use?")
    sent = script.requests[0]["messages"][-1]["content"]
    assert "port=8080" in sent and "FILE" in sent  # it did not go off searching


def test_a_folder_that_is_not_allowed_is_asked_about_before_any_model_runs(ro_settings, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.log").write_text("log", encoding="utf-8")
    manager, script = manager_with(ro_settings)  # any model call would raise
    job = ask(manager, f"what does x.log say in {elsewhere}?")
    assert job.status is Status.NEEDS_ACCESS and script.requests == []


def test_changes_outside_are_not_turned_into_questions(ro_settings, tmp_path):
    assert mentioned_paths(r"create C:\Somewhere\x.txt with hi") == []
    assert mentioned_paths(r"delete C:\Somewhere\x.txt") == []
    assert mentioned_paths(r"edit C:\Somewhere\x.txt") == []
    assert mentioned_paths(r"what is in C:\Somewhere\x.txt, please.") == [r"C:\Somewhere\x.txt"]
    job = ask(JobManager(ro_settings), f"create {tmp_path / 'elsewhere' / 'x.txt'} with hi")  # the typed-command planner
    assert job.status is Status.FAILED and "for changes" in job.error


def test_the_chat_memory_is_kept_short(ro_settings):
    assert LlmConfig().memory_turns == 4
