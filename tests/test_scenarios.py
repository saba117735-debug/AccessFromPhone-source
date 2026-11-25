"""Many ways real people ask about a file: typos, capitals, missing extensions, odd phrasing, missing things."""
import copy
import time

import pytest

from handoff.config import Action, LlmConfig
from handoff.jobs import JobManager, Status
from handoff.names import best_match
from handoff.planner import OllamaPlanner


@pytest.fixture
def folder(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    files = {
        "actionsClient.ts": "export const actions = 1;",
        "actionsClient.test.ts": "test('actions', () => {});",
        "AskCore.tsx": "export function AskCore() {}",
        "AskCore.test.tsx": "test('core', () => {});",
        "README.md": "# Readme text",
        ".gitignore": "node_modules",
        "notes.txt": "plain notes",
        "my notes.txt": "spaced notes",
    }
    for name, text in files.items():
        (ref / name).write_text(text, encoding="utf-8")
    return ref


class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def run(settings, prompt, conv="c", model=None):
    """The model is never expected to be called unless one is given: any call would raise."""
    manager = JobManager(settings, OllamaPlanner(LlmConfig(provider="ollama"), model or Script()))
    job = manager.submit(prompt, conv)
    manager.join_background()
    return job


# --- answered by the app itself, whatever the typo ------------------------------------

@pytest.mark.parametrize("prompt, expect", [
    ("read actionsClient.ts in {ref}", "export const actions"),
    ("Read actionClient.ts file in this path {ref}", "export const actions"),        # the typo from the screenshot
    ("Read actionsClent.ts in {ref}", "export const actions"),                        # a different typo
    ("show ACTIONSCLIENT.TS from {ref}", "export const actions"),                     # capitals
    ("open README in {ref}", "Readme text"),                                          # no extension
    ("read readme.md in {ref}", "Readme text"),
    ("read AskCore.ts in {ref}", "export function AskCore"),                          # wrong extension
    ("show me contents of notes.txt in the path {ref}", "plain notes"),               # filler words first
    ("can you please display the file notes.txt from {ref}?", "plain notes"),
    ("read .gitignore in {ref}", "node_modules"),                                     # a dotfile
    ("cat notes.txt at {ref}", "plain notes"),
    ('read "my notes.txt" in "{ref}"', "spaced notes"),                               # quotes and a space
    ("read notes.txt in {fwd}", "plain notes"),                                       # forward slashes
    ("read notes.txt in {ref}/", "plain notes"),                                      # trailing slash
    ("read {ref}/notes.txt", "plain notes"),                                          # one full path
])
def test_the_app_reads_the_right_file_without_the_model(ro_settings, folder, prompt, expect):
    job = run(ro_settings, prompt.format(ref=folder, fwd=str(folder).replace("\\", "/")))
    assert job.status is Status.DONE and expect in job.outputs[0], (job.summary, job.outputs, job.error)


def test_a_corrected_name_is_said_out_loud(ro_settings, folder):
    job = run(ro_settings, f"Read actionClient.ts file in this path {folder}")
    assert "actionsClient.ts" in job.summary and "you typed actionClient.ts" in job.summary
    assert job.steps[0].path.endswith("actionsClient.ts")


def test_a_download_with_a_typo_is_corrected_and_still_asks_first(ro_settings, folder):
    job = run(ro_settings, f"download actionClient.ts from {folder}")
    assert job.status is Status.AWAITING and job.steps[0].action is Action.SEND
    assert job.steps[0].path.endswith("actionsClient.ts") and "you typed" in job.summary


def test_two_close_files_are_offered_not_guessed(ro_settings, folder):
    job = run(ro_settings, f"read actions.ts in {folder}")
    assert job.status is Status.DONE and job.steps == []
    assert "Did you mean" in job.outputs[0] and "actionsClient.ts" in job.outputs[0]


@pytest.mark.parametrize("prompt, words", [
    ("read zzz.txt in {ref}", "no file called zzz.txt"),
    ("read a.txt in {ref}/nodir", "can't find the folder"),
    ("download nothing-here.pdf from {ref}", "no file called nothing-here.pdf"),
])
def test_what_is_missing_is_said_plainly_and_nothing_is_invented(ro_settings, folder, prompt, words):
    job = run(ro_settings, prompt.format(ref=folder))
    assert job.status is Status.DONE and words in job.outputs[0]


def test_a_follow_up_with_a_typo_uses_the_chat_folder_and_corrects_it(ro_settings, folder):
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), Script({"content": "Noted."}, {"content": "Noted."})))
    manager.submit(f"my code is in {folder}", "chat")
    manager.join_background()
    time.sleep(0.01)
    job = manager.submit("read actionClient.ts", "chat")
    manager.join_background()
    assert job.status is Status.DONE and "export const actions" in job.outputs[0]


