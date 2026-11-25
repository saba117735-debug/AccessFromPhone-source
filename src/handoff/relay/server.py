"""The relay: the reception desk that lives on your domain.

Every PC keeps one connection open to `/v1/agent`. A phone opens `https://<pc-id>.<domain>/`; the relay finds that PC's
connection, passes the request down it, and streams the answer back. It never stores a file or a request, and it cannot
act on a PC. Only the app on the PC can.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import protocol as proto
from .registry import Registry

_PC_ID = re.compile(r"^[a-z0-9]{12}$")

OFFLINE_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your PC is offline</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#060b16;color:#e6f1ff;font:16px system-ui,sans-serif;text-align:center;padding:24px">
<main style="max-width:420px"><h1 style="font-size:1.5rem">Your PC isn't reachable</h1>
<p style="color:#8392ad;line-height:1.5">It may be off or asleep, or the app isn't running on it. Turn it on, make sure the app is open, then try again.</p>
<p><a href="" style="color:#22d3ee">Try again</a></p></main></body></html>"""

LANDING_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>AccessFromPhone relay</title>
<body style="font:16px system-ui,sans-serif;padding:32px;color:#222"><h1>AccessFromPhone relay</h1><p>This server connects phones to their PCs. Open the link from your PC to use it.</p></body></html>"""


@dataclass
class Agent:
    """One PC's open connection."""

    ws: WebSocket
    pc_id: str
    pending: dict[int, asyncio.Queue] = field(default_factory=dict)
    ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(proto.MAX_IN_FLIGHT))
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, frame: dict) -> None:
        async with self.send_lock:  # one writer at a time on a socket
            await self.ws.send_text(proto.pack(frame))


class Limiter:
    """At most `limit` events per `window` seconds for one key."""

    def __init__(self, limit: int, window: float):
        self._limit, self._window, self._hits = limit, window, defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, hits = time.monotonic(), self._hits[key]
        while hits and now - hits[0] > self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True


