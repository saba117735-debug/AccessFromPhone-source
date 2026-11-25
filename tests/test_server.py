import pytest
from fastapi.testclient import TestClient

from handoff.server import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings)
    c = TestClient(app)
    c.headers.update({"X-Token": app.state.token})
    return c


def test_api_needs_the_token(settings):
    anonymous = TestClient(create_app(settings))
    assert anonymous.get("/api/jobs").status_code == 401
    assert anonymous.get("/api/jobs", headers={"X-Token": "wrong"}).status_code == 401


def test_page_and_health_are_open(settings):
    anonymous = TestClient(create_app(settings))
    assert anonymous.get("/healthz").json() == {"ok": True}
    assert "AccessFromPhone" in anonymous.get("/").text


def test_token_is_stable_across_restarts(settings):
    assert create_app(settings).state.token == create_app(settings).state.token


def test_full_flow_over_http(client, settings):
    workspace = settings.agent.allowed_folders[0]

    job = client.post("/api/jobs", json={"prompt": "create hi.txt with hello"}).json()
    assert job["status"] == "awaiting_approval"
    assert not (workspace / "hi.txt").exists()

    done = client.post(f"/api/jobs/{job['id']}/approve").json()
    assert done["status"] == "done" and done["changed"] == ["hi.txt"]

    download = client.get("/api/files", params={"path": "hi.txt"})
    assert download.status_code == 200 and download.text == "hello"

    undone = client.post(f"/api/jobs/{job['id']}/undo").json()
    assert undone["status"] == "undone"
    assert not (workspace / "hi.txt").exists()


def test_download_outside_workspace_is_forbidden(client, project):
    (project / "secret.txt").write_text("nope", encoding="utf-8")
    assert client.get("/api/files", params={"path": "..\\secret.txt"}).status_code == 403


def test_wrong_state_and_unknown_job(client):
    job = client.post("/api/jobs", json={"prompt": "create a.txt with x"}).json()
    client.post(f"/api/jobs/{job['id']}/reject")
    assert client.post(f"/api/jobs/{job['id']}/approve").status_code == 409
    assert client.get("/api/jobs/nope").status_code == 404


def test_empty_prompt_is_rejected(client):
    assert client.post("/api/jobs", json={"prompt": ""}).status_code == 422


def test_audit_endpoint_needs_the_token(settings):
    assert TestClient(create_app(settings)).get("/api/audit").status_code == 401


def test_audit_endpoint_returns_the_whole_trail_in_order(client):
    job = client.post("/api/jobs", json={"prompt": "create hi.txt with hello"}).json()
    client.post(f"/api/jobs/{job['id']}/approve")
    events = [e["event"] for e in client.get("/api/audit").json() if e["job"] == job["id"]]
    assert events == ["submitted", "awaiting_approval", "approved", "write_file", "done"]


def test_audit_and_requests_survive_a_restart(settings):
    first = TestClient(create_app(settings), headers={"X-Token": create_app(settings).state.token})
    first.post("/api/jobs", json={"prompt": "list"})
    again = TestClient(create_app(settings), headers={"X-Token": create_app(settings).state.token})
    events = again.get("/api/audit").json()
    assert any(e["event"] == "submitted" and e["prompt"] == "list" for e in events)
    assert [j["prompt"] for j in again.get("/api/jobs").json()] == ["list"]  # the requests are remembered too, so chats survive