def test_other_chats_do_not_borrow_this_chats_folder(ro_settings, folder):
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), Script({"content": "Noted."}, {"content": "Noted."}, {"content": "Which folder?"})))
    manager.submit(f"my code is in {folder}", "chat-1")
    manager.join_background()
    time.sleep(0.01)
    job = manager.submit("read actionsClient.ts", "chat-2")
    manager.join_background()
    assert job.outputs == ["Which folder?"] or job.status is Status.DONE  # it went to the model, not to chat-1's folder
    assert "export const actions" not in " ".join(job.outputs)


# --- the model gets the truth when the request is longer than a plain command ---------

def test_the_exact_sentence_from_the_screenshot(ro_settings, folder):
    script = Script({"content": "It exports one constant."})
    job = run(ro_settings, f"Hi please get summaries this file actionClient.ts which us there in this path {folder}", model=script)
    sent = script.requests[0]["messages"][-1]["content"]
    assert 'the real file is "actionsClient.ts"' in sent and "export const actions" in sent
    assert "Do not just say where the file is" in sent
    assert job.outputs == ["It exports one constant."]
    assert any("actionsClient.ts" in line for line in job.activity)


def test_a_missing_file_tells_the_model_not_to_dump_the_folder(ro_settings, folder):
    script = Script({"content": "There is no such file."})
    run(ro_settings, f"please summarise quarterly-report.docx in {folder}", model=script)
    sent = script.requests[0]["messages"][-1]["content"]
    assert 'no file named "quarterly-report.docx"' in sent and "Do not list the whole folder" in sent


def test_when_the_model_picks_a_wrong_name_its_tool_error_names_the_right_one(ro_settings, folder):
    script = Script(
        {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": str(folder / "actionClient.ts")}}}]},
        {"content": "Done."},
    )
    run(ro_settings, "tell me what the actions client does", model=script)
    tool_reply = script.requests[1]["messages"][-1]["content"]
    assert tool_reply.startswith("error:") and "Did you mean: actionsClient.ts" in tool_reply


# --- the matcher on its own ---------------------------------------------------------

NAMES = ["actionsClient.ts", "actionsClient.test.ts", "AskCore.tsx", "AskCore.test.tsx", "README.md", "package.json", "package-lock.json"]


@pytest.mark.parametrize("wanted, auto", [
    ("actionClient.ts", "actionsClient.ts"), ("ASKCORE.TSX", "AskCore.tsx"), ("readme", "README.md"),
    ("readme.txt", "README.md"), ("AskCore", "AskCore.tsx"), ("Askcore.ts", "AskCore.tsx"), ("pakage.json", "package.json"),
])
def test_obvious_typos_are_matched(wanted, auto):
    assert best_match(wanted, NAMES).auto == auto


@pytest.mark.parametrize("wanted", ["zzz.txt", "invoice.pdf", "x", "completely-different-name.rs"])
def test_unrelated_names_match_nothing(wanted):
    assert best_match(wanted, NAMES) == best_match("zzz", [])


def test_a_tie_is_offered_not_decided():
    match = best_match("actions.ts", NAMES)
    assert match.auto is None and {"actionsClient.ts", "actionsClient.test.ts"} <= set(match.maybe)
