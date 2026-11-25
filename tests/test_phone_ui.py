import copy

import pytest
from fastapi.testclient import TestClient

from handoff.config import LlmConfig, ServerConfig
from handoff.jobs import JobManager
from handoff.planner import AnthropicPlanner, OllamaPlanner
from handoff.server import create_app
from handoff.tls import ensure_certificate, hosts_for


@pytest.fixture
def client(settings):
    app = create_app(settings)
    return TestClient(app, headers={"X-Token": app.state.token})


# --- conversations -----------------------------------------------------------

def test_jobs_belong_to_a_conversation(client, settings):
    mine = client.post("/api/jobs", json={"prompt": "list", "conversation": "chat-1"}).json()
    plain = client.post("/api/jobs", json={"prompt": "list"}).json()
    assert mine["conversation"] == "chat-1" and plain["conversation"] == "default"
    submitted = [e for e in client.get("/api/audit").json() if e["event"] == "submitted"]
    assert {e["conversation"] for e in submitted} == {"chat-1", "default"}


@pytest.mark.parametrize("bad", ["has space", "../etc", "x" * 41, "a/b", "semi;colon"])
def test_a_bad_conversation_id_is_refused(client, bad):
    assert client.post("/api/jobs", json={"prompt": "list", "conversation": bad}).status_code == 422


class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def test_the_ai_only_remembers_the_chat_it_is_in(ro_settings):
    import time

    script = Script({"content": "one"}, {"content": "two"}, {"content": "three"})
    manager = JobManager(ro_settings, OllamaPlanner(LlmConfig(provider="ollama"), lambda u, p, t: {"message": script(u, p, t)["message"]}))
    manager.submit("secret plan for chat A", "chat-a")
    manager.join_background()
    time.sleep(0.01)
    manager.submit("hello from chat B", "chat-b")
    manager.join_background()
    time.sleep(0.01)
    manager.submit("follow-up in chat A", "chat-a")
    manager.join_background()
    sent_b = [m["content"] for m in script.requests[1]["messages"]]
    sent_a = [m["content"] for m in script.requests[2]["messages"]]
    assert not any("secret plan" in c for c in sent_b)
    assert any("secret plan" in c for c in sent_a) and not any("hello from chat B" in c for c in sent_a)


# --- info --------------------------------------------------------------------

def test_info_needs_the_token(settings):
    assert TestClient(create_app(settings)).get("/api/info").status_code == 401


def test_info_describes_what_the_pc_may_do(client, settings):
    info = client.get("/api/info").json()
    assert info["ai"]["kind"] == "none" and info["commands_enabled"] is False
    assert info["folders"]["writable"] == [str(f) for f in settings.agent.allowed_folders]
    assert info["approval"]["required_for"] == ["write", "delete", "send", "deploy", "run", "power"]
    assert info["https"] is False and info["max_download_mb"] == 0
    assert {"pc", "user", "version", "memory_turns"} <= set(info)


def test_info_never_contains_a_secret(client, settings, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-should-not-leak")
    app = create_app(settings, AnthropicPlanner(LlmConfig(provider="anthropic"), "sk-test-should-not-leak", lambda u, p, t: {}))
    body = TestClient(app, headers={"X-Token": app.state.token}).get("/api/info")
    assert body.json()["ai"] == {"kind": "hosted", "model": "claude-sonnet-5-5", "note": "AI model: custom planner"}
    assert "sk-test" not in body.text and app.state.token not in body.text


# --- the page and its files --------------------------------------------------

def test_the_page_loads_its_files_and_is_never_cached(settings):
    anonymous = TestClient(create_app(settings))
    page = anonymous.get("/")
    assert page.headers["cache-control"] == "no-store"
    assert "/static/app.js" in page.text and "/static/app.css" in page.text and "manifest.webmanifest" in page.text


@pytest.mark.parametrize("name, kind", [("app.js", "javascript"), ("app.css", "css"), ("icon.svg", "svg"), ("manifest.webmanifest", "manifest+json"), ("vendor/jsQR.js", "javascript")])
def test_the_page_files_are_served(settings, name, kind):
    response = TestClient(create_app(settings)).get(f"/static/{name}")
    assert response.status_code == 200 and kind in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-cache"


def test_static_files_cannot_reach_outside_the_web_folder(settings):
    anonymous = TestClient(create_app(settings))
    assert anonymous.get("/static/../server.py").status_code == 404
    assert anonymous.get("/static/%2e%2e/config.py").status_code == 404


def test_the_data_endpoints_stay_private(settings):
    anonymous = TestClient(create_app(settings))
    for path in ("/api/jobs", "/api/audit", "/api/info", "/api/files?path=x"):
        assert anonymous.get(path).status_code == 401


# --- the secure link ---------------------------------------------------------

def test_the_link_scheme_follows_the_https_setting():
    assert ServerConfig(public_url="http://10.0.0.5:8765").link_base == "http://10.0.0.5:8765"
    assert ServerConfig(public_url="http://10.0.0.5:8765", https=True).link_base == "https://10.0.0.5:8765"
    assert ServerConfig(public_url="https://10.0.0.5:8765", https=False).link_base == "http://10.0.0.5:8765"


def test_a_certificate_is_made_once_and_reused(tmp_path):
    hosts = hosts_for("http://10.1.2.3:8765")
    cert, key = ensure_certificate(tmp_path / "tls", hosts)
    first = cert.read_bytes()
    assert b"BEGIN CERTIFICATE" in first and b"PRIVATE KEY" in key.read_bytes()
    ensure_certificate(tmp_path / "tls", hosts)
    assert cert.read_bytes() == first


def test_a_new_ip_address_gets_a_new_certificate(tmp_path):
    cert, _ = ensure_certificate(tmp_path / "tls", hosts_for("http://10.1.2.3:8765"))
    before = cert.read_bytes()
    ensure_certificate(tmp_path / "tls", hosts_for("http://10.9.9.9:8765"))
    assert cert.read_bytes() != before


def test_the_certificate_names_the_phone_address(tmp_path):
    from cryptography import x509

    cert, _ = ensure_certificate(tmp_path / "tls", hosts_for("http://10.1.2.3:8765"))
    names = x509.load_pem_x509_certificate(cert.read_bytes()).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "10.1.2.3" in {str(v) for v in names.get_values_for_type(x509.IPAddress)}
    assert "localhost" in names.get_values_for_type(x509.DNSName)


def test_the_page_uses_only_its_own_files_no_outside_hosts(settings):
    import re
    from pathlib import Path

    web = Path(__import__("handoff").__file__).parent / "web"
    for name in ("index.html", "app.js", "app.css"):
        text = (web / name).read_text(encoding="utf-8")
        hosts = set(re.findall(r"""(?:src|href)=["']https?://([^/"']+)""", text)) | set(re.findall(r"""url\(["']?https?://([^/"')]+)""", text))
        assert not hosts, f"{name} loads from {hosts}"
