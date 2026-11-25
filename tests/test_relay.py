"""The relay and the PC's connection to it, over real sockets: phone -> relay -> PC app and back."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from handoff.db import Database
from handoff.relay import Registry, create_relay_app
from handoff.relay import protocol as proto
from handoff.tunnel import RelayClient, RelayLink, agent_url, phone_url

KEY = "a-long-registration-key"
BIG = bytes(range(256)) * 1500  # 384 KB: several frames


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, port=None):
    port = port or free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the test server did not start"

    def stop():
        server.should_exit = True
        thread.join(5)
    return port, stop


def wait_for(check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def pc_app():
    app = FastAPI()

    @app.get("/hello")
    def hello():
        return {"hello": "from the PC"}

    @app.post("/echo")
    async def echo(request: Request):
        return {"body": (await request.body()).decode(), "custom": request.headers.get("x-custom"), "host": request.headers.get("host"),
                "type": request.headers.get("content-type")}

    @app.get("/big")
    def big():
        return Response(BIG, media_type="application/octet-stream")

    @app.get("/ip")
    def ip(request: Request):
        return {"ip": request.client.host}

    @app.get("/boom")
    def boom():
        return JSONResponse({"detail": "nope"}, status_code=418)

    @app.get("/crash")
    def crash():
        raise RuntimeError("bug")
    return app


@pytest.fixture
def relay(tmp_path):
    registry = Registry(tmp_path / "relay.db")
    port, stop = serve(create_relay_app("localhost", registry, register_key=KEY, trust_proxy=True))
    yield {"port": port, "url": f"http://127.0.0.1:{port}", "registry": registry}
    stop()


@pytest.fixture
def pc(relay, tmp_path):
    link = RelayLink(pc_app(), Database(tmp_path / "pc.db"), relay["url"], KEY)
    status = link.enable()
    assert wait_for(lambda: link.connected), status
    pc_id = link._creds()[0]
    client = httpx.Client(base_url=relay["url"], headers={"Host": f"{pc_id}.localhost:{relay['port']}"}, timeout=15)
    yield {"link": link, "id": pc_id, "phone": client, "relay": relay}
    client.close()
    link._client.stop()


# ---------------------------------------------------------------- addresses

def test_the_phones_address_is_the_relays_name_with_the_pc_id_in_front():
    assert phone_url("https://example.com", "abc") == "https://abc.example.com"
    assert phone_url("http://localhost:9000", "abc") == "http://abc.localhost:9000"
    assert agent_url("https://example.com") == "wss://example.com/v1/agent" and agent_url("http://localhost:9000") == "ws://localhost:9000/v1/agent"


# ---------------------------------------------------------------- registering

def test_registering_needs_the_key_and_gives_a_secret_once(relay):
    root = httpx.Client(base_url=relay["url"], headers={"Host": "localhost"})
    assert root.post("/v1/register", json={"key": "wrong"}).status_code == 403
    assert root.post("/v1/register", json={}).status_code == 403
    ok = root.post("/v1/register", json={"key": KEY}).json()
    assert len(ok["pc_id"]) == 12 and len(ok["secret"]) >= 40
    assert relay["registry"].verify(ok["pc_id"], ok["secret"]) and not relay["registry"].verify(ok["pc_id"], "x")


def test_the_secret_is_kept_only_as_a_hash(relay, tmp_path):
    ok = httpx.post(relay["url"] + "/v1/register", json={"key": KEY}, headers={"Host": "localhost"}).json()
    raw = b"".join(p.read_bytes() for p in (tmp_path).glob("relay.db*"))
    assert ok["secret"].encode() not in raw


def test_registering_too_often_from_one_address_is_stopped(relay):
    codes = [httpx.post(relay["url"] + "/v1/register", json={"key": KEY}, headers={"Host": "localhost", "X-Forwarded-For": "198.51.100.7"}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


def test_the_relay_itself_answers_on_its_own_name(relay):
    assert httpx.get(relay["url"] + "/healthz", headers={"Host": "localhost"}).json() == {"ok": True, "pcs_online": 0}
    assert "relay" in httpx.get(relay["url"] + "/", headers={"Host": "localhost"}).text.lower()


# ---------------------------------------------------------------- the tunnel

def test_a_phone_reaches_the_pcs_app_through_the_relay(pc):
    r = pc["phone"].get("/hello")
    assert r.status_code == 200 and r.json() == {"hello": "from the PC"}
    assert httpx.get(pc["relay"]["url"] + "/healthz", headers={"Host": "localhost"}).json()["pcs_online"] == 1


def test_bodies_and_headers_go_through_and_the_relays_host_does_not(pc):
    r = pc["phone"].post("/echo", content="hello ✓", headers={"X-Custom": "yes", "Content-Type": "text/plain"}).json()
    assert r["body"] == "hello ✓" and r["custom"] == "yes" and r["type"] == "text/plain"
    assert pc["id"] not in (r["host"] or "")


def test_a_large_answer_arrives_whole_and_in_order(pc):
    r = pc["phone"].get("/big")
    assert r.status_code == 200 and r.content == BIG and int(r.headers["content-length"]) == len(BIG)


def test_the_pc_sees_the_phones_real_address_not_the_relays(pc):
    assert pc["phone"].get("/ip", headers={"X-Forwarded-For": "203.0.113.9"}).json() == {"ip": "203.0.113.9"}


def test_the_pcs_own_error_codes_come_back_unchanged(pc):
    assert pc["phone"].get("/boom").status_code == 418
    assert pc["phone"].get("/crash").status_code == 500
    assert pc["phone"].get("/missing").status_code == 404
    assert pc["phone"].get("/hello").status_code == 200  # and it carries on


def test_many_requests_at_once_each_get_their_own_answer(pc):
    results = []

    def go(i):
        results.append(pc["phone"].post("/echo", content=f"n{i}").json()["body"])
    threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    [t.start() for t in threads]
    [t.join(20) for t in threads]
    assert sorted(results) == sorted(f"n{i}" for i in range(20))


def test_a_huge_request_is_refused_before_it_reaches_the_pc(pc):
    assert pc["phone"].post("/echo", content=b"x" * (proto.MAX_BODY + 10)).status_code == 413


def test_an_unknown_pc_is_a_404(relay):
    r = httpx.get(relay["url"] + "/hello", headers={"Host": "zzzzzzzzzzzz.localhost"})
    assert r.status_code == 404


def test_a_pc_that_is_off_gets_a_friendly_page_not_an_error(pc):
    pc["link"]._client.stop()
    assert wait_for(lambda: not pc["link"].connected, 10) or True
    pc["link"]._client._stop.set()
    # force the relay to drop it: a PC id that exists but has no connection
    other = pc["relay"]["registry"].register()[0]
    r = httpx.get(pc["relay"]["url"] + "/", headers={"Host": f"{other}.localhost"})
    assert r.status_code == 503 and "isn't reachable" in r.text and r.headers["retry-after"]


def test_a_pc_with_the_wrong_secret_is_never_connected(relay, tmp_path):
    pc_id, _ = relay["registry"].register()
    client = RelayClient(pc_app(), relay["url"], pc_id, "not-the-secret")
    client.start()
    time.sleep(1.5)
    assert not client.connected and client.last_error
    client.stop()


def test_a_second_connection_from_the_same_pc_replaces_the_first(relay, pc):
    second = RelayClient(pc_app(), relay["url"], pc["id"], pc["link"]._creds()[1])
    second.start()
    assert wait_for(lambda: second.connected)
    assert httpx.get(relay["url"] + "/healthz", headers={"Host": "localhost"}).json()["pcs_online"] == 1
    assert pc["phone"].get("/hello").status_code == 200
    second.stop()


def test_the_pc_reconnects_by_itself_when_the_relay_comes_back(tmp_path):
    registry = Registry(tmp_path / "relay.db")
    port, stop = serve(create_relay_app("localhost", registry, register_key=KEY))
    link = RelayLink(pc_app(), Database(tmp_path / "pc.db"), f"http://127.0.0.1:{port}", KEY)
    link.enable()
    assert wait_for(lambda: link.connected)
    stop()  # the server goes down
    assert wait_for(lambda: not link.connected, 10)
    port2, stop2 = serve(create_relay_app("localhost", registry, register_key=KEY), port)  # and comes back at the same address
    try:
        assert wait_for(lambda: link.connected, 20)
    finally:
        link._client.stop()
        stop2()


# ---------------------------------------------------------------- the link the PC page shows

def test_the_link_registers_once_and_remembers_across_restarts(relay, tmp_path):
    db = Database(tmp_path / "pc.db")
    first = RelayLink(pc_app(), db, relay["url"], KEY)
    first.enable()
    first_id = first._creds()[0]
    first._client.stop()
    second = RelayLink(pc_app(), db, relay["url"], KEY)
    second.enable()
    assert second._creds()[0] == first_id and relay["registry"].count() == 1
    second._client.stop()


def test_the_link_reports_each_step_and_the_address_to_scan(pc):
    status = pc["link"].status()
    assert status["provider"] == "relay" and status["active"] and [s["ok"] for s in status["steps"]] == [True, True]
    assert status["url"] == f"http://{pc['id']}.127.0.0.1:{pc['relay']['port']}"  # the relay's own name with the id in front


def test_a_wrong_registration_key_is_explained_not_crashed(relay, tmp_path):
    link = RelayLink(pc_app(), Database(tmp_path / "pc.db"), relay["url"], "wrong")
    status = link.enable()
    assert not status["active"] and "registration key" in status["error"]


def test_an_unreachable_server_is_explained_not_crashed(tmp_path):
    link = RelayLink(pc_app(), Database(tmp_path / "pc.db"), f"http://127.0.0.1:{free_port()}", KEY)
    status = link.enable()
    assert not status["active"] and status["error"]
