"""Runs the real HTTP code against a fake Ollama server on this machine."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from handoff.config import LlmConfig
from handoff.planner import OllamaPlanner, PlanError, ollama_ready


@pytest.fixture
def fake_ollama():
    state = {
        "requests": [],
        "status": 200,
        "reply": {"message": {"role": "assistant", "content": "Hi there"}},
        "models": ["qwen2.5:7b"],
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/api/tags":
                self._send(200, {"models": [{"name": n} for n in state["models"]]})
            else:
                self._send(404, {"error": "no such route"})

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            state["requests"].append((self.path, json.loads(self.rfile.read(length))))
            self._send(state["status"], state["reply"])

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["config"] = LlmConfig(provider="ollama", base_url=f"http://127.0.0.1:{server.server_port}")
    yield state
    server.shutdown()


def test_plan_over_real_http(fake_ollama):
    plan = OllamaPlanner(fake_ollama["config"]).plan("hi")
    assert plan.reply == "Hi there"
    path, body = fake_ollama["requests"][0]
    assert path == "/api/chat" and body["model"] == "qwen2.5:7b" and body["stream"] is False


def test_model_not_installed_over_real_http(fake_ollama):
    fake_ollama["status"] = 404
    fake_ollama["reply"] = {"error": "model 'qwen2.5:7b' not found"}
    with pytest.raises(PlanError, match="ollama pull qwen2.5:7b"):
        OllamaPlanner(fake_ollama["config"]).plan("hi")


def test_server_error_is_reported(fake_ollama):
    fake_ollama["status"] = 500
    fake_ollama["reply"] = {"error": "out of memory"}
    with pytest.raises(PlanError, match="HTTP 500"):
        OllamaPlanner(fake_ollama["config"]).plan("hi")


def test_ollama_not_running_is_a_clear_error():
    cfg = LlmConfig(provider="ollama", base_url="http://127.0.0.1:1")
    with pytest.raises(PlanError, match="Cannot reach Ollama"):
        OllamaPlanner(cfg).plan("hi")


def test_ready_check_over_real_http(fake_ollama):
    assert ollama_ready(fake_ollama["config"]) == (True, "")
    fake_ollama["models"] = ["llama3.1:8b"]
    ready, reason = ollama_ready(fake_ollama["config"])
    assert not ready and "ollama pull" in reason
