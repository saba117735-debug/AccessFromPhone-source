"""When no AI is connected, say so plainly and say why. And find a key saved with setx, however the app was started."""
import sys
import types

import pytest

from handoff.config import LlmConfig
from handoff.jobs import JobManager, Status
from handoff.planner import AnthropicPlanner, DemoPlanner, PlanError, build_planner, read_key


# --- the message --------------------------------------------------------------------------------------

def test_a_request_the_demo_cannot_parse_explains_that_no_ai_is_connected():
    # (a request the typed commands cannot parse, such as a question)
    with pytest.raises(PlanError) as caught:
        DemoPlanner("No API key was found.").plan("what is the weather like today")
    text = str(caught.value)
    assert "no AI model is connected" in text and "Why: No API key was found." in text
    assert "These work without an AI:" in text and "read <file>" in text
    assert "The demo planner understands" not in text  # the old, confusing wording


def test_without_a_reason_the_original_help_is_kept():
    with pytest.raises(PlanError, match="The demo planner understands"):
        DemoPlanner().plan("what is the weather like today")


def test_the_reason_reaches_the_phone(ro_settings, monkeypatch):
    monkeypatch.setattr("handoff.planner.read_key", lambda name: "")
    ro_settings.llm.provider = "anthropic"
    planner, _ = build_planner(ro_settings.llm)
    job = JobManager(ro_settings, planner).submit("what is the weather like today")
    assert job.status is Status.FAILED and "no AI model is connected" in job.error and "setx ANTHROPIC_API_KEY" in job.error


@pytest.mark.parametrize("provider, reason", [("demo", "llm.provider is 'demo'"), ("ollama", None), ("auto", "not installed")])
def test_every_fallback_says_why(provider, reason):
    def no_ollama(url, payload, timeout):
        raise PlanError("Cannot reach Ollama at http://127.0.0.1:11434. Is it installed and running?")

    planner, note = build_planner(LlmConfig(provider=provider), no_ollama)
    if provider == "ollama":
        assert not isinstance(planner, DemoPlanner)
        return
    assert isinstance(planner, DemoPlanner) and planner._reason  # a reason is always on hand
    if reason and provider == "demo":
        assert reason in planner._reason


def test_a_pasted_key_is_never_echoed_in_the_reason():
    planner, note = build_planner(LlmConfig(provider="anthropic", api_key_env="sk-ant-secret-pasted"))
    assert "sk-ant-secret-pasted" not in planner._reason and "sk-ant-secret-pasted" not in note


# --- finding the key ------------------------------------------------------------------------------------

def test_the_environment_wins(monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "  from-environment  ")
    assert read_key("MY_TEST_KEY") == "from-environment"


def test_a_key_saved_with_setx_is_found_by_a_window_that_was_opened_earlier(monkeypatch):
    """The cause of the 'demo planner' message: the saved key is invisible to older windows."""
    monkeypatch.delenv("MY_TEST_KEY", raising=False)
    monkeypatch.setattr("os.name", "nt")
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = object()

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake.OpenKey = lambda root, path: Key()
    fake.QueryValueEx = lambda key, name: (" saved-with-setx ", 1) if name == "MY_TEST_KEY" else (_ for _ in ()).throw(FileNotFoundError())
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert read_key("MY_TEST_KEY") == "saved-with-setx"
    assert read_key("SOMETHING_ELSE") == ""  # nothing saved under that name


def test_no_key_anywhere_is_an_empty_string_not_an_error(monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE_XYZ", raising=False)
    assert read_key("NOT_SET_ANYWHERE_XYZ") == ""


def test_a_found_key_selects_the_hosted_model(monkeypatch):
    monkeypatch.setattr("handoff.planner.read_key", lambda name: "sk-ant-test")
    planner, note = build_planner(LlmConfig(provider="anthropic"))
    assert isinstance(planner, AnthropicPlanner) and "HOSTED" in note and "sk-ant-test" not in note