def create_relay_app(domain: str, registry: Registry, register_key: str = "", trust_proxy: bool = False) -> FastAPI:
    """`domain` is the bare name phones' addresses end in, e.g. "example.com" (or "localhost" while testing)."""
    app = FastAPI(title="AccessFromPhone relay", docs_url=None, redoc_url=None, openapi_url=None)
    domain = domain.lower().strip(".")
    agents: dict[str, Agent] = {}
    register_limit = Limiter(10, 3600)
    app.state.agents, app.state.registry = agents, registry

    def host_of(headers) -> str:
        return (headers.get("x-forwarded-host") if trust_proxy and headers.get("x-forwarded-host") else headers.get("host", "")).split(":")[0].lower().strip(".")

    def pc_for(host: str) -> str | None:
        """`abc123def456.example.com` -> `abc123def456`. The bare domain (and anything else) is the relay itself."""
        if host.endswith("." + domain):
            label = host[: -len(domain) - 1]
            return label if _PC_ID.match(label) else ""
        return None

    def client_ip(request: Request) -> str:
        if trust_proxy and request.headers.get("x-forwarded-for"):
            return request.headers["x-forwarded-for"].split(",")[0].strip()
        return request.client.host if request.client else ""

    # ------------------------------------------------------------------ PCs connect here
    @app.websocket("/v1/agent")
    async def agent_socket(ws: WebSocket) -> None:
        pc_id, auth = ws.headers.get("x-pc-id", ""), ws.headers.get("authorization", "")
        secret = auth[7:] if auth.lower().startswith("bearer ") else ""
        if pc_for(host_of(ws.headers)) is not None:  # an agent talks to the relay itself, not to a PC's address
            await ws.close(code=1008)
            return
        if not registry.verify(pc_id, secret):
            await ws.close(code=1008)
            return
        await ws.accept()
        conn = Agent(ws=ws, pc_id=pc_id)
        if old := agents.get(pc_id):  # the same PC reconnecting: the new connection wins
            with contextlib.suppress(Exception):
                await old.ws.close(code=1012)
        agents[pc_id] = conn
        registry.touch(pc_id)
        try:
            while True:
                try:
                    frame = proto.unpack(await ws.receive_text())
                except ValueError:
                    continue  # junk from the PC is ignored, not fatal
                queue = conn.pending.get(frame.get("id"))
                if queue is not None:
                    queue.put_nowait(frame)
        except WebSocketDisconnect:
            pass
        finally:
            if agents.get(pc_id) is conn:
                del agents[pc_id]
            registry.touch(pc_id)
            for queue in list(conn.pending.values()):
                queue.put_nowait({"t": "err", "status": 502, "message": "The PC disconnected."})

    # ------------------------------------------------------------------ everything else
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def everything(path: str, request: Request):
        pc_id = pc_for(host_of(request.headers))
        if pc_id is None:
            return await own_pages(path, request)
        if not pc_id or not registry.exists(pc_id):
            raise HTTPException(status_code=404, detail="no such PC")
        return await forward(pc_id, request)

    async def own_pages(path: str, request: Request):
        if path == "healthz":
            return {"ok": True, "pcs_online": len(agents)}
        if path == "v1/register" and request.method == "POST":
            if not register_limit.allow(client_ip(request)):
                raise HTTPException(status_code=429, detail="too many registrations from this address")
            try:
                body = await request.json()
            except ValueError:
                body = {}
            if register_key and body.get("key") != register_key:
                raise HTTPException(status_code=403, detail="registration needs the right key")
            pc_id, secret = registry.register()
            return {"pc_id": pc_id, "secret": secret}
        if path == "":
            return HTMLResponse(LANDING_PAGE)
        raise HTTPException(status_code=404, detail="not found")

    async def forward(pc_id: str, request: Request):
        conn = agents.get(pc_id)
        if conn is None:
            return HTMLResponse(OFFLINE_PAGE, status_code=503, headers={"Retry-After": "10", "Cache-Control": "no-store"})
        if int(request.headers.get("content-length") or 0) > proto.MAX_BODY:
            raise HTTPException(status_code=413, detail="request too large")
        body = await request.body()
        if len(body) > proto.MAX_BODY:
            raise HTTPException(status_code=413, detail="request too large")
        try:
            await asyncio.wait_for(conn.slots.acquire(), 5)
        except asyncio.TimeoutError:
            return JSONResponse({"detail": "the PC is busy; try again"}, status_code=503)
        req_id, queue = next(conn.ids), asyncio.Queue()
        conn.pending[req_id] = queue
        finished = False

        async def done(cancel: bool) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            conn.pending.pop(req_id, None)
            conn.slots.release()
            if cancel:
                with contextlib.suppress(Exception):
                    await conn.send({"t": "cancel", "id": req_id})

        query = request.url.query
        try:
            await conn.send({
                "t": "req", "id": req_id, "method": request.method, "path": request.url.path + ("?" + query if query else ""),
                "headers": [[k, v] for k, v in request.headers.items() if k.lower() not in proto.REQUEST_DROP],
                "ip": client_ip(request), "body": proto.b64(body),
            })
            first = await asyncio.wait_for(queue.get(), proto.REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            await done(cancel=True)
            return JSONResponse({"detail": "the PC took too long to answer"}, status_code=504)
        except Exception:
            await done(cancel=False)
            return HTMLResponse(OFFLINE_PAGE, status_code=503, headers={"Retry-After": "10"})
        if first["t"] == "err":
            await done(cancel=False)
            return JSONResponse({"detail": first.get("message", "the PC could not answer")}, status_code=int(first.get("status", 502)))

        async def stream():
            clean = False
            try:
                while True:
                    frame = await queue.get()
                    if frame["t"] == "data":
                        yield proto.unb64(frame["b64"])
                    else:  # end, or the PC went away part-way
                        clean = frame["t"] == "end"
                        return
            finally:
                await done(cancel=not clean)

        response = StreamingResponse(stream(), status_code=int(first["status"]))
        response.raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in first.get("headers", []) if k.lower() not in proto.HOP_BY_HOP]
        response.raw_headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer")]
        return response

    return app


def settings_from_environment() -> dict:
    """The relay is configured with environment variables, so a server needs no config file."""
    return {
        "domain": os.environ.get("RELAY_DOMAIN", "localhost"),
        "database": Path(os.environ.get("RELAY_DB", "relay-data/relay.db")),
        "register_key": os.environ.get("RELAY_REGISTER_KEY", ""),
        "trust_proxy": os.environ.get("RELAY_TRUST_PROXY", "").lower() in ("1", "true", "yes"),
    }
