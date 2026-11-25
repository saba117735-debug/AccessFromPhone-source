"""Naming a chat: the model's answer is made safe, only the local model is asked, and the page shows its own name first."""
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from handoff.config import LlmConfig
from handoff.planner import DemoPlanner, OllamaPlanner
from handoff.server import create_app
from handoff.titles import clean_title

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "handoff" / "web"
APP = (WEB / "app.js").read_text(encoding="utf-8")


def test_the_models_answer_becomes_a_clean_name():
    assert clean_title('"Open output folder"') == "Open output folder"
    assert clean_title("Title: Battery status check.\nThis chat is about the battery") == "Battery status check"
    assert clean_title("**lock PC**") == "Lock PC"
    assert clean_title("one two three four five six seven eight nine ten eleven") == "One two three four five six seven eight nine"


def test_a_refusal_or_a_ramble_is_not_a_name():
    for bad in ("", None, "   ", "I'm sorry, I can't help with that", "As an AI I do not know", "x" * 80):
        assert clean_title(bad) is None


class FakeOllama:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        return {"message": {"role": "assistant", "content": self.reply}, "done": True}


def test_the_local_model_is_asked_for_a_short_name_without_changing_its_settings(settings):
    fake = FakeOllama("Open output folder")
    planner = OllamaPlanner(settings.llm, fake)
    assert planner.title("i want to open C:/output folder please open it") == "Open output folder"
    url, payload, timeout = fake.calls[0]
    assert url.endswith("/api/chat") and "tools" not in payload and payload["stream"] is False
    assert payload["options"]["num_predict"] <= 24 and payload["options"]["num_ctx"] == settings.llm.num_ctx  # same size: no model reload
    assert timeout <= 25
    assert "output folder" in payload["messages"][1]["content"]


def test_a_failing_model_gives_no_name_instead_of_an_error(settings):
    from handoff.planner import PlanError

    def broken(url, payload, timeout):
        raise PlanError("Ollama is not running")

    assert OllamaPlanner(settings.llm, broken).title("lock my pc") is None


def client_for(settings, planner):
    app = create_app(settings, planner)
    client = TestClient(app)
    client.headers.update({"X-Token": app.state.token})
    return client


def test_the_title_call_asks_the_model_once_per_request(settings):
    fake = FakeOllama("Lock PC")
    client = client_for(settings, OllamaPlanner(settings.llm, fake))
    assert client.post("/api/title", json={"prompt": "lock my pc"}).json() == {"title": "Lock PC"}
    assert client.post("/api/title", json={"prompt": "lock   my pc"}).json() == {"title": "Lock PC"}
    assert len(fake.calls) == 1


def test_without_a_local_model_nothing_is_asked_and_nothing_leaves_the_pc(settings):
    assert client_for(settings, DemoPlanner()).post("/api/title", json={"prompt": "lock my pc"}).json() == {"title": None}


def test_a_hosted_model_is_never_asked_to_name_a_chat(settings):
    from handoff.planner import AnthropicPlanner

    def forbidden(*args, **kwargs):
        raise AssertionError("the hosted model must not be called to name a chat")

    planner = AnthropicPlanner(settings.llm, "key", forbidden)
    assert client_for(settings, planner).post("/api/title", json={"prompt": "open my folder"}).json() == {"title": None}


def test_the_page_shows_its_own_name_at_once_then_the_models_and_keeps_a_rename():
    model = APP[APP.index("function buildModel("):APP.index("function orbSVG(")]
    assert "S.titles[c.id] || S.autoTitles[c.id]" in model and "AfpTitles" in model  # a rename wins, then the model's, then the one made from the words
    assert '"/title"' in APP and 'S.info.ai.kind !== "local"' in APP  # only asked when this PC has its own model


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_the_naming_rules_pass_their_own_tests():
    done = subprocess.run(["node", str(ROOT / "tests" / "js" / "titles_test.js")], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
